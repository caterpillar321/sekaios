"""시스템 트레이 — StatusNotifierItem (SNI).

Wayland 에는 X11 의 XEmbed 트레이가 없다. 요즘 앱들은 D-Bus 규격인
StatusNotifierItem 을 쓰는데, 그러려면 세션에 두 가지가 있어야 한다:

  · Watcher — org.kde.StatusNotifierWatcher.
              앱들이 "나 트레이 아이콘 있어요" 하고 등록하는 곳.
  · Host   — 등록된 아이템을 실제로 그려 주는 쪽. 즉 우리 패널.

둘 다 여기서 구현한다. 데스크탑 환경이 없으면 아무도 제공하지 않으므로
우리가 안 만들면 트레이 아이콘은 영영 안 보인다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio  # noqa: E402

from . import dbg
from . import dbusutil as D
from .dbusmenu import DBusMenuClient, build_rows
from .popup import PanelPopup

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem">
      <arg type="s" direction="in" name="service"/>
    </method>
    <method name="RegisterStatusNotifierHost">
      <arg type="s" direction="in" name="service"/>
    </method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>"""


# ───────────────────────────────────────────────────────────────
def pixmap_to_pixbuf(pixmaps, want=22):
    """SNI 의 IconPixmap a(iiay) → GdkPixbuf.

    데이터는 ARGB32, 네트워크 바이트 순서(big-endian). GdkPixbuf 는
    RGBA 를 원하므로 바이트를 재배열해야 한다.
    """
    if not pixmaps:
        return None
    # 원하는 크기 이상 중 가장 작은 것, 없으면 가장 큰 것
    best = None
    for w, h, data in sorted(pixmaps, key=lambda p: p[0]):
        if w >= want:
            best = (w, h, data)
            break
    if best is None:
        best = max(pixmaps, key=lambda p: p[0])
    w, h, data = best
    if w <= 0 or h <= 0 or not data:
        return None
    src = bytes(data)
    if len(src) < w * h * 4:
        return None
    out = bytearray(w * h * 4)
    for i in range(w * h):
        a, r, g, b = src[i * 4], src[i * 4 + 1], src[i * 4 + 2], src[i * 4 + 3]
        out[i * 4] = r
        out[i * 4 + 1] = g
        out[i * 4 + 2] = b
        out[i * 4 + 3] = a
    pb = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB, True, 8,
        w, h, w * 4)
    if w != want:
        pb = pb.scale_simple(want, want, GdkPixbuf.InterpType.BILINEAR)
    return pb


def _icon_from_theme_path(theme_path, name, size):
    """appindicator 는 자기 아이콘을 테마 밖 디렉터리에 두기도 한다."""
    if not theme_path or not name:
        return None
    for ext in (".png", ".svg", ".xpm", ""):
        for sub in ("", f"{size}x{size}/apps/", "hicolor/scalable/apps/"):
            p = os.path.join(theme_path, sub, name + ext)
            if os.path.isfile(p):
                try:
                    return GdkPixbuf.Pixbuf.new_from_file_at_size(p, size, size)
                except Exception:
                    pass
    return None


# ───────────────────────────────────────────────────────────────
class TrayItem:
    """트레이 아이템 하나 — D-Bus 프록시 + 버튼 위젯."""

    def __init__(self, box, service, path):
        self.box = box
        self.conn = box.conn
        self.service = service
        self.path = path
        self.props = {}
        self.menu_client = None

        self.button = Gtk.Button()
        self.button.get_style_context().add_class("tray-item")
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.button.set_valign(Gtk.Align.CENTER)
        self.image = Gtk.Image()
        self.image.set_pixel_size(box.icon_size)
        self.button.add(self.image)
        self.button.add_events(Gdk.EventMask.SCROLL_MASK)
        self.button.connect("button-press-event", self._press)
        self.button.connect("scroll-event", self._scroll)

        self._subs = []
        for sig in ("NewIcon", "NewOverlayIcon", "NewAttentionIcon",
                    "NewTitle", "NewToolTip", "NewStatus"):
            self._subs.append(
                D.subscribe(self.conn, service, ITEM_IFACE, sig, path,
                            lambda *_a: self.refresh()))
        self._watch = D.watch_vanish(self.conn, service,
                                     lambda _n: box.remove_item(self.key))
        self.refresh()

    @property
    def key(self):
        return f"{self.service}{self.path}"

    # ── 속성 읽기 ──
    def refresh(self):
        def got(props):
            if not props:
                return
            self.props = props
            self._apply()
        D.get_all(self.conn, self.service, self.path, ITEM_IFACE, got)

    def _apply(self):
        p = self.props
        size = self.box.icon_size
        status = p.get("Status", "Active")

        name = p.get("IconName")
        if status == "NeedsAttention" and p.get("AttentionIconName"):
            name = p["AttentionIconName"]

        pb = _icon_from_theme_path(p.get("IconThemePath"), name, size)
        if pb is not None:
            self.image.set_from_pixbuf(pb)
        elif name and Gtk.IconTheme.get_default().has_icon(name):
            self.image.set_from_icon_name(name, Gtk.IconSize.MENU)
            self.image.set_pixel_size(size)
        else:
            pb = pixmap_to_pixbuf(p.get("IconPixmap"), size)
            if pb is not None:
                self.image.set_from_pixbuf(pb)
            else:
                self.image.set_from_icon_name("application-x-executable",
                                              Gtk.IconSize.MENU)
                self.image.set_pixel_size(size)

        title = p.get("Title") or p.get("Id") or "트레이 항목"
        tip = p.get("ToolTip")
        if isinstance(tip, tuple) and len(tip) >= 4:
            head, body = tip[2], tip[3]
            title = "\n".join(x for x in (head or title, body) if x)
        self.button.set_tooltip_text(title)

        ctx = self.button.get_style_context()
        (ctx.add_class if status == "NeedsAttention" else ctx.remove_class)("attention")
        self.button.set_visible(status != "Passive" or self.box.show_passive)

        menu_path = p.get("Menu")
        if menu_path and (self.menu_client is None
                          or self.menu_client.path != menu_path):
            self.menu_client = DBusMenuClient(self.conn, self.service, menu_path)

    # ── 입력 ──
    def _press(self, widget, ev):
        x, y = self._screen_xy(widget)
        if ev.button == 1:
            if self.props.get("ItemIsMenu") or not self._call("Activate", x, y):
                self.box.show_menu(self, widget)
        elif ev.button == 2:
            self._call("SecondaryActivate", x, y)
        elif ev.button == 3:
            if self.menu_client is not None:
                self.box.show_menu(self, widget)
            else:
                self._call("ContextMenu", x, y)
        return True

    def _scroll(self, _w, ev):
        delta = 0
        orient = "vertical"
        if ev.direction == Gdk.ScrollDirection.UP:
            delta = -1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            delta = 1
        elif ev.direction == Gdk.ScrollDirection.LEFT:
            delta, orient = -1, "horizontal"
        elif ev.direction == Gdk.ScrollDirection.RIGHT:
            delta, orient = 1, "horizontal"
        else:
            return False
        D.call(self.conn, self.service, self.path, ITEM_IFACE, "Scroll",
               GLib.Variant("(is)", (delta, orient)))
        return True

    def _screen_xy(self, widget):
        try:
            win = widget.get_window()
            a = widget.get_allocation()
            ox, oy = win.get_origin()[1:] if win else (0, 0)
            return ox + a.x, oy + a.y
        except Exception:
            return 0, 0

    def _call(self, method, x, y):
        """Activate 류는 실패하는 앱이 많아서 성공 여부를 알려 준다."""
        ok = {"v": True}

        def reply(out):
            if out is None:
                ok["v"] = False
                dbg(f"{method} 실패 → 메뉴로 대체")
                GLib.idle_add(lambda: (self.box.show_menu(self, self.button), False)[1])

        D.call(self.conn, self.service, self.path, ITEM_IFACE, method,
               GLib.Variant("(ii)", (int(x), int(y))), reply, timeout=1200)
        # 비동기라 즉시 판정할 수 없다. ItemIsMenu 가 아니면 일단 보냈다고 본다.
        return True

    def destroy(self):
        for s in self._subs:
            try:
                self.conn.signal_unsubscribe(s)
            except Exception:
                pass
        try:
            Gio.bus_unwatch_name(self._watch)
        except Exception:
            pass
        self.button.destroy()


# ───────────────────────────────────────────────────────────────
class TrayMenuPopup(PanelPopup):
    """트레이 항목의 컨텍스트 메뉴를 그리는 레이어 창."""

    def __init__(self):
        super().__init__(dim=False)
        self.get_style_context().add_class("tray-menu")
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.root.pack_start(self.list, True, True, 0)

    def present_for(self, item, widget):
        for c in self.list.get_children():
            self.list.remove(c)

        # 아이콘 위치에 맞춰 오른쪽 여백을 잡는다
        try:
            top = widget.get_toplevel()
            a = widget.get_allocation()
            right = top.get_allocated_width() - (a.x + a.width)
            self.set_x_margin(max(4, right - 60))
        except Exception:
            pass

        head = Gtk.Label(label=item.props.get("Title")
                         or item.props.get("Id") or "트레이", xalign=0)
        head.get_style_context().add_class("menu-head")
        self.list.pack_start(head, False, False, 0)

        if item.menu_client is None:
            self.list.pack_start(
                Gtk.Label(label="이 앱은 메뉴를 제공하지 않습니다.", xalign=0),
                False, False, 0)
            self.open()
            return

        def ready(node):
            if node is None or not node.children:
                self.list.pack_start(
                    Gtk.Label(label="메뉴를 읽지 못했습니다.", xalign=0),
                    False, False, 0)
            else:
                build_rows(node, item.menu_client, self.close, self.list)
            self.list.show_all()
            self.open()

        item.menu_client.fetch(ready)


# ───────────────────────────────────────────────────────────────
class TrayBox(Gtk.Box):
    """패널에 들어가는 트레이 영역. Watcher + Host 를 겸한다."""

    def __init__(self, icon_size=18, show_passive=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.get_style_context().add_class("tray")
        self.icon_size = icon_size
        self.show_passive = show_passive
        self.items = {}
        self.host_registered = False
        self.menu = None

        try:
            self.conn = D.bus()
        except Exception as e:
            dbg("세션 버스 없음 — 트레이 비활성:", e)
            self.conn = None
            return

        self._node = D.node_info(WATCHER_XML)
        self._reg_id = None
        self._start_watcher()

    # ── Watcher ────────────────────────────────────────────
    def _start_watcher(self):
        try:
            self._reg_id = D.export(self.conn, WATCHER_PATH,
                                    self._node.interfaces[0],
                                    self._on_call, self._on_get)
        except Exception as e:
            dbg("Watcher 등록 실패:", e)
            return

        def acquired(name):
            dbg("Watcher 이름 획득:", name)
            self.host_registered = True
            D.emit(self.conn, WATCHER_PATH, WATCHER_NAME,
                   "StatusNotifierHostRegistered", None)

        def lost(name):
            # 다른 데스크탑 환경이 이미 떠 있는 경우. 그쪽에 맡긴다.
            dbg("Watcher 이름을 잡지 못했습니다 (다른 트레이가 있는 듯):", name)

        D.own_name(WATCHER_NAME, acquired, lost)

    def _on_get(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items.keys()))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def _on_call(self, _conn, sender, _path, _iface, method, params, inv):
        if method == "RegisterStatusNotifierItem":
            arg = params.unpack()[0]
            if arg.startswith("/"):
                service, path = sender, arg
            else:
                service, path = arg, "/StatusNotifierItem"
            self.add_item(service, path)
            inv.return_value(None)
        elif method == "RegisterStatusNotifierHost":
            self.host_registered = True
            D.emit(self.conn, WATCHER_PATH, WATCHER_NAME,
                   "StatusNotifierHostRegistered", None)
            inv.return_value(None)
        else:
            inv.return_value(None)

    # ── Host ───────────────────────────────────────────────
    def add_item(self, service, path):
        key = f"{service}{path}"
        if key in self.items:
            self.items[key].refresh()
            return
        dbg("트레이 등록:", key)
        item = TrayItem(self, service, path)
        self.items[key] = item
        self.pack_start(item.button, False, False, 0)
        item.button.show_all()
        D.emit(self.conn, WATCHER_PATH, WATCHER_NAME,
               "StatusNotifierItemRegistered", GLib.Variant("(s)", (key,)))
        self.show()

    def remove_item(self, key):
        item = self.items.pop(key, None)
        if item is None:
            return
        dbg("트레이 해제:", key)
        item.destroy()
        D.emit(self.conn, WATCHER_PATH, WATCHER_NAME,
               "StatusNotifierItemUnregistered", GLib.Variant("(s)", (key,)))

    def show_menu(self, item, widget):
        if self.menu is None:
            self.menu = TrayMenuPopup()
        self.menu.present_for(item, widget)
