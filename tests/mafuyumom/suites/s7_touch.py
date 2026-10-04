"""터치패드 제스처 — VM 안에 uinput 가상 터치패드(mm/agent/vtouchpad.py)를 만들어 여러 손가락으로 쓴다.
   libinput 이 진짜 터치패드처럼 알아보므로 실제 노트북과 같은 길(libinput → WorldLink → 셸)을 지난다.
   세 손가락: 위 = 작업 보기, 아래 = 바탕 화면 보기(다시 위 = 되살리기), 좌우 = 앱 전환 / 네 손가락 좌우 = 데스크톱"""
import os
import time

from mm import remote
from mm.runner import test

TP_LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mm", "agent", "vtouchpad.py")
TP = "/tmp/vtouchpad.py"
NP, CALC = "org.sekaios.Notepad", "org.sekaios.Calculator"


def swipe(t, fingers, dx, dy):
    if not t.sh(f"test -f {TP}").ok:
        remote.push(TP_LOCAL, TP)
    r = t.root(f"python3 {TP} swipe {fingers} {dx} {dy}", timeout=30)
    if "ok" not in r.out:
        t.fail(f"가상 터치패드 실패: {r.err[-300:]}")
    time.sleep(0.8)


def active(t):
    return (t.hypr("activewindow") or {}).get("class")


def two_windows(t):
    t.kill("sekai-notepad")
    t.close(CALC)
    t.after(lambda: (t.kill("sekai-notepad"), t.close(CALC)))
    t.expect(t.launch("sekai-notepad", NP), "메모장")
    t.expect(t.launch("sekai-calc", CALC), "계산기")
    t.wait(lambda: active(t) == CALC, 5)


@test("가상 터치패드가 터치패드로 잡힌다 (libinput)", suite="touch", quick=True)
def touchpad_seen(t):
    remote.push(TP_LOCAL, TP)
    # 장치를 만든 채 hyprctl devices 에 나오는지 — swipe 0 은 손가락 없이 만들고 지운다
    t.root(f"(python3 - <<'PY'\nimport sys, time\nsys.path.insert(0, '/tmp')\nimport vtouchpad\np = vtouchpad.Pad()\ntime.sleep(2)\np.close()\nPY\n) >/dev/null 2>&1 &")
    seen = t.wait(lambda: "mafuyumom-virtual-touchpad" in t.sh("hyprctl devices").out.lower(), 4)
    t.expect(seen, "hyprctl devices 에 MafuyuMom virtual touchpad")
    time.sleep(2)


@test("세 손가락 아래 = 바탕 화면 보기, 다시 위 = 되살리기", suite="touch", quick=True)
def three_down_up(t):
    two_windows(t)

    def mins():
        return [bool(c.get("sekaiMinimized")) for c in t.clients() if c["class"] in (NP, CALC)]
    swipe(t, 3, 0, 900)
    t.expect(t.wait(lambda: mins() and all(mins()), 3), f"세 손가락 아래 → 모두 최소화 ({mins()})")
    swipe(t, 3, 0, -900)
    t.expect(t.wait(lambda: mins() and not any(mins()), 3), f"세 손가락 위 → 되살렸다 ({mins()})")


@test("세 손가락 좌우 = 앱 전환", suite="touch")
def three_side(t):
    two_windows(t)
    swipe(t, 3, 900, 0)
    t.expect(t.wait(lambda: active(t) == NP, 3), f"세 손가락 오른쪽 → 메모장 ({active(t)})")
    swipe(t, 3, 900, 0)
    t.expect(t.wait(lambda: active(t) == CALC, 3), f"한 번 더 → 계산기 ({active(t)})")


@test("세 손가락 위 = 작업 보기", suite="touch")
def three_up(t):
    two_windows(t)
    swipe(t, 3, 0, -900)
    time.sleep(0.6)
    t.shot("작업보기")
    t.sh("hyprctl dispatch hyprexpo:expo off >/dev/null")
    t.note("작업 보기(hyprexpo)는 상태를 알려 주지 않는다 — 스크린숏으로 본다")


@test("네 손가락 좌우 = 데스크톱 넘기기", suite="touch")
def four_side(t):
    def ws():
        return (t.hypr("activeworkspace") or {}).get("id")
    made = False
    if 2 not in [w.get("id") for w in (t.hypr("workspaces") or [])]:
        t.sh("hyprctl dispatch workspace 1 >/dev/null; sekai-ctl desktop new >/dev/null 2>&1; true")   # 데스크톱이 하나뿐이면 넘길 곳이 없다
        made = t.wait(lambda: ws() == 2, 3)
        t.expect(made, "데스크톱 2 를 만들었다")

    def back():
        t.sh("hyprctl dispatch workspace 2 >/dev/null")
        if made:
            t.sh("sekai-ctl desktop close >/dev/null 2>&1; true")
        t.sh("hyprctl dispatch workspace 1 >/dev/null")
    t.after(back)
    t.sh("hyprctl dispatch workspace 1 >/dev/null")
    t.expect(t.wait(lambda: ws() == 1, 3), "데스크톱 1 에서")
    swipe(t, 4, -1200, 0)                                 # 손가락을 왼쪽으로 = 오른쪽(다음) 데스크톱 (윈도우처럼)
    t.expect(t.wait(lambda: ws() == 2, 3), f"네 손가락 왼쪽 → 데스크톱 2 ({ws()})")
    swipe(t, 4, 1200, 0)
    t.expect(t.wait(lambda: ws() == 1, 3), f"네 손가락 오른쪽 → 데스크톱 1 ({ws()})")
