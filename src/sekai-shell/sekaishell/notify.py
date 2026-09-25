"""알림 — org.freedesktop.Notifications 서버 + 토스트 + 알림 센터.

fnott 같은 외부 데몬을 쓰면 알림이 화면에 잠깐 떴다 사라질 뿐,
지난 알림을 다시 볼 방법이 없다. 그래서 데몬을 직접 구현하고
받은 알림을 기록해 '알림 센터'에서 다시 볼 수 있게 한다.
"""
import html
import json
import os
import re
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango  # noqa: E402

from .layer import GtkLayerShell
from . import dbg
from . import config
from . import dbusutil as D
from .popup import PanelPopup

BUS_NAME = "org.freedesktop.Notifications"
OBJ_PATH = "/org/freedesktop/Notifications"

XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities">
      <arg type="as" direction="out" name="capabilities"/>
    </method>
    <method name="Notify">
      <arg type="s"     direction="in"  name="app_name"/>
      <arg type="u"     direction="in"  name="replaces_id"/>
      <arg type="s"     direction="in"  name="app_icon"/>
      <arg type="s"     direction="in"  name="summary"/>
      <arg type="s"     direction="in"  name="body"/>
      <arg type="as"    direction="in"  name="actions"/>
      <arg type="a{sv}" direction="in"  name="hints"/>
      <arg type="i"     direction="in"  name="expire_timeout"/>
      <arg type="u"     direction="out" name="id"/>
    </method>
    <method name="CloseNotification">
      <arg type="u" direction="in" name="id"/>
    </method>
    <method name="GetServerInformation">
      <arg type="s" direction="out" name="name"/>
      <arg type="s" direction="out" name="vendor"/>
      <arg type="s" direction="out" name="version"/>
      <arg type="s" direction="out" name="spec_version"/>
    </method>
    <signal name="NotificationClosed">
      <arg type="u" name="id"/><arg type="u" name="reason"/>
    </signal>
    <signal name="ActionInvoked">
      <arg type="u" name="id"/><arg type="s" name="action_key"/>
    </signal>
  </interface>
</node>"""

# NotificationClosed 사유
EXPIRED, DISMISSED, CLOSED_BY_CALL, UNDEFINED = 1, 2, 3, 4

HISTORY_FILE = os.path.join(config.STATE_DIR, "notifications.json")
MAX_HISTORY = 200             # 설정(알림 → 보관할 알림 개수)이 없을 때


def max_history():
    try:
        return max(1, int(config.settings("notifications", "history", MAX_HISTORY)))
    except (TypeError, ValueError):
        return MAX_HISTORY

URGENCY_NAMES = {0: "낮음", 1: "보통", 2: "긴급"}


# ───────────────────────────────────────────────────────────────
def _hint(hints, *names):
    for n in names:
        if n in hints:
            return hints[n]
    return None


# 태그 이름 뒤에는 경계(공백·"/"·">")가 있어야 태그다 — "<a@x.com>", "<a1234@…>" 는 글자
_TAG = re.compile(r"<(/?)([A-Za-z]+)(\s[^<>]*|/)?>")
# 우리가 만든 <a href="…"> / </a> — 검사할 때만 같은 자리의 <span> 으로 바꾼다
_A_OUT = re.compile(r'<(/?)a(?: href="[^"]*")?>')


def _attr(attrs, name):
    """속성 값 (엔티티를 푼 것). 없으면 None — 따옴표 없는 값도 받는다"""
    m = re.search(r"""(?<![\w-])%s\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""" % name, attrs or "", re.I)
    if not m:
        return None
    return html.unescape(next(g for g in m.groups() if g is not None))


def _convert(body, markup):
    """본문을 Pango 마크업(markup=True) 또는 맨 글자로. 규격의 태그(b·i·u·a href·img alt·br)만 태그로
    다루고, 나머지 <…> 는 글자 그대로 둔다 ("I have <a lot> of work", "if a<b and c>d" 가 지워졌다).
    href 없는 <a …>, alt 없는 <img …>, 속성이 붙은 <b …> 도 글자다."""
    esc = GLib.markup_escape_text if markup else (lambda t: t)
    out, pos, open_a = [], 0, False

    def text(t):
        out.append(esc(html.unescape(t)))
    for m in _TAG.finditer(body):
        text(body[pos:m.start()])
        pos = m.end()
        close, tag, rest = m.group(1), m.group(2).lower(), (m.group(3) or "").strip()
        href = _attr(rest, "href") if tag == "a" and not close and not open_a else None
        alt = _attr(rest, "alt") if tag == "img" and not close else None
        if tag in ("b", "i", "u") and not rest:
            out.append(f"<{close}{tag}>" if markup else "")
        elif tag == "br" and not close and rest in ("", "/"):
            out.append("\n")
        elif href is not None:
            out.append('<a href="%s">' % GLib.markup_escape_text(href) if markup else "")
            open_a = True
        elif tag == "a" and close and not rest and open_a:
            out.append("</a>" if markup else "")
            open_a = False
        elif alt is not None:
            out.append(esc(alt))
        else:                                   # 규격에 없는 것 — 글자 그대로
            text(m.group(0))
    text(body[pos:])
    if open_a:
        out.append("</a>" if markup else "")
    return "".join(out)


def body_markup(body):
    """알림 본문(규격상 제한된 HTML: b·i·u·a·img, 덤으로 br) → 라벨에 넣을 마크업. 못 만들면 None.
    set_markup 은 틀린 마크업이어도 예외 없이 빈 글자가 된다 — "&" 나 "<" 가 든 본문이 빈칸으로 보였다.
    태그 사이 글자는 엔티티를 풀었다가 다시 이스케이프한다 (규격대로 &amp; 를 보내는 앱도, 날것 & 를
    보내는 앱도 같게 보이게). img 는 alt 글자만."""
    s = _convert(body, True)
    try:
        # 라벨에 넣을 문자열 그대로 검사한다. <a> 는 GtkLabel 이 <span> 으로 바꿔 Pango 에 넘기므로 같게 바꿔서 —
        #   빼고 검사하면 <b><a href="x">hi</b></a> 처럼 엇갈린 짝이 통과해 본문이 빈칸이 됐다
        Pango.parse_markup(_A_OUT.sub(r"<\1span>", s), -1, "\0")
    except GLib.Error:
        return None
    return s


def body_plain(body):
    """마크업을 못 쓸 때 (set_text 로 넣는다) — 규격의 태그만 빼고 엔티티를 푼 글자. 나머지 <…> 는 그대로"""
    return _convert(body, False)


def pixbuf_from_image_data(v):
    """image-data 힌트 (iiibiiay) → GdkPixbuf."""
    try:
        w, h, rowstride, has_alpha, bps, channels, data = v
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB,
            bool(has_alpha), int(bps), int(w), int(h), int(rowstride))
    except Exception as e:
        dbg("image-data 해석 실패:", e)
        return None


def load_icon(name, size=40):
    """앱 아이콘 이름 / 파일 경로 / file:// URI 를 픽스버프로."""
    if not name:
        return None
    if name.startswith("file://"):
        name = GLib.filename_from_uri(name)[0]
    if os.path.isabs(name) and os.path.isfile(name):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(name, size, size)
        except Exception:
            return None
    theme = Gtk.IconTheme.get_default()
    if theme.has_icon(name):
        try:
            return theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except Exception:
            return None
    return None


class Notification:
    __slots__ = ("id", "app", "app_icon", "summary", "body", "actions",
                 "urgency", "timeout", "time", "image", "transient",
                 "desktop_entry", "resident")

    def __init__(self, nid, app, app_icon, summary, body, actions, hints,
                 timeout):
        self.id = nid
        self.app = app or "알림"
        self.app_icon = app_icon or ""
        self.summary = summary or ""
        self.body = body or ""
        # actions 는 [키, 라벨, 키, 라벨, ...]
        self.actions = [(actions[i], actions[i + 1])
                        for i in range(0, len(actions) - 1, 2)]
        u = _hint(hints, "urgency")              # 0(낮음)도 살린다 — "or 1" 이면 0 이 1 로 바뀐다
        self.urgency = int(u) if u is not None else 1
        self.transient = bool(_hint(hints, "transient") or False)
        self.resident = bool(_hint(hints, "resident") or False)
        self.desktop_entry = _hint(hints, "desktop-entry", "desktop_entry") or ""
        self.timeout = timeout
        self.time = time.time()

        img = _hint(hints, "image-data", "image_data", "icon_data")
        self.image = pixbuf_from_image_data(img) if img is not None else None
        if self.image is None:
            path = _hint(hints, "image-path", "image_path")
            self.image = load_icon(path) if path else None
        if self.image is None:
            self.image = load_icon(self.app_icon)
        if self.image is None:
            self.image = load_icon(self.desktop_entry)

    # 기록용 (픽스버프는 저장하지 않는다)
    def to_dict(self):
        return {"id": self.id, "app": self.app, "app_icon": self.app_icon,
                "summary": self.summary, "body": self.body,
                "urgency": self.urgency, "time": self.time,
                "desktop_entry": self.desktop_entry}

    @staticmethod
    def from_dict(d):
        n = Notification.__new__(Notification)
        n.id = d.get("id", 0)
        n.app = d.get("app", "알림")
        n.app_icon = d.get("app_icon", "")
        n.summary = d.get("summary", "")
        n.body = d.get("body", "")
        n.actions = []
        n.urgency = d.get("urgency", 1)
        n.transient = False
        n.resident = False
        n.desktop_entry = d.get("desktop_entry", "")
        n.timeout = -1
        n.time = d.get("time", 0)
        n.image = load_icon(n.app_icon) or load_icon(n.desktop_entry)
        return n


def fmt_time(ts):
    if not ts:
        return ""
    d = time.time() - ts
    if d < 60:
        return "방금"
    if d < 3600:
        return "%d분 전" % (d // 60)
    if d < 86400 and time.localtime(ts).tm_mday == time.localtime().tm_mday:
        return time.strftime("%H:%M", time.localtime(ts))
    return time.strftime("%m/%d %H:%M", time.localtime(ts))


# ───────────────────────────────────────────────────────────────
def build_card(n, on_action, on_close, compact=False):
    """알림 한 장. 토스트와 센터가 같은 모양을 쓴다."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.get_style_context().add_class("noti-card")
    if n.urgency == 2:
        card.get_style_context().add_class("critical")
    elif n.urgency == 0:
        card.get_style_context().add_class("low")

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

    if n.image is not None:
        pb = n.image
        want = 32 if compact else 36
        if pb.get_width() != want:
            pb = pb.scale_simple(want, want, GdkPixbuf.InterpType.BILINEAR)
        img = Gtk.Image.new_from_pixbuf(pb)
        img.set_valign(Gtk.Align.START)
        top.pack_start(img, False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    app = Gtk.Label(label=n.app, xalign=0)
    app.get_style_context().add_class("noti-app")
    head.pack_start(app, False, False, 0)
    when = Gtk.Label(label=fmt_time(n.time), xalign=0)
    when.get_style_context().add_class("noti-time")
    head.pack_end(when, False, False, 0)
    text.pack_start(head, False, False, 0)

    if n.summary:
        s = Gtk.Label(label=n.summary, xalign=0)
        s.get_style_context().add_class("noti-summary")
        s.set_line_wrap(True)
        s.set_max_width_chars(38)
        text.pack_start(s, False, False, 0)

    if n.body:
        b = Gtk.Label(xalign=0)
        # 알림 본문은 제한된 HTML 을 허용하는 규격이다 (body_markup 이 걸러 낸다)
        mk = body_markup(n.body)
        if mk is not None:
            b.set_markup(mk)
        else:
            b.set_text(body_plain(n.body))
        b.get_style_context().add_class("noti-body")
        b.set_line_wrap(True)
        b.set_max_width_chars(42)
        b.set_lines(4 if not compact else 3)
        b.set_ellipsize(3)
        text.pack_start(b, False, False, 0)

    top.pack_start(text, True, True, 0)

    x = Gtk.Button()
    xi = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
    xi.set_pixel_size(14)
    x.add(xi)
    x.get_style_context().add_class("noti-close")
    x.set_relief(Gtk.ReliefStyle.NONE)
    x.set_valign(Gtk.Align.START)
    x.connect("clicked", lambda *_: on_close(n))
    top.pack_end(x, False, False, 0)

    card.pack_start(top, False, False, 0)

    acts = [(k, l) for k, l in n.actions if k != "default"]
    if acts:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.END)
        for key, label in acts[:3]:
            btn = Gtk.Button(label=label or key)
            btn.get_style_context().add_class("noti-action")
            btn.connect("clicked", lambda _b, k=key: on_action(n, k))
            row.pack_start(btn, False, False, 0)
        card.pack_start(row, False, False, 0)

    # 카드 본체 클릭 = 기본 동작
    ev = Gtk.EventBox()
    ev.add(card)
    ev.connect("button-press-event",
               lambda _w, e: (on_action(n, "default"), True)[1]
               if e.button == 1 else False)
    return ev


# ───────────────────────────────────────────────────────────────
class ToastStack(Gtk.Window):
    """화면 오른쪽 아래에 쌓이는 토스트.

    알림마다 창을 따로 만들면 위치 계산이 지저분해진다.
    레이어 창 하나에 세로로 쌓는다.
    """

    def __init__(self, service):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.service = service
        self.get_style_context().add_class("toast-stack")

        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-toast")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 12)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 12)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.box.get_style_context().add_class("toast-box")
        self.add(self.box)

        self.cards = {}      # id -> (widget, timeout_source)

    def push(self, n):
        if not self.get_visible():
            # 주 디스플레이에 (윈도우처럼). 떠 있는 동안 옮기면 깜빡이므로 처음 뜰 때만
            from .monitors import primary_gdk
            m = primary_gdk()
            if m is not None:
                GtkLayerShell.set_monitor(self, m)
        self.drop(n.id)
        w = build_card(n, self.service.invoke_action, self.service.dismiss)
        self.box.pack_end(w, False, False, 0)
        src = None
        ms = self.service.timeout_for(n)
        if ms > 0:
            src = GLib.timeout_add(ms, self._expire, n.id)
        self.cards[n.id] = (w, src)
        self.show_all()

    def _expire(self, nid):
        self.service.close(nid, EXPIRED)
        return False

    def drop(self, nid):
        entry = self.cards.pop(nid, None)
        if entry is None:
            return
        w, src = entry
        if src:
            GLib.source_remove(src)
        self.box.remove(w)
        w.destroy()
        if not self.cards:
            self.hide()

    def clear(self):
        for nid in list(self.cards):
            self.drop(nid)


# ───────────────────────────────────────────────────────────────
class NotificationCenter(PanelPopup):
    """지난 알림을 모아 보는 패널."""

    def __init__(self, service):
        super().__init__(anchor_edge=GtkLayerShell.Edge.RIGHT)
        self.service = service
        self.get_style_context().add_class("noti-center")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="알림", xalign=0)
        title.get_style_context().add_class("pop-title")
        head.pack_start(title, True, True, 0)

        self.dnd = Gtk.ToggleButton(label="방해 금지")
        self.dnd.get_style_context().add_class("pop-btn")
        self.dnd.set_active(service.dnd)
        self.dnd.connect("toggled", lambda b: service.set_dnd(b.get_active()))
        head.pack_end(self.dnd, False, False, 0)

        clear = Gtk.Button(label="모두 지우기")
        clear.get_style_context().add_class("pop-btn")
        clear.connect("clicked", lambda *_: service.clear_history())
        head.pack_end(clear, False, False, 0)
        self.root.pack_start(head, False, False, 0)

        sc = Gtk.ScrolledWindow()
        self.scroller = sc
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_size_request(400, -1)
        # 알림이 적으면 창도 작게. 많으면 스크롤.
        sc.set_propagate_natural_height(True)
        sc.set_max_content_height(520)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.list.get_style_context().add_class("noti-list")
        sc.add(self.list)
        self.root.pack_start(sc, True, True, 0)

        self.empty = Gtk.Label(label="새 알림이 없습니다.", xalign=0.5)
        self.empty.get_style_context().add_class("noti-empty")
        # show_all() 이 되살리지 못하도록 — 표시 여부는 우리가 정한다
        self.empty.set_no_show_all(True)
        self.root.pack_start(self.empty, False, False, 0)

    def on_open(self):
        self.rebuild()
        self.service.mark_read()

    def rebuild(self):
        for c in self.list.get_children():
            self.list.remove(c)
        hist = self.service.history
        for n in reversed(hist[-60:]):
            self.list.pack_start(
                build_card(n, self.service.invoke_action,
                           self.service.forget, compact=True),
                False, False, 0)
        self.list.show_all()
        self.empty.set_visible(not hist)
        self.dnd.set_active(self.service.dnd)
        # 레이어 창에서는 '내용 높이 따라가기'가 첫 배치 때 0 으로 잡힌다 → 직접 재서 지정
        _min, nat = self.list.get_preferred_height()
        self.scroller.set_min_content_height(max(0, min(nat, 520)))
        self.resize(1, 1)       # 알림이 줄면 창도 줄게


# ───────────────────────────────────────────────────────────────
class NotificationService:
    """org.freedesktop.Notifications 서버."""

    def __init__(self, on_count_changed=None):
        self.next_id = 1
        self.live = {}                 # id -> Notification (아직 안 닫힌 것)
        self.history = []
        self.unread = 0
        self._unread_ids = set()       # 읽지 않음으로 센 알림 — 같은 알림을 갱신(replaces_id)할 때 또 세지 않게
        self.on_count_changed = on_count_changed
        self.dnd = bool(config.state("dnd", False))
        # 설정 앱에서 "기록 지우기"를 누른 시각 — 이보다 새 값이 오면 메모리의 기록도 비운다
        self._cleared_seen = config.state("history_cleared", 0)
        self.conn = None
        self.toasts = None
        self.center = None

        self._load_history()

        try:
            self.conn = D.bus()
        except Exception as e:
            dbg("세션 버스 없음 — 알림 데몬 비활성:", e)
            return

        self._node = D.node_info(XML)
        try:
            D.export(self.conn, OBJ_PATH, self._node.interfaces[0],
                     self._on_call, None, None)
        except Exception as e:
            dbg("알림 객체 등록 실패:", e)
            return

        D.own_name(BUS_NAME,
                   lambda n: dbg("알림 데몬 이름 획득:", n),
                   lambda n: dbg("알림 데몬 이름 획득 실패 "
                                 "(다른 알림 데몬이 떠 있습니다):", n))

        self.toasts = ToastStack(self)
        self.center = NotificationCenter(self)

    # ── D-Bus ──────────────────────────────────────────────
    def _on_call(self, _conn, _sender, _path, _iface, method, params, inv):
        # 여기서 예외가 나면 답을 못 보내, 알림을 보낸 앱이 D-Bus 시간 제한(25초)까지 멈췄다 — 곧바로 오류로 답한다
        try:
            self._handle_call(method, params, inv)
        except Exception as e:
            dbg("알림 D-Bus 처리 실패", method, e)
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", f"{method}: {e}")

    def _handle_call(self, method, params, inv):
        if method == "GetCapabilities":
            inv.return_value(GLib.Variant("(as)", (
                ["body", "body-markup", "body-hyperlinks", "icon-static",
                 "actions", "persistence"],)))
        elif method == "GetServerInformation":
            inv.return_value(GLib.Variant(
                "(ssss)", ("sekai-shell", "SekaiOS", "0.1", "1.2")))
        elif method == "Notify":
            app, replaces, icon, summary, body, actions, hints, timeout = \
                params.unpack()
            nid = self.notify(app, replaces, icon, summary, body,
                              actions, hints, timeout)
            inv.return_value(GLib.Variant("(u)", (nid,)))
        elif method == "CloseNotification":
            self.close(params.unpack()[0], CLOSED_BY_CALL)
            inv.return_value(None)
        else:
            inv.return_value(None)

    def _emit(self, signal, variant):
        if self.conn:
            D.emit(self.conn, OBJ_PATH, BUS_NAME, signal, variant)

    # ── 알림 처리 ──────────────────────────────────────────
    def notify(self, app, replaces, icon, summary, body, actions, hints,
               timeout):
        nid = replaces if replaces else self.next_id
        if not replaces:
            self.next_id += 1

        n = Notification(nid, app, icon, summary, body, actions, hints, timeout)
        self.live[nid] = n

        if not n.transient:
            self.history = [h for h in self.history if h.id != nid]
            self.history.append(n)
            keep = max_history()
            if len(self.history) > keep:
                self.history = self.history[-keep:]
            self._save_history()
            # 갱신(진행률 등)마다 늘리면 센터엔 1건인데 배지는 갱신 횟수만큼 커졌다
            if nid not in self._unread_ids:
                self._unread_ids.add(nid)
                self.unread += 1
            self._changed()

        if self.toasts is not None and not self.dnd:
            self.toasts.push(n)
        elif not n.resident:
            # 방해 금지: 토스트는 안 띄우지만 "닫힘" 신호는 보내야 한다 — 안 그러면
            # 동작 버튼을 기다리는 쪽(notify-send --action 등)이 영영 끝나지 않고 쌓인다
            GLib.timeout_add(self.timeout_for(n) or 6000, self._dnd_expire, nid)
        if self.center is not None and self.center.get_visible():
            self.center.rebuild()
        dbg(f"알림 #{nid} [{app}] {summary}")
        return nid

    def timeout_for(self, n):
        """토스트가 떠 있을 시간(ms). 0 이면 직접 닫을 때까지."""
        if n.timeout == 0:
            return 0
        if n.timeout and n.timeout > 0:
            return n.timeout
        if n.urgency == 2:                     # 긴급은 자동으로 안 닫는다
            return 0
        default = config.settings("notifications", "timeout", 6)
        try:
            return max(1, int(default)) * 1000
        except Exception:
            return 6000

    def _dnd_expire(self, nid):
        if nid in self.live:
            self.close(nid, EXPIRED)
        return False

    def close(self, nid, reason=CLOSED_BY_CALL):
        if self.toasts is not None:
            self.toasts.drop(nid)
        if nid in self.live:
            del self.live[nid]
        self._emit("NotificationClosed", GLib.Variant("(uu)", (nid, reason)))

    def dismiss(self, n):
        self.close(n.id, DISMISSED)

    def invoke_action(self, n, key):
        keys = [k for k, _ in n.actions]
        if key == "default" and "default" not in keys:
            # 기본 동작이 없는 알림은 클릭하면 그냥 닫는다
            self.close(n.id, DISMISSED)
            return
        self._emit("ActionInvoked", GLib.Variant("(us)", (n.id, key)))
        if not n.resident:
            self.close(n.id, DISMISSED)

    def forget(self, n):
        """센터 기록에서 지우기."""
        self.history = [h for h in self.history if h.id != n.id]
        self._save_history()
        self.close(n.id, DISMISSED)
        if self.center:
            self.center.rebuild()
        self._changed()

    def clear_history(self):
        self.history = []
        self.unread = 0
        self._unread_ids.clear()
        self._save_history()
        # 떠 있는 알림은 규격대로 닫는다 (사용자가 닫음) — 토스트만 걷으면 NotificationClosed 가 안 나가
        #   notify-send --wait/--action 으로 기다리는 쪽(sekai-screenshot 등)이 영영 끝나지 않았다.
        #   기록까지 지웠으니 나중에 눌러 줄 길도 없다 — 방해 금지로 토스트 없이 살아 있는 것도 닫는다
        for nid in list(self.live):
            self.close(nid, DISMISSED)
        if self.toasts:
            self.toasts.clear()
        if self.center:
            self.center.rebuild()
        self._changed()

    def sync_settings(self):
        """설정 앱이 바꾼 것을 따라간다 (패널이 SIGHUP 을 받으면) — 방해 금지, 기록 지우기, 보관 개수"""
        dnd = bool(config.state("dnd", False))
        if dnd != self.dnd:
            self.set_dnd(dnd)
        cleared = config.state("history_cleared", 0)
        if cleared and cleared != self._cleared_seen:
            # 파일만 지우면 메모리에 남은 기록이 다음 알림 때 도로 저장된다
            self._cleared_seen = cleared
            self.clear_history()
            return
        keep = max_history()
        if len(self.history) > keep:
            self.history = self.history[-keep:]
            self._save_history()
            if self.center is not None and self.center.get_visible():
                self.center.rebuild()
            self._changed()

    def mark_read(self):
        self._unread_ids.clear()
        if self.unread:
            self.unread = 0
            self._changed()

    def set_dnd(self, on):
        self.dnd = bool(on)
        config.set_state("dnd", self.dnd)
        if self.dnd and self.toasts:
            # 떠 있던 토스트를 닫는다 (사용자가 닫음 — 닫힘 신호를 보내고 live 에서 뺀다).
            #   resident 는 방해 금지 중에 온 것처럼 토스트만 걷는다 — 센터에서 눌러 동작을 부를 수 있게
            for nid in list(self.toasts.cards):
                n = self.live.get(nid)
                if n is not None and n.resident and not n.transient:
                    self.toasts.drop(nid)
                else:
                    self.close(nid, DISMISSED)
        self._changed()

    def _changed(self):
        if self.on_count_changed:
            self.on_count_changed(self.unread, len(self.history), self.dnd)

    # ── 기록 저장 ──────────────────────────────────────────
    def _load_history(self):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                self.history = [Notification.from_dict(d)
                                for d in json.load(f)]
            if self.history:
                self.next_id = max(h.id for h in self.history) + 1
        except Exception:
            self.history = []

    def _save_history(self):
        try:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            tmp = HISTORY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([h.to_dict() for h in self.history], f,
                          ensure_ascii=False)
            os.replace(tmp, HISTORY_FILE)
        except Exception as e:
            dbg("알림 기록 저장 실패:", e)
