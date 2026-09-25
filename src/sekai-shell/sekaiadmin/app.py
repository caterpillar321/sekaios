"""SekaiOS 컴퓨터 관리 — 메인 창 (윈도우의 "컴퓨터 관리"처럼).

왼쪽 메뉴: 묶음 머리(시스템 도구 · 저장소 · 서비스 및 응용 프로그램) 아래 페이지들. 아직 없는 페이지는 보이지 않는다.
오른쪽: 위쪽 검색 칸(페이지가 원할 때만) · 제목줄(제목과 페이지의 단추들) · 페이지 · 아래쪽 짧은 알림.
색은 작업 관리자와 같다 — settings.css 앞에 강조색·모드를 붙이고 이 창의 모양(ADMIN_CSS)을 더한다.
설정 앱에서 모드·강조색을 바꾸면 곧바로 따라간다. 창 상태(크기·마지막 페이지·페이지마다 기억할 것)는
~/.local/state/sekai/admin.json.

실행: sekai-admin [--page=<id>] [--<이름>=<값> …]
    --page 말고 붙인 것은 그 페이지의 on_show(**kw) 로 간다 (sekai-admin --page=events --unit=ssh.service).
    이미 떠 있으면 새 창을 만들지 않고 그 창을 앞으로 가져와 페이지만 바꾼다.

── 페이지 규칙 ───────────────────────────────────────────────
pages/<모듈>.py 가 PAGE 를 가진다 (모듈 목록·순서는 pages/__init__.py 의 MODULES):
    PAGE = {
        "id": "events",                 # --page=<id> · win.show_page(<id>)
        "title": "이벤트 뷰어",           # 왼쪽 메뉴와 제목줄
        "group": "system",              # "system"(시스템 도구) | "storage"(저장소) | "services"(서비스 및 응용 프로그램)
        "order": 10,                    # 묶음 안의 순서 — 작을수록 위
        "icon": ["아이콘 후보", …],       # 테마에 있는 첫 것 (18px — 기호(-symbolic) 아이콘이 어울린다)
        "build": f(win) -> 페이지 객체,   # 그 페이지를 처음 열 때 한 번 (지연 생성)
        "css": "…",                     # (없어도 됨) 이 페이지만의 모양 — @accent·@fg·@card·@line 같은 색을 쓸 수 있다
    }
페이지 객체:
    .widget          Gtk.Widget — 오른쪽 내용 (필수)
    .actions         Gtk.Widget — 제목줄 오른쪽 단추 상자 (없어도 됨)
    .searchable      True 면 위쪽 검색 칸이 보이고, 칠 때마다 .set_query(text) (검색어는 페이지마다 따로 기억)
    .search_hint     검색 칸의 안내 글 (없어도 됨)
    .on_show(**kw)   보이게 될 때 — 여기서 새로 고침·감시를 시작한다. 이미 보이는 중에도 kw 와 함께 다시 불릴 수 있다
                     (win.show_page("events", unit=…)). kw 를 받지 않는 페이지는 on_show(self) 로 두어도 된다
    .on_hide()       가려질 때(다른 페이지로 · 창을 닫을 때) — 타이머·감시·자식 프로세스를 멈춘다
    .refresh()       F5 (없어도 됨)
    .busy            오래 걸리는 일이 도는 중 — 바꾼 뒤 win.busy_changed() 를 부르면 제목줄에 도는 표시가 뜬다
win 이 주는 것 (모두 메인 스레드에서 부른다 — 메인 스레드에서 오래 걸리는 명령·D-Bus 동기 호출은 하지 않는다):
    win.run_async(argv, done, stdin=None) -> Gio.Cancellable
                     명령을 돌리고 끝나면 메인 스레드에서 done(ok, out, err). 취소하면 done 은 불리지 않는다.
                     pkexec 를 앞에 붙이면 관리자 인증 창이 뜨는 동안에도 화면이 멈추지 않는다
    win.run_thread(work, done)   work() 를 작업 스레드에서, 끝나면 메인 스레드에서 done(결과, 예외 또는 None)
    win.toast(text, secs=5)      아래쪽 짧은 알림
    win.set_search(text)         위쪽 검색 칸의 글자를 바꾼다 (지금 페이지 — .set_query 도 불린다)
    win.show_page(id, **kw)      다른 페이지로 (예: 서비스 → win.show_page("events", unit="ssh.service"))
                                 명령줄(--이름=값)로 온 kw 는 모두 글자다
    win.specs                    {id: PAGE} — 지금 있는 페이지 ("로그 보기" 같은 단추를 보일지 정할 때)
    win.confirm(title, text, ok_label, on_ok) / win.notice(title, text)   묻기(기본 단추는 취소) / 알리기
    win.page_state(id) -> dict   페이지가 기억할 것 (창을 닫을 때 JSON 으로 저장 — 문자열·숫자·목록만)
    win.saved_sort(id, sid, order) / win.sort_saver(id)   목록 정렬 기억 (taskmgr_common.SortHeaders 와 함께)
    win.busy_changed()           .busy 를 바꾼 뒤
공용 도움(목록 열·정렬·세부 칸·안내 막대·시각 표기·권한·D-Bus 오류 글)은 sekaiadmin/common.py.
"""
import json
import os
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import dbg, theme  # noqa: E402

from .common import appearance, confirm, icon_image, notice  # noqa: E402
from .pages import GROUPS, all_pages  # noqa: E402

APP_ID = "org.sekaios.Admin"
TITLE = "컴퓨터 관리"
APP_ICONS = ["preferences-system-services", "computer", "preferences-system"]
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
STATE = os.path.expanduser("~/.local/state/sekai/admin.json")

# settings.css 위에 얹는 이 창만의 모양 (작업 관리자와 같은 틀)
ADMIN_CSS = """
@define-color adm_err mix(#e0453a, @fg, 0.25);
@define-color adm_warn mix(#d69a00, @fg, 0.20);
.adm-window { background: @winbg; color: @fg; }
.adm-top { padding: 12px 24px 4px 24px; }
.adm-search {
    min-width: 380px;
    background: @card;
    border: 1px solid @line;
    border-bottom: 2px solid @line;
    border-radius: 8px;
    padding: 5px 10px;
}
.adm-search:focus { border-bottom-color: @accent; }
.adm-bar { padding: 10px 24px 10px 24px; }
.adm-title { font-size: 20px; font-weight: 700; color: @fg; }
.adm-body { padding: 0 16px 16px 16px; }
label.adm-group { padding: 14px 22px 4px 22px; font-size: 12px; font-weight: 600; color: @text3; }
/* 메뉴의 기호 아이콘 — 고른 줄에서 GTK 테마의 선택 글자색(흰색)을 따라가 라이트 모드에서 사라지지 않게 */
.side-row image { color: @text2; }
.side-row:hover image, .side-row:selected image { color: @fg; }
.adm-listbox { border: 1px solid @line; border-radius: 8px; background: @winbg; }
treeview.adm-list { background-color: @winbg; color: @fg; font-size: 13px; }
treeview.adm-list:selected { background-color: alpha(@accent, 0.28); color: @fg; }
treeview.adm-list header button {
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
treeview.adm-list header button:hover { background: @hover; }
treeview.adm-list header button label { color: @text2; }
.adm-empty { padding: 16px 4px; color: @text2; }
.adm-toast {
    background: @card;
    border: 1px solid alpha(@accent, 0.45);
    border-radius: 8px;
    padding: 8px 14px;
    margin: 0 24px 12px 24px;
}
/* 목록 위 안내 막대 (권한이 모자랄 때 등) */
.adm-notice {
    background: alpha(@accent, 0.10);
    border: 1px solid alpha(@accent, 0.30);
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 8px;
}
.adm-notice.warn { background: alpha(@adm_warn, 0.12); border-color: alpha(@adm_warn, 0.40); }
.adm-notice.error { background: alpha(@adm_err, 0.12); border-color: alpha(@adm_err, 0.40); }
.adm-notice button { padding: 4px 12px; }
/* 거르기 줄 */
.adm-filters { margin-bottom: 8px; }
.adm-filters combobox button, .adm-filters button { padding: 4px 10px; font-size: 13px; }
label.adm-cap { color: @text2; font-size: 12px; }
button.adm-chip { padding: 2px 10px; border-radius: 999px; font-size: 12px; background: @card; }
button.adm-chip:hover { background: @hover; }
button.adm-chip.err label { color: @adm_err; }
button.adm-chip.warn label { color: @adm_warn; }
label.adm-sum { color: @text2; font-size: 12px; }
/* 두 칸짜리 전환 (시스템 서비스 | 사용자 서비스) */
.adm-seg button { border-radius: 0; padding: 4px 14px; }
.adm-seg button:first-child { border-radius: 6px 0 0 6px; }
.adm-seg button:last-child { border-radius: 0 6px 6px 0; }
.adm-seg button:checked { background: alpha(@accent, 0.25); border-color: alpha(@accent, 0.6); }
/* 세부 칸 */
.adm-details {
    background: @card;
    border: 1px solid @line;
    border-radius: 8px;
    padding: 12px 16px;
}
label.adm-dtitle { font-size: 14px; font-weight: 600; color: @fg; }
label.adm-key { font-size: 12px; color: @text2; }
label.adm-val { font-size: 12px; color: @fg; }
textview.adm-msg, textview.adm-msg text { background: @card; color: @fg; font-size: 13px; }
label.adm-status { font-size: 12px; color: @text2; padding: 6px 2px 0 2px; }
button.adm-link { padding: 2px 8px; font-size: 12px; background: transparent; border-color: transparent; }
button.adm-link label { color: mix(@accent, @fg, 0.25); }
button.adm-link:hover { background: @hover; }
paned > separator { background: transparent; min-width: 6px; min-height: 6px; }
/* 페이지 안의 왼쪽 보기 목록 (이벤트 뷰어의 시스템·응용 프로그램…) */
treeview.adm-side { background-color: @winbg; color: @fg; font-size: 13px; }
treeview.adm-side:selected { background-color: alpha(@accent, 0.22); color: @fg; }
"""


def _load_css(a, extra=""):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + ADMIN_CSS + extra).encode())
    except GLib.Error as e:
        print("[sekai-admin] CSS 오류:", e.message, file=sys.stderr, flush=True)
        if not extra:
            return None
        return _load_css(a)                       # 페이지가 더한 모양이 틀렸다 — 그것만 빼고
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
        dbg("컴퓨터 관리 상태 저장 실패", e)


class _ErrorPage:
    """페이지를 만들다 오류가 났을 때 — 창은 살려 두고 이유를 보여 준다"""
    searchable = False
    busy = False

    def __init__(self, title, err):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Label(label=f"'{title}' 페이지를 여는 중 오류가 났습니다.", xalign=0)
        head.get_style_context().add_class("adm-empty")
        box.pack_start(head, False, False, 0)
        lbl = Gtk.Label(label=str(err), xalign=0)
        lbl.set_selectable(True)
        lbl.set_line_wrap(True)
        lbl.get_style_context().add_class("adm-notice")
        box.pack_start(lbl, False, False, 0)
        self.widget = box


class AdminWindow(Gtk.Window):
    def __init__(self, start=None, start_kw=None):
        super().__init__(title=TITLE)
        self.set_icon_name(APP_ICONS[0])
        self.state = _load_state()
        w, h = self.state.get("size") or (1120, 740)
        self.set_default_size(max(760, int(w)), max(500, int(h)))
        if self.state.get("maximized"):
            self.maximize()
        self.set_size_request(760, 480)
        for c in ("settings-window", "adm-window"):
            self.get_style_context().add_class(c)

        self.specs = {p["id"]: p for p in all_pages()}
        self._extra_css = "".join(p.get("css") or "" for p in self.specs.values())
        self._css = _load_css(appearance(), self._extra_css)
        self.pages = {}                             # 만든 페이지 객체 (처음 열 때)
        self.current = None
        self._queries = {}                          # 페이지마다 검색어
        self._toast_src = 0

        root = Gtk.Box(spacing=0)
        self.add(root)
        root.pack_start(self._build_sidebar(), False, False, 0)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("content")
        root.pack_start(main, True, True, 0)

        self.top = Gtk.Box()
        self.top.get_style_context().add_class("adm-top")
        self.search = Gtk.SearchEntry()
        self.search.get_style_context().add_class("adm-search")
        self._search_sig = self.search.connect("search-changed", self._on_search)
        self.search.connect("stop-search", lambda *_: self.search.set_text(""))
        self.top.set_center_widget(self.search)
        self.top.set_no_show_all(True)
        self.search.show()
        main.pack_start(self.top, False, False, 0)

        bar = Gtk.Box(spacing=10)
        bar.get_style_context().add_class("adm-bar")
        self.page_title = Gtk.Label(xalign=0)
        self.page_title.get_style_context().add_class("adm-title")
        bar.pack_start(self.page_title, False, False, 0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        bar.pack_start(self.spinner, False, False, 0)
        self.action_stack = Gtk.Stack()
        self.action_stack.set_homogeneous(False)
        bar.pack_end(self.action_stack, False, False, 0)
        main.pack_start(bar, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(100)
        self.stack.get_style_context().add_class("adm-body")
        main.pack_start(self.stack, True, True, 0)

        self.toast_label = Gtk.Label(xalign=0)
        self.toast_label.set_line_wrap(True)
        self.toast_label.get_style_context().add_class("adm-toast")
        self.toast_label.set_no_show_all(True)
        main.pack_start(self.toast_label, False, False, 0)

        if not self.specs:
            empty = Gtk.Label(label="보여 줄 페이지가 없습니다.")
            empty.get_style_context().add_class("adm-empty")
            self.stack.add_named(empty, "-")

        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_close)
        self.connect("window-state-event", self._on_wstate)
        self._watch_settings()

        first = start if start in self.specs else self.state.get("page")
        if first not in self.specs:
            first = next(iter(self.specs), None)
        if first:
            self.show_page(first, **((start_kw or {}) if first == start else {}))

    # ── 왼쪽 메뉴 ──
    def _build_sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.get_style_context().add_class("sidebar")
        side.set_size_request(240, -1)
        head = Gtk.Box(spacing=10)
        head.get_style_context().add_class("side-head")
        head.pack_start(icon_image(APP_ICONS, 22), False, False, 0)
        t = Gtk.Label(label=TITLE, xalign=0)
        t.get_style_context().add_class("side-title")
        head.pack_start(t, True, True, 0)
        side.pack_start(head, False, False, 0)

        self.rail = Gtk.ListBox()
        self.rail.get_style_context().add_class("side-list")
        names = dict(GROUPS)

        def header(row, before):
            if before is not None and before.group == row.group:
                row.set_header(None)
                return
            if row.get_header() is None:
                lbl = Gtk.Label(label=names.get(row.group, ""), xalign=0)
                lbl.get_style_context().add_class("adm-group")
                lbl.show()
                row.set_header(lbl)
        self.rail.set_header_func(header)
        # show_page 가 스스로 행을 고를 때 한 번 더 불리지 않게 (지금 페이지면 건너뛴다)
        self.rail.connect("row-selected", lambda _l, r: r is not None and r.page_id != self.current and
                          self.show_page(r.page_id))
        self._rows = {}
        for pid, spec in self.specs.items():
            r = Gtk.ListBoxRow()
            r.page_id = pid
            r.group = spec["group"]
            r.get_style_context().add_class("side-row")
            h = Gtk.Box(spacing=12)
            h.pack_start(icon_image(spec.get("icon") or ["application-x-executable"], 18), False, False, 0)
            h.pack_start(Gtk.Label(label=spec["title"], xalign=0), True, True, 0)
            r.add(h)
            self.rail.add(r)
            self._rows[pid] = r
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.rail)
        side.pack_start(sc, True, True, 0)
        return side

    # ── 페이지 ──
    def _page(self, pid):
        pg = self.pages.get(pid)
        if pg is not None:
            return pg
        spec = self.specs[pid]
        try:
            pg = spec["build"](self)
        except Exception as e:
            import traceback
            traceback.print_exc()
            pg = _ErrorPage(spec["title"], e)
        self.pages[pid] = pg
        pg.widget.show_all()
        self.stack.add_named(pg.widget, pid)
        acts = getattr(pg, "actions", None) or Gtk.Box()
        acts.show_all()
        self.action_stack.add_named(acts, pid)
        return pg

    def show_page(self, page_id, **kw):
        """page_id 페이지로. kw 는 그 페이지의 on_show(**kw) 로 (단위 거르기 따위)"""
        if page_id not in self.specs:
            self.toast("그 페이지는 아직 없습니다")
            return False
        pg = self._page(page_id)
        if self.current != page_id:
            old = self.pages.get(self.current) if self.current else None
            if old is not None:
                self._call(old, "on_hide")
            self.current = page_id
            self.stack.set_visible_child_name(page_id)
            self.action_stack.set_visible_child_name(page_id)
            self.page_title.set_text(self.specs[page_id]["title"])
            self.state["page"] = page_id
            r = self._rows[page_id]
            if self.rail.get_selected_row() is not r:
                self.rail.select_row(r)
            self._sync_search(pg)
        self._call(pg, "on_show", **kw)
        self.busy_changed()
        return False

    def _call(self, pg, name, **kw):
        fn = getattr(pg, name, None)
        if fn is None:
            return
        try:
            if kw:
                try:
                    fn(**kw)
                except TypeError:              # kw 를 받지 않는 페이지 — 그냥 보인다
                    dbg("페이지가 인자를 받지 않습니다", name, kw)
                    fn()
            else:
                fn()
        except Exception:
            import traceback
            traceback.print_exc()

    def _sync_search(self, pg):
        on = bool(getattr(pg, "searchable", False))
        self.top.set_visible(on)
        if not on:
            return
        self.search.set_placeholder_text(getattr(pg, "search_hint", None) or "검색")
        q = self._queries.get(self.current, "")
        if self.search.get_text() != q:
            self.search.handler_block(self._search_sig)
            self.search.set_text(q)
            self.search.handler_unblock(self._search_sig)

    def _on_search(self, e):
        pg = self.pages.get(self.current)
        if pg is None or not getattr(pg, "searchable", False):
            return
        self._queries[self.current] = e.get_text()
        pg.set_query(e.get_text())

    def set_search(self, text):
        self.search.set_text(text or "")          # → search-changed → 지금 페이지의 set_query

    def busy_changed(self):
        pg = self.pages.get(self.current)
        on = bool(pg is not None and getattr(pg, "busy", False))
        self.spinner.set_visible(on)
        if on:
            self.spinner.start()
        else:
            self.spinner.stop()

    # ── 페이지들이 부르는 것 ──
    def run_async(self, argv, done, stdin=None):
        """명령을 돌리고 끝나면 메인 스레드에서 done(ok, out, err). 돌려주는 Cancellable 로 취소 (그러면 done 없음)"""
        cancel = Gio.Cancellable()
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        if stdin is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new([str(a) for a in argv], flags)
        except GLib.Error as e:
            msg = e.message                       # e 는 except 를 벗어나면 사라진다
            GLib.idle_add(lambda: (cancel.is_cancelled() or done(False, "", msg), False)[1])
            return cancel
        cancel.connect(lambda _c: proc.force_exit())

        def finished(p, res):
            try:
                _ok, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                if cancel.is_cancelled():
                    return
                out, err = "", e.message
            if cancel.is_cancelled():
                return
            try:
                done(p.get_successful(), out or "", err or "")
            except Exception:
                import traceback
                traceback.print_exc()
        proc.communicate_utf8_async(stdin, None, finished)
        return cancel

    def run_thread(self, work, done):
        """work() 를 작업 스레드에서 — 끝나면 메인 스레드에서 done(결과, 예외 또는 None)"""
        def go():
            try:
                res, exc = work(), None
            except Exception as e:                  # 부르는 쪽이 이유를 보여 준다
                res, exc = None, e
            GLib.idle_add(lambda: (done(res, exc), False)[1])
        threading.Thread(target=go, daemon=True, name="sekai-admin-work").start()

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

    def confirm(self, title, text, ok_label, on_ok):
        confirm(self, title, text, ok_label, on_ok)

    def notice(self, title, text):
        notice(self, title, text)

    def page_state(self, page_id):
        pages = self.state.get("pages")
        if not isinstance(pages, dict):
            pages = self.state["pages"] = {}
        d = pages.get(page_id)
        if not isinstance(d, dict):
            d = pages[page_id] = {}
        return d

    def saved_sort(self, page_id, sid, order):
        s = self.page_state(page_id).get("sort")
        if isinstance(s, list) and len(s) == 2:
            try:
                return int(s[0]), Gtk.SortType(int(s[1]))
            except (TypeError, ValueError):
                pass
        return sid, order

    def sort_saver(self, page_id):
        def save(sid, order):
            self.page_state(page_id)["sort"] = [int(sid), int(order)]
        return save

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
        self._css = _load_css(a, self._extra_css)
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self.queue_draw()
        return False

    # ── 키·닫기 ──
    def _on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        pg = self.pages.get(self.current)
        searchable = pg is not None and getattr(pg, "searchable", False)
        if ctrl and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            if searchable:
                self.search.grab_focus()
            return True
        if ev.keyval == Gdk.KEY_F5:
            if pg is not None and hasattr(pg, "refresh"):
                self._call(pg, "refresh")
            return True
        # 목록에서 글자를 치면 검색 칸으로 (GTK 목록의 떠 있는 검색 창 대신 — 작업 관리자와 같게)
        if not ctrl and searchable and isinstance(self.get_focus(), Gtk.TreeView) and \
                ev.string and ev.string.isprintable() and not ev.string.isspace():
            if self.search.handle_event(ev) == Gdk.EVENT_STOP:
                self.search.grab_focus_without_selecting()
                return True
        return False

    def _on_wstate(self, _w, ev):
        self.state["maximized"] = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def _on_close(self, *_):
        pg = self.pages.get(self.current)
        if pg is not None:
            self._call(pg, "on_hide")
        if not self.state.get("maximized"):
            w, h = self.get_size()
            self.state["size"] = [w, h]
        _save_state(self.state)
        return False


def _parse_args(args):
    """--page=<id> 또는 id 만 · 나머지 --이름=값 은 그 페이지로"""
    page, kw = None, {}
    for a in args:
        if a.startswith("--page="):
            page = a.split("=", 1)[1]
        elif a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            if k.replace("-", "_").isidentifier():
                kw[k.replace("-", "_")] = v
        elif not a.startswith("-") and page is None:
            page = a
    return page, kw


class AdminApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_command_line(self, cl):
        page, kw = _parse_args(cl.get_arguments()[1:])
        if self.win is not None:                  # 이미 떠 있다 — 앞으로 (페이지를 말했으면 그리로)
            if page:
                self.win.show_page(page, **kw)
            self.win.present()
            return 0
        theme.apply_gtk_settings(Gtk.Settings.get_default(), appearance()["mode"])
        self.win = win = AdminWindow(page, kw)
        self.add_window(win)
        win.show_all()

        # 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 종료한다 (설정 앱·작업 관리자와 같은 방법)
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
        print("사용법: sekai-admin [--page=<아이디>] [--<이름>=<값> …]")
        print("  페이지:", ", ".join(p["id"] for p in all_pages()))
        print("  예: sekai-admin --page=events --unit=ssh.service")
        return 0
    return AdminApp().run([sys.argv[0]] + list(argv))
