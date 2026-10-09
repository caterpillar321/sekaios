"""SekaiOS 스토어 — 윈도우의 Microsoft Store 처럼 앱을 찾아 설치한다.

실행: sekai-store [--app=<패키지>] [--search=<검색어>]
    이미 떠 있으면 그 창을 앞으로 (말한 앱·검색어로).

목록은 데비안 저장소의 AppStream 목록(sekaistore/catalog.py), 설치·제거는 sekai-apps-repo(pkexec)로 —
한 번에 하나씩 대기열(widgets.Jobs)로 돈다. 아무 .deb 설치는 앱 설치 관리자(sekai-appinstall)의 몫.
쪽: 홈(배너·추천 줄) · 앱 · 게임(분류 칩·격자) · 라이브러리(설치한 앱·목록 새로 고침) · 검색 · 앱 자세히.
"""
import os
import sys
import threading
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import appmgr, dbg  # noqa: E402
from sekaishell.appkit import AppTheme  # noqa: E402

from . import catalog as C  # noqa: E402
from .widgets import Jobs, app_icon, fill, grid, label, launch, picture  # noqa: E402

APP_ID = "org.sekaios.Store"
APP_NAME = "스토어"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
PAGE = 48                               # 격자에 한 번에 붙이는 타일 수 ("더 보기"로 더)

STORE_CSS = """
.store-window { background: @winbg; color: @fg; }
.store-nav { background: @sidebar; border-right: 1px solid @line; padding: 10px 6px; }
button.nav-btn {
    background: transparent; background-image: none; border: none; box-shadow: none;
    border-radius: 8px; padding: 8px 4px; min-width: 64px;
}
button.nav-btn:hover { background: @hover; }
button.nav-btn.on { background: @card; }
button.nav-btn.on image { color: @accent; }
button.nav-btn label { font-size: 8.25pt; color: @text2; }
button.nav-btn.on label { color: @fg; font-weight: 600; }
.store-top { padding: 10px 24px 6px 16px; }
entry.store-search { min-width: 440px; border-radius: 8px; }
button.flat-btn { background: transparent; background-image: none; border: none; box-shadow: none; border-radius: 6px; }
button.flat-btn:hover { background: @hover; }
.page-pad { padding: 10px 32px 32px 32px; }
label.page-h { font-size: 19.5pt; font-weight: 700; color: @fg; }
label.sec-h { font-size: 13.5pt; font-weight: 700; color: @fg; }
button.link-btn { background: transparent; background-image: none; border: none; box-shadow: none; padding: 2px 6px; }
button.link-btn label { color: @accent; }

button.store-tile {
    background: @card; background-image: none; border: 1px solid @line; border-radius: 10px;
    padding: 14px; box-shadow: none;
}
button.store-tile:hover { background: @hover; }
button.store-tile:active { background: @pressed; }
label.tile-name { font-weight: 600; color: @fg; }
label.tile-sub { color: @text2; font-size: 9.38pt; }
label.tile-badge { color: @text3; font-size: 9pt; }
label.tile-badge.on { color: @accent; font-weight: 600; }

.hero { border-radius: 14px; padding: 30px 34px; border: 1px solid @line; }
.hero-0 { background-image: linear-gradient(110deg, mix(@accent, @surface, 0.55), mix(@accent, @surface, 0.85)); }
.hero-1 { background-image: linear-gradient(110deg, mix(#c2185b, @surface, 0.55), mix(#c2185b, @surface, 0.85)); }
.hero-2 { background-image: linear-gradient(110deg, mix(#3949ab, @surface, 0.50), mix(#3949ab, @surface, 0.85)); }
.hero-3 { background-image: linear-gradient(110deg, mix(#ef6c00, @surface, 0.55), mix(#ef6c00, @surface, 0.85)); }
label.hero-name { font-size: 22.5pt; font-weight: 800; color: @fg; }
label.hero-tag { font-size: 12pt; font-weight: 600; color: @fg; }
label.hero-sub { color: @fg; }
button.dot { min-width: 8px; min-height: 8px; padding: 0; border-radius: 999px; border: none;
             background: alpha(@fg, 0.25); background-image: none; box-shadow: none; }
button.dot.on { background: @accent; min-width: 22px; }

button.chip { border-radius: 999px; padding: 4px 14px; background: @card; background-image: none;
              border: 1px solid @line; box-shadow: none; }
button.chip:hover { background: @hover; }
button.chip.on { background: @accent; border-color: @accent; }
button.chip.on label { color: @on_accent; font-weight: 600; }

.detail-head { background: @card; border: 1px solid @line; border-radius: 12px; padding: 24px 28px; }
label.detail-name { font-size: 19.5pt; font-weight: 700; color: @fg; }
label.detail-dev { color: @accent; }
label.detail-sum { color: @fg; }
label.detail-meta { color: @text2; font-size: 9.38pt; }
label.detail-err { color: #ff6b6b; }
label.info-k { color: @text2; }
label.info-v { color: @fg; }
.store-shot { background: @card; border-radius: 8px; }
.lib-row { background: @card; border: 1px solid @line; border-radius: 10px; padding: 10px 14px; }
.store-notice { background: alpha(@accent, 0.10); border: 1px solid alpha(@accent, 0.30); border-radius: 8px; padding: 10px 14px; }
"""


def _icon_btn(names, tip=None, cls="flat-btn", size=16):
    b = Gtk.Button()
    th = Gtk.IconTheme.get_default()
    n = next((x for x in names if th.has_icon(x)), names[-1])
    img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    b.add(img)
    b.get_style_context().add_class(cls)
    if tip:
        b.set_tooltip_text(tip)
    return b


def _scroller(child):
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.add(child)
    return sw


def _dpkg_installed_kb(pkg):
    """깔린 패키지의 Installed-Size (KiB) 또는 None"""
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Installed-Size}", pkg],
                           capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _human_bytes(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return appmgr.human_size(max(1, n // 1024)) if n else None


_THEME = AppTheme("sekai-store", STORE_CSS)


class StoreWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1180, 800)
        self.set_size_request(760, 520)
        self.set_icon_name("system-software-install")
        self.get_style_context().add_class("store-window")
        self.cat = C.Catalog()
        self.jobs = Jobs(self.cat)
        self.history = []               # 뒤로 가기 — [(쪽 이름, 인자)]
        self.cur = None
        self._search_src = 0
        self._banner_src = 0
        self._detail_watch = None
        self.pending = None             # 목록을 다 읽기 전에 받은 --app / --search

        root = Gtk.Box()
        self.add(root)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        nav.get_style_context().add_class("store-nav")
        root.pack_start(nav, False, False, 0)
        self.nav = {}
        for key, text, icons in (("home", "홈", ["go-home-symbolic", "go-home"]),
                                 ("apps", "앱", ["view-app-grid-symbolic", "view-grid-symbolic", "applications-all"]),
                                 ("games", "게임", ["applications-games-symbolic", "applications-games"])):
            nav.pack_start(self._nav_btn(key, text, icons), False, False, 0)
        nav.pack_end(self._nav_btn("library", "라이브러리",
                                   ["folder-download-symbolic", "emblem-downloads", "folder-download"]), False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(right, True, True, 0)
        top = Gtk.Box(spacing=8)
        top.get_style_context().add_class("store-top")
        self.back = _icon_btn(["go-previous-symbolic", "go-previous"], "뒤로 (Alt+←)")
        self.back.connect("clicked", lambda *_: self.go_back())
        self.back.set_sensitive(False)
        top.pack_start(self.back, False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("앱 · 게임 찾기")
        self.search.get_style_context().add_class("store-search")
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda *_: self._run_search())
        top.set_center_widget(self.search)
        self.spinner = Gtk.Spinner()
        top.pack_end(self.spinner, False, False, 0)
        right.pack_start(top, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        right.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._loading_page(), "loading")
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self._lib_src = 0
        self.jobs.watch(self._on_job)
        self.show_all()
        self._load()

    # ── 왼쪽 탐색 ──
    def _nav_btn(self, key, text, icons):
        b = Gtk.Button()
        b.get_style_context().add_class("nav-btn")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        th = Gtk.IconTheme.get_default()
        img = Gtk.Image.new_from_icon_name(next((x for x in icons if th.has_icon(x)), icons[-1]), Gtk.IconSize.BUTTON)
        img.set_pixel_size(20)
        v.pack_start(img, False, False, 0)
        v.pack_start(Gtk.Label(label=text), False, False, 0)
        b.add(v)
        b.connect("clicked", lambda *_: self.show(key, None))
        self.nav[key] = b
        return b

    def _mark_nav(self, key):
        for k, b in self.nav.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)("on")

    # ── 목록 읽기 ──
    def _loading_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.set_valign(Gtk.Align.CENTER)
        sp = Gtk.Spinner()
        sp.set_size_request(32, 32)
        sp.start()
        v.pack_start(sp, False, False, 0)
        self.loading_text = label("앱 목록을 읽는 중…", "detail-meta", xalign=0.5)
        v.pack_start(self.loading_text, False, False, 0)
        return v

    def _load(self, then=None):
        self.stack.set_visible_child_name("loading")
        self._mark_nav(None)

        def work():
            self.cat.load()
            GLib.idle_add(self._loaded, then)
        threading.Thread(target=work, daemon=True).start()

    FLATHUB_AS = "/var/lib/flatpak/appstream/flathub/x86_64/active/appstream.xml.gz"

    def _flathub_refresh(self):
        """Flathub 앱 목록이 없거나 하루가 지났으면 뒤에서 받는다 (flatpak 정책상 사용자도 암호 없이).
        없던 목록을 처음 받았으면 다시 읽고, 오래됐을 뿐이면 다음에 열 때 쓴다."""
        if not self.cat.flatpak or getattr(self, "_fh_started", False):
            return
        try:
            age = GLib.get_real_time() / 1e6 - os.path.getmtime(self.FLATHUB_AS)
            missing = False
        except OSError:
            age, missing = None, True
        if not missing and age < 20 * 3600:
            return
        self._fh_started = True

        def work():
            import subprocess
            rc = subprocess.run(["flatpak", "update", "--appstream", "--system", "--noninteractive"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            GLib.idle_add(done, rc)

        def done(rc):
            if rc == 0 and missing and os.path.exists(self.FLATHUB_AS) and not self.jobs.busy():
                where = self.cur[0] if self.cur and self.cur[0] in ("home", "apps", "games", "library") else None
                if self.cur is None or where:
                    self._load(then=where)      # Flathub 앱이 처음 생겼다 — 목록을 다시
            return False
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, then):
        for name in ("home", "apps", "games", "empty"):
            old = self.stack.get_child_by_name(name)
            if old is not None:
                old.destroy()
        self._flathub_refresh()
        if not self.cat.apps:
            self.stack.add_named(self._empty_page(), "empty")
            self.stack.set_visible_child_name("empty")
            self._wait_catalog()
            return False
        self.stack.add_named(self._home_page(), "home")
        self.apps_page = ListPage(self, "apps")
        self.games_page = ListPage(self, "games")
        self.stack.add_named(self.apps_page, "apps")
        self.stack.add_named(self.games_page, "games")
        self.history.clear()
        self.cur = None
        p = self.pending
        self.pending = None
        if p and p[0] == "app" and p[1] in self.cat.by_pkg:
            self.show("home", None, push=False)
            self.show_app(self.cat.by_pkg[p[1]])
        elif p and p[0] == "search":
            self.show("home", None, push=False)
            self.search.set_text(p[1])
            self._run_search()
        else:
            self.show(then or "home", None, push=False)
        return False

    def _empty_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.set_valign(Gtk.Align.CENTER)
        v.set_halign(Gtk.Align.CENTER)
        import subprocess
        busy = subprocess.run(["systemctl", "is-active", "--quiet", "sekai-store-catalog.service"]).returncode == 0
        if busy:
            # 업데이트 직후 — sekai-store-catalog 가 뒤에서 받는 중 (다 받으면 _wait_catalog 가 다시 읽는다)
            sp = Gtk.Spinner()
            sp.start()
            sp.set_size_request(28, 28)
            v.pack_start(sp, False, False, 0)
            v.pack_start(label("앱 목록을 받는 중입니다", "sec-h", xalign=0.5), False, False, 0)
            msg = "업데이트 뒤 처음 한 번 받습니다. 다 받으면 저절로 나타납니다."
        else:
            v.pack_start(label("앱 목록이 아직 없습니다", "sec-h", xalign=0.5), False, False, 0)
            msg = (self.cat.error or "") + ("\n" if self.cat.error else "") + \
                "인터넷에 연결된 상태에서 앱 목록을 받아 주세요."
        v.pack_start(label(msg, "detail-meta", wrap=True, xalign=0.5), False, False, 0)
        b = Gtk.Button(label="앱 목록 받기")
        b.get_style_context().add_class("accent-btn")
        b.set_halign(Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: self.refresh_catalog())
        v.pack_start(b, False, False, 0)
        self.empty_status = label("", "detail-meta", xalign=0.5)
        v.pack_start(self.empty_status, False, False, 0)
        v.show_all()
        return v

    def _wait_catalog(self):
        """빈 목록 — 뒤에서 받는 중일 수 있다 (sekai-store-catalog·Flathub 받기). 목록 파일이 생기면 다시 읽는다"""
        import glob

        def have():
            return bool(glob.glob("/var/lib/apt/lists/*_dep11_Components-*")) or os.path.exists(self.FLATHUB_AS)
        before = have()

        def tick():
            if self.cat.apps or self.stack.get_visible_child_name() != "empty":
                return False
            if have() and not before and not getattr(self, "_refreshing", False):
                self._load()
                return False
            return True
        if not before:
            GLib.timeout_add_seconds(5, tick)

    def refresh_catalog(self, status=None):
        """apt update (관리자) → 목록 다시 읽기"""
        if self.jobs.busy() or getattr(self, "_refreshing", False):
            if status is not None:
                status.set_text("설치가 끝난 뒤에 새로 고칠 수 있습니다")
            return
        self._refreshing = True
        self.spinner.start()
        target = status or getattr(self, "empty_status", None)
        if target is not None:
            target.set_text("관리자 인증을 기다리는 중…")
        err = {"e": None}

        def line(kind, rest):
            if kind == "PROGRESS" and target is not None:
                target.set_text(rest.partition(" ")[2])
            elif kind == "ERROR":
                err["e"] = rest
            return False

        def done(rc):
            self._refreshing = False
            self.spinner.stop()
            if rc in appmgr.CANCELLED and not err["e"]:
                if target is not None:
                    target.set_text("인증이 취소되었습니다")
                return False
            if rc != 0 or err["e"]:
                if target is not None:
                    target.set_text(err["e"] or f"목록을 받지 못했습니다 (코드 {rc})")
                return False
            self._load(then=self.cur[0] if self.cur and self.cur[0] in ("home", "apps", "games", "library")
                       else None)
            return False
        appmgr.run_helper(["refresh"], line, done, repo=True)

    # ── 쪽 바꾸기 ──
    def show(self, name, arg, push=True):
        if not self.cat.apps and name != "loading":
            return
        if push and self.cur is not None and self.cur != (name, arg):
            self.history.append(self.cur)
            del self.history[:-50]
        self.cur = (name, arg)
        self.back.set_sensitive(bool(self.history))
        if name == "detail":
            self._build_detail(arg)
        elif name == "library":
            self._build_library()
        elif name == "search":
            self._build_search(arg)
        elif name in ("apps", "games"):
            page = self.apps_page if name == "apps" else self.games_page
            page.set_category(arg)
        self.stack.set_visible_child_name(name)
        self._mark_nav(name if name in self.nav else None)
        if name != "search" and self.search.get_text() and push:
            self.search.handler_block_by_func(self._on_search_changed)
            self.search.set_text("")
            self.search.handler_unblock_by_func(self._on_search_changed)
        self._banner_run(name == "home")

    def show_app(self, app):
        # 양쪽에 있는 앱 — 이미 설치된 쪽이 있으면 그 쪽을 연다
        inst = self.cat.installed_variant(app)
        self.show("detail", inst or app)

    def go_back(self):
        if not self.history:
            return
        name, arg = self.history.pop()
        self.cur = None
        self.show(name, arg, push=False)

    # ── 검색 ──
    def _on_search_changed(self, *_):
        if self._search_src:
            GLib.source_remove(self._search_src)
        self._search_src = GLib.timeout_add(250, self._run_search)

    def _run_search(self):
        self._search_src = 0
        text = self.search.get_text().strip()
        if not self.cat.apps:
            if text:
                self.pending = ("search", text)
            return False
        if not text:
            if self.cur and self.cur[0] == "search":
                self.go_back()
            return False
        if self.cur and self.cur[0] == "search":
            self.cur = ("search", text)        # 고쳐 치는 중 — 뒤로 가기에 쌓지 않는다
            self._build_search(text)
        else:
            self.show("search", text)
        return False

    def _build_search(self, text):
        old = self.stack.get_child_by_name("search")
        if old is not None:
            old.destroy()
        hits = self.cat.search(text)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        v.get_style_context().add_class("page-pad")
        v.pack_start(label(f"‘{text}’ 검색 결과 {len(hits)}개", "page-h", ellipsize=True), False, False, 0)
        if not hits:
            v.pack_start(label("맞는 앱이 없습니다. 다른 이름이나 영어 이름으로 찾아보세요.\n"
                               "데비안 저장소에 없는 앱은 업체 홈페이지의 .deb 설치 파일로 설치할 수 있습니다.",
                               "detail-meta", wrap=True), False, False, 0)
        fb = grid()
        v.pack_start(fb, False, False, 0)
        self._paged(v, fb, hits)
        sw = _scroller(v)
        sw.show_all()
        self.stack.add_named(sw, "search")
        if self.cur and self.cur[0] == "search":
            self.stack.set_visible_child(sw)

    def _paged(self, box, fb, apps):
        """격자를 PAGE 개씩 — 끝에 "더 보기" """
        more = Gtk.Button(label="더 보기")
        more.set_halign(Gtk.Align.CENTER)
        pos = {"n": fill(fb, self, apps, 0, PAGE)}

        def add(*_):
            pos["n"] = fill(fb, self, apps, pos["n"], PAGE)
            more.set_visible(pos["n"] < len(apps))
        more.connect("clicked", add)
        box.pack_start(more, False, False, 0)
        more.set_no_show_all(True)
        more.set_visible(pos["n"] < len(apps))
        return more

    # ── 홈 ──
    def _home_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        v.get_style_context().add_class("page-pad")
        v.pack_start(label("홈", "page-h"), False, False, 0)
        banner = [(self.cat.by_pkg[p], tag, sub) for p, tag, sub in C.BANNER if p in self.cat.by_pkg]
        if banner:
            v.pack_start(self._banner(banner), False, False, 0)
        for title, key, pkgs in C.SECTIONS:
            apps = self.cat.pick(pkgs)
            if not apps:
                continue
            head = Gtk.Box(spacing=8)
            head.pack_start(label(title, "sec-h"), False, False, 0)
            more = Gtk.Button(label="모두 보기 ›")
            more.get_style_context().add_class("link-btn")
            more.connect("clicked", lambda *_, k=key: self.show("games", None) if k == "games"
                         else self.show("apps", k))
            head.pack_end(more, False, False, 0)
            v.pack_start(head, False, False, 0)
            fb = grid()
            fill(fb, self, apps, 0, 6)
            v.pack_start(fb, False, False, 0)
        sw = _scroller(v)
        sw.show_all()
        return sw

    def _banner(self, items):
        """배너 — 돌아가는 그림 카드(Stack) 위에 [자세히 보기] 를 겹쳐 둔다 (Overlay).
        단추를 돌아가는 카드 안에 두면, 카드가 바뀐 뒤 첫 클릭을 단추가 받지 못했다 (Wayland·GtkStack).
        배너 위에 커서가 있는 동안은 넘기지 않는다 — 누르려는 순간 다른 앱으로 바뀌지 않게."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ov = Gtk.Overlay()
        eb = Gtk.EventBox()
        st = Gtk.Stack()
        st.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        st.set_transition_duration(350)
        eb.add(st)
        ov.add(eb)
        box.pack_start(ov, False, False, 0)
        dots = Gtk.Box(spacing=6)
        dots.set_halign(Gtk.Align.CENTER)
        box.pack_start(dots, False, False, 0)
        self._banner_st, self._banner_dots, self._banner_apps = st, [], [a for a, _t, _s in items]
        for i, (app, tag, sub) in enumerate(items):
            card = Gtk.Box(spacing=28)
            card.get_style_context().add_class("hero")
            card.get_style_context().add_class(f"hero-{i % 4}")
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            left.set_valign(Gtk.Align.CENTER)
            left.set_margin_bottom(46)                   # 겹친 [자세히 보기] 자리
            ic = app_icon(app, 80)
            ic.set_halign(Gtk.Align.START)
            left.pack_start(ic, False, False, 0)
            left.pack_start(label(app.name, "hero-name", ellipsize=True), False, False, 0)
            left.pack_start(label(tag, "hero-tag", wrap=True), False, False, 0)
            left.pack_start(label(sub, "hero-sub", wrap=True), False, False, 0)
            card.pack_start(left, True, True, 0)
            shots = app.screenshots(624)
            if shots:
                pic = picture(shots[0][0], 440, 248)
                pic.set_valign(Gtk.Align.CENTER)
                card.pack_end(pic, False, False, 0)
            card.set_size_request(-1, 300)
            st.add_named(card, str(i))
            d = Gtk.Button()
            d.get_style_context().add_class("dot")
            d.set_tooltip_text(app.name)
            d.connect("clicked", lambda *_, n=i: self._banner_to(n, user=True))
            dots.pack_start(d, False, False, 0)
            self._banner_dots.append(d)
        go = Gtk.Button(label="자세히 보기")
        go.get_style_context().add_class("accent-btn")
        go.set_halign(Gtk.Align.START)
        go.set_valign(Gtk.Align.END)
        go.set_margin_start(35)
        go.set_margin_bottom(32)
        go.connect("clicked", lambda *_: self.show_app(self._banner_apps[self._banner_n]))
        ov.add_overlay(go)
        # 배너 어디를 눌러도 그 앱으로 (누른 곳에서 뗄 때)
        eb.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                      | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        eb.connect("button-release-event",
                   lambda _w, ev: self.show_app(self._banner_apps[self._banner_n]) if ev.button == 1 else None)
        self._banner_ov = ov
        eb.connect("enter-notify-event", lambda *_: self._banner_hover(True))
        eb.connect("leave-notify-event", lambda *_: GLib.timeout_add(120, self._banner_left) and False)
        self._banner_n = 0
        self._banner_hovered = False
        self._banner_to(0)
        return box

    def _banner_hover(self, on):
        self._banner_hovered = on
        self._banner_run(self.cur is not None and self.cur[0] == "home")
        return False

    def _banner_left(self):
        """떠난 것이 겹친 단추로 옮겨 간 것이면 아직 배너 위다"""
        ov = getattr(self, "_banner_ov", None)
        if ov is None or not ov.get_mapped():
            return False
        x, y = ov.get_pointer()
        a = ov.get_allocation()
        inside = 0 <= x < a.width and 0 <= y < a.height
        if not inside:
            self._banner_hover(False)
        return False

    def _banner_to(self, n, user=False):
        if not getattr(self, "_banner_dots", None):
            return
        n %= len(self._banner_dots)
        self._banner_n = n
        self._banner_st.set_visible_child_name(str(n))
        for i, d in enumerate(self._banner_dots):
            (d.get_style_context().add_class if i == n else d.get_style_context().remove_class)("on")
        if user:
            self._banner_run(True)          # 눌렀으면 다음으로 넘어가기까지 처음부터 센다

    def _banner_run(self, on):
        if self._banner_src:
            GLib.source_remove(self._banner_src)
            self._banner_src = 0
        if on and getattr(self, "_banner_dots", None) and len(self._banner_dots) > 1 \
                and not getattr(self, "_banner_hovered", False):
            def tick():
                self._banner_to(self._banner_n + 1)
                return True
            self._banner_src = GLib.timeout_add_seconds(7, tick)

    # ── 앱 자세히 ──
    def _build_detail(self, app):
        old = self.stack.get_child_by_name("detail")
        if old is not None:
            old.destroy()
        if self._detail_watch:
            self.jobs.unwatch(self._detail_watch)
            self._detail_watch = None
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        v.get_style_context().add_class("page-pad")

        head = Gtk.Box(spacing=24)
        head.get_style_context().add_class("detail-head")
        ic = app_icon(app, 96)
        ic.set_valign(Gtk.Align.START)
        head.pack_start(ic, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.pack_start(label(app.name, "detail-name", wrap=True), False, False, 0)
        dev = app.developer()
        if dev:
            col.pack_start(label(dev, "detail-dev", ellipsize=True), False, False, 0)
        col.pack_start(label(app.summary, "detail-sum", wrap=True), False, False, 0)
        meta = label(app.category_name(), "detail-meta", wrap=True)
        col.pack_start(meta, False, False, 0)
        if app.alt is not None:
            # 설치 출처 — 데비안 저장소 / Flathub (같은 앱이 양쪽에 있을 때)
            srow = Gtk.Box(spacing=6)
            srow.set_margin_top(6)
            srow.pack_start(label("설치 출처", "detail-meta"), False, False, 4)
            for v_ in sorted(app.variants(), key=lambda x: x.is_flatpak):
                t = v_.source_name() + (" · 설치됨" if self.cat.is_installed(v_) else "")
                b = Gtk.Button(label=t)
                b.get_style_context().add_class("chip")
                if v_ is app:
                    b.get_style_context().add_class("on")
                else:
                    b.connect("clicked", lambda *_, x=v_: self.show("detail", x, push=False))
                srow.pack_start(b, False, False, 0)
            col.pack_start(srow, False, False, 0)
        elif app.is_flatpak:
            col.pack_start(label("설치 출처 · Flathub", "detail-meta"), False, False, 0)
        head.pack_start(col, True, True, 0)

        act = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        act.set_size_request(220, -1)
        act.set_valign(Gtk.Align.START)
        main = Gtk.Button()
        main.get_style_context().add_class("accent-btn")
        main.set_size_request(200, 36)
        rm = Gtk.Button(label="제거")
        rm.set_size_request(200, -1)
        bar = Gtk.ProgressBar()
        bar.get_style_context().add_class("update-progress")
        # 진행 문구는 설치하는 패키지 이름이 계속 바뀐다 — 글자 길이에 따라 오른쪽 칸 너비가 널뛰지 않게
        #   너비를 못 박고(width/max_width_chars) 긴 것은 줄여 보인다
        ptext = label("", "detail-meta", ellipsize=True)
        err = label("", "detail-err", wrap=True)
        for lb in (ptext, err):
            lb.set_width_chars(26)
            lb.set_max_width_chars(26)
        ext_note = label("", "detail-meta", wrap=True)
        ext_note.set_width_chars(26)
        ext_note.set_max_width_chars(26)
        ext_note.set_no_show_all(True)
        act.set_hexpand(False)
        for w in (main, rm, bar, ptext, ext_note, err):
            act.pack_start(w, False, False, 0)
        head.pack_end(act, False, False, 0)
        v.pack_start(head, False, False, 0)

        plan = appmgr.Plan()
        planned = {"done": False}
        last = {"st": self.jobs.state_of(app, merged=False)}

        def refresh(_changed=None):
            st = self.jobs.state_of(app, merged=False)
            if last["st"] in ("installing", "removing") and st in ("installed", "none") and app.alt is not None:
                # 끝났다 — 출처 칩의 "설치됨" 표시까지 새로 (양쪽에 있는 앱)
                last["st"] = st
                GLib.idle_add(lambda: self.cur == ("detail", app) and self.show("detail", app, push=False) and False)
                return False
            last["st"] = st
            prog = self.jobs.progress_of(app)
            e = self.jobs.errors.get(app.pkg)
            err.set_text(e or "")
            err.set_visible(bool(e))
            main.set_sensitive(True)
            rm.set_visible(st == "installed")
            if st == "installed" and self.cat.is_system(app):
                rm.set_sensitive(False)
                rm.set_tooltip_text("SekaiOS 기본 구성에 들어 있어 제거할 수 없습니다")
            ext = self.cat.external(app) if st == "installed" else None
            ext_note.set_visible(bool(ext))
            if ext:
                # 스토어 밖에서 깐 판 — 여는 것만. 지우기·업데이트는 그 판의 길로 (설정 › 앱 › 설치된 앱)
                rm.set_visible(False)
                ext_note.set_text(f"이 컴퓨터에 이미 설치되어 있습니다 — {ext[0]}. "
                                  "제거는 설정 › 앱 › 설치된 앱에서 할 수 있습니다.")
            bar.set_visible(prog is not None)
            ptext.set_visible(prog is not None or st == "queued")
            if st == "installed":
                main.set_label("열기")
                meta.set_text(app.category_name())      # 받을 크기 안내는 설치 전에만
            elif st == "queued":
                main.set_label("대기 취소")
                ptext.set_text("다른 설치가 끝나면 시작합니다")
            elif st in ("installing", "removing"):
                main.set_label("설치하는 중…" if st == "installing" else "제거하는 중…")
                main.set_sensitive(False)
                bar.set_fraction(prog["pct"] / 100)
                ptext.set_text(prog["text"])
            else:
                main.set_label("설치")
                if planned["done"] and plan.blocks:
                    main.set_sensitive(False)
                    err.set_text(plan.blocks[0])
                    err.show()
            return False

        def on_main(*_):
            st = self.jobs.state_of(app, merged=False)
            if st == "installed":
                if not launch(app, self.cat):
                    err.set_text("이 앱은 시작 메뉴에 나오는 창이 없습니다 (명령줄 프로그램이거나 구성 요소일 수 있습니다)")
                    err.show()
            elif st == "queued":
                self.jobs.cancel(app)
            elif st == "none":
                self.jobs.add("install", app)
        main.connect("clicked", on_main)
        rm.connect("clicked", lambda *_: self._confirm_remove(app))
        self._detail_watch = refresh
        self.jobs.watch(refresh)

        # 설치하면 함께 들어오는 것·크기 (보통 권한으로 미리 계산)
        def got_plan(rc):
            if self.cur != ("detail", app) or self._detail_watch is not refresh:
                return False                    # 그사이 다른 쪽으로 갔다 — 이 결과를 쓸 화면이 없다
            planned["done"] = True
            if plan.error:
                if not self.cat.is_installed(app):
                    err.set_text(plan.error)
                    err.show()
                return False
            bits = []
            dl = _human_bytes(plan.info.get("Download"))
            sz = appmgr.human_size(plan.info.get("Size"))
            if self.cat.is_installed(app):
                # 이미 깔렸으면 더 받을 것이 없어 계획이 0 KB 다 — 깔린 패키지 자체의 크기로
                dl, sz = None, appmgr.human_size(_dpkg_installed_kb(app.pkg))
            else:
                if dl:
                    bits.append(f"다운로드 {dl}")
                if sz:
                    bits.append(f"설치 크기 {sz}")
                if plan.add:
                    bits.append(f"함께 설치되는 구성 요소 {len(plan.add)}개")
            meta.set_text(" · ".join([app.category_name()] + bits))
            self._info_set("버전", plan.info.get("Version"))
            self._info_set("다운로드 크기", dl, hide_empty=True)
            self._info_set("설치 크기", sz)
            refresh()
            return False
        if app.is_flatpak:
            # Flathub — 크기는 libflatpak 으로 (처음이면 런타임도 함께 받는다는 것까지)
            def fp_plan():
                r = appmgr.flatpak_plan(app.ref)
                GLib.idle_add(got_fp, r)

            def got_fp(r):
                if self.cur != ("detail", app) or self._detail_watch is not refresh:
                    return False
                planned["done"] = True
                if r.get("error"):
                    if not self.cat.is_installed(app):
                        err.set_text(r["error"])
                        err.show()
                    return False
                dl, sz = _human_bytes(r["dl"]), _human_bytes(r["inst"])
                bits = []
                if not self.cat.is_installed(app):
                    bits += [f"다운로드 {dl}" if dl else None, f"설치 크기 {sz}" if sz else None]
                    if r["runtime"]:
                        bits.append(f"처음 한 번 실행 환경({r['runtime']}) {_human_bytes(r['rt_dl'])} 를 함께 받습니다")
                meta.set_text(" · ".join([app.category_name()] + [b for b in bits if b]))
                if self.cat.is_installed(app):
                    dl = None
                self._info_set("다운로드 크기", dl, hide_empty=True)
                self._info_set("설치 크기", sz)
                refresh()
                return False
            threading.Thread(target=fp_plan, daemon=True).start()
        else:
            appmgr.run_helper(["plan-install", app.pkg], plan.feed, got_plan)

        shots = app.screenshots()[:6]
        if shots:
            sh = label("스크린샷", "sec-h")
            v.pack_start(sh, False, False, 0)
            row = Gtk.Box(spacing=12)
            shot_sw = Gtk.ScrolledWindow()
            left = {"n": len(shots)}

            def failed(_pic):
                left["n"] -= 1
                if left["n"] <= 0:              # 하나도 못 받았다 — 제목째 숨긴다 (쪽 전체가 아니라)
                    for w in (sh, shot_sw):
                        w.hide()
                        w.set_no_show_all(True)
            for small, big in shots:
                pic = picture(small, 400, 225, on_fail=failed)
                pic.connect("button-press-event", lambda _w, ev, u=big: self._show_shot(u) if ev.button == 1 else None)
                row.pack_start(pic, False, False, 0)
            shot_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            shot_sw.set_min_content_height(240)
            shot_sw.add(row)
            v.pack_start(shot_sw, False, False, 0)

        desc = app.description_markup()
        if desc:
            v.pack_start(label("설명", "sec-h"), False, False, 0)
            # 고를 수 있는 글로 두면 쪽이 열릴 때 초점을 받아 글 전체가 선택된 채로 보였다
            d = label(desc, "info-v", wrap=True, markup=True)
            d.set_max_width_chars(110)
            v.pack_start(d, False, False, 0)

        v.pack_start(label("정보", "sec-h"), False, False, 0)
        info = Gtk.Grid(column_spacing=40, row_spacing=8)
        self._info_grid, self._info_rows = info, {}
        for k, val in (("개발자", dev), ("버전", app.version() if app.is_flatpak else None),
                       ("다운로드 크기", None), ("설치 크기", None),
                       ("라이선스", app.license()), ("분류", app.category_name()), ("출처", app.source_name()),
                       ("앱 ID" if app.is_flatpak else "패키지", app.pkg)):
            self._info_set(k, val)
        hp = app.homepage()
        if hp:
            n = len(self._info_rows)
            info.attach(label("홈페이지", "info-k"), 0, n, 1, 1)
            lb = Gtk.LinkButton.new_with_label(hp, hp)
            lb.set_halign(Gtk.Align.START)
            info.attach(lb, 1, n, 1, 1)
        v.pack_start(info, False, False, 0)

        sw = _scroller(v)
        sw.show_all()
        self.stack.add_named(sw, "detail")
        refresh()

    def _info_set(self, key, val, hide_empty=False):
        """정보 표의 한 줄. hide_empty 면 값이 없을 때 줄째 숨긴다 (설치된 앱의 다운로드 크기처럼)"""
        g = getattr(self, "_info_grid", None)
        if g is None:
            return
        if key not in self._info_rows:
            n = len(self._info_rows)
            k, v = label(key, "info-k"), label("", "info-v")
            g.attach(k, 0, n, 1, 1)
            g.attach(v, 1, n, 1, 1)
            self._info_rows[key] = (k, v)
        k, v = self._info_rows[key]
        v.set_text(val or "-")
        for w in (k, v):
            w.set_visible(bool(val) or not hide_empty)

    def _show_shot(self, url):
        d = Gtk.Dialog(title="스크린샷", transient_for=self, modal=True)
        d.set_default_size(1000, 640)
        pic = picture(url, 960, 600)
        d.get_content_area().pack_start(pic, True, True, 0)
        pic.connect("button-press-event", lambda *_: d.destroy())
        d.connect("key-press-event", lambda _w, ev: d.destroy() if ev.keyval == Gdk.KEY_Escape else None)
        d.show_all()

    def _confirm_remove(self, app):
        if self.jobs.state_of(app, merged=False) != "installed":
            return
        d = Gtk.Dialog(title=f"{app.name} 제거", transient_for=self, modal=True)
        d.set_default_size(460, -1)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        ok = d.add_button("제거", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("accent-btn")
        ok.set_sensitive(False)
        box = d.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)
        box.add(label(f"{app.name} 을(를) 이 컴퓨터에서 제거합니다.", "tile-name", wrap=True))
        msg = label("함께 지워지는 것을 확인하는 중…", "info-v", wrap=True)
        msg.set_max_width_chars(56)
        box.add(msg)
        plan = appmgr.Plan()
        if app.is_flatpak:
            msg.set_text("Flathub 앱과, 더 이상 쓰는 앱이 없는 실행 환경(런타임)을 함께 정리합니다.\n"
                         "내 문서와 앱 설정(홈 폴더의 .var/app)은 그대로 남습니다.")
            ok.set_sensitive(True)

        def planned(rc):
            if plan.error or not plan.done:
                msg.set_text(plan.error or f"확인하지 못했습니다 (코드 {rc})")
                return False
            if plan.blocks:
                msg.set_text("\n".join(plan.blocks))
                ok.hide()
                return False
            lines = []
            if plan.dele:
                lines.append(f"함께 제거되는 구성 요소 {len(plan.dele)}개: " + ", ".join(plan.dele[:10])
                             + (" …" if len(plan.dele) > 10 else ""))
            lines.append("내 문서와 앱 설정(홈 폴더)은 그대로 남습니다.")
            msg.set_text("\n".join(lines))
            ok.set_sensitive(True)
            return False
        if not app.is_flatpak:
            appmgr.run_helper(["plan-remove", app.pkg], plan.feed, planned)

        def resp(_d, r):
            if r == Gtk.ResponseType.OK and ok.get_sensitive():
                self.jobs.add("remove", app)
            d.destroy()
        d.connect("response", resp)
        d.show_all()

    def _on_job(self, _app=None):
        """라이브러리를 보고 있으면 설치·제거가 바뀔 때 다시 그린다 (진행률마다는 아니고 잠깐 모아서)"""
        if self.cur and self.cur[0] == "library" and not self._lib_src:
            def again():
                self._lib_src = 0
                if self.cur and self.cur[0] == "library":
                    adj = self.stack.get_child_by_name("library").get_vadjustment().get_value()
                    self._build_library()
                    lib = self.stack.get_child_by_name("library")
                    self.stack.set_visible_child(lib)   # 보이던 쪽을 바꿔 끼웠다 — 새 쪽을 보이게
                    GLib.idle_add(lambda: lib.get_vadjustment().set_value(adj) and False)
                return False
            self._lib_src = GLib.timeout_add(600, again)

    # ── 라이브러리 ──
    def _build_library(self):
        old = self.stack.get_child_by_name("library")
        if old is not None:
            old.destroy()
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        v.get_style_context().add_class("page-pad")
        head = Gtk.Box(spacing=8)
        head.pack_start(label("라이브러리", "page-h"), False, False, 0)
        upd = Gtk.Button(label="업데이트 확인")
        upd.connect("clicked", lambda *_: Gio.Subprocess.new(["sekai-settings", "--page=update"],
                                                               Gio.SubprocessFlags.NONE))
        head.pack_end(upd, False, False, 0)
        ref = Gtk.Button(label="앱 목록 새로 고침")
        head.pack_end(ref, False, False, 0)
        v.pack_start(head, False, False, 0)
        status = label("", "detail-meta")
        v.pack_start(status, False, False, 0)
        ref.connect("clicked", lambda *_: self.refresh_catalog(status))

        if self.jobs.current or self.jobs.queue:
            v.pack_start(label("진행 중", "sec-h"), False, False, 0)
            items = ([self.jobs.current[1]] if self.jobs.current else []) + [a for _o, a in self.jobs.queue]
            fb = grid()
            fill(fb, self, items, 0, len(items))
            v.pack_start(fb, False, False, 0)

        inst = sorted((a for a in self.cat.by_pkg.values() if self.cat.is_installed(a)), key=lambda a: a.name.lower())
        inst += sorted((a for a in self.cat.apps if self.cat.external(a)), key=lambda a: a.name.lower())
        mine = [a for a in inst if not self.cat.is_system(a)]
        base = [a for a in inst if self.cat.is_system(a)]
        v.pack_start(label(f"설치한 앱 {len(mine)}개", "sec-h"), False, False, 0)
        v.pack_start(label("스토어 목록에 있는 앱만 보입니다. 전체 목록은 설정 › 앱 › 설치된 앱에서 볼 수 있습니다.",
                           "detail-meta", wrap=True), False, False, 0)
        self._lib_rows(v, mine, removable=True)
        if base:
            v.pack_start(label(f"SekaiOS 기본 구성 {len(base)}개", "sec-h"), False, False, 0)
            v.pack_start(label("SekaiOS 가 쓰는 앱이라 제거할 수 없습니다.", "detail-meta", wrap=True), False, False, 0)
            self._lib_rows(v, base, removable=False)
        sw = _scroller(v)
        sw.show_all()
        self.stack.add_named(sw, "library")

    def _lib_rows(self, v, apps, removable):
        for a in apps:
            row = Gtk.Box(spacing=14)
            row.get_style_context().add_class("lib-row")
            row.pack_start(app_icon(a, 36), False, False, 0)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            col.set_valign(Gtk.Align.CENTER)
            name = Gtk.Button(label=a.name)
            name.get_style_context().add_class("link-btn")
            name.set_halign(Gtk.Align.START)
            name.connect("clicked", lambda *_, x=a: self.show_app(x))
            col.pack_start(name, False, False, 0)
            ext = self.cat.external(a)
            col.pack_start(label(a.summary + (f" · {ext[0]}" if ext else " · Flathub" if a.is_flatpak else ""),
                                 "tile-sub", ellipsize=True), False, False, 0)
            row.pack_start(col, True, True, 0)
            o = Gtk.Button(label="열기")
            o.connect("clicked", lambda *_, x=a: launch(x, self.cat))
            r = Gtk.Button(label="제거")
            r.connect("clicked", lambda *_, x=a: self._confirm_remove(x))
            r.set_sensitive(removable and not ext)
            if ext:
                r.set_tooltip_text("스토어 밖에서 설치한 판 — 설정 › 앱 › 설치된 앱에서 제거할 수 있습니다")
            row.pack_end(r, False, False, 0)
            row.pack_end(o, False, False, 0)
            v.pack_start(row, False, False, 0)

    # ── 키·닫기 ──
    def _on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        alt = ev.state & Gdk.ModifierType.MOD1_MASK
        if ctrl and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_l, Gdk.KEY_L):
            self.search.grab_focus()
            return True
        if (alt and ev.keyval == Gdk.KEY_Left) or ev.keyval == Gdk.KEY_Back:
            self.go_back()
            return True
        if ev.keyval == Gdk.KEY_BackSpace and not self.search.has_focus():
            self.go_back()
            return True
        if ev.keyval == Gdk.KEY_Escape and self.search.has_focus() and self.search.get_text():
            self.search.set_text("")
            return True
        return False

    def _on_delete(self, *_):
        if not self.jobs.busy():
            return False
        # 설치 중에 닫으면 — 지금 것은 끝까지 가지만(도우미는 창과 상관없이 끝난다) 대기열은 사라진다
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE,
                              text="설치가 진행 중입니다")
        d.format_secondary_text("지금 설치 중인 앱은 끝까지 설치됩니다. 대기 중인 앱은 설치하지 않습니다.\n"
                                "스토어를 닫을까요?")
        d.add_button("계속 두기", Gtk.ResponseType.CANCEL)
        d.add_button("닫기", Gtk.ResponseType.OK)
        r = d.run()
        d.destroy()
        if r == Gtk.ResponseType.OK:
            self.jobs.queue.clear()
            return False
        return True


class ListPage(Gtk.ScrolledWindow):
    """앱 · 게임 쪽 — 분류 칩 + 격자"""

    def __init__(self, store, kind):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.store, self.kind = store, kind
        self.cats = ([("all", "전체", ())] + C.CATEGORIES) if kind == "apps" else C.GAME_CATEGORIES
        self.key = None
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        v.get_style_context().add_class("page-pad")
        v.pack_start(label("앱" if kind == "apps" else "게임", "page-h"), False, False, 0)
        chips = Gtk.FlowBox()
        chips.set_selection_mode(Gtk.SelectionMode.NONE)
        chips.set_max_children_per_line(20)
        chips.set_column_spacing(6)
        chips.set_row_spacing(6)
        self.chips = {}
        for key, name, _cs in self.cats:
            b = Gtk.Button(label=name)
            b.get_style_context().add_class("chip")
            b.connect("clicked", lambda *_, k=key: store.show(kind, k) if k != self.key else None)
            chips.add(b)
            self.chips[key] = b
        v.pack_start(chips, False, False, 0)
        self.count = label("", "detail-meta")
        v.pack_start(self.count, False, False, 0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        v.pack_start(self.body, False, False, 0)
        self.add(v)
        self.show_all()

    def set_category(self, key):
        key = key or "all"
        if key == self.key:
            return
        self.key = key
        for k, b in self.chips.items():
            (b.get_style_context().add_class if k == key else b.get_style_context().remove_class)("on")
        cs = next((c for k, _n, c in self.cats if k == key), ())
        base = self.store.cat.apps_only() if self.kind == "apps" else self.store.cat.games()
        apps = [a for a in base if a.in_cats(cs)] if cs else base
        self.count.set_text(f"{len(apps)}개")
        for c in self.body.get_children():
            c.destroy()
        fb = grid()
        self.body.pack_start(fb, False, False, 0)
        self.store._paged(self.body, fb, apps)
        self.body.show_all()
        self.get_vadjustment().set_value(0)


class StoreApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        _THEME.reload()                                  # 설정 앱의 색·모드 — 바꾸면 곧바로 따라간다
        _THEME.follow(lambda _a: [w.queue_draw() for w in self.get_windows()])

    def do_command_line(self, cl):
        want = None
        for a in cl.get_arguments()[1:]:
            if a.startswith("--app="):
                want = ("app", a.split("=", 1)[1])
            elif a.startswith("--search="):
                want = ("search", a.split("=", 1)[1])
            else:
                dbg(f"[sekai-store] 모르는 옵션: {a}")
        if self.win is None:
            self.win = StoreWindow(self)
            self.win.connect("destroy", lambda *_: setattr(self, "win", None))
        w = self.win
        if want:
            if not w.cat.apps:
                w.pending = want
            elif want[0] == "app" and want[1] in w.cat.by_pkg:
                w.show_app(w.cat.by_pkg[want[1]])
            elif want[0] == "search":
                w.search.set_text(want[1])
                w._run_search()
        w.present()
        return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-store [--app=<패키지>] [--search=<검색어>]")
        return 0
    GLib.set_prgname(APP_ID)
    return StoreApp().run([sys.argv[0]] + list(argv))
