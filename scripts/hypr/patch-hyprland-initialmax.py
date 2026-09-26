#!/usr/bin/env python3
"""Hyprland 패치 — 창이 나타나기 전에 청한 최대화를 지킨다 (SEKAI_INITIAL_MAXIMIZE).

Hyprland 0.50.1 은 창이 나타나기(map) 전에 온 xdg_toplevel.set_maximized 를 기록하지 않고 버린다
(전체 화면 요청은 m_wantsInitialFullscreen 으로 기억하면서 최대화는 빠져 있다 — Window.cpp onUpdateState).
그래서 크롬·크로미움을 최대화한 채 닫으면 다음에 열 때 크롬은 최대화를 청하고 "복원" 단추를 보이는데
창은 최대화되지 않은 채 떠 있었다 — 단추를 눌러도 어긋난 상태로 움직여 "최대화가 제대로 안 된다".
고친다: 창이 나타날 때(events/Windows.cpp mapWindow) 그 창의 요청 상태(requestsMaximize — 이미 있는 값)를
보고 최대화로 연다. 헤더(클래스 구조)는 건드리지 않는다 — 플러그인과 ABI 가 그대로. 멱등.
사용법: patch-hyprland-initialmax.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_INITIAL_MAXIMIZE"
wp = root / "src/events/Windows.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = wp.read_text()
if MARK not in t:
    t = sub(t, '''    if (PWINDOW->m_wantsInitialFullscreen || (PWINDOW->m_isX11 && PWINDOW->m_xwaylandSurface->m_fullscreen))
        requestedClientFSMode = FSMODE_FULLSCREEN;
''', '''    if (PWINDOW->m_wantsInitialFullscreen || (PWINDOW->m_isX11 && PWINDOW->m_xwaylandSurface->m_fullscreen))
        requestedClientFSMode = FSMODE_FULLSCREEN;
    // SEKAI_INITIAL_MAXIMIZE: 나타나기 전에 최대화를 청한 창 (크롬을 최대화한 채 닫았으면 다음에 그렇게 연다) —
    //   onUpdateState 는 나타나기 전의 최대화 요청을 기억하지 않는다. 요청 상태를 여기서 직접 본다
    if (!requestedClientFSMode.has_value()) {
        const bool WANTSMAX = PWINDOW->m_isX11 ? (PWINDOW->m_xwaylandSurface && PWINDOW->m_xwaylandSurface->m_state.requestsMaximize.value_or(false)) :
                                                 (PWINDOW->m_xdgSurface && PWINDOW->m_xdgSurface->m_toplevel && PWINDOW->m_xdgSurface->m_toplevel->m_state.requestsMaximize.value_or(false));
        if (WANTSMAX)
            requestedClientFSMode = FSMODE_MAXIMIZED;
    }
''', "처음 전체 화면 요청")
    wp.write_text(t)
    print("    적용: Windows.cpp 나타나기 전의 최대화 요청")
else:
    print("    (Windows.cpp 이미 적용됨)")
