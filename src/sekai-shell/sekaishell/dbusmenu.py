"""com.canonical.dbusmenu 클라이언트.

트레이 아이콘의 컨텍스트 메뉴는 거의 전부 이 규격으로 온다.
GtkMenu 는 레이어셸 표면 위에서 z-순서가 꼬이므로,
메뉴도 우리 레이어 창(MenuPopup)에 직접 그린다.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GdkPixbuf  # noqa: E402

from . import dbg
from . import dbusutil as D

IFACE = "com.canonical.dbusmenu"


class MenuNode:
    __slots__ = ("id", "props", "children")

    def __init__(self, mid, props, children):
        self.id = mid
        self.props = props or {}
        self.children = children or []

    # ── 속성 접근 ──
    @property
    def type(self):
        return self.props.get("type", "standard")

    @property
    def label(self):
        # dbusmenu 라벨은 니모닉용 밑줄(_)을 포함한다
        return (self.props.get("label", "") or "").replace("_", "")

    @property
    def enabled(self):
        return self.props.get("enabled", True)

    @property
    def visible(self):
        return self.props.get("visible", True)

    @property
    def icon_name(self):
        return self.props.get("icon-name")

    @property
    def icon_data(self):
        return self.props.get("icon-data")

    @property
    def toggle_type(self):
        return self.props.get("toggle-type", "")

    @property
    def toggle_state(self):
        return self.props.get("toggle-state", -1)

    @property
    def has_submenu(self):
        return self.props.get("children-display") == "submenu"


def _parse(layout):
    """GetLayout 결과 (id, props, children) 을 MenuNode 로."""
    mid, props, children = layout
    kids = []
    for c in children:
        # children 은 variant 로 감싸여 온다
        if hasattr(c, "unpack"):
            c = c.unpack()
        kids.append(_parse(c))
    return MenuNode(mid, props, kids)


class DBusMenuClient:
    """한 트레이 아이템의 메뉴를 가져오고, 클릭을 되돌려 보낸다."""

    def __init__(self, conn, service, path):
        self.conn = conn
        self.service = service
        self.path = path

    def fetch(self, on_ready):
        """GetLayout(0, -1) → MenuNode 트리."""
        def done(out):
            if out is None:
                on_ready(None)
                return
            try:
                _rev, layout = out.unpack()
                on_ready(_parse(layout))
            except Exception as e:
                dbg("메뉴 해석 실패", e)
                on_ready(None)

        # 먼저 AboutToShow 로 갱신 기회를 준다 (앱이 메뉴를 늦게 채우는 경우)
        def then_layout(_out):
            D.call(self.conn, self.service, self.path, IFACE, "GetLayout",
                   GLib.Variant("(iias)", (0, -1, [])), done)

        D.call(self.conn, self.service, self.path, IFACE, "AboutToShow",
               GLib.Variant("(i)", (0,)), then_layout, timeout=1500)

    def clicked(self, node_id):
        D.call(self.conn, self.service, self.path, IFACE, "Event",
               GLib.Variant("(isvu)", (node_id, "clicked",
                                       GLib.Variant("i", 0),
                                       int(GLib.get_real_time() // 1000000))))

    def opened(self, node_id):
        D.call(self.conn, self.service, self.path, IFACE, "AboutToShow",
               GLib.Variant("(i)", (node_id,)))


# ── 메뉴를 위젯으로 ─────────────────────────────────────────
def icon_for(node, size=16):
    if node.icon_name:
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon(node.icon_name):
            img = Gtk.Image.new_from_icon_name(node.icon_name, Gtk.IconSize.MENU)
            img.set_pixel_size(size)
            return img
    data = node.icon_data
    if data:
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(bytes(data))
            loader.close()
            pb = loader.get_pixbuf().scale_simple(size, size,
                                                  GdkPixbuf.InterpType.BILINEAR)
            return Gtk.Image.new_from_pixbuf(pb)
        except Exception:
            pass
    return None


def build_rows(node, client, close_cb, box, depth=0):
    """MenuNode 트리를 세로 박스에 줄줄이 붙인다.

    하위 메뉴는 따로 창을 띄우지 않고 들여쓰기해서 펼친다.
    트레이 메뉴는 대개 얕아서 이 편이 훨씬 안정적이다.
    """
    for ch in node.children:
        if not ch.visible:
            continue

        if ch.type == "separator":
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.get_style_context().add_class("menu-sep")
            box.pack_start(sep, False, False, 0)
            continue

        row = Gtk.Button()
        row.get_style_context().add_class("menu-item")
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.set_sensitive(bool(ch.enabled))

        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h.set_margin_start(6 + depth * 14)

        if ch.toggle_type:
            mark = "●" if ch.toggle_type == "radio" else "✓"
            t = Gtk.Label(label=mark if ch.toggle_state == 1 else " ")
            t.get_style_context().add_class("menu-check")
            t.set_size_request(14, -1)
            h.pack_start(t, False, False, 0)

        img = icon_for(ch)
        if img:
            h.pack_start(img, False, False, 0)

        lbl = Gtk.Label(label=ch.label or "(이름 없음)", xalign=0)
        h.pack_start(lbl, True, True, 0)

        if ch.has_submenu:
            h.pack_end(Gtk.Label(label="▸"), False, False, 0)

        row.add(h)

        if ch.has_submenu:
            # 하위 항목은 이미 GetLayout(-1) 로 다 받아 왔다
            row.connect("clicked", lambda _b, n=ch: client.opened(n.id))
            box.pack_start(row, False, False, 0)
            sub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.pack_start(sub, False, False, 0)
            build_rows(ch, client, close_cb, sub, depth + 1)
        else:
            def on_click(_b, n=ch):
                client.clicked(n.id)
                close_cb()
            row.connect("clicked", on_click)
            box.pack_start(row, False, False, 0)
