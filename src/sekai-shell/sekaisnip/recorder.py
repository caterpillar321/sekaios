"""캡처 도구의 화면 녹화 — wf-recorder 로 MP4(H.264 + AAC).

흐름: 범위 고르기(영역=slurp · 창=지금 창 · 전체=마우스가 있는 모니터) → 3·2·1 → 녹화 → 중지 → 파일.
  중지하는 길: 녹화 범위 밖에 띄우는 작은 막대의 [중지] · Win+Shift+R(sekai-snip --record 를 한 번 더) ·
  알림 센터의 [녹화 중지] (토스트는 바로 걷고 센터에만 둔다 — 영상에 찍히지 않게).
  막대는 화면 녹화에 같이 찍히므로(합성기가 레이어를 뺄 수 없다) 범위 밖에 자리가 있을 때만 띄운다.
소리: 시스템 소리(기본 출력의 모니터) · 마이크(기본 입력). 둘 다면 잠깐 섞는 장치(null sink)를 만들어 거기로 모은다.
인코딩: 인텔·AMD 는 VAAPI 를 먼저 해 보고, 곧바로 끝나면(지원 안 함) CPU(libx264)로 다시. NVIDIA 는 CPU.
  -D: 화면이 그대로여도 프레임을 받는다 — 안 그러면 가만히 있는 화면은 첫 프레임이 오지 않아 파일이 안 생겼다.
"""
import glob
import json
import os
import signal
import subprocess
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

from sekaishell.monitors import gdk_for  # noqa: E402

FPS = 30
COUNTDOWN = 3
BAR_H = 44                        # 막대가 들어갈 자리 (여백 포함)
MIX_SINK = "sekai_rec_mix"


def videos_dir():
    try:
        d = subprocess.run(["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        d = ""
    if not d or d == os.path.expanduser("~"):
        d = os.path.expanduser("~/Videos")
    d = os.path.join(d, "화면 녹화")
    os.makedirs(d, exist_ok=True)
    return d


def _hypr(*args):
    try:
        out = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True, timeout=3).stdout
        return json.loads(out)
    except Exception:
        return None


def monitors():
    return [m for m in (_hypr("monitors") or []) if not m.get("disabled")]


def _logical(m):
    s = m.get("scale") or 1.0
    w, h = m["width"], m["height"]
    if m.get("transform", 0) % 2:
        w, h = h, w
    return m["x"], m["y"], int(w / s), int(h / s)


def focused_monitor():
    ms = monitors()
    return next((m for m in ms if m.get("focused")), ms[0] if ms else None)


def active_window_box():
    w = _hypr("activewindow")
    if not w or not w.get("size"):
        return None
    (x, y), (a, b) = w["at"], w["size"]
    return x, y, a, b


def vaapi_device():
    """인텔·AMD 렌더 노드 — NVIDIA 는 VAAPI 가 기본으로 없다"""
    for node in sorted(glob.glob("/dev/dri/renderD*")):
        drv = os.path.basename(os.path.realpath(f"/sys/class/drm/{os.path.basename(node)}/device/driver"))
        if drv in ("i915", "xe", "amdgpu", "radeon"):
            return node
    return None


class Countdown(Gtk.Window):
    """3·2·1 — 모니터 가운데, 녹화가 시작되기 전에 사라진다"""

    def __init__(self, hmon, done, hint):
        super().__init__()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        gm = gdk_for(hmon)
        if gm is not None:
            GtkLayerShell.set_monitor(self, gm)
        self.get_style_context().add_class("rec-countdown")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.num = Gtk.Label()
        self.num.get_style_context().add_class("rec-count-num")
        sub = Gtk.Label(label=hint)
        sub.get_style_context().add_class("rec-count-sub")
        box.pack_start(self.num, False, False, 0)
        box.pack_start(sub, False, False, 0)
        self.add(box)
        self.n = COUNTDOWN
        self.done = done
        self._tick()
        self.show_all()
        GLib.timeout_add(1000, self._step)

    def _tick(self):
        self.num.set_text(str(self.n))

    def _step(self):
        self.n -= 1
        if self.n <= 0:
            self.destroy()
            GLib.timeout_add(250, self.done)     # 걷힌 뒤에 녹화 시작
            return False
        self._tick()
        return True


class StopBar(Gtk.Window):
    """녹화 중 막대 — ● 0:12  [■ 중지] · 녹화 범위 밖 (x, y 는 모니터 안 논리 좌표)"""

    def __init__(self, hmon, x, y, on_stop):
        super().__init__()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-recbar")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        gm = gdk_for(hmon)
        if gm is not None:
            GtkLayerShell.set_monitor(self, gm)
        for e in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT):
            GtkLayerShell.set_anchor(self, e, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, max(0, x))
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, max(0, y))
        self.get_style_context().add_class("rec-bar")
        box = Gtk.Box(spacing=8)
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("rec-dot")
        self.time = Gtk.Label(label="0:00")
        self.time.get_style_context().add_class("rec-time")
        stop = Gtk.Button()
        sb = Gtk.Box(spacing=6)
        sb.pack_start(Gtk.Image.new_from_icon_name("media-playback-stop-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        sb.pack_start(Gtk.Label(label="중지"), False, False, 0)
        stop.add(sb)
        stop.get_style_context().add_class("rec-stop")
        stop.connect("clicked", lambda *_: on_stop())
        for w in (dot, self.time, stop):
            box.pack_start(w, False, False, 0)
        self.add(box)
        self.show_all()

    def set_elapsed(self, secs):
        self.time.set_text(f"{int(secs) // 60}:{int(secs) % 60:02d}")


class Recorder:
    """한 번의 녹화. on_state(kind, value): ("started", None) · ("stopped", 경로) · ("error", 글) · ("cancelled", None)"""

    def __init__(self, on_state):
        self.on_state = on_state
        self.proc = None
        self.path = None
        self.bar = None
        self.mods = []
        self.nid = 0
        self.bus = None
        self.sub = 0
        self.t0 = 0
        self.timer = 0
        self.stopping = False
        self.args = None
        self.retried = False
        self.duration = 0.0               # 실제로 녹화한 시간 (초)

    @property
    def active(self):
        return self.proc is not None or self.args is not None

    # ── 범위 ──────────────────────────────────────────────
    def begin(self, mode, system_audio, mic):
        self.audio = (system_audio, mic)
        if mode == "region":
            try:
                p = Gio.Subprocess.new(["slurp", "-d", "-b", "00000077", "-c", "39c5bbff", "-s", "39c5bb18", "-w", "2"],
                                       Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
            except GLib.Error as e:
                self.on_state("error", f"영역을 고르지 못했습니다: {e.message}")
                return
            p.communicate_utf8_async(None, None, self._picked)
            return
        if mode == "window":
            box = active_window_box()
            if box:
                self._ready(box)
                return
        m = focused_monitor()
        if m is None:
            self.on_state("error", "모니터를 찾지 못했습니다")
            return
        self._ready(_logical(m), output=m["name"])

    def _picked(self, p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = ""
        g = (out or "").strip()
        try:
            xy, wh = g.split(" ")
            x, y = (int(v) for v in xy.split(","))
            w, h = (int(v) for v in wh.split("x"))
        except ValueError:
            self.on_state("cancelled", None)      # Esc
            return
        # 선택 화면이 걷힐 시간 (layerrule noanim selection 이지만 한 장면은 기다린다)
        GLib.timeout_add(120, lambda: (self._ready((x, y, w, h)), False)[1])

    # ── 막대 자리 · 카운트다운 ─────────────────────────────
    def _ready(self, box, output=None):
        x, y, w, h = box
        w, h = max(2, w - w % 2), max(2, h - h % 2)       # H.264 는 짝수 크기
        self.box = (x, y, w, h)
        self.path = os.path.join(videos_dir(), time.strftime("화면 녹화 %Y-%m-%d %H%M%S.mp4"))
        args = ["wf-recorder", "-y", "-D", "-r", str(FPS), "-f", self.path]
        args += ["-o", output] if output else ["-g", f"{x},{y} {w}x{h}"]
        self.base = args
        ms = monitors()
        mon, bar_xy = None, None
        for m in ms:
            mx, my, mw, mh = _logical(m)
            if mx <= x < mx + mw and my <= y < my + mh:
                mon = m
                cx = min(max(x - mx + w // 2 - 90, 8), mw - 190)
                if output is None and y - my >= BAR_H:            # 범위 위에 자리가 있으면 위, 없으면 아래
                    bar_xy = (m, cx, y - my - BAR_H)
                elif output is None and my + mh - (y + h) >= BAR_H:
                    bar_xy = (m, cx, y - my + h + 6)
        if bar_xy is None and len(ms) > 1:                         # 범위가 꽉 찼으면 다른 모니터 위쪽에
            other = next((m for m in ms if m is not mon), None)
            if other is not None:
                bar_xy = (other, _logical(other)[2] // 2 - 90, 12)
        self.bar_xy = bar_xy
        hint = "막대의 [중지] · Win+Shift+R 로 멈춥니다" if bar_xy else "Win+Shift+R 또는 알림 센터의 [녹화 중지]로 멈춥니다"
        self._notify_stop()                               # 토스트는 바로 걷힌다 — 카운트다운 동안
        Countdown(mon, self._start, hint)

    # ── 녹화 ──────────────────────────────────────────────
    def _audio_args(self):
        sysa, mic = self.audio
        if sysa and mic:
            src = self._mix()
            return [f"--audio={src}"] if src else ["--audio=@DEFAULT_MONITOR@"]
        if sysa:
            return ["--audio=@DEFAULT_MONITOR@"]
        if mic:
            return ["--audio=@DEFAULT_SOURCE@"]
        return []

    def _mix(self):
        """시스템 소리 + 마이크를 한 곳으로 — 녹화가 끝나면 지운다"""
        def load(*a):
            r = subprocess.run(["pactl", "load-module", *a], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip().isdigit():
                self.mods.append(r.stdout.strip())
                return True
            return False
        try:
            if not load("module-null-sink", f"sink_name={MIX_SINK}",
                        "sink_properties=device.description=SekaiRecordingMix"):
                return None
            load("module-loopback", "source=@DEFAULT_MONITOR@", f"sink={MIX_SINK}", "latency_msec=30")
            load("module-loopback", "source=@DEFAULT_SOURCE@", f"sink={MIX_SINK}", "latency_msec=30")
        except (OSError, subprocess.SubprocessError):
            self._unmix()
            return None
        return f"{MIX_SINK}.monitor"

    def _unmix(self):
        for m in reversed(self.mods):
            subprocess.run(["pactl", "unload-module", m], capture_output=True, timeout=5)
        self.mods = []

    def _start(self, use_vaapi=None):
        if use_vaapi is None:
            use_vaapi = vaapi_device()
        codec = (["-c", "h264_vaapi", "-d", use_vaapi] if use_vaapi
                 else ["-c", "libx264", "-p", "preset=veryfast", "-p", "crf=23", "-x", "yuv420p"])
        self.args = self.base + codec + self._audio_args()
        self.vaapi = bool(use_vaapi)
        try:
            self.proc = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError as e:
            self._finish(error=f"녹화를 시작하지 못했습니다: {e}")
            return False
        self.t0 = time.monotonic()
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.proc.pid, self._exited)
        if self.bar_xy:
            i, bx, by = self.bar_xy
            self.bar = StopBar(i, bx, by, self.stop)
        self.timer = GLib.timeout_add(500, self._tick)
        self.on_state("started", None)
        return False

    def _tick(self):
        if self.bar is not None and self.proc is not None:
            self.bar.set_elapsed(time.monotonic() - self.t0)
        return self.proc is not None

    def _exited(self, _pid, status):
        quick = time.monotonic() - self.t0 < 2.0
        self.duration = time.monotonic() - self.t0
        err = ""
        try:
            err = (self.proc.stderr.read() or "")[-400:] if self.proc and self.proc.stderr else ""
        except (OSError, ValueError):
            pass
        self.proc = None
        if quick and not self.stopping and self.vaapi and not self.retried:
            self.retried = True                       # VAAPI 를 못 쓴다 — CPU 인코딩으로 다시
            self._start(use_vaapi=False)
            return
        if self.stopping or (os.path.isfile(self.path or "") and os.path.getsize(self.path) > 1024):
            self._finish(path=self.path)
        else:
            self._finish(error="녹화가 멈췄습니다" + (f": {err.strip().splitlines()[-1]}" if err.strip() else ""))

    def stop(self):
        if self.proc is None:
            if self.args is None and not self.stopping:
                return
            self.stopping = True
            return
        if self.stopping:
            return
        self.stopping = True
        try:
            os.killpg(self.proc.pid, signal.SIGINT)        # wf-recorder 는 SIGINT 에 파일을 마저 쓰고 끝난다
        except OSError:
            pass

    def _finish(self, path=None, error=None):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.bar is not None:
            self.bar.destroy()
            self.bar = None
        self._unmix()
        self._close_notice()
        self.args = None
        if error:
            self.on_state("error", error)
        else:
            self.on_state("stopped", path)

    # ── 알림 센터의 [녹화 중지] ────────────────────────────
    def _notify_stop(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            hints = {"urgency": GLib.Variant("y", 1), "desktop-entry": GLib.Variant("s", "sekai-snip")}
            r = self.bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                   "org.freedesktop.Notifications", "Notify",
                                   GLib.Variant("(susssasa{sv}i)", ("캡처 도구", 0, "media-record",
                                                "화면 녹화 중", "Win+Shift+R 또는 [녹화 중지]로 멈춥니다.",
                                                ["stop", "녹화 중지"], hints, 1)),
                                   GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 3000, None)
            self.nid = r.unpack()[0]
            self.sub = self.bus.signal_subscribe("org.freedesktop.Notifications", "org.freedesktop.Notifications",
                                                 "ActionInvoked", "/org/freedesktop/Notifications", None,
                                                 Gio.DBusSignalFlags.NONE, self._action)
        except GLib.Error:
            self.nid = 0

    def _action(self, _c, _s, _p, _i, _sig, params):
        nid, key = params.unpack()
        if nid == self.nid and key == "stop":
            self.nid = 0
            self.stop()

    def _close_notice(self):
        if self.bus is None:
            return
        if self.sub:
            self.bus.signal_unsubscribe(self.sub)
            self.sub = 0
        if self.nid:
            try:
                self.bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                   "org.freedesktop.Notifications", "CloseNotification",
                                   GLib.Variant("(u)", (self.nid,)), None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
            self.nid = 0
