"""스냅 도우미 — 창 하나를 스냅하면 남은 칸에 다른 창들을 보여 주고, 고르면 그 칸을 채운다 (윈도우 11).

  반쪽      → 반대쪽 반쪽
  4분의 1   → 나머지 세 칸을 차례로
  레이아웃  → 그 레이아웃의 나머지 칸을 차례로
윈도우 11 은 창 미리보기 그림을 보여 주지만, 여기서는 앱 아이콘과 창 제목 카드로 보여 준다.
바깥을 누르거나 Esc 를 누르면 닫힌다. 고를 창이 없으면 뜨지 않는다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango  # noqa: E402

from .layer import GtkLayerShell  # noqa: E402
from .popup import ClickCatcher  # noqa: E402
from .appicon import app_icon  # noqa: E402

E = GtkLayerShell.Edge

# 드래그·Win+화살표로 스냅했을 때 남은 칸
REST = {"left": ["right"], "right": ["left"],
        "tl": ["tr", "bl", "br"], "tr": ["tl", "bl", "br"],
        "bl": ["tl", "tr", "br"], "br": ["tl", "tr", "bl"]}


class SnapAssist(Gtk.Window):
    def __init__(self, wm):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.wm = wm
        self.queue = []            # 채울 칸들
        self.used = set()          # 이미 배치한 창 주소
        self.mon = None
        self.get_style_context().add_class("snap-assist-win")
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_anchor(self, E.TOP, True)
        GtkLayerShell.set_anchor(self, E.LEFT, True)
        self.connect("key-press-event", self._key)
        self.catcher = ClickCatcher(self.close, dim=False)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.box.get_style_context().add_class("snap-assist")
        self.add(self.box)

    # ── 시작 ──
    def start(self, addr, zone, rest=None, mon_name=None):
        """addr 창을 zone 에 스냅한 직후 부른다. rest 가 없으면 REST 표를 쓴다."""
        self.queue = list(rest if rest is not None else REST.get(zone, []))
        self.used = {addr}
        self.mon = mon_name
        self._next()

    def _candidates(self):
        """고를 수 있는 창 — 지금 작업 공간의 보이는 일반 창 (이미 배치한 것 제외), 최근 쓴 순서"""
        cur = (self.wm.hypr.query("activeworkspace") or {}).get("id")
        out = []
        for c in self.wm.hypr.query("clients") or []:
            ws = c.get("workspace") or {}
            if c.get("address") in self.used or ws.get("id") != cur or not c.get("mapped", True):
                continue
            if c.get("hidden") or (c.get("class") or "") in ("", "sekai-desk"):
                continue
            out.append(c)
        out.sort(key=lambda c: c.get("focusHistoryID", 999))
        return out

    def _next(self):
        cands = self._candidates()
        if not self.queue or not cands:
            self.close()
            return
        zone = self.queue.pop(0)
        rect = self.wm.zone_rect(zone, mon_name=self.mon)
        if not rect:
            self.close()
            return
        self.zone = zone
        x, y, w, h = rect
        for ch in self.box.get_children():
            self.box.remove(ch)
        title = Gtk.Label(label="이 자리에 둘 창을 고르세요", xalign=0)
        title.get_style_context().add_class("snap-assist-title")
        self.box.pack_start(title, False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(max(1, int(w // 220)))
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_valign(Gtk.Align.START)          # 카드가 칸 높이만큼 늘어나지 않게
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_homogeneous(True)
        for c in cands[:12]:
            flow.add(self._card(c))
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(flow)
        self.box.pack_start(sw, True, True, 0)
        self._place(x, y, w, h)
        self.catcher.show_all()
        self.show_all()
        self.present()

    def _card(self, c):
        b = Gtk.Button()
        b.get_style_context().add_class("snap-card")
        b.set_relief(Gtk.ReliefStyle.NONE)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(app_icon(c.get("class"), 48), False, False, 4)
        t = Gtk.Label(label=c.get("title") or c.get("class") or "")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.set_max_width_chars(22)
        v.pack_start(t, False, False, 0)
        b.add(v)
        b.set_size_request(200, 120)
        b.connect("clicked", lambda _b, a=c["address"]: self._pick(a))
        return b

    def _place(self, x, y, w, h):
        disp = Gdk.Display.get_default()
        for i in range(disp.get_n_monitors()):
            m = disp.get_monitor(i)
            g = m.get_geometry()
            if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
                GtkLayerShell.set_monitor(self, m)
                GtkLayerShell.set_margin(self, E.LEFT, int(x - g.x))
                GtkLayerShell.set_margin(self, E.TOP, int(y - g.y))
                break
        self.set_size_request(int(w), int(h))

    # ── 고르기 / 닫기 ──
    def _pick(self, addr):
        self.used.add(addr)
        self.hide()
        self.wm.snap_to(addr, self.zone, mon_name=self.mon, assist=False)
        self._next()

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def close(self):
        self.queue = []
        self.hide()
        self.catcher.hide()
