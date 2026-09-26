"""파일 탐색기 — 창 하나 (윈도우 11 탐색기처럼).

    ┌ ← → ↑ ⟳  [ 주소 창 (칸 · 고쳐 쓰기) ]                [ 🔍 문서 검색 ] ┐
    │ 새로 만들기 ▾ │ ✂ ⧉ 📋 ✎ 🗑 │ 정렬 ▾  보기 ▾ │ (휴지통 비우기 · 복원 · 꺼내기 …)   ⋯ │
    ├ 탐색 창 ─┬ 내용 — 홈 · 내 PC · 폴더(큰 아이콘 · 보통 아이콘 · 자세히) · 검색 결과 ─────┤
    └ 항목 24개   1개 항목 선택함 3.4 MB                                    [≡][▦] ┘

위치는 URI 문자열 하나 (common: HOME · COMPUTER · TRASH · file:///…). 뒤로/앞으로는 창마다.
폴더 목록은 folder.FolderModel, 보기는 views.IconsView · views.DetailsView (둘이 같은 모델을 번갈아 보인다).
파일 작업(복사·이동·삭제·이름 바꾸기·클립보드)은 sekaishell.fileops — 진행 창과 충돌 창도 그쪽이 띄운다.
썸네일(thumbs) · 압축(archive) · 속성(properties) 도 부품 — 없으면 그 명령만 꺼진다.
"""
import os
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import opener  # noqa: E402
from .addressbar import AddressBar  # noqa: E402
from .common import (COMPUTER, HOME, TRASH, archive, copy_text, count_text, crumbs,  # noqa: E402
                     dbg, edit_text, favorites, fileops, fit_pixbuf, fmt_size, gfile_of, home_dir, icons,
                     image, is_in_trash, is_special, list_drives, local_path, location_icon, mount_names, norm_uri,
                     parse_location, properties, quote_path, split_ext, theme_icon, thumbable, thumbs,
                     title_of, uri_of_path, SORT_LABELS)
from .folder import PX_L, PX_M, FolderModel  # noqa: E402
from .navpane import NavPane  # noqa: E402
from .pages import ComputerPage, HomePage  # noqa: E402
from .search import Searcher  # noqa: E402
from .views import DetailsView, IconsView  # noqa: E402

VIEW_MODES = ("large", "medium", "details")
VIEW_LABELS = {"large": "큰 아이콘", "medium": "보통 아이콘", "details": "자세히"}
VIEW_ICONS = {"large": ["view-grid-symbolic", "view-app-grid-symbolic"],
              "medium": ["view-grid-symbolic", "view-app-grid-symbolic"],
              "details": ["view-list-symbolic", "view-list-details-symbolic"]}
FOLDER_SORTS = ("name", "mtime", "type", "size")
NUMERIC_SORTS = ("mtime", "size", "dtime")
CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK
ALT = Gdk.ModifierType.MOD1_MASK
COPIED_TARGETS = ("x-special/gnome-copied-files", "text/uri-list", "x-special/mate-copied-files")


def _mitem(menu, label, cb=None, icon=None, accel=None, sensitive=True, bold=False, submenu=None):
    """아이콘 · 이름 · 단축키가 있는 메뉴 항목"""
    it = Gtk.MenuItem()
    box = Gtk.Box(spacing=10)
    if icon:
        box.pack_start(image(icon, 16), False, False, 0)
    else:
        pad = Gtk.Box()
        pad.set_size_request(16, 16)
        box.pack_start(pad, False, False, 0)
    lbl = Gtk.Label(xalign=0)
    if bold:
        lbl.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
    else:
        lbl.set_text(label)
    box.pack_start(lbl, True, True, 0)
    if accel:
        a = Gtk.Label(label=accel, xalign=1)
        a.get_style_context().add_class("fx-accel")
        box.pack_end(a, False, False, 0)
    it.add(box)
    it.set_sensitive(sensitive)
    if submenu is not None:
        it.set_submenu(submenu)
    elif cb is not None:
        it.connect("activate", lambda *_: cb())
    menu.append(it)
    return it


def _sep(menu):
    menu.append(Gtk.SeparatorMenuItem())


def _new_menu():
    m = Gtk.Menu()
    m.get_style_context().add_class("fx-menu")
    m.connect("deactivate", lambda mm: GLib.idle_add(mm.destroy))
    return m


def _same_fs(a, b):
    """같은 파일 시스템인가 (끌어 놓기의 기본 동작 — 같으면 이동, 다르면 복사). 로컬 경로끼리만 안다"""
    pa, pb = a.get_path(), b.get_path()
    if not pa or not pb:
        return False
    try:
        return os.lstat(pa).st_dev == os.stat(pb).st_dev
    except OSError:
        return False


# Hyprland 세션인가 — 아니면 기본 화면 모드(X11, xfwm4)
HYPR = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


class _CapIcon(Gtk.DrawingArea):
    """창 단추 기호 — 1px 선으로 그린다 (다른 창의 제목 표시줄 hyprbars 의 sekai:min·max·close 와 같은 모양).
    색은 단추의 CSS 글자색을 따른다 (닫기 단추에 마우스를 올리면 흰색)"""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.set_size_request(16, 16)
        self.connect("draw", self._draw)

    def set_kind(self, kind):
        self.kind = kind
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        S = 10                                   # 기호 한 변 (논리 픽셀)
        x0, y0 = (a.width - S) // 2 + 0.5, (a.height - S) // 2 + 0.5   # 선이 픽셀에 맞게
        c = w.get_style_context().get_color(w.get_state_flags())
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.set_line_width(1)
        k = self.kind
        if k == "min":
            y = y0 + S // 2
            cr.move_to(x0, y)
            cr.line_to(x0 + S, y)
        elif k == "max":
            cr.rectangle(x0, y0, S - 1, S - 1)
        elif k == "restore":                     # 겹친 네모 둘
            cr.rectangle(x0, y0 + 2, S - 3, S - 3)
            cr.move_to(x0 + 2, y0 + 2)
            cr.line_to(x0 + 2, y0)
            cr.line_to(x0 + S - 1, y0)
            cr.line_to(x0 + S - 1, y0 + S - 3)
            cr.line_to(x0 + S - 3, y0 + S - 3)
        else:                                    # close
            cr.move_to(x0, y0)
            cr.line_to(x0 + S - 1, y0 + S - 1)
            cr.move_to(x0 + S - 1, y0)
            cr.line_to(x0, y0 + S - 1)
        cr.stroke()
        return False


class Tab:
    """탭 하나 — 위치·뒤로/앞으로 기록·폴더 내용·검색·보기 모양은 탭마다.
    툴바·주소 표시줄·왼쪽 창·보기 위젯은 창에 하나고, 탭을 바꾸면 그 탭의 것을 갈아 끼운다 (switch_tab)"""

    def __init__(self):
        self.uri = None
        self.history = []
        self.hpos = -1
        self.searching = False
        self.searcher = None
        self.search_query = ""
        self.saved_mode = None                  # 검색 결과·휴지통은 자세히 — 그 전 보기 모양
        self.want_select = None                 # (uri 집합, 이름 바꾸기 시작?)
        self.folder = None
        self.search_model = None
        self.model = None
        self.view_mode = "details"
        self.sel = []                           # 다른 탭으로 갈 때 고른 항목 · 스크롤 (돌아오면 되살린다)
        self.scroll = None
        self.button = self.box = self.icon = self.label = None


def _tab_attr(name):
    """창의 self.uri · self.model … 은 지금 탭의 것 (창 코드는 탭을 몰라도 된다)"""
    return property(lambda self: getattr(self.tab, name), lambda self, v: setattr(self.tab, name, v))


class ExplorerWindow(Gtk.ApplicationWindow):
    uri = _tab_attr("uri")
    history = _tab_attr("history")
    hpos = _tab_attr("hpos")
    searching = _tab_attr("searching")
    searcher = _tab_attr("searcher")
    search_query = _tab_attr("search_query")
    _saved_mode = _tab_attr("saved_mode")
    _want_select = _tab_attr("want_select")
    folder = _tab_attr("folder")
    search_model = _tab_attr("search_model")
    model = _tab_attr("model")
    view_mode = _tab_attr("view_mode")

    def __init__(self, app, uri=None, select=None):
        super().__init__(application=app, title="파일 탐색기")
        self.app = app
        st = app.state
        self.set_icon_name("system-file-manager")
        w, h = st.get("size") or (1100, 680)
        try:
            self.set_default_size(max(640, int(w)), max(420, int(h)))
        except (TypeError, ValueError):
            self.set_default_size(1100, 680)
        # 지난번에 최대화한 채 닫았으면 — 창이 나타난 뒤에 (그 전에 청하면 Hyprland 가 받지 않는데 GTK 는
        #   최대화된 줄로 알아 단추 모양이 어긋났다)
        self._max_at_start = bool(st.get("maximized"))
        # 좁게도 줄어든다 — 화면 3분할 스냅(1280 화면이면 426px)까지. 좁으면 왼쪽 창을 숨긴다 (_on_alloc)
        self.set_size_request(380, 320)
        for c in ("settings-window", "fx-window"):
            self.get_style_context().add_class(c)

        self.tab = Tab()
        self.tabs = [self.tab]
        self.view_mode = st.get("view") if st.get("view") in VIEW_MODES else "details"
        srt = st.get("sort") if isinstance(st.get("sort"), list) and len(st.get("sort")) == 2 else ["name", False]
        self.sort_field = srt[0] if srt[0] in SORT_LABELS else "name"
        self.sort_desc = bool(srt[1])
        self.show_hidden = bool(st.get("hidden"))
        self._thumb_src = 0
        self._sel_src = 0
        self._toast_src = 0
        self._typeahead = ""
        self._typeahead_t = 0
        self._can_paste = False
        self._cut_uris = set()
        self._after_delete = None
        self.thumbnailer = None
        if thumbs is not None:
            try:
                self.thumbnailer = thumbs.Thumbnailer()
            except Exception as e:
                print("[sekai-files] 썸네일을 쓸 수 없습니다:", e, flush=True)

        # ── 보기 · 모델 ──
        self.icons = IconsView(self, None)
        self.fitter = self.icons.fitter
        self.icons.set_mode("large" if self.view_mode != "medium" else "medium")
        self.details = DetailsView(self, st.get("columns") if isinstance(st.get("columns"), dict) else None)
        self.folder = self._new_model("folder")
        self.model = self.folder

        # ── 틀 ──
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.get_style_context().add_class("fx-top")
        root.pack_start(top, False, False, 0)
        self._build_titlebar()
        top.pack_start(self._build_toolbar(), False, False, 0)
        top.pack_start(self._build_cmdbar(), False, False, 0)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.get_style_context().add_class("fx-paned")
        root.pack_start(self.paned, True, True, 0)
        self.nav = NavPane(self, app.vm)
        self.nav.set_size_request(160, -1)
        self.paned.pack1(self.nav, False, False)
        try:
            self.paned.set_position(max(160, min(480, int(st.get("nav_width", 230)))))
        except (TypeError, ValueError):
            self.paned.set_position(230)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.get_style_context().add_class("fx-content")
        self.paned.pack2(content, True, False)
        overlay = Gtk.Overlay()
        content.pack_start(overlay, True, True, 0)
        self.stack = Gtk.Stack()
        self.stack.set_hhomogeneous(False)      # 안 보이는 페이지(홈·내 PC)의 폭까지 창 최소 폭에 넣지 않는다
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        overlay.add(self.stack)
        self.home_page = HomePage(self)
        self.stack.add_named(self.home_page.widget, "home")
        self.computer_page = ComputerPage(self, app.vm)
        self.stack.add_named(self.computer_page.widget, "computer")
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.view_stack.add_named(self.icons.scroll, "icons")
        self.view_stack.add_named(self.details.scroll, "details")
        folder_box = Gtk.Overlay()
        folder_box.add(self.view_stack)
        self.empty = Gtk.Label()
        self.empty.get_style_context().add_class("fx-empty")
        self.empty.set_halign(Gtk.Align.CENTER)
        self.empty.set_valign(Gtk.Align.START)
        self.empty.set_line_wrap(True)
        self.empty.set_justify(Gtk.Justification.CENTER)
        self.empty.set_no_show_all(True)
        folder_box.add_overlay(self.empty)
        folder_box.set_overlay_pass_through(self.empty, True)
        self.stack.add_named(folder_box, "folder")
        self.toast_label = Gtk.Label(xalign=0)
        self.toast_label.set_line_wrap(True)
        self.toast_label.get_style_context().add_class("fx-toast")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.END)
        self.toast_label.set_no_show_all(True)
        overlay.add_overlay(self.toast_label)
        overlay.set_overlay_pass_through(self.toast_label, True)

        root.pack_start(self._build_statusbar(), False, False, 0)

        self.connect("key-press-event", self._on_key)
        self.connect("size-allocate", self._on_alloc)
        self.connect("button-press-event", self._on_button)
        self.connect("delete-event", self._on_close)
        self.connect("destroy", self._on_destroy)
        self.connect("window-state-event", self._on_wstate)
        self.icons.view.connect("scroll-event", self._on_scroll_zoom)
        self.details.view.connect("scroll-event", self._on_scroll_zoom)

        self._clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._clip_sig = self._clip.connect("owner-change", lambda *_: self._check_clipboard())
        self._check_clipboard()
        self._vm_sigs = [app.vm.connect(s, lambda *_: self._drives_changed())
                         for s in ("mount-added", "mount-removed", "mount-changed", "volume-added",
                                   "volume-removed", "volume-changed")]
        self.details.set_sort_indicator(self.sort_field, self.sort_desc)
        self.root_box = root
        root.show_all()
        self.navigate(uri or HOME, select=select)

    # ════════════════════════════════════════════════════════
    #  탭 (윈도우 11 탐색기처럼 — Ctrl+T 새 탭, Ctrl+W 닫기, Ctrl+Tab 다음, 가운데 단추로 새 탭에서 열기)
    # ════════════════════════════════════════════════════════
    def _new_model(self, kind):
        """탭마다 따로 — 알림에 그 모델 자신을 실어 보낸다 (_model_event 는 지금 탭의 모델 것만 받는다)"""
        box = []
        m = FolderModel(kind, self._sort_for(kind), self.sort_desc, self.show_hidden, self.fitter,
                        lambda ev, v: self._model_event(box[0], ev, v))
        box.append(m)
        m.dimmed = set(self._cut_uris)
        return m

    # ── 제목 표시줄 ──
    def _build_titlebar(self):
        """탭이 제목 표시줄에 (윈도우 11 탐색기처럼). 이 창은 Hyprland 의 제목 표시줄(hyprbars)을 쓰지 않는다 —
        hyprland.conf 의 nobar 규칙이 "처음 제목이 '파일 탐색기'인 창"을 고른다 (대화상자는 같은 앱이라도 그대로).
        그래서 폴더 이름은 창이 나타난 뒤에 제목으로 건다 (_set_win_title).
        빈 곳을 끌면 옮겨진다(가장자리로 끌면 스냅 — Hyprland 의 SEKAI_CLIENT_MOVE), 두 번 누르면 최대화 — GTK 가 한다.
        기본 화면 모드(X11)에서는 xfwm4 가 이 제목 표시줄을 알아보고 자기 것을 그리지 않는다 (GTK CSD)"""
        hb = Gtk.HeaderBar()
        hb.get_style_context().add_class("fx-titlebar")
        hb.set_show_close_button(False)
        hb.set_custom_title(Gtk.Box())              # 가운데 제목 글은 없다 (윈도우처럼)
        tabs = self._build_tabbar()
        tabs.set_valign(Gtk.Align.END)
        hb.pack_start(tabs)
        caps = Gtk.Box(spacing=0)
        caps.set_valign(Gtk.Align.START)
        self.b_min = self._cap_btn("min", "최소화", self._minimize)
        self.b_max = self._cap_btn("max", "최대화", self._toggle_max)
        self.b_close = self._cap_btn("close", "닫기", self.close, "close")
        for b in (self.b_min, self.b_max, self.b_close):
            caps.pack_start(b, False, False, 0)
        hb.pack_end(caps)
        if HYPR:
            # Hyprland 의 최대화(fullscreen 1)는 GTK 가 알아보지 못한다 — GTK 가 두 번 누르기로 보내는 최대화·복원
            #   요청은 어긋났다(복원했는데 최대화된 줄로 알거나 크기가 작아짐). 두 번 누르기는 창이 직접 받는다
            #   (_on_button — 제목 표시줄 위치인지 본다; 이 위젯은 자기 입력 창이 없어 누름을 받지 못한다)
            Gtk.Settings.get_default().set_property("gtk-titlebar-double-click", "none")
            self.connect("configure-event", lambda *_: self._sync_max_later(250))
        self._titlebar = hb
        self.set_titlebar(hb)
        hb.show_all()
        self._win_title = self.get_title()
        self._title_ready = False

        def ready(*_):
            if self._max_at_start:
                self._max_at_start = False
                GLib.timeout_add(120, lambda: (self._toggle_max(), False)[1])
            if not self._title_ready:
                # 처음 제목("파일 탐색기")이 Hyprland 에 자리 잡은 뒤 — 조금 기다렸다가 폴더 이름으로
                def go():
                    self._title_ready = True
                    self.set_title(self._win_title)
                    return False
                GLib.timeout_add(200, go)
            return False
        self.connect("map-event", ready)

    def _set_win_title(self, text):
        self._win_title = text
        if getattr(self, "_title_ready", False):
            self.set_title(text)

    def _cap_btn(self, kind, tooltip, cb, cls=None):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.get_style_context().add_class("fx-cap")
        if cls:
            b.get_style_context().add_class(cls)
        b.add(_CapIcon(kind))
        b.set_tooltip_text(tooltip)
        b.connect("clicked", lambda *_: cb())
        return b

    def _toggle_max(self):
        """다른 창의 제목 표시줄(hyprbars)의 최대화 단추와 같은 명령 (복원 크기도 Hyprland 가 기억한 것).
        단추 모양은 Hyprland 에 물어 맞춘다 (_sync_max). 기본 화면 모드(X11)는 GTK 로"""
        if HYPR:
            try:
                subprocess.Popen(["hyprctl", "dispatch", "fullscreen", "1"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._sync_max_later(200)
                return
            except OSError:
                pass
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def _sync_max_later(self, ms):
        if getattr(self, "_max_src", 0):
            GLib.source_remove(self._max_src)

        def go():
            self._max_src = 0
            self._sync_max()
            return False
        self._max_src = GLib.timeout_add(ms, go)

    def _sync_max(self):
        """Hyprland 에 이 창이 최대화됐는지 묻는다 → 단추 모양 · 다음 실행 때 최대화할지 (지금 초점인 창일 때만 —
        제목으로 이 창인지 확인한다)"""
        if not self.is_active():
            return
        try:
            proc = Gio.Subprocess.new(["hyprctl", "-j", "activewindow"],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            return

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
                import json
                j = json.loads(out or "{}")
            except (GLib.Error, ValueError):
                return
            if j.get("class") != self.app.get_application_id() or j.get("title") != self.get_title():
                return
            self._show_max(j.get("fullscreen") == 1)
        proc.communicate_utf8_async(None, None, done)

    def _show_max(self, maxed):
        self.app.state["maximized"] = maxed
        self.b_max.get_child().set_kind("restore" if maxed else "max")
        self.b_max.set_tooltip_text("이전 크기로 복원" if maxed else "최대화")

    def _minimize(self):
        """SekaiOS 의 최소화는 숨김 작업 공간으로 옮기기 (작업 표시줄이 다시 꺼낸다) — hyprbars 의 최소화 단추와
        같은 명령. GTK 의 최소화(iconify)는 Hyprland 가 받지 않는다. 기본 화면 모드(X11)는 xfwm4 가 한다"""
        if HYPR:
            try:
                subprocess.Popen(["hyprctl", "dispatch", "movetoworkspacesilent", "special:min"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                pass
        self.iconify()

    def _build_tabbar(self):
        bar = Gtk.Box(spacing=2)
        bar.get_style_context().add_class("fx-tabbar")
        self.tabbox = Gtk.Box(spacing=2)
        bar.pack_start(self.tabbox, False, False, 0)
        plus = self._tbtn(["list-add-symbolic"], "새 탭 (Ctrl+T)", lambda: self.new_tab())
        plus.get_style_context().add_class("fx-tab-new")
        plus.set_valign(Gtk.Align.CENTER)
        bar.pack_start(plus, False, False, 0)
        self.tabbox.pack_start(self._tab_widget(self.tab), False, False, 0)
        return bar

    def _tab_widget(self, t):
        eb = Gtk.EventBox()
        box = Gtk.Box(spacing=8)
        box.get_style_context().add_class("fx-tab")
        img = Gtk.Image()
        lbl = Gtk.Label(xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_width_chars(3)                  # 좁으면 탭도 줄어든다 (이름은 … 로)
        lbl.set_max_width_chars(22)
        close = Gtk.Button()
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_focus_on_click(False)
        close.get_style_context().add_class("fx-tab-close")
        close.add(image(["window-close-symbolic"], 12))
        close.set_tooltip_text("탭 닫기 (Ctrl+W)")
        close.connect("clicked", lambda *_: self.close_tab(t))
        box.pack_start(img, False, False, 0)
        box.pack_start(lbl, True, True, 0)
        box.pack_end(close, False, False, 0)
        eb.add(box)
        eb.connect("button-press-event", lambda _w, ev: self._tab_press(t, ev))
        t.button, t.box, t.icon, t.label = eb, box, img, lbl
        eb.show_all()
        return eb

    def _tab_press(self, t, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if ev.button == 1:
            self.switch_tab(t)
        elif ev.button == 2:
            self.close_tab(t)                   # 가운데 단추 — 닫기 (윈도우·브라우저처럼)
        return True

    def _update_tab_button(self, t):
        if t.label is None:
            return
        uri = t.uri or HOME
        title = "검색 결과" if t.searching else title_of(uri, self.mounts)
        t.label.set_text(title)
        names = ["system-search", "edit-find"] if t.searching else location_icon(uri)
        t.icon.set_from_pixbuf(icons().get(Gio.ThemedIcon.new_from_names([theme_icon(names)]), 16))
        p = local_path(uri)
        t.button.set_tooltip_text(p or title)
        ctx = t.box.get_style_context()
        if t is self.tab:
            ctx.add_class("active")
        else:
            ctx.remove_class("active")

    def new_tab(self, uri=None, activate=True, after_current=False):
        """새 탭 — activate 가 아니면 뒤에서 연다 (가운데 단추·"새 탭에서 열기")"""
        t = Tab()
        t.view_mode = self._saved_mode or self.view_mode    # 지금 탭의 보기 모양을 물려받는다
        t.folder = self._new_model("folder")
        t.model = t.folder
        i = self.tabs.index(self.tab) + 1 if after_current else len(self.tabs)
        self.tabs.insert(i, t)
        w = self._tab_widget(t)
        self.tabbox.pack_start(w, False, False, 0)
        self.tabbox.reorder_child(w, i)
        # 위치·기록·폴더 읽기를 먼저 — 그다음에 보이면(switch_tab) 창이 이 탭의 것을 되살린다
        uri = norm_uri(uri) or HOME
        t.uri, t.history, t.hpos = uri, [uri], 0
        if uri not in (HOME, COMPUTER):
            t.folder.kind = "trash" if is_in_trash(uri) else "folder"
            t.folder.sort = self._sort_for(t.folder.kind)
            if t.folder.kind == "trash":
                t.saved_mode, t.view_mode = t.view_mode, "details"
            t.folder.load(Gio.File.new_for_uri(uri))
        self._update_tab_button(t)
        if activate:
            self.switch_tab(t)
        return t

    def open_new_tab(self, uri):
        """다른 곳을 새 탭에서 (뒤에서 — 지금 보던 것은 그대로)"""
        if uri:
            self.new_tab(uri, activate=False, after_current=True)

    def switch_tab(self, t):
        if t is self.tab or t not in self.tabs:
            return
        cur = self.tab
        if self._page_name() == "folder":
            cur.sel = self.selected_uris()
            cur.scroll = self._active_view().scroll.get_vadjustment().get_value()
        else:
            cur.sel, cur.scroll = [], None
        self.tab = t
        self._restore_tab()

    def _restore_tab(self):
        """지금 탭(self.tab)의 위치·모델·검색·보기 모양을 창에 되살린다"""
        t = self.tab
        if self.thumbnailer is not None:
            try:
                self.thumbnailer.cancel_all()
            except Exception:
                pass
        self._typeahead = ""
        self.search.handler_block_by_func(self._on_search_changed)
        self.search.set_text(t.search_query if t.searching else "")
        self.search.handler_unblock_by_func(self._on_search_changed)
        uri = t.uri or HOME
        if t.searching:
            page = "folder"
            self.details.set_kind("search")
        elif uri == HOME:
            page = "home"
            self.home_page.refresh()
        elif uri == COMPUTER:
            page = "computer"
            self.computer_page.refresh()
        else:
            page = "folder"
            self.details.set_kind(t.folder.kind)
        self.stack.set_visible_child_name(page)
        if page == "folder":
            self.details.set_sort_indicator(t.model.sort, self.sort_desc)
            self.empty.hide()
            self._apply_view(list(t.sel))
            self._update_empty()
            self._set_busy(bool(t.model.loading))
            if t.scroll is not None:
                sc, val = self._active_view().scroll, t.scroll
                GLib.idle_add(lambda: (sc.get_vadjustment().set_value(val), False)[1])
        else:
            self._set_busy(False)
        self._sync_view_toggles()
        self._update_location()
        self._update_commands()
        self._update_status()
        for x in self.tabs:
            self._update_tab_button(x)
        GLib.idle_add(lambda: (self.focus_view(), False)[1])

    def close_tab(self, t=None):
        """마지막 탭이면 창을 닫는다"""
        t = t or self.tab
        if t not in self.tabs:
            return
        if len(self.tabs) == 1:
            self.close()
            return
        i = self.tabs.index(t)
        if t.searcher is not None:
            t.searcher.cancel()
        for m in (t.folder, t.search_model):
            if m is not None:
                m.stop()
        self.tabs.remove(t)
        if t.button is not None:
            t.button.destroy()
        if t is self.tab:
            self.tab = self.tabs[min(i, len(self.tabs) - 1)]
            self._restore_tab()

    def cycle_tab(self, step):
        i = (self.tabs.index(self.tab) + step) % len(self.tabs)
        self.switch_tab(self.tabs[i])

    # ════════════════════════════════════════════════════════
    #  틀 만들기
    # ════════════════════════════════════════════════════════
    def _tbtn(self, icons_, tooltip, cb, cls="fx-tool"):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.get_style_context().add_class(cls)
        b.add(image(icons_, 16))
        b.set_tooltip_text(tooltip)
        b.connect("clicked", lambda *_: cb())
        return b

    def _build_toolbar(self):
        bar = Gtk.Box(spacing=4)
        bar.get_style_context().add_class("fx-toolbar")
        self.b_back = self._tbtn(["go-previous-symbolic"], "뒤로 (Alt+왼쪽 화살표)", self.go_back)
        self.b_fwd = self._tbtn(["go-next-symbolic"], "앞으로 (Alt+오른쪽 화살표)", self.go_forward)
        self.b_up = self._tbtn(["go-up-symbolic"], "위로 (Alt+위쪽 화살표)", self.go_up)
        self.b_reload = self._tbtn(["view-refresh-symbolic"], "새로 고침 (F5)", self.refresh)
        for b in (self.b_back, self.b_fwd, self.b_up, self.b_reload):
            bar.pack_start(b, False, False, 0)
        self.address = AddressBar(self)
        addr_wrap = Gtk.Box()
        addr_wrap.get_style_context().add_class("fx-address-box")
        addr_wrap.pack_start(self.address, True, True, 0)
        bar.pack_start(addr_wrap, True, True, 6)
        self.search = Gtk.SearchEntry()
        self.search.get_style_context().add_class("fx-search")
        self.search.set_width_chars(8)          # 좁으면 여기까지 줄어든다
        self.search.set_max_width_chars(24)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("stop-search", lambda *_: self._stop_search_entry())
        self.search.connect("activate", lambda *_: self._on_search_changed(self.search))
        bar.pack_start(self.search, False, False, 0)
        return bar

    def _cbtn(self, icons_, label, tooltip, cb, arrow=False):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.get_style_context().add_class("fx-cmd")
        box = Gtk.Box(spacing=6)
        box.pack_start(image(icons_, 16), False, False, 0)
        if label:
            box.pack_start(Gtk.Label(label=label), False, False, 0)
        if arrow:
            box.pack_start(image(["pan-down-symbolic", "go-down-symbolic"], 10), False, False, 0)
        b.add(box)
        if tooltip:
            b.set_tooltip_text(tooltip)
        b.connect("clicked", lambda w: cb(w))
        return b

    def _vsep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("fx-cmd-sep")
        return s

    def _build_cmdbar(self):
        """단추 줄 — 창이 좁으면 다 들어가지 않는 단추는 숨기고 ⋯(더 보기) 메뉴로 옮긴다 (윈도우 11 처럼)"""
        outer = Gtk.Box(spacing=2)
        outer.get_style_context().add_class("fx-cmdbar")
        bar = Gtk.Box(spacing=2)
        self.c_new = self._cbtn(["list-add-symbolic", "document-new-symbolic"], "새로 만들기", None,
                                self._new_menu_popup, arrow=True)
        bar.pack_start(self.c_new, False, False, 0)
        bar.pack_start(self._vsep(), False, False, 4)
        self.c_cut = self._cbtn(["edit-cut-symbolic"], None, "잘라내기 (Ctrl+X)", lambda _w: self.cut())
        self.c_copy = self._cbtn(["edit-copy-symbolic"], None, "복사 (Ctrl+C)", lambda _w: self.copy())
        self.c_paste = self._cbtn(["edit-paste-symbolic"], None, "붙여넣기 (Ctrl+V)", lambda _w: self.paste())
        self.c_rename = self._cbtn(["document-edit-symbolic", "edit-symbolic", "text-editor-symbolic"], None,
                                   "이름 바꾸기 (F2)", lambda _w: self.rename_selected())
        self.c_delete = self._cbtn(["user-trash-symbolic", "edit-delete-symbolic"], None, "삭제 (Delete)",
                                   lambda _w: self.delete_selected())
        for b in (self.c_cut, self.c_copy, self.c_paste, self.c_rename, self.c_delete):
            bar.pack_start(b, False, False, 0)
        bar.pack_start(self._vsep(), False, False, 4)
        self.c_sort = self._cbtn(["view-sort-descending-symbolic", "view-sort-ascending-symbolic"], "정렬", None,
                                 self._sort_menu_popup, arrow=True)
        self.c_view = self._cbtn(VIEW_ICONS["details"], "보기", None, self._view_menu_popup, arrow=True)
        bar.pack_start(self.c_sort, False, False, 0)
        bar.pack_start(self.c_view, False, False, 0)
        self.ctx_sep = self._vsep()
        bar.pack_start(self.ctx_sep, False, False, 4)
        # 그 자리에서만 보이는 단추
        self.x_empty = self._cbtn(["user-trash-full-symbolic", "user-trash-symbolic"], "휴지통 비우기", None,
                                  lambda _w: self.empty_trash())
        self.x_restore = self._cbtn(["edit-undo-symbolic", "document-revert-symbolic"], "선택 항목 복원", None,
                                    lambda _w: self.restore_selected())
        self.x_eject = self._cbtn(["media-eject-symbolic"], "꺼내기", None, lambda _w: self._eject_selected())
        self.x_extract = self._cbtn(["package-x-generic-symbolic", "archive-extract-symbolic",
                                     "extract-archive-symbolic"], "모두 압축 풀기", None,
                                    lambda _w: self.extract_selected())
        self.x_wall = self._cbtn(["preferences-desktop-wallpaper-symbolic", "image-x-generic-symbolic"],
                                 "배경으로 설정", None, lambda _w: self.set_wallpaper_selected())
        self.ctx_buttons = [self.x_empty, self.x_restore, self.x_eject, self.x_extract, self.x_wall]
        for b in self.ctx_buttons:
            b.set_no_show_all(True)
            b.get_child().show_all()
            bar.pack_start(b, False, False, 0)
        self.c_more = self._cbtn(["view-more-horizontal-symbolic", "view-more-symbolic", "open-menu-symbolic"],
                                 None, "더 보기", self._more_menu_popup)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        sw.set_propagate_natural_width(True)
        sw.set_propagate_natural_height(True)
        sw.add(bar)
        vp = sw.get_child()
        if isinstance(vp, Gtk.Viewport):
            vp.set_shadow_type(Gtk.ShadowType.NONE)
        outer.pack_start(sw, True, True, 0)
        outer.pack_end(self.c_more, False, False, 0)
        self._cmd_bar, self._cmd_sw, self._cmd_fit_src = bar, sw, 0
        # 숨긴 단추 → ⋯ 메뉴의 항목 (이름, 할 일, 아이콘, 하위 메뉴를 만드는 함수)
        self._cmd_overflow = {
            self.c_new: ("새로 만들기", None, ["list-add-symbolic"], self._new_submenu),
            self.c_cut: ("잘라내기", self.cut, ["edit-cut-symbolic"], None),
            self.c_copy: ("복사", self.copy, ["edit-copy-symbolic"], None),
            self.c_paste: ("붙여넣기", self.paste, ["edit-paste-symbolic"], None),
            self.c_rename: ("이름 바꾸기", self.rename_selected, ["document-edit-symbolic", "edit-symbolic"], None),
            self.c_delete: ("삭제", self.delete_selected, ["user-trash-symbolic", "edit-delete-symbolic"], None),
            self.c_sort: ("정렬", None, ["view-sort-descending-symbolic"], self._sort_submenu),
            self.c_view: ("보기", None, VIEW_ICONS["details"], self._view_submenu),
            self.x_empty: ("휴지통 비우기", self.empty_trash, ["user-trash-full-symbolic"], None),
            self.x_restore: ("선택 항목 복원", self.restore_selected, ["edit-undo-symbolic"], None),
            self.x_eject: ("꺼내기", self._eject_selected, ["media-eject-symbolic"], None),
            self.x_extract: ("모두 압축 풀기", self.extract_selected, ["package-x-generic-symbolic"], None),
            self.x_wall: ("배경으로 설정", self.set_wallpaper_selected,
                          ["preferences-desktop-wallpaper-symbolic", "image-x-generic-symbolic"], None),
        }
        for w in (sw, bar):
            w.connect("size-allocate", lambda *_: self._cmd_fit_later())
        return outer

    def _cmd_fit_later(self):
        if not self._cmd_fit_src:
            self._cmd_fit_src = GLib.idle_add(self._cmd_fit)

    def _cmd_fit(self):
        """보이는 폭 밖으로 (조금이라도) 나가는 단추는 감춘다 — 자리는 그대로 두고 그리지만 않는다
        (set_child_visible: 크기 계산이 바뀌지 않아 다시 배치가 돌지 않는다). 구분선은 바로 뒤 단추를 따른다"""
        self._cmd_fit_src = 0
        width = self._cmd_sw.get_allocated_width()
        x0 = self._cmd_bar.get_allocation().x
        kids = [k for k in self._cmd_bar.get_children() if k.get_visible()]
        fits = []
        for k in kids:
            a = k.get_allocation()
            fits.append(a.x - x0 + a.width <= width)
        for i, k in enumerate(kids):
            ok = fits[i]
            if isinstance(k, Gtk.Separator):
                # 뒤에 보이는 단추가 있어야 구분선도 보인다
                ok = any(fits[j] for j in range(i + 1, len(kids)) if not isinstance(kids[j], Gtk.Separator))
            if k.get_child_visible() != ok:
                k.set_child_visible(ok)
        return False

    def _cmd_hidden(self):
        """좁아서 감춘 단추들 (단추 줄 순서)"""
        return [k for k in self._cmd_bar.get_children()
                if k.get_visible() and not k.get_child_visible() and k in self._cmd_overflow]

    def _build_statusbar(self):
        bar = Gtk.Box(spacing=16)
        bar.get_style_context().add_class("fx-status")
        self.st_count = Gtk.Label(xalign=0)
        self.st_sel = Gtk.Label(xalign=0)
        self.st_sel.set_ellipsize(Pango.EllipsizeMode.END)
        bar.pack_start(self.st_count, False, False, 0)
        bar.pack_start(self.st_sel, True, True, 0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        bar.pack_end(self.spinner, False, False, 0)
        vb = Gtk.Box(spacing=0)
        self.s_details = Gtk.ToggleButton()
        self.s_large = Gtk.ToggleButton()
        for b, mode, tip in ((self.s_details, "details", "자세히 (Ctrl+Shift+6)"),
                             (self.s_large, "large", "큰 아이콘 (Ctrl+Shift+2)")):
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_focus_on_click(False)
            b.get_style_context().add_class("fx-status-btn")
            b.add(image(VIEW_ICONS[mode], 14))
            b.set_tooltip_text(tip)
            b.connect("toggled", self._status_view_toggled, mode)
            vb.pack_start(b, False, False, 0)
        bar.pack_end(vb, False, False, 0)
        return bar

    # ════════════════════════════════════════════════════════
    #  이동
    # ════════════════════════════════════════════════════════
    @property
    def mounts(self):
        return mount_names(self.app.vm)

    def navigate(self, uri, select=None, push=True, rename=False):
        uri = norm_uri(uri)
        if not uri:
            return
        if self.searching:
            self._end_search(show=False)
        if push:
            if self.hpos >= 0 and self.history[self.hpos] == uri:
                push = False
            else:
                del self.history[self.hpos + 1:]
                self.history.append(uri)
                if len(self.history) > 100:
                    del self.history[0]
                self.hpos = len(self.history) - 1
        if self.thumbnailer is not None:
            try:
                self.thumbnailer.cancel_all()
            except Exception:
                pass
        self.uri = uri
        self._typeahead = ""
        self._want_select = (set(select), rename) if select else None
        self._force_details(is_in_trash(uri))  # 휴지통은 윈도우처럼 자세히 (원래 위치 · 삭제한 날짜)
        if uri == HOME:
            self.folder.stop()
            self.home_page.refresh()
            self.stack.set_visible_child_name("home")
            self.home_page.unselect_all()
        elif uri == COMPUTER:
            self.folder.stop()
            self.computer_page.refresh()
            self.stack.set_visible_child_name("computer")
            self.computer_page.unselect_all()
        else:
            kind = "trash" if is_in_trash(uri) else "folder"
            self.folder.kind = kind
            self.details.set_kind(kind)
            self.folder.sort = self._sort_for(kind)
            self.folder.desc = self.sort_desc
            self.details.set_sort_indicator(self.folder.sort, self.sort_desc)
            self.model = self.folder
            self.empty.hide()
            self._apply_view()
            self.stack.set_visible_child_name("folder")
            self.folder.load(Gio.File.new_for_uri(uri))
            self._set_busy(True)
        self._update_location()
        self._update_commands()
        self._update_status()
        GLib.idle_add(lambda: (self.focus_view(), False)[1])

    def _force_details(self, on):
        """검색 결과 · 휴지통은 자세히로 보인다 — 나오면 원래 보기 모양으로 (저장하지 않는다)"""
        if on and self._saved_mode is None:
            self._saved_mode = self.view_mode
            self.view_mode = "details"
        elif not on and self._saved_mode is not None:
            self.view_mode = self._saved_mode
            self._saved_mode = None

    def _sort_for(self, kind):
        f = self.sort_field
        if f in ("orig", "dtime") and kind != "trash":
            return "name"
        if f == "loc" and kind != "search":
            return "name"
        return f

    def _update_location(self):
        uri = self.uri
        mounts = self.mounts
        title = title_of(uri, mounts)
        if self.searching:
            self._set_win_title("검색 결과")
            self.address.set_location(uri, [(f"검색 결과 ({title})", uri)], ["system-search", "edit-find"])
        else:
            self._set_win_title(title)
            self.address.set_location(uri, crumbs(uri, mounts), location_icon(uri))
        self.search.set_placeholder_text(f"{title} 검색")
        self.nav.select_uri(uri)
        self.b_back.set_sensitive(self.hpos > 0)
        self.b_fwd.set_sensitive(self.hpos < len(self.history) - 1)
        self.b_up.set_sensitive(self._parent_uri() is not None)
        self._update_tab_button(self.tab)

    def _parent_uri(self):
        u = self.uri
        if u in (HOME, None):
            return None
        if u == COMPUTER:
            return None
        if u == TRASH:
            return None
        p = local_path(u)
        if p is not None:
            p = os.path.normpath(p)
            if p == "/" or p in self.mounts:
                return COMPUTER                 # 드라이브의 맨 위 → 내 PC (윈도우처럼)
            return uri_of_path(os.path.dirname(p))
        f = Gio.File.new_for_uri(u).get_parent()
        return f.get_uri() if f is not None else COMPUTER

    def go_back(self):
        if self.searching:
            self.search.set_text("")
            return
        if self.hpos > 0:
            self.hpos -= 1
            cur = self.uri
            self.navigate(self.history[self.hpos], push=False, select=[cur] if cur else None)

    def go_forward(self):
        if self.hpos < len(self.history) - 1:
            self.hpos += 1
            self.navigate(self.history[self.hpos], push=False)

    def go_up(self):
        if self.searching:
            self.search.set_text("")
            return
        p = self._parent_uri()
        if p:
            cur = self.uri
            self.navigate(p, select=[cur])      # 윈도우처럼 방금 있던 폴더를 골라 둔다

    def refresh(self):
        if self.searching:
            self._start_search(self.search_query)
            return
        keep = self.selected_uris()
        if self.uri == HOME:
            self.home_page.refresh()
        elif self.uri == COMPUTER:
            self.computer_page.refresh()
        else:
            self.navigate(self.uri, select=keep or None, push=False)

    def open_new_window(self, uri=None, select=None):
        self.app.open_window(uri or self.uri, select=select)

    def address_entered(self, text):
        uri = parse_location(text, None if is_special(self.uri or HOME) else self.uri)
        if uri is None:
            self.address.cancel_edit()
            return
        if is_special(uri) or uri == TRASH:
            self.address.cancel_edit()
            self.navigate(uri)
            return
        f = Gio.File.new_for_uri(uri)

        def got(src, res):
            try:
                info = src.query_info_finish(res)
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED):
                    self._mount_and_go(src)
                    return
                opener.error(self, f"‘{text.strip()}’을(를) 찾을 수 없습니다.",
                             "이름을 올바르게 입력했는지 확인한 후 다시 시도하세요.")
                return
            self.address.cancel_edit()
            if info.get_file_type() in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE):
                self.navigate(src.get_uri())
            else:
                # 파일 — 윈도우처럼 그 파일을 연다
                from .common import Entry
                parent = src.get_parent()
                self.open_entries([Entry.from_info(parent, info, gfile=src)])
        f.query_info_async("standard::*,unix::mode,time::modified", Gio.FileQueryInfoFlags.NONE,
                           GLib.PRIORITY_DEFAULT, None, got)

    def _mount_and_go(self, f):
        op = Gtk.MountOperation(parent=self)

        def done(src, res):
            try:
                src.mount_enclosing_volume_finish(res)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    opener.error(self, "위치에 연결하지 못했습니다.", e.message)
                return
            self.address.cancel_edit()
            self.navigate(src.get_uri())
        f.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, None, done)

    def focus_view(self):
        if self.address.editing() or self.search.has_focus():
            return
        page = self.stack.get_visible_child_name()
        if page == "home":
            self.home_page.focus()
        elif page == "computer":
            self.computer_page.focus()
        else:
            self._active_view().view.grab_focus()

    def top_menu_items(self):
        out = [("홈", HOME, ["user-home"])]
        out += [(label, uri_of_path(p), icons_) for p, label, icons_ in favorites()]
        out += [("내 PC", COMPUTER, ["computer"]), ("휴지통", TRASH, ["user-trash"])]
        return out

    def drives_menu_items(self):
        from .common import list_drives
        return [(d.name, d.root_uri, ["drive-harddisk", "drive-removable-media"])
                for d in list_drives(self.app.vm) if d.root_uri]

    # ════════════════════════════════════════════════════════
    #  보기
    # ════════════════════════════════════════════════════════
    def _active_view(self):
        return self.details if self._effective_mode() == "details" else self.icons

    def _effective_mode(self):
        return self.view_mode

    def _apply_view(self, keep=None):
        """지금 모드의 보기에 지금 모델을 붙인다 (keep: 다시 고를 URI — 보기를 바꾸기 전에 읽어 둔 것)"""
        keep = keep or []
        mode = self._effective_mode()
        store = self.model.store
        # 보일 보기를 먼저 바꾸고 나서 숨은 보기의 모델을 뗀다 — IconView 는 고른 항목을 하나씩
        #   다시 그리게 해서 10,000개를 고른 채 보이는 동안 떼면 1초 가까이 멈췄다
        if mode == "details":
            self.details.set_kind(self.model.kind)
            self.details.set_model(store)
            self.view_stack.set_visible_child_name("details")
            self.icons.set_model(None)
        else:
            self.icons.set_mode(mode)
            self.icons.set_model(self.model.store)
            self.view_stack.set_visible_child_name("icons")
            self.details.set_model(None)
        if keep:
            self._select_uris(keep, scroll=True)
        self._sync_view_toggles()
        self._schedule_thumbs()

    def set_view_mode(self, mode):
        if mode not in VIEW_MODES:
            return
        keep = self.selected_uris() if self._page_name() == "folder" else []
        self.view_mode = mode
        if self._saved_mode is not None:
            self._saved_mode = mode             # 검색·휴지통에서 바꾼 모양은 나와도 남는다
        self.app.state["view"] = mode
        self.app.save_state()
        if self._page_name() == "folder":
            self._apply_view(keep)
        self._sync_view_toggles()

    def _sync_view_toggles(self):
        mode = self.view_mode
        for b, m in ((self.s_details, "details"), (self.s_large, "large")):
            b.handler_block_by_func(self._status_view_toggled)
            b.set_active(mode == m or (m == "large" and mode == "medium"))
            b.handler_unblock_by_func(self._status_view_toggled)

    def _status_view_toggled(self, b, mode):
        if b.get_active():
            self.set_view_mode(mode)
        else:
            self._sync_view_toggles()

    def _on_scroll_zoom(self, _w, ev):
        """Ctrl+휠 — 자세히 ↔ 보통 아이콘 ↔ 큰 아이콘 (윈도우처럼)"""
        if not ev.state & CTRL:
            return False
        order = ["details", "medium", "large"]
        i = order.index(self.view_mode)
        d = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            d = 1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            d = -1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = ev.get_scroll_deltas()
            d = -1 if dy > 0 else (1 if dy < 0 else 0)
        j = max(0, min(len(order) - 1, i + d))
        if j != i:
            self.set_view_mode(order[j])
        return True

    def set_sort(self, field, desc):
        self.sort_field, self.sort_desc = field, bool(desc)
        self.app.state["sort"] = [field, bool(desc)]
        self.app.save_state()
        for t in self.tabs:                     # 정렬은 창 전체 — 모든 탭에
            t.folder.set_sort(self._sort_for(t.folder.kind), self.sort_desc)
            if t.search_model is not None:
                t.search_model.set_sort(self._sort_for("search"), self.sort_desc)
        self.details.set_sort_indicator(self.model.sort, self.sort_desc)
        self._schedule_thumbs()

    def header_clicked(self, field):
        if field == self.model.sort:
            self.set_sort(field, not self.sort_desc)
        else:
            self.set_sort(field, field in NUMERIC_SORTS)

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.app.state["hidden"] = self.show_hidden
        self.app.save_state()
        for t in self.tabs:
            t.folder.set_show_hidden(self.show_hidden)
        if self.search_model is not None and self.searching:
            self._start_search(self.search_query)
        self._update_status()
        self.toast("숨긴 항목을 표시합니다" if self.show_hidden else "숨긴 항목을 숨깁니다", 2)

    # ════════════════════════════════════════════════════════
    #  모델 알림
    # ════════════════════════════════════════════════════════
    def _model_event(self, m, ev, val):
        if m is not self.model:
            return
        if ev == "store":
            self._attach_store(val)
        elif ev == "added":
            self._try_select()
            self._schedule_thumbs()
            self._update_empty()
        elif ev == "changed":
            self._update_status()
            self._update_empty()
            self._schedule_thumbs()
        elif ev == "loaded":
            self._set_busy(False)
            self._try_select(final=True)
            self._update_status()
            self._update_empty()
            self._update_commands()
        elif ev == "error":
            self._set_busy(False)
            self._show_empty(val)
            self._update_commands()
        elif ev == "gone":
            self._folder_gone()

    def _attach_store(self, store):
        v = self._active_view()
        v.set_model(store)
        other = self.icons if v is self.details else self.details
        other.set_model(None)
        self._update_status()

    def _folder_gone(self):
        """보던 폴더가 지워지거나 옮겨짐 — 남아 있는 가장 가까운 윗폴더로"""
        u = self.uri
        p = local_path(u)
        if p:
            d = os.path.dirname(os.path.normpath(p))
            while d and d != "/" and not os.path.isdir(d):
                d = os.path.dirname(d)
            self.navigate(uri_of_path(d or "/"), push=False)
        else:
            self.navigate(COMPUTER, push=False)

    def _update_empty(self):
        if self.stack.get_visible_child_name() != "folder":
            return
        m = self.model
        if m.error:
            self._show_empty(m.error)
        elif m.count() == 0 and not m.loading:
            if self.searching:
                self._show_empty("검색 조건과 일치하는 항목이 없습니다.")
            elif m.kind == "trash":
                self._show_empty("휴지통이 비어 있습니다.")
            else:
                self._show_empty("이 폴더는 비어 있습니다.")
        else:
            self.empty.hide()

    def _show_empty(self, text):
        self.empty.set_text(text)
        self.empty.show()

    def _set_busy(self, on):
        self.spinner.set_visible(on)
        if on:
            self.spinner.start()
        else:
            self.spinner.stop()

    # ════════════════════════════════════════════════════════
    #  고르기
    # ════════════════════════════════════════════════════════
    def _page_name(self):
        return self.stack.get_visible_child_name()

    def selected_entries(self):
        if self._page_name() != "folder":
            if self._page_name() == "home":
                return list(self.home_page.selected_entries())
            return []
        m = self.model
        out = []
        for pos in self._active_view().selected_positions():
            e = m.entry_at(pos)
            if e is not None:
                out.append(e)
        return out

    def selected_uris(self):
        return [e.uri for e in self.selected_entries()]

    def selected_gfiles(self):
        page = self._page_name()
        if page == "home":
            return self.home_page.selected_gfiles()
        if page == "computer":
            return self.computer_page.selected_gfiles()
        return [e.gfile for e in self.selected_entries()]

    def _select_uris(self, uris, scroll=True):
        m = self.model
        pos = sorted(p for p in (m.pos_of(u) for u in uris) if p is not None)
        if pos:
            self._active_view().select_positions(pos, cursor=pos[0], scroll=scroll)
        return pos

    def _try_select(self, final=False):
        """열자마자 고를 것(--select · 위로 · 새 폴더 · 붙여넣은 것)이 목록에 나타나면 고른다"""
        if not self._want_select or self._page_name() != "folder":
            return
        want, rename = self._want_select
        m = self.model
        found = [u for u in want if m.pos_of(u) is not None]
        if not found and not final:
            return
        if len(found) < len(want) and not final and m.loading:
            return
        self._want_select = None if (final or len(found) == len(want)) else (want - set(found), rename)
        if found:
            pos = self._select_uris(found)
            self._active_view().view.grab_focus()
            if rename and len(found) == 1 and pos:
                GLib.idle_add(lambda: (self.rename_selected(), False)[1])

    def view_selection_changed(self):
        if self._sel_src:
            return
        self._sel_src = GLib.idle_add(self._selection_idle)

    def _selection_idle(self):
        self._sel_src = 0
        self._update_status()
        self._update_commands()
        return False

    def page_selection_changed(self):
        self.view_selection_changed()

    def select_all(self):
        page = self._page_name()
        if page == "folder":
            self._active_view().select_all()
        elif page == "home":
            self.home_page.select_all()

    def unselect_all(self):
        page = self._page_name()
        if page == "folder":
            self._active_view().unselect_all()
        elif page == "home":
            self.home_page.unselect_all()
        else:
            self.computer_page.unselect_all()

    def invert_selection(self):
        if self._page_name() != "folder":
            return
        v = self._active_view()
        cur = set(v.selected_positions())
        rest = [i for i in range(self.model.count()) if i not in cur]
        v.select_positions(rest, scroll=False)

    # ════════════════════════════════════════════════════════
    #  상태 표시줄 · 명령 단추
    # ════════════════════════════════════════════════════════
    def _update_status(self):
        page = self._page_name()
        if page == "home":
            n = self.home_page.count()
            k = len(self.home_page.selected_gfiles())
            self.st_count.set_text(count_text(n))
            self.st_sel.set_text(f"{k:,}개 항목 선택함" if k else "")
            return
        if page == "computer":
            n = self.computer_page.count()
            k = len(self.computer_page.selected_drives())
            self.st_count.set_text(count_text(n))
            self.st_sel.set_text(f"{k:,}개 항목 선택함" if k else "")
            return
        m = self.model
        self.st_count.set_text(count_text(m.count()))
        sel = self.selected_entries()
        if not sel:
            self.st_sel.set_text("")
            return
        text = f"{len(sel):,}개 항목 선택함"
        if all(not e.is_dir for e in sel):
            text += f" {fmt_size(sum(e.size for e in sel))}"
        self.st_sel.set_text(text)

    def _update_commands(self):
        page = self._page_name()
        uri = self.uri or HOME
        in_trash = is_in_trash(uri) and not self.searching
        folder = page == "folder"
        sel = self.selected_entries() if folder else []
        n = len(sel)
        writable = folder and not in_trash and not self.searching and fileops is not None
        have_files = bool(self.selected_gfiles())
        ops = fileops is not None
        self.c_new.set_sensitive(writable)
        self.c_cut.set_sensitive(ops and folder and n > 0)
        self.c_copy.set_sensitive(ops and have_files and page != "computer")
        self.c_paste.set_sensitive(writable and self._can_paste)
        self.c_rename.set_sensitive(ops and folder and n == 1 and not in_trash and sel[0].can_rename)
        self.c_delete.set_sensitive(ops and folder and n > 0)
        self.c_sort.set_sensitive(folder)
        self.c_view.set_sensitive(folder)
        # 그 자리에서만
        show = set()
        if in_trash and uri == TRASH:
            show.add(self.x_empty)
            self.x_empty.set_sensitive(ops and self.model.count() > 0)
            show.add(self.x_restore)
            self.x_restore.set_sensitive(ops and n > 0)
        if page == "computer":
            ds = self.computer_page.selected_drives()
            if ds and ds[0].removable and (ds[0].can_eject or ds[0].can_unmount):
                show.add(self.x_eject)
        if folder and n == 1 and not in_trash:
            e = sel[0]
            if archive is not None and not e.is_dir and self._is_archive(e):
                show.add(self.x_extract)
            if not e.is_dir and (e.ctype or "").startswith("image/") and e.path:
                show.add(self.x_wall)
        for b in self.ctx_buttons:
            b.set_visible(b in show)
        self.ctx_sep.set_visible(bool(show))

    def _is_archive(self, e):
        try:
            return bool(archive.is_archive(e.path or e.uri, e.ctype))
        except Exception:
            return False

    # ════════════════════════════════════════════════════════
    #  열기
    # ════════════════════════════════════════════════════════
    def view_activated(self):
        self.open_selected()

    def open_selected(self, new_window=False, new_tab=False):
        page = self._page_name()
        if new_tab:
            if page == "home":
                for t in self.home_page.selected_tiles():
                    self.open_new_tab(t.uri)
            elif page == "computer":
                for d in self.computer_page.selected_drives()[:1]:
                    self.open_new_tab(d.root_uri)
            else:
                for e in self.selected_entries():
                    if e.is_dir:
                        self.open_new_tab(e.target or e.uri)
            return
        if page == "home":
            tiles = self.home_page.selected_tiles()
            if tiles:
                if new_window:
                    for t in tiles:
                        self.open_new_window(t.uri)
                else:
                    self.navigate(tiles[0].uri)
                return
            self.open_entries(self.home_page.selected_entries())
            return
        if page == "computer":
            for d in self.computer_page.selected_drives()[:1]:
                if new_window and d.root_uri:
                    self.open_new_window(d.root_uri)
                else:
                    self.open_drive(d)
            return
        self.open_entries(self.selected_entries(), new_window=new_window)

    def open_entries(self, entries, new_window=False):
        if not entries:
            return
        if is_in_trash(self.uri or "") and not self.searching and self._page_name() == "folder":
            self.show_properties([e.gfile for e in entries])   # 휴지통 — 윈도우처럼 속성
            return
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        for i, e in enumerate(dirs):
            target = e.target or e.uri
            if i == 0 and not new_window:
                self.navigate(target)
            else:
                self.open_new_window(target)
        if not files:
            return
        desktop, execs, arcs, rest = [], [], [], []
        for e in files:
            p = e.path
            if p and (p.lower().endswith(".desktop") or Gio.content_type_is_a(e.ctype or "", "application/x-desktop")):
                desktop.append(p)
                continue
            if p:
                k = opener.exec_kind(e)
                if k:
                    execs.append((e, k))
                    continue
            if archive is not None and self._is_archive(e):
                arcs.append(e)
                continue
            rest.append(e)
        if desktop:
            opener.open_desktop_files(self, desktop)
        for e, k in execs[:1]:                  # 여러 개를 한꺼번에 실행하지 않는다 — 하나씩 묻는다
            opener.open_executable(self, e, k, lambda e=e: opener.open_as_text(self, e))
        if arcs:
            self.extract([e.gfile for e in arcs])
        if rest:
            opener.open_with_default(self, rest)

    def open_drive(self, d):
        if d.root_uri:
            self.navigate(d.root_uri)
            return
        if d.volume is None:
            return
        op = Gtk.MountOperation(parent=self)
        self.toast(f"‘{d.name}’에 연결하는 중…", 3)

        def done(vol, res):
            try:
                vol.mount_finish(res)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    opener.error(self, f"‘{d.name}’에 연결하지 못했습니다.", e.message)
                return
            m = vol.get_mount()
            if m is not None:
                self.navigate(m.get_root().get_uri())
        d.volume.mount(Gio.MountMountFlags.NONE, op, None, done)

    def eject_drive(self, d):
        op = Gtk.MountOperation(parent=self)
        name = d.name
        root = d.root_uri

        def done(obj, res, how):
            try:
                getattr(obj, how + "_finish")(res)
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    return
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.BUSY):
                    opener.error(self, f"‘{name}’을(를) 꺼낼 수 없습니다.",
                                 "장치를 사용 중입니다. 열려 있는 파일이나 프로그램을 닫고 다시 시도하세요.")
                else:
                    opener.error(self, f"‘{name}’을(를) 꺼낼 수 없습니다.", e.message)
                return
            self.toast(f"이제 ‘{name}’을(를) 안전하게 제거할 수 있습니다.")
            if root and self.uri and self.uri.startswith(root.rstrip("/")):
                self.navigate(COMPUTER)
        # 지금 보는 폴더가 그 안이면 먼저 나온다 (열어 둔 감시 때문에 '사용 중'이 되지 않게)
        if root and self.uri and self.uri.startswith(root.rstrip("/")) and self._page_name() == "folder":
            self.navigate(COMPUTER)
        m, v = d.mount, d.volume
        if m is not None and m.can_eject():
            m.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, "eject_with_operation")
        elif v is not None and v.can_eject():
            v.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, "eject_with_operation")
        elif m is not None and m.can_unmount():
            m.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, "unmount_with_operation")

    def _eject_selected(self):
        for d in self.computer_page.selected_drives()[:1]:
            self.eject_drive(d)

    def _drives_changed(self):
        if self._page_name() == "computer":
            GLib.timeout_add(150, lambda: (self.computer_page.refresh(), False)[1])

    # ════════════════════════════════════════════════════════
    #  파일 작업
    # ════════════════════════════════════════════════════════
    def _need_ops(self):
        if fileops is None:
            self.toast("파일 작업 모듈을 불러오지 못해 이 기능을 쓸 수 없습니다.")
            return False
        return True

    def _dest_dir(self):
        if self.searching or self._page_name() != "folder" or is_in_trash(self.uri or ""):
            return None
        return gfile_of(self.uri)

    def _done_select(self, dest):
        """작업이 끝나면 — 지금 폴더에 생긴 것이면 골라 둔다"""
        def done(ok, results=None):
            if not results or self.searching or dest is None or self.uri != dest.get_uri():
                return
            self._want_select = ({f.get_uri() for f in results}, False)
            self._try_select()
        return done

    def cut(self):
        self._to_clipboard(True)

    def copy(self):
        self._to_clipboard(False)

    def _to_clipboard(self, cut):
        if not self._need_ops():
            return
        files = self.selected_gfiles()
        if not files:
            return
        fileops.clipboard_set(files, cut)
        self.app.clipboard_changed()            # 다른 창도 붙여넣기 단추 · 흐린 항목을 다시 본다
        self.toast(f"{len(files):,}개 항목을 {'잘라냈습니다' if cut else '복사했습니다'}", 2)

    def _check_clipboard(self):
        """붙여넣을 파일이 있는지 (클립보드 형식만 비동기로 본다) · 잘라낸 항목은 흐리게"""
        def got(_cb, atoms, *_a):
            names = set()
            for a in atoms or []:
                try:
                    names.add(a.name())
                except Exception:
                    pass
            self._can_paste = bool(names & set(COPIED_TARGETS))
            self._update_commands()
        try:
            self._clip.request_targets(got)
        except Exception:
            pass
        # 잘라낸 항목은 흐리게 — 이 프로세스가 둔 클립보드만 안다 (다른 앱이 가져가면 풀린다)
        owned = None
        if fileops is not None:
            try:
                owned = fileops.clipboard_owned()
            except Exception as e:
                dbg("클립보드를 읽지 못함", e)
        files, cut = owned if owned else ([], False)
        new = {f.get_uri() for f in files} if cut else set()
        self._cut_uris = new
        for t in self.tabs:
            for m in (t.folder, t.search_model):
                if m is not None:
                    m.set_dimmed(new)

    def paste(self, into=None):
        if not self._need_ops():
            return
        dest = into or self._dest_dir()
        if dest is None:
            return

        def got(files, cut):
            if not files:
                return
            self._transfer(files, dest, move=cut)
            if cut:
                GLib.timeout_add(300, lambda: (self.app.clipboard_changed(), False)[1])
        fileops.clipboard_get(got)

    def _transfer(self, files, dest, move):
        for f in files:
            if dest.equal(f) or dest.has_prefix(f):
                opener.error(self, "작업을 완료할 수 없습니다.", "대상 폴더가 원본 폴더의 하위 폴더입니다.")
                return False
        if move and all((f.get_parent() is not None and f.get_parent().equal(dest)) for f in files):
            return False                       # 제자리로 옮기기
        done = self.app.track(self._done_select(dest))
        if move:
            fileops.move(files, dest, self, done)
        else:
            fileops.copy(files, dest, self, done)
        return True

    def delete_selected(self, permanent=False):
        if not self._need_ops():
            return
        if self._page_name() != "folder":
            return
        sel = self.selected_entries()
        if not sel:
            return
        files = [e.gfile for e in sel]
        v = self._active_view()
        cur = v.selected_positions()
        first = cur[0] if cur else 0

        def done(ok, results=None):
            # 지운 자리 근처의 항목을 골라 둔다 (방향키로 이어서 지울 수 있게)
            if self._page_name() != "folder":
                return
            GLib.timeout_add(350, self._select_near, first)
        done = self.app.track(done)
        if permanent or (is_in_trash(self.uri or "") and not self.searching):
            fileops.delete_permanently(files, self, done)
        else:
            fileops.trash(files, self, done)

    def _select_near(self, pos):
        n = self.model.count()
        if n and not self._active_view().selected_positions():
            p = min(pos, n - 1)
            self._active_view().select_positions([p], cursor=p)
        return False

    def rename_selected(self):
        if not self._need_ops() or self._page_name() != "folder":
            return
        v = self._active_view()
        pos = v.selected_positions()
        if len(pos) != 1:
            return
        e = self.model.entry_at(pos[0])
        if e is None or is_in_trash(e.uri):
            return
        name = e.edit
        v.start_rename(pos[0], name, split_ext(name, e.is_dir), lambda t: self._finish_rename(e, t))

    def _finish_rename(self, e, text):
        if text is None:
            self.focus_view()
            return
        new = text.strip()
        if not new or new == e.edit:
            self.focus_view()
            return
        bad = fileops.validate_name(new) if hasattr(fileops, "validate_name") else None
        if bad:
            opener.error(self, "이름을 바꿀 수 없습니다.", bad)
            GLib.idle_add(lambda: (self._rename_again(e.uri), False)[1])
            return
        old_ext = os.path.splitext(e.edit)[1] if not e.is_dir else ""
        new_ext = os.path.splitext(new)[1] if not e.is_dir else ""

        def do():
            gf, err = fileops.rename(e.gfile, new)
            if err or gf is None:
                opener.error(self, f"‘{e.name}’의 이름을 바꿀 수 없습니다.", err or "")
                GLib.idle_add(lambda: (self._rename_again(e.uri), False)[1])
                return
            self._want_select = ({gf.get_uri()}, False)
            self._insert_now(gf)
            self.focus_view()
        if old_ext.lower() != new_ext.lower() and not e.is_dir and old_ext:
            opener.ask(self, "이름 바꾸기",
                       "확장명을 변경하면 파일을 사용할 수 없게 될 수도 있습니다.\n변경하시겠습니까?",
                       [("아니요", Gtk.ResponseType.NO), ("예", Gtk.ResponseType.YES)],
                       lambda r: do() if r == Gtk.ResponseType.YES else self.focus_view(),
                       kind=Gtk.MessageType.WARNING, default=Gtk.ResponseType.NO)
            return
        do()

    def _rename_again(self, uri):
        if self._select_uris([uri]):
            self.rename_selected()

    def _insert_now(self, gf):
        """감시 알림을 기다리지 않고 새 항목을 바로 넣는다 (새 폴더 → 곧바로 이름 바꾸기)"""
        from .common import ATTRS, Entry
        m = self.model
        gen = m.gen

        def got(src, res):
            try:
                info = src.query_info_finish(res)
            except GLib.Error:
                return
            if m.gen != gen or m is not self.folder or self.uri != (m.dir.get_uri() if m.dir else None):
                return
            m.upsert(Entry.from_info(None, info, gfile=src))
            self._try_select()
        gf.query_info_async(ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT, None, got)

    def new_folder(self):
        self._new_item(True)

    def new_text(self):
        self._new_item(False)

    def _new_item(self, folder):
        if not self._need_ops():
            return
        d = self._dest_dir()
        if d is None:
            return
        try:
            gf = fileops.new_folder(d, self) if folder else fileops.new_text_file(d, self)
        except GLib.Error as e:
            opener.error(self, "새 폴더를 만들 수 없습니다." if folder else "새 텍스트 문서를 만들 수 없습니다.", e.message)
            return
        except Exception as e:
            opener.error(self, "새 항목을 만들 수 없습니다.", str(e))
            return
        if gf is None:
            return
        self._want_select = ({gf.get_uri()}, True)
        self._insert_now(gf)

    def show_properties(self, gfiles=None):
        if properties is None:
            self.toast("속성 창을 불러오지 못했습니다.")
            return
        if gfiles is None:
            gfiles = self.selected_gfiles()
            if not gfiles:
                g = gfile_of(self.uri)
                gfiles = [g] if g is not None else []
        if not gfiles:
            return
        try:
            properties.show_properties(gfiles, self)
        except Exception as e:
            opener.error(self, "속성을 표시할 수 없습니다.", str(e))

    def compress_selected(self):
        files = self.selected_gfiles()
        if archive is None or not files:
            return
        here = self.uri

        def done(ok, results=None):
            # 새 zip 을 골라 이름을 바꿀 수 있게 (윈도우처럼)
            if ok and results and self.uri == here and not self.searching:
                self._want_select = ({results[0].get_uri()}, True)
                self._insert_now(results[0])
        try:
            archive.compress(files, self, self.app.track(done))
        except Exception as e:
            opener.error(self, "압축하지 못했습니다.", str(e))

    def extract(self, files):
        if archive is None or not files:
            return
        try:
            archive.extract(files, self, None, self.app.track())
        except Exception as e:
            opener.error(self, "압축을 풀지 못했습니다.", str(e))

    def extract_selected(self):
        self.extract([e.gfile for e in self.selected_entries() if not e.is_dir and self._is_archive(e)])

    def set_wallpaper_selected(self):
        sel = self.selected_entries()
        if len(sel) != 1 or not sel[0].path:
            return
        opener.set_wallpaper(self, sel[0].path, lambda: self.toast("바탕 화면 배경을 바꿨습니다.", 3))

    def copy_path(self, entries=None):
        files = [e.gfile for e in entries] if entries is not None else self.selected_gfiles()
        if not files:
            g = gfile_of(self.uri)
            files = [g] if g is not None else []
        if not files:
            return
        copy_text("\n".join(quote_path(f.get_path() or f.get_uri()) for f in files))
        self.toast("경로를 복사했습니다.", 2)

    def open_terminal_here(self, entry=None):
        if entry is not None and entry.is_dir and entry.path:
            opener.open_terminal(self, entry.path)
            return
        p = local_path(self.uri or "")
        if self.uri == HOME or self.uri == COMPUTER or not p:
            p = home_dir()
        opener.open_terminal(self, p)

    def restore_selected(self):
        if not self._need_ops():
            return
        files = [e.gfile for e in self.selected_entries()]
        if files:
            fileops.restore_from_trash(files, self, self.app.track())

    def empty_trash(self):
        if self._need_ops():
            fileops.empty_trash(self, self.app.track())

    def open_location_of(self, e):
        """검색 결과 · 최근 항목 — 담긴 폴더를 열고 그 항목을 골라 둔다"""
        parent = e.gfile.get_parent()
        if parent is not None:
            if self.searching:
                self.search.set_text("")
            self.navigate(parent.get_uri(), select=[e.uri])

    # ════════════════════════════════════════════════════════
    #  썸네일
    # ════════════════════════════════════════════════════════
    def scrolled(self):
        self._schedule_thumbs()

    def _schedule_thumbs(self):
        if self._thumb_src:
            return
        self._thumb_src = GLib.timeout_add(60, self._request_thumbs)

    def _request_thumbs(self):
        """보이는 항목만 — 이름을 두 줄로 맞추고 썸네일을 부탁한다"""
        self._thumb_src = 0
        if self._effective_mode() == "details" or self._page_name() != "folder":
            return False
        r = self.icons.visible_range()
        if r is None:
            return False
        m = self.model
        a, b = r
        m.fit_range(a - 20, b + 20)
        if self.thumbnailer is None:
            return False
        for pos in range(max(0, a), min(m.count(), b + 1)):
            e = m.entry_at(pos)
            if e is None or e.thumb is not None or e.thumb_req or e.is_dir or not thumbable(e.ctype):
                continue
            e.thumb_req = 1
            uri = e.uri
            try:
                self.thumbnailer.request(e.gfile, 256, e.mtime, lambda pb, uri=uri, m=m: self._thumb_ready(m, uri, pb))
            except Exception as ex:
                dbg("썸네일 요청 실패", ex)
                break
        return False

    def _thumb_ready(self, m, uri, pb):
        if pb is None:
            return
        try:
            m.set_thumb(uri, fit_pixbuf(pb, PX_M), fit_pixbuf(pb, PX_L))
        except Exception as ex:
            dbg("썸네일 적용 실패", ex)

    def icon_theme_changed(self):
        for t in self.tabs:
            for m in (t.folder, t.search_model):
                if m is not None:
                    m.refill_icons()
            self._update_tab_button(t)
        self.nav.rebuild()
        if self._page_name() == "home":
            self.home_page.refresh()
        elif self._page_name() == "computer":
            self.computer_page.refresh()

    # ════════════════════════════════════════════════════════
    #  검색
    # ════════════════════════════════════════════════════════
    def _on_search_changed(self, entry):
        q = entry.get_text().strip()
        if not q:
            if self.searching:
                self._end_search()
            return
        if q == self.search_query and self.searching:
            return
        self._start_search(q)

    def _stop_search_entry(self):
        if self.search.get_text():
            self.search.set_text("")
        self.focus_view()

    def _drive_roots(self):
        """연결된 드라이브의 맨 위 (루트 / 와 홈 안에 있는 것은 빼고)"""
        home = home_dir().rstrip("/") + "/"
        out = []
        try:
            for d in list_drives(self.app.vm):
                p = local_path(d.root_uri) if d.root_uri and d.mount is not None else None
                if p and p != "/" and not (p.rstrip("/") + "/").startswith(home):
                    out.append(d.root_uri)
        except Exception as e:
            dbg("드라이브 목록 실패", e)
        return out

    def _start_search(self, q):
        base = self.uri
        if base == COMPUTER:
            # 내 PC — 홈과 연결된 드라이브(USB 등). 시스템 전체(/)는 뒤지지 않는다 (시스템 파일뿐이고 오래 걸린다)
            base = [uri_of_path(home_dir())] + self._drive_roots()
        elif base == HOME or base is None:
            base = uri_of_path(home_dir())
        if self.searcher is not None:
            self.searcher.cancel()
        if self.search_model is None:
            self.search_model = self._new_model("search")
        sm = self.search_model
        sm.sort = self._sort_for("search")
        sm.desc = self.sort_desc
        sm.show_hidden = True                   # 찾는 쪽이 이미 걸렀다
        first = not self.searching
        self.searching = True
        self.search_query = q
        self.model = sm
        if first:
            self._force_details(True)
        sm.begin_search()                       # → "store" → 보기에 붙는다
        self.empty.hide()
        self.stack.set_visible_child_name("folder")
        self.details.set_kind("search")
        self.details.set_sort_indicator(sm.sort, self.sort_desc)
        self._apply_view()
        self._set_busy(True)
        self._update_location()
        self._update_commands()
        self._update_status()
        t = self.tab                            # 결과는 이 탭으로 (그사이 다른 탭으로 가도)
        s = self.searcher = Searcher(base, q, self.show_hidden,
                                     lambda batch: self._search_batch(t, s, batch),
                                     lambda n, cut: self._search_done(t, s, n, cut))
        s.start()

    def _search_batch(self, t, s, batch):
        if s is not t.searcher or t.search_model is None:
            return
        t.search_model.add_entries(batch)

    def _search_done(self, t, s, n, truncated):
        if s is not t.searcher or t.search_model is None:
            return
        t.search_model.loading = False
        if t is not self.tab:                   # 뒤에 있는 탭 — 돌아오면 _restore_tab 이 보인다
            return
        self._set_busy(False)
        self._update_empty()
        self._update_status()
        if truncated:
            self.toast(f"결과가 너무 많아 {n:,}개까지만 보입니다. 검색어를 더 자세히 입력하세요.")

    def _end_search(self, show=True):
        if self.searcher is not None:
            self.searcher.cancel()
            self.searcher = None
        if not self.searching:
            return
        self.searching = False
        self.search_query = ""
        self._force_details(is_in_trash(self.uri or ""))
        self.model = self.folder
        if self.search.get_text():
            self.search.handler_block_by_func(self._on_search_changed)
            self.search.set_text("")
            self.search.handler_unblock_by_func(self._on_search_changed)
        if self.search_model is not None:
            self.search_model.stop()
            self.search_model.begin_search()
            self.search_model.loading = False
        self._set_busy(self.folder.loading)
        if not show:
            return
        uri = self.uri
        if uri == HOME:
            self.stack.set_visible_child_name("home")
        elif uri == COMPUTER:
            self.stack.set_visible_child_name("computer")
        else:
            self.details.set_kind(self.folder.kind)
            self.details.set_sort_indicator(self.folder.sort, self.sort_desc)
            self._apply_view()
            self.stack.set_visible_child_name("folder")
            self._update_empty()
        self._update_location()
        self._update_commands()
        self._update_status()

    # ════════════════════════════════════════════════════════
    #  끌어 놓기
    # ════════════════════════════════════════════════════════
    def drag_uris(self):
        return self.selected_uris()

    def drag_started(self, uris):
        self.app.drag_uris = list(uris)

    def drag_ended(self):
        self.app.drag_uris = None

    def folder_uri_at(self, pos):
        e = self.model.entry_at(pos)
        if e is None or not e.is_dir:
            return None
        if is_in_trash(e.uri):
            return None
        return e.target or e.uri

    def current_drop_uri(self):
        if self.searching or self._page_name() != "folder":
            return None
        return self.uri

    def pointer_mods(self):
        try:
            seat = self.get_display().get_default_seat()
            _w, _x, _y, mask = self.get_window().get_device_position(seat.get_pointer())
            return mask
        except Exception:
            return 0

    def drop_action(self, ctx, dest):
        if not dest or is_special(dest):
            return 0
        acts = ctx.get_actions()
        own = self.app.drag_uris if Gtk.drag_get_source_widget(ctx) is not None else None
        dest_f = Gio.File.new_for_uri(dest)
        if is_in_trash(dest):
            if own and any(is_in_trash(u) for u in own):
                return 0
            return Gdk.DragAction.MOVE if acts & Gdk.DragAction.MOVE else 0
        if own:
            for u in own:
                f = Gio.File.new_for_uri(u)
                if dest_f.equal(f) or dest_f.has_prefix(f):
                    return 0                    # 자기 자신이나 그 안으로
        mods = self.pointer_mods()
        if mods & CTRL:
            want = Gdk.DragAction.COPY
        elif mods & SHIFT:
            want = Gdk.DragAction.MOVE
        elif own:
            src = Gio.File.new_for_uri(own[0])
            want = Gdk.DragAction.MOVE if (is_in_trash(own[0]) or _same_fs(src, dest_f)) else Gdk.DragAction.COPY
            if want == Gdk.DragAction.MOVE:
                parents = [Gio.File.new_for_uri(u).get_parent() for u in own]
                if all(p is not None and p.equal(dest_f) for p in parents):
                    return 0                    # 제자리
        else:
            want = Gdk.DragAction.COPY          # 다른 앱에서 — 놓을 때 경로를 보고 다시 정한다
        if not acts & want:
            want = ctx.get_suggested_action()
        return want

    def drop_files(self, uris, dest, act, mods, external):
        if not self._need_ops():
            return False
        files = [Gio.File.new_for_uri(u) for u in uris
                 if u and not u.lower().startswith(("http:", "https:", "data:", "javascript:"))]
        if not files:
            return False
        if is_in_trash(dest):
            fileops.trash(files, self, self.app.track())
            return True
        dest_f = Gio.File.new_for_uri(dest)
        if external and not (mods & (CTRL | SHIFT)):
            act = Gdk.DragAction.MOVE if _same_fs(files[0], dest_f) else Gdk.DragAction.COPY
        if not act:
            return False
        return self._transfer(files, dest_f, move=(act == Gdk.DragAction.MOVE))

    # ════════════════════════════════════════════════════════
    #  메뉴
    # ════════════════════════════════════════════════════════
    def _popup_at_button(self, menu, btn):
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_widget(btn, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())

    def _new_submenu(self):
        m = _new_menu()
        ok = self._dest_dir() is not None and fileops is not None
        _mitem(m, "폴더", self.new_folder, ["folder-new-symbolic", "folder-new", "folder"], "Ctrl+Shift+N", ok)
        _sep(m)
        _mitem(m, "텍스트 문서", self.new_text, ["text-x-generic-symbolic", "text-x-generic"], None, ok)
        return m

    def _new_menu_popup(self, btn):
        self._popup_at_button(self._new_submenu(), btn)

    def _sort_submenu(self):
        m = _new_menu()
        group = None
        kind = self.model.kind if self._page_name() == "folder" else "folder"
        fields = list(FOLDER_SORTS)
        if kind == "trash":
            fields = ["name", "orig", "dtime", "size", "type"]
        elif kind == "search":
            fields = list(FOLDER_SORTS) + ["loc"]
        cur = self.model.sort
        for f in fields:
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, SORT_LABELS[f])
            group = it
            it.set_active(f == cur)
            it.connect("toggled", lambda w, f=f: w.get_active() and self.set_sort(f, self.sort_desc))
            m.append(it)
        _sep(m)
        g2 = None
        for label, d in (("오름차순", False), ("내림차순", True)):
            it = Gtk.RadioMenuItem.new_with_label_from_widget(g2, label)
            g2 = it
            it.set_active(self.sort_desc == d)
            it.connect("toggled", lambda w, d=d: w.get_active() and self.set_sort(self.model.sort, d))
            m.append(it)
        return m

    def _sort_menu_popup(self, btn):
        self._popup_at_button(self._sort_submenu(), btn)

    def _view_submenu(self):
        m = _new_menu()
        group = None
        accels = {"large": "Ctrl+Shift+2", "medium": "Ctrl+Shift+3", "details": "Ctrl+Shift+6"}
        for mode in VIEW_MODES:
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, f"{VIEW_LABELS[mode]}")
            it.set_tooltip_text(accels[mode])
            group = it
            it.set_active(self.view_mode == mode)
            it.connect("toggled", lambda w, mode=mode: w.get_active() and self.set_view_mode(mode))
            m.append(it)
        _sep(m)
        h = Gtk.CheckMenuItem(label="숨긴 항목 표시")
        h.set_active(self.show_hidden)
        h.connect("toggled", lambda *_: self.toggle_hidden())
        m.append(h)
        return m

    def _view_menu_popup(self, btn):
        self._popup_at_button(self._view_submenu(), btn)

    def _more_menu_popup(self, btn):
        m = _new_menu()
        folder = self._page_name() == "folder"
        # 창이 좁아 단추 줄에서 감춘 것들이 먼저
        hidden = self._cmd_hidden()
        for b in hidden:
            label, cb, icon, sub = self._cmd_overflow[b]
            _mitem(m, label, cb, icon, None, b.get_sensitive(), submenu=sub() if sub else None)
        if hidden:
            _sep(m)
        _mitem(m, "모두 선택", self.select_all, ["edit-select-all-symbolic"], "Ctrl+A", folder or self._page_name() == "home")
        _mitem(m, "선택 안 함", self.unselect_all, ["edit-clear-symbolic"])
        _mitem(m, "선택 영역 반전", self.invert_selection, ["edit-select-symbolic"], None, folder)
        _sep(m)
        _mitem(m, "새 창 열기", lambda: self.open_new_window(), ["window-new-symbolic"], "Ctrl+N")
        _mitem(m, "터미널에서 열기", self.open_terminal_here, ["utilities-terminal-symbolic", "utilities-terminal"])
        _mitem(m, "경로 복사", self.copy_path, ["edit-copy-symbolic"], None,
               bool(self.selected_gfiles() or gfile_of(self.uri or HOME)))
        _sep(m)
        _mitem(m, "속성", self.show_properties, ["document-properties-symbolic"], "Alt+Enter", properties is not None)
        self._popup_at_button(m, btn)

    def _open_with_submenu(self, entries):
        m = _new_menu()
        e = entries[0]
        same = all(x.ctype == e.ctype for x in entries)
        apps = opener.recommended_apps(e.ctype) if same and e.ctype else []
        for a in apps[:12]:
            it = Gtk.MenuItem()
            box = Gtk.Box(spacing=10)
            from .common import icons
            gi_ = a.get_icon() or Gio.ThemedIcon.new("application-x-executable")
            box.pack_start(Gtk.Image.new_from_pixbuf(icons().get(gi_, 16)), False, False, 0)
            box.pack_start(Gtk.Label(label=a.get_display_name() or a.get_name(), xalign=0), True, True, 0)
            it.add(box)
            it.connect("activate", lambda _i, a=a: opener.launch_app(self, a, [x.gfile for x in entries]))
            m.append(it)
        if apps:
            _sep(m)
        _mitem(m, "다른 앱 선택…", lambda: opener.choose_app(self, entries))
        return m

    def view_context_menu(self, event):
        if self._page_name() != "folder":
            return
        sel = self.selected_entries()
        m = self._item_menu(sel) if sel else self._background_menu()
        if m is None:
            return
        m.show_all()
        m.attach_to_widget(self, None)
        v = self._active_view()
        if event is not None:
            m.popup_at_pointer(event)
            return
        pos = v.cursor_pos()
        if pos is None and sel:
            pos = v.selected_positions()[0]
        rect = v.cell_rect(pos) if pos is not None and sel else None
        if rect is not None:
            m.popup_at_rect(v.view.get_window(), rect, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST,
                            Gtk.get_current_event())
        else:
            m.popup_at_widget(v.view, Gdk.Gravity.CENTER, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())

    def _item_menu(self, sel):
        m = _new_menu()
        ops = fileops is not None
        n = len(sel)
        e = sel[0]
        in_trash = is_in_trash(self.uri or "") and not self.searching
        if in_trash:
            _mitem(m, "복원", self.restore_selected, ["edit-undo-symbolic"], None, ops)
            _sep(m)
            _mitem(m, "잘라내기", self.cut, ["edit-cut-symbolic"], "Ctrl+X", ops)
            _mitem(m, "삭제", lambda: self.delete_selected(True), ["edit-delete-symbolic"], "Delete", ops)
            _sep(m)
            _mitem(m, "속성", lambda: self.show_properties(), ["document-properties-symbolic"], "Alt+Enter",
                   properties is not None)
            return m
        _mitem(m, "열기", self.open_selected, None, "Enter", True, bold=True)
        if n == 1 and e.is_dir:
            _mitem(m, "새 탭에서 열기", lambda: self.open_selected(new_tab=True), ["tab-new-symbolic", "list-add-symbolic"])
            _mitem(m, "새 창에서 열기", lambda: self.open_selected(new_window=True), ["window-new-symbolic"])
        files = [x for x in sel if not x.is_dir]
        if files and len(files) == n:
            _mitem(m, "연결 프로그램", icon=["system-run-symbolic", "application-x-executable-symbolic"],
                   submenu=self._open_with_submenu(files))
        if self.searching or self._page_name() == "home":
            _mitem(m, "파일 위치 열기", lambda: self.open_location_of(e), ["folder-open-symbolic"], None, n == 1)
        _sep(m)
        _mitem(m, "잘라내기", self.cut, ["edit-cut-symbolic"], "Ctrl+X", ops)
        _mitem(m, "복사", self.copy, ["edit-copy-symbolic"], "Ctrl+C", ops)
        if n == 1 and e.is_dir and self._can_paste:
            _mitem(m, "붙여넣기", lambda: self.paste(into=e.gfile), ["edit-paste-symbolic"], None, ops)
        _mitem(m, "이름 바꾸기", self.rename_selected, ["document-edit-symbolic", "edit-symbolic"], "F2",
               ops and n == 1 and e.can_rename)
        _mitem(m, "삭제", self.delete_selected, ["user-trash-symbolic", "edit-delete-symbolic"], "Delete", ops)
        _sep(m)
        if archive is not None:
            _mitem(m, "압축(ZIP) 파일로 보내기", self.compress_selected, ["package-x-generic-symbolic",
                                                                        "application-zip-symbolic"])
            arcs = [x for x in files if self._is_archive(x)]
            if arcs:
                _mitem(m, "압축 풀기", self.extract_selected, ["package-x-generic-symbolic",
                                                             "archive-extract-symbolic"])
        if n == 1 and not e.is_dir and (e.ctype or "").startswith("image/") and e.path:
            _mitem(m, "바탕 화면 배경으로 설정", self.set_wallpaper_selected,
                   ["preferences-desktop-wallpaper-symbolic", "image-x-generic-symbolic"])
        _mitem(m, "경로 복사", self.copy_path, ["edit-copy-symbolic"])
        if n == 1 and e.is_dir and e.path:
            _mitem(m, "터미널에서 열기", lambda: self.open_terminal_here(e),
                   ["utilities-terminal-symbolic", "utilities-terminal"])
        _sep(m)
        _mitem(m, "속성", lambda: self.show_properties(), ["document-properties-symbolic"], "Alt+Enter",
               properties is not None)
        return m

    def _background_menu(self):
        m = _new_menu()
        in_trash = is_in_trash(self.uri or "") and not self.searching
        _mitem(m, "보기", icon=VIEW_ICONS[self.view_mode], submenu=self._view_submenu())
        _mitem(m, "정렬 기준", icon=["view-sort-descending-symbolic"], submenu=self._sort_submenu())
        _mitem(m, "새로 고침", self.refresh, ["view-refresh-symbolic"], "F5")
        _sep(m)
        if in_trash:
            _mitem(m, "휴지통 비우기", self.empty_trash, ["user-trash-full-symbolic"], None,
                   fileops is not None and self.model.count() > 0)
            _sep(m)
            _mitem(m, "속성", lambda: self.show_properties(), ["document-properties-symbolic"], None,
                   properties is not None)
            return m
        if self.searching:
            _mitem(m, "모두 선택", self.select_all, ["edit-select-all-symbolic"], "Ctrl+A")
            return m
        writable = self._dest_dir() is not None and fileops is not None
        _mitem(m, "붙여넣기", self.paste, ["edit-paste-symbolic"], "Ctrl+V", writable and self._can_paste)
        _sep(m)
        _mitem(m, "새로 만들기", icon=["list-add-symbolic"], submenu=self._new_submenu())
        _sep(m)
        _mitem(m, "터미널에서 열기", self.open_terminal_here, ["utilities-terminal-symbolic", "utilities-terminal"],
               None, bool(local_path(self.uri or "")))
        _mitem(m, "속성", lambda: self.show_properties(), ["document-properties-symbolic"], "Alt+Enter",
               properties is not None)
        return m

    def page_context_menu(self, page, event):
        m = _new_menu()
        if page is self.home_page:
            tiles = page.selected_tiles()
            if tiles:
                t = tiles[0]
                _mitem(m, "열기", lambda: self.navigate(t.uri), None, "Enter", bold=True)
                _mitem(m, "새 탭에서 열기", lambda: self.open_new_tab(t.uri), ["tab-new-symbolic", "list-add-symbolic"])
                _mitem(m, "새 창에서 열기", lambda: self.open_new_window(t.uri), ["window-new-symbolic"])
                _sep(m)
                _mitem(m, "경로 복사", lambda: copy_text(quote_path(t.path)), ["edit-copy-symbolic"])
                _mitem(m, "터미널에서 열기", lambda: opener.open_terminal(self, t.path),
                       ["utilities-terminal-symbolic", "utilities-terminal"])
                _sep(m)
                _mitem(m, "속성", lambda: self.show_properties([Gio.File.new_for_path(t.path)]),
                       ["document-properties-symbolic"], None, properties is not None)
            else:
                ents = page.selected_entries()
                if not ents:
                    return
                e = ents[0]
                _mitem(m, "열기", lambda: self.open_entries(ents), None, "Enter", bold=True)
                _mitem(m, "연결 프로그램", icon=["system-run-symbolic"], submenu=self._open_with_submenu(ents))
                _mitem(m, "파일 위치 열기", lambda: self.open_location_of(e), ["folder-open-symbolic"])
                _sep(m)
                _mitem(m, "복사", self.copy, ["edit-copy-symbolic"], "Ctrl+C", fileops is not None)
                _mitem(m, "경로 복사", lambda: self.copy_path(ents), ["edit-copy-symbolic"])
                _sep(m)
                _mitem(m, "속성", lambda: self.show_properties([x.gfile for x in ents]),
                       ["document-properties-symbolic"], None, properties is not None)
        else:
            ds = page.selected_drives()
            if not ds:
                return
            d = ds[0]
            _mitem(m, "열기", lambda: self.open_drive(d), None, "Enter", bold=True)
            if d.root_uri:
                _mitem(m, "새 탭에서 열기", lambda: self.open_new_tab(d.root_uri), ["tab-new-symbolic", "list-add-symbolic"])
                _mitem(m, "새 창에서 열기", lambda: self.open_new_window(d.root_uri), ["window-new-symbolic"])
            if d.removable and (d.can_eject or d.can_unmount):
                _sep(m)
                _mitem(m, "꺼내기", lambda: self.eject_drive(d), ["media-eject-symbolic"])
            _sep(m)
            _mitem(m, "속성", lambda: self.show_properties([Gio.File.new_for_uri(d.root_uri)]),
                   ["document-properties-symbolic"], None, properties is not None and bool(d.root_uri))
        m.show_all()
        m.attach_to_widget(self, None)
        if event is not None:
            m.popup_at_pointer(event)
        else:
            m.popup_at_widget(page.widget, Gdk.Gravity.CENTER, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())

    def nav_context_menu(self, row, event):
        m = _new_menu()
        uri = row.uri
        if row.kind == "drive":
            d = row.drive
            _mitem(m, "열기", lambda: self.open_drive(d), None, None, bold=True)
            if d.root_uri:
                _mitem(m, "새 탭에서 열기", lambda: self.open_new_tab(d.root_uri), ["tab-new-symbolic", "list-add-symbolic"])
                _mitem(m, "새 창에서 열기", lambda: self.open_new_window(d.root_uri), ["window-new-symbolic"])
            if d.removable and (d.can_eject or d.can_unmount):
                _sep(m)
                _mitem(m, "꺼내기", lambda: self.eject_drive(d), ["media-eject-symbolic"])
            _sep(m)
            _mitem(m, "속성", lambda: self.show_properties([Gio.File.new_for_uri(d.root_uri)]),
                   ["document-properties-symbolic"], None, properties is not None and bool(d.root_uri))
        else:
            _mitem(m, "열기", lambda: self.navigate(uri), None, None, bold=True)
            _mitem(m, "새 탭에서 열기", lambda: self.open_new_tab(uri), ["tab-new-symbolic", "list-add-symbolic"])
            _mitem(m, "새 창에서 열기", lambda: self.open_new_window(uri), ["window-new-symbolic"])
            if uri == TRASH:
                _sep(m)
                _mitem(m, "휴지통 비우기", self.empty_trash, ["user-trash-full-symbolic"], None, fileops is not None)
            p = local_path(uri)
            if p:
                _sep(m)
                _mitem(m, "경로 복사", lambda: copy_text(quote_path(p)), ["edit-copy-symbolic"])
                _mitem(m, "터미널에서 열기", lambda: opener.open_terminal(self, p),
                       ["utilities-terminal-symbolic", "utilities-terminal"])
                _sep(m)
                _mitem(m, "속성", lambda: self.show_properties([Gio.File.new_for_path(p)]),
                       ["document-properties-symbolic"], None, properties is not None)
        m.show_all()
        m.attach_to_widget(self, None)
        if event is not None:
            m.popup_at_pointer(event)
        else:
            m.popup_at_widget(row, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())

    # ════════════════════════════════════════════════════════
    #  키보드 · 마우스
    # ════════════════════════════════════════════════════════
    def view_nav_button(self, n):
        if n == 8:
            self.go_back()
        elif n == 9:
            self.go_forward()

    def _on_button(self, _w, ev):
        if (HYPR and ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1
                and ev.window == self.get_window() and self._in_titlebar(ev.x, ev.y)):
            self._toggle_max()                  # 제목 표시줄의 빈 곳을 두 번 — 최대화 ↔ 복원
            return True
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (8, 9):
            self.view_nav_button(ev.button)
            return True
        return False

    def _on_alloc(self, _w, a):
        """창이 좁으면 왼쪽 탐색 창을 숨긴다 (윈도우 11 탐색기처럼) — 넓어지면 다시. 경계에서 깜박이지 않게 틈을 둔다"""
        w = a.width
        if w < 600 and self.nav.get_visible():
            GLib.idle_add(lambda: (self.nav.hide(), False)[1])
        elif w >= 660 and not self.nav.get_visible():
            GLib.idle_add(lambda: (self.nav.show(), False)[1])

    def _in_titlebar(self, x, y):
        a = self._titlebar.get_allocation()
        return a.x <= x < a.x + a.width and a.y <= y < a.y + a.height

    def _on_key(self, _w, ev):
        mods = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl, shift, alt = bool(mods & CTRL), bool(mods & SHIFT), bool(mods & ALT)
        kv = Gdk.keyval_to_lower(ev.keyval)
        focus = self.get_focus()
        editing = isinstance(focus, (Gtk.Entry, Gtk.TextView)) or self.icons.editing() or self.details.editing()
        K = Gdk

        # ── 어디서나 ──
        if ctrl and not shift and kv == K.KEY_n:
            self.open_new_window()
            return True
        if ctrl and shift and kv == K.KEY_n:
            self.new_folder()
            return True
        if ctrl and kv == K.KEY_w:
            self.close_tab()                    # 마지막 탭이면 창을 닫는다
            return True
        if ctrl and not shift and kv == K.KEY_t:
            self.new_tab()
            return True
        if ctrl and ev.keyval in (K.KEY_Tab, K.KEY_ISO_Left_Tab, K.KEY_Page_Down, K.KEY_Page_Up):
            back = ev.keyval in (K.KEY_ISO_Left_Tab, K.KEY_Page_Up) or (shift and ev.keyval == K.KEY_Tab)
            self.cycle_tab(-1 if back else 1)
            return True
        if ctrl and not shift and K.KEY_1 <= ev.keyval <= K.KEY_9:
            n = ev.keyval - K.KEY_1
            self.switch_tab(self.tabs[-1] if n == 8 else self.tabs[min(n, len(self.tabs) - 1)])
            return True
        if (ctrl and kv == K.KEY_l) or (alt and kv == K.KEY_d):
            self.address.begin_edit()
            return True
        if (ctrl and kv == K.KEY_f) or ev.keyval == K.KEY_F3 or (ctrl and kv == K.KEY_e):
            self.search.grab_focus()
            return True
        if ev.keyval == K.KEY_F5 or (ctrl and kv == K.KEY_r):
            self.refresh()
            return True
        if alt and ev.keyval in (K.KEY_Left, K.KEY_KP_Left):
            self.go_back()
            return True
        if alt and ev.keyval in (K.KEY_Right, K.KEY_KP_Right):
            self.go_forward()
            return True
        if alt and ev.keyval in (K.KEY_Up, K.KEY_KP_Up):
            self.go_up()
            return True
        if ctrl and kv == K.KEY_h:
            self.toggle_hidden()
            return True
        if ctrl and shift:
            # Ctrl+Shift+2 · 3 · 6 — 키 배치와 상관없이 숫자 줄의 자리로
            num = {K.KEY_2: "large", K.KEY_at: "large", K.KEY_3: "medium", K.KEY_numbersign: "medium",
                   K.KEY_6: "details", K.KEY_asciicircum: "details"}.get(ev.keyval)
            if num:
                self.set_view_mode(num)
                return True
        if ev.keyval == K.KEY_F4 and not ctrl and not alt:
            self.address.begin_edit()
            return True
        if editing:
            return False
        # 탐색 창 · 단추에 초점이 있으면 그 위젯이 알아서 (Enter 로 행 열기 · 단추 누르기)
        if focus is not None and (focus.get_ancestor(NavPane) is not None or isinstance(focus, Gtk.Button)):
            if alt and ev.keyval in (K.KEY_Return, K.KEY_KP_Enter):
                self.show_properties()
                return True
            return False

        # ── 목록에 초점이 있을 때 ──
        page = self._page_name()
        if ev.keyval in (K.KEY_Return, K.KEY_KP_Enter, K.KEY_ISO_Enter):
            if alt:
                self.show_properties()
            elif page == "home" and isinstance(focus, Gtk.FlowBoxChild) and not self.home_page.selected_tiles():
                return False                    # FlowBox 가 커서 타일을 연다
            else:
                self.open_selected(new_window=ctrl)
            return True
        if ctrl and kv == K.KEY_a:
            self.select_all()
            return True
        if ctrl and kv == K.KEY_c:
            self.copy()
            return True
        if ctrl and kv == K.KEY_x:
            self.cut()
            return True
        if ctrl and kv == K.KEY_v:
            self.paste()
            return True
        if ev.keyval in (K.KEY_Delete, K.KEY_KP_Delete):
            if page == "folder":
                self.delete_selected(permanent=shift)
            return True
        if ev.keyval == K.KEY_F2:
            self.rename_selected()
            return True
        if ev.keyval == K.KEY_BackSpace:
            self.go_back() if self.searching else self.go_up()
            return True
        if ev.keyval == K.KEY_Escape:
            if self.searching:
                self.search.set_text("")
                return True
            if page == "folder":
                self.unselect_all()
            return False
        if ev.keyval == K.KEY_Menu or (ev.keyval == K.KEY_F10 and shift):
            if page == "folder":
                self.view_context_menu(None)
                return True
            return False
        if page == "folder" and not ctrl and not alt and ev.string and ev.string.isprintable() \
                and not ev.string.isspace():
            self._type_ahead(ev.string, ev.time)
            return True
        return False

    def _type_ahead(self, ch, t):
        """글자를 치면 그 글자로 시작하는 이름으로 (같은 글자를 거듭 치면 다음 것으로 — 윈도우처럼)"""
        if t - self._typeahead_t > 1000:
            self._typeahead = ""
        self._typeahead_t = t
        prev = self._typeahead
        self._typeahead += ch.casefold()
        q = self._typeahead
        m = self.model
        n = m.count()
        if not n:
            return
        v = self._active_view()
        cur = v.cursor_pos()
        start = 0
        cycling = len(set(q)) == 1 and len(q) > 1
        if cycling:
            q = q[0]
            start = (cur + 1) if cur is not None else 0
        elif prev and cur is not None:
            start = cur
        for k in range(n):
            pos = (start + k) % n
            e = m.entry_at(pos)
            if e is not None and e.name.casefold().startswith(q):
                v.select_positions([pos], cursor=pos)
                return

    # ════════════════════════════════════════════════════════
    #  알림 · 닫기
    # ════════════════════════════════════════════════════════
    def toast(self, text, secs=4):
        self.toast_label.set_text(text)
        self.toast_label.show()
        if self._toast_src:
            GLib.source_remove(self._toast_src)

        def hide():
            self._toast_src = 0
            self.toast_label.hide()
            return False
        self._toast_src = GLib.timeout_add_seconds(secs, hide)

    def _on_wstate(self, _w, ev):
        # 최대화 단추 — 최대화된 창이면 "이전 크기로 복원" (윈도우처럼). Hyprland 에서는 GTK 가 최대화를
        #   알아보지 못해 Hyprland 에 묻는다 (_sync_max)
        if HYPR:
            self._sync_max_later(250)
        else:
            self._show_max(bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED))
        return False

    def _on_close(self, *_):
        st = self.app.state
        if not (self.get_window() and self.get_window().get_state() & Gdk.WindowState.MAXIMIZED):
            w, h = self.get_size()
            st["size"] = [w, h]
            st["maximized"] = False
        st["nav_width"] = self.paned.get_position()
        cols = dict(st.get("columns") or {}) if isinstance(st.get("columns"), dict) else {}
        cols.update(self.details.widths())
        st["columns"] = cols
        if self._saved_mode is not None:
            st["view"] = self._saved_mode
        self.app.save_state()
        return False

    def _on_destroy(self, *_):
        """감시 · 작업 스레드 · 신호를 거둔다 (닫힌 창이 드라이브·클립보드 알림을 받지 않게)"""
        for t in self.tabs:
            for m in (t.folder, t.search_model):
                if m is not None:
                    m.stop()
            if t.searcher is not None:
                t.searcher.cancel()
        if self.thumbnailer is not None:
            try:
                self.thumbnailer.cancel_all()
            except Exception:
                pass
        try:
            self._clip.disconnect(self._clip_sig)
        except Exception:
            pass
        for sid in self._vm_sigs:
            try:
                self.app.vm.disconnect(sid)
            except Exception:
                pass
        self._vm_sigs = []
        self.nav.shutdown()
        self.home_page.shutdown()


# 쓰이지 않는 이름 경고를 막는다 (다른 모듈이 window 에서 가져다 쓰는 것)
__all__ = ["ExplorerWindow", "edit_text", "theme_icon"]
