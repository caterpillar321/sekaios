"""창 메뉴 — 제목줄을 오른쪽 클릭하면 뜨는 복원·최소화·최대화·닫기 (윈도우의 시스템 메뉴).

WorldLink 가 hyprbars 막대의 오른쪽 클릭과 앱이 그린 제목줄의 xdg_toplevel.show_window_menu 를
"sekaiwinmenu>>주소,x,y" 이벤트로 보내면 WindowManager 가 open_for() 를 부른다. 좌표는 Hyprland 논리 좌표.
GtkMenu 는 레이어셸 표면이 없으면 띄울 수 없어서 트레이 메뉴처럼 OVERLAY 층 창으로 그린다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from .layer import GtkLayerShell  # noqa: E402
from .popup import ClickCatcher, make_translucent  # noqa: E402

E = GtkLayerShell.Edge


class WindowMenu(Gtk.Window):
    def __init__(self, wm):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.wm = wm
        ctx = self.get_style_context()
        ctx.add_class("panel-popup")
        ctx.add_class("tray-menu")
        make_translucent(self)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, E.TOP, True)
        GtkLayerShell.set_anchor(self, E.LEFT, True)
        GtkLayerShell.set_exclusive_zone(self, -1)        # 작업 표시줄 자리와 상관없이 좌표 그대로
        # ON_DEMAND — 뜨면서 키보드를 받는다(↑↓·Enter·Esc). EXCLUSIVE 는 Hyprland 가 포인터까지 이 층에만 줘서
        #   바깥을 눌러도 닫히지 않았다 (트레이 메뉴와 같게)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        self.catcher = ClickCatcher(self.close, dim=False)
        self.connect("key-press-event", self._key)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.root.get_style_context().add_class("popup-root")
        self.add(self.root)

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def close(self):
        self.hide()
        self.catcher.hide()

    def _row(self, text, accel, enabled, action):
        row = Gtk.Button()
        row.get_style_context().add_class("menu-item")
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.set_sensitive(enabled)
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        h.pack_start(Gtk.Label(label=text, xalign=0), True, True, 0)
        if accel:
            a = Gtk.Label(label=accel, xalign=1)
            a.get_style_context().add_class("menu-accel")
            h.pack_end(a, False, False, 0)
        row.add(h)

        def clicked(_b):
            self.close()
            # 메뉴가 키보드를 쥔 채면 Hyprland 가 창에 초점을 주지 않는다(최대화·복원은 초점 창에 건다) — 닫힌 뒤에
            GLib.timeout_add(60, lambda: action() and False)
        row.connect("clicked", clicked)
        self.root.pack_start(row, False, False, 0)
        return row

    def open_for(self, addr, x, y):
        c = self.wm._client(addr)
        if not c or c.get("fullscreen", 0) == 2:
            return False                                  # 이미 닫혔거나 전체 화면(F11·동영상)
        for ch in self.root.get_children():
            self.root.remove(ch)
        maxed = c.get("fullscreen", 0) == 1
        snapped = addr in self.wm.snapped and addr in self.wm.saved
        fixed = bool(c.get("sekaiFixed"))
        wm = self.wm

        def restore():
            if maxed:
                wm._toggle_max(addr)
            else:
                wm._restore(addr)

        first = [
            self._row("복원", "", maxed or snapped, restore),
            self._row("최소화", "", True,
                      lambda: wm.hypr.dispatch(f"movetoworkspacesilent special:min,address:{addr}")),
            self._row("최대화", "", not maxed and not fixed,
                      lambda: wm.snap_to(addr, "max", assist=False)),
        ]
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("menu-sep")
        self.root.pack_start(sep, False, False, 0)
        close = self._row("닫기", "Alt+F4", True,
                          lambda: wm.hypr.dispatch(f"closewindow address:{addr}"))
        close.get_style_context().add_class("menu-bold")
        self.root.show_all()

        # 커서가 있는 모니터 — 메뉴가 화면 밖으로 나가면 커서의 왼쪽·위로 연다
        disp = Gdk.Display.get_default()
        mon = None
        for i in range(disp.get_n_monitors()):
            g = disp.get_monitor(i).get_geometry()
            if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
                mon = disp.get_monitor(i)
                break
        mon = mon or disp.get_primary_monitor() or disp.get_monitor(0)
        g = mon.get_geometry()
        self.resize(1, 1)
        _m, nat = self.get_preferred_size()
        w, h = max(nat.width, 200), nat.height
        lx, ly = x - g.x, y - g.y
        if lx + w > g.width:
            lx = max(0, lx - w)
        if ly + h > g.height:
            ly = max(0, ly - h)
        GtkLayerShell.set_monitor(self, mon)
        GtkLayerShell.set_monitor(self.catcher, mon)
        GtkLayerShell.set_margin(self, E.LEFT, int(lx))
        GtkLayerShell.set_margin(self, E.TOP, int(ly))
        self.set_size_request(200, -1)
        self.catcher.show_all()
        self.show_all()
        self.present()
        # 처음 고를 수 있는 항목에 초점 (↑↓ 로 옮긴다)
        for r in first + [close]:
            if r.get_sensitive():
                r.grab_focus()
                break
        return False
