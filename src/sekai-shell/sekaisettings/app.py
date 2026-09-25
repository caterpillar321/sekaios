"""SekaiOS 설정 — 메인 창.

왼쪽에 카테고리, 오른쪽에 페이지. 페이지는 처음 열 때 만든다 (지연 생성).
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib  # noqa: E402

from .store import Store
from .util import dbg
from .widgets import icon_image
from .pages import all_pages

HERE = os.path.dirname(os.path.abspath(__file__))
# 설정 창은 하나만 — 두 창이 각자 메모리의 설정으로 저장하면 서로의 변경을 덮어쓴다.
#   두 번째로 실행하면 이미 떠 있는 창을 앞으로 (--page 가 있으면 그 페이지로)
APP_ID = "org.sekaios.Settings"
CSS_PATHS = [
    os.path.join(HERE, "..", "settings.css"),
    "/usr/share/sekai-shell/settings.css",
]


def _load_css(store):
    a = store.get("appearance")
    prelude = (
        f"@define-color accent {a['accent']};\n"
        f"@define-color bg {a['bg']};\n"
        f"@define-color surface {a['surface']};\n"
        f"@define-color fg {a['fg']};\n"
    )
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            dbg("CSS:", p)
            break
    if not body:
        dbg("CSS 를 찾지 못했습니다 — 기본 모양으로 뜹니다")
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body).encode())
    except Exception as e:
        dbg("CSS 오류:", e)
        return None
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


class SettingsWindow(Gtk.Window):
    def __init__(self, store, start_page=None):
        super().__init__(title="설정")
        self.store = store
        self.set_default_size(1100, 760)
        self.set_size_request(760, 480)
        self.get_style_context().add_class("settings-window")

        self._css = _load_css(store)
        store.connect(self._on_change)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(root)

        root.pack_start(self._build_sidebar(), False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.get_style_context().add_class("content")
        root.pack_start(self.stack, True, True, 0)

        self._built = set()
        self._select(start_page or self.pages[0]["id"])

    # ── 사이드바 ────────────────────────────────────────
    def _build_sidebar(self):
        self.pages = all_pages()

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.get_style_context().add_class("sidebar")
        side.set_size_request(260, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.get_style_context().add_class("side-head")
        head.pack_start(icon_image(["preferences-system", "emblem-system"], 22),
                        False, False, 0)
        t = Gtk.Label(label="설정", xalign=0)
        t.get_style_context().add_class("side-title")
        head.pack_start(t, True, True, 0)
        side.pack_start(head, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("설정 찾기")
        self.search.get_style_context().add_class("side-search")
        self.search.connect("search-changed", self._on_search)
        self.search.connect("stop-search", lambda *_: self.search.set_text(""))
        side.pack_start(self.search, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list = Gtk.ListBox()
        self.list.get_style_context().add_class("side-list")
        self.list.connect("row-selected", self._on_row)
        sc.add(self.list)
        side.pack_start(sc, True, True, 0)

        self._rows = {}
        for pg in self.pages:
            r = Gtk.ListBoxRow()
            r.page_id = pg["id"]
            r.search_key = pg["title"].lower()
            r.get_style_context().add_class("side-row")
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            h.pack_start(icon_image(pg["icon"], 20), False, False, 0)
            h.pack_start(Gtk.Label(label=pg["title"], xalign=0), True, True, 0)
            r.add(h)
            self.list.add(r)
            self._rows[pg["id"]] = r

        ver = Gtk.Label(label="SekaiOS 1.0 (Hatsune)", xalign=0)
        ver.get_style_context().add_class("side-foot")
        side.pack_start(ver, False, False, 0)
        return side

    def _on_search(self, w):
        q = w.get_text().strip().lower()
        for r in self.list.get_children():
            r.set_visible(not q or q in r.search_key)

    def _on_row(self, _lb, r):
        if r is not None:
            self._select(r.page_id)

    def _select(self, page_id):
        if page_id not in self._built:
            pg = next((p for p in self.pages if p["id"] == page_id), None)
            if pg is None:
                return
            try:
                w = pg["build"](self.store)
            except Exception as e:
                import traceback
                traceback.print_exc()
                w = _error_page(pg["title"], e)
            w.show_all()
            self.stack.add_named(w, page_id)
            self._built.add(page_id)
        self.stack.set_visible_child_name(page_id)
        r = self._rows.get(page_id)
        if r and self.list.get_selected_row() is not r:
            self.list.select_row(r)

    # ── 설정이 바뀌면 ───────────────────────────────────
    def _on_change(self, section, key, value):
        if section == "appearance" and key in ("accent", "bg", "surface", "fg", "mode"):
            # 창 자신의 색도 즉시 따라가게 CSS 를 다시 읽는다
            GLib.idle_add(self._reload_css)
        if section == "appearance" and key == "mode":
            from sekaishell import theme
            theme.apply_gtk_settings(Gtk.Settings.get_default(), theme.mode_of({"mode": value}))

    def _reload_css(self):
        if self._css is not None:
            Gtk.StyleContext.remove_provider_for_screen(
                Gdk.Screen.get_default(), self._css)
        self._css = _load_css(self.store)
        return False


def _error_page(title, err):
    from .widgets import Page
    p = Page(title, "이 페이지를 만드는 중 오류가 났습니다.")
    l = Gtk.Label(label=str(err), xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    l.set_selectable(True)
    p.add_widget(l)
    return p


def _start_page(args):
    """--page=<아이디> 또는 아이디만 (sekai-settings display)"""
    ids = {p["id"] for p in all_pages()}
    for a in args:
        if a.startswith("--page="):
            return a.split("=", 1)[1]
        if a in ids:
            return a
    return None


class SettingsApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_command_line(self, cl):
        start = _start_page(cl.get_arguments()[1:])
        if self.win is not None:                 # 이미 떠 있다 — 앞으로
            if start:
                self.win._select(start)
            self.win.present()
            return 0

        store = Store()
        # 처음 실행이면 조각 파일을 만들어 둔다
        store.write_hypr_fragment()

        from sekaishell import theme
        theme.apply_gtk_settings(Gtk.Settings.get_default(), theme.mode_of(store.get("appearance")))

        self.win = win = SettingsWindow(store, start)
        self.add_window(win)                      # 창을 닫으면 프로그램도 끝난다
        win.show_all()

        # 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 종료한다.
        #   (원격에서 화면을 직접 볼 수 없을 때 쓰려고 넣어 둔 것)
        shot = os.environ.get("SEKAI_SHOT")
        if shot:
            delay = int(os.environ.get("SEKAI_SHOT_DELAY", "1500"))

            def grab():
                gw = win.get_window()
                if gw is not None:
                    pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
                    if pb:
                        pb.savev(shot, "png", [], [])
                        print("shot:", shot, gw.get_width(), "x", gw.get_height())
                self.quit()
                return False
            GLib.timeout_add(delay, grab)
        return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-settings [--page=<아이디>]")
        print("  페이지:", ", ".join(p["id"] for p in all_pages()))
        return 0
    return SettingsApp().run([sys.argv[0]] + list(argv))
