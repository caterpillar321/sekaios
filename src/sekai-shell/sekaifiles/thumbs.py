"""썸네일 — freedesktop 썸네일 규격대로 만들고, 다른 앱이 만든 것도 다시 쓴다.

    th = Thumbnailer()
    h = th.request(gfile_or_path, size_px, mtime, callback)   # callback(pixbuf 또는 None) — 메인 스레드에서
    th.cancel(h)          # 그 요청의 callback 은 불리지 않는다
    th.cancel_all()       # 폴더를 옮길 때

저장: ~/.cache/thumbnails/{normal(128), large(256), x-large(512), xx-large(1024)}/<md5(uri)>.png
      PNG 안의 tEXt Thumb::URI · Thumb::MTime 이 파일과 맞아야 다시 쓴다. size_px 보다 큰 단계가 있으면
      그것을 줄여 쓴다. 만들다 실패한 파일은 fail/sekai-files/ 에 적어 두고 (수정한 시각이 같으면) 다시 하지 않는다.
      임시 파일에 쓰고 이름을 바꾼다 (0600, 폴더 0700).
만드는 것: 그림(GdkPixbuf 가 읽는 형식 — 사진의 방향(EXIF) 반영, 50 MB 넘거나 터무니없이 큰 것은 건너뜀),
      동영상(ffmpegthumbnailer 가 있으면), PDF(pdftoppm 이 있으면). 나머지는 None — 보기가 형식 아이콘을 쓴다.
      ~/.cache/thumbnails 안의 파일, 네트워크·원격(네이티브가 아닌) 파일은 만들지 않는다.
작업 스레드 2개가 가장 최근 요청부터 처리한다 (스크롤해 지금 보이는 것 먼저). 최근 결과는 메모리에도 조금 둔다.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import traceback
from collections import OrderedDict

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

SIZES = (("normal", 128), ("large", 256), ("x-large", 512), ("xx-large", 1024))
FAIL_SUB = os.path.join("fail", "sekai-files")
MAX_BYTES = 50 * 1024 * 1024           # 이보다 큰 그림은 만들지 않는다 (읽는 데 너무 오래·메모리)
MAX_SIDE = 30000
MAX_PIXELS = 150 * 1000 * 1000
TOOL_TIMEOUT = 30                      # 동영상·PDF 도구가 멈추면
WORKERS = 2
MEM_CACHE = 300


def cache_root():
    return os.path.join(GLib.get_user_cache_dir(), "thumbnails")


def _bucket(size_px):
    for name, px in SIZES:
        if size_px <= px:
            return name, px
    return SIZES[-1]


class _Req:
    __slots__ = ("id", "target", "size", "mtime", "callback", "cancelled")

    def __init__(self, rid, target, size, mtime, callback):
        self.id = rid
        self.target = target
        self.size = size
        self.mtime = mtime
        self.callback = callback
        self.cancelled = False


class Thumbnailer:
    def __init__(self, workers=WORKERS):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._queue = []                        # 뒤에서부터 꺼낸다 (최근 요청 먼저)
        self._reqs = {}                         # id → _Req (결과를 넘기기 전까지)
        self._next = 1
        self._nworkers = workers
        self._threads = []
        self._mem = OrderedDict()               # (uri, mtime, size) → pixbuf
        self._mem_lock = threading.Lock()
        self._pix_mimes = None
        self._stopped = False                   # shutdown() 뒤 — 작업 스레드가 끝난다
        self._root = cache_root()
        self._tools = {"ffmpegthumbnailer": shutil.which("ffmpegthumbnailer"),
                       "pdftoppm": shutil.which("pdftoppm")}

    # ── 메인 스레드 ──
    def request(self, gfile_or_path, size_px, mtime, callback):
        """썸네일을 부탁한다 — 번호(handle)를 돌려준다. callback(pixbuf|None) 는 늘 나중에 메인 스레드에서"""
        size_px = max(16, int(size_px or 128))
        with self._lock:
            rid = self._next
            self._next += 1
            req = _Req(rid, gfile_or_path, size_px, mtime, callback)
            self._reqs[rid] = req
            self._queue.append(req)
            self._cond.notify()
        self._ensure_workers()
        return rid

    def cancel(self, handle):
        with self._lock:
            req = self._reqs.pop(handle, None)
            if req is not None:
                req.cancelled = True

    def cancel_all(self):
        with self._lock:
            for req in self._reqs.values():
                req.cancelled = True
            self._reqs.clear()
            self._queue.clear()

    def shutdown(self):
        """창이 닫힐 때 — 작업 스레드를 끝내고 기억해 둔 그림을 버린다 (창마다 하나라, 안 그러면 닫은 창마다
        스레드 둘과 그림 캐시(최대 수십 MB)가 프로세스에 남았다)"""
        with self._lock:
            self._stopped = True
            for req in self._reqs.values():
                req.cancelled = True
            self._reqs.clear()
            self._queue.clear()
            self._cond.notify_all()
        with self._mem_lock:
            self._mem.clear()

    def _ensure_workers(self):
        if self._stopped or len(self._threads) >= self._nworkers:
            return
        while len(self._threads) < self._nworkers:
            t = threading.Thread(target=self._work, daemon=True, name="sekai-thumbs")
            t.start()
            self._threads.append(t)

    def _deliver(self, req, pb):
        with self._lock:
            if self._reqs.get(req.id) is req:
                del self._reqs[req.id]
            else:
                return False
        if req.cancelled:
            return False
        try:
            req.callback(pb)
        except Exception:
            traceback.print_exc()
        return False

    # ── 작업 스레드 ──
    def _work(self):
        while True:
            with self._lock:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                req = self._queue.pop()
            if req.cancelled:
                continue
            try:
                pb = self._thumbnail(req.target, req.size, req.mtime)
            except Exception:
                traceback.print_exc()
                pb = None
            if not req.cancelled:
                GLib.idle_add(self._deliver, req, pb)

    def _thumbnail(self, target, size, mtime):
        gf = target if isinstance(target, Gio.File) else Gio.File.new_for_path(str(target))
        if not gf.is_native():
            return None
        path = gf.get_path()
        if not path:
            return None
        root = self._root.rstrip("/") + "/"
        if path.startswith(root):
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not os.path.isfile(path):
            return None
        mt = int(mtime) if isinstance(mtime, (int, float)) and mtime else int(st.st_mtime)
        uri = gf.get_uri()
        key = (uri, mt, size)
        with self._mem_lock:
            pb = self._mem.get(key)
            if pb is not None:
                self._mem.move_to_end(key)
                return pb
        md5 = hashlib.md5(uri.encode("utf-8", "surrogateescape")).hexdigest()
        bname, bpx = _bucket(size)

        # 1) 이미 있는 것 — 이 단계나 더 큰 단계
        start = [n for n, _p in SIZES].index(bname)
        for name, _px in SIZES[start:]:
            pb = self._load_valid(os.path.join(self._root, name, md5 + ".png"), uri, mt)
            if pb is not None:
                return self._remember(key, _fit(pb, size))
        # 2) 전에 실패한 것
        fail = os.path.join(self._root, FAIL_SUB, md5 + ".png")
        if self._load_valid(fail, uri, mt) is not None:
            return None
        # 3) 새로 만든다
        kind = self._kind(gf, path)
        if kind is None:
            return None
        pb = None
        try:
            if kind == "image":
                pb = self._from_image(path, st.st_size, bpx)
            elif kind == "video":
                pb = self._from_tool(["ffmpegthumbnailer", "-i", path, "-o", "{out}", "-s", str(bpx), "-c", "png"])
            elif kind == "pdf":
                pb = self._from_tool(["pdftoppm", "-png", "-singlefile", "-scale-to", str(bpx), "-f", "1", "-l", "1",
                                      path, "{base}"])
        except Exception as e:                      # 깨진 파일 — 실패로 적어 둔다
            print("[sekai-files] 썸네일 실패:", GLib.filename_display_name(path), e, flush=True)
            pb = None
        if pb is None:
            self._save(fail, _tiny(), uri, mt)
            return None
        if pb.get_width() > bpx or pb.get_height() > bpx:
            pb = _fit(pb, bpx)
        self._save(os.path.join(self._root, bname, md5 + ".png"), pb, uri, mt)
        return self._remember(key, _fit(pb, size))

    def _remember(self, key, pb):
        with self._mem_lock:
            self._mem[key] = pb
            while len(self._mem) > MEM_CACHE:
                self._mem.popitem(last=False)
        return pb

    def _kind(self, gf, path):
        try:
            ct = gf.query_info("standard::content-type", 0, None).get_content_type() or ""
        except GLib.Error:
            ct = Gio.content_type_guess(path, None)[0] or ""
        mime = Gio.content_type_get_mime_type(ct) or ct
        if mime in self._pixbuf_mimes():
            return "image"
        if mime.startswith("video/") and self._tools["ffmpegthumbnailer"]:
            return "video"
        if mime == "application/pdf" and self._tools["pdftoppm"]:
            return "pdf"
        return None

    def _pixbuf_mimes(self):
        if self._pix_mimes is None:
            s = set()
            for fmt in GdkPixbuf.Pixbuf.get_formats():
                s.update(fmt.get_mime_types() or [])
            self._pix_mimes = s
        return self._pix_mimes

    def _from_image(self, path, nbytes, px):
        if nbytes > MAX_BYTES:
            return None
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        if fmt is None or w <= 0 or h <= 0 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
            return None
        vector = fmt.get_name() == "svg"
        if not vector and w <= px and h <= px:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)          # 작은 그림은 키우지 않는다
        else:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, px, px, True)
        return pb.apply_embedded_orientation() if pb is not None else None

    def _from_tool(self, argv):
        with tempfile.TemporaryDirectory(prefix="sekai-thumb-") as tmp:
            base = os.path.join(tmp, "t")
            out = base + ".png"
            argv = [a.replace("{out}", out).replace("{base}", base) for a in argv]
            try:
                r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=TOOL_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                return None
            if r.returncode != 0 or not os.path.exists(out):
                return None
            return GdkPixbuf.Pixbuf.new_from_file(out)

    def _load_valid(self, png, uri, mt):
        """규격에 맞고 이 파일(uri·수정 시각)의 것이면 픽스버프"""
        if not os.path.exists(png):
            return None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(png)
        except GLib.Error:
            return None
        if pb.get_option("tEXt::Thumb::URI") != uri:
            return None
        try:
            if int(float(pb.get_option("tEXt::Thumb::MTime") or "")) != mt:
                return None
        except ValueError:
            return None
        return pb

    def _save(self, png, pb, uri, mt):
        """임시 파일(0600)에 쓰고 이름을 바꾼다 — 반쯤 쓴 썸네일을 다른 앱이 읽지 않게"""
        folder = os.path.dirname(png)
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            ok, buf = pb.save_to_bufferv("png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Software"],
                                         [uri, str(mt), "SekaiOS"])
            if not ok:
                return
            tmp = os.path.join(folder, f".{os.path.basename(png)}.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, bytes(buf))
            finally:
                os.close(fd)
            os.replace(tmp, png)
        except (OSError, GLib.Error) as e:
            print("[sekai-files] 썸네일을 저장하지 못했습니다:", e, flush=True)


def _fit(pb, size):
    """비율을 지켜 size 안으로 줄인다 (키우지는 않는다)"""
    w, h = pb.get_width(), pb.get_height()
    s = min(size / w, size / h, 1.0)
    if s >= 1.0:
        return pb
    return pb.scale_simple(max(1, round(w * s)), max(1, round(h * s)), GdkPixbuf.InterpType.BILINEAR)


def _tiny():
    """실패 기록용 1×1 그림"""
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
    pb.fill(0)
    return pb
