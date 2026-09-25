"""사진 — 그림 읽기·쓰기와 작업 스레드.

여기 있는 함수들은 작업 스레드에서 돈다 — GTK 위젯은 건드리지 않는다 (GdkPixbuf·cairo 그림만 만든다).
GdkPixbuf 와 cairo 의 그림 객체는 스레드를 건너 넘겨도 된다 (만든 뒤엔 읽기만 한다).

  Pool            우선순위가 있는 작업 스레드 묶음. 끝나면 메인 스레드에서 done(결과, 예외)
  list_folder     폴더의 그림 목록 (탐색기 기본 정렬처럼 자연 순서 — 2.jpg 가 10.jpg 앞)
  load_entry      파일 하나를 읽어 Entry 로 (EXIF 방향 적용, 큰 그림은 줄여서, 창 맞춤 크기 미리 그리기)
  render_region   원본의 한 부분을 배율에 맞춰 고품질로 줄이거나 늘린 cairo 그림
  save_rotated    회전을 파일에 저장 (임시 파일에 다 쓰고 바꿔치기)
  export          다른 이름으로 저장
"""
import errno
import heapq
import itertools
import math
import os
import re
import stat
import tempfile
import threading
import traceback

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402

from . import exif  # noqa: E402

HUGE = 50_000_000               # 이보다 큰 그림은 화면용으로 줄여 읽고, 맞춤보다 크게 볼 때 원본을 읽는다
REDUCED = 16_000_000            # 줄여 읽을 때의 화소 수 (4K 화면에 맞춰 보기엔 넉넉하다)
VECTOR_SIDE = 4096              # SVG 는 긴 변을 이만큼으로 그려 둔다 (작은 아이콘도 창에 맞춰 선명하게)
CHUNK = 1 << 20
# 회전을 파일에 저장할 수 있는 형식 (윈도우 사진 앱처럼 나갈 때 저장) — 나머지는 보기만 돌린다
ROTATE_SAVE = {"jpeg", "png", "bmp", "tiff"}
# EXIF 를 우리가 직접 읽는 형식 (찍은 날짜·카메라, 그리고 GdkPixbuf 가 방향을 알려 주지 않을 때의 방향)
EXIF_FMTS = ("jpeg", "tiff", "png", "webp")
# 여는 데 따로 설치해야 하는 부품이 있는 형식 (없으면 "구성 요소가 없다"고 알려 준다)
NEEDS_LOADER = {"heic": "HEIC", "heif": "HEIF", "avif": "AVIF", "webp": "WebP", "jxl": "JPEG XL"}
# 폴더에서 함께 넘겨 볼 확장자 — 설치된 로더가 아는 것 + 흔한 그림 형식(로더가 없어도 목록엔 넣고 이유를 보여 준다)
KNOWN_EXT = {"jpg", "jpeg", "jpe", "jfif", "png", "apng", "gif", "bmp", "dib", "webp", "tif", "tiff", "ico",
             "cur", "svg", "svgz", "avif", "heic", "heif", "jxl", "tga", "pnm", "pbm", "pgm", "ppm", "xpm",
             "xbm", "icns", "qoi", "jp2"}


def _loader_exts():
    out = set()
    try:
        for f in GdkPixbuf.Pixbuf.get_formats():
            if f.get_name() in ("ani", "qtif"):  # 마우스 커서·퀵타임 — 사진 목록에 섞이면 이상하다
                continue
            out.update(x.lower() for x in f.get_extensions())
    except Exception:
        traceback.print_exc()
    return out


IMAGE_EXT = KNOWN_EXT | _loader_exts()
try:
    _UMASK = os.umask(0)
    os.umask(_UMASK)
except OSError:
    _UMASK = 0o022


class LoadError(Exception):
    """사람이 읽을 이유를 담은 오류"""


class Cancelled(Exception):
    pass


# ── 작업 스레드 ──────────────────────────────────────────────
class Job:
    __slots__ = ("work", "done", "cancelled", "started", "tag")

    def __init__(self, work, done, tag):
        self.work, self.done, self.tag = work, done, tag
        self.cancelled = False
        self.started = False


class Pool:
    """작업 스레드 n 개. submit(work, done, prio) — work(job) 은 작업 스레드에서, done(결과, 예외) 는 메인 스레드에서.
    prio 가 작을수록 먼저. 같은 prio 는 넣은 순서대로. job.cancelled 가 서면 아직 시작 안 한 것은 건너뛰고,
    끝난 것도 done 을 부르지 않는다 (work 가 스스로 job.cancelled 를 보고 멈출 수도 있다)."""

    def __init__(self, n, name):
        self._heap = []
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._busy = 0
        for i in range(n):
            threading.Thread(target=self._loop, daemon=True, name=f"sekai-photos-{name}-{i}").start()

    def submit(self, work, done=None, prio=5, tag=None):
        job = Job(work, done, tag)
        with self._cv:
            heapq.heappush(self._heap, (prio, next(self._seq), job))
            self._cv.notify()
        return job

    def cancel_where(self, pred):
        with self._cv:
            for _p, _s, j in self._heap:
                if pred(j):
                    j.cancelled = True

    def _loop(self):
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                _p, _s, job = heapq.heappop(self._heap)
                if job.cancelled:
                    continue
                job.started = True
                self._busy += 1
            try:
                res, exc = job.work(job), None
            except Exception as e:                  # 부르는 쪽이 이유를 보여 준다
                res, exc = None, e
            finally:
                with self._cv:
                    self._busy -= 1
            if job.done is not None and not job.cancelled:
                GLib.idle_add(self._deliver, job, res, exc, priority=GLib.PRIORITY_HIGH_IDLE)

    @staticmethod
    def _deliver(job, res, exc):
        if not job.cancelled:
            try:
                job.done(res, exc)
            except Exception:
                traceback.print_exc()
        return False


# ── 폴더 ────────────────────────────────────────────────────
_DIGITS = re.compile(r"(\d+)")


def natural_key(name):
    """탐색기처럼 숫자는 수로 견준다 (사진2 < 사진10). 숫자가 글자보다 앞"""
    key = []
    for i, part in enumerate(_DIGITS.split(name.casefold())):
        if i % 2:
            key.append((0, int(part), len(part), ""))
        elif part:
            key.append((1, 0, 0, part))
    return key, name


def ext_of(path):
    base = os.path.basename(path)
    return base.rsplit(".", 1)[1].lower() if "." in base.lstrip(".") else ""


def list_folder(folder, keep=None):
    """폴더의 그림 파일 (숨김 파일은 빼되, 연 파일 keep 은 무엇이든 넣는다) — 자연 순서"""
    out = []
    with os.scandir(folder) as it:
        for de in it:
            p = de.path
            if p != keep:
                if de.name.startswith(".") or ext_of(p) not in IMAGE_EXT:
                    continue
            try:
                if not de.is_file():
                    continue
            except OSError:
                continue
            out.append(p)
    if keep and keep not in out:
        out.append(keep)
    out.sort(key=lambda p: natural_key(os.path.basename(p)))
    return out


# ── 읽기 ────────────────────────────────────────────────────
_entry_ids = itertools.count(1)


class Entry:
    """그림 한 장. src 는 EXIF 방향을 적용한 GdkPixbuf (큰 그림은 줄인 것), W·H 는 원본 크기(방향 적용).
    k = src 너비 / W (줄였으면 1 보다 작고, SVG 는 크게 그려 두어 1 보다 클 수 있다).
    base = (배율, (x, y, w, h), cairo 그림) — 그림 전체를 어떤 배율로 그려 둔 것 (창 맞춤 크기)."""

    def __init__(self, path):
        self.id = next(_entry_ids)
        self.ver = 0                    # src 가 바뀔 때마다 (원본 해상도로 바꿔 끼울 때)
        self.path = path
        self.stamp = None               # (수정 시각 ns, 크기) — 파일이 바뀌었는지
        self.size = 0
        self.mtime = 0.0
        self.fmt = ""                   # GdkPixbuf 형식 이름 (jpeg, png, gif, svg …)
        self.fmt_desc = ""
        self.src = None
        self.small = None               # 줄여 읽은 것 (원본을 읽어 끼운 동안에도 남겨 두었다가 떠날 때 되돌린다)
        self.W = self.H = 0
        self.k = 1.0
        self.reduced = False
        self.vector = False
        self.anim = None
        self.base = None
        self.exif = {}
        self.error = None
        self.pre_rot = 0                # 회전을 파일에 저장하는 중 — 새로 읽기 전까지 보기로 더 돌려 보여 준다

    @property
    def ok(self):
        return self.error is None and self.src is not None

    def nbytes(self):
        n = 0
        for pb in (self.src, self.small):
            if pb is not None:
                n += pb.get_byte_length()
        if self.base is not None:
            _z, (_x, _y, w, h), _s = self.base
            n += 4 * w * h
        if self.anim is not None:
            n += 4 * self.W * self.H * 8          # 움직이는 그림은 틀 수를 알 수 없다 — 넉넉히
        return n


def error_text(exc):
    """예외 → 사람이 읽을 한 줄"""
    if isinstance(exc, LoadError):
        return str(exc)
    if isinstance(exc, MemoryError):
        return "그림이 너무 커서 메모리가 부족합니다."
    if isinstance(exc, GLib.Error):
        if exc.domain == "gdk-pixbuf-error-quark":
            if exc.code == GdkPixbuf.PixbufError.CORRUPT_IMAGE:
                return "파일이 손상되었거나 끝이 잘렸습니다."
            if exc.code == GdkPixbuf.PixbufError.INSUFFICIENT_MEMORY:
                return "그림이 너무 커서 메모리가 부족합니다."
            if exc.code == GdkPixbuf.PixbufError.UNKNOWN_TYPE:
                return "지원하지 않는 파일 형식입니다."
        if exc.domain == "g-io-error-quark":
            code = exc.code
            if code == Gio.IOErrorEnum.NOT_FOUND:
                return "파일을 찾을 수 없습니다."
            if code == Gio.IOErrorEnum.PERMISSION_DENIED:
                return "권한이 없습니다."
            if code == Gio.IOErrorEnum.NO_SPACE:
                return "디스크 공간이 부족합니다."
            if code == Gio.IOErrorEnum.READ_ONLY:
                return "읽기 전용 위치입니다."
            if code == Gio.IOErrorEnum.EXISTS:
                return "같은 이름의 파일이 이미 있습니다."
        return exc.message or "알 수 없는 오류"
    if isinstance(exc, OSError):
        return {
            errno.ENOENT: "파일을 찾을 수 없습니다.",
            errno.EACCES: "권한이 없습니다.",
            errno.EPERM: "권한이 없습니다.",
            errno.ENOSPC: "디스크 공간이 부족합니다.",
            errno.EROFS: "읽기 전용 위치입니다.",
            errno.EISDIR: "폴더입니다.",
            errno.EIO: "파일을 읽는 중 입출력 오류가 났습니다.",
        }.get(exc.errno, exc.strerror or str(exc))
    return str(exc) or exc.__class__.__name__


def _decode(path, typ, size=None, job=None, data=None):
    """GdkPixbufLoader 로 조금씩 읽는다 — 다른 그림으로 넘어가면(job.cancelled) 도중에 멈춘다"""
    loader = GdkPixbuf.PixbufLoader.new_with_type(typ)
    if size:
        loader.set_size(max(1, int(size[0])), max(1, int(size[1])))
    try:
        if data is not None:
            loader.write(data)
        else:
            with open(path, "rb") as f:
                while True:
                    if job is not None and job.cancelled:
                        raise Cancelled()
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    loader.write(chunk)
        loader.close()
    except BaseException:
        try:
            loader.close()                   # 닫지 않고 버리면 GdkPixbuf 가 경고를 낸다
        except GLib.Error:
            pass
        raise
    return loader


# EXIF 방향 → (좌우 뒤집기, 시계 방향 90° 회전 수) — 뒤집은 다음 돌린다
_ORIENT = {1: (0, 0), 2: (1, 0), 3: (0, 2), 4: (1, 2), 5: (1, 3), 6: (0, 1), 7: (1, 1), 8: (0, 3)}
_ROT = {1: GdkPixbuf.PixbufRotation.CLOCKWISE, 2: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
        3: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}


def rotate(pb, quarter):
    """시계 방향으로 quarter × 90°"""
    q = quarter % 4
    if q == 0 or pb is None:
        return pb
    out = pb.rotate_simple(_ROT[q])
    if out is None:
        raise MemoryError()
    return out


def orient(pb, ex_orientation=None):
    """EXIF 방향대로 세운다. GdkPixbuf 가 알려 주면 그걸로, 못 읽은 형식이면 우리가 읽은 EXIF 로"""
    if pb.get_option("orientation"):
        out = pb.apply_embedded_orientation()
        return out if out is not None else pb
    flip, q = _ORIENT.get(ex_orientation or 1, (0, 0))
    if flip:
        pb = pb.flip(True)
    return rotate(pb, q)


def fit_zoom(W, H, vw, vh, vector=False):
    """창에 맞추는 배율 — 작은 그림은 늘리지 않는다 (윈도우 사진 앱처럼). SVG 는 창에 맞춰 늘린다"""
    if W <= 0 or H <= 0 or vw <= 0 or vh <= 0:
        return 1.0
    z = min(vw / W, vh / H)
    if not vector:
        z = min(z, 1.0)
    return max(z, 1e-4)


def scaled_dims(W, H, z):
    return max(1, round(W * z)), max(1, round(H * z))


def render_region(src, UW, UH, rect):
    """src 를 UW×UH 로 늘리거나 줄인 그림에서 rect=(x, y, w, h) 부분만 — cairo 그림으로.
    줄일 때는 BILINEAR (GdkPixbuf 는 줄일 때 넓이를 평균해 계단이 지지 않는다), 아주 크게 볼 때는 화소 그대로"""
    ux, uy, uw, uh = rect
    sw, sh = src.get_width(), src.get_height()
    if (UW, UH) == (sw, sh):
        pb = src if (ux, uy, uw, uh) == (0, 0, sw, sh) else src.new_subpixbuf(ux, uy, uw, uh)
    else:
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, src.get_has_alpha(), 8, uw, uh)
        if pb is None:
            raise MemoryError()
        interp = GdkPixbuf.InterpType.NEAREST if UW / sw >= 4 else GdkPixbuf.InterpType.BILINEAR
        src.scale(pb, 0, 0, uw, uh, -ux, -uy, UW / sw, UH / sh, interp)
    return to_surface(pb)


def to_surface(pb):
    """GdkPixbuf → cairo 그림 (작업 스레드에서 불러도 된다).
    Gdk.cairo_surface_create_from_pixbuf(pb, 1, None) 은 창이 없으면 X11 루트 창을 닮은 그림을 만든다 —
    작업 스레드에서 Xlib 를 부르게 되어 기본 화면 모드(X11)에서 프로그램이 죽었다.
    cairo 그림 위의 문맥에 set_source_pixbuf 를 하면 변환된 그림(패턴의 그림)만 받을 수 있다"""
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    surf = cr.get_source().get_surface()
    if surf is None or surf.get_width() != pb.get_width():
        raise MemoryError()
    return surf


def _info(path):
    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    return fmt, (w or 0), (h or 0)


def load_entry(path, viewport, job=None, full=False):
    """파일 하나 → Entry. 실패해도 Entry(error=이유) 를 돌려준다 (넘겨 보기는 계속되게).
    viewport() 는 지금 그림 칸 크기(장치 픽셀) — 창 맞춤 그림(base)을 미리 그려 둔다.
    full=True 면 큰 그림도 원본 해상도로 (Entry 의 src 만 쓴다)."""
    e = Entry(path)
    try:
        st = os.stat(path)
    except OSError as ex:
        e.error = error_text(ex)
        return e
    e.stamp = (st.st_mtime_ns, st.st_size)
    e.size, e.mtime = st.st_size, st.st_mtime
    if stat.S_ISDIR(st.st_mode):
        e.error = "폴더입니다."
        return e
    if st.st_size == 0:
        e.error = "파일이 비어 있습니다."
        return e
    try:
        fmt, w, h = _info(path)
    except GLib.Error as ex:
        fmt, w, h = None, 0, 0
        e.error = error_text(ex)
    if fmt is None:
        name = NEEDS_LOADER.get(ext_of(path))
        e.error = (f"이 형식({name})을 여는 데 필요한 구성 요소가 설치되어 있지 않습니다." if name else
                   e.error or "지원하지 않는 파일 형식이거나 파일이 손상되었습니다.")
        return e
    e.fmt = fmt.get_name()
    e.fmt_desc = fmt.get_description() or e.fmt.upper()
    e.vector = bool(fmt.is_scalable()) and e.fmt in ("svg", "svg+xml", "svgz")
    if e.fmt in EXIF_FMTS:
        e.exif = exif.read(path)
    try:
        size = None
        if e.vector and w > 0 and h > 0:
            s = min(max(VECTOR_SIDE / max(w, h), 1.0), math.sqrt(REDUCED / (w * h)))
            size = (round(w * s), round(h * s))
        elif not full and w * h > HUGE:
            f = math.sqrt(REDUCED / (w * h))
            size = (round(w * f), round(h * f))
            e.reduced = True
        loader = _decode(path, e.fmt, size, job)
        anim = loader.get_animation()
        if anim is not None and not anim.is_static_image():
            e.anim = anim
            pb = anim.get_static_image()
            e.W, e.H = anim.get_width(), anim.get_height()
            e.reduced = False
        else:
            pb = loader.get_pixbuf()
        if pb is None:
            raise LoadError("그림을 읽지 못했습니다.")
        pre = (pb.get_width(), pb.get_height())
        if e.anim is None:
            pb = orient(pb, e.exif.get("orientation"))
        post = (pb.get_width(), pb.get_height())
        if e.vector and w > 0 and h > 0:
            e.W, e.H = w, h                      # SVG 의 "100%" 는 파일에 적힌 크기
        elif e.reduced and w > 0 and h > 0:
            e.W, e.H = (h, w) if post != pre else (w, h)   # 원본 크기 (방향을 세워 가로·세로가 바뀌었으면 맞바꿔)
        else:
            e.W, e.H = post
        e.src = pb
        e.k = pb.get_width() / e.W if e.W else 1.0
        if e.reduced:
            e.small = pb
    except Cancelled:
        raise
    except Exception as ex:
        if not isinstance(ex, (GLib.Error, LoadError, MemoryError, OSError)):
            traceback.print_exc()
        e.error = error_text(ex)
        e.src = e.anim = None
        return e
    if not full and e.anim is None:
        if job is not None and job.cancelled:
            raise Cancelled()
        vw, vh = viewport()
        z = fit_zoom(e.W, e.H, vw, vh, e.vector)
        UW, UH = scaled_dims(e.W, e.H, z)
        try:
            e.base = (z, (0, 0, UW, UH), render_region(e.src, UW, UH, (0, 0, UW, UH)))
        except MemoryError:
            e.base = None
    return e


def load_full(path, fmt, job=None):
    """큰 그림의 원본 해상도 (확대할 때) — EXIF 방향 적용"""
    loader = _decode(path, fmt, None, job)
    pb = loader.get_pixbuf()
    if pb is None:
        raise LoadError("그림을 읽지 못했습니다.")
    return orient(pb, exif.read(path).get("orientation") if fmt in EXIF_FMTS else None)


# ── 쓰기 ────────────────────────────────────────────────────
def write_atomic(path, data, like=None):
    """같은 폴더의 임시 파일(숨김)에 다 쓰고 바꿔치기 — 도중에 꺼져도 원래 파일은 온전하다.
    권한은 원래 파일(like) 것을, 새 파일이면 umask 대로"""
    real = os.path.realpath(path)             # 바로 가기(심볼릭 링크)는 링크가 아니라 가리키는 파일을 바꾼다
    d = os.path.dirname(real) or "."
    try:
        mode = stat.S_IMODE(os.stat(like or real).st_mode)
    except OSError:
        mode = 0o666 & ~_UMASK
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(real) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _encode(pb, fmt, opts):
    keys, vals = [], []
    if fmt == "jpeg":
        keys, vals = ["quality"], ["95"]
    elif fmt == "png":
        for k, v in (opts or {}).items():
            if k.startswith("tEXt::") or k in ("icc-profile", "x-dpi", "y-dpi"):
                keys.append(k)
                vals.append(v)
    elif fmt == "tiff":
        for k in ("icc-profile", "x-dpi", "y-dpi", "compression"):
            if k in (opts or {}):
                keys.append(k)
                vals.append(opts[k])
    elif fmt == "webp":
        keys, vals = ["quality"], ["95"]
    try:
        ok, buf = pb.save_to_bufferv(fmt, keys, vals)
    except GLib.Error:
        if not keys:
            raise
        ok, buf = pb.save_to_bufferv(fmt, [], [])     # 원래 파일에서 옮긴 설정이 맞지 않으면 기본값으로
    if not ok:
        raise LoadError("그림을 저장하지 못했습니다.")
    return bytes(buf)


def _options(pb):
    try:
        return dict(pb.get_options() or {})
    except Exception:
        return {}


def _full_oriented(path, fmt, data=None):
    loader = _decode(path, fmt, None, None, data)
    pb = loader.get_pixbuf()
    if pb is None:
        raise LoadError("그림을 읽지 못했습니다.")
    opts = _options(pb)
    ori = None
    if fmt in EXIF_FMTS and not pb.get_option("orientation"):
        ori = exif.read(path).get("orientation")
    return orient(pb, ori), opts


def save_rotated(path, quarter, fmt):
    """회전을 파일에 저장 (JPEG 은 품질 95 로 다시 쓰되 EXIF·ICC 는 옮겨 싣는다)"""
    if fmt not in ROTATE_SAVE:
        raise LoadError("회전한 상태로 저장할 수 없는 형식입니다.")
    real = os.path.realpath(path)
    with open(real, "rb") as f:
        orig = f.read()
    pb, opts = _full_oriented(real, fmt, orig)
    pb = rotate(pb, quarter)
    data = _encode(pb, fmt, opts)
    if fmt == "jpeg":
        data = exif.splice_jpeg_meta(orig, data, pb.get_width(), pb.get_height())
    write_atomic(real, data)


# 다른 이름으로 저장 — 확장자 → GdkPixbuf 형식
SAVE_EXT = {"jpg": "jpeg", "jpeg": "jpeg", "jpe": "jpeg", "jfif": "jpeg", "png": "png", "bmp": "bmp",
            "tif": "tiff", "tiff": "tiff", "webp": "webp", "ico": "ico"}


def writable_formats():
    out = set()
    try:
        for f in GdkPixbuf.Pixbuf.get_formats():
            if f.is_writable():
                out.add(f.get_name())
    except Exception:
        pass
    return out


def export(src_path, src_fmt, dest, dest_fmt, quarter, is_anim=False):
    """다른 이름으로 저장. 형식이 같고 돌리지 않았으면 파일을 그대로 복사한다 (화질·메타데이터 손실 없음)"""
    real_src = os.path.realpath(src_path)
    with open(real_src, "rb") as f:
        orig = f.read()
    if dest_fmt == src_fmt and quarter % 4 == 0:
        if os.path.realpath(dest) == real_src:
            return
        write_atomic(dest, orig, like=real_src)
        return
    if is_anim:
        loader = _decode(real_src, src_fmt, None, None, orig)
        pb, opts = loader.get_pixbuf(), {}
    else:
        pb, opts = _full_oriented(real_src, src_fmt, orig)
    pb = rotate(pb, quarter)
    if dest_fmt in ("jpeg", "bmp") and pb.get_has_alpha():
        pb = _drop_alpha(pb)                     # 투명을 모르는 형식 — 흰 바탕에 얹는다 (윈도우 그림판처럼)
    if dest_fmt == "ico" and (pb.get_width() > 256 or pb.get_height() > 256):
        raise LoadError("아이콘(.ico)은 256×256 보다 큰 그림을 담을 수 없습니다.")
    data = _encode(pb, dest_fmt, opts if dest_fmt == src_fmt else {})
    if dest_fmt == "jpeg" and src_fmt == "jpeg":
        data = exif.splice_jpeg_meta(orig, data, pb.get_width(), pb.get_height())
    write_atomic(dest, data)


def _drop_alpha(pb):
    if not pb.get_has_alpha():
        return pb
    out = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
    if out is None:
        raise MemoryError()
    out.fill(0xFFFFFFFF)
    pb.composite(out, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return out

