#!/usr/bin/env python3
"""hyprbars 패치 — 제목줄 가장자리 4px 누름은 가로채지 않는다 (SEKAI_BORDER_GRAB).

제목줄은 창 위쪽에 붙은 장식이라 창의 위쪽 가장자리가 곧 제목줄 맨 위다. hyprbars 가 누름을
먼저 가로채 끌어 옮기기로 써서, 위쪽으로 크기를 늘릴 수 없었다. 맨 위·왼쪽 끝·오른쪽 끝 4px 은
넘겨서 Hyprland(patch-hyprland-bordergrab.py 의 sekaiBorderAt)가 크기 조절을 하게 한다.
SEKAI_BORDER_GRAB2: Hyprland 가 테두리 크기 조절을 하지 않는 창(최대화·전체 화면, resize_on_border 꺼짐)이면
넘기지 않는다 — 넘기면 아무도 받지 않아 제목줄 맨 위·양 끝이 끌기·더블클릭이 안 되는 띠가 됐다.
SEKAI_BORDER_EDGE: 화면 끝(모니터 끝·작업 표시줄 끝)에 붙은 변도 넘기지 않는다 — Hyprland 도 그 변은 안쪽 띠를
보지 않는다(윈도우처럼 화면 맨 위에서 잡으면 제목줄 끌기).
SEKAI_BAR_HIDDEN: 막대를 숨긴 창(nobar 규칙 — 크롬·탐색기처럼 제목줄을 스스로 그리는 창)의 누름은 받지 않는다. 숨긴 막대는
폭이 0 이라 원래 받을 일이 없었는데, EDGE2 가 화면 끝 테두리까지 넓히면서 최대화한 크롬·탐색기의 위쪽 34px(탭 줄·
창 단추)을 가로챘다 — 복원이 몇 번 눌러야 되고(두 번 누름이 제목줄 더블클릭으로) 최소화는 아예 안 먹었다. 멱등.
사용법: patch-hyprbars-bordergrab.py <barDeco.cpp>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()


def sekai_bar_hidden(s):
    """3단계 SEKAI_BAR_HIDDEN — 숨긴 막대는 누름을 받지 않는다"""
    if "SEKAI_BAR_HIDDEN" in s:
        return s
    # inputfix(뒤에 적용) 전 소스면 첫 줄 뒤, 이미 적용된 소스면 그 줄 뒤 — 어느 쪽이든 결과는 같다
    old = """    m_bCancelledDown = false; // SEKAI_BAR_INPUT: 지난 누름의 표시가 남아 이번 뗌(본문 클릭)을 삼키지 않게
"""
    if s.count(old) != 1:
        old = """void CHyprBar::handleDownEvent(SCallbackInfo& info, std::optional<ITouch::SDownEvent> touchEvent) {
    m_bTouchEv = touchEvent.has_value();
"""
    assert s.count(old) == 1, "handleDownEvent 앞 기준점 없음"
    return s.replace(old, old + """    // SEKAI_BAR_HIDDEN: 막대를 숨긴 창(nobar — 제목줄을 스스로 그리는 크롬·탐색기)의 누름은 앱 몫이다
    //   (전엔 화면 끝까지 넓힌 판정(EDGE2)이 최대화한 창의 탭 줄·창 단추를 가로챘다)
    if (m_hidden) {
        m_bDragPending = false;
        return;
    }
""", 1)

NEW2 = '''    // SEKAI_BORDER_GRAB: 제목줄 가장자리 4px 은 창 테두리 — Hyprland 가 크기 조절하게 넘긴다
    //   SEKAI_BORDER_GRAB2: Hyprland 가 테두리 크기 조절을 하는 창일 때만 (최대화·전체 화면은 아니다)
    static auto* const PSEKAIRESIZE = (Hyprlang::INT* const*)HyprlandAPI::getConfigValue(PHANDLE, "general:resize_on_border")->getDataStaticPtr();
    const auto         SEKAI_C      = cursorRelativeToBar();
    const auto         SEKAI_W      = m_pWindow.lock();
    if (**PSEKAIRESIZE && SEKAI_W && !SEKAI_W->isFullscreen() && !SEKAI_W->isX11OverrideRedirect()) {
        // SEKAI_BORDER_EDGE: 화면 끝(모니터 끝·작업 표시줄 끝)에 붙은 변은 넘기지 않는다 — 제목줄 몫
        const auto SEKAI_B = assignedBoxGlobal();
        int        SEKAI_E = 0;
        if (const auto M = SEKAI_W->m_monitor.lock()) {
            const double L = M->m_position.x + M->m_reservedTopLeft.x, T = M->m_position.y + M->m_reservedTopLeft.y;
            const double R = M->m_position.x + M->m_size.x - M->m_reservedBottomRight.x;
            SEKAI_E        = (SEKAI_B.x <= L + 1 ? 1 : 0) | (SEKAI_B.x + SEKAI_B.w >= R - 1 ? 2 : 0) | (SEKAI_B.y <= T + 1 ? 4 : 0);
        }
        if ((SEKAI_C.y < 4 && !(SEKAI_E & 4)) || (SEKAI_C.x < 4 && !(SEKAI_E & 1)) || (SEKAI_C.x > SEKAI_B.w - 4 && !(SEKAI_E & 2)))
            return;
    }
'''
OLD1 = '''    // SEKAI_BORDER_GRAB: 제목줄 가장자리 4px 은 창 테두리 — Hyprland 가 크기 조절하게 넘긴다
    const auto SEKAI_C = cursorRelativeToBar();
    if (SEKAI_C.y < 4 || SEKAI_C.x < 4 || SEKAI_C.x > assignedBoxGlobal().w - 4)
        return;
'''
OLD2 = '''    if (**PSEKAIRESIZE && SEKAI_W && !SEKAI_W->isFullscreen() && !SEKAI_W->isX11OverrideRedirect() &&
        (SEKAI_C.y < 4 || SEKAI_C.x < 4 || SEKAI_C.x > assignedBoxGlobal().w - 4))
        return;
'''
if "SEKAI_BORDER_EDGE" in s:
    pass                                          # 1단계는 됐다 — 2단계(EDGE2)만 본다
elif "SEKAI_BORDER_GRAB2" in s:                   # 예전 판(sekai12 까지)이 적용된 소스
    i = NEW2.index("    if (**PSEKAIRESIZE")
    assert s.count(OLD2) == 1, "예전 SEKAI_BORDER_GRAB2 기준점 없음"
    s = s.replace(OLD2, NEW2[i:], 1)
elif "SEKAI_BORDER_GRAB" in s:                    # 예전 판(sekai9)이 적용된 소스
    assert s.count(OLD1) == 1, "예전 SEKAI_BORDER_GRAB 기준점 없음"
    s = s.replace(OLD1, NEW2, 1)
else:
    old = '''    if (e.state != WL_POINTER_BUTTON_STATE_PRESSED) {
        handleUpEvent(info);
        return;
    }

    handleDownEvent(info, std::nullopt);
}
'''
    assert s.count(old) == 1, "onMouseButton 기준점 없음 (patch-hyprbars-snap.py 먼저)"
    s = s.replace(old, '''    if (e.state != WL_POINTER_BUTTON_STATE_PRESSED) {
        handleUpEvent(info);
        return;
    }

''' + NEW2 + '''
    handleDownEvent(info, std::nullopt);
}
''', 1)

# ── 2단계 SEKAI_BORDER_EDGE2: 화면 끝 판정을 Hyprland 와 같은 상자(테두리까지 포함한 창)로, 그 변 쪽 테두리 픽셀도 제목줄 몫 ──
#   제목줄(34px) 위엔 창 테두리(1px)가 있어, 화면 맨 위에 붙은 창의 y=0 은 제목줄이 아니라 테두리였다 — 누르면 아무 일도
#   없었다(전엔 크기 조절). 윈도우처럼 화면 끝 픽셀을 눌러도 제목줄 끌기·두 번 눌러 최대화가 되게 한다.
if "SEKAI_BORDER_EDGE2" in s:
    s = sekai_bar_hidden(s)
    open(p, "w", encoding="utf-8").write(s)
    print("적용함 (HIDDEN)" if "SEKAI_BAR_HIDDEN" in s else "이미 적용됨")
    sys.exit(0)
OLD_E = '''        const auto SEKAI_B = assignedBoxGlobal();
        int        SEKAI_E = 0;
        if (const auto M = SEKAI_W->m_monitor.lock()) {
            const double L = M->m_position.x + M->m_reservedTopLeft.x, T = M->m_position.y + M->m_reservedTopLeft.y;
            const double R = M->m_position.x + M->m_size.x - M->m_reservedBottomRight.x;
            SEKAI_E        = (SEKAI_B.x <= L + 1 ? 1 : 0) | (SEKAI_B.x + SEKAI_B.w >= R - 1 ? 2 : 0) | (SEKAI_B.y <= T + 1 ? 4 : 0);
        }
'''
assert s.count(OLD_E) == 1, "EDGE 판정 기준점 없음"
s = s.replace(OLD_E, '''        const auto SEKAI_B = assignedBoxGlobal();
        const int  SEKAI_E = sekaiBarScreenEdges(SEKAI_W); // SEKAI_BORDER_EDGE2: Hyprland 와 같은 상자로
''', 1)
OLD_H = "void CHyprBar::onMouseButton(SCallbackInfo& info, IPointer::SButtonEvent e) {\n"
assert s.count(OLD_H) == 1, "onMouseButton 기준점 없음"
s = s.replace(OLD_H, '''// SEKAI_BORDER_EDGE2: 창(제목줄·테두리 포함) 변 중 화면 끝(모니터 끝·작업 표시줄 끝)에 붙은 변 — 1 왼 · 2 오 · 4 위.
//   Hyprland 의 sekaiScreenEdges(patch-hyprland-bordergrab.py)와 같은 상자·기준
static int sekaiBarScreenEdges(PHLWINDOW w) {
    const auto M = w ? w->m_monitor.lock() : nullptr;
    if (!M)
        return 0;
    const auto   EXT = w->getFullWindowReservedArea();
    const auto   POS = w->m_realPosition->value(), SIZE = w->m_realSize->value();
    const double L = M->m_position.x + M->m_reservedTopLeft.x, T = M->m_position.y + M->m_reservedTopLeft.y;
    const double R = M->m_position.x + M->m_size.x - M->m_reservedBottomRight.x;
    return (POS.x - EXT.topLeft.x <= L + 1 ? 1 : 0) | (POS.x + SIZE.x + EXT.bottomRight.x >= R - 1 ? 2 : 0) | (POS.y - EXT.topLeft.y <= T + 1 ? 4 : 0);
}

''' + OLD_H, 1)
OLD_V = "    if (!VECINRECT(COORDS, 0, 0, assignedBoxGlobal().w, **PHEIGHT - 1)) {\n"
assert s.count(OLD_V) == 1, "handleDownEvent 영역 기준점 없음"
s = s.replace(OLD_V, '''    // SEKAI_BORDER_EDGE2: 화면 끝에 붙은 변 쪽은 제목줄 바깥 테두리 픽셀도 제목줄 몫 (화면 맨 위·끝을 눌러도 끌기)
    double SEKAI_X0 = 0, SEKAI_Y0 = 0, SEKAI_X1 = assignedBoxGlobal().w;
    if (PWINDOW && !m_bDraggingThis) {
        const int  SE  = sekaiBarScreenEdges(PWINDOW);
        const auto BB  = assignedBoxGlobal();
        const auto EXT = PWINDOW->getFullWindowReservedArea();
        const auto POS = PWINDOW->m_realPosition->value(), SIZE = PWINDOW->m_realSize->value();
        if (SE & 1)
            SEKAI_X0 = std::min(0.0, (POS.x - EXT.topLeft.x) - BB.x);
        if (SE & 2)
            SEKAI_X1 = std::max(BB.w, (POS.x + SIZE.x + EXT.bottomRight.x) - BB.x);
        if (SE & 4)
            SEKAI_Y0 = std::min(0.0, (POS.y - EXT.topLeft.y) - BB.y);
    }
    if (!VECINRECT(COORDS, SEKAI_X0, SEKAI_Y0, SEKAI_X1, **PHEIGHT - 1)) {
''', 1)
s = sekai_bar_hidden(s)
open(p, "w", encoding="utf-8").write(s)
print("적용함")
