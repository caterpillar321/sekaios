"""세션·셸 — 작업 표시줄, 시작 메뉴, 작업 표시줄 검색, 빠른 설정, 알림, Alt+Tab, 가상 데스크톱, 잠금."""
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


def search_open(t):
    """작업 표시줄 검색 창이 떠 있나 — 갈래 단추(전체)는 검색 창에만 있다"""
    return t.ui.find(app=PANEL, role="radio button", name="전체")


def search_mode(t, mode):
    t.sh(f"python3 -c \"from sekaisettings.store import Store; Store().set('panel', 'search', '{mode}')\"")


@test("작업 표시줄 검색(Win+S) — 홈 → 검색·미리보기 → Enter 로 열기", suite="session", quick=True)
def taskbar_search(t):
    t.kill("sekai-notepad")
    t.after(lambda: (t.kill("sekai-notepad"), search_open(t) and t.key("esc")))
    t.key("meta_l-s")
    t.expect(t.ui.wait(app=PANEL, role="radio button", name="전체", timeout=5), "Win+S 로 검색 창 (갈래 단추)")
    t.expect(t.ui.find(app=PANEL, role="label", name="자주 쓰는 앱"), "홈: 자주 쓰는 앱")
    t.expect(t.ui.find(app=PANEL, role="label", name="빠른 검색") and t.ui.find(app=PANEL, role="label", name="디스플레이"),
             "홈: 빠른 검색 (디스플레이 …)")
    t.expect(t.ui.find(app=PANEL, role="label", name="검색할 내용을 입력하세요"),
             "초점이 있어도 안내 글자가 보인다 (GTK3 는 초점이 오면 숨긴다)")
    t.shot("검색창홈")
    t.type("notepad")
    t.expect(t.ui.wait(app=PANEL, role="button", name_re="^작업 표시줄(에 고정|에서 고정 해제)$", timeout=5),
             "오른쪽 미리보기에 메모장의 할 일 (작업 표시줄 고정 …)")
    t.expect(not t.ui.find(app=PANEL, role="label", name="검색할 내용을 입력하세요"), "치면 안내 글자는 사라진다")
    t.shot("검색창결과")
    t.key("ret")
    t.expect(t.window("org.sekaios.Notepad"), "Enter 로 첫 결과(메모장)가 열렸다")
    t.expect(t.wait(lambda: not search_open(t), 3), "검색 창은 닫혔다")


@test("검색 상자를 눌러 열고 갈래(설정·앱)로 거르기, Esc 로 닫기", suite="session")
def taskbar_search_filter(t):
    t.after(lambda: search_open(t) and t.key("esc"))
    t.expect(t.ui.click(app=PANEL, role="button", name="검색", timeout=5), "작업 표시줄의 검색 상자")
    t.expect(t.ui.wait(app=PANEL, role="radio button", name="전체", timeout=5), "검색 창이 열렸다")
    t.type("terminal")
    t.expect(t.ui.wait(app=PANEL, role="label", name="기본 터미널", timeout=5)
             and t.ui.find(app=PANEL, role="label", name="터미널"), "전체: 앱(터미널)과 설정(기본 터미널)")
    t.expect(t.ui.click(app=PANEL, role="radio button", name="설정", timeout=3), "설정 갈래")
    t.expect(t.wait(lambda: not t.ui.find(app=PANEL, role="label", name="터미널"), 3)
             and t.ui.find(app=PANEL, role="label", name="기본 터미널"), "설정만 남는다")
    t.shot("갈래설정")
    t.expect(t.ui.click(app=PANEL, role="radio button", name="앱", timeout=3), "앱 갈래")
    t.expect(t.wait(lambda: not t.ui.find(app=PANEL, role="label", name="기본 터미널"), 3)
             and t.ui.find(app=PANEL, role="label", name="터미널"), "앱만 남는다")
    t.type("x")                                         # 갈래 단추를 눌러도 초점은 검색 칸에 — 계속 칠 수 있다
    t.expect(t.wait(lambda: t.ui.text(app=PANEL, role="text", name="검색") == "terminalx", 3),
             f"갈래를 바꾼 뒤에도 검색 칸에 이어서 친다 ({t.ui.text(app=PANEL, role='text', name='검색')!r})")
    t.key("esc")
    t.expect(t.wait(lambda: not search_open(t), 3), "Esc 로 닫혔다")


@test("작업 표시줄 우클릭 › 검색 — 아이콘만 · 숨기기, 숨겨도 Win+S 는 열린다", suite="session")
def taskbar_search_mode(t):
    t.after(lambda: search_mode(t, "box"))
    search_mode(t, "box")
    box = t.ui.wait(app=PANEL, role="button", name="검색", timeout=5)
    t.expect(box and box["w"] > 120, f"기본은 검색 상자 (너비 {box and box['w']})")

    def pick(label):
        """우클릭 메뉴 › 검색 › label — 키보드로 고른다. Wayland 의 팝업 메뉴는 AT-SPI 가 화면 좌표를 모른다
        (메뉴 표면 안의 좌표만 — 누를 자리를 알 수 없다). 고른 줄은 selected 상태로 확인"""
        t.click(config.SCREEN[0] // 2, config.SCREEN[1] - 24, btn="right")    # 작업 표시줄의 빈 곳
        t.expect(t.ui.wait(app=PANEL, role="menu", name="검색", timeout=5), "우클릭 메뉴에 검색 (하위 메뉴)")
        t.key("down", "right")
        t.expect(t.ui.wait(app=PANEL, role="radio menu item", name=label, timeout=3), f"검색 › {label}")
        for _ in range(4):
            if "selected" in (t.ui.find(app=PANEL, role="radio menu item", name=label) or {}).get("states", []):
                break
            t.key("down")
        t.expect("selected" in (t.ui.find(app=PANEL, role="radio menu item", name=label) or {}).get("states", []),
                 f"{label} 에 닿았다")
        t.key("ret")
    pick("검색 아이콘만")
    t.expect(t.wait(lambda: (t.ui.find(app=PANEL, role="button", name="검색") or {}).get("w", 999) < 60, 5),
             "아이콘만 — 작은 단추")
    t.shot("검색아이콘")
    pick("숨기기")
    t.expect(t.wait(lambda: not t.ui.find(app=PANEL, role="button", name="검색"), 5), "숨기기 — 작업 표시줄에서 사라졌다")
    t.key("meta_l-s")
    t.expect(t.ui.wait(app=PANEL, role="radio button", name="전체", timeout=5), "숨겨도 Win+S 로 검색 창")
    t.key("esc")
    t.expect(t.wait(lambda: not search_open(t), 3), "Esc 로 닫혔다")
    search_mode(t, "box")
    t.expect(t.ui.wait(app=PANEL, role="button", name="검색", timeout=5), "설정으로 되돌리면 상자가 다시")


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
