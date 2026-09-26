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
from .imemode import HangulMode
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
    return _fit(pb, want)


def _fit(pb, want):
    """want×want 칸에 비율을 지켜 맞춘다. 예전엔 정사각형으로 늘리고(비율이 깨졌다)
    BILINEAR 로 크게 줄여 계단이 졌다 — HYPER 로 (줄일 때 가장 깨끗하다)"""
    w, h = pb.get_width(), pb.get_height()
    if max(w, h) == want:
        return pb
    f = want / max(w, h)
    return pb.scale_simple(max(1, round(w * f)), max(1, round(h * f)), GdkPixbuf.InterpType.HYPER)


def _icon_from_file(path, size):
    """IconName 이 파일 경로일 때 (ibus 는 엔진 아이콘을 /usr/share/ibus-hangul/icons/…svg 처럼 준다) —
    원하는 크기로 새로 그린다 (SVG 는 선명하게)"""
    if not path or not path.startswith("/") or not os.path.isfile(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except Exception:
        return None


def _icon_from_theme_path(theme_path, name, size):
    """appindicator 는 자기 아이콘을 테마 밖 디렉터리에 두기도 한다."""
    if not theme_path or not name:
        return None
    for ext in (".svg", ".png", ".xpm", ""):
        for sub in ("", f"{size}x{size}/apps/", f"hicolor/{size}x{size}/apps/", "hicolor/scalable/apps/",
                    "scalable/apps/", "hicolor/48x48/apps/", "hicolor/32x32/apps/", "hicolor/22x22/apps/"):
            p = os.path.join(theme_path, sub, name + ext)
            if os.path.isfile(p):
                try:
                    return GdkPixbuf.Pixbuf.new_from_file_at_scale(p, size, size, True)
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
        # 한글 입력기(ibus)는 태극 그림 대신 지금 상태를 글자로 — "가" 한글 · "A" 영문 (imemode)
        self.text = Gtk.Label()
        self.text.get_style_context().add_class("ime-mode")
        for w in (self.image, self.text):
            w.set_no_show_all(True)          # 둘 중 무엇을 보일지는 _apply 가 정한다
        inner = Gtk.Box()
        inner.pack_start(self.image, True, True, 0)
        inner.pack_start(self.text, True, True, 0)
        self.button.add(inner)
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

    def is_hidden(self):
        """작업 표시줄이 직접 보여 주는 것 (TrayBox.hidden_ids) — appindicator 는 Id 를, 못 읽으면
        객체 경로 끝(/org/ayatana/NotificationItem/nm_applet)을 본다"""
        ids = self.box.hidden_ids
        return bool(ids) and (str(self.props.get("Id") or "") in ids
                              or os.path.basename(self.path).replace("_", "-") in ids)

    def is_hangul_ime(self):
        """ibus 패널의 아이콘이고 엔진이 한글이면 True — 그림 대신 "가"/"A" 를 그린다"""
        p = self.props
        return (self.box.ime is not None and str(p.get("Id") or "").startswith("ibus")
                and "ibus-hangul" in str(p.get("IconName") or ""))

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

        ime = self.is_hangul_ime()
        self.image.set_visible(not ime)
        self.text.set_visible(ime)
        if ime:
            self.text.set_text("가" if self.box.ime.state else "A")
        else:
            self._apply_icon(p, size, status)

        title = p.get("Title") or p.get("Id") or "트레이 항목"
        tip = p.get("ToolTip")
        if isinstance(tip, tuple) and len(tip) >= 4:
            head, body = tip[2], tip[3]
            title = "\n".join(x for x in (head or title, body) if x)
        if ime:
            title = ("한글 입력" if self.box.ime.state else "영문 입력") + "\n누르거나 한/영 키로 바꿉니다"
        self.button.set_tooltip_text(title)

        ctx = self.button.get_style_context()
        (ctx.add_class if status == "NeedsAttention" else ctx.remove_class)("attention")
        self.button.set_visible((status != "Passive" or self.box.show_passive) and not self.is_hidden())

        menu_path = p.get("Menu")
        if menu_path and (self.menu_client is None
                          or self.menu_client.path != menu_path):
            self.menu_client = DBusMenuClient(self.conn, self.service, menu_path)

    def _apply_icon(self, p, size, status):
        name = p.get("IconName")
        if status == "NeedsAttention" and p.get("AttentionIconName"):
            name = p["AttentionIconName"]

        # 그림은 화면 배율만큼 크게 그려 붙인다 (HiDPI 에서 흐리거나 깨지지 않게)
        sf = max(1, self.image.get_scale_factor())
        pixmap = p.get("IconPixmap")
        if status == "NeedsAttention" and p.get("AttentionIconPixmap"):
            pixmap = p["AttentionIconPixmap"]
        pb = _icon_from_file(name, size * sf) or \
            _icon_from_theme_path(p.get("IconThemePath"), name, size * sf)
        if pb is not None:
            self._set_pixbuf(pb, sf)
        elif name and not name.startswith("/") and Gtk.IconTheme.get_default().has_icon(name):
            self.image.set_from_icon_name(name, Gtk.IconSize.MENU)
            self.image.set_pixel_size(size)
        else:
            pb = pixmap_to_pixbuf(pixmap, size * sf)
            if pb is not None:
                self._set_pixbuf(pb, sf)
            else:
                self.image.set_from_icon_name("application-x-executable",
                                              Gtk.IconSize.MENU)
                self.image.set_pixel_size(size)

    def _set_pixbuf(self, pb, sf):
        if sf > 1:
            self.image.set_from_surface(Gdk.cairo_surface_create_from_pixbuf(pb, sf, None))
        else:
            self.image.set_from_pixbuf(pb)

    # ── 입력 ──
    def _press(self, widget, ev):
        self.box.click_gen += 1                # 늦게 온 옛 클릭의 응답은 버린다 (_call)
        x, y = self._screen_xy(widget)
        if ev.button == 1 and self.is_hangul_ime():
            self._toggle_hangul()
        elif ev.button == 1:
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

    def _toggle_hangul(self):
        """한/영 전환 — ibus 패널의 트레이 메뉴에서 그 항목("한글 상태")을 누른 것처럼.
        입력 칸(InputContext)은 그것을 만든 앱만 다룰 수 있어서 우리가 직접 바꿀 수는 없다.
        항목을 못 찾으면 메뉴를 연다."""
        label = self.box.ime.label.replace("_", "")
        if self.menu_client is None or not label:
            self.box.show_menu(self, self.button)
            return

        def find(node):
            for ch in node.children:
                if ch.toggle_type == "checkmark" and ch.label == label:
                    return ch
                hit = find(ch)
                if hit is not None:
                    return hit
            return None

        def ready(node):
            hit = find(node) if node is not None else None
            if hit is None:
                self.box.show_menu(self, self.button)
                return
            # 그 항목의 체크 표시는 메뉴에서 누를 때만 바뀐다 — 한/영 키로 바꾸거나 다른 창으로 옮기면
            #   실제 상태와 어긋난 채 남는다. 누르면 "체크 표시의 반대"로 맞추라고 보내므로, 어긋나 있으면
            #   첫 번째는 지금 상태 그대로(아무 일 없음)이고 두 번째가 바꾼다. (같은 연결이라 순서대로 간다)
            real = self.box.ime.state
            if real is not None and (hit.toggle_state == 1) != real:
                self.menu_client.clicked(hit.id)
            self.menu_client.clicked(hit.id)
        self.menu_client.fetch(ready)

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
        gen = self.box.click_gen

        def reply(out):
            if out is None:
                ok["v"] = False
                # 응답이 멈춘 앱이면 시간 제한(1.2초) 뒤에야 실패가 온다 — 그사이 사용자가 트레이를 또 눌렀거나
                #   아이콘을 떠났으면 메뉴를 띄우지 않는다 (다른 창에 치던 키보드 초점을 빼앗았다)
                if gen != self.box.click_gen or not self._pointer_here():
                    dbg(f"{method} 실패 — 사용자가 떠나 메뉴는 띄우지 않는다")
                    return
                dbg(f"{method} 실패 → 메뉴로 대체")
                GLib.idle_add(lambda: (self.box.show_menu(self, self.button), False)[1])

        D.call(self.conn, self.service, self.path, ITEM_IFACE, method,
               GLib.Variant("(ii)", (int(x), int(y))), reply, timeout=1200)
        # 비동기라 즉시 판정할 수 없다. ItemIsMenu 가 아니면 일단 보냈다고 본다.
        return True

    def _pointer_here(self):
        """커서가 아직 이 아이콘 위에 있나 (GTK 가 아는 마우스 올림 상태)"""
        return bool(self.button.get_state_flags() & Gtk.StateFlags.PRELIGHT)

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


def _note(text):
    """메뉴 자리의 안내 한 줄 (불러오는 중 · 못 읽음 · 메뉴 없음)"""
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.get_style_context().add_class("menu-note")
    return lbl


# ───────────────────────────────────────────────────────────────
class TrayMenuPopup(PanelPopup):
    """트레이 항목의 컨텍스트 메뉴를 그리는 레이어 창."""

    def __init__(self):
        super().__init__(dim=False)
        # 메뉴를 빨리 두 번 열면 늦게 온 옛 응답이 새 메뉴를 덮었다 — 세대로 거른다. 닫을 때도 올린다
        self._gen = 0
        self.get_style_context().add_class("tray-menu")
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.root.pack_start(self.list, True, True, 0)

    def present_for(self, item, widget):
        self._gen += 1
        gen = self._gen
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
            self.list.pack_start(_note("이 앱은 메뉴를 제공하지 않습니다."), False, False, 0)
            self.open()
            return

        # 응답을 기다렸다 열면(AboutToShow 1.5초 + GetLayout 3초) 사용자가 다른 창에서 글을 쓰는 중에
        #   팝업이 떠 키보드 초점과 다음 클릭을 빼앗았다 → 곧 안 오면 "불러오는 중" 으로 먼저 연다.
        #   열려 있으면 바깥을 눌러 닫을 수 있고, 닫은 뒤에 온 응답은 세대로 버린다 (close).
        #   (곧바로 열지 않는 것은 금방 답하는 대부분의 앱에서 "불러오는 중" 이 깜빡이지 않게)
        loading = _note("불러오는 중…")
        wait = {"src": 0}

        def show_loading():
            wait["src"] = 0
            if gen == self._gen:
                self.list.pack_start(loading, False, False, 0)
                self.list.show_all()
                self.open()
            return False
        wait["src"] = GLib.timeout_add(150, show_loading)

        def ready(node):
            if wait["src"]:
                GLib.source_remove(wait["src"])
                wait["src"] = 0
            if gen != self._gen:           # 그사이 닫았거나 다른 메뉴를 열었다
                return
            if loading.get_parent() is not None:
                self.list.remove(loading)
            if node is None or not node.children:
                self.list.pack_start(_note("메뉴를 읽지 못했습니다."), False, False, 0)
            else:
                build_rows(node, item.menu_client, self.close, self.list)
            self.list.show_all()
            if self.get_visible():
                self.resize(1, 1)          # 불러오는 중 줄보다 좁아질 수도 있다
            else:
                self.open()

        item.menu_client.fetch(ready)

    def close(self):
        self._gen += 1                     # 닫힌 뒤에 온 응답은 버린다 (다시 열지 않게)
        super().close()


# ───────────────────────────────────────────────────────────────
class TrayBox(Gtk.Box):
    """패널에 들어가는 트레이 영역. Watcher + Host 를 겸한다."""

    def __init__(self, icon_size=18, show_passive=False, hidden_ids=()):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.get_style_context().add_class("tray")
        self.icon_size = icon_size
        self.show_passive = show_passive
        # 등록은 받되 그리지 않는 항목 (SNI Id) — 예: nm-applet (사용자가 직접 띄운 경우). 네트워크 상태는
        #   작업 표시줄의 빠른 설정 아이콘이 보여 준다
        self.hidden_ids = frozenset(hidden_ids)
        self.items = {}
        self.host_registered = False
        self.menu = None
        self.ime = None
        self.click_gen = 0         # 트레이를 누를 때마다 — 늦게 온 Activate 실패로 메뉴를 띄울지 가린다

        try:
            self.conn = D.bus()
        except Exception as e:
            dbg("세션 버스 없음 — 트레이 비활성:", e)
            self.conn = None
            return

        self._node = D.node_info(WATCHER_XML)
        self._reg_id = None
        self._start_watcher()

        self.ime = HangulMode()
        self.ime.on_change(self._ime_changed)

    def _ime_changed(self):
        for item in list(self.items.values()):
            if item.props and item.is_hangul_ime():
                item._apply()

    # ── Watcher ────────────────────────────────────────────
    def refresh_icons(self):
        """아이콘 테마가 바뀌었을 때(다크/라이트) 트레이 아이콘을 새 테마로 다시 그린다"""
        for item in list(self.items.values()):
            try:
                if getattr(item, "props", None):
                    item._apply()
            except Exception:
                pass

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
        # 예외가 나도 곧바로 답한다 (안 그러면 부른 앱이 D-Bus 시간 제한까지 멈춘다)
        try:
            self._handle_call(sender, method, params, inv)
        except Exception as e:
            dbg("트레이 D-Bus 처리 실패", method, e)
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", f"{method}: {e}")

    def _handle_call(self, sender, method, params, inv):
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
