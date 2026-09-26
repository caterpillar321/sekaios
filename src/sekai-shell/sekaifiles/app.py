"""파일 탐색기 (sekai-files) — 앱 (한 프로세스, 부를 때마다 새 창 — 윈도우처럼).

실행:
    sekai-files                       홈
    sekai-files 경로|URI …            인자마다 창 하나 (file:// · trash:/// · computer:/// · "내 PC" · ~)
                                      파일을 주면 담긴 폴더를 열고 그 파일을 골라 둔다
    sekai-files --select 파일 …       담긴 폴더를 열고 그 파일을 골라 보이는 곳까지 굴린다
    sekai-files --extract 파일 …      창 없이 압축 풀기 창만 (압축 파일의 '연결 프로그램')
    sekai-files --service             D-Bus 로 불려 떴을 때 (org.freedesktop.FileManager1 — 창 없이 잠깐 기다린다)

D-Bus: 세션 버스에서 org.freedesktop.FileManager1 을 가진다 — ShowFolders · ShowItems · ShowItemProperties.
    크롬의 '폴더에 표시', 작업 관리자의 '파일 위치 열기' 가 이것을 부른다.
모양: settings.css(설정 앱과 같은 색) 앞에 강조색·모드를 붙이고 이 앱의 모양(FILES_CSS)을 더한다.
    설정 앱에서 모드·강조색을 바꾸면 곧바로 따라간다.
창 상태(크기 · 탐색 창 폭 · 보기 · 정렬 · 숨긴 항목 · 열 폭)는 ~/.local/state/sekai/files.json (모든 창이 함께).
"""
import json
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import theme  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402

from .common import SPECIAL_WORDS, archive, dbg, norm_uri, properties  # noqa: E402

APP_ID = "org.sekaios.Files"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
STATE = os.path.expanduser("~/.local/state/sekai/files.json")
FM1_NAME = "org.freedesktop.FileManager1"
FM1_PATH = "/org/freedesktop/FileManager1"
FM1_XML = """
<node>
  <interface name="org.freedesktop.FileManager1">
    <method name="ShowFolders">
      <arg type="as" name="URIs" direction="in"/>
      <arg type="s" name="StartupId" direction="in"/>
    </method>
    <method name="ShowItems">
      <arg type="as" name="URIs" direction="in"/>
      <arg type="s" name="StartupId" direction="in"/>
    </method>
    <method name="ShowItemProperties">
      <arg type="as" name="URIs" direction="in"/>
      <arg type="s" name="StartupId" direction="in"/>
    </method>
  </interface>
</node>
"""
SERVICE_LINGER_S = 30          # D-Bus 로 불려 떴는데 부탁이 오지 않을 때 기다릴 시간
ORPHAN_OP_LIMIT_S = 120        # 창이 없는데 끝났다는 알림이 오지 않는 작업을 기다릴 최대 시간

# settings.css 위에 얹는 이 창만의 모양 (윈도우 11 탐색기처럼)
FILES_CSS = """
@define-color fx_top mix(@surface, @fg, 0.035);
@define-color fx_sel alpha(@accent, 0.28);
.fx-window { background: @winbg; color: @fg; }
.fx-top { background: @fx_top; border-bottom: 1px solid @line; }
.fx-toolbar { padding: 8px 12px 4px 8px; }
.fx-cmdbar { padding: 2px 12px 8px 10px; }
/* 탭 — 탭 줄은 창 바탕색, 고른 탭은 아래 툴바와 같은 색으로 이어진다 (윈도우 11 탐색기) */
.fx-tabbar { background: @fx_strip; padding: 6px 8px 0 8px; }
/* 제목 표시줄 — 탭 + 창 단추 (윈도우 11 탐색기). 창 단추는 다른 창의 제목 표시줄(hyprbars)과 같은 모양 */
headerbar.fx-titlebar {
    background: @fx_strip; background-image: none; border: none; box-shadow: none; border-radius: 0;
    min-height: 0; padding: 0 0 0 8px; margin: 0;
}
headerbar.fx-titlebar .fx-tabbar { background: transparent; padding: 6px 0 0 0; }
button.fx-cap {
    background: transparent; background-image: none; border: none; border-radius: 0; box-shadow: none;
    min-width: 46px; min-height: 36px; padding: 0; color: @fg;
}
button.fx-cap:hover { background: alpha(@fg, 0.10); }
button.fx-cap:active { background: alpha(@fg, 0.16); }
button.fx-cap.close:hover { background: #c42b1c; color: #ffffff; }
button.fx-cap.close:active { background: #b22a1d; color: #ffffff; }
/* GTK 의 창 그림자·둥근 모서리는 끈다 — Hyprland 에서만. 그림자·크기 조절 자리는 Hyprland 가 창 둘레에 따로 둔다. 기본 화면 모드(X11)의 xfwm4 는
   GTK 가 그린 제목 표시줄 창에 테두리를 그리지 않으므로 GTK 의 그림자 자리(가장자리 끌어 크기 조절)를 남긴다 */
window.fx-window.fx-hypr decoration, window.fx-window.fx-hypr.csd decoration { box-shadow: none; border-radius: 0; margin: 0; border: none; }
.fx-tabbar scrolledwindow, .fx-tabbar viewport { background: transparent; border: none; }
.fx-tab { padding: 5px 5px 5px 12px; border-radius: 8px 8px 0 0; min-height: 24px; }
.fx-tab label { font-size: 13px; color: @text2; }
.fx-tab:hover { background: alpha(@fg, 0.05); }
.fx-tab.active {
    background: @fx_top;
    box-shadow: inset 0 1px alpha(@fg, 0.10), inset 1px 0 alpha(@fg, 0.07), inset -1px 0 alpha(@fg, 0.07);
}
.fx-tab.active label { color: @fg; }
button.fx-tab-close {
    background: transparent; background-image: none; border: none; box-shadow: none;
    border-radius: 4px; padding: 3px; min-height: 0; min-width: 0; opacity: 0.65;
}
button.fx-tab-close:hover { background: alpha(@fg, 0.10); opacity: 1; }
button.fx-tab-new { margin-bottom: 3px; }
button.fx-tool, button.fx-cmd, button.fx-crumb, button.fx-crumb-sep, button.fx-eject, button.fx-status-btn {
    background: transparent;
    background-image: none;
    border: 1px solid transparent;
    border-radius: 6px;
    box-shadow: none;
    min-height: 0;
    min-width: 0;
}
button.fx-tool { padding: 7px 9px; }
button.fx-cmd { padding: 6px 10px; }
button.fx-cmd label { font-size: 13px; color: @fg; }
button.fx-tool:hover, button.fx-cmd:hover, button.fx-crumb:hover, button.fx-crumb-sep:hover,
button.fx-eject:hover, button.fx-status-btn:hover { background: @hover; }
button.fx-tool:active, button.fx-cmd:active, button.fx-crumb:active, button.fx-crumb-sep:active,
button.fx-eject:active, button.fx-status-btn:active { background: @pressed; }
button.fx-tool:disabled, button.fx-cmd:disabled { opacity: 0.4; }
separator.fx-cmd-sep { background: @line; min-width: 1px; margin: 7px 2px; }
.fx-address-box {
    background: @card;
    border: 1px solid @line;
    border-radius: 6px;
    min-height: 32px;
}
.fx-crumbs { padding: 0 2px; }
.fx-crumb-icon { margin: 0 2px 0 8px; color: @text2; }
button.fx-crumb { padding: 3px 6px; }
button.fx-crumb label { font-size: 13px; color: @fg; }
button.fx-crumb-sep { padding: 3px 3px; }
button.fx-crumb-sep image { color: @text2; }
entry.fx-address-entry {
    background: transparent;
    border: none;
    border-radius: 6px;
    box-shadow: none;
    padding: 4px 10px;
    min-height: 22px;
    font-size: 13px;
}
entry.fx-search {
    background: @card;
    border: 1px solid @line;
    border-radius: 6px;
    padding: 4px 10px;
    min-height: 22px;
    font-size: 13px;
    box-shadow: none;
}
entry.fx-search:focus { border-bottom: 2px solid @accent; }
entry.fx-search image { color: @text3; }

/* 탐색 창 */
.fx-nav { background: @winbg; }
paned.fx-paned > separator { background: @line; min-width: 1px; }
.fx-nav-list, .fx-nav-list row { background: transparent; }
.fx-nav-list { padding: 6px 0; }
.fx-nav-list row.fx-nav-row {
    padding: 5px 8px;
    margin: 1px 6px;
    border-radius: 6px;
    background-size: 3px 16px;
    background-position: 0% 50%;
    background-repeat: no-repeat;
}
.fx-nav-list row.fx-nav-row label { font-size: 13px; color: @fg; }
.fx-nav-list row.fx-nav-row:hover { background-color: @hover; }
.fx-nav-list row.fx-nav-row:selected {
    background-color: @card;
    background-image: linear-gradient(@accent, @accent);
    background-size: 3px 16px;
    background-position: 0% 50%;
    background-repeat: no-repeat;
}
.fx-nav-list row.fx-nav-row:selected label { font-weight: 600; }
.fx-nav-list row.fx-nav-row:drop(active) { background-color: alpha(@accent, 0.20); box-shadow: inset 0 0 0 1px @accent; }
.fx-nav-list row.fx-nav-sep { padding: 5px 14px; min-height: 0; }
.fx-nav-list row.fx-nav-sep separator { background: @line; min-height: 1px; }
button.fx-eject { padding: 2px 4px; }
button.fx-eject image { color: @text2; }

/* 내용 */
.fx-content { background: @winbg; }
iconview.fx-icons { background-color: @winbg; color: @fg; font-size: 13px; outline-style: none; }
iconview.fx-icons:selected, iconview.fx-icons.cell:selected {
    background-color: @fx_sel;
    color: @fg;
    border-radius: 6px;
}
iconview.fx-icons:drop(active), iconview.fx-icons.cell:drop(active) {
    background-color: alpha(@accent, 0.18);
    border-radius: 6px;
    box-shadow: inset 0 0 0 1px @accent;
}
iconview.fx-icons rubberband, iconview.fx-icons .rubberband,
treeview.fx-details rubberband, treeview.fx-details .rubberband {
    background-color: alpha(@accent, 0.16);
    border: 1px solid alpha(@accent, 0.85);
}
treeview.fx-details { background-color: @winbg; color: @fg; font-size: 13px; outline-style: none; }
treeview.fx-details:hover { background-color: alpha(@fg, 0.05); }
treeview.fx-details:selected { background-color: @fx_sel; color: @fg; }
treeview.fx-details:drop(active) { background-color: alpha(@accent, 0.18); }
treeview.fx-details header button {
    background: @winbg;
    background-image: none;
    border: none;
    border-right: 1px solid @line;
    border-bottom: 1px solid @line;
    border-radius: 0;
    padding: 5px 8px;
    box-shadow: none;
}
treeview.fx-details header button:hover { background: @hover; }
treeview.fx-details header button label { color: @text2; font-size: 12px; }
entry.fx-rename {
    background: @winbg;
    border: 1px solid @accent;
    border-radius: 4px;
    padding: 1px 4px;
    min-height: 0;
    font-size: 13px;
}
label.fx-empty { color: @text2; padding: 40px 24px; font-size: 13px; }
.fx-status { padding: 3px 10px 3px 14px; border-top: 1px solid @line; background: @winbg; }
.fx-status label { font-size: 12px; color: @text2; }
button.fx-status-btn { padding: 2px 6px; }
button.fx-status-btn:checked { background: @card; border-color: @line; }
label.fx-toast {
    background: @card;
    border: 1px solid alpha(@accent, 0.45);
    border-radius: 8px;
    padding: 8px 14px;
    margin: 0 24px 16px 24px;
}

/* 홈 · 내 PC */
.fx-page, .fx-page viewport { background: @winbg; }
.fx-page-body { padding: 6px 20px 24px 20px; }
label.fx-section { font-size: 14px; font-weight: 600; color: @fg; margin: 14px 4px 8px 4px; }
flowbox.fx-tiles flowboxchild.fx-tile { padding: 10px 12px; border-radius: 8px; }
flowbox.fx-tiles flowboxchild.fx-tile:hover { background: @hover; }
flowbox.fx-tiles flowboxchild.fx-tile:selected { background: @fx_sel; }
label.fx-tile-name { font-size: 13px; color: @fg; }
label.fx-tile-sub { font-size: 12px; color: @text2; }
progressbar.fx-cap trough {
    background: alpha(@fg, 0.14);
    border: none;
    border-radius: 2px;
    min-height: 6px;
    min-width: 180px;
}
progressbar.fx-cap progress {
    background: @accent;
    border: none;
    border-radius: 2px;
    min-height: 6px;
}
progressbar.fx-cap.full progress { background: #d13438; }
label.fx-empty-inline { color: @text2; padding: 6px 8px; font-size: 13px; }

/* 메뉴 */
menu.fx-menu { padding: 4px 0; }
menu.fx-menu menuitem { padding: 6px 14px; }
menu.fx-menu menuitem label { font-size: 13px; }
label.fx-accel { color: @text3; font-size: 12px; margin-left: 28px; }
menu.fx-menu menuitem.separator, menu.fx-menu separator { background: none; min-height: 0; padding: 0; margin: 4px 0; }
menu.fx-menu menuitem.separator separator, menu.fx-menu separator { background: @line; min-height: 1px; }

/* 연결 프로그램 선택 */
label.fx-dialog-title { font-size: 15px; font-weight: 600; }
.fx-app-scroll { border: 1px solid @line; border-radius: 8px; }
.fx-app-list, .fx-app-list row { background: transparent; }
.fx-app-list row.fx-app-row { padding: 6px 10px; }
.fx-app-list row.fx-app-row:hover { background: @hover; }
.fx-app-list row.fx-app-row:selected { background: @fx_sel; }
.fx-app-list row.fx-app-row label { color: @fg; }
label.fx-app-group { font-size: 12px; font-weight: 600; color: @text2; padding: 10px 10px 4px 10px; }
"""


def _load_css(a):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    # 제목 표시줄(탭 줄)은 툴바보다 어둡게 — 고른 탭이 툴바와 이어져 보이게. 다크는 더, 라이트는 조금
    k = 0.07 if a.get("mode") == "light" else 0.22
    prelude += f"@define-color fx_strip mix(mix(@surface, @fg, 0.035), #000000, {k});\n"
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + FILES_CSS).encode())
    except GLib.Error as e:
        print("[sekai-files] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return None
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = f"{STATE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except (OSError, TypeError, ValueError) as e:
        dbg("파일 탐색기 상태 저장 실패", e)


USAGE = """사용법: sekai-files [경로|URI …]
       sekai-files --select 파일 …
       sekai-files --extract 압축파일 …
  경로 대신 '내 PC', '휴지통', '홈', computer:///, trash:/// 도 된다."""


class FilesApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.state = _load_state()
        self.vm = None
        self.drag_uris = None
        self._css = None
        self._cfg_mon = None
        self._cfg_src = 0
        self._fm_reg = 0
        self._fm_own = 0
        self._ops = 0
        self._keepalive = False
        self.connect("window-removed", lambda *_: self._keep_alive())

    # ── 시작 ──
    def do_startup(self):
        Gtk.Application.do_startup(self)
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self._css = _load_css(a)
        Gtk.Window.set_default_icon_name("system-file-manager")
        self.vm = Gio.VolumeMonitor.get()
        self._watch_settings()
        self._icons_src = 0
        Gtk.IconTheme.get_default().connect("changed", self._icons_changed)
        self._export_fm1()

    def do_shutdown(self):
        conn = self.get_dbus_connection()
        if self._fm_own:
            Gio.bus_unown_name(self._fm_own)
            self._fm_own = 0
        if conn is not None and self._fm_reg:
            conn.unregister_object(self._fm_reg)
            self._fm_reg = 0
        _save_state(self.state)
        Gtk.Application.do_shutdown(self)

    def save_state(self):
        _save_state(self.state)

    # ── 명령줄 ──
    def do_command_line(self, cl):
        args = [a for a in cl.get_arguments()[1:]]
        if any(a in ("-h", "--help") for a in args):
            return 0                            # main() 이 이미 보였다
        if "--service" in args:
            args = [a for a in args if a != "--service"]
            if not args:
                self._linger()
                return 0
        if args and args[0] == "--extract":
            files = [cl.create_file_for_arg(a) for a in args[1:] if a]
            self.extract(files)
            return 0
        select_mode = False
        targets = []                            # (위치 uri, 고를 uri 목록)
        joined = " ".join(a for a in args if not a.startswith("--"))
        if joined.casefold() in SPECIAL_WORDS and len(args) > 1:
            args = [joined]                     # 따옴표 없이 쓴 내 PC
        for a in args:
            if a == "--select":
                select_mode = True
                continue
            if a.startswith("--"):
                continue
            targets.append(self._resolve_arg(cl, a, select_mode))
        if not targets:
            targets.append((None, None))
        # 같은 폴더에서 고를 파일들은 한 창으로 (--select a b · 파일 여러 개)
        merged, order = {}, []
        for uri, sel in targets:
            if sel and uri in merged and merged[uri] is not None:
                merged[uri] += sel
                continue
            key = uri if sel else (uri, len(order))
            if sel:
                merged[uri] = list(sel)
            order.append((key, uri))
        for key, uri in order:
            self.open_window(uri, select=merged.get(uri) if key == uri else None)
        return 0

    def _resolve_arg(self, cl, a, select_mode):
        low = a.strip().casefold()
        if low in SPECIAL_WORDS:
            return SPECIAL_WORDS[low], None
        if "://" in a or a.startswith(("file:", "trash:", "computer:")):
            f = Gio.File.new_for_uri(a) if not a.lower().startswith("computer:") else None
            if f is None:
                return norm_uri("computer:///"), None
        else:
            f = cl.create_file_for_arg(os.path.expanduser(a))
        p = f.get_path()
        if p and (select_mode or (os.path.exists(p) and not os.path.isdir(p))):
            parent = f.get_parent()
            if parent is not None:
                return parent.get_uri(), [f.get_uri()]
        if select_mode and not p:
            parent = f.get_parent()
            if parent is not None:
                return parent.get_uri(), [f.get_uri()]
        return f.get_uri(), None

    # ── 창 ──
    def open_window(self, uri=None, select=None, startup_id=None):
        from .window import ExplorerWindow
        win = ExplorerWindow(self, uri, select)
        if startup_id:
            try:
                win.set_startup_id(startup_id)
            except Exception:
                pass
        win.present()
        return win

    def clipboard_changed(self):
        for w in self.get_windows():
            if hasattr(w, "_check_clipboard"):
                w._check_clipboard()

    def extract(self, files, parent=None):
        """창 없이 압축 풀기 (--extract · 압축 파일의 연결 프로그램)"""
        files = [f for f in files if f is not None]
        if not files:
            return
        if archive is None:
            print("[sekai-files] 압축 모듈을 불러오지 못했습니다", file=sys.stderr, flush=True)
            return
        self.hold()
        try:
            archive.extract(files, parent)
        except Exception as e:
            print("[sekai-files] 압축 풀기 실패:", e, file=sys.stderr, flush=True)
        GLib.timeout_add(1500, lambda: (self.release(), False)[1])     # 압축 창이 뜬 뒤에 놓는다
        GLib.timeout_add(1000, lambda: (self._keep_alive(), False)[1])

    # ── 창이 없어도 끝날 때까지 살아 있기 ──
    def track(self, done=None):
        """파일 작업의 done 을 감싼다 — 도는 작업 수를 센다 (창을 닫아도 복사가 끝날 때까지 프로세스가 살게)"""
        self._ops += 1
        called = [False]

        def wrapped(*a):
            if not called[0]:
                called[0] = True
                self._ops = max(0, self._ops - 1)
            if done is not None:
                return done(*a)
        return wrapped

    def _other_toplevels(self):
        mine = set(self.get_windows())
        out = []
        for w in Gtk.Window.list_toplevels():
            try:
                if w in mine or not w.get_visible() or w.get_window_type() != Gtk.WindowType.TOPLEVEL:
                    continue
            except Exception:
                continue
            out.append(w)
        return out

    def _keep_alive(self):
        """탐색기 창을 다 닫아도 진행 창 · 묻는 창 · 속성 창 · 도는 작업이 있으면 끝날 때까지 기다린다"""
        if self._keepalive or self.get_windows():
            return
        if not self._other_toplevels() and not self._ops:
            return
        self._keepalive = True
        self.hold()
        waited = [0]

        def poll():
            if self.get_windows():
                busy = False
            else:
                vis = bool(self._other_toplevels())
                waited[0] = 0 if vis else waited[0] + 1
                busy = vis or (self._ops > 0 and waited[0] < ORPHAN_OP_LIMIT_S)
            if busy:
                return True
            self._keepalive = False
            self.release()
            return False
        GLib.timeout_add(1000, poll)

    def _linger(self):
        self.hold()
        GLib.timeout_add_seconds(SERVICE_LINGER_S, lambda: (self.release(), False)[1])

    # ── D-Bus: org.freedesktop.FileManager1 ──
    def _export_fm1(self):
        conn = self.get_dbus_connection()
        if conn is None:
            return
        try:
            node = Gio.DBusNodeInfo.new_for_xml(FM1_XML)
            self._fm_reg = conn.register_object(FM1_PATH, node.interfaces[0], self._fm_call, None, None)
        except GLib.Error as e:
            print("[sekai-files] FileManager1 을 등록하지 못했습니다:", e.message, file=sys.stderr, flush=True)
            return
        self._fm_own = Gio.bus_own_name_on_connection(
            conn, FM1_NAME, Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE, None, None)

    def _fm_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            uris, sid = params.unpack()
        except Exception:
            uris, sid = [], ""
        uris = [u for u in uris if u]
        try:
            if method == "ShowFolders":
                for u in uris:
                    self.open_window(norm_uri(u), startup_id=sid)
            elif method == "ShowItems":
                groups, order = {}, []
                for u in uris:
                    f = Gio.File.new_for_uri(u)
                    parent = f.get_parent()
                    key = parent.get_uri() if parent is not None else u
                    if key not in groups:
                        groups[key] = []
                        order.append(key)
                    groups[key].append(f.get_uri())
                for k in order:
                    self.open_window(k, select=groups[k], startup_id=sid)
            elif method == "ShowItemProperties":
                if properties is not None and uris:
                    self.hold()
                    try:
                        properties.show_properties([Gio.File.new_for_uri(u) for u in uris], None)
                    finally:
                        GLib.timeout_add(1000, lambda: (self.release(), False)[1])
                    GLib.timeout_add(500, lambda: (self._keep_alive(), False)[1])
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
        except Exception as e:
            print("[sekai-files] FileManager1 호출 실패:", e, file=sys.stderr, flush=True)
        invocation.return_value(None)

    # ── 설정(색) 따라가기 ──
    def _watch_settings(self):
        """설정 앱이 settings.json 을 바꿔치기(원자적 저장)하므로 폴더를 본다"""
        cfg_dir = os.path.expanduser("~/.config/sekai")
        try:
            os.makedirs(cfg_dir, exist_ok=True)
            self._cfg_mon = Gio.File.new_for_path(cfg_dir).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except (GLib.Error, OSError) as e:
            dbg("설정 폴더를 볼 수 없습니다:", e)
            return

        def changed(_m, f, other, _ev):
            names = {x.get_basename() for x in (f, other) if x is not None}
            if "settings.json" not in names:
                return
            if self._cfg_src:
                GLib.source_remove(self._cfg_src)
            self._cfg_src = GLib.timeout_add(200, self._reload_theme)
        self._cfg_mon.connect("changed", changed)

    def _reload_theme(self):
        self._cfg_src = 0
        a = appearance()
        if self._css is not None:
            Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self._css)
        self._css = _load_css(a)
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        for w in self.get_windows():
            w.queue_draw()
        return False

    def _icons_changed(self, *_):
        """아이콘 테마가 바뀜 (다크 ↔ 라이트) — 목록의 그림을 새 테마로"""
        if self._icons_src:
            return

        def go():
            self._icons_src = 0
            for w in self.get_windows():
                if hasattr(w, "icon_theme_changed"):
                    try:
                        w.icon_theme_changed()
                    except Exception as e:
                        dbg("아이콘 다시 그리기 실패", e)
            return False
        self._icons_src = GLib.timeout_add(150, go)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return 0
    # 기본 화면 모드(X11)의 창 클래스도 Wayland 의 app_id 와 같게 — .desktop 의 StartupWMClass 로 작업 표시줄이 알아본다
    GLib.set_prgname(APP_ID)
    Gdk.set_program_class(APP_ID)
    return FilesApp().run([sys.argv[0]] + list(argv))
