#!/usr/bin/env python3
"""Hyprland 패치 — 최대화한 창을 끌어 내리면 커서가 제목줄의 같은 자리에 남게 (SEKAI_DRAG_RESTORE).

Hyprland 0.50.1 은 최대화(전체 화면)한 창을 끌기 시작하면 최대화를 풀고 창 "가운데"를 커서에 맞춘다
(layout/IHyprLayout.cpp updateDragWindow). 원래 크기가 크면 제목줄이 커서보다 한참 위, 때로는 화면 밖으로
나갔다 — 최대화한 메모장을 제목줄로 끌어 내리자 제목줄이 화면 위로 나간 채 놓였다.
윈도우처럼 고친다: 가로는 커서가 제목줄에서 차지하던 비율 그대로, 세로는 창 위쪽에서 커서까지의 거리 그대로
(제목줄을 잡았으면 제목줄이 커서 밑에 온다). 아직 최대화가 풀리지 않았으면(끌기 문턱 전) 창은 그대로다.
헤더는 그대로 (플러그인 ABI). 멱등.
사용법: patch-hyprland-dragrestore.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_DRAG_RESTORE"
lay = root / "src/layout/IHyprLayout.cpp"

t = lay.read_text()
if MARK in t:
    print("    (IHyprLayout.cpp 이미 적용됨)")
    sys.exit(0)


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = sub(t, '''    const auto DRAGGINGWINDOW = g_pInputManager->m_currentlyDraggedWindow.lock();
    const bool WAS_FULLSCREEN = DRAGGINGWINDOW->isFullscreen();
''', '''    const auto DRAGGINGWINDOW = g_pInputManager->m_currentlyDraggedWindow.lock();
    const bool WAS_FULLSCREEN = DRAGGINGWINDOW->isFullscreen();
    // SEKAI_DRAG_RESTORE: 최대화를 풀기 전의 자리 — 커서가 제목줄의 어디를 잡았는지
    const CBox SEKAIMAXBOX = {DRAGGINGWINDOW->m_realPosition->goal(), DRAGGINGWINDOW->m_realSize->goal()};
''', "updateDragWindow 앞")
t = sub(t, '''    if (WAS_FULLSCREEN && DRAGGINGWINDOW->m_isFloating) {
        const auto MOUSECOORDS          = g_pInputManager->getMouseCoordsInternal();
        *DRAGGINGWINDOW->m_realPosition = MOUSECOORDS - DRAGGINGWINDOW->m_realSize->goal() / 2.f;
    }''', '''    if (WAS_FULLSCREEN && DRAGGINGWINDOW->m_isFloating) {
        const auto MOUSECOORDS = g_pInputManager->getMouseCoordsInternal();
        // SEKAI_DRAG_RESTORE: 윈도우처럼 — 가로는 잡은 비율 그대로, 세로는 창 위쪽에서 커서까지 그대로
        //   (원래는 창 가운데를 커서에 맞춰, 큰 창은 제목줄이 커서보다 한참 위·화면 밖으로 나갔다)
        const auto NEWSIZE = DRAGGINGWINDOW->m_realSize->goal();
        if (SEKAIMAXBOX.w > 0 && SEKAIMAXBOX.h > 0) {
            const double RELX = std::clamp((MOUSECOORDS.x - SEKAIMAXBOX.x) / SEKAIMAXBOX.w, 0.0, 1.0);
            double       offY = MOUSECOORDS.y - SEKAIMAXBOX.y; // 제목줄을 잡았으면 음수 (창 위쪽보다 위)
            if (offY > NEWSIZE.y)
                offY = NEWSIZE.y / 2.0; // 창 아래쪽을 잡고 끌었다 (Super+끌기) — 줄어든 창 안에 커서가 오게
            *DRAGGINGWINDOW->m_realPosition = Vector2D(MOUSECOORDS.x - RELX * NEWSIZE.x, MOUSECOORDS.y - offY).round();
        } else
            *DRAGGINGWINDOW->m_realPosition = MOUSECOORDS - NEWSIZE / 2.f;
    }''', "최대화 창 끌기 자리")
lay.write_text(t)
print("    적용: IHyprLayout.cpp 최대화 창을 끌면 커서가 제목줄 같은 자리에")
