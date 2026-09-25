#!/usr/bin/env python3
"""hyprbars 패치 — 제목줄을 누르면 키보드가 레이어에 가 있어도 창에 초점 (SEKAI_BAR_FOCUS).

바탕화면(sekai-desk)을 한 번 누르면 키보드가 바탕화면 레이어로 가지만, 레이어 초점은 "마지막 창"을
바꾸지 않는다. 그 뒤 터미널 제목줄을 (끌지 않고) 누르면 hyprbars 가 "이미 마지막 창"이라며 초점을
주지 않고 누름을 삼켜서, 키보드가 바탕화면에 남았다 — 터미널 커서는 빈 네모, Delete 는 선택한 아이콘을
휴지통으로. 창 본문 누름은 Hyprland 패치(patch-hyprland-layerfocus.py)가 고쳤고, 이것은 제목줄 쪽. 멱등.
사용법: patch-hyprbars-focus.py <barDeco.cpp>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SEKAI_BAR_FOCUS" in s:
    print("이미 적용됨")
    sys.exit(0)
old = '''    if (g_pCompositor->m_lastWindow.lock() != PWINDOW)
        g_pCompositor->focusWindow(PWINDOW);
'''
assert s.count(old) == 1, "handleDownEvent 초점 기준점 없음"
s = s.replace(old, '''    // SEKAI_BAR_FOCUS: 마지막 창이어도 키보드가 레이어(바탕화면 등)에 가 있으면 다시 초점
    if (g_pCompositor->m_lastWindow.lock() != PWINDOW || g_pCompositor->getLayerSurfaceFromSurface(g_pSeatManager->m_state.keyboardFocus.lock()))
        g_pCompositor->focusWindow(PWINDOW);
''', 1)
open(p, "w", encoding="utf-8").write(s)
print("적용함")
