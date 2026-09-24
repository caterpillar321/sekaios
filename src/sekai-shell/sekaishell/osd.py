"""음량·밝기 OSD — 키를 누르면 화면 아래 가운데에 잠깐 뜨는 알약.

윈도우 11 처럼: [아이콘] ━━━━━━━○──── 57
입력은 받지 않는다 (키보드 초점을 뺏지 않는다).
"""
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GLib, GtkLayerShell  # noqa: E402

from . import dbg
from .popup import make_translucent

E = GtkLayerShell.Edge
SHOW_MS = 1400


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
    except Exception as e:
        dbg("osd 명령 실패", cmd, e)
        return ""


# ── 음량 (PipeWire / wpctl) ──
def volume_get():
    out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    parts = out.split()
    try:
        v = int(round(float(parts[1]) * 100))
    except (IndexError, ValueError):
        v = 0
    return v, "MUTED" in out


def volume_do(action):
    if action == "up":
        _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%+"])
    elif action == "down":
        _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"])
    elif action == "mute":
        _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
    elif action == "mic-mute":
        _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])


def mic_muted():
    return "MUTED" in _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"])


# ── 밝기 (brightnessctl) ──
def brightness_get():
    out = _run(["brightnessctl", "-m", "info"])
    # 형식: 장치,종류,현재,백분율%,최대
    try:
        return int(out.strip().split(",")[3].rstrip("%"))
    except (IndexError, ValueError):
        return None


def brightness_do(action):
    if action == "up":
        _run(["brightnessctl", "-q", "set", "5%+"])
    elif action == "down":
        # 0 이 되면 화면이 완전히 꺼지는 패널이 있다 → 1% 아래로는 내리지 않는다
        _run(["brightnessctl", "-q", "-n1", "set", "5%-"])


def vol_icon(v, muted):
    if muted or v == 0:
        return "audio-volume-muted-symbolic"
    if v < 34:
        return "audio-volume-low-symbolic"
    if v < 67:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


class Osd(Gtk.Window):
    def __init__(self, bottom_margin=64):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("osd-window")
        make_translucent(self)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, E.BOTTOM, True)
        GtkLayerShell.set_margin(self, E.BOTTOM, bottom_margin)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.set_accept_focus(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.get_style_context().add_class("osd")
        self.icon = Gtk.Image()
        self.icon.set_pixel_size(20)
        box.pack_start(self.icon, False, False, 0)
        self.bar = Gtk.LevelBar()
        self.bar.set_min_value(0)
        self.bar.set_max_value(100)
        self.bar.set_size_request(150, -1)
        self.bar.set_valign(Gtk.Align.CENTER)
        # 기본 구간 색(낮음/높음)을 지운다 — 하나의 강조색으로
        for name in ("low", "high", "full"):
            self.bar.remove_offset_value(name)
        box.pack_start(self.bar, True, True, 0)
        self.value = Gtk.Label()
        self.value.get_style_context().add_class("osd-value")
        self.value.set_width_chars(3)
        self.value.set_xalign(1)
        box.pack_start(self.value, False, False, 0)
        self.text = Gtk.Label()
        self.text.get_style_context().add_class("osd-text")
        box.pack_start(self.text, False, False, 0)
        self.add(box)
        self._timer = None

    def set_bottom(self, px):
        GtkLayerShell.set_margin(self, E.BOTTOM, px)

    def show_level(self, icon, value, dimmed=False):
        self.icon.set_from_icon_name(icon, Gtk.IconSize.MENU)
        self.icon.set_pixel_size(20)
        self.bar.show()
        self.value.show()
        self.text.hide()
        self.bar.set_value(max(0, min(100, value)))
        self.value.set_text(str(value))
        ctx = self.bar.get_style_context()
        (ctx.add_class if dimmed else ctx.remove_class)("muted")
        self._pop()

    def show_text(self, icon, text):
        self.icon.set_from_icon_name(icon, Gtk.IconSize.MENU)
        self.icon.set_pixel_size(20)
        self.bar.hide()
        self.value.hide()
        self.text.set_text(text)
        self.text.show()
        self._pop()

    def _pop(self):
        if not self.get_visible():
            self.get_child().show()
            self.icon.show()
            self.show()
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(SHOW_MS, self._hide)

    def _hide(self):
        self._timer = None
        self.hide()
        return False

    # ── 동작 + 표시 ──
    def volume(self, action):
        volume_do(action)
        if action == "mic-mute":
            m = mic_muted()
            self.show_text("microphone-sensitivity-muted-symbolic" if m
                           else "audio-input-microphone-symbolic",
                           "마이크 꺼짐" if m else "마이크 켜짐")
            return
        v, muted = volume_get()
        self.show_level(vol_icon(v, muted), v, dimmed=muted)

    def brightness(self, action):
        brightness_do(action)
        b = brightness_get()
        if b is None:
            self.show_text("display-brightness-symbolic", "밝기를 바꿀 수 없는 화면입니다")
            return
        self.show_level("display-brightness-symbolic", b)
