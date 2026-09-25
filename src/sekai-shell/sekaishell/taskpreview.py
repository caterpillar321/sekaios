"""작업 표시줄 창 미리보기 — 윈도우 7 이후처럼 앱 버튼에 마우스를 올리면 그 앱 창들의 작은 그림.

그림은 sekai-winshot (hyprland-toplevel-export) 으로 찍는다. 가려진 창·최소화한 창도 찍힌다.
도구가 없거나(기본 화면 모드 등) 찍기에 실패하면 앱 아이콘을 대신 보인다.
작업 표시줄(모니터마다 하나)이 이 창 하나를 같이 쓴다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango  # noqa: E402

from .layer import GtkLayerShell
from .popup import make_translucent
from .appicon import app_icon
from . import dbg

WINSHOT = "/usr/libexec/sekai/sekai-winshot"
THUMB_W, THUMB_H = 200, 120        # 그림 칸 (논리 픽셀)
CARD_W = THUMB_W + 16              # 카드 너비 (안쪽 여백 포함)
SPACING, PAD = 6, 8                # 카드 사이, 테두리 안쪽
SHOW_DELAY, HIDE_DELAY = 350, 300  # ms — 올리고 잠시 뒤에 뜨고, 벗어나고 잠시 뒤에 닫힌다


def _title_of(c):
    return (c.get("title") or "").strip() or (c.get("class") or "?")


class TaskPreview(Gtk.Window):
    def __init__(self, hypr=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.hypr = hypr
        self.mon = None
        self.get_style_context().add_class("panel-popup")
        self.get_style_context().add_class("task-preview")
        make_translucent(self)
        self.set_decorated(False)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-popup")     # 뒤를 흐리게 (hyprland layerrule)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 6)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("popup-root")
        self.root.get_style_context().add_class("task-preview-root")
        self.add(self.root)
        self.flow = None

        self.key = None             # 지금 보이는 앱
        self._gen = 0               # 찍기 결과가 늦게 오면 버리려고
        self._hide_src = 0
        self._show_src = 0
        self._pending = None           # show_later 가 기다리는 (앱 묶음, 버튼, 창들, 초점 창, 고름, 닫음)
        self._cb = None
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", lambda *_: self.cancel_hide())
        self.connect("leave-notify-event", self._leave)

    # ── 보이기 / 숨기기 ──
    def show_later(self, *args):
        """마우스를 올렸을 때 — 이미 떠 있으면 곧바로 바꾸고, 아니면 잠시 뒤에"""
        self.cancel_show()
        self.cancel_hide()
        if self.get_visible():
            self.show_for(*args)
        else:
            self._pending = list(args)
            self._show_src = GLib.timeout_add(SHOW_DELAY, self._show_timeout)

    def _show_timeout(self):
        self._show_src = 0
        args, self._pending = self._pending, None
        if args:
            self.show_for(*args)
        return False

    def cancel_show(self):
        if self._show_src:
            GLib.source_remove(self._show_src)
            self._show_src = 0
        self._pending = None

    # 작업 표시줄은 창이 열리고 닫히거나 초점·자리가 바뀌면 버튼을 새로 만든다 — 기다리던 미리보기를 새 버튼으로 옮긴다
    #   (그냥 취소하면 창이 자주 바뀔 때 미리보기가 영영 안 뜬다). 제목만 바뀌면 update_title 로 제자리에서.
    def pending(self):
        """기다리는 미리보기의 (앱 묶음, 버튼) — 없으면 None"""
        return (self._pending[0], self._pending[1]) if self._pending else None

    def retarget(self, btn, wins, active_addr):
        if self._pending:
            self._pending[1:4] = [btn, wins, active_addr]

    @staticmethod
    def _attached(btn):
        """버튼이 아직 작업 표시줄 창에 붙어 있나. 떨어진 위젯의 get_toplevel() 은 None 이 아니라
        자기 자신이다 — 창(Gtk.Window)인지로 본다"""
        top = btn.get_toplevel()
        return isinstance(top, Gtk.Window) and top is not btn and top.get_visible()

    def schedule_hide(self, ms=HIDE_DELAY):
        self.cancel_show()
        if self._hide_src or not self.get_visible():
            return
        self._hide_src = GLib.timeout_add(ms, self._hide_timeout)

    def cancel_hide(self):
        if self._hide_src:
            GLib.source_remove(self._hide_src)
            self._hide_src = 0

    def _hide_timeout(self):
        self._hide_src = 0
        if self._pointer_inside():
            return False          # 가짜 leave (카드가 빠지며 크기가 바뀔 때 등) — 진짜로 나가면 또 온다
        self.hide_now()
        return False

    def _pointer_inside(self):
        """커서가 정말 이 창 위에 있는지 컴포지터에 묻는다.
        Wayland 에선 leave 가 가짜로 오기도 하고, leave 때 좌표도 믿을 수 없다."""
        if self.hypr is None or self.mon is None or not self.get_visible():
            return False
        try:
            cur = self.hypr.query("cursorpos") or {}
            cx, cy = cur["x"], cur["y"]
            g = self.mon.get_geometry()
            m = next((m for m in self.hypr.query("monitors") or []
                      if (m.get("x"), m.get("y")) == (g.x, g.y)), None)
            bottom_res = (m.get("reserved") or [0, 0, 0, 0])[3] if m else 0
        except (KeyError, TypeError, AttributeError):
            return False
        x0 = g.x + GtkLayerShell.get_margin(self, GtkLayerShell.Edge.LEFT)
        y1 = g.y + g.height - bottom_res - GtkLayerShell.get_margin(self, GtkLayerShell.Edge.BOTTOM)
        w, h = self.get_allocated_width(), self.get_allocated_height()
        return x0 <= cx < x0 + w and y1 - h <= cy < y1

    def hide_now(self):
        self.cancel_show()
        self.cancel_hide()
        self.key = None
        self._gen += 1
        self.hide()

    def _leave(self, _w, ev):
        if ev.detail != Gdk.NotifyType.INFERIOR:
            self.schedule_hide()
        return False

    def update_title(self, c):
        """창 제목이 바뀜 — 떠 있는 카드의 제목만 고친다 (c 는 제목을 고친 창 정보)"""
        if self.flow is None:
            return
        for child in self.flow.get_children():
            card = child.get_child()
            if getattr(card, "_sekai_addr", None) == c.get("address"):
                card._sekai_title.set_text(_title_of(c))

    # ── 내용 ──
    def show_for(self, key, btn, wins, active_addr, on_pick, on_close):
        """key: 앱 묶음, btn: 작업 표시줄 버튼, wins: 그 앱의 창들 (hyprctl clients 항목)"""
        self.cancel_show()
        self.cancel_hide()
        if not wins or not self._attached(btn):
            self.hide_now()
            return
        self._cb = (on_pick, on_close)
        same = self.get_visible() and self.key == key and \
            [c.get("address") for c in wins] == getattr(self, "_addrs", None)
        self.key = key
        self._addrs = [c.get("address") for c in wins]
        panel = btn.get_toplevel()
        mon = getattr(panel, "gdk_monitor", None)
        self.mon = mon
        if mon is not None:
            GtkLayerShell.set_monitor(self, mon)
        self._wins, self._active = list(wins), active_addr
        if not same:
            self._fill(wins, active_addr, mon)
        self._place(btn, panel, len(wins), mon)
        self.show_all()

    def _per_line(self, mon):
        mw = mon.get_geometry().width if mon is not None else 1280
        return max(1, (mw - 16 - 2 * PAD) // (CARD_W + SPACING))

    def _place(self, btn, panel, n, mon):
        """버튼 한가운데 위로. 버튼 가운데를 기억해 둔다 — 카드 수가 바뀌면 다시 맞추려고
        (버튼은 작업 표시줄이 다시 그려질 때 새로 만들어지므로 버튼 자체는 붙잡지 않는다)"""
        pos = btn.translate_coordinates(panel, 0, 0)
        self._center = (pos[0] if pos else 0) + btn.get_allocated_width() / 2
        self._recenter(n)

    def _recenter(self, n):
        mon = self.mon
        per = min(n, self._per_line(mon))
        w = per * CARD_W + (per - 1) * SPACING + 2 * PAD + 2
        mw = mon.get_geometry().width if mon is not None else 1280
        x = int(getattr(self, "_center", 0) - w / 2)
        x = max(8, min(x, mw - w - 8))
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, x)

    def _fill(self, wins, active_addr, mon):
        self._gen += 1
        gen = self._gen
        if self.flow is not None:
            self.root.remove(self.flow)
        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(True)
        self.flow.set_column_spacing(SPACING)
        self.flow.set_row_spacing(SPACING)
        self.flow.set_max_children_per_line(min(len(wins), self._per_line(mon)))
        self.flow.set_min_children_per_line(min(len(wins), self._per_line(mon)))
        self.root.pack_start(self.flow, False, False, 0)
        for c in wins:
            self.flow.add(self._card(c, c.get("address") == active_addr, gen))

    def _card(self, c, active, gen):
        addr = c.get("address")
        # Gtk.Button 은 입력 창이 자식을 덮어서 안의 닫기 버튼이 눌리지 않는다 → EventBox
        card = Gtk.EventBox()
        card.set_visible_window(False)
        card._sekai_addr = addr
        card.set_size_request(CARD_W, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ctx = box.get_style_context()
        ctx.add_class("preview-card")
        if active:
            ctx.add_class("active")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(app_icon(c.get("class") or "", 16), False, False, 0)
        lbl = Gtk.Label(label=_title_of(c), xalign=0)
        card._sekai_title = lbl
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(1)                    # 카드 너비를 넘지 않게 (남는 만큼 늘어난다)
        lbl.set_hexpand(True)
        lbl.get_style_context().add_class("preview-title")
        head.pack_start(lbl, True, True, 0)
        close = Gtk.Button()
        close.get_style_context().add_class("preview-close")
        close.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))
        close.set_tooltip_text("닫기")
        close.connect("clicked", lambda *_: self._close(c))
        head.pack_start(close, False, False, 0)
        box.pack_start(head, False, False, 0)

        holder = Gtk.Box()
        holder.set_size_request(THUMB_W, THUMB_H)
        holder.get_style_context().add_class("preview-thumb")
        img = app_icon(c.get("class") or "", 48)      # 그림이 오기 전·못 찍을 때
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        holder.set_center_widget(img)
        box.pack_start(holder, False, False, 0)
        card.add(box)
        card.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        card.connect("enter-notify-event", lambda *_: ctx.add_class("hover") and False)
        card.connect("leave-notify-event",
                     lambda _w, ev: ev.detail != Gdk.NotifyType.INFERIOR and ctx.remove_class("hover") and False)
        card.connect("button-release-event", self._card_release, c)
        self._capture(addr, img, gen)
        return card

    def _card_release(self, _w, ev, c):
        if ev.button == 1:
            self._pick(c)
        elif ev.button == 2:                  # 가운데 클릭 = 닫기 (윈도우처럼)
            self._close(c)
        return True

    def _pick(self, c):
        cb = self._cb
        self.hide_now()
        if cb:
            cb[0](c)

    def _close(self, c):
        if self._cb:
            self._cb[1](c)
        self._wins = [w for w in self._wins if w.get("address") != c.get("address")]
        self._addrs = [w.get("address") for w in self._wins]
        if not self._wins:
            self.hide_now()
            return
        # 남은 카드를 새로 만든다 — 창이 줄어 옮겨 가면 커서 밑이 바뀌는데 GTK 는 몰라서
        #   옛 카드엔 마우스 올림 표시(닫기 버튼 빨강 등)가 남는다
        self._fill(self._wins, self._active, self.mon)
        self._recenter(len(self._wins))
        self.resize(1, 1)                     # 줄어든 크기로
        self.show_all()

    # ── 창 그림 ──
    def _capture(self, addr, img, gen):
        if not addr or not os.access(WINSHOT, os.X_OK) or not os.environ.get("WAYLAND_DISPLAY"):
            return
        sf = max(1, img.get_scale_factor())
        try:
            proc = Gio.Subprocess.new([WINSHOT, addr.removeprefix("0x"), str(THUMB_W * sf), str(THUMB_H * sf)],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            dbg("[preview] 찍기 실패", e)
            return
        proc.communicate_async(None, None, self._captured, (img, gen, sf))

    def _captured(self, proc, res, data):
        img, gen, sf = data
        try:
            ok, out, _err = proc.communicate_finish(res)
        except GLib.Error:
            return
        if gen != self._gen or not ok or not proc.get_successful() or out is None:
            return
        raw = out.get_data()
        head, _, px = raw.partition(b"\n")
        try:
            magic, w, h = head.split()
            w, h = int(w), int(h)
        except ValueError:
            return
        if magic != b"SWS1" or len(px) != w * h * 4:
            return
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(px), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
        if sf > 1:
            surf = Gdk.cairo_surface_create_from_pixbuf(pb, sf, None)
            img.set_from_surface(surf)
        else:
            img.set_from_pixbuf(pb)
