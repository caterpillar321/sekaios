"""레이어셸 팝업 창과 클릭 캐처.

Wayland 에서는 GTK Popover / GtkMenu 를 레이어셸 표면에 붙이면
z-순서가 꼬여 배경화면 아래로 내려가 버린다.
그래서 팝업은 전부 독립 layer-shell 창(OVERLAY)으로 만들고,
바깥 클릭은 화면 전체를 덮는 투명 레이어(ClickCatcher)로 받는다.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

from .layer import GtkLayerShell
from . import dbg

E = GtkLayerShell.Edge


def make_translucent(win):
    """창 배경을 투명하게 — CSS 의 반투명 면이 뒤의 (흐려진) 화면 위에 그려지게.
    둥근 모서리 바깥도 비워져서 모서리가 깔끔해진다."""
    vis = Gdk.Screen.get_default().get_rgba_visual()
    if vis:
        win.set_visual(vis)
    win.set_app_paintable(False)
ALL_EDGES = (E.TOP, E.BOTTOM, E.LEFT, E.RIGHT)


class ClickCatcher(Gtk.Window):
    """팝업 바깥 클릭을 받아내는 전체 화면 반투명 레이어.

    패널(정상 동작)과 같은 구조여야 한다:
      · app_paintable 대신 CSS 배경으로 칠한다
      · 실제 GdkWindow 를 가지는 위젯(EventBox)을 자식으로 둔다
    app_paintable(True) + 직접 그리기는 레이어셸 표면에서
    포인터 이벤트를 전혀 받지 못한다.
    """

    def __init__(self, on_click, dim=True):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_click = on_click
        self.get_style_context().add_class("click-catcher")
        if not dim:
            self.get_style_context().add_class("no-dim")

        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_decorated(False)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-catcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        for e in ALL_EDGES:
            GtkLayerShell.set_anchor(self, e, True)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        area = Gtk.EventBox()
        area.get_style_context().add_class("catcher-area")
        if not dim:
            area.get_style_context().add_class("no-dim")
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        area.connect("button-press-event", self._hit)
        area.add(Gtk.Fixed())
        self.add(area)

    def _hit(self, _w, ev):
        dbg(f"[catcher] click {ev.x:.0f},{ev.y:.0f}")
        self.on_click()
        return True


class PanelPopup(Gtk.Window):
    """패널에서 올라오는 팝업 창.

    anchor_edge  가로 방향 기준 모서리 (기본 RIGHT)
    margin       기준 모서리에서 띄울 여백
    x_margin     가로 여백을 따로 줄 때 (트레이 아이콘 위치 맞추기 등)
    dim          바깥 영역을 어둡게 할지
    keyboard     키보드 모드 (기본 ON_DEMAND, 열자마자 입력을 받으려면 EXCLUSIVE)
    """

    def __init__(self, anchor_edge=None, margin=8, x_margin=None, dim=True, keyboard=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("panel-popup")
        edge = anchor_edge or E.RIGHT
        make_translucent(self)

        GtkLayerShell.init_for_window(self)
        # 이름표로 Hyprland 가 이 창 뒤를 흐리게 한다 (layerrule = blur, sekai-popup)
        GtkLayerShell.set_namespace(self, "sekai-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, E.BOTTOM, True)
        GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_margin(self, E.BOTTOM, margin)
        GtkLayerShell.set_margin(self, edge, margin if x_margin is None else x_margin)
        # 키보드 모드는 한 번만 정한다 (두 번 바꾸면 두 번째가 무시될 때가 있다)
        GtkLayerShell.set_keyboard_mode(self, keyboard or GtkLayerShell.KeyboardMode.ON_DEMAND)
        self._edge = edge

        self.catcher = ClickCatcher(self.close, dim=dim)
        self.connect("key-press-event", self._key)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.get_style_context().add_class("popup-root")
        self.add(self.root)

    def set_x_margin(self, px):
        GtkLayerShell.set_margin(self, self._edge, max(0, int(px)))

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def close(self):
        self.hide()
        self.catcher.hide()

    def toggle(self):
        if self.get_visible():
            self.close()
        else:
            self.open()

    def open(self):
        self.on_open()
        self.catcher.show_all()
        self.show_all()
        self.present()

    def on_open(self):
        pass
