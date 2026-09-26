"""사진 — EXIF 읽기(찍은 날짜·카메라·노출·방향)와, 회전해 다시 저장한 JPEG 에 메타데이터 옮겨 싣기.

무거운 라이브러리(PIL·exiv2) 없이 필요한 태그만 직접 읽는다:
  JPEG 의 APP1("Exif"), TIFF 파일 자체, PNG 의 eXIf 조각, WebP 의 EXIF 조각.
모두 작업 스레드에서 부른다 (파일을 읽는다). 깨진 EXIF 는 조용히 건너뛴다 — 그림은 그대로 열려야 한다.
"""
import re
import struct
import time

ORIENTATION = 0x0112
MAKE = 0x010F
MODEL = 0x0110
DATETIME = 0x0132
EXIF_IFD = 0x8769
EXPOSURE = 0x829A
FNUMBER = 0x829D
ISO = 0x8827
DT_ORIGINAL = 0x9003
DT_DIGITIZED = 0x9004
FOCAL = 0x920A
PIXEL_X = 0xA002
PIXEL_Y = 0xA003
LENS = 0xA434

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_MAX_ENTRIES = 512                      # 깨진 IFD 가 수만 개라고 우겨도 끝없이 돌지 않게


class _Tiff:
    """TIFF 구조(EXIF 의 속) 읽기. read(off, n) 은 TIFF 머리 기준 위치에서 n 바이트"""

    def __init__(self, read):
        self.read = read
        head = read(0, 8)
        if len(head) < 8 or head[:2] not in (b"II", b"MM"):
            raise ValueError("TIFF 머리가 아님")
        self.e = "<" if head[:2] == b"II" else ">"
        magic, self.ifd0 = struct.unpack(self.e + "HI", head[2:8])
        if magic != 42:
            raise ValueError("TIFF 표식이 아님")

    def entries(self, off):
        """{태그: (형식, 개수, 값 칸의 위치)} — 값 칸은 4바이트(넘치면 거기에 실제 위치가 있다)"""
        raw = self.read(off, 2)
        if len(raw) < 2:
            return {}
        n = min(struct.unpack(self.e + "H", raw)[0], _MAX_ENTRIES)
        body = self.read(off + 2, 12 * n)
        out = {}
        for i in range(len(body) // 12):
            tag, typ, cnt = struct.unpack(self.e + "HHI", body[i * 12:i * 12 + 8])
            out[tag] = (typ, cnt, off + 2 + i * 12 + 8)
        return out

    def value(self, ent):
        typ, cnt, field = ent
        size = _TYPE_SIZE.get(typ)
        if not size or cnt <= 0 or cnt > 4096:
            return None
        total = size * cnt
        if total <= 4:
            data = self.read(field, total)
        else:
            ptr = struct.unpack(self.e + "I", self.read(field, 4))[0]
            data = self.read(ptr, total)
        if len(data) < total:
            return None
        e = self.e
        if typ == 2:
            return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        if typ == 3:
            v = struct.unpack(e + "%dH" % cnt, data)
        elif typ == 4:
            v = struct.unpack(e + "%dI" % cnt, data)
        elif typ == 9:
            v = struct.unpack(e + "%di" % cnt, data)
        elif typ in (5, 10):
            nums = struct.unpack(e + ("%dI" if typ == 5 else "%di") % (cnt * 2), data)
            v = tuple((nums[i] / nums[i + 1]) if nums[i + 1] else 0.0 for i in range(0, len(nums), 2))
        elif typ in (1, 7):
            return data
        else:
            return None
        return v[0] if cnt == 1 else v


def _parse(tiff):
    out = {}
    ifd0 = tiff.entries(tiff.ifd0)
    for tag, key in ((ORIENTATION, "orientation"), (MAKE, "make"), (MODEL, "model"), (DATETIME, "datetime")):
        if tag in ifd0:
            v = tiff.value(ifd0[tag])
            if v not in (None, ""):
                out[key] = v
    if EXIF_IFD in ifd0:
        ptr = tiff.value(ifd0[EXIF_IFD])
        if isinstance(ptr, int) and ptr > 0:
            sub = tiff.entries(ptr)
            for tag, key in ((DT_ORIGINAL, "original"), (DT_DIGITIZED, "digitized"), (EXPOSURE, "exposure"),
                             (FNUMBER, "fnumber"), (ISO, "iso"), (FOCAL, "focal"), (LENS, "lens")):
                if tag in sub:
                    v = tiff.value(sub[tag])
                    if isinstance(v, tuple) and v and key != "lens":
                        v = v[0]
                    if v not in (None, ""):
                        out[key] = v
    return out


def _exif_date(s):
    """"2026:09:25 15:21:05" → 초 (그 자리의 시각으로)"""
    if not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        y, mo, d, h, mi, se = (int(x) for x in m.groups())
        if y < 1900:
            return None
        return time.mktime((y, mo, d, h, mi, se, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


# ── 파일 형식마다 EXIF 자리 찾기 ─────────────────────────────
def _jpeg_exif(f):
    f.seek(2)
    for _ in range(64):
        b = f.read(1)
        while b == b"\xff":                    # 채움 바이트
            b = f.read(1)
        if not b:
            return None
        m = b[0]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        if m in (0xDA, 0xD9):                  # 그림 자료 시작 — 머리는 끝났다
            return None
        raw = f.read(2)
        if len(raw) < 2:
            return None
        n = struct.unpack(">H", raw)[0] - 2
        if n < 0:
            return None
        if m == 0xE1:
            data = f.read(n)
            if data[:6] == b"Exif\0\0":
                return data[6:]
        else:
            f.seek(n, 1)
        nb = f.read(1)
        if nb != b"\xff":
            return None
    return None


def _png_exif(f):
    f.seek(8)
    for _ in range(4096):
        head = f.read(8)
        if len(head) < 8:
            return None
        n, typ = struct.unpack(">I4s", head)
        if typ == b"eXIf":
            return f.read(n)
        if typ in (b"IDAT", b"IEND"):
            return None
        f.seek(n + 4, 1)
    return None


def _webp_exif(f):
    f.seek(12)
    for _ in range(256):
        head = f.read(8)
        if len(head) < 8:
            return None
        typ, n = struct.unpack("<4sI", head)
        if typ == b"EXIF":
            data = f.read(n)
            return data[6:] if data[:6] == b"Exif\0\0" else data
        f.seek(n + (n & 1), 1)
    return None


def read(path):
    """{orientation, taken(초), taken_is_original, make, model, exposure, fnumber, iso, focal, lens} — 없는 건 빠진다"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            tiff = None
            if head[:2] == b"\xff\xd8":
                blob = _jpeg_exif(f)
                if blob:
                    tiff = _Tiff(lambda off, n, b=blob: b[off:off + n])
            elif head[:4] in (b"II*\0", b"MM\0*"):
                def rd(off, n, f=f):
                    f.seek(off)
                    return f.read(n)
                tiff = _Tiff(rd)
                raw = _parse(tiff)
                return _finish(raw)
            elif head[:8] == b"\x89PNG\r\n\x1a\n":
                blob = _png_exif(f)
                if blob:
                    tiff = _Tiff(lambda off, n, b=blob: b[off:off + n])
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                blob = _webp_exif(f)
                if blob:
                    tiff = _Tiff(lambda off, n, b=blob: b[off:off + n])
            if tiff is None:
                return {}
            return _finish(_parse(tiff))
    except (OSError, ValueError, struct.error, IndexError, TypeError):
        return {}


def _finish(raw):
    out = {}
    o = raw.get("orientation")
    if isinstance(o, int) and 1 <= o <= 8:
        out["orientation"] = o
    t = _exif_date(raw.get("original")) or _exif_date(raw.get("digitized"))
    if t:
        out["taken"] = t
    for k in ("make", "model", "lens"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = " ".join(v.split())
    for k in ("exposure", "fnumber", "focal"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and v > 0:
            out[k] = float(v)
    v = raw.get("iso")
    if isinstance(v, int) and v > 0:
        out["iso"] = v
    return out


# ── 보여 줄 글 ───────────────────────────────────────────────
def camera_text(ex):
    make, model = ex.get("make", ""), ex.get("model", "")
    if model and make and model.lower().startswith(make.split()[0].lower()):
        return model                            # "Canon Canon EOS R6" 같은 겹침
    return " ".join(x for x in (make, model) if x)


def exposure_text(v):
    if v >= 1:
        return f"{v:g}초"
    return f"1/{max(1, round(1 / v))}초"


# ── 회전해 다시 저장한 JPEG 에 원래 메타데이터 싣기 ─────────────
def _segments(data):
    """JPEG 머리 조각들 [(표식, 시작, 끝)] 과 그림 자료(머리 뒤)의 시작 위치"""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG 가 아님")
    segs, i, n = [], 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            raise ValueError("표식 자리가 어긋남")
        m = data[i + 1]
        if m == 0xFF:                           # 채움
            i += 1
            continue
        if not (0xE0 <= m <= 0xEF or m == 0xFE):
            return segs, i                      # DQT·SOF 등 — 여기부터는 그림 자료
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        segs.append((m, i, i + 2 + ln))
        i += 2 + ln
    raise ValueError("머리가 끝나지 않음")


def _components(data, start):
    """SOF 의 색 성분 수 (CMYK 면 4)"""
    i, n = start, len(data)
    while i + 4 <= n and data[i] == 0xFF:
        m = data[i + 1]
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            return data[i + 9] if i + 9 < n else 3
        if m == 0xDA:
            break
        i += 2 + ln
    return 3


def _patch_exif(seg, width, height):
    """APP1 Exif 조각(바이트 배열, 표식 포함)을 고친다: 방향 = 1, 픽셀 크기 = 새 크기, 작은 미리 보기(IFD1) 떼기.
    고칠 수 없으면 ValueError — 그러면 부르는 쪽이 이 조각을 버린다 (옛 방향이 남아 두 번 돌아가 보이지 않게)"""
    base = 4 + 6                                 # FF E1 길이(2) "Exif\0\0"
    buf = seg

    def rd(off, n):
        return bytes(buf[base + off:base + off + n])
    t = _Tiff(rd)
    e = t.e
    ifd0 = t.entries(t.ifd0)
    if ORIENTATION in ifd0:
        typ, cnt, field = ifd0[ORIENTATION]
        if typ != 3 or cnt != 1:
            raise ValueError("방향 태그 형식이 이상함")
        struct.pack_into(e + "H", buf, base + field, 1)
    n = struct.unpack(e + "H", rd(t.ifd0, 2))[0]
    nxt = base + t.ifd0 + 2 + 12 * n
    if nxt + 4 > len(buf):
        raise ValueError("IFD0 가 잘림")
    struct.pack_into(e + "I", buf, nxt, 0)       # 미리 보기 그림은 옛 방향이다 — 뗀다
    if EXIF_IFD in ifd0:
        ptr = t.value(ifd0[EXIF_IFD])
        if isinstance(ptr, int) and ptr > 0:
            sub = t.entries(ptr)
            for tag, val in ((PIXEL_X, width), (PIXEL_Y, height)):
                if tag in sub:
                    typ, cnt, field = sub[tag]
                    if typ == 3 and val < 65536:
                        struct.pack_into(e + "H", buf, base + field, val)
                    elif typ == 4:
                        struct.pack_into(e + "I", buf, base + field, val)
    return bytes(buf)


_XMP_ORIENT = re.compile(rb'(tiff:Orientation\s*=\s*["\']|<tiff:Orientation>)\s*([1-8])')


def splice_jpeg_meta(orig, new, width, height):
    """GdkPixbuf 가 새로 쓴 JPEG(new) 에 원래 JPEG(orig) 의 메타데이터를 옮긴다.
    EXIF(찍은 날짜·카메라)·XMP·ICC 색 프로필·주석이 남는다. 그림은 이미 돌렸으므로 방향 표시는 1 로.
    원래 것을 읽을 수 없으면 new 를 그대로 돌려준다."""
    try:
        osegs, ostart = _segments(orig)
        nsegs, nstart = _segments(new)
    except (ValueError, struct.error, IndexError):
        return new
    cmyk = _components(orig, ostart) == 4
    app0 = None
    keep = []
    for m, a, b in osegs:
        seg = orig[a:b]
        if m == 0xE0:
            if seg[4:9] == b"JFIF\0" and app0 is None:
                app0 = seg
            continue
        if m == 0xEE:                            # Adobe 색 변환 표시 — 새 파일은 YCbCr 라 맞지 않는다
            continue
        if m == 0xE2 and seg[4:16] == b"ICC_PROFILE\0" and cmyk:
            continue                             # CMYK 프로필을 RGB 그림에 붙이면 색이 틀어진다
        if m == 0xE1 and seg[4:10] == b"Exif\0\0":
            try:
                seg = _patch_exif(bytearray(seg), width, height)
            except (ValueError, struct.error, IndexError):
                continue
        elif m == 0xE1 and b"http://ns.adobe.com/xap/1.0/" in seg[4:40]:
            seg = _XMP_ORIENT.sub(lambda mo: mo.group(1) + b"1", seg)
        keep.append(seg)
    if not keep and app0 is None:
        return new
    napp0 = next((new[a:b] for m, a, b in nsegs if m == 0xE0), None)
    has_exif = any(s[:2] == b"\xff\xe1" and s[4:10] == b"Exif\0\0" for s in keep)
    head = app0 if app0 is not None else (None if has_exif else napp0)
    extra = [new[a:b] for m, a, b in nsegs if m not in (0xE0,) and not (m == 0xE2 and new[a + 4:a + 16] == b"ICC_PROFILE\0")]
    parts = [b"\xff\xd8"]
    if head is not None:
        parts.append(head)
    parts += keep
    parts += extra
    parts.append(new[nstart:])
    return b"".join(parts)


# ── JPEG 무손실 회전 — 그림은 그대로 두고 EXIF 방향 표시만 바꾼다 ─────────────
#   (다시 인코딩하면 화질이 한 번 더 깎이고, 뒤에 붙은 모션 포토 동영상·HDR 게인 맵이 사라진다)
#   방향 값 → (좌우 뒤집기, 시계 방향 90° 횟수): 표시할 때 먼저 뒤집고 그다음 돌린다
_ORI_TO = {1: (0, 0), 2: (1, 0), 3: (0, 2), 4: (1, 2), 5: (1, 3), 6: (0, 1), 7: (1, 1), 8: (0, 3)}
_TO_ORI = {v: k for k, v in _ORI_TO.items()}


def rotate_jpeg_lossless(data, quarter):
    """EXIF 방향 태그를 quarter × 90°(시계 방향)만큼 더 돌린 JPEG 바이트. 방향 태그가 없거나 읽을 수 없으면 None
    (태그를 새로 끼워 넣지는 않는다 — 그때는 부르는 쪽이 다시 인코딩할지 정한다)"""
    try:
        segs, _start = _segments(data)
    except (ValueError, struct.error, IndexError):
        return None
    buf = bytearray(data)
    new = None
    for m, a, b in segs:
        if m != 0xE1 or data[a + 4:a + 10] != b"Exif\0\0":
            continue
        base = a + 10

        def rd(off, n, base=base, end=b):
            lo = base + off
            return bytes(buf[lo:min(lo + n, end)]) if lo < end else b""
        try:
            t = _Tiff(rd)
            ifd0 = t.entries(t.ifd0)
            ent = ifd0.get(ORIENTATION)
            if ent is None or ent[0] != 3 or ent[1] != 1:
                return None
            field = base + ent[2]
            cur = struct.unpack_from(t.e + "H", buf, field)[0]
            flip, rot = _ORI_TO.get(cur, (0, 0))
            new = _TO_ORI[(flip, (rot + quarter) % 4)]
            struct.pack_into(t.e + "H", buf, field, new)
        except (ValueError, struct.error, IndexError):
            return None
        break
    if new is None:
        return None
    # XMP 에 적힌 방향도 같게 (한 글자를 한 글자로 — 조각 길이는 그대로)
    for m, a, b in segs:
        if m == 0xE1 and b"http://ns.adobe.com/xap/1.0/" in data[a + 4:a + 40]:
            seg = _XMP_ORIENT.sub(lambda mo: mo.group(1) + str(new).encode(), bytes(buf[a:b]))
            if len(seg) == b - a:
                buf[a:b] = seg
    return bytes(buf)


def jpeg_extra(data):
    """그림 말고 함께 든 자료가 있나 — 다시 인코딩하면 사라지는 것 (까닭 글 또는 None):
    끝(EOI) 뒤에 붙은 자료(모션 포토 동영상 등), MPF 보조 그림(HDR 게인 맵·깊이 지도)"""
    try:
        segs, start = _segments(data)
    except (ValueError, struct.error, IndexError):
        return None
    for m, a, b in segs:
        if m == 0xE2 and data[a + 4:a + 8] == b"MPF\0":
            return "함께 든 보조 그림(HDR·깊이 정보)"
    # 압축된 그림 자료 안에서 0xFF 뒤에는 00 이나 재시작 표식만 온다 — 처음 나오는 FF D9 가 그림의 끝
    end = data.find(b"\xff\xd9", start)
    if end >= 0 and len(data) - (end + 2) > 64:
        return "사진 뒤에 붙은 자료(모션 포토 동영상 등)"
    return None

