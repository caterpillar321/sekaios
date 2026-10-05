"""노트북 — 배터리 · 전원 모드 · 배터리 절약 모드 저절로 켜기 · 배터리 부족 알림 · 전원 단추와 덮개.
   VM 엔 배터리가 없어 /run/user/<uid>/sekai-test/power_supply 에 가짜 배터리를 만든다 (sekaishell.power.supply_dir).
   작업 표시줄의 배터리 감시는 30초마다 보므로 바뀐 것을 기다리는 시간이 길다.
   전원 단추는 QEMU 의 ACPI 전원 단추(system_powerdown), 덮개는 uinput 가짜 덮개(mm/agent/vlid.py),
   절전은 QEMU 가 정말 잠들었다가(suspended) system_wakeup 으로 깬다."""
import json
import os
import time

from mm import config, remote, vm
from mm.config import PASSWORD
from mm.runner import test

PANEL = "sekai-panel"
SETTINGS = "sekai-settings"
FAKE = "/run/user/$(id -u)/sekai-test/power_supply"
STATE = "~/.local/state/sekai/battery-saver.json"


def fake_battery(t, pct, status):
    ac = "0" if status == "Discharging" else "1"
    t.sh(f"d={FAKE}; mkdir -p $d/BAT0 $d/AC && printf Battery > $d/BAT0/type && printf 1 > $d/BAT0/present && "
         f"printf {pct} > $d/BAT0/capacity && printf {status} > $d/BAT0/status && "
         f"printf Mains > $d/AC/type && printf {ac} > $d/AC/online")


def setting(t, section, key, value):
    """~/.config/sekai/settings.json 의 값 하나 (작업 표시줄은 볼 때마다 새로 읽는다)"""
    t.sh("python3 -c \"import json,os,sys;p=os.path.expanduser('~/.config/sekai/settings.json');"
         "d=json.load(open(p)) if os.path.exists(p) else {};"
         f"d.setdefault('{section}',{{}})['{key}']=json.loads(sys.argv[1]);json.dump(d,open(p,'w'),indent=1)\" "
         f"'{json.dumps(value)}'")


def profile(t):
    return t.sh("powerprofilesctl get").out.strip()


def battery_test(t):
    """가짜 배터리 · 전원 모드를 시험 전 모습으로 되돌리게 걸어 둔다"""
    t.expect(t.sh("command -v powerprofilesctl && systemctl is-active power-profiles-daemon").ok,
             "power-profiles-daemon 이 돈다")
    t.sh(f"rm -f {STATE}")
    set_profile(t, "balanced")
    t.after(lambda: (t.sh(f"rm -rf /run/user/$(id -u)/sekai-test/power_supply {STATE}; true"), set_profile(t, "balanced")))


def set_profile(t, p):
    """ssh 세션은 활성 세션이 아니라 polkit 이 막는다 — 시험의 "사람이 직접"은 root 로"""
    t.expect(t.root(f"powerprofilesctl set {p}").ok, f"powerprofilesctl set {p}")


@test("배터리가 기준 아래로 → 절약 모드 저절로 + 알림 → 부족 알림 → 충전기 꽂으면 원래 모드로", suite="laptop",
      timeout=240)
def battery_saver_auto(t):
    battery_test(t)
    setting(t, "power", "saver_at", 20)
    fake_battery(t, 15, "Discharging")
    t.expect(t.wait(lambda: profile(t) == "power-saver", 45, every=2), f"전원 모드 = power-saver ({profile(t)})")
    t.expect(t.ui.wait(app=PANEL, name="배터리 절약 모드를 켰습니다", timeout=10), "알림 『배터리 절약 모드를 켰습니다』")
    t.shot("절약 모드 알림")
    fake_battery(t, 4, "Discharging")
    t.expect(t.ui.wait(app=PANEL, name="배터리가 곧 다 됩니다", timeout=45), "알림 『배터리가 곧 다 됩니다』 (5%)")
    t.expect(t.ui.find(app=PANEL, name="배터리가 부족합니다"), "알림 『배터리가 부족합니다』 (10%)")
    fake_battery(t, 4, "Charging")
    t.expect(t.wait(lambda: profile(t) == "balanced", 45, every=2), f"충전기를 꽂자 balanced 로 돌아왔다 ({profile(t)})")
    t.expect(t.sh(f"test ! -s {STATE} || grep -qx '{{}}' {STATE}").ok, "저절로 켠 기록을 지웠다")


@test("사람이 고른 절약 모드는 충전기를 꽂아도 그대로 · 기준 '켜지 않음'이면 바꾸지 않는다", suite="laptop", timeout=200)
def battery_saver_respects_user(t):
    battery_test(t)
    setting(t, "power", "saver_at", 0)
    fake_battery(t, 10, "Discharging")
    time.sleep(40)                                       # 감시가 한 번 이상 돈다
    t.expect(profile(t) == "balanced", f"켜지 않음 — balanced 그대로 ({profile(t)})")
    set_profile(t, "power-saver")                        # 사람이 직접 — 기준을 올리기 전에 (사이에 감시가 돌 수 있다)
    setting(t, "power", "saver_at", 20)
    fake_battery(t, 10, "Charging")
    time.sleep(40)
    t.expect(profile(t) == "power-saver", f"직접 고른 절약 모드는 그대로 ({profile(t)})")


@test("설정 › 전원 및 잠금 — 배터리 줄, 전원 모드 콤보로 바꾸기, 절약 모드 기준 저장", suite="laptop", quick=True)
def power_page(t):
    battery_test(t)
    setting(t, "power", "saver_at", 20)
    fake_battery(t, 55, "Discharging")
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.after(lambda: t.kill("sekai-settings"))
    t.expect(t.launch("sekai-settings --page=power", SETTINGS, timeout=20), "설정 › 전원 및 잠금")
    t.expect(t.ui.wait(app=SETTINGS, role="label", name="배터리 55%", timeout=15), "[배터리 55%] 줄")

    def row_combo(title):
        lab = t.ui.wait(app=SETTINGS, role="label", name=title, timeout=10)
        if not lab:
            return None
        return next((c for c in (t.ui.find(app=SETTINGS, role="combo box", all=True) or [])
                     if c["cy"] is not None and abs(c["cy"] - lab["cy"]) < 40), None)

    mode = row_combo("전원 모드")
    t.expect(mode, "[전원 모드] 콤보")
    t.click(mode["cx"], mode["cy"])                       # 최고 전원 효율 · 균형 (· 최고 성능) — 위로 하나
    time.sleep(0.6)
    t.key("up")
    time.sleep(0.2)
    t.key("ret")
    t.expect(t.wait(lambda: profile(t) == "power-saver", 10), f"전원 모드 = power-saver ({profile(t)})")
    t.shot("전원 모드")

    at = row_combo("배터리 절약 모드 저절로 켜기")
    t.expect(at, "[배터리 절약 모드 저절로 켜기] 콤보")
    t.click(at["cx"], at["cy"])                           # 20% → 30%
    time.sleep(0.6)
    t.key("down")
    time.sleep(0.2)
    t.key("ret")
    got = lambda: t.sh("python3 -c \"import json,os;print(json.load(open(os.path.expanduser("
                       "'~/.config/sekai/settings.json'))).get('power',{}).get('saver_at'))\"").out.strip()
    t.expect(t.wait(lambda: got() == "30", 5), f"saver_at = 30 으로 저장 ({got()})")


# ── 전원 단추 · 덮개 ──
LID_LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mm", "agent", "vlid.py")
LID, FIFO = "/tmp/vlid.py", "/tmp/vlid.fifo"
LOCK_MARK = "$XDG_RUNTIME_DIR/sekai-lock.pid.locked"


def qstatus(t, since=None):
    """VM 은 QEMU 상태. 실기는 원격으로 상태를 볼 수 없으니 since(시각) 뒤 커널 로그에 절전 진입이 있었는지로 —
    있었으면 "suspended"(이미 깨어났더라도), 없으면 "running"."""
    if config.REAL:
        if since is None:
            return "running"
        r = t.root(f"journalctl -k -q --no-pager --since @{int(since)} -g 'PM: suspend entry'", timeout=20)
        return "suspended" if r.out.strip() else "running"
    return (t.q.cmd("query-status") or {}).get("status")


def inhibited(t, what):
    """작업 표시줄(WHO=SekaiOS)이 logind 에 그 억제를 걸었나"""
    return t.sh(f"systemd-inhibit --list --no-pager | grep -q '^SekaiOS .*{what}'").ok


def screens_on(t):
    return all(m.get("dpmsStatus", True) for m in (t.hypr("monitors") or []))


def logind_lid(t):
    return t.sh("busctl get-property org.freedesktop.login1 /org/freedesktop/login1 "
                "org.freedesktop.login1.Manager LidClosed").out.strip()


def wake_if_asleep(t):
    if config.REAL:                                       # 실기는 원격으로 깨울 수 없다 — 잠들게 하는 단계는 건너뛴다
        return
    if qstatus(t) == "suspended":
        t.q.cmd("system_wakeup")
        t.wait(lambda: t.sh("true").ok, 30)


def unlock(t):
    """절전 직전에 잠긴 화면을 푼다"""
    if t.wait(lambda: t.sh(f"test -e {LOCK_MARK}").ok, 8):
        time.sleep(1)
        t.click(config.SCREEN[0] // 2, config.SCREEN[1] // 2)
        time.sleep(1)
        t.type(PASSWORD)
        t.key("ret")
        t.expect(t.wait(lambda: not t.sh(f"test -e {LOCK_MARK}").ok, 10), "잠금을 풀었다")


def restart_after_s3(t):
    """S3 를 거친 QEMU 는 화면 캡처·다시 부팅이 망가진다 — 같은 디스크로 QEMU 를 새로 띄운다 (mm/vm.py)"""
    if vm.s3_used():
        t.ui.stop()
        t.ui.pushed = False                              # /tmp 가 비었다 — 에이전트를 다시 올린다
        t.expect(vm.cold_restart(t.q), "절전 시험 뒤 QEMU 를 새로 띄워 다시 로그인")


def sleep_and_wake(t, trigger, what):
    if config.REAL:
        from mm.realio import RealOnly
        raise RealOnly(f"{what} → 절전: 실기는 하네스가 깨울 수 없어 VM 에서만")
    vm.mark_s3()                                         # 이 QEMU 는 이제 다시 부팅하지 못한다 — 하네스가 새로 띄운다
    trigger()
    t.expect(t.wait(lambda: qstatus(t) == "suspended", 30, every=1), f"{what} → 절전 (QEMU suspended)")
    time.sleep(2)
    t.q.cmd("system_wakeup")
    t.expect(t.wait(lambda: qstatus(t) == "running", 15), "깨어났다")
    t.expect(t.wait(lambda: t.sh("true").ok, 30), "깨어난 뒤 ssh 가 닿는다")
    time.sleep(6)
    t.expect(qstatus(t) == "running", "깨어난 뒤 다시 잠들지 않는다")
    unlock(t)


@test("전원 단추 — 억제를 잡고 설정대로: 디스플레이 끄기(마우스로 켜짐) · 아무 것도 안 함 · 절전", suite="laptop",
      timeout=300)
def power_button(t):
    t.after(lambda: restart_after_s3(t))                 # 정리 맨 끝에 (after 는 거꾸로 돈다)
    t.after(lambda: (wake_if_asleep(t), setting(t, "power", "button_ac", "")))
    t.expect(t.wait(lambda: inhibited(t, "handle-power-key"), 10),
             "작업 표시줄이 logind 에 전원 단추 억제를 걸었다 (안 걸렸으면 단추가 VM 을 끈다 — 여기서 멈춤)")
    boot = vm.boot_id()

    setting(t, "power", "button_ac", "display")
    t.q.cmd("system_powerdown")
    t.expect(t.wait(lambda: not screens_on(t), 5), "디스플레이 끄기 — 화면이 꺼졌다")
    time.sleep(2)
    t.q.move(700, 500)
    t.q.move(900, 600)
    t.expect(t.wait(lambda: screens_on(t), 5), "마우스를 움직이자 다시 켜졌다")

    setting(t, "power", "button_ac", "nothing")
    t.q.cmd("system_powerdown")
    time.sleep(6)
    t.expect(qstatus(t) == "running" and vm.boot_id() == boot and screens_on(t), "아무 것도 안 함 — 그대로 켜져 있다")

    setting(t, "power", "button_ac", "sleep")
    sleep_and_wake(t, lambda: t.q.cmd("system_powerdown"), "전원 단추")
    t.expect(vm.boot_id() == boot, "다시 부팅하지 않고 이어졌다")


@test("덮개 — 가짜 덮개를 닫으면 설정대로: 아무 것도 안 함 · 절전 (늦게 생긴 덮개도 억제)", suite="laptop", timeout=300)
def lid_switch(t):
    t.after(lambda: restart_after_s3(t))
    # 뒷정리는 따로따로 — 묶어 두면 앞의 것이 실패할 때(실기의 QMP 등) 가짜 덮개가 '닫힘'으로 남아,
    #   작업 표시줄이 다시 뜨며 덮개 억제가 잠깐 풀리는 순간 logind 가 실제 PC 를 재웠다 (2026-10-06 노트북)
    t.after(lambda: setting(t, "power", "lid_ac", ""))
    t.after(lambda: t.root(f"test -p {FIFO} && {{ echo open > {FIFO}; sleep 0.5; echo quit > {FIFO}; }}; true", timeout=15))
    t.after(lambda: wake_if_asleep(t))
    remote.push(LID_LOCAL, LID)
    t.root(f"rm -f {FIFO}; (setsid python3 {LID} serve {FIFO} >/tmp/vlid.log 2>&1 < /dev/null &)")
    t.expect(t.wait(lambda: t.root(f"test -p {FIFO}").ok, 5), "가짜 덮개를 만들었다")
    t.expect(t.wait(lambda: inhibited(t, "handle-lid-switch"), 20),
             "작업 표시줄이 덮개 억제까지 잡았다 (덮개가 늦게 생겨도)")
    t.expect("Lid Switch" in json.dumps(t.hypr("devices") or {}), "합성기가 덮개 스위치를 본다")

    setting(t, "power", "lid_ac", "nothing")
    closed_at = time.time() - 1
    t.root(f"echo close > {FIFO}")
    t.expect(t.wait(lambda: logind_lid(t) == "b true", 5), "logind 가 덮개 닫힘을 본다")
    time.sleep(6)
    t.expect(qstatus(t, closed_at) == "running", "아무 것도 안 함 — 그대로 켜져 있다")
    t.root(f"echo open > {FIFO}")
    t.expect(t.wait(lambda: logind_lid(t) == "b false", 5), "덮개를 열었다")
    time.sleep(3)                                        # 작업 표시줄도 열린 것을 본다 (2초마다)

    setting(t, "power", "lid_ac", "sleep")
    sleep_and_wake(t, lambda: t.root(f"echo close > {FIFO}"), "덮개 닫기")
    t.root(f"echo open > {FIFO}")


@test("자리 비움 시간 — 배터리로 돌면 배터리 쪽 시간, 충전기를 꽂으면 전원 연결 쪽 시간으로 swayidle 을 다시 건다",
      suite="laptop", quick=True)
def idle_times_follow_power(t):
    battery_test(t)
    for k, v in (("screen_off", 600), ("lock", 900), ("suspend", 0),
                 ("screen_off_battery", 300), ("lock_battery", 600), ("suspend_battery", 900)):
        setting(t, "power", k, v)
    t.after(lambda: t.sh("pkill -HUP -f '^\\S*python3\\S* \\S*[s]ekai-idle'; true"))

    def idle():
        return t.sh("pgrep -a -x swayidle").out
    fake_battery(t, 50, "Discharging")
    t.expect(t.wait(lambda: "timeout 300 hyprctl dispatch dpms off" in idle() and "timeout 900 systemctl suspend" in idle(),
                    15), f"배터리 — 화면 끄기 5분 · 절전 15분 ({idle()[:200]})")
    fake_battery(t, 50, "Charging")
    t.expect(t.wait(lambda: "timeout 600 hyprctl dispatch dpms off" in idle() and "systemctl suspend" not in idle(),
                    15), f"전원 연결 — 화면 끄기 10분 · 절전 안 함 ({idle()[:200]})")


# ── 터치패드 · Fn 키 ──
AGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mm", "agent")
TP_FIFO = "/tmp/vtouchpad.fifo"
KEY_TOUCHPAD_TOGGLE = 0x212


def cursor(t):
    c = t.hypr("cursorpos") or {}
    return c.get("x"), c.get("y")


def tp_swipe(t, fingers, dx, dy):
    t.root(f"echo 'swipe {fingers} {dx} {dy}' > {TP_FIFO}", timeout=10)
    time.sleep(1.0)


def get_setting(t, section, key):
    return t.sh("python3 -c \"import json,os;print(json.dumps(json.load(open(os.path.expanduser("
                f"'~/.config/sekai/settings.json'))).get('{section}',{{}}).get('{key}')))\"").out.strip()


@test("터치패드 Fn 키 — 끄면 터치패드가 커서를 못 움직이고 설정에 남는다, 다시 누르면 켜진다", suite="laptop", timeout=180)
def touchpad_fn_key(t):
    remote.push(os.path.join(AGENT, "vtouchpad.py"), "/tmp/vtouchpad.py")
    remote.push(os.path.join(AGENT, "vkey.py"), "/tmp/vkey.py")
    t.after(lambda: (t.root(f"test -p {TP_FIFO} && echo quit > {TP_FIFO}; true", timeout=10),
                     t.sh("sekai-ctl touchpad on; true"), time.sleep(1)))
    t.sh("sekai-ctl touchpad on")                        # 켜진 데서 시작
    t.expect(t.wait(lambda: get_setting(t, "input", "tp_enabled") in ("true", "null"), 5), "처음엔 터치패드 켬")
    t.root(f"rm -f {TP_FIFO}; (setsid python3 /tmp/vtouchpad.py serve {TP_FIFO} >/tmp/vtp.log 2>&1 < /dev/null &)")
    t.expect(t.wait(lambda: "mafuyumom-virtual-touchpad" in json.dumps(t.hypr("devices") or {}), 10),
             "가상 터치패드가 합성기에 잡혔다")
    time.sleep(1)

    c0 = cursor(t)
    tp_swipe(t, 1, 600, 0)
    t.expect(cursor(t) != c0, f"켜진 터치패드는 커서를 움직인다 ({c0} → {cursor(t)})")

    t.expect("ok" in t.root(f"python3 /tmp/vkey.py {KEY_TOUCHPAD_TOGGLE}", timeout=20).out, "Fn 터치패드 키를 눌렀다")
    t.expect(t.wait(lambda: get_setting(t, "input", "tp_enabled") == "false", 5), "설정에 '터치패드 끔'이 저장됐다")
    t.shot("터치패드 끔")
    time.sleep(1)
    c1 = cursor(t)
    tp_swipe(t, 1, -600, 0)
    t.expect(cursor(t) == c1, f"끈 터치패드는 커서를 못 움직인다 ({c1} → {cursor(t)})")
    t.expect(t.sh("grep -q 'name = mafuyumom-virtual-touchpad' ~/.config/hypr/sekai.conf").ok,
             "다음 로그인에도 꺼진 채 (조각 파일의 device 묶음)")

    t.root(f"python3 /tmp/vkey.py {KEY_TOUCHPAD_TOGGLE}", timeout=20)
    # 기본값(켬)으로 돌아오면 설정 파일에서 빠진다 (store 는 바꾼 것만 적는다) — null 도 켬
    t.expect(t.wait(lambda: get_setting(t, "input", "tp_enabled") in ("true", "null"), 5), "다시 누르자 켬")
    time.sleep(1)
    c2 = cursor(t)
    tp_swipe(t, 1, 600, 0)
    t.expect(cursor(t) != c2, f"다시 켠 터치패드는 커서를 움직인다 ({c2} → {cursor(t)})")


@test("세 손가락 쓸기 끄기 — 설정에서 끄면 아래로 쓸어도 바탕 화면 보기가 되지 않는다", suite="laptop", timeout=120)
def gesture3_off(t):
    remote.push(os.path.join(AGENT, "vtouchpad.py"), "/tmp/vtouchpad.py")
    t.kill("sekai-notepad")
    t.after(lambda: (setting(t, "input", "gesture3", True), t.kill("sekai-notepad")))
    t.expect(t.launch("sekai-notepad", "org.sekaios.Notepad"), "메모장")
    setting(t, "input", "gesture3", False)
    t.root("python3 /tmp/vtouchpad.py swipe 3 0 900", timeout=30)
    time.sleep(1.5)
    np = next((c for c in t.clients() if c["class"] == "org.sekaios.Notepad"), {})
    t.expect(np and not np.get("sekaiMinimized"), "세 손가락 아래로 — 꺼 두었으니 창이 그대로")
