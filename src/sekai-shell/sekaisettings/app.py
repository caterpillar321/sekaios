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
        store.on_rebuild = self._request_rebuild
        self._rebuild_src = {}              # 페이지 아이디 → 예약된 다시 그리기
        self._stale = set()                 # 작업 중이라 다시 그리기를 미뤄 둔 페이지 (다음에 열 때)

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
        self.search.connect("activate", self._on_search_enter)
        side.pack_start(self.search, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        both = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.list = Gtk.ListBox()
        self.list.get_style_context().add_class("side-list")
        self.list.connect("row-selected", self._on_row)
        both.pack_start(self.list, False, False, 0)
        # 찾기 결과 — 검색어가 있는 동안 페이지 목록 대신 (윈도우 설정처럼 "항목 · 페이지")
        self.results = Gtk.ListBox()
        self.results.get_style_context().add_class("side-list")
        self.results.set_activate_on_single_click(True)
        self.results.connect("row-activated", lambda _lb, r: self._open_hit(r))
        self.results.set_no_show_all(True)
        both.pack_start(self.results, False, False, 0)
        sc.add(both)
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
        """페이지 이름뿐 아니라 설정 항목(볼륨·고정 IP·자판 배열 …)까지 — 시작 메뉴와 같은 목록·같은 순위
        (sekaishell/search.py 의 SETTINGS_PAGES · SETTINGS_ITEMS). 한글 초성·영타도 된다"""
        q = w.get_text().strip()
        for r in self.results.get_children():
            r.destroy()
        if not q:
            self.results.hide()
            self.list.show()
            return
        from sekaishell import search as S
        hits = [e for _s, e in S.rank_settings(S.Query(q))][:40]
        for e in hits:
            r = Gtk.ListBoxRow()
            r.entry = e
            r.get_style_context().add_class("side-row")
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            h.pack_start(icon_image(e["icons"], 20), False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            v.set_valign(Gtk.Align.CENTER)
            t = Gtk.Label(label=e["title"], xalign=0)
            t.set_ellipsize(3)                    # Pango.EllipsizeMode.END
            v.pack_start(t, False, False, 0)
            if e["sub"]:
                s = Gtk.Label(label=e["sub"], xalign=0)
                s.get_style_context().add_class("side-hit-sub")
                v.pack_start(s, False, False, 0)
            h.pack_start(v, True, True, 0)
            r.add(h)
            r.show_all()                          # 목록이 no_show_all 이라 show_all 이 줄까지 내려가지 않는다
            self.results.add(r)
        if not hits:
            r = Gtk.ListBoxRow()
            r.set_activatable(False)
            r.set_selectable(False)
            r.entry = None
            l = Gtk.Label(label="결과가 없습니다", xalign=0)
            l.get_style_context().add_class("side-hit-sub")
            r.add(l)
            r.show_all()
            self.results.add(r)
        self.list.hide()
        self.results.show()

    def _on_search_enter(self, _w):
        first = self.results.get_row_at_index(0) if self.results.get_visible() else None
        if first is not None and getattr(first, "entry", None):
            self._open_hit(first)

    def _open_hit(self, r):
        e = getattr(r, "entry", None)
        if not e:
            return
        self.search.set_text("")                  # 페이지 목록으로 돌아가고 그 페이지가 골라진다
        self._select(e["page"])
        if e["sub"]:
            self.focus_item(e["page"], e["title"])

    # ── 항목 찾아 보여 주기 ──────────────────────────────
    def focus_item(self, page_id, title, tries=8):
        """그 페이지에서 제목이 맞는 줄(또는 섹션 제목)로 스크롤하고 잠깐 강조한다.
        페이지가 막 만들어져 크기가 아직 없으면 조금 뒤에 다시 (몇 번까지). 못 찾으면 페이지만 연다"""
        def go(left=tries):
            page = self.stack.get_child_by_name(page_id)
            if page is None or self.stack.get_visible_child() is not page:
                return False
            target = _find_titled(page, title)
            if target is None:
                return False
            box = page.get_child()
            box = box.get_child() if isinstance(box, Gtk.Viewport) else box
            pos = target.translate_coordinates(box, 0, 0) if box is not None else None
            if pos is None or target.get_allocated_height() <= 1:
                if left > 0:
                    GLib.timeout_add(80, go, left - 1)
                return False
            if isinstance(page, Gtk.ScrolledWindow):
                adj = page.get_vadjustment()
                adj.set_value(max(adj.get_lower(), min(pos[1] - 96, adj.get_upper() - adj.get_page_size())))
            ctx = target.get_style_context()
            ctx.add_class("item-found")
            GLib.timeout_add(1800, lambda: (ctx.remove_class("item-found"), False)[1])
            return False
        GLib.timeout_add(60, go)

    def _on_row(self, _lb, r):
        if r is not None:
            self._select(r.page_id)

    def _select(self, page_id):
        if page_id in self._stale and self.stack.get_visible_child_name() != page_id:
            self.rebuild_page(page_id)        # 미뤄 둔 다시 그리기 — 아직 작업 중이면 그대로 둔다
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
            old = self.stack.get_child_by_name(page_id)
            if old is not None:               # 다시 그리는 중 — 옛 것의 이름을 비워 준다 (rebuild_page 가 곧 없앤다)
                self.stack.child_set_property(old, "name", page_id + "~old")
            self.stack.add_named(w, page_id)
            self._built.add(page_id)
        self.stack.set_visible_child_name(page_id)
        r = self._rows.get(page_id)
        if r and self.list.get_selected_row() is not r:
            self.list.select_row(r)

    # ── 페이지 다시 그리기 ──────────────────────────────
    #   페이지는 처음 열 때 한 번 만들어진다. 되돌리기(reset_section)나 모니터를 켜고 끄는 것처럼
    #   화면의 구성·값이 통째로 바뀌면 새로 만들어야 옛 값·옛 구성이 남지 않는다.
    def _request_rebuild(self, page_id=None, delay_ms=0, section=None):
        """store.request_rebuild 가 부른다. 신호 처리 도중일 수 있으니 바로 없애지 않고 미룬다.
        page_id 가 없으면 그 섹션을 쓰는 페이지들만 (섹션도 없으면 만들어 둔 모든 페이지).
        같은 페이지에 대한 요청이 겹치면 나중 것만 남는다."""
        if page_id is not None:
            ids = [page_id]
        else:
            ids = [pg["id"] for pg in self.pages if pg["id"] in self._built and
                   (section is None or section in pg.get("sections", ()))]
        for pid in ids:
            old = self._rebuild_src.pop(pid, 0)
            if old:
                GLib.source_remove(old)

            def go(pid=pid):
                self._rebuild_src.pop(pid, None)
                self.rebuild_page(pid)
                return False
            self._rebuild_src[pid] = (GLib.timeout_add(delay_ms, go) if delay_ms > 0
                                      else GLib.idle_add(go))

    def rebuild_page(self, page_id):
        """그 페이지를 새로 만든다. 보고 있던 페이지면 스크롤 위치를 지켜 다시 보여 주고,
        안 보이는 페이지는 없애 두었다가 다음에 열 때 만든다."""
        old = self.stack.get_child_by_name(page_id)
        if old is None:
            self._built.discard(page_id)
            self._stale.discard(page_id)
            return
        if getattr(old, "busy", False):
            # 작업(관리자 권한 설치 등)이 도는 중 — 없애면 진행 상태·결과를 잃고 같은 작업을 또 띄울 수 있다.
            #   끝난 뒤 이 페이지를 다시 열 때 새로 만든다
            self._stale.add(page_id)
            return
        self._stale.discard(page_id)
        showing = self.stack.get_visible_child_name() == page_id
        scroll = None
        if showing and isinstance(old, Gtk.ScrolledWindow):
            scroll = old.get_vadjustment().get_value()
        self._built.discard(page_id)
        if showing:
            self._select(page_id)             # 새 페이지를 먼저 올린 뒤 옛 것을 없앤다 (빈 화면이 번쩍이지 않게)
            new = self.stack.get_child_by_name(page_id)
            if new is old:                    # (이름이 겹쳐 새것이 못 올라간 경우는 없지만 방어)
                return
        self.stack.remove(old)
        old.destroy()
        if showing and scroll is not None:
            new = self.stack.get_visible_child()
            if isinstance(new, Gtk.ScrolledWindow):
                GLib.idle_add(lambda: (new.get_vadjustment().set_value(scroll), False)[1])

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


def _find_titled(root, title):
    """root 아래에서 제목이 title 인 설정 줄(widgets.row — title_label)이나 섹션 제목을 찾는다.
    똑같은 것 → 한쪽이 다른 쪽을 품는 것 순. 없으면 None"""
    want = " ".join(title.split()).casefold()
    exact, part = None, None
    stack = [root]
    while stack:
        w = stack.pop()
        text = None
        tl = getattr(w, "title_label", None)
        if isinstance(tl, Gtk.Label):
            text = tl.get_text()
        elif isinstance(w, Gtk.Label) and w.get_style_context().has_class("section-title"):
            text = w.get_text()
        if text and w.get_mapped():
            t = " ".join(text.split()).casefold()
            if t == want:
                exact = exact or w
            elif part is None and t and (want in t or t in want):
                part = w
        if isinstance(w, Gtk.Container):
            stack.extend(reversed(w.get_children()))
    return exact or part


def _error_page(title, err):
    from .widgets import Page
    p = Page(title, "이 페이지를 만드는 중 오류가 났습니다.")
    l = Gtk.Label(label=str(err), xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    l.set_selectable(True)
    p.add_widget(l)
    return p


def _start_item(args):
    """--item=<항목 제목> — 그 페이지에서 찾아 보여 줄 줄 (시작 메뉴 검색에서 항목을 고르면)"""
    for a in args:
        if a.startswith("--item="):
            return a.split("=", 1)[1] or None
    return None


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
        args = cl.get_arguments()[1:]
        start, item = _start_page(args), _start_item(args)
        if self.win is not None:                 # 이미 떠 있다 — 앞으로
            if start:
                self.win._select(start)
                if item:
                    self.win.focus_item(start, item)
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
        if start and item:
            win.focus_item(start, item)

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
        print("사용법: sekai-settings [--page=<아이디> [--item=<항목 제목>]]")
        print("  페이지:", ", ".join(p["id"] for p in all_pages()))
        return 0
    return SettingsApp().run([sys.argv[0]] + list(argv))
