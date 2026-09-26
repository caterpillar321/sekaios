"""파일 탐색기 — 여러 모듈이 함께 쓰는 것.

  · 부품 불러오기 (fileops · thumbs · archive · properties — 아직 없으면 그 기능만 꺼진다)
  · 위치: 특별한 곳(홈 · 내 PC · 휴지통)과 폴더를 URI 문자열 하나로 다룬다
  · 이름·크기·날짜 글자 모양 (윈도우 한국어 표기)
  · 목록 한 줄(Entry)과 정렬 열쇠
  · 아이콘 그림 캐시 · 아이콘 보기의 두 줄 이름 자르기
"""
import importlib
import os
import re
import stat
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import dbg  # noqa: E402,F401


# ── 부품 ─────────────────────────────────────────────────────
def _optional(name):
    """다른 사람이 따로 만드는 부품 — 없거나 깨져도 창은 뜨게 (그 기능만 쓸 수 없다)"""
    try:
        return importlib.import_module(name)
    except Exception as e:                      # ImportError 말고도 부품 안의 오류까지
        print(f"[sekai-files] {name} 을(를) 불러오지 못했습니다: {e}", file=sys.stderr, flush=True)
        return None


fileops = _optional("sekaishell.fileops")
thumbs = _optional("sekaifiles.thumbs")
archive = _optional("sekaifiles.archive")
properties = _optional("sekaifiles.properties")


# ── 위치 ─────────────────────────────────────────────────────
# 폴더는 그 URI(file:///…, trash:///, smb://…), 특별한 곳은 아래 이름.
#   홈은 GIO 에 없는 곳이라 우리만 아는 이름을 쓴다 (Gio.File 로 만들지 않는다)
HOME = "sekai-home:///"
COMPUTER = "computer:///"
TRASH = "trash:///"

SPECIAL_TITLES = {HOME: "홈", COMPUTER: "내 PC", TRASH: "휴지통"}
SPECIAL_ICONS = {
    HOME: ["user-home", "go-home", "folder"],
    COMPUTER: ["computer", "system", "drive-harddisk"],
    TRASH: ["user-trash", "user-trash-full", "trash-empty"],
}
# 주소 창에 쳐도 알아듣는 이름
SPECIAL_WORDS = {
    "홈": HOME, "home:": HOME, "home:///": HOME, HOME: HOME,
    "내 pc": COMPUTER, "내pc": COMPUTER, "이 pc": COMPUTER, "내 컴퓨터": COMPUTER, "computer:": COMPUTER,
    "computer:///": COMPUTER, "computer://": COMPUTER,
    "휴지통": TRASH, "trash:": TRASH, "trash://": TRASH, "trash:///": TRASH,
}

ROOT_LABEL = "로컬 디스크 (/)"

# 즐겨찾기 — XDG 사용자 폴더 (한국어 시스템이면 폴더 이름도 한국어). 보이는 이름은 윈도우처럼 늘 한국어
KNOWN = [
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "바탕 화면", ["user-desktop", "folder-desktop", "desktop"]),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "다운로드", ["folder-download", "folder-downloads"]),
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "문서", ["folder-documents", "folder-document"]),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "사진", ["folder-pictures", "folder-images"]),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "음악", ["folder-music", "folder-sound"]),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "동영상", ["folder-videos", "folder-video"]),
]


def home_dir():
    return os.path.normpath(GLib.get_home_dir())


def favorites():
    """[(경로, 보이는 이름, 아이콘 후보)] — 설정되지 않은 폴더(홈과 같은 것)는 뺀다"""
    home = home_dir()
    out = []
    for d, label, icons in KNOWN:
        p = GLib.get_user_special_dir(d)
        if not p:
            continue
        p = os.path.normpath(p)
        if p == home:
            continue
        out.append((p, label, icons + ["folder"]))
    return out


def known_label(path):
    """즐겨찾기 폴더면 (이름, 아이콘) — 아니면 None"""
    for p, label, icons in favorites():
        if p == path:
            return label, icons
    return None


def user_name():
    return GLib.get_user_name() or "사용자"


def is_special(uri):
    return uri in (HOME, COMPUTER)


def gfile_of(uri):
    """위치 → Gio.File (홈·내 PC 는 None)"""
    if not uri or is_special(uri):
        return None
    return Gio.File.new_for_uri(uri)


def uri_of_path(path):
    return Gio.File.new_for_path(path).get_uri()


def local_path(uri):
    """file:// 이면 그 경로, 아니면 None"""
    if not uri or not uri.startswith("file:"):
        return None
    return Gio.File.new_for_uri(uri).get_path()


def norm_uri(uri):
    """같은 곳을 같은 글자로 (끝의 / · %XX 모양 차이) — 역사·즐겨찾기 비교용"""
    if not uri:
        return uri
    if uri in SPECIAL_WORDS:
        return SPECIAL_WORDS[uri]
    if uri.startswith("trash:"):
        f = Gio.File.new_for_uri(uri)
        u = f.get_uri()
        return TRASH if u in ("trash:", "trash://", "trash:///") else u
    return Gio.File.new_for_uri(uri).get_uri()


def is_in_trash(uri):
    return bool(uri) and uri.startswith("trash:")


def parse_location(text, cwd_uri=None):
    """주소 창·명령줄에 쓴 글 → 위치 URI (특별한 곳 · URI · ~ · 절대/상대 경로). 모르면 None"""
    t = (text or "").strip()
    if not t:
        return None
    low = t.casefold()
    if low in SPECIAL_WORDS:
        return SPECIAL_WORDS[low]
    if t in SPECIAL_WORDS:
        return SPECIAL_WORDS[t]
    if t.startswith("~"):
        t = os.path.expanduser(t)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", t) and not t.startswith("/"):
        if low.startswith("computer:"):
            return COMPUTER
        if low.startswith("trash:"):
            return norm_uri(t)
        return Gio.File.new_for_uri(t).get_uri()
    if not t.startswith("/"):
        base = local_path(cwd_uri) if cwd_uri else None
        t = os.path.join(base or home_dir(), t)
    return Gio.File.new_for_path(os.path.normpath(t)).get_uri()


def edit_text(uri):
    """주소 창을 고쳐 쓸 때 보일 글 — 경로는 경로로, 특별한 곳은 그 이름"""
    if uri in SPECIAL_TITLES:
        return SPECIAL_TITLES[uri]
    p = local_path(uri)
    if p:
        return p
    return GLib.uri_unescape_string(uri, None) or uri


# ── 글자 모양 ────────────────────────────────────────────────
def fmt_size(n):
    """윈도우처럼 1024 단위 — 512바이트 · 1.2 KB · 34.5 MB · 120 GB"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n}바이트"
    v = float(n)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        v /= 1024.0
        if v < 1024 or unit == "PB":
            if v < 100:
                return f"{v:.1f} {unit}"
            return f"{v:.0f} {unit}"
    return ""


def fmt_size_short(n):
    """용량 막대 밑 글 — 소수점 없이 (475 GB)"""
    v = float(max(0, int(n)))
    for unit in ("바이트", "KB", "MB", "GB", "TB", "PB"):
        if v < 1024 or unit == "PB":
            if unit == "바이트":
                return f"{int(v)}바이트"
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
        v /= 1024.0
    return ""


def fmt_date(ts):
    """2026-09-25 오후 3:21 (윈도우 한국어 표기)"""
    if not ts:
        return ""
    try:
        t = time.localtime(ts)
    except (OverflowError, OSError, ValueError):
        return ""
    h = t.tm_hour % 12 or 12
    return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d} {'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d}"


def parse_trash_date(s):
    """trash::deletion-date (2026-09-25T15:21:05, 현지 시각) → 초"""
    if not s:
        return 0
    try:
        return int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, OverflowError):
        return 0


def count_text(n):
    return f"항목 {n:,}개"


def display_basename(path):
    return GLib.filename_display_basename(path) if path else ""


def display_dir(path):
    """원래 위치·검색 위치 칸 — 홈 아래면 ~ 대신 그대로 (윈도우도 전체 경로를 보인다)"""
    return GLib.filename_display_name(path) if path else ""


# ── 이름 비교 ────────────────────────────────────────────────
_DIGITS = re.compile(r"(\d+)")


def natural(s):
    """자연스러운 순서 (파일2 < 파일10) · 대소문자 무시.
    re.split 은 글·숫자·글… 차례라 같은 자리끼리는 늘 같은 종류끼리 비교된다"""
    parts = _DIGITS.split((s or "").casefold())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def split_ext(name, is_dir):
    """이름 바꾸기에서 처음 고를 부분 — 확장명 앞까지 (윈도우처럼). 폴더·.bashrc 는 전부"""
    if is_dir:
        return len(name)
    i = name.rfind(".")
    return i if i > 0 else len(name)


# ── 목록 한 줄 ───────────────────────────────────────────────
ATTRS = ",".join([
    "standard::name", "standard::display-name", "standard::edit-name", "standard::type",
    "standard::size", "standard::icon", "standard::content-type", "standard::fast-content-type",
    "standard::is-hidden", "standard::is-backup", "standard::is-symlink", "standard::target-uri",
    "time::modified", "access::can-rename", "access::can-delete", "access::can-trash",
    "access::can-write", "access::can-execute", "unix::mode", "unix::uid", "trash::orig-path",
    "trash::deletion-date", "trash::item-count",
])

FOLDER_ICON = Gio.ThemedIcon.new_from_names(["folder"])
FILE_ICON = Gio.ThemedIcon.new_from_names(["text-x-generic", "unknown"])

_desc_cache = {}


def type_desc(ctype, is_dir, name=None):
    """유형 칸 — 폴더는 윈도우처럼 '파일 폴더'. 번역이 빠진 형식은 fileops.type_description 이 한국어로"""
    if is_dir:
        return "파일 폴더"
    if not ctype:
        return "파일"
    ext = os.path.splitext(name or "")[1].lower()
    d = _desc_cache.get((ctype, ext))
    if d is None:
        if fileops is not None:
            d = fileops.type_description(ctype, name)
        else:
            try:
                d = Gio.content_type_get_description(ctype) or ctype
            except Exception:
                d = ctype
        _desc_cache[(ctype, ext)] = d
    return d


def thumbable(ctype):
    """썸네일을 만들 만한 것 — 그림 · 동영상 (· PDF)"""
    if not ctype:
        return False
    return ctype.startswith("image/") or ctype.startswith("video/") or ctype == "application/pdf"


class Entry:
    """목록의 한 항목 — 모델의 행에 보이는 글자는 여기서 나온다"""
    __slots__ = ("uri", "gfile", "name", "edit", "is_dir", "size", "mtime", "ctype", "gicon",
                 "hidden", "info", "orig", "dtime", "loc", "mode", "uid", "target", "key",
                 "thumb", "thumb_req", "can_rename", "can_delete", "can_trash", "is_link",
                 "short", "short_w")

    def __init__(self):
        self.info = None
        self.orig = ""
        self.dtime = 0
        self.loc = ""
        self.mode = 0
        self.uid = -1
        self.target = None
        self.key = None
        self.thumb = None                   # {크기: 그림}
        self.thumb_req = 0                  # 요청한 썸네일 크기 (다시 요청하지 않게)
        self.can_rename = self.can_delete = self.can_trash = True
        self.is_link = False
        self.short = None                   # 아이콘 보기에 넣은 이름 (두 줄로 자른 것)
        self.short_w = None                 # 그 이름을 잰 폭·글꼴 (NameFitter.key)

    @classmethod
    def from_info(cls, parent, info, gfile=None):
        e = cls()
        e.gfile = gfile or parent.get_child(info.get_name())
        e.uri = e.gfile.get_uri()
        e.name = info.get_display_name() or info.get_name() or ""
        e.edit = info.get_edit_name() or e.name
        ft = info.get_file_type()
        e.is_dir = ft in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE)
        e.size = 0 if e.is_dir else info.get_size()
        e.mtime = info.get_attribute_uint64("time::modified")
        e.ctype = info.get_content_type() or info.get_attribute_string("standard::fast-content-type") or ""
        e.gicon = info.get_icon() or (FOLDER_ICON if e.is_dir else FILE_ICON)
        e.hidden = info.get_is_hidden() or info.get_is_backup()   # GIO 가 점 파일과 폴더의 .hidden 목록을 본다
        e.is_link = info.get_is_symlink()
        if info.has_attribute("unix::mode"):
            e.mode = info.get_attribute_uint32("unix::mode")
            e.uid = info.get_attribute_uint32("unix::uid")
        if info.has_attribute("access::can-rename"):
            e.can_rename = info.get_attribute_boolean("access::can-rename")
        if info.has_attribute("access::can-delete"):
            e.can_delete = info.get_attribute_boolean("access::can-delete")
        if info.has_attribute("access::can-trash"):
            e.can_trash = info.get_attribute_boolean("access::can-trash")
        t = info.get_attribute_string("standard::target-uri")
        e.target = t or None
        op = info.get_attribute_byte_string("trash::orig-path") if info.has_attribute("trash::orig-path") else None
        if op:
            e.orig = display_dir(os.path.dirname(op))
            e.dtime = parse_trash_date(info.get_attribute_string("trash::deletion-date"))
        return e

    @classmethod
    def from_path(cls, path, name, is_dir, size, mtime, mode=0, loc=""):
        """검색 결과 — os.scandir 로 모은 것 (내용 유형은 이름으로 짐작: 파일을 열지 않는다)"""
        e = cls()
        e.gfile = Gio.File.new_for_path(path)
        e.uri = e.gfile.get_uri()
        e.name = GLib.filename_display_name(name)
        e.edit = e.name
        e.is_dir = is_dir
        e.size = 0 if is_dir else size
        e.mtime = int(mtime)
        e.mode = mode
        if is_dir:
            e.ctype = "inode/directory"
            e.gicon = FOLDER_ICON
        else:
            ct, _unsure = Gio.content_type_guess(name, None)
            e.ctype = ct or "application/octet-stream"
            e.gicon = Gio.content_type_get_icon(e.ctype) or FILE_ICON
        e.hidden = name.startswith(".")
        e.loc = loc
        return e

    @property
    def path(self):
        return self.gfile.get_path()

    @property
    def is_exec_file(self):
        return (not self.is_dir) and bool(self.mode & 0o111) and stat.S_ISREG(self.mode or stat.S_IFREG)


# 정렬 항목: 이름 · 수정한 날짜 · 유형 · 크기 (+ 휴지통의 원래 위치 · 삭제한 날짜, 검색의 위치)
SORT_FIELDS = ("name", "mtime", "type", "size", "orig", "dtime", "loc")
SORT_LABELS = {"name": "이름", "mtime": "수정한 날짜", "type": "유형", "size": "크기",
               "orig": "원래 위치", "dtime": "삭제한 날짜", "loc": "위치"}


def sort_key(e, field):
    """오름차순 열쇠 — 폴더가 늘 먼저, 끝의 URI 로 같은 열쇠가 둘 생기지 않게"""
    grp = 0 if e.is_dir else 1
    nat = natural(e.name)
    if field == "mtime":
        return (grp, e.mtime, nat, e.uri)
    if field == "type":
        return (grp, type_desc(e.ctype, e.is_dir, e.name).casefold(), nat, e.uri)
    if field == "size":
        return (grp, e.size, nat, e.uri)
    if field == "orig":
        return (grp, e.orig.casefold(), nat, e.uri)
    if field == "dtime":
        return (grp, e.dtime, nat, e.uri)
    if field == "loc":
        return (grp, e.loc.casefold(), nat, e.uri)
    return (grp, nat, e.uri)


# ── 아이콘 ───────────────────────────────────────────────────
def theme_icon(names):
    """후보 중 테마에 있는 첫 이름"""
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if n and th.has_icon(n):
            return n
    return "folder" if th.has_icon("folder") else "image-missing"


def image(names, size=16):
    img = Gtk.Image.new_from_icon_name(theme_icon(names), Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


class IconCache:
    """GIcon·크기 → 그림. 한 폴더의 파일 대부분이 같은 아이콘이라 10,000개여도 몇십 번만 읽는다.
    아이콘 테마가 바뀌면(다크 ↔ 라이트) 비운다"""

    def __init__(self):
        self._c = {}
        Gtk.IconTheme.get_default().connect("changed", lambda *_: self._c.clear())

    def get(self, gicon, size):
        k = (gicon.to_string() if gicon else "", size)
        pb = self._c.get(k)
        if pb is not None:
            return pb
        th = Gtk.IconTheme.get_default()
        pb = None
        try:
            if gicon is not None:
                info = th.lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
                # Papirus 는 22px 보다 작은 장소(places — 폴더·드라이브) 아이콘을 글자색 한 가지로 그린다 →
                #   자세히 보기의 폴더가 회색이었다. 윈도우처럼 작아도 색 있는 폴더가 보이게 24px 그림을 줄여 쓴다
                if info is not None and size < 22 and "/places/" in (info.get_filename() or ""):
                    big = th.lookup_by_gicon(gicon, 24, 0)
                    fn = big.get_filename() if big is not None else None
                    if fn and fn.endswith(".svg"):
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(fn, size, size)
                if pb is None and info is not None:
                    pb = info.load_icon()
        except GLib.Error:
            pb = None
        if pb is None:
            for n in ("text-x-generic", "unknown", "image-missing"):
                try:
                    pb = th.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
                    break
                except GLib.Error:
                    continue
        if pb is None:
            pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
            pb.fill(0)
        self._c[k] = pb
        return pb


ICONS = None


def icons():
    global ICONS
    if ICONS is None:
        ICONS = IconCache()
    return ICONS


def fit_pixbuf(pb, box):
    """썸네일을 box×box 안에 비율 그대로"""
    w, h = pb.get_width(), pb.get_height()
    if w <= 0 or h <= 0:
        return pb
    s = min(box / w, box / h)
    if s >= 1.0:                            # 작은 그림은 키우지 않는다 (흐려진다)
        return pb
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    return pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)


class NameFitter:
    """아이콘 보기의 이름을 두 줄로 — 넘치면 끝을 '…' 로 (윈도우 탐색기처럼).
    GtkCellRendererText 는 여러 줄에 말줄임을 못 하므로 글 자체를 잘라 넣는다.
    재는 데 드는 값이 커서(Pango) 화면에 보이는 항목만 잰다 — folder.FolderModel.fit_range.
    폭마다 결과를 기억한다 (큰 아이콘 ↔ 보통 아이콘을 오가도 다시 재지 않게)"""

    def __init__(self, widget):
        self._w = widget
        self._layout = None
        self.width = 0
        self._caches = {}
        self._cache = {}
        self._stamp = 0
        self.key = (0, 0)                  # (폭, 글꼴 세대) — 이 값이 바뀌면 잰 이름이 낡은 것

    def set_width(self, px):
        if px != self.width:
            self.width = px
            self._cache = self._caches.setdefault(px, {})
            self._layout = None
            self.key = (px, self._stamp)

    def _lay(self):
        if self._layout is None:
            lay = self._w.create_pango_layout("")   # 보기의 글꼴(CSS) 그대로
            lay.set_width(self.width * Pango.SCALE)
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            self._layout = lay
        return self._layout

    def style_changed(self):
        """글꼴이 바뀌면 다시 잰다"""
        self._caches = {}
        self._cache = self._caches.setdefault(self.width, {})
        self._layout = None
        self._stamp += 1
        self.key = (self.width, self._stamp)

    def line_height(self):
        lay = self._w.create_pango_layout("가Ag\n가Ag")
        return lay.get_pixel_size()[1]

    def cached(self, name):
        """잰 적이 있으면 그 결과, 아니면 None (짧은 이름은 재지 않아도 안다)"""
        if not self.width or len(name) <= 8:
            return name
        return self._cache.get(name)

    def fit(self, name):
        if not self.width or len(name) <= 8:
            return name
        hit = self._cache.get(name)
        if hit is not None:
            return hit
        lay = self._lay()
        lay.set_text(name, -1)
        if lay.get_line_count() <= 2:
            out = name
        else:
            ln = lay.get_line_readonly(1)
            end = ln.start_index + ln.length
            head = name.encode("utf-8")[:end].decode("utf-8", "ignore")
            out = head + "…"
            while head:
                head = head[:-1]
                out = head.rstrip() + "…"
                lay.set_text(out, -1)
                if lay.get_line_count() <= 2:
                    break
        if len(self._cache) > 50000:
            self._cache.clear()
        self._cache[name] = out
        return out


# ── 드라이브 (왼쪽 창 · 내 PC) ───────────────────────────────
class Drive:
    """내 PC 의 드라이브 하나 — 루트 파일 시스템 · 볼륨(연결됐든 아니든) · 볼륨 없는 마운트"""
    __slots__ = ("key", "name", "gicon", "volume", "mount", "root_uri", "removable", "can_eject",
                 "can_unmount")

    def __init__(self, key, name, gicon, volume=None, mount=None, root_uri=None):
        self.key = key
        self.name = name
        self.gicon = gicon
        self.volume = volume
        self.mount = mount
        self.root_uri = root_uri
        self.removable = False
        self.can_eject = False
        self.can_unmount = False


def list_drives(vm):
    """Gio.VolumeMonitor → [Drive] (루트 먼저). 모두 메인 스레드의 빠른 호출 (디스크를 읽지 않는다)"""
    out = [Drive("root", ROOT_LABEL, Gio.ThemedIcon.new_from_names(["drive-harddisk-system", "drive-harddisk"]),
                 root_uri="file:///")]
    seen = set()
    for v in vm.get_volumes():
        m = v.get_mount()
        if m is not None and m.is_shadowed():
            continue
        root = m.get_root().get_uri() if m is not None else None
        if root in ("file:///", None) and m is not None:
            continue
        d = Drive("vol:" + (v.get_uuid() or v.get_identifier("unix-device") or v.get_name() or str(id(v))),
                  v.get_name() or "볼륨", v.get_icon(), volume=v, mount=m, root_uri=root)
        drv = v.get_drive()
        d.removable = bool(v.can_eject() or (drv is not None and (drv.is_removable() or drv.is_media_removable())))
        d.can_eject = bool((m is not None and m.can_eject()) or v.can_eject())
        d.can_unmount = bool(m is not None and m.can_unmount())
        if root:
            seen.add(root)
        out.append(d)
    for m in vm.get_mounts():
        if m.get_volume() is not None or m.is_shadowed():
            continue
        root = m.get_root().get_uri()
        if root in seen or root == "file:///":
            continue
        d = Drive("mnt:" + root, m.get_name() or root, m.get_icon(), mount=m, root_uri=root)
        d.removable = True
        d.can_eject = bool(m.can_eject())
        d.can_unmount = bool(m.can_unmount())
        out.append(d)
    return out


def mount_names(vm):
    """마운트 뿌리 경로 → 보이는 이름 (주소 창의 첫 칸)"""
    out = {"/": ROOT_LABEL}
    for m in vm.get_mounts():
        try:
            p = m.get_root().get_path()
        except Exception:
            p = None
        if p and p != "/" and not m.is_shadowed():
            out[os.path.normpath(p)] = m.get_name() or display_basename(p)
    return out


# ── 주소 창 칸 ───────────────────────────────────────────────
def crumbs(uri, mounts):
    """[(보이는 이름, 위치 URI)] — 윈도우 주소 창처럼 '내 PC › 문서 › 폴더'"""
    if uri in SPECIAL_TITLES and uri != TRASH:
        return [(SPECIAL_TITLES[uri], uri)]
    if is_in_trash(uri):
        out = [("휴지통", TRASH)]
        f = Gio.File.new_for_uri(uri)
        parts = []
        root = Gio.File.new_for_uri(TRASH)
        while f is not None and not f.equal(root):
            parts.append(f)
            f = f.get_parent()
        for p in reversed(parts):
            out.append((GLib.uri_unescape_string(p.get_basename() or "", None) or "?", p.get_uri()))
        return out
    path = local_path(uri)
    if path:
        path = os.path.normpath(path)
        home = home_dir()
        anchors = [(p, label, True) for p, label, _i in favorites()]
        anchors.append((home, user_name(), False))
        anchors += [(p, n, True) for p, n in mounts.items()]
        best = None
        for p, label, under_pc in anchors:
            if path == p or path.startswith(p.rstrip("/") + "/"):
                if best is None or len(p) > len(best[0]):
                    best = (p, label, under_pc)
        p, label, under_pc = best or ("/", ROOT_LABEL, True)
        out = [("내 PC", COMPUTER)] if under_pc else []
        out.append((label, uri_of_path(p)))
        rest = path[len(p):].strip("/") if path != p else ""
        cur = p
        for seg in [s for s in rest.split("/") if s]:
            cur = os.path.join(cur, seg)
            out.append((GLib.filename_display_name(seg), uri_of_path(cur)))
        return out
    # 그 밖의 GIO 위치 (smb:// · sftp:// · mtp:// …)
    f = Gio.File.new_for_uri(uri)
    chain = []
    while f is not None:
        chain.append(f)
        f = f.get_parent()
    out = []
    for i, g in enumerate(reversed(chain)):
        if i == 0:
            u = g.get_uri()
            label = GLib.uri_unescape_string(u.rstrip("/"), None) or u
            label = label.split("://", 1)[-1] or label
        else:
            label = GLib.uri_unescape_string(g.get_basename() or "", None) or "?"
        out.append((label, g.get_uri()))
    return out


def title_of(uri, mounts):
    if uri in SPECIAL_TITLES:
        return SPECIAL_TITLES[uri]
    c = crumbs(uri, mounts)
    return c[-1][0] if c else uri


def location_icon(uri):
    """주소 창 앞 · 창 제목 옆 아이콘 후보"""
    if uri in SPECIAL_ICONS:
        return SPECIAL_ICONS[uri]
    if is_in_trash(uri):
        return SPECIAL_ICONS[TRASH]
    p = local_path(uri)
    if p:
        p = os.path.normpath(p)
        k = known_label(p)
        if k:
            return k[1]
        if p == home_dir():
            return ["user-home", "folder"]
        if p == "/":
            return ["drive-harddisk-system", "drive-harddisk"]
    return ["folder"]


# ── 기타 ─────────────────────────────────────────────────────
def copy_text(text):
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    cb.set_text(text, -1)
    cb.store()


def quote_path(p):
    """경로 복사 — 윈도우처럼 큰따옴표로 감싼다 (공백이 있어도 붙여넣기 쉽게)"""
    return f'"{p}"'


def idle(fn, *args):
    """작업 스레드 → 메인 스레드 (한 번만)"""
    GLib.idle_add(lambda: (fn(*args), False)[1])
