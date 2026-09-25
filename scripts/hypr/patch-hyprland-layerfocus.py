#!/usr/bin/env python3
"""Hyprland 패치 — 바탕화면 레이어가 창의 키보드 초점을 가로채지 않게 (SEKAI_LAYER_FOCUS).

바탕화면(sekai-desk)은 아이콘을 키보드로 다루려고 키보드를 받을 수 있는 레이어(BACKGROUND, on-demand)다.
시작 메뉴가 닫히면 Hyprland 가 커서 밑의 그 레이어에 키보드 초점을 주는데,
  ① 그 뒤에 뜬 새 창은 초점을 못 받았다 — "초점이 레이어에 있으면 새 창은 초점을 받지 않는다"는 규칙이
     메뉴·런처(위쪽 레이어)뿐 아니라 바탕화면에도 걸렸다. (시작 메뉴로 연 터미널이 초점 없이 떴다)
  ② 창을 눌러도 초점이 돌아오지 않았다 — 누른 창이 "마지막 창"과 같으면 초점을 옮기지 않는데,
     키보드는 실제로 레이어에 가 있었다. (터미널 커서가 빈 네모가 되고 입력이 안 됐다)
고친다: ① 새 창을 막는 것은 TOP·OVERLAY 레이어만. ② 누른 창이 마지막 창이어도 키보드가 레이어에 있으면
다시 초점. 뒤의 절(SEKAI_LAYER_HOVER·SEKAI_LAYER_REFOCUS)은 아래쪽 레이어가 올림·다시 잡기로 키보드를
가져가지 않게. 헤더는 그대로. 멱등.
사용법: patch-hyprland-layerfocus.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_LAYER_FOCUS"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


win = root / "src/events/Windows.cpp"
t = win.read_text()
if MARK not in t:
    t = sub(t, '''    if (PLSFROMFOCUS && PLSFROMFOCUS->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE)
        PWINDOW->m_noInitialFocus = true;''', '''    // SEKAI_LAYER_FOCUS: 메뉴·런처(위쪽 레이어)만 막는다 — 바탕화면(아래쪽 레이어)이 초점을 갖고 있어도 새 창은 초점을 받는다
    if (PLSFROMFOCUS && PLSFROMFOCUS->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE &&
        PLSFROMFOCUS->m_layer >= ZWLR_LAYER_SHELL_V1_LAYER_TOP)
        PWINDOW->m_noInitialFocus = true;''', "새 창 초점")
    win.write_text(t)
    print("    적용: Windows.cpp 새 창 초점")
else:
    print("    (Windows.cpp 이미 적용됨)")

inp = root / "src/managers/input/InputManager.cpp"
t = inp.read_text()
if MARK not in t:
    t = sub(t, '''            if ((g_pSeatManager->m_mouse.expired() || !isConstrained()) /* No constraints */
                && (w && g_pCompositor->m_lastWindow.lock() != w) /* window should change */) {''', '''            // SEKAI_LAYER_FOCUS: 누른 창이 마지막 창이어도 키보드가 레이어(바탕화면 등)에 가 있으면 다시 초점
            const bool SEKAIKBONLAYER = w && g_pCompositor->getLayerSurfaceFromSurface(g_pSeatManager->m_state.keyboardFocus.lock());
            if ((g_pSeatManager->m_mouse.expired() || !isConstrained()) /* No constraints */
                && (w && (g_pCompositor->m_lastWindow.lock() != w || SEKAIKBONLAYER)) /* window should change */) {''', "누를 때 초점")
    inp.write_text(t)
    print("    적용: InputManager.cpp 누를 때 초점")
else:
    print("    (InputManager.cpp 초점 이미 적용됨)")

# ── SEKAI_LAYER_HOVER: 마우스를 올리기만 해도 바탕화면이 키보드를 가져가던 것 ─────────────
#   Hyprland 는 키보드를 받을 수 있는 레이어 위에 커서가 오면 follow_mouse 와 상관없이 그 레이어에
#   키보드 초점을 준다. 터미널에 치다가 커서를 창 밖(바탕화면)으로 옮기거나 창 크기를 바꾸면(커서가
#   창 밖으로 나간다) 바탕화면이 키보드를 가져가, 터미널 커서가 빈 네모가 되고 입력이 씹혔다.
#   클릭해야 초점 이동(follow_mouse = 2)일 땐 아래쪽 레이어(BACKGROUND·BOTTOM)는 올림으로 초점을
#   가져가지 않고, 누를 때 준다 (바탕화면 아이콘 이름 바꾸기·Delete 는 누른 뒤에 된다).
t = inp.read_text()
if "SEKAI_LAYER_HOVER" not in t:
    t = sub(t, '''        if (pFoundLayerSurface && (pFoundLayerSurface->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE) && FOLLOWMOUSE != 3 &&
            (allowKeyboardRefocus || pFoundLayerSurface->m_layerSurface->m_current.interactivity == ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_EXCLUSIVE)) {''',
            '''        if (pFoundLayerSurface && (pFoundLayerSurface->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE) && FOLLOWMOUSE != 3 &&
            !(FOLLOWMOUSE == 2 && !refocus && pFoundLayerSurface->m_layer <= ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM) && // SEKAI_LAYER_HOVER
            (allowKeyboardRefocus || pFoundLayerSurface->m_layerSurface->m_current.interactivity == ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_EXCLUSIVE)) {''',
            "레이어 올림 초점")
    t = sub(t, '''            // SEKAI_LAYER_FOCUS: 누른 창이 마지막 창이어도 키보드가 레이어(바탕화면 등)에 가 있으면 다시 초점''',
            '''            // SEKAI_LAYER_HOVER: 창이 아닌 곳(바탕화면 레이어)을 누르면 그 레이어에 키보드 초점 — 올림으로는 안 준다
            if (!w && *PFOLLOWMOUSE == 2) {
                const auto PSURF = g_pSeatManager->m_state.pointerFocus.lock();
                const auto PLS   = PSURF ? g_pCompositor->getLayerSurfaceFromSurface(PSURF) : nullptr;
                if (PLS && PLS->m_layerSurface->m_current.interactivity != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE && g_pSeatManager->m_state.keyboardFocus != PSURF)
                    g_pCompositor->focusSurface(PSURF);
            }

            // SEKAI_LAYER_FOCUS: 누른 창이 마지막 창이어도 키보드가 레이어(바탕화면 등)에 가 있으면 다시 초점''',
            "누를 때 레이어 초점")
    inp.write_text(t)
    print("    적용: InputManager.cpp 레이어는 올림 대신 누를 때 초점")
else:
    print("    (InputManager.cpp 레이어 올림 이미 적용됨)")

# ── SEKAI_LAYER_REFOCUS: 팝업·메뉴가 닫힐 때(다시 초점 잡기) 바탕화면이 키보드를 가져가던 것 ────────
#   창 가장자리에서 연 우클릭 메뉴·콤보 목록이 창 밖(바탕화면 위)으로 넘친 채 닫히면 Hyprland 가
#   "커서 밑"을 다시 초점 잡는데(refocus), 그게 바탕화면 레이어라 키보드가 창에서 바탕화면으로 갔다.
#   클릭해야 초점 이동(follow_mouse = 2)이면 아래쪽 레이어는 다시 잡기 때도 초점을 가져가지 않는다 —
#   다만 보이는 작업 공간에 마지막 창이 없을 때(창을 다 닫았을 때)는 바탕화면이 받는다 (윈도우와 같게).
t = inp.read_text()
if "SEKAI_LAYER_REFOCUS" not in t:
    t = sub(t, '''            !(FOLLOWMOUSE == 2 && !refocus && pFoundLayerSurface->m_layer <= ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM) && // SEKAI_LAYER_HOVER''',
            '''            !(FOLLOWMOUSE == 2 && pFoundLayerSurface->m_layer <= ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM && (!refocus || sekaiLastWindowShown())) && // SEKAI_LAYER_HOVER''',
            "레이어 다시 잡기 초점")
    t = sub(t, '''CInputManager::CInputManager() {''',
            '''// SEKAI_LAYER_REFOCUS: 보이는 작업 공간에 마지막으로 초점을 가졌던 창이 있나
static bool sekaiLastWindowShown() {
    const auto W = g_pCompositor->m_lastWindow.lock();
    return W && W->m_isMapped && W->m_workspace && W->m_workspace->isVisibleNotCovered();
}

CInputManager::CInputManager() {''', "도우미 함수")
    inp.write_text(t)
    print("    적용: InputManager.cpp 다시 잡기 때도 아래쪽 레이어는 창에서 키보드를 가져가지 않음")
else:
    print("    (InputManager.cpp 레이어 다시 잡기 이미 적용됨)")
