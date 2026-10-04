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
    # 패널이 새 창을 기억한 크기로 가운데에 놓는다 — 그보다 먼저 옮기면 되돌려진다. 자리가 맞을 때까지
    for _ in range(4):
        m = main_win(t)
        if m and is_max(m):                               # 메모장은 최대화한 채 닫으면 최대화로 다시 연다
            t.sh(f"hyprctl dispatch sekaimaximize off,address:{m['address']} >/dev/null")
            time.sleep(0.5)
        t.sh(f"hyprctl dispatch resizewindowpixel exact {w} {h},class:{NP} >/dev/null; "
             f"hyprctl dispatch movewindowpixel exact {x} {y},class:{NP} >/dev/null")
        time.sleep(0.8)
        m = main_win(t)
        if m and abs(m["at"][0] - x) < 4 and abs(m["at"][1] - y) < 4 and abs(m["size"][0] - w) < 4:
            return m
    t.fail(f"메모장을 {x},{y} {w}x{h} 에 놓지 못했다 ({m and (m['at'], m['size'])})")


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


def is_max(c):
    """최대화 — WorldLink sekai34 부터 창의 상태(sekaiMaximized), 옛 합성기는 fullscreen 1"""
    return bool(c.get("sekaiMaximized")) if "sekaiMaximized" in c else c.get("fullscreen") == 1


def state(t):
    c = main_win(t)
    if not c:
        return "없음"
    if c.get("sekaiMinimized") or c["workspace"]["name"].startswith("special"):
        return "최소화"
    if c.get("fullscreen", 0) & 2:
        return "전체 화면"
    return "최대화" if is_max(c) else "보통"


def work_box(t, c):
    """최대화한 창이 있어야 할 자리 — 그 모니터의 작업 영역에서 제목줄 몫(sekaiTop)을 뺀 곳"""
    m = next((m for m in (t.hypr("monitors") or []) if m["id"] == c["monitor"]), None)
    if not m:
        return None
    sc = m.get("scale", 1) or 1
    l, tp, r, b = (m.get("reserved") or [0, 0, 0, 0])[:4]
    top = c.get("sekaiTop", 0) or 0
    return (m["x"] + l, m["y"] + tp + top, round(m["width"] / sc) - l - r, round(m["height"] / sc) - tp - b - top)


def fits_work(t, c):
    w = work_box(t, c)
    return w and all(abs(a - b) <= 2 for a, b in zip((*c["at"], *c["size"]), w)), w


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


@test("최대화·복원 (단추·제목줄 두 번 누르기)", suite="windows", quick=True)
def maximize(t):
    c = fresh_notepad(t)
    by, cl, mx, mn = bar(c)
    t.click(mx, by)
    t.expect(t.wait(lambda: state(t) == "최대화", 3), "최대화 단추 → 최대화")
    t.shot("최대화")
    c = t.wait(lambda: (lambda m: m if fits_work(t, m)[0] else None)(main_win(t)), 3) or main_win(t)
    ok, box = fits_work(t, c)
    t.expect(ok, f"작업 영역에 꼭 맞다 (창 {c['at']} {c['size']} · 작업 영역 {box})")
    t.expect(c.get("fullscreen", 0) == 0, f"작업 공간 전체 화면을 쓰지 않는다 (fullscreen={c.get('fullscreen')})")
    t.expect(not (t.hypr("activeworkspace") or {}).get("hasfullscreen"), "데스크톱이 전체 화면 상태가 아니다")
    t.click(c["at"][0] + 300, c["at"][1] - 17, n=2)
    t.expect(t.wait(lambda: state(t) == "보통", 3), "제목줄 두 번 → 복원")
    c = main_win(t)
    t.expect(abs(c["size"][0] - 900) <= 4 and abs(c["size"][1] - 640) <= 4, f"원래 크기로 ({c['size']})")


@test("최소화하고 작업 표시줄로 되살리기 — 데스크톱은 그대로", suite="windows", quick=True)
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


@test("끌어서 스냅 — 왼쪽·오른쪽 절반, 레이아웃 바, Esc 취소", suite="windows", quick=True)
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
            t.sh(f"hyprctl dispatch sekaimaximize toggle,address:{c['address']} >/dev/null")
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
        time.sleep(0.3)                                    # 크기 애니메이션이 끝난 뒤
        for c in t.clients():
            if is_max(c) and not c.get("hidden") and not c.get("sekaiMinimized") and not fits_work(t, c)[0]:
                bad.append(f"{step}: 최대화 창 {c['class']} 이 작업 영역에 안 맞다 {c['at']} {c['size']} ≠ {work_box(t, c)}")
    t.sh("pkill -x foot; pkill -x sekai-notepad; pkill -f sekai-cal[c]; true")
    t.expect(not bad, f"작업 공간의 전체 화면 표시·최대화 창 자리가 맞다 (어긋남 {len(bad)}: {bad[:3]})")


# ── sekai34: 최대화가 창의 상태 (작업 공간 전체 화면이 아니다) ──
TERM = "org.sekaios.Nenerobo"


def maximize_win(t, addr):
    t.sh(f"hyprctl dispatch sekaimaximize on,address:{addr} >/dev/null")


@test("최대화 창 여럿 — 둘 다 최대화된 채로 오가고, 데스크톱은 전체 화면 상태가 아니다", suite="windows", quick=True)
def multimax(t):
    n = fresh_notepad(t)
    t.close(TERM)
    t.after(lambda: t.close(TERM))
    term = t.launch("sekai-terminal", TERM, timeout=15)
    t.expect(term, "터미널")
    maximize_win(t, n["address"])
    maximize_win(t, term["address"])
    time.sleep(0.8)
    for cls in (NP, TERM):
        c = t.window(cls)
        ok, box = fits_work(t, c)
        t.expect(is_max(c) and ok, f"{cls} 최대화 · 작업 영역에 맞다 ({c['at']} {c['size']} · {box})")
    t.sh(f"hyprctl dispatch focuswindow address:{n['address']} >/dev/null")
    time.sleep(0.5)
    t.shot("메모장위")
    t.expect(all(is_max(t.window(cls)) for cls in (NP, TERM)), "다른 최대화 창을 골라도 둘 다 최대화 그대로")
    t.expect(not (t.hypr("activeworkspace") or {}).get("hasfullscreen"), "데스크톱이 전체 화면 상태가 아니다 (뒤 창도 그려진다)")
    t.expect((t.hypr("activewindow") or {}).get("address") == n["address"], "고른 창이 맨 앞·초점")


@test("최대화 창을 끌면 복원 — 커서는 제목줄 같은 자리, 끌다가 Esc 면 다시 최대화", suite="windows")
def drag_restore(t):
    c = fresh_notepad(t)
    maximize_win(t, c["address"])
    m = t.wait(lambda: (lambda w: w if is_max(w) and fits_work(t, w)[0] else None)(main_win(t)), 3)
    t.expect(m, "최대화")
    top = m.get("sekaiTop", 34) or 34
    gx, gy = m["at"][0] + m["size"][0] * 0.3, m["at"][1] - top // 2      # 제목줄 왼쪽 30% 자리
    t.drag(gx, gy, gx + 40, gy + 260, steps=25)
    time.sleep(0.8)
    r = main_win(t)
    t.shot("끌어복원")
    t.expect(state(t) == "보통", f"끌면 복원 ({state(t)})")
    t.expect(abs(r["size"][0] - 900) <= 4 and abs(r["size"][1] - 640) <= 4, f"원래 크기 ({r['size']})")
    cx, cy = gx + 40, gy + 260
    rx = (cx - r["at"][0]) / r["size"][0]
    t.expect(r["at"][1] - top <= cy <= r["at"][1] and abs(rx - 0.3) < 0.08,
             f"커서가 제목줄의 같은 자리 (가로 {rx:.2f} · 창 {r['at']})")
    # 끌다가 Esc
    maximize_win(t, r["address"])
    m = t.wait(lambda: (lambda w: w if is_max(w) and fits_work(t, w)[0] else None)(main_win(t)), 3)
    t.q.move(gx, gy)
    time.sleep(0.1)
    t.q.button(True)
    for i in range(1, 16):
        t.q.move(gx + 3 * i, gy + 15 * i)
        time.sleep(0.03)
    time.sleep(0.2)
    t.key("esc")
    time.sleep(0.2)
    t.q.button(False)
    t.expect(t.wait(lambda: state(t) == "최대화" and fits_work(t, main_win(t))[0], 3), f"Esc → 다시 최대화 ({state(t)})")
    t.sh(f"hyprctl dispatch sekaimaximize off,address:{r['address']} >/dev/null")
    t.expect(t.wait(lambda: (lambda w: abs(w['size'][0] - 900) <= 4)(main_win(t)), 3), "그 뒤 복원하면 처음 크기 (Esc 가 복원할 자리를 지켰다)")


@test("최대화 → F11 전체 화면 → F11 → 다시 최대화", suite="windows")
def fullscreen_roundtrip(t):
    c = fresh_notepad(t)
    maximize_win(t, c["address"])
    t.expect(t.wait(lambda: state(t) == "최대화", 3), "최대화")
    t.sh(f"hyprctl dispatch focuswindow address:{c['address']} >/dev/null")
    time.sleep(0.3)
    t.key("f11")
    t.expect(t.wait(lambda: state(t) == "전체 화면", 3), f"F11 → 전체 화면 ({state(t)})")
    f = main_win(t)
    t.expect(f["at"] == [0, 0] and f["size"] == [1920, 1080], f"화면 전체 ({f['at']} {f['size']})")
    t.key("f11")
    t.expect(t.wait(lambda: state(t) == "최대화" and fits_work(t, main_win(t))[0], 3), f"F11 → 최대화로 돌아왔다 ({state(t)})")


@test("Win+↑ 최대화 · Win+↓ 복원", suite="windows")
def win_keys(t):
    c = fresh_notepad(t)
    t.sh(f"hyprctl dispatch focuswindow address:{c['address']} >/dev/null")
    time.sleep(0.3)
    t.key("meta_l-up")
    t.expect(t.wait(lambda: state(t) == "최대화" and fits_work(t, main_win(t))[0], 3), f"Win+↑ → 최대화 ({state(t)})")
    t.key("meta_l-down")
    t.expect(t.wait(lambda: state(t) == "보통", 3), f"Win+↓ → 복원 ({state(t)})")
    r = main_win(t)
    t.expect(abs(r["size"][0] - 900) <= 4 and abs(r["size"][1] - 640) <= 4, f"원래 크기 ({r['size']})")
