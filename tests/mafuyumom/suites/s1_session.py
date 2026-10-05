"""세션·셸 — 작업 표시줄, 시작 메뉴, 빠른 설정, 알림, Alt+Tab, 가상 데스크톱, 잠금."""
import time

from mm import config
from mm.runner import test

PANEL = "sekai-panel"


def active(t):
    return (t.hypr("activewindow") or {}).get("class")


@test("작업 표시줄이 떠 있고 시작 단추가 있다", suite="session", quick=True)
def taskbar(t):
    bar = t.ui.find(app=PANEL, role="frame")
    t.expect(bar and bar["y"] is not None and bar["y"] >= config.SCREEN[1] - 80, f"작업 표시줄 자리 {bar and (bar['y'], bar['h'])}")
    start = t.ui.find(app=PANEL, role="button")
    t.expect(start and start["x"] < 60, "왼쪽 끝에 시작 단추")
    nameless = t.ui.unnamed(PANEL)
    if nameless:
        t.finding(f"작업 표시줄의 이름 없는 단추 {len(nameless)}개 — 내레이터가 '단추'라고만 읽는다 "
                  f"({', '.join(str((n['x'], n['y'])) for n in nameless[:6])})")


@test("시작 메뉴 → 모든 앱에서 계산기 열기", suite="session")
def start_menu_list(t):
    t.close("org.sekaios.Calculator")
    t.key("meta_l")
    t.expect(t.ui.wait(app=PANEL, role="text", name="검색", timeout=5), "시작 메뉴가 열리고 검색 칸이 보인다")
    t.shot("시작메뉴")
    t.expect(t.ui.click(app=PANEL, role="label", name="계산기", timeout=5), "모든 앱 목록에 계산기")
    t.expect(t.window("org.sekaios.Calculator"), "계산기 창이 떴다")
    t.expect(not t.ui.find(app=PANEL, role="text", name="검색"), "시작 메뉴는 닫혔다")
    t.close("org.sekaios.Calculator")


@test("시작 메뉴 검색(영문)으로 메모장 열기", suite="session", quick=True)
def start_menu_search(t):
    t.kill("sekai-notepad")
    t.key("meta_l")
    t.expect(t.ui.wait(app=PANEL, role="text", name="검색", timeout=5), "시작 메뉴")
    t.type("notepad")
    time.sleep(1)
    t.shot("검색결과")
    t.key("ret")
    t.expect(t.window("org.sekaios.Notepad"), "Enter 로 첫 결과(메모장)가 열렸다")
    t.kill("sekai-notepad")


@test("빠른 설정(Win+A) 열고 Esc 로 닫기", suite="session")
def quick_settings(t):
    t.key("meta_l-a")
    w = t.ui.wait(app=PANEL, name_re="다크 모드|방해 금지|이더넷|Wi-Fi", timeout=5)
    t.expect(w, "빠른 설정 타일이 보인다")
    t.shot("빠른설정")
    t.key("esc")
    time.sleep(0.6)
    t.expect(not t.ui.find(app=PANEL, name_re="방해 금지"), "Esc 로 닫혔다")


@test("알림이 뜨고 알림 센터에 남는다", suite="session")
def notification(t):
    t.sh("notify-send -a MafuyuMom 'MM 시험 알림' '엄마가 보고 있다'")
    t.expect(t.ui.wait(app=PANEL, name="MM 시험 알림", timeout=5), "알림 팝업에 제목")
    t.shot("알림")
    time.sleep(6)                                       # 팝업이 사라질 때까지
    t.key("meta_l-n")
    t.expect(t.ui.wait(app=PANEL, name="MM 시험 알림", timeout=5), "알림 센터에 남아 있다")
    t.shot("알림센터")
    t.key("esc")


@test("Alt+Tab 으로 다른 창으로", suite="session", quick=True)
def alt_tab(t):
    t.kill("sekai-notepad")
    t.close("org.sekaios.Calculator")
    t.expect(t.launch("sekai-notepad", "org.sekaios.Notepad"), "메모장")
    t.expect(t.launch("sekai-calc", "org.sekaios.Calculator"), "계산기")
    t.wait(lambda: active(t) == "org.sekaios.Calculator", 5)
    t.q.keydown("alt")
    t.q.key("tab")
    time.sleep(0.6)
    t.shot("전환기")
    t.q.keyup("alt")
    t.expect(t.wait(lambda: active(t) == "org.sekaios.Notepad", 5), f"메모장으로 바뀌었다 (지금 {active(t)})")
    t.kill("sekai-notepad")
    t.close("org.sekaios.Calculator")


@test("가상 데스크톱 만들기·옮기기·닫기", suite="session")
def desktops(t):
    ws0 = (t.hypr("activeworkspace") or {}).get("id")
    t.key("meta_l-ctrl-d")
    ws1 = t.wait(lambda: (t.hypr("activeworkspace") or {}).get("id") != ws0 and (t.hypr("activeworkspace") or {}).get("id"), 5)
    t.expect(ws1 and ws1 != ws0, f"새 데스크톱으로 ({ws0} → {ws1})")
    t.key("meta_l-ctrl-left")
    t.expect(t.wait(lambda: (t.hypr("activeworkspace") or {}).get("id") == ws0, 5), "Win+Ctrl+← 로 돌아왔다")
    t.key("meta_l-ctrl-right")
    t.wait(lambda: (t.hypr("activeworkspace") or {}).get("id") == ws1, 5)
    t.key("meta_l-ctrl-f4")
    t.expect(t.wait(lambda: (t.hypr("activeworkspace") or {}).get("id") == ws0, 5), "Win+Ctrl+F4 로 닫고 원래 데스크톱")
    t.expect(t.wait(lambda: ws1 not in [w.get("id") for w in (t.hypr("workspaces") or [])], 3),
             "닫은 데스크톱이 남지 않는다 (네 손가락 쓸기·Win+Ctrl+→ 로 다시 가지 않게)")


@test("잠그고(Win+L) 암호로 풀기", suite="session", quick=True)
def lock_unlock(t):
    mark = "$XDG_RUNTIME_DIR/sekai-lock.pid.locked"
    t.key("meta_l-l")
    t.expect(t.wait(lambda: t.sh(f"test -e {mark}").ok, 8), "잠금 화면이 걸렸다 (잠김 표시)")
    time.sleep(1)
    t.shot("잠금")
    t.click(config.SCREEN[0] // 2, config.SCREEN[1] // 2)  # 암호 칸을 띄운다
    time.sleep(1)
    t.type("miku1234")
    t.key("ret")
    t.expect(t.wait(lambda: not t.sh(f"test -e {mark}").ok, 10), "암호로 풀렸다")


@test("바탕 화면 보기(Win+D) — 창이 모두 내려가고, 다시 누르면 그대로 돌아온다", suite="session", quick=True)
def show_desktop(t):
    t.kill("sekai-notepad")
    t.close("org.sekaios.Calculator")
    t.after(lambda: (t.kill("sekai-notepad"), t.close("org.sekaios.Calculator")))
    t.expect(t.launch("sekai-notepad", "org.sekaios.Notepad"), "메모장")
    t.expect(t.launch("sekai-calc", "org.sekaios.Calculator"), "계산기")
    t.wait(lambda: active(t) == "org.sekaios.Calculator", 5)

    def mins():
        return {c["class"]: bool(c.get("sekaiMinimized")) for c in t.clients()
                if c["class"] in ("org.sekaios.Notepad", "org.sekaios.Calculator")}
    t.key("meta_l-d")
    t.expect(t.wait(lambda: mins() and all(mins().values()), 3), f"Win+D → 모두 최소화 ({mins()})")
    t.shot("바탕화면")
    t.key("meta_l-d")
    t.expect(t.wait(lambda: mins() and not any(mins().values()), 3), f"Win+D 다시 → 모두 돌아왔다 ({mins()})")
    t.expect(t.wait(lambda: active(t) == "org.sekaios.Calculator", 3), f"초점도 그대로 계산기 ({active(t)})")
