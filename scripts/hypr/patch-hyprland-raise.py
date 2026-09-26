#!/usr/bin/env python3
"""Hyprland 패치 — 윈도우처럼 창을 고르면 맨 앞으로, 최대화한 창도 보통 창과 같은 쌓임 순서 (SEKAI_RAISE).

Hyprland 0.50.1 은 두 가지가 윈도우와 달랐다:
  1. focuswindow 디스패처(작업 표시줄·작업 관리자·스냅 도우미가 쓴다)가 초점만 주고 떠 있는 창을 올리지 않았다 —
     작업 표시줄에서 뒤에 있던 창을 눌러도 앞으로 나오지 않았다.
  2. 최대화(전체 화면 모드 1)한 창은 따로 취급해서, 그 위에 떠 있던 창("created over fullscreen")이 계속 위에
     남았다 — 최대화한 크롬을 눌러도(작업 표시줄·창 클릭·Alt+Tab 모두) 메모장이 크롬 위에 그대로 떠 있었다.
고친다:
  - Compositor.cpp changeWindowZOrder(맨 위로): 올린 창이 최대화·전체 화면이면 같은 데스크톱의 다른 창을 그 아래로
    (고정한 창은 그대로 위), 그 창의 대화상자(부모가 이 창인 창)는 함께 위로 — 윈도우에서 대화상자가 주인 창 위에
    남는 것처럼. 떠 있는 창을 올리면 최대화한 창 위에 다시 보이게(흐림 값) 맞춘다.
    클릭·alterzorder·hyprbars 제목줄 누르기가 모두 이 함수를 거친다.
  - KeybindManager.cpp focusWindow 디스패처: 초점을 준 뒤 떠 있는(또는 최대화한) 창을 맨 위로.
헤더는 그대로다 (플러그인 ABI). 멱등.
사용법: patch-hyprland-raise.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_RAISE"
comp = root / "src/Compositor.cpp"
keyb = root / "src/managers/KeybindManager.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = comp.read_text()
if MARK not in t:
    t = sub(t, '''void CCompositor::changeWindowZOrder(PHLWINDOW pWindow, bool top) {
    if (!validMapped(pWindow))
        return;
''', '''void CCompositor::changeWindowZOrder(PHLWINDOW pWindow, bool top) {
    if (!validMapped(pWindow))
        return;

    // SEKAI_RAISE: 윈도우처럼 — 최대화한 창도 보통 창과 같은 쌓임 순서, 대화상자는 주인 창과 함께 위로
    static bool sekaiRaising = false;
    if (top && !sekaiRaising) {
        sekaiRaising = true;
        changeWindowZOrder(pWindow, true); // 창 자체 (아래의 원래 경로)

        const auto WS      = pWindow->m_workspace;
        auto       isChild = [&](PHLWINDOW w) {
            auto p = w->parent();
            for (int i = 0; p && i < 16; ++i, p = p->parent())
                if (p == pWindow)
                    return true;
            return false;
        };
        std::vector<PHLWINDOW> kids; // 지금 쌓인 순서(아래→위) 그대로
        for (auto const& w : m_windows)
            if (w != pWindow && w->m_isMapped && !w->isHidden() && w->m_workspace == WS && isChild(w))
                kids.emplace_back(w);

        if (WS && WS->m_hasFullscreenWindow && pWindow->isFullscreen()) {
            // 최대화한 창을 올렸다 — 그 위에 떠 있던 창들을 아래로 (고정한 창·대화상자는 그대로 위)
            for (auto const& w : m_windows)
                if (w != pWindow && w->m_workspace == WS && !w->isFullscreen() && !w->m_fadingOut && !w->m_pinned && std::ranges::find(kids, w) == kids.end())
                    w->m_createdOverFullscreen = false;
        }
        for (auto const& w : kids)
            changeWindowZOrder(w, true);
        if (WS && WS->m_hasFullscreenWindow)
            updateFullscreenFadeOnWorkspace(WS); // 아래로 간 창은 흐리게 숨기고, 올린 창은 보이게

        sekaiRaising = false;
        return;
    }
''', "changeWindowZOrder 앞")
    comp.write_text(t)
    print("    적용: Compositor.cpp 최대화한 창도 맨 위로")
else:
    print("    (Compositor.cpp 이미 적용됨)")

t = keyb.read_text()
if MARK not in t:
    t = sub(t, '''    } else
        g_pCompositor->focusWindow(PWINDOW);

    PWINDOW->warpCursor();

    return {};
}''', '''    } else
        g_pCompositor->focusWindow(PWINDOW);

    // SEKAI_RAISE: 윈도우처럼 — 창을 고르면(작업 표시줄·작업 관리자) 맨 앞으로
    if (PWINDOW->m_isFloating || PWINDOW->isFullscreen())
        g_pCompositor->changeWindowZOrder(PWINDOW, true);

    PWINDOW->warpCursor();

    return {};
}''', "focusWindow 디스패처 끝")
    keyb.write_text(t)
    print("    적용: KeybindManager.cpp focuswindow 가 창을 맨 앞으로")
else:
    print("    (KeybindManager.cpp 이미 적용됨)")
