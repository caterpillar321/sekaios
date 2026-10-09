"""전원 — 배터리 · 전원 모드(power-profiles-daemon) · 배터리 감시(절약 모드 저절로, 부족 알림) · 덮개와 전원 단추.

배터리는 /sys/class/power_supply 를 직접 읽는다 (read_battery — 빠른 설정·설정 › 정보도 이것을 쓴다). 시험(MafuyuMom)은 배터리가 없는 VM 에서
$XDG_RUNTIME_DIR/sekai-test/power_supply 에 가짜 배터리를 만든다 — 그 사용자만 쓸 수 있는 곳이라 남이 속일 수 없다.
전원 모드는 powerprofilesctl (polkit 이 로그인한 사람에게 허용 — 인증을 묻지 않는다).
"""
import glob
import json
import os
import shutil
import subprocess
import time

from gi.repository import Gio, GLib

from . import dbg, sysfs

PROFILES = [("power-saver", "최고 전원 효율"), ("balanced", "균형"), ("performance", "최고 성능")]
STATE = os.path.expanduser("~/.local/state/sekai/battery-saver.json")


def supply_dir():
    test = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), "sekai-test", "power_supply")
    return test if os.path.isdir(test) else "/sys/class/power_supply"


def battery():
    return read_battery(supply_dir())


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
        typ = sysfs.read(d, "type")
        if typ == "Mains":
            ac = ac or sysfs.read(d, "online") == "1"
            continue
        if typ != "Battery" or sysfs.read(d, "scope") == "Device" or sysfs.read(d, "present") == "0":
            continue
        found = True
        states.append(sysfs.read(d, "status"))
        c = sysfs.read(d, "capacity")
        if c.isdigit():
            caps.append(int(c))
        # 에너지(µWh·µW)가 있으면 그것을, 없으면 전하(µAh·µA) — 짝을 맞춰야 시간이 맞다
        if sysfs.read(d, "energy_full"):
            now, full, rate = now + sysfs.num(d, "energy_now"), full + sysfs.num(d, "energy_full"), rate + abs(sysfs.num(d, "power_now"))
        elif sysfs.read(d, "charge_full"):
            now, full, rate = now + sysfs.num(d, "charge_now"), full + sysfs.num(d, "charge_full"), rate + abs(sysfs.num(d, "current_now"))
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


def _ctl(*args, timeout=5):
    if not shutil.which("powerprofilesctl"):
        return None
    try:
        r = subprocess.run(["powerprofilesctl", *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception as e:
        dbg("[전원] powerprofilesctl", args, e)
        return None


def profiles():
    """이 PC 가 고를 수 있는 전원 모드 [(id, 이름)] — performance 는 CPU 가 지원할 때만 나온다"""
    out = _ctl("list")
    if out is None:
        return []
    have = set()
    for line in out.splitlines():                          # "* balanced:" · "  power-saver:" (아래 줄은 들여 쓴 속성)
        t = line.strip()
        if t.endswith(":"):
            have.add(t.lstrip("*").strip().rstrip(":"))
    return [(k, v) for k, v in PROFILES if k in have]


def profile():
    return _ctl("get")


def set_profile(p):
    if p not in dict(PROFILES):
        return False
    return _ctl("set", p) is not None


def _notify(summary, body, icon="battery-caution-symbolic"):
    """알림 데몬은 작업 표시줄 자신이다 — 동기로 부르면 제 답을 기다리며 멈추므로 보내기만 한다"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("전원", 0, icon, summary, body, [], {}, -1)),
                      None, Gio.DBusCallFlags.NONE, 2000, None, None)
    except Exception as e:
        dbg("[전원] 알림 실패", e)


class BatteryGuard:
    """작업 표시줄이 띄운다. 30초마다(배터리가 있을 때만):
      · 배터리를 쓰는 중 잔량이 설정(power.saver_at)보다 낮으면 절약 모드(power-saver)로 — 원래 모드는 적어 둔다
      · 충전기를 꽂으면 저절로 켠 절약 모드를 원래 모드로 되돌린다 (사람이 직접 고른 모드는 건드리지 않는다)
      · 잔량 10% 에 한 번, 5% 에 한 번 알린다 (충전하면 다시)"""

    def __init__(self, conf):
        self.conf = conf                    # (섹션, 키, 기본값) → 값
        self.warned = set()
        self.st = self._load()
        GLib.timeout_add_seconds(30, self.tick)
        GLib.timeout_add_seconds(5, lambda: (self.tick(), False)[1])

    def _load(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump(self.st, f)
        except OSError:
            pass

    def tick(self):
        b = battery()
        if not b:
            return True
        pct, on_battery = b["pct"], b["state"] == "discharging"
        try:
            at = int(self.conf("power", "saver_at", 20) or 0)
        except (TypeError, ValueError):
            at = 20
        if on_battery:
            if at and pct <= at and not self.st.get("auto") and profile() not in (None, "power-saver"):
                self.st = {"auto": True, "prev": profile()}
                self._save()
                set_profile("power-saver")
                _notify("배터리 절약 모드를 켰습니다", f"잔량이 {pct}% 입니다. 성능을 조금 낮춰 배터리를 아낍니다. 충전기를 꽂으면 되돌립니다.",
                        "battery-low-symbolic")
            for lvl in (10, 5):
                if pct <= lvl and lvl not in self.warned:
                    self.warned.add(lvl)
                    _notify("배터리가 부족합니다" if lvl == 10 else "배터리가 곧 다 됩니다",
                            f"잔량 {pct}% — 충전기를 연결하세요." + ("" if lvl == 10 else " 열린 문서를 저장해 두세요."))
        else:
            self.warned.clear()
            if self.st.get("auto"):
                prev = self.st.get("prev") or "balanced"
                if profile() == "power-saver":
                    set_profile(prev)
                self.st = {}
                self._save()
        return True


# ───────────────────────────────────────────────────────────────
# 덮개 · 전원 단추 — 윈도우의 "전원 단추와 덮개" (배터리 사용 시 / 전원 연결 시 따로)
#   logind 설정(HandleLidSwitch…)은 시스템 전체이고 전원 단추는 배터리·전원 연결을 나누지 못한다. 그래서 GNOME 처럼
#   작업 표시줄이 logind 에 억제(inhibitor, handle-power-key · handle-lid-switch)를 걸고 스스로 처리한다.
#   작업 표시줄이 죽거나 로그인 화면이면 억제가 풀려 logind 기본(절전·끄기)으로 돌아간다 — 덮개를 닫았는데
#   아무 일도 없는 일은 없다.
#   전원 단추 = 합성기가 받는 XF86PowerOff 키 (sekai-ctl power button), 덮개 = logind 의 LidClosed 를 2초마다
#   (합성기의 switch:Lid Switch 바인딩이 오면 바로) — 덮개 장치 이름이 달라도 놓치지 않게.
# ───────────────────────────────────────────────────────────────
ACTIONS = {"nothing": "아무 것도 안 함", "sleep": "절전", "hibernate": "최대 절전 모드",
           "shutdown": "시스템 종료", "display": "디스플레이 끄기"}
LOGIN1 = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")


def lid_present():
    """덮개 스위치(SW_LID)가 있는 입력 장치가 있나 — capabilities/sw 비트맵의 0번 비트"""
    for f in glob.glob("/sys/class/input/input*/capabilities/sw"):
        try:
            with open(f) as fh:
                if int(fh.read().split()[-1], 16) & 1:
                    return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def _login1(method, args=None, sig=None):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        r = bus.call_sync(*LOGIN1, method, args, GLib.VariantType(sig) if sig else None,
                          Gio.DBusCallFlags.NONE, 3000, None)
        return r.unpack()[0] if r is not None and r.n_children() else None
    except Exception as e:
        dbg("[전원] logind", method, e)
        return None


def _login1_prop(name):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        r = bus.call_sync(LOGIN1[0], LOGIN1[1], "org.freedesktop.DBus.Properties", "Get",
                          GLib.Variant("(ss)", (LOGIN1[2], name)), GLib.VariantType("(v)"),
                          Gio.DBusCallFlags.NONE, 3000, None)
        return r.unpack()[0]
    except Exception as e:
        dbg("[전원] logind 속성", name, e)
        return None


def can_hibernate():
    return _login1("CanHibernate", sig="(s)") == "yes"


def actions(kind):
    """고를 수 있는 동작 [(id, 이름)] — kind: "button" | "lid" (디스플레이 끄기는 전원 단추만)"""
    keys = ["nothing", "sleep"] + (["hibernate"] if can_hibernate() else []) + ["shutdown"]
    if kind == "button":
        keys.append("display")
    return [(k, ACTIONS[k]) for k in keys]


def default_action(kind, laptop):
    """윈도우 기본값 — 덮개는 절전, 전원 단추는 노트북이면 절전 · 데스크톱이면 시스템 종료"""
    return "sleep" if kind == "lid" or laptop else "shutdown"


def on_ac():
    b = battery()
    return not b or b["state"] != "discharging"


class PowerKeys:
    """작업 표시줄이 띄운다 — 억제를 잡고, 전원 단추·덮개를 설정(power.button_ac · button_battery · lid_ac ·
    lid_battery)대로. 덮개를 닫았을 때 외부 모니터가 있으면(logind 의 Docked) logind 처럼 아무것도 안 한다."""

    def __init__(self, conf):
        self.conf = conf
        self.fd = None
        self.resumed = 0.0
        self.display_off = False
        self.woke = 0.0
        self.has_lid = lid_present()
        self._inhibit()
        try:
            Gio.bus_get_sync(Gio.BusType.SYSTEM, None).signal_subscribe(
                LOGIN1[0], LOGIN1[2], "PrepareForSleep", LOGIN1[1], None, Gio.DBusSignalFlags.NONE,
                self._on_sleep)
        except Exception as e:
            dbg("[전원] PrepareForSleep 구독 실패", e)
        self.lid = bool(_login1_prop("LidClosed")) if self.has_lid else False
        self.ticks = 0
        GLib.timeout_add_seconds(2, self._tick)

    def _tick(self):
        self.ticks += 1
        if self.has_lid:
            self.lid_changed()
        elif self.ticks % 5 == 0 and lid_present():      # 늦게 잡힌 덮개 — 억제를 덮개까지 넓혀 다시 잡는다
            self.has_lid = True
            self.lid = bool(_login1_prop("LidClosed"))
            self._inhibit()
        return True

    def _inhibit(self):
        what = "handle-power-key" + (":handle-lid-switch" if self.has_lid else "")
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            r, fds = bus.call_with_unix_fd_list_sync(
                *LOGIN1, "Inhibit",
                GLib.Variant("(ssss)", (what, "SekaiOS", "전원 단추와 덮개를 설정대로 처리합니다", "block")),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, 3000, None, None)
            old, self.fd = self.fd, fds.steal_fds()[r.unpack()[0]]   # 이 fd 를 쥐고 있는 동안 logind 가 손대지 않는다
            if old is not None:
                os.close(old)
        except Exception as e:
            dbg("[전원] 억제 실패 — logind 기본 동작대로", e)

    def _on_sleep(self, _conn, _sender, _path, _iface, _sig, params):
        if not params.unpack()[0]:                       # 깨어났다
            self.resumed = time.monotonic()
            self.lid = bool(_login1_prop("LidClosed")) if self.has_lid else False

    def action(self, kind):
        laptop = battery() is not None
        key = f"{kind}_{'ac' if on_ac() else 'battery'}"
        v = self.conf("power", key, "") or ""
        return v if v in ACTIONS else default_action(kind, laptop)

    def handle(self, what="button"):
        """sekai-ctl power button | lid"""
        if what == "lid":
            self.lid_changed()
            return
        if time.monotonic() - self.resumed < 3:          # 깨울 때 누른 단추가 다시 절전으로 보내지 않게
            dbg("[전원] 막 깨어남 — 전원 단추 무시")
            return
        self.do(self.action("button"))

    def lid_changed(self):
        if not self.has_lid:
            return
        closed = bool(_login1_prop("LidClosed"))
        if closed and not self.lid:
            self.lid = True
            if _login1_prop("Docked"):
                dbg("[전원] 덮개 닫힘 — 외부 모니터가 있어 그대로")
            else:
                self.do(self.action("lid"))
        elif not closed:
            self.lid = False

    def do(self, a):
        dbg("[전원] 동작", a)
        if a == "sleep":
            subprocess.Popen(["systemctl", "suspend"])
        elif a == "hibernate":
            subprocess.Popen(["systemctl", "hibernate"])
        elif a == "shutdown":
            subprocess.Popen(["systemctl", "poweroff"])
        elif a == "display":
            self.toggle_display()

    # 디스플레이 끄기 — 다시 누르면 켠다. 마우스·키 입력으로도 켜진다 (hyprland.conf 의 misc:*_enables_dpms).
    #   꺼진 화면에서 전원 단추를 누르면 합성기가 그 키로 먼저 켜고 나서 이리 온다 — 그 누름을 "끄기"로 읽지 않게
    #   꺼 둔 동안 1초마다 보고, 입력으로 켜진 때를 적어 둔다.
    @staticmethod
    def _screens_on():
        try:
            out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=3).stdout
            return all(m.get("dpmsStatus", True) for m in json.loads(out))
        except Exception:
            return True

    def toggle_display(self):
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):     # 기본 화면 모드(X11) — X 가 입력으로 알아서 켠다
            subprocess.Popen(["xset", "dpms", "force", "off"])
            return
        if self.display_off or time.monotonic() - self.woke < 1.0:
            self.display_off = False                     # 이 누름이 깨웠다(또는 아직 꺼져 있다) — 켜 두기만
            subprocess.Popen(["hyprctl", "dispatch", "dpms", "on"], stdout=subprocess.DEVNULL)
            return
        self.display_off = True
        subprocess.run(["hyprctl", "dispatch", "dpms", "off"], stdout=subprocess.DEVNULL, timeout=3)

        def watch():
            if not self.display_off:
                return False
            if self._screens_on():
                self.display_off, self.woke = False, time.monotonic()
                return False
            return True
        GLib.timeout_add_seconds(1, watch)
