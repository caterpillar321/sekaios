#!/usr/bin/env python3
"""Hyprland 패치 — 앱이 스스로 청한 최소화·최대화 (SEKAI_MINIMIZE).

Hyprland 에는 최소화가 없어 앱의 최소화 요청을 받아 두기만 하고 버렸다 — 크롬처럼 창 단추를 스스로 그리는 앱의
최소화 단추가 먹지 않았다. SekaiOS 의 최소화(숨김 작업 공간 special:min — 제목줄의 최소화 단추·작업 표시줄과 같은 것)로
받는다:
  - Wayland: xdg_toplevel.set_minimized (desktop/Window.cpp onUpdateState)
  - X11: WM_CHANGE_STATE(IconicState) · _NET_WM_STATE_HIDDEN 추가 (xwayland/XWM.cpp) — 전엔 _NET_WM_STATE 에서
    전체 화면만 봤다. 최대화(_NET_WM_STATE_MAXIMIZED_VERT/HORZ) 요청도 같이 받는다 (X11 앱의 자체 최대화 단추)
SEKAI_MINIMIZE_FOCUS: 초점 창을 최소화(movetoworkspacesilent)하면 그 데스크톱의 맨 위 창에 초점을 넘긴다 (윈도우처럼).
  원래는 옛 가운데 점의 창을 찾는데, 그 함수(vectorToWindowUnified)가 떠 있는 창은 커서 자리로 찾아 대개 못 찾았다 —
  초점이 숨은 창에 남아 키 입력·Alt+F4 가 보이지 않는 창에 갔다. 남은 창이 없으면 초점을 비운다.
헤더는 그대로 (플러그인 ABI). 멱등.
사용법: patch-hyprland-minimize.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_MINIMIZE"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


# ── KeybindManager.cpp: 최소화한 뒤 초점 (따로 표시 — 앞부분만 적용된 소스에도 붙게) ──
kbm = root / "src/managers/KeybindManager.cpp"
tk = kbm.read_text()
if "SEKAI_MINIMIZE_FOCUS" not in tk:
    tk = sub(tk, '''    auto       pWorkspace = g_pCompositor->getWorkspaceByID(WORKSPACEID);
    const auto OLDMIDDLE  = PWINDOW->middle();
''', '''    auto       pWorkspace = g_pCompositor->getWorkspaceByID(WORKSPACEID);
    const auto OLDMIDDLE  = PWINDOW->middle();
    const auto SEKAIOLDWS = PWINDOW->m_workspace; // SEKAI_MINIMIZE_FOCUS
''', "옛 데스크톱")
    tk = sub(tk, '''    if (PWINDOW == g_pCompositor->m_lastWindow) {
        if (const auto PATCOORDS = g_pCompositor->vectorToWindowUnified(OLDMIDDLE, RESERVED_EXTENTS | INPUT_EXTENTS | ALLOW_FLOATING, PWINDOW); PATCOORDS)
            g_pCompositor->focusWindow(PATCOORDS);
        else
            g_pInputManager->refocus();
    }

    return {};
}
''', '''    if (PWINDOW == g_pCompositor->m_lastWindow) {
        // SEKAI_MINIMIZE_FOCUS: 윈도우처럼 그 데스크톱의 맨 위 창에 초점을 넘긴다 (없으면 비운다). 원래 찾던 "옛 가운데 점의
        //   창"은 떠 있는 창을 커서 자리로 찾아 대개 못 찾았고, 초점이 숨은 창에 남았다.
        //   보이는 보통 데스크톱에서 옮길 때만 — 숨김 칸(최소화된 창들)에서 꺼낼 때 다른 최소화 창에 초점을 주면 숨김 칸이 열린다
        if (SEKAIOLDWS && !SEKAIOLDWS->m_isSpecialWorkspace && SEKAIOLDWS->isVisible()) {
            PHLWINDOW next;
            for (auto const& w : g_pCompositor->m_windows | std::views::reverse) {
                if (w != PWINDOW && w->m_isMapped && !w->isHidden() && w->m_workspace == SEKAIOLDWS && !w->isX11OverrideRedirect() &&
                    !w->m_X11ShouldntFocus && !w->m_windowData.noFocus.valueOrDefault()) {
                    next = w;
                    break;
                }
            }
            g_pCompositor->focusWindow(next);
        } else if (const auto PATCOORDS = g_pCompositor->vectorToWindowUnified(OLDMIDDLE, RESERVED_EXTENTS | INPUT_EXTENTS | ALLOW_FLOATING, PWINDOW); PATCOORDS)
            g_pCompositor->focusWindow(PATCOORDS);
        else
            g_pInputManager->refocus();
    }

    return {};
}
''', "초점 넘기기")
    if "#include <ranges>" not in tk:
        tk = "#include <ranges> // SEKAI_MINIMIZE_FOCUS\n" + tk
    kbm.write_text(tk)
    print("    적용: KeybindManager.cpp 최소화한 뒤 초점")
else:
    print("    (KeybindManager.cpp 최소화 초점 이미 적용됨)")

win = root / "src/desktop/Window.cpp"
xwm = root / "src/xwayland/XWM.cpp"
tw, tx = win.read_text(), xwm.read_text()
if MARK in tw:
    print("    (Window.cpp·XWM.cpp 이미 적용됨)")
    sys.exit(0)

tw = sub(tw, '#include "../Compositor.hpp"\n', '#include "../Compositor.hpp"\n#include "../managers/KeybindManager.hpp" // SEKAI_MINIMIZE\n',
         "include")
tw = sub(tw, '''void CWindow::onUpdateState() {
    std::optional<bool>      requestsFS = m_xdgSurface ? m_xdgSurface->m_toplevel->m_state.requestsFullscreen : m_xwaylandSurface->m_state.requestsFullscreen;
''', '''void CWindow::onUpdateState() {
    // SEKAI_MINIMIZE: 앱이 스스로 청한 최소화 (크롬·GTK 의 최소화 단추, X11 의 WM_CHANGE_STATE) — 원래는 받아 두기만 했다.
    //   SekaiOS 의 최소화(숨김 작업 공간 special:min — 제목줄의 최소화 단추와 같은 명령, 이 창 주소로)
    {
        const std::optional<bool> MN = m_xdgSurface ? m_xdgSurface->m_toplevel->m_state.requestsMinimize : m_xwaylandSurface->m_state.requestsMinimize;
        if (MN.value_or(false) && m_isMapped && m_workspace && !m_workspace->m_isSpecialWorkspace)
            g_pKeybindManager->m_dispatchers["movetoworkspacesilent"](std::format("special:min,address:0x{:x}", (uintptr_t)this));
    }

    std::optional<bool>      requestsFS = m_xdgSurface ? m_xdgSurface->m_toplevel->m_state.requestsFullscreen : m_xwaylandSurface->m_state.requestsFullscreen;
''', "onUpdateState")

tx = sub(tx, '''                if (prop == HYPRATOMS["_NET_WM_STATE_FULLSCREEN"])
                    XSURF->m_state.requestsFullscreen = updateState(action, XSURF->m_fullscreen);
            }
''', '''                if (prop == HYPRATOMS["_NET_WM_STATE_FULLSCREEN"])
                    XSURF->m_state.requestsFullscreen = updateState(action, XSURF->m_fullscreen);
                // SEKAI_MINIMIZE: 최대화·숨기기(최소화) 요청도 받는다 (전엔 버렸다)
                else if (prop == HYPRATOMS["_NET_WM_STATE_MAXIMIZED_VERT"] || prop == HYPRATOMS["_NET_WM_STATE_MAXIMIZED_HORZ"])
                    XSURF->m_state.requestsMaximize = updateState(action, XSURF->m_maximized);
                else if (prop == HYPRATOMS["_NET_WM_STATE_HIDDEN"] && updateState(action, XSURF->m_minimized))
                    XSURF->m_state.requestsMinimize = true;
            }
''', "X11 상태 요청")
tx = sub(tx, '''            XSURF->m_events.stateChanged.emit();
        }
    } else if (e->type == HYPRATOMS["_NET_ACTIVE_WINDOW"]) {''', '''            XSURF->m_events.stateChanged.emit();
            XSURF->m_state.requestsMaximize.reset(); // SEKAI_MINIMIZE: 한 번 쓰고 지운다 (XDG 쪽과 같게)
            XSURF->m_state.requestsMinimize.reset();
        }
    } else if (e->type == HYPRATOMS["WM_CHANGE_STATE"]) {
        // SEKAI_MINIMIZE: ICCCM 의 최소화 요청 (XIconifyWindow — IconicState = 3)
        if (e->format == 32 && e->data.data32[0] == 3) {
            XSURF->m_state.requestsMinimize = true;
            XSURF->m_events.stateChanged.emit();
            XSURF->m_state.requestsMinimize.reset();
        }
    } else if (e->type == HYPRATOMS["_NET_ACTIVE_WINDOW"]) {''', "WM_CHANGE_STATE")

win.write_text(tw)
xwm.write_text(tx)
print("    적용: Window.cpp·XWM.cpp 앱이 청한 최소화·최대화")
