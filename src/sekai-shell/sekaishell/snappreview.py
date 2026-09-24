"""끌어서 스냅 미리보기 — 창을 화면 가장자리로 끌면 놓을 자리를 반투명하게 보여 준다 (윈도우 11).

hyprbars(패치)가 끄는 동안 "sekaisnap>>영역,모니터" 이벤트를 보내면 WindowManager 가
show(x, y, w, h, 모니터) / hide() 를 부른다. 좌표는 Hyprland 논리 좌표(전체 화면 기준).
입력을 받지 않는 OVERLAY 층 창이라 끌기를 방해하지 않는다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

from .layer import GtkLayerShell  # noqa: E402

E = GtkLayerShell.Edge


class SnapPreview(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("snap-preview-win")
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_app_paintable(False)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-snap")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)      # 작업 표시줄 자리와 상관없이 좌표 그대로
        GtkLayerShell.set_anchor(self, E.TOP, True)
        GtkLayerShell.set_anchor(self, E.LEFT, True)
        box = Gtk.Box()
        box.get_style_context().add_class("snap-preview")
        self.add(box)
        # 입력이 이 창에 걸리지 않게 (끄는 중인 창 아래로 통과)
        self.connect("realize", self._no_input)

    @staticmethod
    def _no_input(w):
        gw = w.get_window()
        if gw is not None:
            try:
                import cairo
                gw.input_shape_combine_region(cairo.Region(), 0, 0)
            except Exception:
                pass

    def show_at(self, x, y, w, h):
        disp = Gdk.Display.get_default()
        mon = None
        for i in range(disp.get_n_monitors()):
            g = disp.get_monitor(i).get_geometry()
            if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
                mon = disp.get_monitor(i)
                break
        if mon is None:
            return
        g = mon.get_geometry()
        GtkLayerShell.set_monitor(self, mon)
        GtkLayerShell.set_margin(self, E.LEFT, int(x - g.x))
        GtkLayerShell.set_margin(self, E.TOP, int(y - g.y))
        self.set_size_request(max(1, int(w)), max(1, int(h)))
        self.resize(max(1, int(w)), max(1, int(h)))
        self.show_all()

    def hide_now(self):
        self.hide()
