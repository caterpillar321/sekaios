#!/usr/bin/env python3
"""Hyprland 패치 — 창이 나타나기 전에 청한 최대화를 지킨다 (SEKAI_INITIAL_MAXIMIZE).

Hyprland 0.50.1 은 창이 나타나기(map) 전에 온 xdg_toplevel.set_maximized 를 버린다: 요청을 받으면
requestsMaximize 를 켜 이벤트를 보낸 뒤 곧바로 지우고(XDGShell.cpp), 이벤트 쪽(Window.cpp onUpdateState)은
나타난 창만 처리한다 (전체 화면 요청은 m_wantsInitialFullscreen 으로 기억하면서 최대화는 빠져 있다).
그래서 크롬·크로미움을 최대화한 채 닫으면 다음에 열 때 크롬은 최대화를 청하고 "복원" 단추를 보이는데
창은 최대화되지 않은 채 떠 있었다 — 단추를 눌러도 어긋난 상태로 움직였다.

고친다: onUpdateState 에서 나타나기 전의 최대화 요청을 이 파일 안의 목록(약한 참조)에 적어 두고,
창이 나타날 때(events/Windows.cpp mapWindow) 꺼내 최대화로 연다. 클래스에 멤버를 더하지 않는다 —
헤더가 그대로라 플러그인(hyprbars·hyprexpo)과 ABI 가 같다. 멱등.
또 (SEKAI_TRUE_MAXIMIZED): Hyprland 는 창이 나타날 때 모든 앱에 "최대화됨" 상태를 붙이고 떼지 않았다
(XDGShell.cpp — 앱이 창 그림자를 그리지 않게 하려는 편법. 그 효과는 처음부터 보내는 "네 변 타일" 상태로 충분하다).
그래서 크롬은 늘 자기가 최대화됐다고 여겨 단추가 "복원"이고, 누르면 "최대화 풀기"만 청해 아무 일이 없었다.
고친다: 그 편법을 빼고, 최대화 상태가 바뀔 때마다(Compositor.cpp setWindowFullscreenState) 실제 값을 알린다.
사용법: patch-hyprland-initialmax.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_INITIAL_MAXIMIZE"
win = root / "src/desktop/Window.cpp"
evs = root / "src/events/Windows.cpp"
xdg = root / "src/protocols/XDGShell.cpp"
comp = root / "src/Compositor.cpp"
MARK2 = "SEKAI_TRUE_MAXIMIZED"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = win.read_text()
if MARK not in t:
    t = sub(t, '''void CWindow::onUpdateState() {''', '''// SEKAI_INITIAL_MAXIMIZE: 나타나기 전에 최대화를 청한 창들 (약한 참조 — 나타나기 전에 닫혀도 남지 않는다)
static std::vector<PHLWINDOWREF> sekaiWantsInitialMax;

bool sekaiTakeInitialMaximize(PHLWINDOW w) { // events/Windows.cpp mapWindow 이 부른다
    bool found = false;
    std::erase_if(sekaiWantsInitialMax, [&](const PHLWINDOWREF& r) {
        const auto L = r.lock();
        if (!L)
            return true;
        if (L == w) {
            found = true;
            return true;
        }
        return false;
    });
    return found;
}

void CWindow::onUpdateState() {''', "onUpdateState 앞")
    t = sub(t, '''    if (requestsMX.has_value() && !(m_suppressedEvents & SUPPRESS_MAXIMIZE)) {
        if (m_isMapped)
            g_pCompositor->changeWindowFullscreenModeClient(m_self.lock(), FSMODE_MAXIMIZED, requestsMX.value());
    }''', '''    if (requestsMX.has_value() && !(m_suppressedEvents & SUPPRESS_MAXIMIZE)) {
        if (m_isMapped)
            g_pCompositor->changeWindowFullscreenModeClient(m_self.lock(), FSMODE_MAXIMIZED, requestsMX.value());
        else { // SEKAI_INITIAL_MAXIMIZE: 나타날 때 쓰게 적어 둔다 (요청 값은 이 함수가 끝나면 지워진다)
            const auto SELF = m_self.lock();
            sekaiTakeInitialMaximize(SELF); // 앞선 요청은 지우고
            if (requestsMX.value())
                sekaiWantsInitialMax.emplace_back(m_self);
        }
    }''', "onUpdateState 최대화")
    win.write_text(t)
    print("    적용: Window.cpp 나타나기 전의 최대화 요청 기억")
else:
    print("    (Window.cpp 이미 적용됨)")

t = evs.read_text()
if MARK not in t:
    t = sub(t, '''#include "Events.hpp"
''', '''#include "Events.hpp"
bool sekaiTakeInitialMaximize(PHLWINDOW w); // SEKAI_INITIAL_MAXIMIZE (desktop/Window.cpp)
''', "Windows.cpp include")
    t = sub(t, '''    if (PWINDOW->m_wantsInitialFullscreen || (PWINDOW->m_isX11 && PWINDOW->m_xwaylandSurface->m_fullscreen))
        requestedClientFSMode = FSMODE_FULLSCREEN;
''', '''    if (PWINDOW->m_wantsInitialFullscreen || (PWINDOW->m_isX11 && PWINDOW->m_xwaylandSurface->m_fullscreen))
        requestedClientFSMode = FSMODE_FULLSCREEN;
    // SEKAI_INITIAL_MAXIMIZE: 나타나기 전에 최대화를 청했으면 최대화로 연다 (크롬을 최대화한 채 닫았으면 다음에도)
    if (sekaiTakeInitialMaximize(PWINDOW) && !requestedClientFSMode.has_value())
        requestedClientFSMode = FSMODE_MAXIMIZED;
''', "처음 전체 화면 요청")
    evs.write_text(t)
    print("    적용: Windows.cpp 나타날 때 최대화")
else:
    print("    (Windows.cpp 이미 적용됨)")

t = xdg.read_text()
if MARK2 not in t:
    t = sub(t, '''        if (m_surface->m_current.texture && !m_mapped) {
            // this forces apps to not draw CSD.
            if (m_toplevel)
                m_toplevel->setMaximized(true);
''', '''        if (m_surface->m_current.texture && !m_mapped) {
            // SEKAI_TRUE_MAXIMIZED: 나타날 때 모든 창에 "최대화됨"을 붙이지 않는다 — 최대화 상태는
            //   CCompositor::setWindowFullscreenState 가 실제 값으로 알린다 (그림자·둥근 모서리는 "네 변 타일"이 막는다)
''', "나타날 때 최대화 편법")
    xdg.write_text(t)
    print("    적용: XDGShell.cpp 나타날 때 최대화 상태를 붙이지 않음")
else:
    print("    (XDGShell.cpp 최대화 상태 이미 적용됨)")

t = comp.read_text()
if MARK2 not in t:
    t = sub(t, '''    PWINDOW->m_fullscreenState.client = state.client;
    g_pXWaylandManager->setWindowFullscreen(PWINDOW, state.client & FSMODE_FULLSCREEN);
''', '''    PWINDOW->m_fullscreenState.client = state.client;
    g_pXWaylandManager->setWindowFullscreen(PWINDOW, state.client & FSMODE_FULLSCREEN);
    // SEKAI_TRUE_MAXIMIZED: 앱에 알리는 최대화 상태를 실제와 같게 (크롬의 최대화·복원 단추가 맞게 움직인다)
    if (PWINDOW->m_xdgSurface && PWINDOW->m_xdgSurface->m_toplevel)
        PWINDOW->m_xdgSurface->m_toplevel->setMaximized(state.client & FSMODE_MAXIMIZED);
''', "최대화 상태 알리기")
    comp.write_text(t)
    print("    적용: Compositor.cpp 최대화 상태를 앱에 알림")
else:
    print("    (Compositor.cpp 최대화 상태 이미 적용됨)")

