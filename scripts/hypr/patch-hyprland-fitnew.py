#!/usr/bin/env python3
"""Hyprland 패치 — 새로 뜬 창을 작업 영역 안으로 (SEKAI_FIT_NEW).

앱이 기억한 크기가 화면보다 크면(GTK 파일 고르기 창은 1150×900 을 기억한다) 창이 부모 가운데에 그대로 떠서
작은 화면(1280×800)에선 제목줄이 화면 위로 나가 옮기지도 닫지도 못했다. 윈도우는 새 창을 작업 영역에 맞춘다
— 떠 있는 새 창이 작업 영역(작업 표시줄 뺀 곳)보다 크면 줄이고, 제목줄이 영역 밖이면 들인다(제목줄이 먼저).
제목줄(hyprbars)이 붙은 뒤(openWindow 훅 다음)라 그 몫까지 넣어 잰다. 앱이 정한 최소 크기보다는 줄이지 않는다.
전체 화면·최대화로 뜬 창, X11 덮개 창(메뉴 등), 테두리를 원하지 않는 X11 창(게임의 테두리 없는 창)은 건드리지 않는다.
헤더는 그대로 (플러그인 ABI). 멱등.
사용법: patch-hyprland-fitnew.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_FIT_NEW"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


p = root / "src/events/Windows.cpp"
t = p.read_text()
if MARK in t:
    print("    (이미 적용됨)")
    sys.exit(0)

t = sub(t, '''static void setVector2DAnimToMove(WP<CBaseAnimatedVariable> pav) {''', '''// SEKAI_FIT_NEW: 새로 뜬 떠 있는 창을 작업 영역 안으로 — 크면 줄이고(가운데는 그대로), 제목줄이 먼저 보이게
static void sekaiFitNewWindow(PHLWINDOW w) {
    const auto M = w->m_monitor.lock();
    if (!M)
        return;
    const auto     EXT  = w->getFullWindowReservedArea(); // 제목줄 등 장식 몫
    const double   WX   = M->m_position.x + M->m_reservedTopLeft.x, WY = M->m_position.y + M->m_reservedTopLeft.y;
    const double   WW   = M->m_size.x - M->m_reservedTopLeft.x - M->m_reservedBottomRight.x;
    const double   WH   = M->m_size.y - M->m_reservedTopLeft.y - M->m_reservedBottomRight.y;
    const Vector2D SIZE = w->m_realSize->goal(), POS = w->m_realPosition->goal();
    if (POS == M->m_position && SIZE == M->m_size) // 모니터를 꽉 채운 창 (테두리 없는 전체 화면 게임 등)
        return;
    const auto     MINS = w->requestedMinSize();
    const Vector2D NS   = {std::min(SIZE.x, std::max(WW - EXT.topLeft.x - EXT.bottomRight.x, MINS.x)),
                           std::min(SIZE.y, std::max(WH - EXT.topLeft.y - EXT.bottomRight.y, MINS.y))};
    Vector2D       NP   = POS + (SIZE - NS) / 2.0;
    NP.x                = std::max(std::min(NP.x, WX + WW - EXT.bottomRight.x - NS.x), WX + EXT.topLeft.x);
    NP.y                = std::max(std::min(NP.y, WY + WH - EXT.bottomRight.y - NS.y), WY + EXT.topLeft.y);
    if (NS == SIZE && NP == POS)
        return;
    Debug::log(LOG, "SEKAI_FIT_NEW: {} {} {} -> {} {}", w, POS, SIZE, NP, NS);
    w->m_realSize->setValueAndWarp(NS);
    w->m_realPosition->setValueAndWarp(NP);
    w->sendWindowSize(true);
}

static void setVector2DAnimToMove(WP<CBaseAnimatedVariable> pav) {''', "도움 함수")

t = sub(t, '''    // apply data from default decos. Borders, shadows.
    g_pDecorationPositioner->forceRecalcFor(PWINDOW);
    PWINDOW->updateWindowDecos();
    g_pLayoutManager->getCurrentLayout()->recalculateWindow(PWINDOW);
''', '''    // apply data from default decos. Borders, shadows.
    g_pDecorationPositioner->forceRecalcFor(PWINDOW);
    PWINDOW->updateWindowDecos();
    g_pLayoutManager->getCurrentLayout()->recalculateWindow(PWINDOW);

    // SEKAI_FIT_NEW: 떠 있는 새 창을 작업 영역 안으로 (제목줄이 붙은 뒤 — 그 몫까지)
    if (PWINDOW->m_isFloating && !PWINDOW->isFullscreen() && !PWINDOW->isX11OverrideRedirect() && !PWINDOW->m_X11DoesntWantBorders)
        sekaiFitNewWindow(PWINDOW);
''', "맵 끝")
p.write_text(t)
print("    적용: Windows.cpp 새 창을 작업 영역 안으로")
