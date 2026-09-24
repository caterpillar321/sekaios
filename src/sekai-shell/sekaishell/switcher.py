"""Alt+Tab 창 전환기.

Hyprland 단축키가 D-Bus(org.sekai.Shell.Switch)로 부른다:
    Alt+Tab        → step(+1)   처음이면 열고 바로 전 창을 고른다
    Alt+Shift+Tab  → step(-1)
Alt 를 떼면 고른 창으로 간다. 떼는 순간은 두 길로 안다:
    · 전환기가 키보드를 쥐고 있으니 GTK 가 Alt 떼기를 받는다
    · 너무 빨리 떼서 키보드를 받기 전이면, 초점을 받는 순간 Alt 가
      눌려 있는지 보고 이미 뗐으면 바로 확정한다
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from .layer import GtkLayerShell
from . import dbg
from .popup import make_translucent
from .appicon import app_icon

E = GtkLayerShell.Edge
ALT_KEYS = (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R)
MAX_COLS = 7


class Switcher(Gtk.Window):
    def __init__(self, hypr, on_restore=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.hypr = hypr
        self.on_restore = on_restore      # 최소화된 창을 되살리는 함수 (패널이 준다)
        self.items = []
        self.index = 0
        self.cards = []
        self.opened_at = 0.0

        self.get_style_context().add_class("switcher-window")
        make_translucent(self)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-switcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        # 모서리에 붙이지 않으면 화면 가운데에 뜬다

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.root.get_style_context().add_class("switcher")
        # FlowBox 는 크기가 정해지지 않은 레이어 창에서 한 줄에 하나씩 쌓인다 → Grid 로 직접 배치
        self.grid = Gtk.Grid()
        self.grid.set_row_spacing(6)
        self.grid.set_column_spacing(6)
        self.grid.set_column_homogeneous(True)
        self.grid.set_halign(Gtk.Align.CENTER)
        self.root.pack_start(self.grid, False, False, 0)
        self.title = Gtk.Label()
        self.title.get_style_context().add_class("switcher-title")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(60)
        self.root.pack_start(self.title, False, False, 0)
        self.add(self.root)

        self.connect("key-release-event", self._key_release)
        self.connect("key-press-event", self._key_press)
        self.connect("focus-in-event", self._focus_in)

    # ── 목록 ──
    def _collect(self):
        clients = self.hypr.query("clients") or []
        out = []
        for c in clients:
            if not c.get("mapped", True) or c.get("hidden"):
                continue
            ws = c.get("workspace", {}) or {}
            wname = ws.get("name", "") or ""
            # 특수 워크스페이스 중 최소화(special:min)만 넣는다 (윈도우도 최소화 창을 보여 준다)
            if ws.get("id", 0) < 0 and wname != "special:min":
                continue
            out.append(c)
        # 최근에 쓴 순서 (focusHistoryID 0 = 지금 창)
        out.sort(key=lambda c: c.get("focusHistoryID", 999))
        return out

    def _build(self):
        for ch in self.grid.get_children():
            self.grid.remove(ch)
        self.cards = []
        for c in self.items:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.get_style_context().add_class("switch-card")
            if (c.get("workspace", {}) or {}).get("id", 0) < 0:
                card.get_style_context().add_class("minimized")
            img = app_icon(c.get("class") or c.get("initialClass") or "", 48)
            img.set_margin_top(6)
            card.pack_start(img, False, False, 0)
            name = (c.get("title") or c.get("class") or "?").strip()
            lbl = Gtk.Label(label=name)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(12)
            lbl.set_width_chars(12)
            card.pack_start(lbl, False, False, 0)
            ev = Gtk.EventBox()
            ev.add(card)
            ev.connect("button-press-event", lambda _w, _e, i=len(self.cards): self._click(i))
            i = len(self.cards)
            self.grid.attach(ev, i % MAX_COLS, i // MAX_COLS, 1, 1)
            self.cards.append(card)
        self.grid.show_all()

    def _paint(self):
        for i, card in enumerate(self.cards):
            ctx = card.get_style_context()
            (ctx.add_class if i == self.index else ctx.remove_class)("selected")
        if self.items:
            c = self.items[self.index]
            self.title.set_text((c.get("title") or c.get("class") or "").strip())

    # ── 조작 ──
    def step(self, d):
        if not self.get_visible():
            self.items = self._collect()
            if not self.items:
                return
            if len(self.items) == 1:
                # 창이 하나뿐 — 보여 줄 필요 없이 그 창에 초점
                self.items = []
                return
            self.index = 1 if d > 0 else len(self.items) - 1
            self._build()
            self._paint()
            self.opened_at = time.monotonic()
            self.show_all()
            self.present()
            return
        if not self.items:
            return
        self.index = (self.index + d) % len(self.items)
        self._paint()

    def commit(self):
        if not self.get_visible():
            return
        c = self.items[self.index] if self.items else None
        self.hide()
        self.items = []
        if not c:
            return
        # 전환기가 키보드를 쥐고 있는 동안에는 Hyprland 가 창 초점 이동을 무시한다
        # → 닫힌 것이 컴포지터에 전달된 뒤에 옮긴다
        Gdk.Display.get_default().flush()
        GLib.timeout_add(40, self._focus, c)

    def _focus(self, c):
        addr = c.get("address")
        if (c.get("workspace", {}) or {}).get("id", 0) < 0:
            if self.on_restore:
                self.on_restore(addr)
            return False
        self.hypr.dispatch(f"focuswindow address:{addr}")
        self.hypr.dispatch(f"alterzorder top,address:{addr}")
        return False

    def cancel(self):
        self.hide()
        self.items = []

    def _click(self, i):
        self.index = i
        self._paint()
        self.commit()
        return True

    def _alt_held(self):
        # 키보드 초점을 받을 때 컴포지터가 알려 준 수식 키 상태
        mods = Gdk.Keymap.get_for_display(Gdk.Display.get_default()).get_modifier_state()
        return bool(mods & Gdk.ModifierType.MOD1_MASK)

    def _focus_in(self, *_):
        # Alt 를 이미 뗐으면 (아주 빠른 Alt+Tab) 곧바로 확정
        GLib.timeout_add(30, self._check_released)
        return False

    def _check_released(self):
        if self.get_visible() and not self._alt_held():
            dbg("[switcher] Alt 이미 뗌 → 확정")
            self.commit()
        return False

    def _key_release(self, _w, ev):
        if ev.keyval in ALT_KEYS:
            self.commit()
            return True
        return False

    def _key_press(self, _w, ev):
        k = ev.keyval
        if k == Gdk.KEY_Escape:
            self.cancel()
        elif k in (Gdk.KEY_Right, Gdk.KEY_Tab):
            self.step(+1)
        elif k in (Gdk.KEY_Left, Gdk.KEY_ISO_Left_Tab):
            self.step(-1)
        elif k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.commit()
        elif k == Gdk.KEY_Delete or (k in (Gdk.KEY_w, Gdk.KEY_W)):
            # 고른 창 닫기 (윈도우의 Alt+Tab 에서 Delete)
            if self.items:
                c = self.items.pop(self.index)
                self.hypr.dispatch(f"closewindow address:{c.get('address')}")
                if not self.items:
                    self.cancel()
                    return True
                self.index %= len(self.items)
                self._build()
                self._paint()
        return True
