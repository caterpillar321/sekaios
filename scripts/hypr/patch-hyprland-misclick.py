#!/usr/bin/env python3
"""Hyprland 패치 — 오조작·잘못 누름에서 나오던 것들 (SEKAI_MISCLICK).

1. 바탕화면을 누르면 어느 창도 활성이 아니게 (InputManager.cpp, SEKAI_LAYER_HOVER 자리)
   키보드만 바탕화면 레이어로 옮기고 "마지막 창"은 그대로 두어서, 그 뒤 작업 표시줄 단추를 누르면 창이
   앞으로 오지 않고 최소화됐고(활성 창을 누른 것으로 봄), Alt+F4 는 보이지 않는 그 창을 닫았다.
   윈도우처럼 바탕화면을 누르면 활성 창을 비운다 (키보드를 받는 레이어만 — 작업 표시줄은 받지 않는다).
2. 창을 최대화하면 그 창의 대화상자도 위에 남게 (Compositor.cpp setWindowFullscreenState)
   최대화할 때 같은 데스크톱의 다른 창을 모두 아래로 내려 대화상자까지 숨었다(모달이면 앱이 멈춘 듯).
   최대화한 뒤 그 창을 한 번 더 "맨 위로" 올린다 — SEKAI_RAISE 가 대화상자를 함께 올린다.
3. X11 창의 부모(x11TransientFor)가 엉뚱한 창이던 것 (desktop/Window.cpp)
   조상을 거슬러 오르는 루프가 null 까지 가서, X11 표면이 없는 첫 창(Wayland 창)을 돌려줬다.
   맨 위 조상에서 멈춘다. (SEKAI_RAISE·대화상자 단추가 이 값을 쓴다)
4. 창이 스스로 그린 제목줄(크롬 탭 줄) 끌기 (SEKAI_CLIENT_MOVE 보강)
   - 놓기 처리를 취소될 수 있는 훅보다 먼저 — 작업 보기(hyprexpo)가 열린 동안 놓으면 놓기가 사라져
     버튼 없이 창이 커서를 따라다녔다
   - 끌기를 시작하기 전에 커서 밑 창이 요청한 창인지 본다 (다른 창을 잡았다 되돌리며 초점·올림이 남았다)
5. 놓기를 놓친 끌기 (InputManager.cpp onMouseButton)
   끄는 중 마우스 연결이 끊기는 등으로 놓기가 오지 않으면 끌기 상태가 남아, Hyprland 가 그 뒤의 끌기를 모두
   조용히 거절했다(다시 로그인할 때까지 창을 옮길 수 없음). 버튼이 하나도 안 눌린 채 새로 누르면 남은 끌기를 끝낸다.
헤더는 그대로 (플러그인 ABI). 멱등.
사용법: patch-hyprland-misclick.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_MISCLICK"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


# ── 1·4 InputManager.cpp ──
inp = root / "src/managers/input/InputManager.cpp"
t = inp.read_text()
if MARK not in t:
    t = sub(t, '''                if (PLS && PLS->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE && g_pSeatManager->m_state.keyboardFocus != PSURF)
                    g_pCompositor->focusSurface(PSURF);
''', '''                if (PLS && PLS->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE) {
                    // SEKAI_MISCLICK: 바탕화면을 누르면 어느 창도 활성이 아니다 (윈도우처럼) — 안 그러면 작업 표시줄
                    //   단추가 그 창을 최소화하고, Alt+F4 가 보이지 않는 그 창을 닫았다
                    if (!g_pCompositor->m_lastWindow.expired())
                        g_pCompositor->focusWindow(nullptr);
                    if (g_pSeatManager->m_state.keyboardFocus != PSURF)
                        g_pCompositor->focusSurface(PSURF);
                }
''', "바탕화면 누름")
    # 놓기 처리를 훅보다 먼저
    old_release = '''    if (sekaiMoving && e.state != WL_POINTER_BUTTON_STATE_PRESSED) { // SEKAI_CLIENT_MOVE — 놓음
        sekaiMoving = false;
        g_pKeybindManager->m_dispatchers["mouse"]("0movewindow");
        PHLMONITOR mon;
        const auto z = sekaiMoveZoneAt(getMouseCoordsInternal(), mon);
        if (const auto w = sekaiMoveWin.lock())
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", std::format("{},{},{:x}", z, mon ? mon->m_name : "", (uintptr_t)w.get())});
        else // SEKAI_CLIENT_MOVE2: 끄던 창이 닫혔다 — 셸이 끌기를 끝내게
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", "none,,0"});
        sekaiMoveZone = "none";
    }
'''
    t = sub(t, '''void CInputManager::onMouseButton(IPointer::SButtonEvent e) {
    EMIT_HOOK_EVENT_CANCELLABLE("mouseButton", e);

''' + old_release, '''void CInputManager::onMouseButton(IPointer::SButtonEvent e) {
    // SEKAI_MISCLICK: 창이 그린 제목줄 끌기의 놓기는 훅보다 먼저 — 작업 보기(hyprexpo) 등이 버튼 이벤트를
    //   취소하면 놓기가 사라져 창이 버튼 없이 커서를 따라다녔다
''' + old_release + '''
    EMIT_HOOK_EVENT_CANCELLABLE("mouseButton", e);

''', "놓기를 훅보다 먼저")
    t = sub(t, '''    if (!w || sekaiMoving || !sekaiButtonHeld || !g_pInputManager->m_currentlyDraggedWindow.expired())
        return;
    g_pKeybindManager->m_dispatchers["mouse"]("1movewindow");
''', '''    if (!w || sekaiMoving || !sekaiButtonHeld || !g_pInputManager->m_currentlyDraggedWindow.expired())
        return;
    // SEKAI_MISCLICK: 커서 밑이 이 창이 아니면(빠르게 튕겨 이미 다른 창 위) 시작하지 않는다 — 다른 창을
    //   잡았다 되돌리면서 그 창의 초점·올림이 남았다
    if (!validMapped(w) || g_pCompositor->vectorToWindowUnified(g_pInputManager->getMouseCoordsInternal(),
                                                               RESERVED_EXTENTS | INPUT_EXTENTS | ALLOW_FLOATING) != w)
        return;
    g_pKeybindManager->m_dispatchers["mouse"]("1movewindow");
''', "끌기 시작 대상 확인")
    inp.write_text(t)
    print("    적용: InputManager.cpp 바탕화면 누름·창 제목줄 끌기")
else:
    print("    (InputManager.cpp 이미 적용됨)")

# ── 5 InputManager.cpp 놓기를 놓친 끌기 (따로 표시 — 1·4 가 이미 붙은 소스에도 붙게) ──
t = inp.read_text()
if "SEKAI_MISCLICK_DRAG" not in t:
    # 놓기를 놓친 끌기 — 버튼이 하나도 안 눌려 있는데 끌기가 남아 있으면 새로 누를 때 끝낸다
    t = sub(t, '''    if (e.state == WL_POINTER_BUTTON_STATE_PRESSED) {
        m_currentlyHeldButtons.push_back(e.button);
    } else {
    ''', '''    if (e.state == WL_POINTER_BUTTON_STATE_PRESSED) {
        // SEKAI_MISCLICK_DRAG: 버튼이 하나도 안 눌려 있었는데 끌기가 남아 있다 — 놓기를 놓쳤다(끄는 중 마우스 연결이
        //   끊김 등). 그대로 두면 Hyprland 가 새 끌기를 모두 조용히 거절해, 다시 로그인할 때까지 창을 옮길 수 없었다
        if (m_currentlyHeldButtons.empty() && (m_dragMode != MBIND_INVALID || !m_currentlyDraggedWindow.expired())) {
            g_pKeybindManager->changeMouseBindMode(MBIND_INVALID);
            if (sekaiMoving) {
                sekaiMoving = false;
                g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", "none,,0"});
            }
        }
        m_currentlyHeldButtons.push_back(e.button);
    } else {
    ''', "놓기를 놓친 끌기")
    inp.write_text(t)
    print("    적용: InputManager.cpp 놓기를 놓친 끌기")
else:
    print("    (InputManager.cpp 놓친 끌기 이미 적용됨)")

# ── 2 Compositor.cpp ──
comp = root / "src/Compositor.cpp"
t = comp.read_text()
if MARK not in t:
    t = sub(t, '''    // make all windows on the same workspace under the fullscreen window
    for (auto const& w : m_windows) {
        if (w->m_workspace == PWORKSPACE && !w->isFullscreen() && !w->m_fadingOut && !w->m_pinned)
            w->m_createdOverFullscreen = false;
    }

    updateFullscreenFadeOnWorkspace(PWORKSPACE);
''', '''    // make all windows on the same workspace under the fullscreen window
    for (auto const& w : m_windows) {
        if (w->m_workspace == PWORKSPACE && !w->isFullscreen() && !w->m_fadingOut && !w->m_pinned)
            w->m_createdOverFullscreen = false;
    }

    updateFullscreenFadeOnWorkspace(PWORKSPACE);

    // SEKAI_MISCLICK: 최대화·전체 화면한 창을 맨 위로 한 번 더 — SEKAI_RAISE 가 그 창의 대화상자를 함께
    //   올린다 (안 그러면 위에서 모두 내린 창에 대화상자도 들어가 숨었다)
    if (EFFECTIVE_MODE != FSMODE_NONE)
        changeWindowZOrder(PWINDOW, true);
''', "최대화 뒤 대화상자")
    comp.write_text(t)
    print("    적용: Compositor.cpp 최대화해도 대화상자는 위에")
else:
    print("    (Compositor.cpp 이미 적용됨)")

# ── 3 Window.cpp ──
win = root / "src/desktop/Window.cpp"
t = win.read_text()
if MARK not in t:
    t = sub(t, '''    auto                              s = m_xwaylandSurface->m_parent;
    std::vector<SP<CXWaylandSurface>> visited;
    while (s) {
''', '''    auto                              s = m_xwaylandSurface->m_parent;
    std::vector<SP<CXWaylandSurface>> visited;
    while (s && s->m_parent) { // SEKAI_MISCLICK: 맨 위 조상에서 멈춘다 (null 까지 가면 엉뚱한 Wayland 창을 돌려줬다)
''', "x11TransientFor")
    win.write_text(t)
    print("    적용: Window.cpp X11 부모")
else:
    print("    (Window.cpp 이미 적용됨)")
