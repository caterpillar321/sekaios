"""모니터마다 창 배치(자유 배치/타일링) 빠르게 바꾸기 — WorldLink SEKAI_TILE_MONITORS

  Win+Shift+T (sekai-ctl tile)       모든 화면에 번호가 뜬다 → 숫자를 누르면 그 화면의 방식이 바뀐다 (Esc 닫기)
  sekai-ctl tile toggle [모니터]      그 화면(없으면 지금 초점이 있는 화면)을 바로 바꾼다 — 우클릭 메뉴에서
번호는 설정 › 디스플레이와 같은 순서(hyprctl monitors). 값은 settings.json 의 layout.tile_monitors 에 적고
Store.set 이 합성기(general:sekai_tile_monitors)에 바로 알린다.
"""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from . import dbg  # noqa: E402
from .layer import GtkLayerShell  # noqa: E402
from .monitors import gdk_for, hypr_monitors  # noqa: E402

E = GtkLayerShell.Edge if GtkLayerShell else None
PICK_MS = 8000          # 아무것도 안 누르면 닫는다
DONE_MS = 900           # 바뀐 결과를 보여 주고 닫는다

_KEYPAD = {getattr(Gdk, f"KEY_KP_{i}"): i for i in range(1, 10)}
# 자판의 물리 자리(XKB 키코드 = evdev + 8) — 단축키(Win+Shift+T)의 Shift 를 아직 누른 채면 2 가 "@" 로 온다.
#   자판 배열(한글·US)과 Shift 에 상관없이 위 숫자 줄·숫자 키패드 자리로 본다
_KEYCODES = {10 + i: i + 1 for i in range(9)}
_KEYCODES.update({87: 1, 88: 2, 89: 3, 83: 4, 84: 5, 85: 6, 79: 7, 80: 8, 81: 9})


def tiled_names():
    from .store import Store
    return [str(n) for n in (Store().get("layout", "tile_monitors") or [])]


def is_tiled(name):
    return name in tiled_names()


def set_tiled(name, tile):
    from .store import Store
    s = Store()
    cur = [n for n in (s.get("layout", "tile_monitors") or []) if n != name]
    if tile:
        cur.append(name)
    s.set("layout", "tile_monitors", cur)


def available():
    """합성기가 모니터마다 방식을 아나 (WorldLink sekai47~) — 옛 합성기면 메뉴를 숨긴다"""
    import subprocess
    try:
        out = subprocess.run(["hyprctl", "-j", "getoption", "general:sekai_tile_monitors"],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return False
    return out.strip().startswith("{")


def name_for_gdk(gmon):
    """GDK 모니터 → 단자 이름(DP-1 등, 설정에 적는 이름)"""
    if gmon is None:
        return None
    g = gmon.get_geometry()
    return next((m.get("name") for m in hypr_monitors() if (m.get("x"), m.get("y")) == (g.x, g.y)), None)


def menu_label(name):
    """우클릭 메뉴 글 — 지금 방식의 반대로 바꾸는 항목"""
    return "이 화면을 자유 배치로" if is_tiled(name) else "이 화면을 타일링으로"


def mode_text(tile):
    return "타일링" if tile else "자유 배치"


def mode_icon(tile):
    return "view-grid-symbolic" if tile else "window-restore-symbolic"


class TilePicker:
    shared = None           # 패널이 만든 것 — 작업 표시줄 메뉴가 같은 것을 쓴다

    def __init__(self):
        TilePicker.shared = self
        self.wins = []          # [(창, 모니터 이름, 방식 글 라벨, 아이콘)]
        self._timer = 0
        self._done = False

    # ── D-Bus (sekai-ctl tile …) ──
    def handle(self, action="pick", name=""):
        if action == "toggle":
            self.toggle(name or self._focused())
        elif self.wins and not self._done:
            self.close()                    # 같은 단축키를 다시 누르면 닫힌다
        else:
            self.pick()

    def _focused(self):
        for m in hypr_monitors():
            if m.get("focused"):
                return m.get("name", "")
        return ""

    def toggle(self, name):
        """이 화면의 방식을 바로 뒤집고 그 화면에 잠깐 보여 준다 (우클릭 메뉴)"""
        if not name:
            return
        tile = not is_tiled(name)
        set_tiled(name, tile)
        dbg("[tile]", name, mode_text(tile))
        self.close()
        mons = hypr_monitors()
        nums = {m.get("name"): i for i, m in enumerate(mons, 1)}
        m = next((x for x in mons if x.get("name") == name), None)
        if m is not None:
            self._card(m, nums.get(name, 0), tile, hint=None, keys=False, changed=True)
            self._done = True
            self._arm(DONE_MS)

    # ── 번호 고르기 ──
    def pick(self):
        self.close()
        mons = hypr_monitors()
        if not mons:
            return
        tiled = set(tiled_names())
        focused = next((m.get("name") for m in mons if m.get("focused")), mons[0].get("name"))
        many = len(mons) > 1
        for i, m in enumerate(mons, 1):
            mine = m.get("name") == focused
            hint = None
            if mine:
                hint = ("숫자를 누르면 그 화면의 창 배치가 바뀝니다 · Esc 닫기" if many
                        else "1 을 누르면 창 배치가 바뀝니다 · Esc 닫기")
            # 키보드는 초점이 있는 화면의 것 하나만 받는다 (EXCLUSIVE 는 한 면만)
            self._card(m, i, m.get("name") in tiled, hint=hint, keys=mine)
        self._done = False
        self._arm(PICK_MS)

    def _card(self, m, num, tile, hint=None, keys=False, changed=False):
        g = gdk_for(m)
        if g is None:
            return
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.get_style_context().add_class("osd-window")
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            win.set_visual(vis)
        if GtkLayerShell:
            GtkLayerShell.init_for_window(win)
            GtkLayerShell.set_namespace(win, "sekai-osd")
            GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_monitor(win, g)
            GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.EXCLUSIVE if keys
                                            else GtkLayerShell.KeyboardMode.NONE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("tilepick")
        if changed:
            box.get_style_context().add_class("changed")
        n = Gtk.Label(label=str(num) if num else "")
        n.get_style_context().add_class("tilepick-num")
        box.pack_start(n, False, False, 0)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        ic = Gtk.Image.new_from_icon_name(mode_icon(tile), Gtk.IconSize.MENU)
        ic.set_pixel_size(20)
        lbl = Gtk.Label(label=mode_text(tile))
        lbl.get_style_context().add_class("tilepick-mode")
        row.pack_start(ic, False, False, 0)
        row.pack_start(lbl, False, False, 0)
        box.pack_start(row, False, False, 0)
        name = Gtk.Label(label=m.get("name", ""))
        name.get_style_context().add_class("tilepick-name")
        box.pack_start(name, False, False, 0)
        if hint:
            h = Gtk.Label(label=hint)
            h.get_style_context().add_class("tilepick-hint")
            box.pack_start(h, False, False, 4)
        win.add(box)
        if keys:
            win.connect("key-press-event", self._on_key)
        win.show_all()
        self.wins.append((win, m.get("name", ""), lbl, ic, box))

    def _on_key(self, _w, ev):
        k = ev.keyval
        if k == Gdk.KEY_Escape:
            self.close()
            return True
        num = _KEYCODES.get(ev.hardware_keycode) or _KEYPAD.get(k)
        if num is None and Gdk.KEY_1 <= k <= Gdk.KEY_9:
            num = k - Gdk.KEY_0
        if num is None or self._done:
            return True                     # 다른 키는 삼킨다 (뒤의 창에 글자가 들어가지 않게)
        if not 1 <= num <= len(self.wins):
            return True
        _win, name, lbl, ic, box = self.wins[num - 1]
        tile = not is_tiled(name)
        set_tiled(name, tile)
        dbg("[tile]", num, name, mode_text(tile))
        lbl.set_text(mode_text(tile))
        ic.set_from_icon_name(mode_icon(tile), Gtk.IconSize.MENU)
        box.get_style_context().add_class("changed")
        self._done = True
        self._arm(DONE_MS)
        return True

    def _arm(self, ms):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(ms, self._expire)

    def _expire(self):
        self._timer = 0
        self.close()
        return False

    def close(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        for w, *_ in self.wins:
            w.destroy()
        self.wins = []
        self._done = False
