#!/usr/bin/env python3
"""Hyprland 패치 — 바탕화면 레이어가 창의 키보드 초점을 가로채지 않게 (SEKAI_LAYER_FOCUS).

바탕화면(sekai-desk)은 아이콘을 키보드로 다루려고 키보드를 받을 수 있는 레이어(BACKGROUND, on-demand)다.
시작 메뉴가 닫히면 Hyprland 가 커서 밑의 그 레이어에 키보드 초점을 주는데,
  ① 그 뒤에 뜬 새 창은 초점을 못 받았다 — "초점이 레이어에 있으면 새 창은 초점을 받지 않는다"는 규칙이
     메뉴·런처(위쪽 레이어)뿐 아니라 바탕화면에도 걸렸다. (시작 메뉴로 연 터미널이 초점 없이 떴다)
  ② 창을 눌러도 초점이 돌아오지 않았다 — 누른 창이 "마지막 창"과 같으면 초점을 옮기지 않는데,
     키보드는 실제로 레이어에 가 있었다. (터미널 커서가 빈 네모가 되고 입력이 안 됐다)
고친다: ① 새 창을 막는 것은 TOP·OVERLAY 레이어만. ② 누른 창이 마지막 창이어도 키보드가 레이어에 있으면
다시 초점. 헤더는 그대로. 멱등.
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
