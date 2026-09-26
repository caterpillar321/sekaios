"""빠른 설정 — 윈도우 11 식 (Win+A, 작업 표시줄 오른쪽의 네트워크·음량·배터리 아이콘 묶음을 누르면).

  [Wi-Fi ›] [블루투스 ›] [비행기 모드]
  [방해 금지] [야간 모드] [다크 모드]
  ☀ ━━━━━━○────────
  🔊 ━━━━━━━━○────── ›
  ─────────────────────────────
  🔋 85%  충전 중                ⚙

› 를 누르면 같은 팝업 안에서 세부 보기 (Wi-Fi 네트워크 · 블루투스 장치 · 출력 장치).
  회사·학교(802.1X) 네트워크와 숨겨진 네트워크는 설정 앱의 Wi-Fi 연결 창(sekaisettings.pages.network.WifiDialog)을
  이 패널 안에서 띄운다 — 다른 데스크톱의 nm-applet 에 맡기지 않는다.
작업 표시줄 아이콘에 필요한 네트워크 상태는 NetworkManager 의 속성 바뀜 신호로 받는다 (묻기를 되풀이하지 않는다).
나머지 조회(nmcli·wpctl·brightnessctl)는 팝업이 열려 있는 동안만, 기다리지 않고 —
run 은 패널의 run_limited(argv, done, secs, capture).
"""
import os
import re
import shutil
import signal
import struct
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Pango  # noqa: E402

from . import config, dbg
from . import layer as _layer
from . import theme
from .layer import GtkLayerShell
from .osd import vol_icon
from .popup import ClickCatcher, PanelPopup

WIDTH = 360
LIST_MAX = 320          # 세부 목록의 최대 높이 — 넘으면 스크롤
NIGHT_TEMP = 4000       # 야간 모드 색온도 (K)


# ───────────────────────────────────────────────────────────────
# 작은 도우미
# ───────────────────────────────────────────────────────────────
def pick_icon(names):
    """후보 중 테마에 있는 첫 아이콘 이름"""
    theme_ = Gtk.IconTheme.get_default()
    for n in names:
        if n and theme_.has_icon(n):
            return n
    return names[-1] if names else "image-missing"


def set_icon(img, names, size=16):
    img.set_from_icon_name(pick_icon(names), Gtk.IconSize.MENU)
    img.set_pixel_size(size)


def spawn(argv):
    """앱 띄우기 — 기다리지 않는다 (패널이 다시 떠도 살아남게 새 세션으로)"""
    try:
        subprocess.Popen(argv, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        dbg("실행 실패", argv, e)


def _read(d, name):
    try:
        with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _num(d, name):
    try:
        return float(_read(d, name))
    except ValueError:
        return 0.0


def nm_fields(line):
    """nmcli -t 한 줄 → 칸들. 값 안의 : 와 \\ 는 \\: \\\\ 로 이스케이프되어 온다 (SSID 에 : 가 들어갈 수 있다)"""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_devices(text):
    """nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device → [{dev, type, state, conn}]"""
    out = []
    for line in (text or "").splitlines():
        f = nm_fields(line)
        if len(f) >= 4 and f[1] != "loopback":
            out.append({"dev": f[0], "type": f[1], "state": f[2], "conn": f[3]})
    return out


def parse_wifi(text):
    """nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list → SSID 마다 하나 (가장 센 것),
    연결된 것 먼저, 그다음 신호 센 순서. 숨은 SSID(빈 이름)는 뺀다"""
    nets = {}
    for line in (text or "").splitlines():
        f = nm_fields(line)
        if len(f) < 4 or not f[1]:
            continue
        try:
            sig = int(f[2])
        except ValueError:
            sig = 0
        sec = f[3].strip()
        sec = "" if sec in ("--", "") else sec
        active = f[0].strip() == "*"
        cur = nets.get(f[1])
        if cur is None:
            nets[f[1]] = {"ssid": f[1], "signal": sig, "sec": sec, "active": active}
        else:
            cur["active"] = cur["active"] or active
            if sig > cur["signal"]:
                cur["signal"], cur["sec"] = sig, sec
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"], n["ssid"].lower()))


def signal_level(strength):
    """신호 세기(0~100) → 아이콘 단계 (GNOME 과 같은 경계)"""
    s = strength or 0
    if s > 80:
        return "excellent"
    if s > 55:
        return "good"
    if s > 30:
        return "ok"
    if s > 5:
        return "weak"
    return "none"


_SINK_ROW = re.compile(r"^(\*)?\s*(\d+)\.\s+(.*?)\s*(\[vol:[^\]]*\])?\s*$")


def parse_sinks(text):
    """wpctl status 의 Audio → Sinks 부분 → [{id, name, default}]. 기본 출력은 * 표시"""
    out, section, sub = [], "", ""
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            section, sub = line.strip(), ""              # PipeWire / Audio / Video / Settings
            continue
        s = line.strip(" \t│├└─")
        if not s:
            continue
        m = _SINK_ROW.match(s)
        if m is None:
            if s.endswith(":"):
                sub = s[:-1].strip()                      # Devices / Sinks / Sources / Filters / Streams
            continue
        if section == "Audio" and sub == "Sinks":
            out.append({"id": int(m.group(2)), "name": m.group(3).strip(), "default": bool(m.group(1))})
    return out


def parse_brightness(text):
    """brightnessctl -m info → 백분율 (형식: 장치,종류,현재,백분율%,최대)"""
    try:
        return int((text or "").strip().splitlines()[0].split(",")[3].rstrip("%"))
    except (IndexError, ValueError):
        return None


def has_backlight():
    try:
        return bool(os.listdir("/sys/class/backlight")) and shutil.which("brightnessctl") is not None
    except OSError:
        return False


def sys_has_wifi():
    """무선 랜 장치가 있나 (커널 기준 — NetworkManager 에 묻기 전)"""
    try:
        return any(os.path.isdir(f"/sys/class/net/{n}/wireless") for n in os.listdir("/sys/class/net"))
    except OSError:
        return False


def read_battery(base="/sys/class/power_supply"):
    """노트북 배터리 → {"pct", "state", "secs"} 또는 None.
    state: charging · discharging · full · plugged(연결됐지만 충전 안 함). secs: 남은/완충까지 시간(모르면 0).
    마우스·키보드처럼 기기에 딸린 배터리(scope=Device)는 뺀다"""
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    now = full = rate = 0.0
    caps, states, ac, found = [], [], False, False
    for n in names:
        d = os.path.join(base, n)
        typ = _read(d, "type")
        if typ == "Mains":
            ac = ac or _read(d, "online") == "1"
            continue
        if typ != "Battery" or _read(d, "scope") == "Device" or _read(d, "present") == "0":
            continue
        found = True
        states.append(_read(d, "status"))
        c = _read(d, "capacity")
        if c.isdigit():
            caps.append(int(c))
        # 에너지(µWh·µW)가 있으면 그것을, 없으면 전하(µAh·µA) — 짝을 맞춰야 시간이 맞다
        if _read(d, "energy_full"):
            now, full, rate = now + _num(d, "energy_now"), full + _num(d, "energy_full"), rate + abs(_num(d, "power_now"))
        elif _read(d, "charge_full"):
            now, full, rate = now + _num(d, "charge_now"), full + _num(d, "charge_full"), rate + abs(_num(d, "current_now"))
    if not found:
        return None
    if full > 0:
        pct = round(100 * now / full)
    elif caps:
        pct = round(sum(caps) / len(caps))
    else:
        pct = 0
    pct = max(0, min(100, pct))
    if "Charging" in states:
        state = "charging"
    elif states and all(s == "Full" for s in states):
        state = "full"
    elif "Discharging" in states:
        state = "discharging"
    else:
        state = "plugged" if ac else "discharging"       # Not charging · Unknown
    secs = 0
    if rate > 0 and full > 0:
        if state == "charging":
            secs = int((full - now) / rate * 3600)
        elif state == "discharging":
            secs = int(now / rate * 3600)
    if secs > 48 * 3600:
        secs = 0                                          # 방금 뽑았을 때 등 — 믿을 수 없는 값
    return {"pct": pct, "state": state, "secs": max(0, secs)}


def _duration(secs):
    h, m = secs // 3600, (secs % 3600) // 60
    if h:
        return f"약 {h}시간 {m}분" if m else f"약 {h}시간"
    return f"약 {max(1, m)}분"


def battery_text(b):
    """(아이콘 후보들, 한 줄 설명)"""
    pct, st = b["pct"], b["state"]
    lvl = min(100, int(round(pct / 10.0)) * 10)
    if st == "full" or (st == "plugged" and pct >= 95):
        icons = ["battery-level-100-charged-symbolic", "battery-full-charged-symbolic", "battery-full-symbolic"]
        text = "완전히 충전됨" if st == "full" else "전원 연결됨"
    elif st in ("charging", "plugged"):
        icons = [f"battery-level-{lvl}-charging-symbolic", "battery-good-charging-symbolic", "battery-symbolic"]
        text = "충전 중" if st == "charging" else "전원 연결됨"
        if st == "charging" and b["secs"]:
            text += f" · {_duration(b['secs'])} 후 완충"
    else:
        rough = ("battery-empty-symbolic" if pct < 5 else "battery-caution-symbolic" if pct < 20
                 else "battery-low-symbolic" if pct < 40 else "battery-good-symbolic" if pct < 80
                 else "battery-full-symbolic")
        icons = [f"battery-level-{lvl}-symbolic", rough, "battery-symbolic"]
        text = f"{_duration(b['secs'])} 남음" if b["secs"] else "배터리 사용 중"
    return icons, text


# ───────────────────────────────────────────────────────────────
# 무선 차단 (rfkill) — 비행기 모드
# ───────────────────────────────────────────────────────────────
RF_ALL, RF_WLAN, RF_BT = 0, 1, 2
RF_TYPES = {"wlan": 1, "bluetooth": 2, "uwb": 3, "wimax": 4, "wwan": 5, "gps": 6, "fm": 7, "nfc": 8}
RF_ADD, RF_DEL, RF_CHANGE, RF_CHANGE_ALL = 0, 1, 2, 3
_RF_EV = struct.Struct("=IBBBB")        # struct rfkill_event 의 앞 8바이트: idx type op soft hard


class Rfkill:
    """/dev/rfkill — 장치 목록과 바뀜은 읽어서(커널이 알려 준다), 끄고 켜기는 써서.
    rfkill 명령이 설치돼 있지 않아도 된다. 로그인한 사용자는 uaccess 로 읽고 쓸 수 있다.
    못 열면 /sys/class/rfkill 을 읽는다 (그땐 바뀜을 스스로 알 수 없어 poll 로)"""
    DEV = "/dev/rfkill"

    def __init__(self, on_change=None):
        self.devs = {}                  # idx → (type, soft, hard)
        self.on_change = on_change
        self._fd = -1
        try:
            self._fd = os.open(self.DEV, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            self._read_sys()
            return
        self._drain()                   # 열자마자 모든 장치가 ADD 로 들어온다
        GLib.io_add_watch(GLib.IOChannel.unix_new(self._fd), GLib.PRIORITY_DEFAULT,
                          GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR, self._io)

    @property
    def available(self):
        """비행기 모드를 쓸 수 있나 — 무선 장치가 있고 차단을 쓸 수 있을 때"""
        return bool(self.devs) and os.access(self.DEV, os.W_OK)

    def _drain(self):
        changed = False
        while True:
            try:
                data = os.read(self._fd, _RF_EV.size)
            except OSError:              # BlockingIOError — 다 읽었다
                break
            if len(data) < _RF_EV.size:
                break
            idx, typ, op, soft, hard = _RF_EV.unpack(data)
            if op == RF_DEL:
                self.devs.pop(idx, None)
            elif op in (RF_ADD, RF_CHANGE):
                self.devs[idx] = (typ, bool(soft), bool(hard))
            changed = True
        return changed

    def _io(self, _ch, cond):
        if cond & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            os.close(self._fd)
            self._fd = -1
            self._read_sys()
            return False
        if self._drain() and self.on_change:
            self.on_change()
        return True

    def _read_sys(self):
        devs = {}
        base = "/sys/class/rfkill"
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        for n in names:
            d = os.path.join(base, n)
            try:
                idx = int(n.replace("rfkill", ""))
            except ValueError:
                continue
            devs[idx] = (RF_TYPES.get(_read(d, "type"), 0), _read(d, "soft") == "1", _read(d, "hard") == "1")
        changed = devs != self.devs
        self.devs = devs
        return changed

    def poll(self):
        """/dev/rfkill 을 못 열었을 때만 — 팝업이 열려 있는 동안 가끔"""
        if self._fd < 0 and self._read_sys() and self.on_change:
            self.on_change()

    def blocked(self, typ):
        return any((s or h) for t, s, h in self.devs.values() if t == typ)

    def all_blocked(self):
        return bool(self.devs) and all((s or h) for _t, s, h in self.devs.values())

    def set_block(self, typ, block):
        """typ(RF_ALL = 전부) 을 소프트 차단/해제. 성공하면 True"""
        try:
            fd = os.open(self.DEV, os.O_WRONLY | os.O_CLOEXEC)
            try:
                os.write(fd, _RF_EV.pack(0, typ, RF_CHANGE_ALL, 1 if block else 0, 0))
            finally:
                os.close(fd)
        except OSError as e:
            dbg("rfkill 쓰기 실패", e)
            return False
        # 바뀐 상태를 곧바로 읽어 둔다 (커널은 쓰는 동안 이벤트를 쌓는다) — 타일이 잠깐 옛 상태로 튀지 않게
        if self._fd >= 0:
            self._drain()
        else:
            self._read_sys()
        return True


# ───────────────────────────────────────────────────────────────
# NetworkManager 상태 (시스템 버스)
# ───────────────────────────────────────────────────────────────
NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_AC = NM + ".Connection.Active"
NM_AP = NM + ".AccessPoint"
NM_CONNECTING, NM_CONNECTED_LOCAL, NM_CONNECTED_GLOBAL = 40, 50, 70


def _prop(proxy, name, default=None):
    if proxy is None:
        return default
    v = proxy.get_cached_property(name)
    return default if v is None else v.unpack()


def _kind(t):
    if t == "802-11-wireless":
        return "wifi"
    if t in ("vpn", "wireguard"):
        return "vpn"
    return "wired" if t else ""


class NetState:
    """작업 표시줄 아이콘에 필요한 것만: 전체 상태, 주 연결(없으면 연결 중인 것)의 종류·이름, Wi-Fi 신호 세기, Wi-Fi 켜짐.
    주 연결 → 활성 연결 객체 → 접속점(AP) 을 따라가며 각각의 속성 바뀜 신호를 받는다.
    NetworkManager 가 다시 떠도 GDBusProxy 가 속성을 새로 읽어 알려 준다 (g-name-owner)."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.running = False
        self.state = 0
        self.kind = ""
        self.name = ""
        self.strength = None
        self.wifi_enabled = False
        self._nm = self._ac = self._ap = None
        self._ac_path = self._ap_path = "/"
        self._proxy(NM_PATH, NM, self._got_nm)

    @staticmethod
    def _proxy(path, iface, done):
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                                  NM, path, iface, None, done)

    def _got_nm(self, _src, res, *_):
        try:
            self._nm = Gio.DBusProxy.new_for_bus_finish(res)
        except GLib.Error as e:
            dbg("NetworkManager 에 붙지 못함", e.message)
            return
        self._nm.connect("g-properties-changed", lambda *_a: self._update())
        self._nm.connect("notify::g-name-owner", lambda *_a: self._update())
        self._update()

    def _update(self):
        nm = self._nm
        self.running = nm.get_name_owner() is not None
        self.state = int(_prop(nm, "State", 0) or 0) if self.running else 0
        self.wifi_enabled = bool(_prop(nm, "WirelessEnabled", False)) if self.running else False
        ac = (_prop(nm, "PrimaryConnection", "/") or "/") if self.running else "/"
        if ac == "/" and self.running:
            ac = _prop(nm, "ActivatingConnection", "/") or "/"
        if ac != self._ac_path:
            self._watch_ac(ac)
        self._emit()

    def _watch_ac(self, path):
        self._ac_path, self._ac = path, None
        self._watch_ap("/")
        if path == "/":
            return

        def got(_src, res, *_):
            if path != self._ac_path:           # 그사이 바뀌었다
                return
            try:
                self._ac = Gio.DBusProxy.new_for_bus_finish(res)
            except GLib.Error:
                return
            self._ac.connect("g-properties-changed", lambda *_a: self._ac_changed())
            self._ac.connect("notify::g-name-owner", lambda *_a: self._ac_changed())
            self._ac_changed()
        self._proxy(path, NM_AC, got)

    def _ac_changed(self):
        ap = _prop(self._ac, "SpecificObject", "/") or "/"
        if _prop(self._ac, "Type", "") != "802-11-wireless":
            ap = "/"
        if ap != self._ap_path:
            self._watch_ap(ap)
        self._emit()

    def _watch_ap(self, path):
        self._ap_path, self._ap = path, None
        if path == "/":
            return

        def got(_src, res, *_):
            if path != self._ap_path:
                return
            try:
                self._ap = Gio.DBusProxy.new_for_bus_finish(res)
            except GLib.Error:
                return
            # 신호 세기는 몇 초마다 바뀐다 — 아이콘만 다시 고른다
            self._ap.connect("g-properties-changed", lambda *_a: self._emit())
            self._emit()
        self._proxy(path, NM_AP, got)

    def _emit(self):
        t = _prop(self._ac, "Type", "") or (_prop(self._nm, "PrimaryConnectionType", "") if self.running else "")
        self.kind = _kind(t)
        self.name = _prop(self._ac, "Id", "") or ""
        self.strength = None
        if self._ap is not None:
            s = _prop(self._ap, "Strength", None)
            self.strength = int(s) if s is not None else None
            ssid = _prop(self._ap, "Ssid", None)
            if ssid:
                self.name = bytes(ssid).decode("utf-8", "replace")
        if self.on_change:
            self.on_change()


def net_icon(ns, airplane, has_wifi):
    """(아이콘 후보들, 설명) — 작업 표시줄 아이콘. NetworkManager 가 없으면 None (아이콘을 숨긴다)"""
    if airplane:
        return ["airplane-mode-symbolic", "network-offline-symbolic"], "비행기 모드"
    if not ns.running:
        return None
    wifi = ns.kind == "wifi" or (not ns.kind and has_wifi)
    if ns.state == NM_CONNECTING:
        return ((["network-wireless-acquiring-symbolic"] if wifi else ["network-wired-acquiring-symbolic"])
                + ["network-transmit-receive-symbolic"], "연결하는 중…")
    if ns.state >= NM_CONNECTED_LOCAL:
        limited = ns.state < NM_CONNECTED_GLOBAL
        if ns.kind == "wifi":
            icons = ["network-wireless-no-route-symbolic"] if limited else []
            icons += [f"network-wireless-signal-{signal_level(ns.strength)}-symbolic", "network-wireless-symbolic"]
        elif ns.kind == "vpn":
            icons = ["network-vpn-symbolic", "network-wired-symbolic"]
        else:
            icons = (["network-wired-no-route-symbolic"] if limited else []) + ["network-wired-symbolic"]
        name = ns.name or ("Wi-Fi" if ns.kind == "wifi" else "이더넷")
        return icons, f"{name}\n" + ("인터넷에 연결되지 않음" if limited else "인터넷 액세스")
    if has_wifi and not ns.wifi_enabled:
        return (["network-wireless-disabled-symbolic", "network-wireless-offline-symbolic", "network-offline-symbolic"],
                "Wi-Fi 꺼짐")
    if has_wifi:
        return (["network-wireless-disconnected-symbolic", "network-wireless-offline-symbolic",
                 "network-wireless-signal-none-symbolic", "network-offline-symbolic"], "연결되지 않음")
    return ["network-wired-disconnected-symbolic", "network-offline-symbolic"], "연결되지 않음 — 케이블을 확인하세요"


# ───────────────────────────────────────────────────────────────
# Wi-Fi 연결 (작업 스레드에서)
# ───────────────────────────────────────────────────────────────
NEED_PW = "need-password"


def _nmcli(argv, timeout):
    """(작업 스레드) nmcli 실행 → None(성공) 또는 오류 한 줄.
    오류 문구로 "암호가 필요함"을 알아보므로 영어(C.UTF-8)로 — 한국어 번역이 깔려 있으면 문구가 바뀐다"""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8"))
    except subprocess.TimeoutExpired:
        return "시간이 초과되었습니다"
    except OSError as e:
        return str(e)
    if r.returncode == 0:
        return None
    lines = (r.stderr or r.stdout).strip().splitlines()
    return lines[-1] if lines else "알 수 없는 오류"


def _secrets_error(err):
    return "Secrets were required" in err or "802-11-wireless-security" in err or "802.1X" in err


def is_enterprise(sec):
    """회사·학교 네트워크 (WPA2/WPA3-Enterprise, 802.1X) — 암호 하나가 아니라 계정으로 로그인한다"""
    return "802.1X" in (sec or "").upper()


def needs_key(sec):
    """암호를 물어야 하는 보안 — 개방과 OWE(보안 개방)는 암호가 없다"""
    return bool(sec) and sec.strip().upper() != "OWE"


def _friendly(err):
    if err is None or err == NEED_PW:
        return err
    if _secrets_error(err):
        return "암호가 맞지 않습니다"
    if "No network with SSID" in err:
        return "네트워크를 찾을 수 없습니다"
    return re.sub(r"^Error:\s*", "", err)


def saved_wifi_uuid(ssid):
    """(작업 스레드) 이 SSID 의 저장된 프로필 — 있으면 UUID. 프로필 이름이 SSID 와 다를 수 있어 SSID 를 직접 본다"""
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"],
                             capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        f = nm_fields(line)
        if len(f) < 2 or f[1] != "802-11-wireless":
            continue
        try:
            got = subprocess.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", f[0]],
                                 capture_output=True, text=True, timeout=8).stdout.rstrip("\n")
        except (OSError, subprocess.SubprocessError):
            continue
        if re.sub(r"\\(.)", r"\1", got) == ssid:     # -g 도 : 와 \ 를 이스케이프한다
            return f[0]
    return None


def wifi_connect(ssid, sec, pw, dev):
    """(작업 스레드) Wi-Fi 연결 → None(성공) · NEED_PW(암호를 물어야 한다) · 오류 한 줄.
    암호는 명령줄에 넣지 않는다 — 설정 앱의 wifi_connect_secret (메모리 파일 passwd-file) 을 그대로 쓴다.
    저장된 프로필이 있으면 그것으로 (암호를 다시 묻지 않는다). 새 보안 네트워크에 암호 없이 nmcli 로
    연결하면 비밀 에이전트(nm-agent)가 따로 창을 띄우므로 먼저 프로필을 찾아 본다."""
    if pw is not None:
        try:
            from sekaisettings.pages.network import wifi_connect_secret
        except Exception as e:                            # 설정 앱이 없는 설치본
            dbg("wifi_connect_secret 를 가져오지 못함", e)
            return "설정 앱의 네트워크 페이지에서 연결해 주세요"
        return _friendly(wifi_connect_secret(ssid, sec, pw, dev))
    if needs_key(sec):
        uuid = saved_wifi_uuid(ssid)
        if uuid is None:
            return NEED_PW
        err = _nmcli(["nmcli", "-w", "30", "connection", "up", "uuid", uuid], 45)
        return NEED_PW if err and _secrets_error(err) else _friendly(err)
    argv = ["nmcli", "-w", "30", "device", "wifi", "connect", ssid]
    if dev:
        argv += ["ifname", dev]
    return _friendly(_nmcli(argv, 45))


# ───────────────────────────────────────────────────────────────
# 야간 모드 (wlsunset)
# ───────────────────────────────────────────────────────────────
class NightLight:
    """wlsunset 으로 화면 색온도를 낮춘다 — 낮·밤 온도를 거의 같게 줘서 켜면 바로, 늘 따뜻하게.
    켜짐은 상태 파일(nightlight)에 남겨 다음 로그인·패널 재시작 때 다시 켠다.
    패널이 죽으면 wlsunset 도 같이 끝나게 (setpriv --pdeathsig). 그래도 남아 있으면(setpriv 가 없을 때)
    새로 띄우지 않고 이어받는다 — 두 개가 서로 감마를 덮어쓰지 않게"""
    PIDFILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or config.STATE_DIR, "sekai-nightlight.pid")

    def __init__(self, on_change):
        self.on_change = on_change
        self.available = _layer.WAYLAND and shutil.which("wlsunset") is not None
        self.failed = False               # 곧바로 죽었다 — 이 화면(컴포지터)에선 안 된다
        self._proc = None                 # 우리가 띄운 것
        self._pid = 0                     # 이어받은 것 (패널이 다시 떴다)
        self._started = 0.0
        if not self.available:
            return
        pid = self._stale_pid()
        if config.state("nightlight", False):
            if pid:
                self._pid = pid
            else:
                self._start()
        elif pid:
            self._kill(pid)

    @property
    def on(self):
        return self._proc is not None or self._pid > 0

    def set(self, on):
        if not self.available or bool(on) == self.on:
            return
        config.set_state("nightlight", bool(on))
        self.failed = False
        if on:
            self._start()
        else:
            self._stop()
        self.on_change()

    def _stale_pid(self):
        try:
            with open(self.PIDFILE, encoding="utf-8") as f:
                pid = int(f.read().strip())
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0")[0]
            if os.path.basename(argv0) != b"wlsunset" or os.stat(f"/proc/{pid}").st_uid != os.getuid():
                return 0
            return pid
        except (OSError, ValueError):
            return 0

    @staticmethod
    def _kill(pid):
        try:
            os.kill(pid, signal.SIGTERM)          # TERM 이면 wlsunset 이 색을 되돌리고 끝난다
        except OSError:
            pass

    def _start(self):
        try:
            temp = max(1000, min(6400, int(config.state("nightlight_temp", NIGHT_TEMP) or NIGHT_TEMP)))
        except (TypeError, ValueError):
            temp = NIGHT_TEMP
        # 위치를 모르므로 해 뜨고 지는 시각을 직접 준다 — 낮(-T)과 밤(-t) 온도가 1K 차이라 언제나 같다
        argv = ["wlsunset", "-t", str(temp), "-T", str(temp + 1), "-S", "06:00", "-s", "18:00"]
        if shutil.which("setpriv"):
            argv = ["setpriv", "--pdeathsig", "TERM"] + argv
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            dbg("wlsunset 실행 실패", e.message)
            self.failed = True
            config.set_state("nightlight", False)
            return
        self._proc, self._started = proc, time.monotonic()
        try:
            os.makedirs(os.path.dirname(self.PIDFILE), exist_ok=True)
            with open(self.PIDFILE, "w", encoding="utf-8") as f:
                f.write(proc.get_identifier() or "")
        except OSError:
            pass
        proc.wait_async(None, self._exited)

    def _exited(self, proc, res, *_):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        if proc is not self._proc:               # 우리가 끈 것
            return
        self._proc = None
        self._rm_pidfile()
        if time.monotonic() - self._started < 5:
            self.failed = True
            dbg("wlsunset 이 곧바로 끝났다 — 이 화면에선 야간 모드를 쓸 수 없다")
        config.set_state("nightlight", False)
        self.on_change()

    def _stop(self):
        p, self._proc = self._proc, None
        if p is not None:
            p.send_signal(signal.SIGTERM)
        elif self._pid:
            self._kill(self._pid)
        self._pid = 0
        self._rm_pidfile()

    def _rm_pidfile(self):
        try:
            os.unlink(self.PIDFILE)
        except OSError:
            pass


# ───────────────────────────────────────────────────────────────
# 위젯
# ───────────────────────────────────────────────────────────────
class LastValue:
    """슬라이더 값 보내기 — 마지막 값만. 명령은 한 번에 하나만 띄우고, 도는 동안 바뀐 값은 끝난 뒤 한 번 더
    (값마다 따로 띄우면 늦게 끝난 옛 값이 이겨 슬라이더와 실제 값이 어긋났다 — 예전 음량 팝업의 _set_volume)"""

    def __init__(self, run, argv, after=None):
        self.run, self.argv, self.after = run, argv, after
        self.want = None
        self.busy = False

    def send(self, value):
        self.want = value
        if self.busy:
            return
        self.busy = True

        def done(_out, sent=value):
            self.busy = False
            if self.want != sent:
                self.send(self.want)
            elif self.after:
                self.after()
        self.run(self.argv(value), done)


def _flat_btn(cls, child=None, tip=None):
    b = Gtk.Button()
    b.get_style_context().add_class(cls)
    b.set_relief(Gtk.ReliefStyle.NONE)
    if child is not None:
        b.add(child)
    if tip:
        b.set_tooltip_text(tip)
    return b


def _icon_btn(names, tip, cb, size=16):
    img = Gtk.Image()
    set_icon(img, names, size)
    b = _flat_btn("qs-icon-btn", img, tip)
    b.set_valign(Gtk.Align.CENTER)
    b.connect("clicked", lambda *_: cb())
    b.img = img
    return b


def _chevron(text="›"):
    """세부 보기 표시 › · ‹ — 글자로 (아이콘 테마마다 go-next 가 화살표이기도 해서)"""
    lbl = Gtk.Label(label=text)
    lbl.get_style_context().add_class("qs-chevron")
    return lbl


class Tile(Gtk.Box):
    """빠른 설정 타일 — [아이콘 | ›] 버튼과 아래 이름. 아이콘 쪽은 켜고 끄기, › 는 세부 보기"""

    def __init__(self, icons, label, on_click, on_more=None, more_tip=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.top.get_style_context().add_class("qs-tile")
        self.img = Gtk.Image()
        set_icon(self.img, icons, 18)
        self.btn = _flat_btn("qs-tile-main", self.img)
        self.btn.connect("clicked", lambda *_: on_click())
        self.top.pack_start(self.btn, True, True, 0)
        if on_more is not None:
            self.top.get_style_context().add_class("has-more")
            m = _flat_btn("qs-tile-more", _chevron(), more_tip)
            m.connect("clicked", lambda *_: on_more())
            self.top.pack_end(m, False, False, 0)
        self.label = Gtk.Label(label=label)
        self.label.get_style_context().add_class("qs-tile-label")
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_max_width_chars(12)
        self.pack_start(self.top, False, False, 0)
        self.pack_start(self.label, False, False, 0)
        self.shown = True               # 격자에 넣을지 (QuickSettings._layout_tiles)

    def set_on(self, on):
        ctx = self.top.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("on")

    def set_icon(self, icons):
        set_icon(self.img, icons, 18)

    def set_label(self, text, tip=None):
        self.label.set_text(text)
        self.set_tooltip_text(tip or text)


class _Detail:
    """세부 보기 한 장 — [‹ 제목 (스위치)] · 목록 · 설정 링크"""

    def __init__(self, qs, title, link_label, link_page, on_switch=None):
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for c in ("qs-body", "qs-detail"):
            self.box.get_style_context().add_class(c)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back = _flat_btn("qs-icon-btn", _chevron("‹"), "뒤로")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", lambda *_: qs.show_main())
        head.pack_start(back, False, False, 0)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("qs-title")
        head.pack_start(t, True, True, 0)
        self.switch = None
        self._guard = False
        if on_switch is not None:
            self.switch = Gtk.Switch()
            self.switch.set_valign(Gtk.Align.CENTER)
            self.switch.connect("notify::active",
                                lambda s, _p: None if self._guard else on_switch(s.get_active()))
            head.pack_end(self.switch, False, False, 0)
        self.box.pack_start(head, False, False, 0)

        self.note = Gtk.Label(xalign=0.5)
        self.note.get_style_context().add_class("qs-note")
        self.note.set_line_wrap(True)
        self.box.pack_start(self.note, False, False, 0)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(LIST_MAX)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.list.get_style_context().add_class("qs-list")
        self.scroll.add(self.list)
        self.box.pack_start(self.scroll, True, True, 0)

        link = Gtk.Button(label=link_label)
        link.get_style_context().add_class("qs-link")
        link.set_relief(Gtk.ReliefStyle.NONE)
        link.set_halign(Gtk.Align.START)
        link.connect("clicked", lambda *_: qs.open_settings(link_page))
        self.box.pack_start(link, False, False, 0)

        self.box.show_all()
        self.note.set_no_show_all(True)
        self.note.hide()

    def set_switch(self, on):
        if self.switch is not None and self.switch.get_active() != bool(on):
            self._guard = True
            self.switch.set_active(bool(on))
            self._guard = False

    def set_note(self, text):
        self.note.set_text(text or "")
        self.note.set_visible(bool(text))

    def clear(self):
        for c in self.list.get_children():
            self.list.remove(c)
            c.destroy()


class _RowUI:
    """펼친 목록 줄의 동작 영역 — 상태 글, (Wi-Fi) 암호 칸, 버튼"""

    def __init__(self, act, label, primary, on_go, password=False):
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("qs-status")
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(36)
        act.pack_start(self.status, False, False, 0)
        self.entry = None
        if password:
            e = Gtk.Entry()
            e.get_style_context().add_class("qs-entry")
            e.set_visibility(False)
            e.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            e.set_placeholder_text("네트워크 보안 키")
            e.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
            e.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "암호 보기")
            e.connect("icon-press", self._reveal)
            e.connect("activate", lambda *_: on_go())
            act.pack_start(e, False, False, 0)
            self.entry = e
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        self.go = Gtk.Button(label=label)
        self.go.get_style_context().add_class("qs-primary" if primary else "pop-btn")
        self.go.connect("clicked", lambda *_: on_go())
        row.pack_end(self.go, False, False, 0)
        act.pack_start(row, False, False, 0)
        act.show_all()
        for w in (self.status, self.entry):
            if w is not None:
                w.set_no_show_all(True)
                w.hide()

    def _reveal(self, e, _pos, _ev):
        vis = not e.get_visibility()
        e.set_visibility(vis)
        e.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY,
                                  "view-conceal-symbolic" if vis else "view-reveal-symbolic")

    def set_status(self, text, error=False):
        self.status.set_text(text or "")
        ctx = self.status.get_style_context()
        (ctx.add_class if error else ctx.remove_class)("error")
        self.status.set_visible(bool(text))

    def busy(self, on):
        self.go.set_sensitive(not on)
        if self.entry is not None:
            self.entry.set_sensitive(not on)

    def ask_password(self):
        if self.entry is not None:
            self.entry.show()
            self.entry.grab_focus()

    def password(self):
        if self.entry is None or not self.entry.get_visible():
            return None
        return self.entry.get_text()


class StatusButton(Gtk.Button):
    """작업 표시줄의 네트워크·음량·배터리 아이콘 묶음 (윈도우 11 처럼 한 버튼) — 누르면 빠른 설정, 휠은 음량"""

    def __init__(self, qs, on_scroll=None, monitor=None):
        super().__init__()
        ctx = self.get_style_context()
        ctx.add_class("sys-item")
        ctx.add_class("qs-btn")
        self.set_valign(Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.net = Gtk.Image()
        self.vol = Gtk.Image()
        self.bat = Gtk.Image()
        set_icon(self.vol, ["audio-volume-high-symbolic"])
        for img in (self.net, self.vol, self.bat):
            box.pack_start(img, False, False, 0)
        self.add(box)
        box.show_all()
        for img in (self.net, self.bat):             # 보일지는 상태가 정한다 (show_all 이 되살리지 않게)
            img.set_no_show_all(True)
            img.hide()
        self.vol.set_tooltip_text("음량")
        # 눌린 모니터에 연다 (monitor: 작업 표시줄의 Gdk 모니터를 돌려주는 함수)
        self.connect("clicked", lambda *_: qs.toggle(monitor() if callable(monitor) else monitor))
        if on_scroll is not None:
            self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
            self.connect("scroll-event", on_scroll)

    def show_net(self, info):
        if info is None:
            self.net.hide()
            return
        set_icon(self.net, info[0])
        self.net.set_tooltip_text(info[1])
        self.net.show()

    def show_volume(self, r):
        if r is None:
            self.vol.set_tooltip_text("음량")
            return
        v, muted = r
        pct = int(round(v * 100))
        set_icon(self.vol, [vol_icon(pct, muted)])
        self.vol.set_tooltip_text("음소거" if muted else f"음량 {pct}%")
        ctx = self.vol.get_style_context()
        (ctx.add_class if muted else ctx.remove_class)("muted")

    def show_battery(self, b):
        if b is None:
            self.bat.hide()
            return
        icons, text = battery_text(b)
        set_icon(self.bat, icons)
        self.bat.set_tooltip_text(f"배터리 {b['pct']}%\n{text}")
        self.bat.show()


# ───────────────────────────────────────────────────────────────
# 빠른 설정 팝업
# ───────────────────────────────────────────────────────────────
class QuickSettings(PanelPopup):
    """run: 패널의 run_limited. notifications: 알림 서비스(방해 금지를 같이 쓴다).
    on_volume: 음량을 바꾼 뒤 부른다 — 패널이 음량을 다시 읽고 show_volume 으로 알려 준다.
    before_open: 열기 전에 — 시작 메뉴·알림 센터를 닫는다 (패널이 넣는다)"""

    def __init__(self, run, notifications=None, on_volume=None):
        super().__init__(GtkLayerShell.Edge.RIGHT, margin=10)
        self.get_style_context().add_class("quick-settings")
        self.run = run
        self.noti = notifications
        self.on_volume = on_volume or (lambda: None)
        self.before_open = None
        self._buttons = []
        self._other_catchers = []
        self._tick_src = 0
        self._n = 0
        self._bt_cid = None
        self._devs = None                 # nmcli device (열 때 읽는다)
        self._dev_busy = False
        self._dev_src = 0
        self._volume = None               # (0~1, 음소거) — 패널이 5초마다 읽어 준다
        self._vol_touch = 0.0             # 사용자가 슬라이더를 만진 때 — 잠시 동안은 읽은 값으로 덮지 않는다
        self._bri_touch = 0.0
        self._bri_busy = False
        self._guard = False
        self._open_row = None             # 펼친 목록 줄 (키, 펼침)
        self._wifi_busy = False
        self._wifi_scan_busy = False
        self._dark_busy = False
        self._air = bool(config.state("airplane", False))
        self._battery = read_battery()
        self._backlight = has_backlight()
        self._has_wpctl = shutil.which("wpctl") is not None
        self._can_dark = self._settings_available()

        self.bt = self._load_bt()
        self.rf = Rfkill(self._net_changed)
        self.net = NetState(self._net_changed)
        self.night = NightLight(self._refresh)
        self._vol_set = LastValue(run, lambda p: ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{p}%"],
                                  after=lambda: self.on_volume())
        self._bri_set = LastValue(run, lambda p: ["brightnessctl", "-q", "-c", "backlight", "set", f"{p}%"])

        self._build()
        if self._battery is not None:
            GLib.timeout_add_seconds(30, self._battery_tick)

    # ── 준비 ─────────────────────────────────────────────
    @staticmethod
    def _load_bt():
        """블루투스 모듈 (sekaishell/bluetooth.py) — 없거나 고장이면 타일을 숨긴다"""
        try:
            from . import bluetooth
            return bluetooth.get()
        except Exception as e:
            dbg("블루투스 모듈 없음", e)
            return None

    @staticmethod
    def _settings_available():
        try:
            import importlib.util
            return importlib.util.find_spec("sekaisettings") is not None
        except (ImportError, ValueError):
            return False

    def _build(self):
        self.root.set_spacing(0)
        self.root.set_size_request(WIDTH, -1)
        self.stack = Gtk.Stack()
        self.stack.set_hhomogeneous(True)
        self.stack.set_vhomogeneous(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(160)
        # 넘어가는 동안엔 두 쪽 중 큰 크기 — 끝나면 창을 보이는 쪽 크기로 줄인다
        self.stack.connect("notify::transition-running",
                           lambda s, _p: None if s.get_transition_running() else self._fit())
        self.root.pack_start(self.stack, False, False, 0)

        self.stack.add_named(self._build_main(), "main")
        self._details = {
            "wifi": _Detail(self, "Wi-Fi", "네트워크 설정", "network", on_switch=self._set_wifi),
            "bt": _Detail(self, "블루투스", "블루투스 설정", "bluetooth", on_switch=self._set_bt),
            "audio": _Detail(self, "소리 출력", "소리 설정", "sound"),
        }
        for name, d in self._details.items():
            self.stack.add_named(d.box, name)
        self.root.pack_start(self._build_foot(), False, False, 0)
        self._refresh()

    def _build_main(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.get_style_context().add_class("qs-body")

        self.t_wifi = Tile(["network-wireless-symbolic"], "Wi-Fi", self._toggle_wifi,
                           lambda: self.show_detail("wifi"), "Wi-Fi 네트워크")
        self.t_wired = Tile(["network-wired-symbolic"], "이더넷", lambda: self.open_settings("network"))
        self.t_bt = Tile(["bluetooth-active-symbolic", "bluetooth-symbolic"], "블루투스", self._toggle_bt,
                         lambda: self.show_detail("bt"), "블루투스 장치")
        self.t_air = Tile(["airplane-mode-symbolic"], "비행기 모드", self._toggle_airplane)
        self.t_dnd = Tile(["notifications-disabled-symbolic"], "방해 금지", self._toggle_dnd)
        self.t_night = Tile(["night-light-symbolic", "weather-clear-night-symbolic"], "야간 모드",
                            lambda: self.night.set(not self.night.on))
        self.t_dark = Tile(["weather-clear-night-symbolic", "preferences-desktop-appearance-symbolic"],
                           "다크 모드", self._toggle_dark)
        self._tiles = [self.t_wifi, self.t_wired, self.t_bt, self.t_air, self.t_dnd, self.t_night, self.t_dark]
        self._tile_layout = None
        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(8)
        self.grid.set_row_spacing(12)
        self.grid.set_column_homogeneous(True)
        page.pack_start(self.grid, False, False, 0)

        sliders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        # 밝기 — 백라이트가 있는 화면(노트북)에서만. 0% 는 화면이 꺼지는 패널이 있어 1% 부터
        bimg = Gtk.Image()
        set_icon(bimg, ["display-brightness-symbolic"])
        bbox = Gtk.Box()
        bbox.set_size_request(34, -1)
        bbox.set_center_widget(bimg)
        self.bri_row, self.bri_scale, self.bri_value = self._slider(bbox, 1, 100, self._on_bri)
        self.bri_row.set_tooltip_text("밝기")
        sliders.pack_start(self.bri_row, False, False, 0)

        self.mute_btn = _icon_btn(["audio-volume-high-symbolic"], "음소거", self._toggle_mute)
        more = _flat_btn("qs-icon-btn", _chevron(), "출력 장치 고르기")
        more.set_valign(Gtk.Align.CENTER)
        more.connect("clicked", lambda *_: self.show_detail("audio"))
        self.vol_row, self.vol_scale, self.vol_value = self._slider(self.mute_btn, 0, 100, self._on_vol, more)
        sliders.pack_start(self.vol_row, False, False, 0)
        page.pack_start(sliders, False, False, 0)

        page.show_all()
        for w in (self.bri_row, self.vol_row):
            w.set_no_show_all(True)
        return page

    @staticmethod
    def _slider(lead, lo, hi, on_change, more=None):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("qs-slider")
        row.pack_start(lead, False, False, 0)
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        sc.set_draw_value(False)
        sc.set_hexpand(True)
        sc.connect("value-changed", on_change)
        row.pack_start(sc, True, True, 0)
        val = Gtk.Label(xalign=1)
        val.get_style_context().add_class("qs-value")
        val.set_width_chars(4)
        row.pack_start(val, False, False, 0)
        if more is not None:
            row.pack_start(more, False, False, 0)
        return row, sc, val

    def _build_foot(self):
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.get_style_context().add_class("qs-foot")
        self.bat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.bat_box.get_style_context().add_class("qs-battery")
        self.bat_img = Gtk.Image()
        self.bat_pct = Gtk.Label(xalign=0)
        self.bat_pct.get_style_context().add_class("qs-battery-pct")
        self.bat_state = Gtk.Label(xalign=0)
        self.bat_state.get_style_context().add_class("qs-battery-state")
        self.bat_state.set_ellipsize(Pango.EllipsizeMode.END)
        for w in (self.bat_img, self.bat_pct, self.bat_state):
            self.bat_box.pack_start(w, False, False, 0)
        foot.pack_start(self.bat_box, True, True, 0)
        foot.pack_end(_icon_btn(["emblem-system-symbolic", "preferences-system-symbolic"], "모든 설정",
                                lambda: self.open_settings(None)), False, False, 0)
        foot.show_all()
        self.bat_box.set_no_show_all(True)
        return foot

    # ── 작업 표시줄 버튼 ────────────────────────────────
    def make_button(self, on_scroll=None, monitor=None):
        """작업 표시줄에 넣을 아이콘 묶음 — 여러 작업 표시줄이 하나씩 가질 수 있다"""
        b = StatusButton(self, on_scroll, monitor)
        self._buttons.append(b)
        b.connect("destroy", lambda w: self._buttons.remove(w) if w in self._buttons else None)
        b.show_net(self._net_info())
        b.show_volume(self._volume)
        b.show_battery(self._battery)
        if self.get_visible():
            b.get_style_context().add_class("open")
        return b

    def _net_info(self):
        return net_icon(self.net, self._airplane(), self._has_wifi())

    def show_volume(self, r):
        """패널이 읽은 음량 (update_sys) — 아이콘들, 열려 있으면 슬라이더"""
        if r is not None:
            self._volume = r
        for b in self._buttons:
            b.show_volume(r)
        if self.get_visible():
            self._apply_volume()

    def refresh_icons(self):
        """아이콘 테마가 바뀌었다 (다크/라이트) — 이름을 다시 고른다"""
        self._net_changed()
        for b in self._buttons:
            b.show_volume(self._volume)
            b.show_battery(self._battery)
        self._refresh()

    def sync_dnd(self):
        """방해 금지가 바뀌었다 (알림 센터·설정 앱) — 타일을 맞춘다"""
        if self.noti is not None:
            self.t_dnd.set_on(self.noti.dnd)

    def _battery_tick(self):
        self._battery = read_battery()
        for b in self._buttons:
            b.show_battery(self._battery)
        return True

    # ── 열기·닫기 ────────────────────────────────────────
    def toggle(self, monitor=None):
        if self.get_visible():
            self.close()
        else:
            self.open(monitor)

    def open(self, monitor=None):
        if self.before_open is not None:
            self.before_open()
        if monitor is not None:
            GtkLayerShell.set_monitor(self, monitor)
            GtkLayerShell.set_monitor(self.catcher, monitor)
            # 다른 모니터를 눌러도 닫히게 (어둡게 하진 않는다) — 시작 메뉴와 같게
            disp = Gdk.Display.get_default()
            for i in range(disp.get_n_monitors()):
                m = disp.get_monitor(i)
                if m != monitor:
                    c = ClickCatcher(self.close, dim=False)
                    GtkLayerShell.set_monitor(c, m)
                    c.show_all()
                    self._other_catchers.append(c)
        self._collapse()
        self.stack.set_visible_child_full("main", Gtk.StackTransitionType.NONE)
        self._start_updates()
        super().open()
        for b in self._buttons:
            b.get_style_context().add_class("open")
        self._fit()

    def close(self):
        super().close()
        self._stop_updates()
        for c in self._other_catchers:
            c.destroy()
        self._other_catchers = []
        for b in self._buttons:
            b.get_style_context().remove_class("open")
        self._collapse()

    def open_settings(self, page):
        self.close()
        spawn(["sekai-settings"] + ([f"--page={page}"] if page else []))

    def _start_updates(self):
        """열려 있는 동안만 — 2초마다 가벼운 것(파일·메모리), 가끔 명령"""
        self._n = 0
        self._refresh()
        self._apply_volume()
        self.on_volume()                  # 음량을 새로 읽어 show_volume 으로
        self._query_brightness()
        self._query_devices()
        if not self._tick_src:
            self._tick_src = GLib.timeout_add_seconds(2, self._tick)
        if self.bt is not None and self._bt_cid is None:
            try:
                self._bt_cid = self.bt.on_change(lambda *_a: GLib.idle_add(self._bt_changed))
            except Exception as e:
                dbg("블루투스 on_change 실패", e)

    def _stop_updates(self):
        if self._tick_src:
            GLib.source_remove(self._tick_src)
            self._tick_src = 0
        if self._dev_src:
            GLib.source_remove(self._dev_src)
            self._dev_src = 0
        if self.bt is not None and self._bt_cid is not None:
            try:
                self.bt.off(self._bt_cid)
            except Exception as e:
                dbg("블루투스 off 실패", e)
            self._bt_cid = None

    def _tick(self):
        self._n += 1
        self.rf.poll()
        if self._battery is not None:
            self._battery_tick()
        # 비행기 모드를 켠 뒤 누가 무선 하나를 되살렸다 — 더는 비행기 모드가 아니다
        if self._air and not self.rf.all_blocked():
            self._air = False
            config.set_state("airplane", False)
            self._net_changed()
        self._refresh()
        if self._n % 2 == 0:
            self._query_brightness()
        if (self._n % 5 == 0 and self.stack.get_visible_child_name() == "wifi"
                and self._open_row is None):
            self._scan_wifi(rescan=False)
        return True

    # ── 상태 → 화면 ─────────────────────────────────────
    def _has_wifi(self):
        if self._devs is not None:
            return any(d["type"] == "wifi" for d in self._devs)
        return sys_has_wifi()

    def _wifi_dev(self):
        return next((d for d in (self._devs or []) if d["type"] == "wifi"), None)

    def _wifi_on(self):
        return self.net.wifi_enabled and not self.rf.blocked(RF_WLAN)

    def _airplane(self):
        return self._air and self.rf.all_blocked()

    def _bt_state(self):
        """블루투스 → {available, powered, blocked, connected: [이름]} 또는 None (모듈 고장이면 숨긴다)"""
        if self.bt is None:
            return None
        try:
            conn = [d.get("name") or d.get("address") or "" for d in (self.bt.devices() or [])
                    if d.get("connected")]
            return {"available": bool(self.bt.available), "powered": bool(self.bt.powered),
                    "blocked": bool(self.bt.blocked), "connected": conn}
        except Exception as e:
            dbg("블루투스 상태 읽기 실패", e)
            return None

    def _net_changed(self):
        info = self._net_info()
        for b in self._buttons:
            b.show_net(info)
        if self.get_visible():
            self._refresh()
            # 연결이 바뀌었다 — 장치 목록(타일 이름)을 곧 다시 읽는다 (신호가 여럿 몰려오므로 모아서)
            if not self._dev_src:
                self._dev_src = GLib.timeout_add(700, self._dev_due)

    def _dev_due(self):
        self._dev_src = 0
        self._query_devices()
        if self.stack.get_visible_child_name() == "wifi" and self._open_row is None:
            self._scan_wifi(rescan=False)
        return False

    def _refresh(self):
        """타일·밝기·배터리를 지금 상태로 (조회 없이 — 메모리·파일만)"""
        nm_ok = self.net.running
        has_wifi = self._has_wifi()
        # Wi-Fi 또는 (무선 장치가 없으면) 유선 상태
        self.t_wifi.shown = nm_ok and has_wifi
        self.t_wired.shown = nm_ok and not has_wifi
        wifi_on = self._wifi_on()
        self.t_wifi.set_on(wifi_on)
        wd = self._wifi_dev()
        ssid = wd["conn"] if wd and wd["state"].startswith("connected") and wd["conn"] else ""
        if not ssid and self.net.kind == "wifi" and self.net.state >= NM_CONNECTED_LOCAL:
            ssid = self.net.name
        if not wifi_on:
            self.t_wifi.set_icon(["network-wireless-disabled-symbolic", "network-wireless-offline-symbolic"])
        elif self.net.kind == "wifi" and self.net.strength is not None:
            self.t_wifi.set_icon([f"network-wireless-signal-{signal_level(self.net.strength)}-symbolic",
                                  "network-wireless-symbolic"])
        else:
            self.t_wifi.set_icon(["network-wireless-symbolic"])
        self.t_wifi.set_label(ssid or "Wi-Fi", f"Wi-Fi — {ssid} 연결됨" if ssid else
                              ("Wi-Fi 켜짐" if wifi_on else "Wi-Fi 꺼짐"))
        wired = [d for d in (self._devs or []) if d["type"] == "ethernet"]
        wconn = next((d for d in wired if d["state"].startswith("connected")), None)
        wired_up = wconn is not None or (self._devs is None and self.net.kind == "wired"
                                         and self.net.state >= NM_CONNECTED_LOCAL)
        self.t_wired.set_on(wired_up)
        self.t_wired.set_icon(["network-wired-symbolic"] if wired_up else
                              ["network-wired-disconnected-symbolic", "network-wired-symbolic"])
        self.t_wired.set_label("이더넷" if wired_up else "연결 안 됨",
                               (f"이더넷 — {wconn['conn']}" if wconn else "이더넷") + "\n누르면 네트워크 설정"
                               if wired_up else "케이블이 연결되지 않았습니다\n누르면 네트워크 설정")

        st = self._bt_state()
        self.t_bt.shown = bool(st and st["available"])
        if st:
            bt_on = st["powered"] and not st["blocked"]
            self.t_bt.set_on(bt_on)
            self.t_bt.set_icon(["bluetooth-active-symbolic", "bluetooth-symbolic"] if bt_on else
                               ["bluetooth-disabled-symbolic", "bluetooth-symbolic"])
            names = st["connected"]
            label = names[0] if len(names) == 1 else (f"{len(names)}개 연결됨" if names else "블루투스")
            self.t_bt.set_label(label, "블루투스 — " + (", ".join(names) + " 연결됨" if names else
                                                       ("켜짐" if bt_on else "꺼짐")))
            self._details["bt"].set_switch(bt_on)

        self.t_air.shown = self.rf.available
        self.t_air.set_on(self._airplane())
        self.t_air.set_label("비행기 모드")
        self.t_dnd.shown = self.noti is not None
        if self.noti is not None:
            self.t_dnd.set_on(self.noti.dnd)
            self.t_dnd.set_label("방해 금지", "방해 금지 — 알림을 띄우지 않고 알림 센터에만 모읍니다")
        self.t_night.shown = self.night.available
        self.t_night.set_on(self.night.on)
        self.t_night.set_sensitive(not self.night.failed)
        self.t_night.set_label("야간 모드", "이 화면에서는 야간 모드를 쓸 수 없습니다" if self.night.failed
                               else "야간 모드 — 화면을 따뜻한 색으로")
        self.t_dark.shown = self._can_dark
        if self._can_dark and not self._dark_busy:
            self.t_dark.set_on(theme.mode_of(config.settings("appearance")) == "dark")
        self.t_dark.set_label("다크 모드")
        self._details["wifi"].set_switch(wifi_on)
        self._layout_tiles()

        self.bri_row.set_visible(self._backlight)
        self.vol_row.set_visible(self._has_wpctl)
        b = self._battery
        self.bat_box.set_visible(b is not None)
        if b is not None:
            icons, text = battery_text(b)
            set_icon(self.bat_img, icons)
            self.bat_pct.set_text(f"{b['pct']}%")
            self.bat_state.set_text(text)
            self.bat_box.set_tooltip_text(f"배터리 {b['pct']}% — {text}")

    def _layout_tiles(self):
        """보이는 타일만 3열로 (숨긴 타일이 빈칸으로 남지 않게) — 바뀌었을 때만 다시 놓는다"""
        shown = [t for t in self._tiles if t.shown]
        if shown == self._tile_layout:
            return
        self._tile_layout = shown
        for c in self.grid.get_children():
            self.grid.remove(c)
        for i, t in enumerate(shown):
            self.grid.attach(t, i % 3, i // 3, 1, 1)
        self.grid.show_all()
        if self.get_visible():
            self._fit()

    def _fit(self):
        """창을 내용에 맞춘다 — 레이어 창에서는 스크롤 칸의 '내용 높이 따라가기'가 첫 배치 때 0 으로 잡혀
        (알림 센터와 같은 문제) 목록 높이를 직접 재서 준다"""
        d = self._details.get(self.stack.get_visible_child_name())
        if d is not None:
            _m, nat = d.list.get_preferred_height()
            d.scroll.set_min_content_height(max(0, min(nat, LIST_MAX)))
        self.resize(1, 1)

    # ── 세부 보기 ────────────────────────────────────────
    def show_main(self):
        self._collapse()
        self.stack.set_visible_child_name("main")

    def show_detail(self, name):
        self._collapse()
        d = self._details[name]
        self.stack.set_visible_child_name(name)
        if name == "wifi":
            if not d.list.get_children():
                d.set_note("네트워크를 찾는 중…" if self._wifi_on() else "")
            self._scan_wifi(rescan=False)
            self._scan_wifi(rescan=True)          # 끝나면 한 번 더 그린다
        elif name == "bt":
            self._fill_bt()
        elif name == "audio":
            if not d.list.get_children():
                d.set_note("출력 장치를 읽는 중…")
            self.run(["wpctl", "status"], self._got_sinks, secs=4, capture=True)
        self._fit()

    def _collapse(self):
        if self._open_row is not None:
            rev = self._open_row[1]
            rev.set_reveal_child(False)
            if rev.get_parent() is not None:
                rev.get_parent().get_style_context().remove_class("expanded")
            self._open_row = None

    def _row(self, d, key, icons, name, sub, current=False, trailing=None, on_click=None, trail_cls="qs-row-trail"):
        """목록 한 줄. on_click 이 없으면 누를 때 아래로 펼쳐지는 동작 영역(돌려주는 상자)을 갖는다.
        trailing: 오른쪽 작은 아이콘 (잠금 = qs-row-trail 흐리게, 고른 것 = qs-row-check 강조색)"""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("qs-row")
        if current:
            outer.get_style_context().add_class("current")
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        img = Gtk.Image()
        set_icon(img, icons, 18)
        h.pack_start(img, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        n = Gtk.Label(label=name, xalign=0)
        n.get_style_context().add_class("qs-row-name")
        n.set_ellipsize(Pango.EllipsizeMode.END)
        texts.pack_start(n, False, False, 0)
        if sub:
            s = Gtk.Label(label=sub, xalign=0)
            s.get_style_context().add_class("qs-row-sub")
            s.set_ellipsize(Pango.EllipsizeMode.END)
            texts.pack_start(s, False, False, 0)
        h.pack_start(texts, True, True, 0)
        if trailing:
            t = Gtk.Image()
            set_icon(t, trailing, 14)
            t.get_style_context().add_class(trail_cls)
            h.pack_end(t, False, False, 0)
        btn = _flat_btn("qs-row-btn", h)
        outer.pack_start(btn, False, False, 0)
        act = None
        if on_click is not None:
            btn.connect("clicked", lambda *_: on_click())
        else:
            rev = Gtk.Revealer()
            rev.set_transition_type(Gtk.RevealerTransitionType.NONE)
            act = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            act.get_style_context().add_class("qs-row-actions")
            rev.add(act)
            outer.pack_start(rev, False, False, 0)
            btn.connect("clicked", lambda *_: self._toggle_row(key, rev))
        d.list.pack_start(outer, False, False, 0)
        return act

    def _toggle_row(self, key, rev):
        was = self._open_row is not None and self._open_row[0] == key
        self._collapse()
        if not was:
            rev.set_reveal_child(True)
            rev.get_parent().get_style_context().add_class("expanded")
            self._open_row = (key, rev)
        self._fit()

    # ── Wi-Fi ────────────────────────────────────────────
    def _toggle_wifi(self):
        self._set_wifi(not self._wifi_on())

    def _set_wifi(self, on):
        self._collapse()                          # 펼친 줄이 있으면 목록을 다시 그리지 않으므로
        self.t_wifi.set_on(on)                    # 곧바로 보여 주고, 결과는 NetworkManager 신호로
        if on:
            if self.rf.blocked(RF_WLAN):
                self.rf.set_block(RF_WLAN, False)
            self._leave_airplane_flag()
        self.run(["nmcli", "radio", "wifi", "on" if on else "off"], lambda _o: self._after_wifi_toggle(on), secs=10)

    def _after_wifi_toggle(self, on):
        self._net_changed()
        if self.get_visible() and self.stack.get_visible_child_name() == "wifi":
            if on:
                self._details["wifi"].set_note("네트워크를 찾는 중…")
                GLib.timeout_add(1500, lambda: (self._scan_wifi(rescan=True), False)[1])
            else:
                self._fill_wifi([], on=False)          # NetworkManager 신호보다 먼저 올 수 있다

    def _scan_wifi(self, rescan):
        """nmcli 의 무선 목록 — rescan=False 는 받아 둔 목록(바로 끝남), True 는 새로 찾아서(몇 초).
        받아 둔 목록 읽기는 한 번에 하나만 (2초 틱·신호가 겹쳐도 쌓이지 않게)"""
        if not rescan:
            if self._wifi_scan_busy:
                return
            self._wifi_scan_busy = True

        def done(out):
            if not rescan:
                self._wifi_scan_busy = False
            if out is None or not self.get_visible() or self.stack.get_visible_child_name() != "wifi":
                return
            if self._open_row is not None or self._wifi_busy:
                return                            # 펼친 줄(암호 입력 중)을 지우지 않는다
            self._fill_wifi(parse_wifi(out))
        self.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                  "--rescan", "yes" if rescan else "no"], done, secs=20 if rescan else 6, capture=True)

    def _fill_wifi(self, nets, on=None):
        d = self._details["wifi"]
        d.clear()
        on = self._wifi_on() if on is None else on
        if not on:
            d.set_note("Wi-Fi 가 꺼져 있습니다.")
        elif not nets:
            d.set_note("찾은 네트워크가 없습니다.")
        else:
            d.set_note("")
        for net in (nets if on else [])[:30]:
            secured = bool(net["sec"])
            sub = ("연결됨" if net["active"] else "") + (", " if net["active"] else "") + \
                ("보안" if secured else "개방")
            icons = [f"network-wireless-signal-{signal_level(net['signal'])}-symbolic", "network-wireless-symbolic"]
            act = self._row(d, "wifi:" + net["ssid"], icons, net["ssid"], sub, current=net["active"],
                            trailing=["changes-prevent-symbolic", "network-wireless-encrypted-symbolic"]
                            if secured else None)
            self._wifi_actions(act, net)
        if on:
            self._row(d, "wifi-hidden", ["network-wireless-symbolic"], "숨겨진 네트워크",
                      "이름을 알리지 않는 네트워크에 연결", on_click=lambda: self._wifi_dialog(None))
        d.list.show_all()
        self._fit()

    def _wifi_actions(self, act, net):
        ui = {}

        def go():
            if self._wifi_busy:
                return
            if net["active"]:
                self._wifi_disconnect(net, ui["ui"])
                return
            pw = ui["ui"].password()
            if pw is not None and not pw:
                ui["ui"].set_status("네트워크 보안 키를 입력하세요", error=True)
                return
            self._wifi_connect(net, ui["ui"], pw)
        # 회사·학교 네트워크는 암호 칸 대신 로그인 창 (저장된 프로필이 있으면 그것으로 먼저 연결해 본다)
        ui["ui"] = _RowUI(act, "연결 끊기" if net["active"] else "연결", not net["active"], go,
                          password=needs_key(net["sec"]) and not is_enterprise(net["sec"]) and not net["active"])

    def _wifi_connect(self, net, ui, pw=None):
        self._wifi_busy = True
        ui.busy(True)
        ui.set_status(f"'{net['ssid']}' 에 연결하는 중…")
        wd = self._wifi_dev()
        dev = wd["dev"] if wd else None

        def work():
            try:
                err = wifi_connect(net["ssid"], net["sec"], pw, dev)
            except Exception as e:                # 스레드에서 새면 버튼이 영영 잠긴다
                err = str(e)
            GLib.idle_add(self._wifi_done, ui, err, pw is not None, net)
        threading.Thread(target=work, daemon=True).start()

    def _wifi_dialog(self, net):
        """회사·학교 네트워크 로그인(net) · 숨겨진 네트워크 연결(None) — 설정 앱의 Wi-Fi 연결 창을 이 패널에서
        띄운다. 팝업은 닫는다 (창이 키보드를 받아야 하고, 팝업 밖을 누르면 어차피 닫힌다)"""
        wd = self._wifi_dev()
        dev = wd["dev"] if wd else None
        self.close()
        try:
            from sekaisettings.pages.network import WifiDialog
        except Exception as e:                            # 설정 앱이 없는 설치본
            dbg("WifiDialog 를 가져오지 못함", e)
            self.open_settings("network")
            return
        WifiDialog(None, net["ssid"] if net else None, dev, security="eap" if net else None)

    def _wifi_done(self, ui, err, had_pw, net=None):
        self._wifi_busy = False
        ui.busy(False)
        if err == NEED_PW and net is not None and is_enterprise(net["sec"]):
            self._wifi_dialog(net)
        elif err == NEED_PW:
            ui.set_status("네트워크 보안 키를 입력하세요")
            ui.ask_password()
        elif err:
            ui.set_status(f"연결하지 못했습니다: {err}", error=True)
            if had_pw:
                ui.ask_password()
        else:
            self._collapse()
            self._scan_wifi(rescan=False)
        self._fit()
        return False

    def _wifi_disconnect(self, net, ui):
        wd = self._wifi_dev()
        argv = (["nmcli", "device", "disconnect", wd["dev"]] if wd else
                ["nmcli", "connection", "down", "id", net["ssid"]])
        self._wifi_busy = True
        ui.busy(True)
        ui.set_status("연결을 끊는 중…")

        def done(out):
            self._wifi_busy = False
            ui.busy(False)
            if out is None:
                ui.set_status("연결을 끊지 못했습니다", error=True)
            else:
                self._collapse()
                self._scan_wifi(rescan=False)
        self.run(argv, done, secs=15)

    def _query_devices(self):
        if self._dev_busy:
            return
        self._dev_busy = True

        def done(out):
            self._dev_busy = False
            if out is not None:
                self._devs = parse_devices(out)
            if self.get_visible():
                self._refresh()
        self.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], done, secs=5, capture=True)

    # ── 비행기 모드 ──────────────────────────────────────
    def _toggle_airplane(self):
        on = not self._airplane()
        st = self._bt_state()
        if on:
            # 끄기 전 상태를 적어 두었다가 풀 때 되돌린다 (윈도우처럼)
            config.set_state("airplane_prev", {"bt": bool(st and st["powered"])})
            self._air = True
            config.set_state("airplane", True)
            self.rf.set_block(RF_ALL, True)
        else:
            prev = config.state("airplane_prev") or {}
            self._leave_airplane_flag()
            self.rf.set_block(RF_ALL, False)
            # Wi-Fi 는 NetworkManager 가 제 설정(켜 둔 채였으면 켜짐)대로 돌아온다.
            #   블루투스는 차단이 풀려도 켜질지 말지가 bluetoothd 설정마다 달라 적어 둔 대로 맞춘다
            if st is not None and isinstance(prev, dict):
                GLib.timeout_add(1000, lambda: (self._bt_power(bool(prev.get("bt"))), False)[1])
        self.t_air.set_on(on)
        self._net_changed()

    def _leave_airplane_flag(self):
        if self._air:
            self._air = False
            config.set_state("airplane", False)

    # ── 블루투스 ─────────────────────────────────────────
    def _toggle_bt(self):
        st = self._bt_state()
        if st is not None:
            self._set_bt(not (st["powered"] and not st["blocked"]))

    def _set_bt(self, on):
        st = self._bt_state()
        if st is None:
            return
        self._collapse()
        self.t_bt.set_on(on)
        if on:
            self._leave_airplane_flag()
        self._bt_power(on)                            # rfkill 막힘은 블루투스 모듈이 풀고 켠다

    def _bt_power(self, on):
        try:
            self.bt.set_powered(on, lambda *_a: GLib.idle_add(self._bt_changed))
        except Exception as e:
            dbg("블루투스 켜기/끄기 실패", e)

    def _bt_changed(self):
        if self.get_visible():
            self._refresh()
            if self.stack.get_visible_child_name() == "bt" and self._open_row is None:
                self._fill_bt()
        return False

    def _fill_bt(self):
        d = self._details["bt"]
        d.clear()
        st = self._bt_state()
        devs = []
        if st is not None and st["powered"] and not st["blocked"]:
            try:
                devs = [x for x in (self.bt.devices() or []) if x.get("paired")]
            except Exception as e:
                dbg("블루투스 장치 목록 실패", e)
            if not devs:
                d.set_note("짝을 맺은 장치가 없습니다.\n블루투스 설정에서 장치를 추가하세요.")
            else:
                d.set_note("")
        else:
            d.set_note("블루투스가 꺼져 있습니다.")
        devs.sort(key=lambda x: (not x.get("connected"), (x.get("name") or "").lower()))
        for dev in devs:
            sub = "연결됨" if dev.get("connected") else "짝을 맺음"
            if dev.get("battery") is not None:
                sub += f" · 배터리 {dev['battery']}%"
            icon = dev.get("icon") or ""
            icons = ([icon + "-symbolic"] if icon else []) + ["bluetooth-active-symbolic", "bluetooth-symbolic"]
            act = self._row(d, "bt:" + str(dev.get("path")), icons, dev.get("name") or dev.get("address") or "?",
                            sub, current=bool(dev.get("connected")))
            self._bt_actions(act, dev)
        d.list.show_all()
        self._fit()

    def _bt_actions(self, act, dev):
        box = {}
        conn = bool(dev.get("connected"))

        def go():
            ui = box["ui"]
            ui.busy(True)
            ui.set_status("연결을 끊는 중…" if conn else "연결하는 중…")

            def done(ok=True, err=None, *_a):
                def apply():
                    ui.busy(False)
                    if ok:
                        self._collapse()
                        self._fill_bt()
                    else:
                        ui.set_status(("연결을 끊지 못했습니다" if conn else "연결하지 못했습니다")
                                      + (f": {err}" if err else ""), error=True)
                        self._fit()
                    return False
                GLib.idle_add(apply)
            try:
                (self.bt.disconnect if conn else self.bt.connect)(dev.get("path"), done)
            except Exception as e:
                done(False, str(e))
        box["ui"] = _RowUI(act, "연결 끊기" if conn else "연결", not conn, go)

    # ── 방해 금지 · 야간 모드 · 다크 모드 ────────────────
    def _toggle_dnd(self):
        if self.noti is not None:
            self.noti.set_dnd(not self.noti.dnd)      # 작업 표시줄의 알림 버튼·알림 센터가 따라온다
            self.sync_dnd()

    def _toggle_dark(self):
        """설정 앱의 다크/라이트와 같은 일 (Store.set_mode) — gsettings·hyprctl 을 부르므로 작업 스레드에서.
        끝나면 설정 앱이 패널에 SIGHUP 을 보내 CSS 를 다시 읽는다"""
        if self._dark_busy:
            return
        mode = "light" if theme.mode_of(config.settings("appearance")) == "dark" else "dark"
        self._dark_busy = True
        self.t_dark.set_on(mode == "dark")

        def work():
            try:
                from sekaisettings.store import Store
                Store().set_mode(mode)
            except Exception as e:
                dbg("다크 모드 바꾸기 실패", e)
            GLib.idle_add(done)

        def done():
            self._dark_busy = False
            self._refresh()
            return False
        threading.Thread(target=work, daemon=True).start()

    # ── 밝기 · 음량 ──────────────────────────────────────
    def _set_scale(self, sc, v):
        self._guard = True
        try:
            sc.set_value(v)
        finally:
            self._guard = False

    def _query_brightness(self):
        if not self._backlight or self._bri_busy:
            return
        self._bri_busy = True

        def done(out):
            self._bri_busy = False
            v = parse_brightness(out)
            if v is None or self._bri_set.busy or time.monotonic() - self._bri_touch < 1.5:
                return
            self._set_scale(self.bri_scale, max(1, v))
            self.bri_value.set_text(f"{v}%")
        self.run(["brightnessctl", "-c", "backlight", "-m", "info"], done, capture=True)

    def _on_bri(self, sc):
        if self._guard:
            return
        v = int(round(sc.get_value()))
        self._bri_touch = time.monotonic()
        self.bri_value.set_text(f"{v}%")
        self._bri_set.send(max(1, v))

    def _apply_volume(self):
        r = self._volume
        if r is None:
            return
        v, muted = r
        pct = int(round(v * 100))
        set_icon(self.mute_btn.img, [vol_icon(pct, muted)])
        self.mute_btn.set_tooltip_text("음소거 해제" if muted else "음소거")
        ctx = self.vol_row.get_style_context()
        (ctx.add_class if muted else ctx.remove_class)("muted")
        if self._vol_set.busy or time.monotonic() - self._vol_touch < 1.5:
            return                                    # 끄는 중 — 읽어 온 옛 값으로 되돌리지 않는다
        self._set_scale(self.vol_scale, min(100, pct))
        self.vol_value.set_text(f"{pct}%")

    def _on_vol(self, sc):
        if self._guard:
            return
        pct = int(round(sc.get_value()))
        self._vol_touch = time.monotonic()
        self.vol_value.set_text(f"{pct}%")
        if self._volume is not None and self._volume[1]:
            # 음소거 중에 슬라이더를 움직이면 소리를 켠다 (윈도우처럼)
            self._volume = (self._volume[0], False)
            self.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], lambda _o: None)
        self._vol_set.send(pct)

    def _toggle_mute(self):
        self.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], lambda _o: self.on_volume())

    def _got_sinks(self, out):
        if not self.get_visible() or self.stack.get_visible_child_name() != "audio":
            return
        d = self._details["audio"]
        d.clear()
        sinks = parse_sinks(out)
        if out is None:
            d.set_note("소리 서버(PipeWire)에 연결하지 못했습니다.")
        elif not sinks:
            d.set_note("출력 장치를 찾지 못했습니다.")
        else:
            d.set_note("")
        for s in sinks:
            low = s["name"].lower()
            if "hdmi" in low or "displayport" in low:
                icons = ["video-display-symbolic", "audio-speakers-symbolic"]
            elif "headphone" in low or "headset" in low or "헤드" in low:
                icons = ["audio-headphones-symbolic", "audio-speakers-symbolic"]
            else:
                icons = ["audio-speakers-symbolic", "audio-card-symbolic"]
            self._row(d, f"sink:{s['id']}", icons, s["name"], "", current=s["default"],
                      trailing=["object-select-symbolic"] if s["default"] else None,
                      on_click=lambda s=s: self._set_sink(s), trail_cls="qs-row-check")
        d.list.show_all()
        self._fit()

    def _set_sink(self, s):
        if s["default"]:
            return

        def done(_out):
            self.on_volume()                          # 새 출력 장치의 음량
            if self.get_visible() and self.stack.get_visible_child_name() == "audio":
                self.run(["wpctl", "status"], self._got_sinks, secs=4, capture=True)
        self.run(["wpctl", "set-default", str(s["id"])], done, secs=4)
