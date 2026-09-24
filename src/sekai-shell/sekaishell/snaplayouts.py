"""스냅 레이아웃 — 최대화 버튼에 마우스를 올리면 배치 그림이 뜨고, 칸을 누르면 그 자리로 (윈도우 11).

hyprbars(패치)가 최대화 버튼 올림/벗어남을 "sekaimaxhover>>on,주소,x,y" / "off,주소" 로 알린다.
잠깐(0.4초) 머물러야 뜨고, 버튼과 이 팝업 둘 다에서 벗어나면 닫힌다.
칸 이름은 WindowManager.zone_rect 의 영역 이름을 그대로 쓴다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from .layer import GtkLayerShell  # noqa: E402

E = GtkLayerShell.Edge

# 레이아웃: 열(column) 목록. 열 = (너비 비율, [그 열의 칸 영역 이름…])
LAYOUTS = [
    [(1, ["left"]), (1, ["right"])],                 # 반반
    [(2, ["l23"]), (1, ["r13"])],                    # 2/3 + 1/3
    [(1, ["c1"]), (1, ["c2"]), (1, ["c3"])],         # 3등분
    [(1, ["left"]), (1, ["tr", "br"])],              # 반쪽 + 4분의 1 두 개
    [(1, ["tl", "bl"]), (1, ["tr", "br"])],          # 4분할
]
FRAME_W, FRAME_H = 96, 60
SHOW_DELAY, HIDE_DELAY = 400, 350


class SnapLayouts(Gtk.Window):
    def __init__(self, wm):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.wm = wm
        self.addr = None
        self.inside = False
        self._show_src = self._hide_src = 0
        self.get_style_context().add_class("snap-layouts-win")
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, E.TOP, True)
        GtkLayerShell.set_anchor(self, E.LEFT, True)

        card = Gtk.FlowBox()
        card.set_selection_mode(Gtk.SelectionMode.NONE)
        card.set_max_children_per_line(3)
        card.set_min_children_per_line(3)
        card.set_row_spacing(10)
        card.set_column_spacing(10)
        card.get_style_context().add_class("snap-layouts")
        for lay in LAYOUTS:
            card.add(self._frame(lay))

        ev = Gtk.EventBox()
        ev.add(card)
        ev.connect("enter-notify-event", self._enter)
        ev.connect("leave-notify-event", self._leave)
        self.add(ev)

    def _frame(self, layout):
        """레이아웃 하나 = 작은 화면 모양 안에 누를 수 있는 칸들"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        row.get_style_context().add_class("snap-frame")
        row.set_size_request(FRAME_W, FRAME_H)
        total = sum(w for w, _ in layout)
        for w, zones in layout:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            col.set_size_request(int((FRAME_W - 6 - 3 * (len(layout) - 1)) * w / total), -1)
            for z in zones:
                b = Gtk.Button()
                b.get_style_context().add_class("snap-zone")
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.set_can_focus(False)
                rest = [o for _w, zs in layout for o in zs if o != z]    # 스냅 도우미가 채울 나머지 칸
                b.connect("clicked", lambda _b, z=z, r=rest: self._pick(z, r))
                col.pack_start(b, True, True, 0)
            row.pack_start(col, w > 1, True, 0)
        return row

    # ── 보이기 / 감추기 ──
    def hover(self, on, addr, x=0, y=0):
        if on:
            self._cancel("_hide_src")
            self._cancel("_show_src")
            self._show_src = GLib.timeout_add(SHOW_DELAY, self._show, addr, x, y)
        else:
            self._cancel("_show_src")
            self._later_hide()

    def _show(self, addr, x, y):
        self._show_src = 0
        self.addr = addr
        disp = Gdk.Display.get_default()
        mon = None
        for i in range(disp.get_n_monitors()):
            g = disp.get_monitor(i).get_geometry()
            if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
                mon = disp.get_monitor(i)
        if mon is None:
            return False
        g = mon.get_geometry()
        self.show_all()
        w, h = self.get_preferred_size()[1].width, self.get_preferred_size()[1].height
        left = min(max(g.x + 8, x - w // 2), g.x + g.width - w - 8) - g.x
        top = min(y + 4, g.y + g.height - h - 8) - g.y
        GtkLayerShell.set_monitor(self, mon)
        GtkLayerShell.set_margin(self, E.LEFT, int(left))
        GtkLayerShell.set_margin(self, E.TOP, int(top))
        return False

    def _enter(self, _w, ev):
        if ev.detail != Gdk.NotifyType.INFERIOR:
            self.inside = True
            self._cancel("_hide_src")
        return False

    def _leave(self, _w, ev):
        if ev.detail != Gdk.NotifyType.INFERIOR:
            self.inside = False
            self._later_hide()
        return False

    def _later_hide(self):
        self._cancel("_hide_src")
        self._hide_src = GLib.timeout_add(HIDE_DELAY, self._hide)

    def _hide(self):
        self._hide_src = 0
        if not self.inside:
            self.hide()
        return False

    def _cancel(self, name):
        src = getattr(self, name)
        if src:
            GLib.source_remove(src)
            setattr(self, name, 0)

    def _pick(self, zone, rest):
        addr = self.addr
        self.inside = False
        self.hide()
        if addr:
            self.wm.snap_to(addr, zone, rest=rest)


class TopLayoutBar(Gtk.Window):
    """창을 화면 위쪽 가운데로 끌면 위에서 내려오는 레이아웃 바 (윈도우 11).

    끄는 동안은 Hyprland 가 포인터를 잡고 있어 이 창이 마우스 이벤트를 못 받는다.
    그래서 WindowManager 가 커서 위치를 읽어 zone_at() 으로 어느 칸 위인지 묻고,
    놓으면 그 칸으로 배치한다."""

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("snap-layouts-win")
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-snapbar")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, E.TOP, True)          # 위에만 붙이면 가로 가운데에 놓인다
        GtkLayerShell.set_margin(self, E.TOP, 8)
        self.zones = []            # [(버튼, 영역, 나머지 칸들)]
        self.hot = None
        self.mon_geo = None
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("snap-layouts")
        for lay in LAYOUTS:
            row.pack_start(self._frame(lay), False, False, 0)
        self.add(row)
        # 입력이 통과하게 — 커서 밑에 이 창이 있으면 hyprbars 가 "놓음"을 처리하지 않는다
        self.connect("realize", self._no_input)

    @staticmethod
    def _no_input(w):
        gw = w.get_window()
        if gw is not None:
            import cairo
            gw.input_shape_combine_region(cairo.Region(), 0, 0)

    def _frame(self, layout):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        box.get_style_context().add_class("snap-frame")
        box.set_size_request(FRAME_W, FRAME_H)
        total = sum(w for w, _ in layout)
        for w, zones in layout:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            col.set_size_request(int((FRAME_W - 6 - 3 * (len(layout) - 1)) * w / total), -1)
            for z in zones:
                cell = Gtk.Box()
                cell.get_style_context().add_class("snap-zone-cell")
                col.pack_start(cell, True, True, 0)
                rest = [o for _w, zs in layout for o in zs if o != z]
                self.zones.append((cell, z, rest))
            box.pack_start(col, w > 1, True, 0)
        return box

    def show_on(self, geo):
        """geo = 그 모니터의 Gdk 사각형"""
        disp = Gdk.Display.get_default()
        for i in range(disp.get_n_monitors()):
            m = disp.get_monitor(i)
            g = m.get_geometry()
            if (g.x, g.y) == (geo.x, geo.y):
                GtkLayerShell.set_monitor(self, m)
        self.mon_geo = geo
        self.set_hot(None)
        self.show_all()

    def zone_at(self, x, y):
        """전체 화면 좌표 (x, y) 가 가리키는 칸 → (영역, 나머지 칸들) 또는 None"""
        if not self.get_visible() or self.mon_geo is None:
            return None
        g = self.mon_geo
        ww = self.get_allocated_width()
        wx, wy = g.x + (g.width - ww) // 2, g.y + 8
        for cell, zone, rest in self.zones:
            p = cell.translate_coordinates(self, 0, 0)
            if not p:
                continue
            cx, cy = wx + p[0], wy + p[1]
            if cx <= x < cx + cell.get_allocated_width() and cy <= y < cy + cell.get_allocated_height():
                return zone, rest
        return None

    def set_hot(self, zone):
        if zone == self.hot:
            return
        self.hot = zone
        for cell, z, _r in self.zones:
            ctx = cell.get_style_context()
            (ctx.add_class if z == zone else ctx.remove_class)("hot")
