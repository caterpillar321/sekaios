"""SekaiOS 작업 관리자 — 메인 창 (윈도우 11 작업 관리자처럼).

왼쪽 레일: 프로세스 · 성능 · 세부 정보 · 시작 앱, 아래에 실시간 업데이트 속도.
자료는 작업 스레드(taskmgr_data.Collector)가 모으고, 보이는 탭만 그 값으로 고친다.

색은 설정 앱과 같은 settings.css 에 강조색·모드를 앞에 붙여 쓰고, 이 창만의 모양(TM_CSS)을 더한다.
settings.json 이 바뀌면(설정 앱에서 모드·강조색을 바꾸면) 곧바로 따라간다.
이 창의 상태(속도·크기·탭·정렬)는 설정 앱과 섞이지 않게 ~/.local/state/sekai/taskmgr.json 에 둔다.
"""
import json
import os
import shutil
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import dbg, theme  # noqa: E402
from . import taskmgr_data as D  # noqa: E402
from .taskmgr_common import AppResolver, appearance, icon_image  # noqa: E402
from .taskmgr_perf import PerfPage  # noqa: E402
from .taskmgr_procs import DetailsPage, ProcessesPage  # noqa: E402
from .taskmgr_startup import StartupPage  # noqa: E402

APP_ID = "org.sekaios.TaskManager"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
STATE = os.path.expanduser("~/.local/state/sekai/taskmgr.json")
SPEEDS = [("fast", "빠름", 0.5), ("normal", "보통", 1.0), ("slow", "느림", 4.0), ("paused", "일시 중지", None)]
PAGE_ICONS = {
    "processes": ["view-app-grid-symbolic", "view-grid-symbolic", "applications-other"],
    "performance": ["utilities-system-monitor-symbolic", "org.gnome.SystemMonitor-symbolic", "utilities-system-monitor"],
    "details": ["view-list-symbolic", "format-justify-fill-symbolic"],
    "startup": ["system-run-symbolic", "media-playback-start-symbolic"],
}
PAGE_IDS = ["processes", "performance", "details", "startup"]

# settings.css 위에 얹는 이 창만의 모양
TM_CSS = """
.tm-window { background: @winbg; color: @fg; }
.tm-top { padding: 12px 24px 4px 24px; }
.tm-search {
    min-width: 380px;
    background: @card;
    border: 1px solid @line;
    border-bottom: 2px solid @line;
    border-radius: 8px;
    padding: 5px 10px;
}
.tm-search:focus { border-bottom-color: @accent; }
.tm-bar { padding: 6px 24px 10px 24px; }
.tm-title { font-size: 20px; font-weight: 700; color: @fg; }
.tm-body { padding: 0 16px 16px 16px; }
.tm-listbox { border: 1px solid @line; border-radius: 8px; background: @winbg; }
treeview.tm-list { background-color: @winbg; color: @fg; font-size: 13px; }
treeview.tm-list:selected { background-color: alpha(@accent, 0.28); color: @fg; }
treeview.tm-list header button {
    background: @winbg;
    background-image: none;
    border: none;
    border-bottom: 1px solid @line;
    border-right: 1px solid @line;
    border-radius: 0;
    padding: 4px 8px;
    color: @text2;
    font-size: 12px;
    box-shadow: none;
}
treeview.tm-list header button:hover { background: @hover; }
treeview.tm-list header button label { color: @text2; }
.tm-colhead-value { font-size: 15px; font-weight: 600; color: @fg; }
label.tm-colhead-name { font-size: 12px; color: @text2; }
.tm-speed-cap { padding: 0 20px; font-size: 12px; color: @text2; }
.tm-speed { margin: 4px 14px 14px 14px; }
.tm-empty { padding: 16px 4px; color: @text2; }
.tm-toast {
    background: @card;
    border: 1px solid alpha(@accent, 0.45);
    border-radius: 8px;
    padding: 8px 14px;
    margin: 0 24px 12px 24px;
}
button.tm-end:disabled { opacity: 0.55; }

/* 성능 */
.perf-side { border-right: 1px solid @line; }
.perf-list, .perf-list row { background: transparent; }
.perf-list row { padding: 8px 10px; margin: 2px 8px; border-radius: 8px; }
.perf-list row:hover { background: @hover; }
.perf-list row:selected {
    background-color: @card;
    background-image: linear-gradient(@accent, @accent);
    background-size: 3px 18px;
    background-position: 0% 50%;
    background-repeat: no-repeat;
}
.perf-title { font-size: 14px; color: @fg; }
.perf-sub { font-size: 12px; color: @text2; }
.perf-main { padding: 4px 28px 24px 28px; }
.perf-head { font-size: 26px; font-weight: 700; color: @fg; }
.perf-model { font-size: 15px; color: @text2; }
.perf-caprow { margin-top: 14px; margin-bottom: 4px; }
.perf-cap { font-size: 12px; color: @text2; }
.perf-foot { margin-top: 2px; }
.perf-stats { margin-top: 18px; }
.perf-big-val { font-size: 22px; color: @fg; font-feature-settings: "tnum"; }
.perf-kv-key { font-size: 12px; color: @text2; }
.perf-kv-val { font-size: 12px; color: @fg; }
.perf-note { font-size: 12px; color: @text2; margin-top: 8px; }
button.perf-toggle { padding: 2px 10px; font-size: 12px; }
button.perf-toggle:checked { background: alpha(@accent, 0.25); border-color: alpha(@accent, 0.6); }
"""


def _load_css(a):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + TM_CSS).encode())
    except GLib.Error as e:
        dbg("작업 관리자 CSS 오류:", e)
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
    except OSError as e:
        dbg("작업 관리자 상태 저장 실패", e)


class TaskManagerWindow(Gtk.Window):
    def __init__(self, start_page=None):
        super().__init__(title="작업 관리자")
        self.set_icon_name("utilities-system-monitor")
        self.state = _load_state()
        w, h = self.state.get("size") or (1060, 700)
        self.set_default_size(max(720, int(w)), max(480, int(h)))
        if self.state.get("maximized"):
            self.maximize()
        self.set_size_request(720, 460)
        for c in ("settings-window", "tm-window"):
            self.get_style_context().add_class(c)

        self._css = _load_css(appearance())
        self.snap = None
        self._nv_seen = False
        self.resolver = AppResolver()
        self._apps_mon = Gio.AppInfoMonitor.get()
        self._apps_mon.connect("changed", lambda *_: self.resolver.reset())
        self.x11 = None
        if not os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
            try:                                   # 기본 화면 모드(X11) — 창 목록은 libwnck 로
                from .wm import X11Hypr
                self.x11 = X11Hypr()
            except Exception as e:                 # (Wnck 가 없으면 앱 묶음 없이)
                dbg("X11 창 목록을 쓸 수 없습니다:", e)

        self.collector = D.Collector(lambda s: GLib.idle_add(self._on_sample, s))

        root = Gtk.Box(spacing=0)
        self.add(root)
        root.pack_start(self._build_sidebar(), False, False, 0)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("content")
        root.pack_start(main, True, True, 0)

        top = Gtk.Box()
        top.get_style_context().add_class("tm-top")
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("이름, 사용자 또는 PID 로 검색")
        self.search.get_style_context().add_class("tm-search")
        self.search.connect("search-changed", lambda e: self._current().set_query(e.get_text())
                            if self._current().searchable else None)
        self.search.connect("stop-search", lambda *_: self.clear_search())
        top.set_center_widget(self.search)
        main.pack_start(top, False, False, 0)

        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class("tm-bar")
        self.page_title = Gtk.Label(xalign=0)
        self.page_title.get_style_context().add_class("tm-title")
        bar.pack_start(self.page_title, False, False, 0)
        self.action_stack = Gtk.Stack()
        bar.pack_end(self.action_stack, False, False, 0)
        main.pack_start(bar, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(100)
        self.stack.get_style_context().add_class("tm-body")
        main.pack_start(self.stack, True, True, 0)

        self.toast_label = Gtk.Label(xalign=0)
        self.toast_label.get_style_context().add_class("tm-toast")
        self.toast_label.set_no_show_all(True)
        main.pack_start(self.toast_label, False, False, 0)
        self._toast_src = 0

        self.pages = {}
        for cls in (ProcessesPage, PerfPage, DetailsPage, StartupPage):
            pg = cls(self)
            self.pages[pg.id] = pg
            self.stack.add_named(pg.widget, pg.id)
            self.action_stack.add_named(pg.actions, pg.id)
        self.perf = self.pages["performance"]

        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_close)
        self.connect("window-state-event", self._on_wstate)
        self._watch_settings()

        speed = self.state.get("speed", "normal")
        self.speed.set_active_id(speed if speed in {s[0] for s in SPEEDS} else "normal")
        self._apply_speed()
        start = start_page if start_page in self.pages else self.state.get("page")
        self.select_page(start if start in self.pages else "processes")
        self.collector.start()

    # ── 왼쪽 레일 ──
    def _build_sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.get_style_context().add_class("sidebar")
        side.set_size_request(230, -1)
        head = Gtk.Box(spacing=10)
        head.get_style_context().add_class("side-head")
        head.pack_start(icon_image(["utilities-system-monitor", "org.gnome.SystemMonitor"], 22), False, False, 0)
        t = Gtk.Label(label="작업 관리자", xalign=0)
        t.get_style_context().add_class("side-title")
        head.pack_start(t, True, True, 0)
        side.pack_start(head, False, False, 0)

        self.rail = Gtk.ListBox()
        self.rail.get_style_context().add_class("side-list")
        # select_page 가 스스로 행을 고를 때 한 번 더 불리지 않게 (지금 탭이면 건너뛴다)
        self.rail.connect("row-selected", lambda _l, r: r is not None and
                          r.page_id != self.stack.get_visible_child_name() and self.select_page(r.page_id))
        self._rows = {}
        for pid, title in zip(PAGE_IDS, ("프로세스", "성능", "세부 정보", "시작 앱")):
            r = Gtk.ListBoxRow()
            r.page_id = pid
            r.get_style_context().add_class("side-row")
            h = Gtk.Box(spacing=12)
            h.pack_start(icon_image(PAGE_ICONS[pid], 18), False, False, 0)
            h.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)
            r.add(h)
            self.rail.add(r)
            self._rows[pid] = r
        side.pack_start(self.rail, True, True, 0)

        cap = Gtk.Label(label="실시간 업데이트 속도", xalign=0)
        cap.get_style_context().add_class("tm-speed-cap")
        side.pack_start(cap, False, False, 0)
        self.speed = Gtk.ComboBoxText()
        for sid, label, iv in SPEEDS:
            self.speed.append(sid, f"{label} ({iv:g}초)" if iv else label)
        self.speed.get_style_context().add_class("tm-speed")
        self.speed.connect("changed", lambda *_: self._apply_speed())
        side.pack_start(self.speed, False, False, 0)
        return side

    def _current(self):
        return self.pages[self.stack.get_visible_child_name() or "processes"]

    def select_page(self, page_id):
        pg = self.pages[page_id]
        self.stack.set_visible_child_name(page_id)
        self.action_stack.set_visible_child_name(page_id)
        self.page_title.set_text(pg.title)
        self.state["page"] = page_id
        r = self._rows[page_id]
        if self.rail.get_selected_row() is not r:
            self.rail.select_row(r)
        self.search.set_sensitive(pg.searchable)
        if pg.searchable:
            pg.set_query(self.search.get_text())
        if hasattr(pg, "on_show"):
            pg.on_show()
        self._update_wants()
        if self.snap is not None:
            if pg.wants_procs and self.snap.get("procs") is None:
                self.collector.wake()              # 프로세스를 아직 안 모았다 (일시 중지 중이어도 한 번)
            else:
                pg.refresh(self.snap, self._clients(self.snap))

    def _update_wants(self):
        pg = self._current()
        self.collector.want_procs = pg.wants_procs
        self.collector.want_clients = pg.id == "processes" and self.x11 is None
        nv = self.collector.nvidia
        if nv is not None:
            iv = self.collector.interval
            nv.set_active(pg.id == "performance" and iv is not None, iv)

    def _apply_speed(self):
        sid = self.speed.get_active_id() or "normal"
        iv = next(s[2] for s in SPEEDS if s[0] == sid)
        self.state["speed"] = sid
        self.collector.interval = iv
        self.perf.set_interval(iv)
        self._update_wants()
        self.collector.wake()

    # ── 자료 받기 (메인 스레드) ──
    def _on_sample(self, snap):
        if self.collector is None:
            return False
        self.snap = snap
        self.perf.record(snap)
        if self.collector.nvidia is not None and not self._nv_seen:
            self._nv_seen = True                   # NVIDIA 감시는 첫 스냅숏 때 생긴다 — 지금 탭에 맞춰 켠다
            self._update_wants()
        pg = self._current()
        try:
            pg.refresh(snap, self._clients(snap))
        except Exception:
            import traceback
            traceback.print_exc()
        return False

    def _clients(self, snap):
        if self.x11 is not None and self._current().id == "processes":
            try:
                return self.x11.query("clients")
            except Exception:
                return None
        return snap.get("clients")

    # ── 페이지들이 부르는 것 ──
    def new_task_button(self):
        b = Gtk.Button(label="새 작업 실행")
        b.connect("clicked", lambda *_: self.new_task())
        return b

    def saved_sort(self, page_id, sid, order):
        s = (self.state.get("sort") or {}).get(page_id)
        if isinstance(s, list) and len(s) == 2:
            try:
                return int(s[0]), Gtk.SortType(int(s[1]))
            except (TypeError, ValueError):
                pass
        return sid, order

    def remember_sort(self, sid, order):
        self.state.setdefault("sort", {})[self._current().id] = [int(sid), int(order)]

    def clear_search(self):
        self.search.set_text("")
        pg = self._current()
        if pg.searchable:
            pg.set_query("")

    def goto_details(self, pid):
        self.select_page("details")
        det = self.pages["details"]
        if self.snap is not None:
            det.refresh(self.snap)
        det.select_pid(pid)

    def switch_to(self, win):
        """창을 앞으로 (최소화돼 있으면 되살려서) — 작업 표시줄과 같은 방법"""
        addr = win.get("address")
        if not addr:
            return
        if self.x11 is not None:
            self.x11.dispatch(f"focuswindow address:{addr}")
            return

        def work():                                # 소켓 요청 — Hyprland 가 굳어 있어도 화면을 막지 않게
            if win.get("minimized"):
                raw = D.hypr_request("j/activeworkspace")
                try:
                    wid = json.loads(raw).get("id", 1) if raw else 1
                except ValueError:
                    wid = 1
                D.hypr_request(f"/dispatch movetoworkspace {wid},address:{addr}")
            D.hypr_request(f"/dispatch focuswindow address:{addr}")
        threading.Thread(target=work, daemon=True, name="taskmgr-focus").start()

    def toast(self, text, secs=5):
        self.toast_label.set_text(text)
        self.toast_label.show()
        if self._toast_src:
            GLib.source_remove(self._toast_src)

        def hide():
            self._toast_src = 0
            self.toast_label.hide()
            return False
        self._toast_src = GLib.timeout_add_seconds(secs, hide)

    # ── 새 작업 실행 ──
    def new_task(self):
        d = Gtk.Dialog(title="새 작업 만들기", transient_for=self, modal=True)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        d.add_button("확인", Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)
        box.pack_start(Gtk.Label(label="실행할 프로그램, 열 폴더·문서·주소를 입력하세요.", xalign=0), False, False, 0)
        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Label(label="열기:"), False, False, 0)
        e = Gtk.Entry()
        e.set_width_chars(40)
        e.set_activates_default(True)
        e.set_text(self.state.get("last_run", ""))
        e.select_region(0, -1)
        row.pack_start(e, True, True, 0)
        browse = Gtk.Button(label="찾아보기…")
        row.pack_start(browse, False, False, 0)
        box.pack_start(row, False, False, 0)
        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("tm-empty")
        err.set_line_wrap(True)
        err.set_no_show_all(True)
        box.pack_start(err, False, False, 0)

        def pick(*_):
            fc = Gtk.FileChooserNative.new("찾아보기", d, Gtk.FileChooserAction.OPEN, "열기", "취소")
            if fc.run() == Gtk.ResponseType.ACCEPT:
                path = fc.get_filename() or ""
                e.set_text(GLib.shell_quote(path) if " " in path else path)
            fc.destroy()
        browse.connect("clicked", pick)

        def resp(dlg, r):
            if r != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            msg = run_command(e.get_text())
            if msg:
                err.set_text(msg)
                err.show()
                return
            self.state["last_run"] = e.get_text().strip()
            dlg.destroy()
            GLib.timeout_add(500, lambda: (self.collector.wake(), False)[1])
        d.connect("response", resp)
        d.show_all()

    # ── 설정(색) 따라가기 ──
    def _watch_settings(self):
        """설정 앱이 settings.json 을 바꿔치기(원자적 저장)하므로 폴더를 본다"""
        cfg_dir = os.path.expanduser("~/.config/sekai")
        self._cfg_src = 0
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
        self.queue_draw()
        return False

    # ── 키·닫기 ──
    def _on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            if self.search.get_sensitive():
                self.search.grab_focus()
            return True
        if ev.keyval == Gdk.KEY_F5:                 # 지금 새로 고침 (일시 중지 중에도)
            self.collector.wake()
            return True
        if ctrl and Gdk.KEY_1 <= ev.keyval <= Gdk.KEY_4:
            self.select_page(PAGE_IDS[ev.keyval - Gdk.KEY_1])
            return True
        # 목록에서 글자를 치면 검색 칸으로 (GTK 목록의 떠 있는 검색 창 대신)
        if not ctrl and self._current().searchable and isinstance(self.get_focus(), Gtk.TreeView) and \
                ev.string and ev.string.isprintable() and not ev.string.isspace():
            if self.search.handle_event(ev) == Gdk.EVENT_STOP:
                self.search.grab_focus_without_selecting()
                return True
        return False

    def _on_wstate(self, _w, ev):
        self.state["maximized"] = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def _on_close(self, *_):
        if not self.state.get("maximized"):
            w, h = self.get_size()
            self.state["size"] = [w, h]
        _save_state(self.state)
        if self.collector is not None:
            self.collector.stop()
            self.collector = None
        return False


def run_command(text):
    """새 작업 실행 — 프로그램이면 띄우고, 폴더·파일·주소면 기본 앱으로 연다. 문제가 있으면 알릴 글을 돌려준다."""
    text = (text or "").strip()
    if not text:
        return "무엇을 열지 입력하세요."
    path = os.path.expanduser(text)
    try:
        if "://" in text and " " not in text:
            Gio.AppInfo.launch_default_for_uri(text, None)
            return None
        if os.path.exists(path) and not (os.path.isfile(path) and os.access(path, os.X_OK)):
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
            return None
        _ok, argv = GLib.shell_parse_argv(text)
    except GLib.Error as e:
        return e.message
    argv[0] = os.path.expanduser(argv[0])
    if not shutil.which(argv[0]):
        return f"'{argv[0]}'을(를) 찾을 수 없습니다. 이름을 올바르게 입력했는지 확인하세요."
    try:
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
        launcher.set_cwd(os.path.expanduser("~"))
        proc = launcher.spawnv(argv)
        proc.wait_async(None, None, None)          # 끝나면 거둔다 (좀비가 남지 않게)
    except GLib.Error as e:
        return e.message
    return None


def _page_arg(args):
    """--page=<아이디> 또는 아이디만 (sekai-taskmgr performance)"""
    for a in args:
        if a.startswith("--page="):
            a = a.split("=", 1)[1]
        if a in PAGE_IDS:
            return a
    return None


class TaskManagerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_command_line(self, cl):
        start = _page_arg(cl.get_arguments()[1:])
        if self.win is not None:                  # 이미 떠 있다 — 앞으로 (Ctrl+Shift+Esc 를 또 눌러도 하나만)
            if start:
                self.win.select_page(start)
            self.win.present()
            return 0
        theme.apply_gtk_settings(Gtk.Settings.get_default(), appearance()["mode"])
        self.win = win = TaskManagerWindow(start)
        self.add_window(win)
        win.show_all()

        # 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 종료한다 (설정 앱과 같은 방법)
        shot = os.environ.get("SEKAI_SHOT")
        if shot:
            def grab():
                gw = win.get_window()
                if gw is not None:
                    pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
                    if pb:
                        pb.savev(shot, "png", [], [])
                        print("shot:", shot, gw.get_width(), "x", gw.get_height())
                win._on_close()
                self.quit()
                return False
            GLib.timeout_add(int(os.environ.get("SEKAI_SHOT_DELAY", "2500")), grab)
        return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-taskmgr [--page=<아이디>]")
        print("  페이지:", ", ".join(PAGE_IDS))
        return 0
    return TaskManagerApp().run([sys.argv[0]] + list(argv))
