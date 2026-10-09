"""Nenerobo — 창 (제목줄 안의 탭 + 터미널 탭들).

윈도우 터미널처럼 탭이 제목줄에 있다 (GTK4 는 Wayland 에서 제목줄을 스스로 그린다 — WorldLink 막대는 창 규칙으로 뺀다).
고른 탭은 터미널 바탕색으로 이어져 보인다. 단축키는 윈도우 터미널을 따른다:
  Ctrl+C (고른 글이 있으면 복사, 없으면 프로그램에 그대로) · Ctrl+V · Ctrl+Shift+C/V
  Ctrl+Shift+T 새 탭 · Ctrl+Shift+W 탭 닫기 · Ctrl+Shift+D 탭 복제 · Ctrl+Shift+N 새 창
  Ctrl+Tab · Ctrl+Shift+Tab · Ctrl+PgDn/PgUp 탭 옮겨 가기 · Ctrl+Alt+1~9 그 탭으로
  Ctrl+Shift+F 찾기 · Ctrl+= / Ctrl+- / Ctrl+0 글자 크기 (Ctrl+휠도) · F11 · Alt+Enter 전체 화면
  Ctrl+Shift+1~9 N번째 프로필로 새 탭 · Ctrl+, 설정
닫을 때 탭이 둘 이상이거나 셸 말고 도는 프로그램이 있으면 묻는다.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from .term import TermTab, open_uri  # noqa: E402

C = Gdk.ModifierType.CONTROL_MASK
S = Gdk.ModifierType.SHIFT_MASK
A = Gdk.ModifierType.ALT_MASK


ACCELS = {"Ctrl+Shift+T": "<Control><Shift>t", "Ctrl+Shift+N": "<Control><Shift>n", "Ctrl+Shift+F": "<Control><Shift>f",
          "F11": "F11", "Ctrl+Shift+D": "<Control><Shift>d", "Ctrl+Shift+W": "<Control><Shift>w",
          "Ctrl+C": "<Control>c", "Ctrl+V": "<Control>v", "Ctrl+,": "<Control>comma"}
for _n in range(1, 10):
    ACCELS[f"Ctrl+Shift+{_n}"] = f"<Control><Shift>{_n}"


def mitem(menu, label, action):
    """메뉴 항목 — "이름\t단축키" 의 단축키는 메뉴 오른쪽에 맞춰 보이는 accel 로"""
    text, _t, key = label.partition("\t")
    it = Gio.MenuItem.new(text, action)
    if key in ACCELS:
        it.set_attribute_value("accel", GLib.Variant("s", ACCELS[key]))
    menu.append_item(it)


class TabButton(Gtk.Box):
    """제목줄의 탭 하나 — 누르면 고르고, 가운데 단추로 닫고, 두 번 누르면 이름 바꾸기, 오른쪽 클릭은 탭 메뉴"""

    def __init__(self, win, tab):
        super().__init__(spacing=0)
        self.win, self.tab = win, tab
        self.add_css_class("nr-tab")
        self.set_valign(Gtk.Align.END)
        mark = Gtk.Box()
        mark.add_css_class("nr-mark")
        mark.set_valign(Gtk.Align.CENTER)
        self.append(mark)
        self.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22, width_chars=6)
        self.append(self.label)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="탭 닫기 (Ctrl+Shift+W)")
        close.add_css_class("flat")
        close.add_css_class("nr-close")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *_: win.close_tab(tab))
        self.append(close)
        self.close_btn = close
        g = Gtk.GestureClick(button=0)
        g.connect("pressed", self._pressed)
        self.add_controller(g)
        # 옆으로 끌면 순서를 바꾼다 — 누르기가 잡은 입력을 같이 받게 한 무리로
        d = Gtk.GestureDrag(button=1)
        d.connect("drag-begin", self._drag_begin)
        d.connect("drag-update", self._drag_update)
        self.add_controller(d)
        d.group(g)                             # (같은 위젯에 붙인 뒤에야 묶인다)
        self._drag_x = None
        self._dragging = False

    def _drag_begin(self, _g, x, _y):
        self._drag_x = x
        self._dragging = False

    def _drag_update(self, _g, dx, _dy):
        if self._drag_x is None:
            return
        if not self._dragging:
            if abs(dx) < 8:
                return
            self._dragging = True
        ok, pt = self.compute_point(self.win.strip, Graphene.Point().init(self._drag_x + dx, 0))
        if ok:
            self.win.move_tab_to_x(self.tab, pt.x)

    def _pressed(self, g, n, x, y):
        # × 단추 위의 누름은 단추 몫 — 여기서 잡으면(CLAIMED) 단추가 놓기를 못 받아 "clicked" 가 안 나서
        #   탭이 닫히지 않았다 (단축키·가운데 클릭은 됐다)
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if w is not None and (w is self.close_btn or w.is_ancestor(self.close_btn)):
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        b = g.get_current_button()
        if b == 1:
            self.win.select_tab(self.tab)
            if n == 2:
                self.win.rename_tab(self.tab)
        elif b == 2:
            self.win.close_tab(self.tab)
        elif b == 3:
            self.win.select_tab(self.tab)
            self.win.tab_menu(self, x, y)
        g.set_state(Gtk.EventSequenceState.CLAIMED)


class WinButton(Gtk.Button):
    """창 조작 단추 — WorldLink 막대(hyprbars sekai:*)처럼 선으로 그린 10px 아이콘 (테마 아이콘이 아니라)"""

    def __init__(self, win, kind, tip, cb):
        super().__init__(tooltip_text=tip)
        self.win, self.kind = win, kind
        self.add_css_class("nr-winbtn")
        if kind == "close":
            self.add_css_class("nr-x")
        self.set_focusable(False)
        self.area = Gtk.DrawingArea(content_width=10, content_height=10)
        self.area.set_size_request(10, 10)
        self.area.set_halign(Gtk.Align.CENTER)
        self.area.set_valign(Gtk.Align.CENTER)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self.connect("clicked", lambda *_: cb())
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self.area.queue_draw())
        hover.connect("leave", lambda *_: self.area.queue_draw())
        self.add_controller(hover)

    def _draw(self, area, cr, w, h):
        fg = self.get_color()
        if self.kind == "close" and self.get_state_flags() & Gtk.StateFlags.PRELIGHT:
            cr.set_source_rgba(1, 1, 1, 1)
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        cr.set_line_width(1.0)
        if self.kind == "min":
            cr.move_to(0, h / 2 + 0.5)
            cr.line_to(w, h / 2 + 0.5)
        elif self.kind == "max":
            if self.win.is_maximized() or self.win.is_fullscreen():
                cr.rectangle(0.5, 2.5, w - 3, h - 3)           # 복원 — 두 겹 네모
                cr.move_to(2.5, 2.5)
                cr.line_to(2.5, 0.5)
                cr.line_to(w - 0.5, 0.5)
                cr.line_to(w - 0.5, h - 2.5)
                cr.line_to(w - 2.5, h - 2.5)
            else:
                cr.rectangle(0.5, 0.5, w - 1, h - 1)
        else:
            cr.move_to(0, 0)
            cr.line_to(w, h)
            cr.move_to(w, 0)
            cr.line_to(0, h)
        cr.stroke()


def window_buttons(win, bar, kinds=("min", "max", "close")):
    """제목줄 끝에 창 조작 단추 — 최대화 상태가 바뀌면 그림도 바꾼다"""
    cbs = {"min": ("최소화", win.minimize),
           "max": ("최대화", lambda: win.unmaximize() if win.is_maximized() else win.maximize()),
           "close": ("닫기", win.close)}
    btns = []
    for k in kinds:
        tip, cb = cbs[k]
        b = WinButton(win, k, tip, cb)
        btns.append(b)
        bar.append(b)
    for prop in ("notify::maximized", "notify::fullscreened"):
        win.connect(prop, lambda *_: [b.area.queue_draw() for b in btns])
    return btns


class TermWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="터미널")
        self.tabs = []
        self.buttons = {}
        self._closing = False
        self._menu_tab = None
        self._menu_link = None
        self.set_default_size(960, 620)
        self.set_icon_name("utilities-terminal")

        self.stack = Gtk.Stack()
        self.stack.add_css_class("nr-body")
        self.set_child(self.stack)
        self._build_titlebar()
        self._build_actions()

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)

    # ── 제목줄 ──
    def _build_titlebar(self):
        bar = Gtk.Box()
        bar.add_css_class("nr-titlebar")
        self.strip = Gtk.Box()
        self.strip.add_css_class("nr-tabs")
        sc = self.strip_scroll = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        sc.set_propagate_natural_width(True)
        sc.set_child(self.strip)
        bar.append(sc)
        plus = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="새 탭 (Ctrl+Shift+T)")
        plus.add_css_class("flat")
        plus.add_css_class("nr-flat")
        plus.set_valign(Gtk.Align.CENTER)
        plus.connect("clicked", lambda *_: self.new_tab())
        bar.append(plus)
        self.menu_btn = mb = Gtk.MenuButton(icon_name="pan-down-symbolic", tooltip_text="새 탭 · 프로필 · 설정")
        mb.set_has_frame(False)
        mb.add_css_class("flat")
        mb.add_css_class("nr-flat")
        mb.set_valign(Gtk.Align.CENTER)
        bar.append(mb)
        self.refresh_menu()
        space = Gtk.Box()
        space.set_hexpand(True)
        bar.append(space)
        window_buttons(self, bar)
        handle = Gtk.WindowHandle()
        handle.set_child(bar)
        self.set_titlebar(handle)

    def _build_actions(self):
        def act(name, cb, param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", lambda _a, p: cb(p) if param else cb())
            self.add_action(a)
        act("new-tab", lambda: self.new_tab())
        act("open-profile", lambda p: self.new_tab(profile=self.cfg.profile(p.get_string())), GLib.VariantType("s"))
        act("settings", lambda: self.get_application().open_settings(self))
        act("new-window", self.new_window)
        act("find", lambda: self.current() and self.current().show_search())
        act("fullscreen", self.toggle_fullscreen)
        act("about", self.about)
        act("copy", lambda: self.current() and self.current().copy())
        act("paste", lambda: self.current() and self.current().paste())
        act("select-all", lambda: self.current() and self.current().term.select_all())
        act("close-tab", lambda: self.current() and self.close_tab(self.current()))
        act("open-link", lambda: self._menu_link and self.open_uri(self._menu_link))
        act("copy-link", lambda: self._menu_link and self.get_clipboard().set(self._menu_link))
        act("rename-tab", lambda: self._menu_tab and self.rename_tab(self._menu_tab))
        act("dup-tab", lambda: self._menu_tab and self.duplicate_tab(self._menu_tab))
        act("close-menu-tab", lambda: self._menu_tab and self.close_tab(self._menu_tab))
        act("close-others", lambda: self._menu_tab and self.close_others(self._menu_tab))

    @property
    def cfg(self):
        return self.get_application().cfg

    @property
    def ap(self):
        return self.get_application().ap

    def refresh_menu(self):
        """▾ 메뉴 — 위에 프로필들 (윈도우 터미널처럼), 아래에 창·설정"""
        menu = Gio.Menu()
        sec = Gio.Menu()
        for i, p in enumerate(self.cfg.profiles()):
            label = p["name"] + (f"\tCtrl+Shift+{i + 1}" if i < 9 else "")
            text, _t, key = label.partition("\t")
            it = Gio.MenuItem.new(text, None)
            it.set_action_and_target_value("win.open-profile", GLib.Variant("s", p["id"]))
            if key in ACCELS:
                it.set_attribute_value("accel", GLib.Variant("s", ACCELS[key]))
            sec.append_item(it)
        menu.append_section(None, sec)
        sec = Gio.Menu()
        mitem(sec, "새 창\tCtrl+Shift+N", "win.new-window")
        mitem(sec, "찾기\tCtrl+Shift+F", "win.find")
        mitem(sec, "전체 화면\tF11", "win.fullscreen")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        mitem(sec, "설정\tCtrl+,", "win.settings")
        mitem(sec, "Nenerobo 정보", "win.about")
        menu.append_section(None, sec)
        self.menu_btn.set_menu_model(menu)

    def apply_config(self):
        """설정이 바뀌었다 — 모든 탭의 모양, 탭 색, 메뉴의 프로필"""
        for t in self.tabs:
            t.apply_config()
            self._style_button(t)
        self.refresh_menu()

    def _style_button(self, tab):
        b = self.buttons.get(tab)
        if b is None:
            return
        for c in list(b.get_css_classes()):
            if c.startswith("sc-"):
                b.remove_css_class(c)
        b.add_css_class(f"sc-{tab.scheme_id}")
        (b.add_css_class if tab.admin else b.remove_css_class)("admin")
        for c in list(self.stack.get_css_classes()):
            if c.startswith("sc-"):
                self.stack.remove_css_class(c)
        cur = self.current()
        if cur is not None:
            self.stack.add_css_class(f"sc-{cur.scheme_id}")

    # ── 탭 ──
    def current(self):
        w = self.stack.get_visible_child()
        return w if isinstance(w, TermTab) else None

    def new_tab(self, argv=None, cwd=None, env=None, title=None, hold=False, profile=None):
        cur = self.current()
        if argv is None and profile is None:
            profile = self.cfg.default_profile()
        if cwd is None and cur is not None and self.cfg["new_tab_same_dir"] and not (profile or {}).get("cwd"):
            cwd = cur.cwd()                    # 새 탭은 지금 탭의 폴더에서 (GNOME 터미널처럼)
        if env is None and cur is not None:
            env = cur.env
        tab = TermTab(self, argv=argv, cwd=cwd, env=env, title=title, hold=hold, profile=profile)
        self.tabs.append(tab)
        self.stack.add_child(tab)
        btn = TabButton(self, tab)
        self.buttons[tab] = btn
        self.strip.append(btn)
        self._style_button(tab)
        self.select_tab(tab)
        self.tab_changed(tab)
        return tab

    def select_tab(self, tab):
        if tab not in self.tabs:
            return
        self.stack.set_visible_child(tab)
        for t, b in self.buttons.items():
            (b.add_css_class if t is tab else b.remove_css_class)("active")
        self._style_button(tab)
        self.set_title(tab.title())
        GLib.idle_add(lambda: (tab.term.grab_focus(), False)[1])
        btn = self.buttons[tab]
        GLib.idle_add(lambda: (self._scroll_to(btn), False)[1])

    def _scroll_to(self, btn):
        ok, bounds = btn.compute_bounds(self.strip)
        if ok:
            adj = self.strip_scroll.get_hadjustment()
            x = bounds.get_x()
            w = bounds.get_width()
            if x < adj.get_value():
                adj.set_value(x)
            elif x + w > adj.get_value() + adj.get_page_size():
                adj.set_value(x + w - adj.get_page_size())

    def move_tab_to_x(self, tab, x):
        """탭을 끌어 옮기는 중 — 지나간 탭의 가운데를 넘으면 자리를 바꾼다 (브라우저처럼)"""
        btn = self.buttons.get(tab)
        if btn is None or tab not in self.tabs:
            return
        others = [t for t in self.tabs if t is not tab]
        to = 0
        for t in others:
            ok, b = self.buttons[t].compute_bounds(self.strip)
            if ok and b.get_x() + b.get_width() / 2 < x:
                to += 1
        if to == self.tabs.index(tab):
            return
        self.tabs.remove(tab)
        self.tabs.insert(to, tab)
        self.strip.reorder_child_after(btn, self.buttons[self.tabs[to - 1]] if to else None)

    def step_tab(self, d):
        cur = self.current()
        if cur is None or len(self.tabs) < 2:
            return
        i = self.tabs.index(cur)
        self.select_tab(self.tabs[(i + d) % len(self.tabs)])

    def tab_changed(self, tab):
        btn = self.buttons.get(tab)
        if btn is None:
            return
        t = tab.title()
        btn.label.set_text(t)
        btn.set_tooltip_text(t)
        if tab is self.current():
            self.set_title(t)

    def tab_bell(self, tab):
        if tab is not self.current() or not self.is_active():
            self.set_urgency_hint(True) if hasattr(self, "set_urgency_hint") else None

    def close_tab(self, tab, ask=True):
        if tab not in self.tabs:
            return
        if ask:
            fg = tab.foreground()
            if fg:
                self._confirm(f"{fg} 이(가) 아직 실행 중입니다", "탭을 닫으면 이 프로그램도 끝납니다.", "닫기",
                              lambda: self.close_tab(tab, ask=False))
                return
        i = self.tabs.index(tab)
        was_current = tab is self.current()
        tab.hangup()
        self.tabs.remove(tab)
        self.strip.remove(self.buttons.pop(tab))
        self.stack.remove(tab)
        if not self.tabs:
            self._closing = True
            self.close()
            return
        if self._closing or self.get_application() is None:
            return        # 창을 닫는 중 — 남은 탭의 셸이 끝나며 들어온다. 고를 탭이 없다 (예전엔 여기서 오류)
        if was_current:
            self.select_tab(self.tabs[min(i, len(self.tabs) - 1)])

    def close_others(self, keep):
        others = [t for t in self.tabs if t is not keep]
        busy = [t.foreground() for t in others if t.foreground()]

        def go():
            for t in others:
                self.close_tab(t, ask=False)
        if busy:
            self._confirm("다른 탭을 모두 닫을까요?", f"실행 중인 프로그램({', '.join(busy)})도 끝납니다.", "닫기", go)
        else:
            go()

    def duplicate_tab(self, tab):
        if tab.cmd_mode and tab.profile.get("kind") == "shell":
            self.new_tab(argv=tab.argv, cwd=tab.cwd(), env=tab.env, title=tab.fixed_title)   # -e 로 연 명령
        else:
            self.new_tab(cwd=tab.cwd(), env=tab.env, title=tab.fixed_title, profile=tab.profile)

    def rename_tab(self, tab):
        btn = self.buttons.get(tab)
        if btn is None:
            return
        pop = Gtk.Popover()
        box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        e = Gtk.Entry(text=tab.fixed_title or tab.title(), width_chars=24)
        e.set_placeholder_text("비우면 프로그램이 정한 제목")
        box.append(e)
        pop.set_child(box)
        pop.set_parent(btn)

        def done(*_):
            tab.fixed_title = e.get_text().strip() or None
            self.tab_changed(tab)
            pop.popdown()
        e.connect("activate", done)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()
        e.grab_focus()

    def tab_menu(self, btn, x, y):
        self._menu_tab = btn.tab
        m = Gio.Menu()
        sec = Gio.Menu()
        mitem(sec, "탭 이름 바꾸기…", "win.rename-tab")
        mitem(sec, "탭 복제\tCtrl+Shift+D", "win.dup-tab")
        m.append_section(None, sec)
        sec = Gio.Menu()
        mitem(sec, "탭 닫기\tCtrl+Shift+W", "win.close-menu-tab")
        if len(self.tabs) > 1:
            mitem(sec, "다른 탭 닫기", "win.close-others")
        m.append_section(None, sec)
        self._popup(m, btn, x, y)

    def context_menu(self, tab, x, y, link):
        self._menu_link = link
        self._menu_tab = tab
        m = Gio.Menu()
        if link:
            sec = Gio.Menu()
            mitem(sec, "링크 열기", "win.open-link")
            mitem(sec, "링크 주소 복사", "win.copy-link")
            m.append_section(None, sec)
        sec = Gio.Menu()
        if tab.term.get_has_selection():
            mitem(sec, "복사\tCtrl+C", "win.copy")
        mitem(sec, "붙여넣기\tCtrl+V", "win.paste")
        mitem(sec, "모두 선택", "win.select-all")
        m.append_section(None, sec)
        sec = Gio.Menu()
        mitem(sec, "찾기…\tCtrl+Shift+F", "win.find")
        m.append_section(None, sec)
        sec = Gio.Menu()
        mitem(sec, "새 탭\tCtrl+Shift+T", "win.new-tab")
        mitem(sec, "새 창\tCtrl+Shift+N", "win.new-window")
        mitem(sec, "탭 닫기\tCtrl+Shift+W", "win.close-tab")
        m.append_section(None, sec)
        self._popup(m, tab.term, x, y)

    def _popup(self, model, widget, x, y):
        pop = Gtk.PopoverMenu.new_from_model(model)
        pop.set_has_arrow(False)
        pop.set_halign(Gtk.Align.START)
        pop.set_parent(widget)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

    # ── 창 ──
    def new_window(self):
        app = self.get_application()
        cur = self.current()
        w = TermWindow(app)
        w.new_tab(cwd=cur.cwd() if cur and self.cfg["new_tab_same_dir"] else None, env=cur.env if cur else None)
        w.present()

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def open_uri(self, uri):
        open_uri(self, uri)

    def about(self):
        d = Gtk.AboutDialog(transient_for=self, modal=True)
        d.set_program_name("Nenerobo")
        from . import __version__
        d.set_version(__version__)
        d.set_comments("SekaiOS 터미널 — 명령하면 움직이는 기계.\n"
                       "터미널 엔진: VTE · 화면: GTK4 (GPU 가 있으면 GPU 로 그립니다)")
        d.set_logo_icon_name("utilities-terminal")
        d.set_license_type(Gtk.License.APACHE_2_0)
        d.set_website("https://github.com/caterpillar321/sekaios")
        d.present()

    def _confirm(self, title, text, ok_label, on_ok):
        d = Gtk.AlertDialog(message=title, detail=text, buttons=["취소", ok_label], cancel_button=0, default_button=1,
                            modal=True)

        def done(dlg, res):
            try:
                if dlg.choose_finish(res) == 1:
                    on_ok()
            except GLib.Error:
                pass
        d.choose(self, None, done)

    def _close_request(self, *_):
        if self._closing or not self.tabs:
            return False
        busy = [n for n in (t.foreground() for t in self.tabs) if n]
        if (len(self.tabs) > 1 and self.cfg["confirm_close"]) or busy:
            what = f"탭 {len(self.tabs)}개를 모두 닫을까요?" if len(self.tabs) > 1 else "창을 닫을까요?"
            more = f"실행 중인 프로그램({', '.join(busy)})도 끝납니다." if busy else "모든 탭의 셸이 끝납니다."

            def go():
                self._closing = True
                for t in list(self.tabs):
                    t.hangup()
                self.close()
            self._confirm(what, more, "모두 닫기", go)
            return True
        for t in self.tabs:
            t.hangup()
        return False

    # ── 키 ──
    def _key(self, _ctrl, keyval, code, state):
        tab = self.current()
        if tab is not None and tab.wait_close and tab.term.has_focus():
            self.close_tab(tab, ask=False)
            return True
        mods = state & (C | S | A)
        k = Gdk.keyval_to_lower(keyval)
        if mods == C:
            if k == Gdk.KEY_comma:
                self.get_application().open_settings(self)
                return True
            if k == Gdk.KEY_c:
                return bool(tab and tab.term.has_focus() and tab.copy())   # 고른 글이 없으면 ^C 를 프로그램에
            if k == Gdk.KEY_v and tab and tab.term.has_focus():
                tab.paste()
                return True
            if k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                tab and tab.zoom(1)
                return True
            if k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                tab and tab.zoom(-1)
                return True
            if k in (Gdk.KEY_0, Gdk.KEY_KP_0):
                tab and tab.zoom(0)
                return True
            if k == Gdk.KEY_Tab or k == Gdk.KEY_Page_Down:
                self.step_tab(1)
                return True
            if k == Gdk.KEY_Page_Up:
                self.step_tab(-1)
                return True
        elif mods == C | S:
            if 10 <= code <= 18:                       # 숫자 줄의 1~9 (Shift 를 누르면 글자가 ! @ # … 라 자판 자리로)
                ps = self.cfg.profiles()
                if code - 10 < len(ps):
                    self.new_tab(profile=ps[code - 10])
                return True
            if k == Gdk.KEY_c:
                tab and tab.copy()
                return True
            if k == Gdk.KEY_v:
                tab and tab.paste()
                return True
            if k == Gdk.KEY_t:
                self.new_tab()
                return True
            if k == Gdk.KEY_w:
                tab and self.close_tab(tab)
                return True
            if k == Gdk.KEY_d:
                tab and self.duplicate_tab(tab)
                return True
            if k == Gdk.KEY_n:
                self.new_window()
                return True
            if k == Gdk.KEY_f:
                tab and tab.show_search()
                return True
            if k in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Tab):
                self.step_tab(-1)
                return True
            if k in (Gdk.KEY_plus, Gdk.KEY_equal):
                tab and tab.zoom(1)
                return True
        elif mods == C | A and Gdk.KEY_1 <= k <= Gdk.KEY_9:
            n = k - Gdk.KEY_1
            if n < len(self.tabs):
                self.select_tab(self.tabs[-1] if k == Gdk.KEY_9 else self.tabs[n])
            return True
        elif mods == A and k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.toggle_fullscreen()
            return True
        elif mods == 0 and k == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True
        return False
