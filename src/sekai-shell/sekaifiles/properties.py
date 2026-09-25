"""속성 창 — 윈도우의 "<이름> 속성" (일반 · 보안 탭, 드라이브는 사용 공간 그림).

show_properties(gfiles, parent=None)
    창을 띄우고 곧바로 돌아온다 (모달 아님, 같은 항목이면 떠 있는 창을 앞으로).
    일반: 아이콘·이름(바꾸기) · 파일 형식 "텍스트 문서(.txt)" · 연결 프로그램 [변경…] · 위치 ·
          크기 "1.23 MB (1,290,000 바이트)" · 디스크 할당 크기 · (폴더) 내용 "파일 N개, 폴더 M개" — 작업 스레드가
          세며 바로바로 고친다(창을 닫으면 멈춘다) · 만든/수정한/액세스한 날짜 "2026년 9월 25일 금요일, 오후 11:07:12" ·
          특성: 읽기 전용(주인 쓰기 권한) · 숨김(그 폴더의 .hidden — 이름을 바꾸지 않는다)
    보안: 소유자·그룹 · 소유자/그룹/다른 사용자마다 "읽기 및 쓰기"·"읽기 전용"·"없음"
          (폴더: "만들기 및 삭제"·"파일 보기"·"없음") · "프로그램으로 실행 허용". 주인만 바꿀 수 있다
    여러 개: "파일 N개, 폴더 M개", 모두 합한 크기, 같은 위치면 "모두 /경로" 아니면 "여러 위치"
    드라이브(마운트 뿌리): 종류·파일 시스템 · 사용 중인/사용 가능한 공간·용량(바이트까지) · 강조색 고리 그림
    [확인] 바꾼 것을 적용하고 닫기 · [취소] · [적용] (바꾼 것이 있을 때만)
choose_app(content_type, parent, on_chosen=None, ext="")
    "연결 프로그램" 창 — 추천 앱 + 다른 앱. 고르면 그 형식의 기본 앱으로 정하고 on_chosen(Gio.AppInfo)
fmt_long_date(ts) -> "2026년 9월 25일 금요일, 오후 11:07:12"
CSS — 이 창만의 모양 (fileops.CSS 위에). 처음 창을 띄울 때 스스로 얹는다
"""
import grp
import math
import os
import pwd
import re
import stat
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import fileops as F  # noqa: E402
from sekaishell.fileops import display_text, fmt_size, fmt_size_exact  # noqa: E402

CSS = """
.sekai-fo.fo-props notebook > stack { padding: 14px 4px 4px 4px; }
.sekai-fo.fo-props label.fo-pkey { color: @fo_text2; font-size: 13px; }
.sekai-fo.fo-props label.fo-pval { font-size: 13px; }
.sekai-fo.fo-props label.fo-sec { font-weight: 600; margin-top: 4px; }
.sekai-fo.fo-props entry.fo-name { font-size: 14px; }
.sekai-fo.fo-props .fo-note { background: alpha(@accent, 0.10); border: 1px solid alpha(@accent, 0.28);
                               border-radius: 6px; padding: 8px 10px; }
.sekai-fo.fo-props button.fo-small { padding: 3px 12px; }
"""

ATTRS = "standard::*,time::*,unix::*,owner::*,access::*,id::filesystem,trash::orig-path,trash::deletion-date"
_WEEK = "월화수목금토일"
_open = {}
ME = os.getuid()


def fmt_long_date(ts):
    """2026년 9월 25일 금요일, 오후 11:07:12 (윈도우 속성 창의 모양)"""
    if not ts:
        return "알 수 없음"
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    ampm = "오전" if t.tm_hour < 12 else "오후"
    return (f"{t.tm_year}년 {t.tm_mon}월 {t.tm_mday}일 {_WEEK[t.tm_wday]}요일, "
            f"{ampm} {h}:{t.tm_min:02d}:{t.tm_sec:02d}")


def _install_css():
    F.install_css()
    F.install_css(CSS, key="properties")


# ── 모으기 (작업 스레드) ─────────────────────────────────────
class _Item:
    """항목 하나의 정보 — 창을 만들기 전에 작업 스레드에서 모은다"""

    def __init__(self, gfile, info):
        self.gfile = gfile
        self.info = info
        self.path = gfile.get_path()
        self.name = F._info_name(gfile, info)
        self.ftype = info.get_file_type()
        self.is_dir = self.ftype == Gio.FileType.DIRECTORY
        self.is_link = info.get_is_symlink() or self.ftype == Gio.FileType.SYMBOLIC_LINK
        self.link_target = info.get_symlink_target() if self.is_link else None
        self.size = info.get_size()
        self.ctype = info.get_content_type() or ("inode/directory" if self.is_dir else "application/octet-stream")
        self.icon = info.get_icon()
        g = info.get_attribute_uint64
        self.mtime = g("time::modified") if info.has_attribute("time::modified") else 0
        self.atime = g("time::access") if info.has_attribute("time::access") else 0
        self.btime = g("time::created") if info.has_attribute("time::created") else 0
        self.has_mode = info.has_attribute("unix::mode")
        self.mode = info.get_attribute_uint32("unix::mode") if self.has_mode else 0
        self.uid = info.get_attribute_uint32("unix::uid") if info.has_attribute("unix::uid") else None
        self.gid = info.get_attribute_uint32("unix::gid") if info.has_attribute("unix::gid") else None
        self.owner = info.get_attribute_string("owner::user") or _user(self.uid)
        self.group = info.get_attribute_string("owner::group") or _group(self.gid)
        self.alloc = None
        self.hidden_file = False           # 그 폴더의 .hidden 에 적혀 있다
        self.orig_path = info.get_attribute_byte_string("trash::orig-path") if \
            info.has_attribute("trash::orig-path") else None
        self.deleted = info.get_attribute_string("trash::deletion-date") if \
            info.has_attribute("trash::deletion-date") else None
        self.drive = None                  # 드라이브 뿌리면 _Drive
        self.app = None                    # 연결 프로그램

    @property
    def dot_hidden(self):
        return (self.gfile.get_basename() or "").startswith(".")


class _Drive:
    def __init__(self):
        self.name = ""
        self.kind = "로컬 디스크"
        self.fs = ""
        self.size = self.free = self.used = 0
        self.readonly = False
        self.icon = None


def _user(uid):
    try:
        return pwd.getpwuid(uid).pw_name if uid is not None else ""
    except KeyError:
        return str(uid)


def _group(gid):
    try:
        return grp.getgrgid(gid).gr_name if gid is not None else ""
    except KeyError:
        return str(gid)


def _hidden_names(folder):
    try:
        with open(os.path.join(folder, ".hidden"), encoding="utf-8", errors="surrogateescape") as f:
            return {ln.rstrip("\n") for ln in f if ln.strip()}
    except OSError:
        return set()


_FS_NAMES = {"vfat": "FAT32", "msdos": "FAT", "exfat": "exFAT", "ntfs": "NTFS", "ntfs3": "NTFS", "fuseblk": "NTFS",
             "ext4": "ext4", "ext3": "ext3", "ext2": "ext2", "btrfs": "Btrfs", "xfs": "XFS", "f2fs": "F2FS",
             "iso9660": "CDFS", "udf": "UDF", "tmpfs": "tmpfs", "overlay": "overlay", "nfs": "NFS", "nfs4": "NFS",
             "cifs": "SMB", "smb3": "SMB", "fuse.sshfs": "SSHFS", "zfs": "ZFS", "9p": "9P"}
_NET_FS = {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "9p", "davfs", "fuse.gvfsd-fuse"}


def _mount_info(path):
    """(마운트 지점, 파일 시스템 종류, 장치) — /proc/self/mountinfo 에서 path 를 담은 가장 긴 것"""
    best = ("", "", "")
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                left, _sep, right = line.partition(" - ")
                # 공백 등은 \040 처럼 8진수로 적혀 있다
                mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), left.split()[4])
                fields = right.split()
                if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best[0]):
                    best = (mp, fields[0] if fields else "", fields[1] if len(fields) > 1 else "")
    except OSError:
        pass
    return best


def _drive_of(gfile, path):
    """드라이브 뿌리면 _Drive, 아니면 None"""
    mount = None
    try:
        mount = gfile.find_enclosing_mount(None)
        if mount is not None and not mount.get_root().equal(gfile):
            mount = None
    except GLib.Error:
        mount = None
    if mount is None and not (path and (path == "/" or os.path.ismount(path))):
        return None
    d = _Drive()
    mp, fstype, _dev = _mount_info(path) if path else ("", "", "")
    try:
        fi = gfile.query_filesystem_info("filesystem::size,filesystem::free,filesystem::used,filesystem::type,"
                                         "filesystem::readonly", None)
        d.size = fi.get_attribute_uint64("filesystem::size")
        d.free = fi.get_attribute_uint64("filesystem::free")
        d.used = fi.get_attribute_uint64("filesystem::used") if fi.has_attribute("filesystem::used") else \
            max(0, d.size - d.free)
        d.readonly = fi.get_attribute_boolean("filesystem::readonly")
        fstype = fstype or fi.get_attribute_string("filesystem::type") or ""
    except GLib.Error:
        pass
    d.fs = _FS_NAMES.get(fstype, fstype.upper() if len(fstype) <= 5 else fstype)
    if mount is not None:
        d.name = display_text(mount.get_name())
        d.icon = mount.get_icon()
        drv = mount.get_drive()
        if mount.can_eject() or (drv is not None and (drv.is_removable() or drv.is_media_removable())):
            d.kind = "이동식 드라이브"
    elif path == "/":
        d.name = "로컬 디스크"
    else:
        d.name = display_text(os.path.basename(path) or path)
    if fstype in _NET_FS or (gfile.get_uri_scheme() not in ("file", None) and mount is not None):
        d.kind = "네트워크 드라이브"
    if d.icon is None:
        d.icon = Gio.ThemedIcon.new_from_names(["drive-removable-media" if d.kind == "이동식 드라이브" else
                                                "folder-remote" if d.kind == "네트워크 드라이브" else
                                                "drive-harddisk"])
    return d


def _gather(gfiles):
    items = []
    for f in gfiles:
        info = f.query_info(ATTRS, F.NOFOLLOW, None)
        it = _Item(f, info)
        if it.path:
            try:
                it.alloc = os.lstat(it.path).st_blocks * 512
            except OSError:
                it.alloc = None
            parent = os.path.dirname(it.path.rstrip("/"))
            if parent and parent != it.path:
                it.hidden_file = (os.path.basename(it.path) in _hidden_names(parent))
        if len(gfiles) == 1 and it.is_dir and not it.is_link:
            it.drive = _drive_of(f, it.path)
        if len(gfiles) == 1 and it.ftype == Gio.FileType.REGULAR:
            it.app = Gio.AppInfo.get_default_for_type(it.ctype, False)
        items.append(it)
    return items


class _Counter:
    """폴더 안의 파일·폴더 수와 크기 — 작업 스레드가 세고, 조금씩 메인 스레드로 알린다 (창을 닫으면 멈춘다)"""

    def __init__(self, items, on_update):
        self.items = items
        self.on_update = on_update
        self.stop = threading.Event()
        self.files = self.dirs = self.size = self.alloc = 0
        self._seen = set()
        threading.Thread(target=self._run, daemon=True, name="sekai-props-count").start()

    def _snapshot(self, finished):
        if not self.stop.is_set():
            snap = (self.files, self.dirs, self.size, self.alloc, finished)
            GLib.idle_add(lambda: (not self.stop.is_set() and self.on_update(*snap), False)[1])

    def _add(self, st):
        if stat.S_ISDIR(st.st_mode):
            self.dirs += 1
        else:
            self.files += 1
            self.size += st.st_size
        if st.st_nlink > 1 and not stat.S_ISDIR(st.st_mode):
            key = (st.st_dev, st.st_ino)
            if key in self._seen:
                return
            self._seen.add(key)
        self.alloc += st.st_blocks * 512

    def _run(self):
        last = time.monotonic()
        for it in self.items:
            if it.path:
                try:
                    root = os.lstat(it.path)
                except OSError:
                    continue
                if not it.is_dir or it.is_link:
                    self._add(root)
                    continue
                if len(self.items) > 1:
                    self._add(root)             # 여러 개 — 고른 폴더도 센다 (윈도우처럼)
                stack = [it.path]
                while stack and not self.stop.is_set():
                    d = stack.pop()
                    try:
                        with os.scandir(d) as sc:
                            for e in sc:
                                try:
                                    st = e.stat(follow_symlinks=False)
                                except OSError:
                                    continue
                                self._add(st)
                                # 다른 드라이브(마운트)는 들어가지 않는다 — 폴더의 크기만 센다
                                if stat.S_ISDIR(st.st_mode) and st.st_dev == root.st_dev:
                                    stack.append(e.path)
                    except OSError:
                        pass
                    if time.monotonic() - last > 0.25:
                        last = time.monotonic()
                        self._snapshot(False)
            else:
                self._count_gio(it)
            if self.stop.is_set():
                return
        self._snapshot(True)

    def _count_gio(self, it):
        """로컬이 아닌 곳 (gvfs)"""
        if not it.is_dir:
            self.files += 1
            self.size += it.size
            return
        if len(self.items) > 1:
            self.dirs += 1
        stack = [it.gfile]
        while stack and not self.stop.is_set():
            d = stack.pop()
            try:
                en = d.enumerate_children("standard::type,standard::size,standard::name", F.NOFOLLOW, None)
                for ci in en:
                    if ci.get_file_type() == Gio.FileType.DIRECTORY:
                        self.dirs += 1
                        stack.append(d.get_child(ci.get_name()))
                    else:
                        self.files += 1
                        self.size += ci.get_size()
                en.close(None)
            except GLib.Error:
                pass


# ── 창 ───────────────────────────────────────────────────────
def _key(text):
    lbl = Gtk.Label(label=text, xalign=0, yalign=0)
    lbl.get_style_context().add_class("fo-pkey")
    return lbl


def _val(text="", selectable=True, ellipsize=False):
    lbl = Gtk.Label(label=text, xalign=0, yalign=0)
    lbl.get_style_context().add_class("fo-pval")
    lbl.set_line_wrap(not ellipsize)
    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lbl.set_max_width_chars(40)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    lbl.set_hexpand(True)
    if selectable:
        lbl.set_selectable(True)
        lbl.set_can_focus(False)
    return lbl


class _Grid(Gtk.Grid):
    def __init__(self):
        super().__init__(column_spacing=18, row_spacing=8)
        self._r = 0

    def row(self, key, value):
        w = _val(value) if isinstance(value, str) else value
        self.attach(_key(key), 0, self._r, 1, 1)
        self.attach(w, 1, self._r, 1, 1)
        self._r += 1
        return w

    def wide(self, widget):
        self.attach(widget, 0, self._r, 2, 1)
        self._r += 1
        return widget

    def sep(self):
        s = Gtk.Separator()
        s.set_margin_top(2)
        s.set_margin_bottom(2)
        return self.wide(s)


_FILE_PERMS = [(6, "읽기 및 쓰기"), (4, "읽기 전용"), (0, "없음")]
_DIR_PERMS = [(7, "만들기 및 삭제"), (5, "파일 보기"), (0, "없음")]


def _bits_text(b):
    return "".join(c if b & m else "-" for c, m in (("r", 4), ("w", 2), ("x", 1)))


class PropertiesWindow:
    def __init__(self, items, parent, key):
        _install_css()
        self.items = items
        self.key = key
        self.counter = None
        self.single = items[0] if len(items) == 1 else None
        self._sync = False
        self.perm_combos = []
        self.exec_check = None
        self.ro_check = self.hid_check = None
        self.size_lbl = self.alloc_lbl = self.contents_lbl = None
        it = self.single
        title = f"{it.name} 속성" if it else f"{items[0].name}, … 속성"
        self.win, body, foot = F.dialog_window(parent, title, modal=False, width=460)
        self.win.get_style_context().add_class("fo-props")
        self.win.connect("destroy", self._on_destroy)
        self.win.connect("key-press-event", self._on_key)

        self.nb = Gtk.Notebook()
        body.pack_start(self.nb, True, True, 0)
        if it is not None and it.drive is not None:
            self.nb.append_page(self._drive_page(it), Gtk.Label(label="일반"))
        else:
            self.nb.append_page(self._general_page(), Gtk.Label(label="일반"))
            if it is not None and it.has_mode:
                self.nb.append_page(self._security_page(it), Gtk.Label(label="보안"))

        self.ok_btn = F._button("확인", True, self._ok)
        foot.pack_start(self.ok_btn, False, False, 0)
        foot.pack_start(F._button("취소", cb=self.win.destroy), False, False, 0)
        self.apply_btn = F._button("적용", cb=self._apply)
        self.apply_btn.set_sensitive(False)
        foot.pack_start(self.apply_btn, False, False, 0)
        for b in foot.get_children():
            b.set_size_request(88, -1)
        self.win.show_all()
        self.ok_btn.grab_focus()
        self._start_count()

    # ── 일반 ──
    def _head(self, icon, name_widget):
        h = Gtk.Box(spacing=14)
        img = F._icon(icon or "text-x-generic", 40)
        img.set_valign(Gtk.Align.CENTER)
        h.pack_start(img, False, False, 0)
        h.pack_start(name_widget, True, True, 0)
        return h

    def _general_page(self):
        g = _Grid()
        items, it = self.items, self.single
        if it is not None:
            self.name_entry = Gtk.Entry()
            self.name_entry.get_style_context().add_class("fo-name")
            self.name_entry.set_text(display_text(it.gfile.get_basename() or it.name))
            self.name_entry.set_width_chars(28)
            can_rename = it.gfile.get_uri_scheme() != "trash" and it.gfile.get_parent() is not None
            self.name_entry.set_sensitive(can_rename)
            self.name_entry.connect("changed", lambda *_: self._changed())
            self.name_entry.connect("activate", lambda *_: self._ok())
            g.wide(self._head(it.icon, self.name_entry))
        else:
            self.name_entry = None
            nf = sum(1 for x in items if not x.is_dir)
            nd = len(items) - nf
            icon = Gio.ThemedIcon.new_from_names(["edit-copy", "text-x-generic"])
            g.wide(self._head(icon, _val(_count_text(nf, nd))))
        g.sep()

        if it is not None:
            if it.is_link:
                g.row("종류:", "링크")
                g.row("대상:", display_text(it.link_target or ""))
            elif it.is_dir:
                g.row("종류:", "파일 폴더")
            else:
                desc = F.type_description(it.ctype, it.gfile.get_basename())
                ext = F.split_ext(it.gfile.get_basename() or "")[1]
                g.row("파일 형식:", f"{desc}({ext})" if ext else desc)
                g.row("연결 프로그램:", self._app_row(it))
        else:
            descs = {("dir" if x.is_dir else F.type_description(x.ctype, x.gfile.get_basename())) for x in items}
            if len(descs) == 1:
                d = next(iter(descs))
                g.row("종류:", "모두 파일 폴더" if d == "dir" else f"모두 {d}")
            else:
                g.row("종류:", "여러 종류")
        g.sep()

        if it is not None:
            if it.orig_path:
                g.row("원래 위치:", display_text(os.path.dirname(it.orig_path.decode("utf-8", "replace")
                                                              if isinstance(it.orig_path, bytes) else it.orig_path)))
                if it.deleted:
                    g.row("삭제한 날짜:", fmt_long_date(_iso_ts(it.deleted)))
            else:
                g.row("위치:", _location(it.gfile))
        else:
            parents = {x.gfile.get_parent().get_uri() if x.gfile.get_parent() else "" for x in items}
            g.row("위치:", f"모두 {_location(items[0].gfile)}" if len(parents) == 1 else "여러 위치")
        counting = any(x.is_dir and not x.is_link for x in items) or len(items) > 1
        if it is not None and not counting:
            g.row("크기:", fmt_size_exact(it.size))
            g.row("디스크 할당 크기:", fmt_size_exact(it.alloc) if it.alloc is not None else "알 수 없음")
            self.size_lbl = self.alloc_lbl = self.contents_lbl = None
        else:
            self.size_lbl = g.row("크기:", "계산 중…")
            self.alloc_lbl = g.row("디스크 할당 크기:", "계산 중…")
            self.contents_lbl = g.row("내용:", "계산 중…")
        g.sep()

        if it is not None:
            g.row("만든 날짜:", fmt_long_date(it.btime))
            g.row("수정한 날짜:", fmt_long_date(it.mtime))
            g.row("액세스한 날짜:", fmt_long_date(it.atime))
            g.sep()

        box = Gtk.Box(spacing=18)
        self.ro_check = Gtk.CheckButton(label="읽기 전용")
        self.hid_check = Gtk.CheckButton(label="숨김")
        modes = [x for x in items if x.has_mode and not x.is_link]
        ro = [not (x.mode & 0o200) for x in modes]
        self._ro0 = _tri(ro)
        _set_tri(self.ro_check, self._ro0)
        self.ro_check.set_sensitive(bool(modes) and all(x.uid in (ME, None) or ME == 0 for x in modes))
        hid = [x.dot_hidden or x.hidden_file for x in items]
        self._hid0 = _tri(hid)
        _set_tri(self.hid_check, self._hid0)
        if all(x.dot_hidden for x in items):
            self.hid_check.set_sensitive(False)
            self.hid_check.set_tooltip_text("이름이 '.'으로 시작하는 항목은 늘 숨겨집니다.")
        elif not all(x.path for x in items):
            self.hid_check.set_sensitive(False)
        self.ro_check.connect("toggled", self._ro_toggled)
        self.hid_check.connect("toggled", lambda b: (b.set_inconsistent(False), self._changed()))
        box.pack_start(self.ro_check, False, False, 0)
        box.pack_start(self.hid_check, False, False, 0)
        g.row("특성:", box)
        return g

    def _app_row(self, it):
        box = Gtk.Box(spacing=8)
        self.app_img = Gtk.Image()
        self.app_lbl = _val("", selectable=False, ellipsize=True)
        self.app_img.set_no_show_all(True)
        box.pack_start(self.app_img, False, False, 0)
        box.pack_start(self.app_lbl, True, True, 0)
        b = F._button("변경…", cb=lambda: choose_app(it.ctype, self.win, self._app_chosen,
                                                     F.split_ext(it.gfile.get_basename() or "")[1]))
        b.get_style_context().add_class("fo-small")
        box.pack_start(b, False, False, 0)
        self._show_app(it.app)
        return box

    def _show_app(self, app):
        if app is not None:
            self.app_lbl.set_text(app.get_display_name() or app.get_name() or "")
            icon = app.get_icon()
            if icon is not None:
                self.app_img.set_from_gicon(icon, Gtk.IconSize.MENU)
            else:
                self.app_img.set_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
            self.app_img.set_pixel_size(18)
            self.app_img.show()
        else:
            self.app_lbl.set_text("선택한 앱 없음")
            self.app_img.clear()
            self.app_img.hide()

    def _app_chosen(self, app):
        if self.single is not None:
            self.single.app = app
        self._show_app(app)

    # ── 드라이브 ──
    def _drive_page(self, it):
        d = it.drive
        g = _Grid()
        name = Gtk.Entry()
        name.set_text(d.name)
        name.set_editable(False)
        name.set_can_focus(False)
        self.name_entry = None
        g.wide(self._head(d.icon, name))
        g.sep()
        g.row("종류:", d.kind)
        g.row("파일 시스템:", d.fs or "알 수 없음")
        g.sep()
        grid = Gtk.Grid(column_spacing=14, row_spacing=8)
        for r, (color, key, n) in enumerate((("accent", "사용 중인 공간:", d.used),
                                             ("fo_free", "사용 가능한 공간:", d.free))):
            sw = Gtk.DrawingArea()
            sw.set_size_request(14, 14)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("draw", lambda w, cr, c=color: self._swatch(w, cr, c))
            grid.attach(sw, 0, r, 1, 1)
            grid.attach(_key(key), 1, r, 1, 1)
            v = _val(f"{n:,} 바이트")
            v.set_xalign(1)
            grid.attach(v, 2, r, 1, 1)
            grid.attach(_val(fmt_size(n)), 3, r, 1, 1)
        g.wide(grid)
        g.sep()
        cap = Gtk.Grid(column_spacing=14)
        cap.attach(Gtk.Box(), 0, 0, 1, 1)
        cap.attach(_key("용량:"), 1, 0, 1, 1)
        v = _val(f"{d.size:,} 바이트")
        v.set_xalign(1)
        cap.attach(v, 2, 0, 1, 1)
        cap.attach(_val(fmt_size(d.size)), 3, 0, 1, 1)
        g.wide(cap)
        chart = Gtk.DrawingArea()
        chart.set_size_request(150, 150)
        chart.set_halign(Gtk.Align.CENTER)
        frac = (d.used / d.size) if d.size else 0.0
        chart.connect("draw", lambda w, cr: self._ring(w, cr, frac))
        chart.connect("style-updated", lambda w: w.queue_draw())
        g.wide(chart)
        cap2 = Gtk.Label(label=d.name + (" · 읽기 전용" if d.readonly else ""))
        cap2.get_style_context().add_class("fo-pkey")
        g.wide(cap2)
        self.ro_check = self.hid_check = None
        self.size_lbl = None
        return g

    @staticmethod
    def _color(widget, name):
        ctx = widget.get_style_context()
        if name == "fo_free":
            ok, c = ctx.lookup_color("fg")
            return (c.red, c.green, c.blue, 0.16) if ok else (0.5, 0.5, 0.5, 0.3)
        ok, c = ctx.lookup_color(name)
        return (c.red, c.green, c.blue, 1.0) if ok else (0.22, 0.77, 0.73, 1.0)

    def _swatch(self, w, cr, color):
        cr.set_source_rgba(*self._color(w, color))
        cr.rectangle(0, 0, w.get_allocated_width(), w.get_allocated_height())
        cr.fill()
        return False

    def _ring(self, w, cr, frac):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        r = min(W, H) / 2 - 10
        cx, cy = W / 2, H / 2
        thick = 22
        cr.set_line_width(thick)
        cr.set_source_rgba(*self._color(w, "fo_free"))
        cr.arc(cx, cy, r - thick / 2, 0, 2 * math.pi)
        cr.stroke()
        if frac > 0:
            cr.set_source_rgba(*self._color(w, "accent"))
            start = -math.pi / 2
            cr.arc(cx, cy, r - thick / 2, start, start + 2 * math.pi * min(1.0, frac))
            cr.stroke()
        return False

    # ── 보안 ──
    def _security_page(self, it):
        g = _Grid()
        g.row("개체 이름:", display_text(it.path or it.gfile.get_uri()))
        g.row("소유자:", display_text(it.owner) or "알 수 없음")
        g.row("그룹:", display_text(it.group) or "알 수 없음")
        g.sep()
        self.perm_combos = []
        self.exec_check = None
        if it.is_link:
            g.wide(F._label("링크의 권한은 링크가 가리키는 항목의 권한을 따릅니다.", "fo-sub"))
            return g
        sec = F._label("권한", "fo-sec", wrap=False)
        g.wide(sec)
        opts = _DIR_PERMS if it.is_dir else _FILE_PERMS
        editable = it.uid in (ME, None) or ME == 0
        for label, shift in (("소유자", 6), ("그룹", 3), ("다른 사용자", 0)):
            bits = (it.mode >> shift) & (7 if it.is_dir else 6)
            combo = Gtk.ComboBoxText()
            values = [v for v, _t in opts]
            for v, t in opts:
                combo.append(str(v), t)
            if bits not in values:
                combo.append(str(bits), f"특수 권한 ({_bits_text(bits)})")
            combo.set_active_id(str(bits))
            combo.set_sensitive(editable)
            combo.shift = shift
            combo.orig = bits
            combo.connect("changed", self._perm_changed)
            who = f"소유자({display_text(it.owner)})" if shift == 6 and it.owner else \
                f"그룹({display_text(it.group)})" if shift == 3 and it.group else label
            g.row(who, combo)
            self.perm_combos.append(combo)
        if not it.is_dir:
            self.exec_check = Gtk.CheckButton(label="프로그램으로 실행 허용")
            self.exec_check.set_active(bool(it.mode & 0o100))
            self.exec_check.set_sensitive(editable)
            self.exec_check.connect("toggled", lambda *_: self._changed())
            g.wide(self.exec_check)
        if not editable:
            note = F._label(f"권한은 소유자({display_text(it.owner)})만 바꿀 수 있습니다.", "fo-sub")
            g.wide(note)
        return g

    # ── 바뀐 것 ──
    def _ro_toggled(self, b):
        b.set_inconsistent(False)
        if self._sync:
            return
        it = self.single
        if it is not None and self.perm_combos:
            # 읽기 전용 ↔ 소유자 권한을 함께 맞춘다
            self._sync = True
            c = self.perm_combos[0]
            cur = int(c.get_active_id())
            want = (cur & ~2) if b.get_active() else (cur | (7 if it.is_dir else 6))
            if not c.get_model() or str(want) not in [r[1] for r in c.get_model()]:
                c.append(str(want), f"특수 권한 ({_bits_text(want)})")
            c.set_active_id(str(want))
            self._sync = False
        self._changed()

    def _perm_changed(self, combo):
        if not self._sync and combo.shift == 6 and self.ro_check is not None:
            self._sync = True
            self.ro_check.set_inconsistent(False)
            self.ro_check.set_active(not (int(combo.get_active_id()) & 2))
            self._sync = False
        self._changed()

    def _new_mode(self, it):
        """이 항목에 적용할 권한 — 바뀌지 않았으면 None"""
        if not it.has_mode or it.is_link:
            return None
        mode = it.mode & 0o7777
        if self.single is it and self.perm_combos:
            for c in self.perm_combos:
                bits = int(c.get_active_id())
                keep_mask = 0 if it.is_dir else 1               # 파일은 x 를 따로 (실행 허용)
                old = (mode >> c.shift) & 7
                mode = (mode & ~(7 << c.shift)) | (((old & keep_mask) | bits) << c.shift)
            if self.exec_check is not None:
                if self.exec_check.get_active():
                    if not mode & 0o111:
                        mode |= 0o100 | (0o010 if mode & 0o040 else 0) | (0o001 if mode & 0o004 else 0)
                    mode |= 0o100
                else:
                    mode &= ~0o111
        elif self.ro_check is not None and not self.ro_check.get_inconsistent() and \
                self.ro_check.get_active() != (self._ro0 is True):
            mode = (mode & ~0o200) if self.ro_check.get_active() else (mode | 0o200)
        return mode if mode != (it.mode & 0o7777) else None

    def _new_hidden(self, it):
        c = self.hid_check
        if c is None or not c.get_sensitive() or c.get_inconsistent() or it.dot_hidden or not it.path:
            return None
        want = c.get_active()
        return want if want != it.hidden_file else None

    def _new_name(self):
        e = self.name_entry
        if e is None or self.single is None:
            return None
        n = e.get_text().strip()
        return n if n and n != (self.single.gfile.get_basename() or "") else None

    def _dirty(self):
        if self._new_name():
            return True
        return any(self._new_mode(x) is not None or self._new_hidden(x) is not None for x in self.items)

    def _changed(self):
        self.apply_btn.set_sensitive(self._dirty())

    def _apply(self, then_close=False):
        if not self._dirty():
            if then_close:
                self.win.destroy()
            return
        modes = [(x, self._new_mode(x)) for x in self.items]
        hides = [(x, self._new_hidden(x)) for x in self.items]
        new_name = self._new_name()
        single = self.single
        if new_name:
            err = F.validate_name(new_name)
            if err:
                F.notice(self.win, "이름 바꾸기", "이름을 바꿀 수 없습니다.", err, icon="dialog-error-symbolic")
                return
        self.apply_btn.set_sensitive(False)
        self.ok_btn.set_sensitive(False)

        def work():
            errors, renamed = [], None
            for it, mode in modes:
                if mode is None:
                    continue
                try:
                    if it.path:
                        os.chmod(it.path, mode)
                    else:
                        it.gfile.set_attribute_uint32("unix::mode", mode, F.NOFOLLOW, None)
                    it.mode = mode
                except (OSError, GLib.Error) as e:
                    errors.append(f"{it.name}: {F.explain(e)}")
            for it, want in hides:
                if want is None:
                    continue
                try:
                    _set_hidden(it.path, want)
                    it.hidden_file = want
                except OSError as e:
                    errors.append(f"{it.name}: {F.explain(e)}")
            if new_name and single is not None:
                old_hidden = single.hidden_file
                gf, err = F.rename(single.gfile, new_name)
                if err:
                    errors.append(err)
                else:
                    renamed = gf
                    if old_hidden and single.path:
                        # 숨김 표시는 이름을 따라간다
                        try:
                            _set_hidden(single.path, False)
                            _set_hidden(gf.get_path(), True)
                        except OSError:
                            pass
            return errors, renamed

        def done(res, exc):
            if self.win is None:
                return
            errors, renamed = res if exc is None else ([str(exc)], None)
            self.ok_btn.set_sensitive(True)
            if renamed is not None and single is not None:
                single.gfile = renamed
                single.path = renamed.get_path()
                single.name = display_text(renamed.get_basename())
                self.win.set_title(f"{single.name} 속성")
                _open.pop(self.key, None)
                self.key = (renamed.get_uri(),)
                _open[self.key] = self
            self._ro0 = _tri([not (x.mode & 0o200) for x in self.items if x.has_mode and not x.is_link])
            for c in getattr(self, "perm_combos", []) or []:
                c.orig = int(c.get_active_id())
            if errors:
                F.notice(self.win, "속성", "일부 설정을 바꾸지 못했습니다.", "\n".join(errors),
                         icon="dialog-error-symbolic")
                self._changed()
            elif then_close:
                self.win.destroy()
            else:
                self._changed()
        F.run_in_thread(work, done)

    def _ok(self):
        self._apply(then_close=True)

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.win.destroy()
            return True
        return False

    # ── 폴더 세기 ──
    def _start_count(self):
        if getattr(self, "size_lbl", None) is None:
            return
        self.counter = _Counter(self.items, self._count_update)

    def _count_update(self, files, dirs, size, alloc, finished):
        if self.size_lbl is None or self.win is None:
            return
        self.size_lbl.set_text(fmt_size_exact(size))
        self.alloc_lbl.set_text(fmt_size_exact(alloc) if any(x.path for x in self.items) else "알 수 없음")
        self.contents_lbl.set_text(_count_text(files, dirs))

    def _on_destroy(self, *_):
        if self.counter is not None:
            self.counter.stop.set()
        _open.pop(self.key, None)
        self.win = None

    def present(self):
        if self.win is not None:
            self.win.present()


def _count_text(files, dirs):
    return f"파일 {files:,}개, 폴더 {dirs:,}개"


def _tri(values):
    """[bool] → True/False (모두 같음) · None (섞임)"""
    if not values:
        return False
    return values[0] if all(v == values[0] for v in values) else None


def _set_tri(check, v):
    check.set_inconsistent(v is None)
    check.set_active(bool(v))


def _location(gfile):
    parent = gfile.get_parent()
    if parent is None:
        return ""
    if parent.get_uri_scheme() == "trash":
        return "휴지통"
    return display_text(parent.get_path() or parent.get_parse_name())


def _iso_ts(s):
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def _set_hidden(path, on):
    """그 폴더의 .hidden 에 이름을 적거나 뺀다 (GTK 파일 선택 창·노틸러스와 같은 방식 — 이름은 그대로)"""
    folder, name = os.path.split(path.rstrip("/"))
    hf = os.path.join(folder, ".hidden")
    try:
        with open(hf, encoding="utf-8", errors="surrogateescape") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except FileNotFoundError:
        lines = []
    names = [ln for ln in lines if ln.strip()]
    if on and name not in names:
        names.append(name)
    elif not on:
        names = [n for n in names if n != name]
    if not names:
        try:
            os.unlink(hf)
        except FileNotFoundError:
            pass
        return
    tmp = f"{hf}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write("\n".join(names) + "\n")
    os.replace(tmp, hf)


def show_properties(gfiles, parent=None):
    """속성 창 (정보를 작업 스레드에서 모은 뒤 뜬다)"""
    gfiles = [F._gfile(g) for g in (gfiles or [])]
    if not gfiles:
        return
    key = tuple(sorted(g.get_uri() for g in gfiles))
    if key in _open:
        if _open[key] is not None:
            _open[key].present()
        return
    _open[key] = None                                  # 모으는 중 — 두 번 눌러도 창은 하나

    def done(items, exc):
        if exc is not None or not items:
            _open.pop(key, None)
            msg = F.explain(exc) if isinstance(exc, (GLib.Error, OSError)) else str(exc or "")
            F.notice(parent, "속성", "속성을 표시할 수 없습니다.", msg, icon="dialog-error-symbolic")
            return
        try:
            _open[key] = PropertiesWindow(items, parent, key)
        except Exception:
            _open.pop(key, None)
            raise
    F.run_in_thread(lambda: _gather(gfiles), done)


# ── 연결 프로그램 선택 ───────────────────────────────────────
def choose_app(content_type, parent, on_chosen=None, ext=""):
    """이 형식을 열 기본 앱 고르기 — 고르면 set_as_default_for_type 하고 on_chosen(app)"""
    _install_css()

    def load():
        rec = Gio.AppInfo.get_recommended_for_type(content_type) or []
        cur = Gio.AppInfo.get_default_for_type(content_type, False)
        ids = {a.get_id() for a in rec}
        others = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in ids]
        others.sort(key=lambda a: (a.get_display_name() or a.get_name() or "").casefold())
        return rec, others, cur

    def build(res, exc):
        rec, others, cur = res if exc is None else ([], [], None)
        what = ext if ext else F.type_description(content_type)
        w, body, foot = F.dialog_window(parent, "연결 프로그램", modal=True, width=460, resizable=True)
        w.set_default_size(460, 520)
        body.pack_start(F._label(f"이제부터 {display_text(what)} 파일을 열 때 사용할 앱을 선택하세요.", "fo-title",
                                 width_chars=40), False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.SINGLE)

        def add_header(text):
            r = Gtk.ListBoxRow()
            r.set_selectable(False)
            r.set_activatable(False)
            r.add(F._label(text, "fo-key", wrap=False))
            lb.add(r)

        def add_app(a):
            r = Gtk.ListBoxRow()
            r.app = a
            h = Gtk.Box(spacing=10)
            img = Gtk.Image()
            icon = a.get_icon()
            if icon is not None:
                img.set_from_gicon(icon, Gtk.IconSize.LARGE_TOOLBAR)
            else:
                img.set_from_icon_name("application-x-executable", Gtk.IconSize.LARGE_TOOLBAR)
            img.set_pixel_size(24)
            h.pack_start(img, False, False, 0)
            h.pack_start(F._label(a.get_display_name() or a.get_name() or "", wrap=False), True, True, 0)
            r.add(h)
            lb.add(r)
            return r
        first = None
        if rec:
            add_header("추천 앱")
            for a in rec:
                r = add_app(a)
                if cur is not None and a.get_id() == cur.get_id():
                    first = r
        if others:
            add_header("다른 앱")
            for a in others:
                add_app(a)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.get_style_context().add_class("fo-frame")
        sc.add(lb)
        sc.set_vexpand(True)
        body.pack_start(sc, True, True, 0)
        ok = F._button("확인", True)
        ok.set_sensitive(False)

        def choose(*_):
            r = lb.get_selected_row()
            if r is None or not hasattr(r, "app"):
                return
            app = r.app
            w.destroy()

            def setdef():
                app.set_as_default_for_type(content_type)
                return app

            def after(a, e):
                if e is not None:
                    F.notice(parent, "연결 프로그램", "기본 앱을 바꾸지 못했습니다.", F.explain(e),
                             icon="dialog-error-symbolic")
                elif on_chosen is not None:
                    on_chosen(a)
            F.run_in_thread(setdef, after)
        ok.connect("clicked", choose)
        lb.connect("row-selected", lambda _l, r: ok.set_sensitive(r is not None and hasattr(r, "app")))
        lb.connect("row-activated", choose)
        foot.pack_start(ok, False, False, 0)
        foot.pack_start(F._button("취소", cb=w.destroy), False, False, 0)
        w.connect("key-press-event", lambda _w, ev: (w.destroy(), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
        w.show_all()
        if first is not None:
            lb.select_row(first)
    F.run_in_thread(load, build)
