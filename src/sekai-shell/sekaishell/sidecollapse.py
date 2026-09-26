"""좁은 창에서 왼쪽 목록(사이드바) 접기 — 윈도우 11 설정·작업 관리자처럼.

창이 좁아지면 사이드바를 숨기고 ≡ 단추(self.button — 부르는 쪽이 내용 위쪽 줄에 넣는다)를 보인다.
누르면 사이드바가 내용 위에 떠서 왼쪽에서 밀려 나오고, 항목을 고르거나 바깥을 누르거나 Esc 를 누르면
닫힌다. 넓어지면 제자리로 돌아간다. 사이드바가 창의 최소 폭을 잡아먹지 않아 화면 반쪽·3분할 스냅에 들어간다.

    sc = SideCollapse(win, side, main, threshold=760)
    win.add(sc.widget)                 # [사이드바 | 내용] 가로 상자 대신
    top_bar.pack_start(sc.button, …)   # ≡ 단추 자리
    sc.close_on(listbox)               # 이 목록에서 고르면 닫힌다
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class _Burger(Gtk.DrawingArea):
    """≡ — 선 세 개를 직접 그린다 (아이콘 테마마다 open-menu 모양이 달라 ⋮ 로 보이기도 했다)"""

    def __init__(self, size=16):
        super().__init__()
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        c = w.get_style_context().get_color(w.get_state_flags())
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.set_line_width(1.4)
        x0, x1 = round(W * 0.12) + 0.5, round(W * 0.88) - 0.5
        for f in (0.28, 0.5, 0.72):
            y = round(H * f) + 0.5
            cr.move_to(x0, y)
            cr.line_to(x1, y)
        cr.stroke()
        return False


class _FoldRow(Gtk.Container):
    """[사이드바 | 내용]. 최소 폭은 내용의 것만 낸다 — 사이드바는 창이 좁아지면 접히므로 창을 붙잡지 않는다.
    (Gtk.Box 면 둘의 최소 폭을 더해, 한 번에 좁게 스냅하면 창이 그 합에서 멈췄다 — 접힌 뒤에도 그대로)"""

    def __init__(self, slot, main):
        super().__init__()
        self.set_has_window(False)
        self._kids = []
        self.slot, self.main = slot, main
        self.add(slot)
        self.add(main)

    def do_add(self, w):
        self._kids.append(w)
        w.set_parent(self)
        self.queue_resize()

    def do_remove(self, w):
        if w in getattr(self, "_kids", ()):
            self._kids.remove(w)
            w.unparent()
            self.queue_resize()

    def do_forall(self, include_internals, callback, *data):
        for w in list(getattr(self, "_kids", ())):
            callback(w, *data)

    def do_child_type(self):
        return Gtk.Widget.__gtype__

    def _side_w(self, width=None):
        if not self.slot.get_visible():
            return 0
        sm, sn = self.slot.get_preferred_width()
        return sn if width is None else max(0, min(sn, width))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self):
        mm, mn = self.main.get_preferred_width()
        return mm, self._side_w() + mn

    def do_get_preferred_height_for_width(self, width):
        sw = self._side_w(width)
        mm, mn = self.main.get_preferred_height_for_width(max(1, width - sw))
        if sw:
            sm, sn = self.slot.get_preferred_height_for_width(sw)
            return max(mm, sm), max(mn, sn)
        return mm, mn

    def do_get_preferred_height(self):
        return self.do_get_preferred_height_for_width(self.do_get_preferred_width()[1])

    def do_get_preferred_width_for_height(self, _height):
        return self.do_get_preferred_width()

    def do_size_allocate(self, a):
        self.set_allocation(a)
        sw = self._side_w(a.width)
        if sw:
            sa = Gdk.Rectangle()
            sa.x, sa.y, sa.width, sa.height = a.x, a.y, sw, a.height
            self.slot.size_allocate(sa)
        ma = Gdk.Rectangle()
        ma.x, ma.y, ma.width, ma.height = a.x + sw, a.y, max(1, a.width - sw), a.height
        self.main.size_allocate(ma)


CSS = """
.side-scrim { background: alpha(#000000, 0.28); }
.side-drawer .sidebar { box-shadow: 6px 0 18px alpha(#000000, 0.35); }
button.side-toggle { padding: 6px 8px; margin: 6px 4px 6px 8px; border-radius: 6px; }
"""
_css_done = [False]


def _load_css():
    if _css_done[0]:
        return
    _css_done[0] = True
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)


class SideCollapse:
    GAP = 40                     # 넓힐 때는 이만큼 더 — 경계에서 접었다 폈다 깜박이지 않게

    def __init__(self, win, side, main, threshold, on_change=None):
        _load_css()
        self.win, self.side, self.main = win, side, main
        self.threshold = threshold
        self.on_change = on_change            # on_change(접혔나) — 부르는 쪽이 ≡ 줄을 보이거나 숨긴다
        self.collapsed = False
        self._pending = 0

        self.slot = Gtk.Box()
        self.slot.pack_start(side, True, True, 0)
        self.row = _FoldRow(self.slot, main)

        self.widget = Gtk.Overlay()
        self.widget.add(self.row)
        # 펼친 사이드바 뒤의 막 — 누르면 닫힌다
        self.scrim = Gtk.EventBox()
        self.scrim.get_style_context().add_class("side-scrim")
        self.scrim.set_no_show_all(True)
        self.scrim.connect("button-press-event", lambda *_: (self.close(), True)[1])
        self.widget.add_overlay(self.scrim)
        self.drawer = Gtk.Revealer()
        self.drawer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.drawer.set_transition_duration(160)
        self.drawer.set_halign(Gtk.Align.START)
        self.drawer.set_valign(Gtk.Align.FILL)
        self.drawer.get_style_context().add_class("side-drawer")
        self.drawer.connect("notify::child-revealed", self._revealed)
        self.widget.add_overlay(self.drawer)

        self.button = Gtk.Button()
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.button.set_focus_on_click(False)
        self.button.get_style_context().add_class("side-toggle")
        self.button.add(_Burger(16))
        self.button.set_tooltip_text("메뉴 열기")
        self.button.set_valign(Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: self.toggle())
        self.button.get_child().show_all()
        self.button.set_no_show_all(True)

        win.connect("size-allocate", self._on_alloc)
        win.connect("key-press-event", self._on_key)

    # ── 목록에서 고르면 닫기 ──
    def close_on(self, listbox):
        listbox.connect("row-activated", lambda *_: self.close())
        listbox.connect("row-selected", lambda _l, r: r is not None and self.close())

    # ── 폭에 따라 ──
    def _on_alloc(self, _w, alloc):
        want = None
        if not self.collapsed and alloc.width < self.threshold:
            want = True
        elif self.collapsed and alloc.width >= self.threshold + self.GAP:
            want = False
        if want is not None and not self._pending:
            # 크기를 나누는 도중에 위젯을 옮기지 않는다 — 한 박자 뒤에
            self._pending = GLib.idle_add(self._apply, want)

    def _apply(self, collapse):
        self._pending = 0
        if collapse == self.collapsed:
            return False
        self.collapsed = collapse
        if collapse:
            self.slot.remove(self.side)
            self.slot.hide()
            self.drawer.add(self.side)
            self.drawer.set_reveal_child(False)
            self.drawer.show()
            self.button.show()
        else:
            self._close_now()
            self.drawer.remove(self.side)
            self.drawer.hide()
            self.slot.pack_start(self.side, True, True, 0)
            self.slot.show()
            self.button.hide()
        self.side.show()
        if self.on_change is not None:
            self.on_change(collapse)
        return False

    # ── 펼치기·닫기 ──
    def toggle(self):
        if self.drawer.get_reveal_child():
            self.close()
        else:
            self.open()

    def open(self):
        if not self.collapsed:
            return
        self.scrim.show()
        self.drawer.set_reveal_child(True)
        self.button.set_tooltip_text("메뉴 닫기")

    def close(self):
        if not self.collapsed or not self.drawer.get_reveal_child():
            return
        self.drawer.set_reveal_child(False)
        self.scrim.hide()
        self.button.set_tooltip_text("메뉴 열기")

    def _close_now(self):
        self.drawer.set_transition_duration(0)
        self.close()
        self.drawer.set_transition_duration(160)

    def _revealed(self, *_):
        if self.drawer.get_child_revealed():
            # 펼쳐지면 목록으로 초점 (키보드로 바로 고를 수 있게)
            self.side.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape and self.collapsed and self.drawer.get_reveal_child():
            self.close()
            return True
        return False
