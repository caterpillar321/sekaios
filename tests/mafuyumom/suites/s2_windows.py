"""창 관리 — 제목줄 단추, 최대화·최소화·복원, 스냅, 끌기 취소, 대화상자, 크기 조절, 무작위 조작.
   (예전 local/test-tools/oclick.sh 의 B1~B6 을 옮기고 sekai33 의 최소화·끌기를 더함)"""
import random
import time

from mm.runner import test

NP = "org.sekaios.Notepad"


def fresh_notepad(t, x=400, y=200, w=900, h=640):
    t.kill("sekai-notepad")
    t.gone(NP, 5)
    c = t.launch("sekai-notepad", NP)
    t.expect(c, "메모장이 떴다")
    t.sh(f"hyprctl dispatch resizewindowpixel exact {w} {h},class:{NP} >/dev/null; "
         f"hyprctl dispatch movewindowpixel exact {x} {y},class:{NP} >/dev/null")
    time.sleep(0.8)
    return main_win(t)


def main_win(t):
    m = [c for c in t.clients(NP) if c["size"][0] > 500]
    return m[0] if m else None


def bar(c):
    """(제목줄 가운데 y, 닫기 x, 최대화 x, 최소화 x)"""
    x, y = c["at"]
    w, _ = c["size"]
    top = c.get("sekaiTop", 34) or 34
    by = y - top // 2
    return by, x + w - 19, x + w - 57, x + w - 95


def state(t):
    c = main_win(t)
    if not c:
        return "없음"
    if c.get("sekaiMinimized") or c["workspace"]["name"].startswith("special"):
        return "최소화"
    return "최대화" if c["fullscreen"] == 1 else "보통"


@test("창 단추를 누른 채 벗어나 떼면 아무 일도 없다", suite="windows")
def button_cancel(t):
    c = fresh_notepad(t)
    by, cl, mx, mn = bar(c)
    for x, name in ((cl, "닫기"), (mx, "최대화"), (mn, "최소화")):
        t.q.move(x, by)
        time.sleep(0.1)
        t.q.button(True)
        time.sleep(0.1)
        for i in range(1, 11):
            t.q.move(x + (900 - x) * i / 10, by + (500 - by) * i / 10)
            time.sleep(0.02)
        t.q.button(False)
        time.sleep(0.8)
        t.expect(state(t) == "보통", f"{name} 단추 — 벗어나 뗌 → 그대로 ({state(t)})")


@test("최대화·복원 (단추·제목줄 두 번 누르기)", suite="windows")
def maximize(t):
    c = fresh_notepad(t)
    by, cl, mx, mn = bar(c)
    t.click(mx, by)
    t.expect(t.wait(lambda: state(t) == "최대화", 3), "최대화 단추 → 최대화")
    t.shot("최대화")
    c = main_win(t)
    t.click(c["at"][0] + 300, c["at"][1] - 17, n=2)
    t.expect(t.wait(lambda: state(t) == "보통", 3), "제목줄 두 번 → 복원")
    c = main_win(t)
    t.expect(abs(c["size"][0] - 900) <= 4 and abs(c["size"][1] - 640) <= 4, f"원래 크기로 ({c['size']})")


@test("최소화하고 작업 표시줄로 되살리기 — 데스크톱은 그대로", suite="windows")
def minimize(t):
    c = fresh_notepad(t)
    ws = c["workspace"]["name"]
    by, cl, mx, mn = bar(c)
    t.click(mn, by)
    t.expect(t.wait(lambda: state(t) == "최소화", 3), "최소화 단추 → 최소화")
    c = main_win(t)
    t.expect(c["workspace"]["name"] == ws, f"데스크톱 그대로 ({c['workspace']['name']})")
    btn = t.ui.find(app="sekai-panel", role="button", all=True)
    # 작업 표시줄 단추에 이름이 없어(접근성 발견) 자리로 — 메모장은 실행 중 앱 단추들 가운데 마지막
    tasks = [b for b in btn if 40 < (b["x"] or 0) < 1600 and b["y"] and b["y"] > 1000]
    t.expect(tasks, "작업 표시줄에 창 단추")
    t.click(tasks[-1]["cx"], tasks[-1]["cy"])
    t.expect(t.wait(lambda: state(t) == "보통", 3), f"작업 표시줄 → 되살아남 ({state(t)})")
    t.expect((t.hypr("activewindow") or {}).get("class") == NP, "초점도 받았다")


@test("끌어서 스냅 — 왼쪽·오른쪽 절반, 레이아웃 바, Esc 취소", suite="windows")
def snap(t):
    c = fresh_notepad(t, 500, 250)
    by = c["at"][1] - 17
    t.drag(c["at"][0] + 300, by, 1, 600, steps=25)
    time.sleep(1.2)
    c = main_win(t)
    t.expect(c["at"][0] <= 2 and abs(c["size"][0] - 960) <= 8, f"왼쪽 절반 ({c['at']}, {c['size']})")
    t.sh(f"hyprctl dispatch resizewindowpixel exact 900 640,class:{NP} >/dev/null; "
         f"hyprctl dispatch movewindowpixel exact 500 250,class:{NP} >/dev/null")
    time.sleep(0.8)
    c = main_win(t)
    t.drag(c["at"][0] + 300, c["at"][1] - 17, 1919, 600, steps=25)
    time.sleep(1.2)
    c = main_win(t)
    t.expect(c["at"][0] >= 955 and abs(c["size"][0] - 960) <= 8, f"오른쪽 절반 ({c['at']}, {c['size']})")
    t.sh(f"hyprctl dispatch resizewindowpixel exact 900 640,class:{NP} >/dev/null; "
         f"hyprctl dispatch movewindowpixel exact 500 250,class:{NP} >/dev/null")
    time.sleep(0.8)
    c = main_win(t)
    x0, y0 = c["at"][0] + 300, c["at"][1] - 17
    t.q.move(x0, y0)
    t.q.button(True)
    for i in range(1, 16):
        t.q.move(x0 + (960 - x0) * i / 15, y0 + (400 - y0) * i / 15)
        time.sleep(0.02)
    for i in range(1, 16):
        t.q.move(960, 400 - 340 * i / 15)
        time.sleep(0.02)
    time.sleep(0.8)
    t.shot("레이아웃바")
    t.key("esc")
    time.sleep(0.4)
    t.q.button(False)
    time.sleep(0.8)
    c = main_win(t)
    t.expect(tuple(c["at"]) == (500, 250) and tuple(c["size"]) == (900, 640), f"Esc → 원래 자리 ({c['at']}, {c['size']})")


@test("빠른 크기 조절 — 모서리가 놓은 자리에", suite="windows")
def resize(t):
    c = fresh_notepad(t)
    x, y = c["at"]
    w, h = c["size"]
    t.drag(x + w - 1, y + h - 1, 1500, 950, steps=8)
    time.sleep(0.8)
    c = main_win(t)
    dx = c["at"][0] + c["size"][0] - 1 - 1500
    dy = c["at"][1] + c["size"][1] - 1 - 950
    t.expect(abs(dx) <= 6 and abs(dy) <= 6, f"모서리 오차 ({dx},{dy})")


@test("대화상자는 부모와 함께 최소화·복원, 작업 표시줄엔 한 칸", suite="windows")
def dialog_follow(t):
    c = fresh_notepad(t)
    t.click(c["at"][0] + 450, c["at"][1] + 320)
    t.type("x")
    by, cl, mx, mn = bar(c)
    t.click(cl, by)                                       # 저장할까요?
    dlg = t.wait(lambda: [d for d in t.clients(NP) if d["size"][0] < 500], 5)
    t.expect(dlg, "저장할까요? 창")
    t.expect(dlg[0].get("sekaiParent", "0x0") != "0x0", "대화상자의 부모를 안다")
    t.click(mn, by)
    t.expect(t.wait(lambda: all(d.get("sekaiMinimized") for d in t.clients(NP)), 3), "부모 최소화 → 대화상자도 숨음")
    t.sh(f"hyprctl dispatch focuswindow address:{dlg[0]['address']} >/dev/null")
    t.expect(t.wait(lambda: not any(d.get("sekaiMinimized") for d in t.clients(NP)), 3), "대화상자를 고르면 둘 다 돌아옴")
    d = [d for d in t.clients(NP) if d["size"][0] < 500][0]
    t.click(d["at"][0] + d["size"][0] // 2, d["at"][1] + d["size"][1] - 40)   # 저장 안 함
    t.gone(NP, 5)


@test("무작위 최대화·최소화·닫기 60번 — 상태가 어긋나지 않는다", suite="windows", timeout=400, slow=True)
def fuzz(t):
    rnd = random.Random(7)
    apps = [("foot", "foot"), ("sekai-calc", "org.sekaios.Calculator"), ("sekai-notepad", NP)]
    bad = []
    for step in range(60):
        cs = t.clients()
        cs = [c for c in cs if c.get("class") not in ("", "sekai-desk")]
        r = rnd.random()
        if r < 0.2 and len(cs) < 6 or not cs:
            cmd, _ = rnd.choice(apps)
            t.sh(f"hyprctl dispatch exec {cmd} >/dev/null")
            time.sleep(1.2)
        elif r < 0.3 and len(cs) > 2:
            c = rnd.choice(cs)
            t.sh(f"hyprctl dispatch closewindow address:{c['address']} >/dev/null")
            time.sleep(0.6)
        elif r < 0.55:
            c = rnd.choice(cs)
            t.sh(f"hyprctl dispatch focuswindow address:{c['address']} >/dev/null; hyprctl dispatch fullscreen 1 >/dev/null")
            time.sleep(0.4)
        elif r < 0.8:
            c = rnd.choice(cs)
            t.sh(f"hyprctl dispatch sekaiminimize toggle,address:{c['address']} >/dev/null")
            time.sleep(0.4)
        else:
            c = rnd.choice(cs)
            t.sh(f"hyprctl dispatch focuswindow address:{c['address']} >/dev/null")
            time.sleep(0.3)
        ws = t.hypr("activeworkspace") or {}
        mine = [c for c in t.clients() if c["workspace"]["id"] == ws.get("id") and not c.get("hidden")]
        if bool(ws.get("hasfullscreen")) != any(c["fullscreen"] for c in mine):
            bad.append(f"{step}: hasfullscreen={ws.get('hasfullscreen')} 창={[c['fullscreen'] for c in mine]}")
    t.sh("pkill -x foot; pkill -x sekai-notepad; pkill -f sekai-cal[c]; true")
    t.expect(not bad, f"작업 공간의 전체 화면 표시가 창 상태와 맞다 (어긋남 {len(bad)}: {bad[:3]})")
