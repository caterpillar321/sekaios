"""음량·밝기 OSD — 키를 누르면 화면 아래 가운데에 잠깐 뜨는 알약.

윈도우 11 처럼: [아이콘] ━━━━━━━○──── 57
입력은 받지 않는다 (키보드 초점을 뺏지 않는다).
"""
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from .layer import GtkLayerShell
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


def volume_steps(n):
    """음량 n 단계(5%) — 양수면 올리고(음소거도 푼다) 음수면 내린다"""
    if n > 0:
        _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{5 * n}%+"])
    elif n < 0:
        _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{5 * -n}%-"])


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


def brightness_steps(n):
    if n > 0:
        _run(["brightnessctl", "-q", "set", f"{5 * n}%+"])
    elif n < 0:
        # 0 이 되면 화면이 완전히 꺼지는 패널이 있다 → 1% 아래로는 내리지 않는다
        _run(["brightnessctl", "-q", "-n1", "set", f"{5 * -n}%-"])


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
        # 작업 스레드가 처리할 몫 — 키를 누르고 있으면(초당 수십 번) 하나씩 처리하다 밀려서, 손을 뗀 뒤에도
        #   한참 음량이 바뀌었다. 쌓인 입력을 합쳐 한 번에 한다 (올림 12번 → 60%+ 한 번)
        self._lock = threading.Lock()
        self._wake = None
        self._want = {"vol": 0, "mute": 0, "mic": 0, "bright": 0}
        self._last = None          # 마지막에 누른 종류 — 그것을 보여 준다
        self.on_volume = None      # 음량을 바꾼 뒤 부른다 (작업 표시줄 아이콘 새로 고침) — 메인 스레드에서

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
    #   wpctl·brightnessctl 은 메인 스레드 밖에서 (PipeWire 가 바쁘면 몇 초씩 멈춰 패널 전체가 굳었다).
    #   작업 스레드 하나가 그동안 쌓인 입력을 합쳐 처리하고, 표시는 메인 스레드에서.
    def _add(self, kind, n):
        with self._lock:
            self._want[kind] += n
            self._last = kind
            if self._wake is None:
                self._wake = threading.Event()
                threading.Thread(target=self._worker, daemon=True).start()
            self._wake.set()

    def _worker(self):
        while True:
            self._wake.wait()
            with self._lock:
                self._wake.clear()
                want, last = dict(self._want), self._last
                for k in self._want:
                    self._want[k] = 0
            try:
                if want["mute"] % 2:                      # 켜고 끄기 — 짝수 번이면 그대로
                    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
                if want["mic"] % 2:
                    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
                volume_steps(want["vol"])
                brightness_steps(want["bright"])
                if last == "mic":
                    res = ("mic", mic_muted())
                elif last == "bright":
                    res = ("bright", brightness_get())
                else:
                    res = ("vol",) + volume_get()
            except Exception as e:
                dbg("osd 작업 실패", e)
                continue
            GLib.idle_add(lambda r=res: (self._show_result(r), False)[1])

    def _show_result(self, r):
        if r[0] == "mic":
            m = r[1]
            self.show_text("microphone-sensitivity-muted-symbolic" if m
                           else "audio-input-microphone-symbolic",
                           "마이크 꺼짐" if m else "마이크 켜짐")
        elif r[0] == "bright":
            if r[1] is None:
                self.show_text("display-brightness-symbolic", "밝기를 바꿀 수 없는 화면입니다")
            else:
                self.show_level("display-brightness-symbolic", r[1])
            return
        else:
            v, muted = r[1], r[2]
            self.show_level(vol_icon(v, muted), v, dimmed=muted)
        if self.on_volume:
            self.on_volume()

    def volume(self, action):
        if action == "up":
            self._add("vol", 1)
        elif action == "down":
            self._add("vol", -1)
        elif action == "mute":
            self._add("mute", 1)
        elif action == "mic-mute":
            self._add("mic", 1)

    def brightness(self, action):
        self._add("bright", 1 if action == "up" else -1)
