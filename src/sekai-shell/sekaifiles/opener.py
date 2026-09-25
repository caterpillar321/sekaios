"""파일 탐색기 — 파일 열기.

  · 기본 앱으로 (Gio.AppInfo.get_default_for_type) — 없으면 우리 '연결 프로그램 선택' 창
  · 연결 프로그램: 추천 앱 · 다른 앱 선택… ('항상 이 앱을 사용하여 … 파일 열기' → 기본 앱으로)
  · 실행기(.desktop): 바탕 화면(sekai-desk)과 같은 신뢰 기준 — 믿을 수 없으면 먼저 묻는다
  · 실행 파일·스크립트: 두 번 눌러도 바로 실행하지 않는다 — 실행 / 터미널에서 실행 / 내용 보기 를 묻는다
  · 터미널에서 열기 (sekai-terminal 을 그 폴더에서) · 바탕 화면 배경으로 설정 (설정 앱 저장소로)
"""
import hashlib
import os
import stat
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .common import dbg, icons  # noqa: E402

TERMINAL = "sekai-terminal"
# 시스템 앱 폴더 — root 가 둔 실행기는 믿는다 (거기에 쓸 수 있으면 이미 관리자다)
SYSTEM_APP_DIRS = [os.path.join(d, "applications") for d in GLib.get_system_data_dirs()] + \
    ["/var/lib/flatpak/exports/share/applications"]


# ── 알림 창 ──────────────────────────────────────────────────
def error(parent, title, text=""):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                          buttons=Gtk.ButtonsType.NONE, text=title)
    if text:
        d.format_secondary_text(text)
    d.add_button("확인", Gtk.ResponseType.OK)
    d.set_title("파일 탐색기")
    d.connect("response", lambda dlg, _r: dlg.destroy())
    d.show_all()


def ask(parent, title, text, buttons, on_answer, kind=Gtk.MessageType.QUESTION, default=None):
    """buttons = [(글, 응답 번호)] — on_answer(응답 번호) (창을 닫으면 부르지 않는다)"""
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind,
                          buttons=Gtk.ButtonsType.NONE, text=title)
    if text:
        d.format_secondary_text(text)
    d.set_title("파일 탐색기")
    for label, rid in buttons:
        b = d.add_button(label, rid)
        if rid == default:
            b.get_style_context().add_class("accent-btn")
    if default is not None:
        d.set_default_response(default)

    def resp(dlg, r):
        dlg.destroy()
        if r >= 0 or r in [rid for _l, rid in buttons]:
            on_answer(r)
    d.connect("response", resp)
    d.show_all()
    return d


def launch_context(widget=None):
    disp = widget.get_display() if widget is not None else Gdk.Display.get_default()
    ctx = disp.get_app_launch_context()
    ctx.set_timestamp(Gtk.get_current_event_time())
    return ctx


def add_recent(gfiles):
    rm = Gtk.RecentManager.get_default()
    for f in gfiles:
        try:
            rm.add_item(f.get_uri())
        except Exception:
            pass


def launch_app(parent, app, gfiles):
    try:
        app.launch(list(gfiles), launch_context(parent))
        add_recent(gfiles)
        return True
    except GLib.Error as e:
        error(parent, f"‘{app.get_display_name() or app.get_name()}’을(를) 실행하지 못했습니다.", e.message)
        return False


# ── 실행기(.desktop) 신뢰 — sekai-desk 와 같은 기준 ──────────
def launcher_trusted(path):
    """실행 권한이 있고 내 파일이며 신뢰 표시(metadata::trusted 또는 Thunar 의 체크섬)가 있어야 한다.
    시스템 앱 폴더에 root 가 둔 것은 믿는다. 브라우저로 받은 '청구서.pdf.desktop' 은 어느 것도 아니다."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    real = os.path.realpath(path)
    if st.st_uid == 0 and any(real.startswith(d.rstrip("/") + "/") for d in SYSTEM_APP_DIRS):
        return True
    if st.st_uid != os.getuid() or not os.access(path, os.X_OK):
        return False
    try:
        info = Gio.File.new_for_path(path).query_info(
            "metadata::trusted,metadata::xfce-exe-checksum", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    if info.get_attribute_as_string("metadata::trusted") == "true":
        return True
    want = info.get_attribute_as_string("metadata::xfce-exe-checksum")
    if not want or st.st_size > 1 << 20:
        return False
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == want
    except OSError:
        return False


def mark_trusted(path):
    """'신뢰하고 실행' — 읽을 수 있는 쪽에만 실행 권한 + 신뢰 표시 (sekai-desk 와 같게)"""
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, stat.S_IMODE(mode) | stat.S_IXUSR | ((mode & 0o044) >> 2))
    except OSError as e:
        dbg("실행 권한을 못 붙임", path, e)
    try:
        Gio.File.new_for_path(path).set_attribute_string(
            "metadata::trusted", "true", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error as e:
        dbg("신뢰 표시를 못 붙임", path, e)


def launcher_command(path):
    try:
        if os.path.getsize(path) > 1 << 20:
            return ""
        kf = GLib.KeyFile()
        kf.load_from_file(path, GLib.KeyFileFlags.NONE)
        return kf.get_string("Desktop Entry", "Exec")
    except (OSError, GLib.Error):
        return ""


def _launch_desktop_file(parent, path):
    try:
        app = Gio.DesktopAppInfo.new_from_filename(path)
    except TypeError:
        app = None
    if app is not None:
        return launch_app(parent, app, [])
    # Type=Link — 주소를 기본 앱으로
    try:
        kf = GLib.KeyFile()
        kf.load_from_file(path, GLib.KeyFileFlags.NONE)
        if kf.get_string("Desktop Entry", "Type") == "Link":
            Gio.AppInfo.launch_default_for_uri(kf.get_string("Desktop Entry", "URL"), launch_context(parent))
            return True
    except GLib.Error:
        pass
    error(parent, f"‘{os.path.basename(path)}’을(를) 실행할 수 없습니다.", "실행기 파일의 내용이 올바르지 않습니다.")
    return False


def open_desktop_files(parent, paths):
    doubt = []
    for p in paths:
        if launcher_trusted(p):
            _launch_desktop_file(parent, p)
        else:
            doubt.append(p)
    if not doubt:
        return
    names = [os.path.basename(p) for p in doubt]
    warn = "믿을 수 있는 곳에서 받은 것이 아니면 실행하지 마세요."
    if len(names) == 1:
        body = f"‘{names[0]}’\n\n프로그램을 실행하는 실행기 파일인데, 실행 권한이나 신뢰 표시가 없습니다. {warn}"
        cmd = launcher_command(doubt[0])
        if cmd:
            body += f"\n\n실행할 명령: {cmd}"
    else:
        shown = ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")
        body = f"실행기 {len(names)}개: {shown}\n\n실행 권한이나 신뢰 표시가 없습니다. {warn}"

    def answer(r):
        if r == 1:
            for p in doubt:
                mark_trusted(p)
                _launch_desktop_file(parent, p)
    ask(parent, "신뢰할 수 없는 실행기입니다", body, [("취소", Gtk.ResponseType.CANCEL), ("신뢰하고 실행", 1)],
        answer, kind=Gtk.MessageType.WARNING, default=Gtk.ResponseType.CANCEL)


# ── 실행 파일 ─────────────────────────────────────────────────
_BINARY_TYPES = ("application/x-executable", "application/x-sharedlib", "application/x-pie-executable",
                 "application/vnd.appimage", "application/x-iso9660-appimage", "application/x-elf")


def exec_kind(e):
    """두 번 눌렀을 때 실행할 수 있는 것인가 — "binary" · "script" · None.
    실행 권한이 있어야 하고, 텍스트는 #! 로 시작해야 스크립트로 본다 (USB 의 FAT·NTFS 는 모든 파일에
    실행 권한이 붙어 보여서 — 그런 .txt 는 그냥 편집기로 연다)"""
    if e.is_dir or not (e.mode & 0o111) or not stat.S_ISREG(e.mode):
        return None
    ct = e.ctype or ""
    if any(Gio.content_type_is_a(ct, t) for t in _BINARY_TYPES):
        return "binary"
    if not Gio.content_type_is_a(ct, "text/plain") and not Gio.content_type_can_be_executable(ct):
        return None
    p = e.path
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            return "script" if f.read(2) == b"#!" else None
    except OSError:
        return None


def _spawn(argv, cwd):
    try:
        la = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        la.set_cwd(cwd)
        la.unsetenv("DESKTOP_STARTUP_ID")
        la.spawnv(argv)
        return None
    except GLib.Error as e:
        return e.message


def open_executable(parent, e, kind, open_as_text):
    name = e.name
    body = "실행할 수 있는 프로그램입니다. 믿을 수 있는 곳에서 받은 것이 아니면 실행하지 마세요."
    buttons = [("취소", Gtk.ResponseType.CANCEL)]
    if kind == "script":
        body += "\n내용만 보려면 '내용 보기'를 누르세요."
        buttons.append(("내용 보기", 2))
    buttons += [("터미널에서 실행", 3), ("실행", 1)]
    path = e.path
    cwd = os.path.dirname(path)

    def answer(r):
        if r == 1:
            err = _spawn([path], cwd)
            if err:
                error(parent, f"‘{name}’을(를) 실행하지 못했습니다.", err)
        elif r == 3:
            err = _spawn([TERMINAL, "-e", path], cwd)
            if err:
                error(parent, "터미널을 열지 못했습니다.", err)
        elif r == 2:
            open_as_text()
    ask(parent, f"‘{name}’을(를) 실행하시겠습니까?", body, buttons, answer,
        kind=Gtk.MessageType.WARNING, default=Gtk.ResponseType.CANCEL)


def open_terminal(parent, path):
    err = _spawn([TERMINAL], path)
    if err:
        error(parent, "터미널을 열지 못했습니다.", err)


# ── 기본 앱 · 연결 프로그램 ───────────────────────────────────
def default_app(ctype):
    try:
        return Gio.AppInfo.get_default_for_type(ctype, False)
    except Exception:
        return None


def recommended_apps(ctype):
    """연결 프로그램 ▸ 에 보일 앱 — 기본 앱 먼저, 같은 앱 두 번 없이"""
    out, seen = [], set()
    d = default_app(ctype)
    for a in ([d] if d else []) + list(Gio.AppInfo.get_recommended_for_type(ctype) or []):
        if a is None or a.get_id() in seen:
            continue
        seen.add(a.get_id())
        out.append(a)
    return out


def ext_label(name, ctype, desc):
    """'이 .txt 파일' 의 가운데 — 확장명이 있으면 그것, 없으면 유형 설명"""
    i = name.rfind(".")
    if 0 < i < len(name) - 1 and len(name) - i <= 12:
        return name[i:].lower()
    return desc or ctype


class OpenWithDialog(Gtk.Dialog):
    """연결 프로그램 선택 — 추천 앱 먼저, 그 아래 모든 앱. '항상 …' 을 켜면 기본 앱으로 정한다"""

    def __init__(self, parent, gfiles, ctype, label, on_done=None):
        super().__init__(title="연결 프로그램 선택", transient_for=parent, modal=True)
        self.gfiles = gfiles
        self.ctype = ctype
        self.on_done = on_done
        self.set_default_size(460, 560)
        self.get_style_context().add_class("fx-dialog")
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        t = Gtk.Label(label=f"이 {label} 파일을 열 때 사용할 앱을 선택하세요.", xalign=0)
        t.set_line_wrap(True)
        t.get_style_context().add_class("fx-dialog-title")
        box.pack_start(t, False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("앱 검색")
        self.search.connect("search-changed", lambda *_: self.list.invalidate_filter())
        box.pack_start(self.search, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.get_style_context().add_class("fx-app-list")
        self.list.set_filter_func(self._filter)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda *_: self.response(Gtk.ResponseType.OK))
        self.list.connect("row-selected", lambda _l, r: self.set_response_sensitive(Gtk.ResponseType.OK, r is not None))
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        sc.add(self.list)
        sc.get_style_context().add_class("fx-app-scroll")
        box.pack_start(sc, True, True, 0)
        self.always = Gtk.CheckButton(label=f"항상 이 앱을 사용하여 {label} 파일 열기")
        box.pack_start(self.always, False, False, 0)
        self.add_button("취소", Gtk.ResponseType.CANCEL)
        ok = self.add_button("확인", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("accent-btn")
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_response_sensitive(Gtk.ResponseType.OK, False)

        rec = recommended_apps(ctype) if ctype else []
        rec_ids = {a.get_id() for a in rec}
        others = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in rec_ids]
        others.sort(key=lambda a: (a.get_display_name() or a.get_name() or "").casefold())
        for a in rec:
            self._add(a, True)
        for a in others:
            self._add(a, False)
        first = self.list.get_row_at_index(0)
        if first is not None:
            self.list.select_row(first)
        self.connect("response", self._response)
        self.show_all()

    def _add(self, app, recommended):
        r = Gtk.ListBoxRow()
        r.app = app
        r.recommended = recommended
        r.get_style_context().add_class("fx-app-row")
        h = Gtk.Box(spacing=12)
        gi_ = app.get_icon() or Gio.ThemedIcon.new("application-x-executable")
        h.pack_start(Gtk.Image.new_from_pixbuf(icons().get(gi_, 32)), False, False, 0)
        name = app.get_display_name() or app.get_name() or app.get_id()
        lbl = Gtk.Label(label=name, xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        h.pack_start(lbl, True, True, 0)
        r.add(h)
        r.search_text = " ".join(x for x in (name, app.get_id() or "", app.get_executable() or "")).casefold()
        self.list.add(r)

    def _filter(self, row):
        q = self.search.get_text().strip().casefold()
        return not q or q in row.search_text

    def _header(self, row, before):
        want = None
        if before is None:
            want = "추천 앱" if row.recommended else "다른 앱"
        elif before.recommended and not row.recommended:
            want = "다른 앱"
        if want is None:
            row.set_header(None)
            return
        h = row.get_header()
        if h is None or h.get_text() != want:
            lbl = Gtk.Label(label=want, xalign=0)
            lbl.get_style_context().add_class("fx-app-group")
            lbl.show()
            row.set_header(lbl)

    def _response(self, _d, r):
        row = self.list.get_selected_row()
        always = self.always.get_active()
        parent = self.get_transient_for()
        self.destroy()
        if r != Gtk.ResponseType.OK or row is None:
            return
        if always and self.ctype:
            try:
                row.app.set_as_default_for_type(self.ctype)
            except GLib.Error as e:
                error(parent, "기본 앱으로 정하지 못했습니다.", e.message)
        launch_app(parent, row.app, self.gfiles)
        if self.on_done:
            self.on_done()


def choose_app(parent, entries):
    """다른 앱 선택… — 첫 항목의 유형 기준"""
    if not entries:
        return
    e = entries[0]
    from .common import type_desc
    OpenWithDialog(parent, [x.gfile for x in entries], e.ctype,
                   ext_label(e.name, e.ctype, type_desc(e.ctype, e.is_dir, e.name)))


def open_with_default(parent, entries):
    """파일들을 기본 앱으로 — 같은 앱끼리 묶어 한 번에. 앱이 없으면 연결 프로그램 선택"""
    groups = {}
    order = []
    none = []
    for e in entries:
        app = default_app(e.ctype) if e.ctype else None
        if app is None:
            none.append(e)
            continue
        k = app.get_id() or id(app)
        if k not in groups:
            groups[k] = (app, [])
            order.append(k)
        groups[k][1].append(e.gfile)
    for k in order:
        app, files = groups[k]
        launch_app(parent, app, files)
    if none:
        choose_app(parent, none)


def open_as_text(parent, e):
    """스크립트 '내용 보기' — 글 편집기(text/plain 의 기본 앱)로"""
    app = default_app("text/plain")
    if app is None:
        choose_app(parent, [e])
        return
    launch_app(parent, app, [e.gfile])


# ── 바탕 화면 배경 ────────────────────────────────────────────
def set_wallpaper(parent, path, on_done=None):
    """설정 앱의 저장소로 — settings.json 에 적고 sekai-wallpaper --apply (작업 스레드에서)"""
    def work():
        err = None
        try:
            from sekaisettings.store import Store
            Store().set("wallpaper", "path", path)
        except Exception as ex:
            err = str(ex)

        def done():
            if err:
                error(parent, "바탕 화면 배경을 바꾸지 못했습니다.", err)
            elif on_done:
                on_done()
            return False
        GLib.idle_add(done)
    threading.Thread(target=work, daemon=True, name="sekai-files-wall").start()
