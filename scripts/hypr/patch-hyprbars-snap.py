#!/usr/bin/env python3
"""hyprbars 패치 — 윈도우 11 식 "끌어서 스냅"을 위해 제목줄 끌기를 셸에 알린다.

셸(sekai-panel)은 마우스 끌기를 볼 수 없다. 제목줄 끌기는 hyprbars 가 하므로 여기서 알린다.
Hyprland IPC 이벤트(socket2)로:
  sekaisnapstart>>주소            끌기 시작 (스냅된 창이면 셸이 원래 크기로 되돌린다)
  sekaisnap>>영역,모니터           끄는 중 커서가 닿은 영역이 바뀔 때 (미리보기)
  sekaisnapdrop>>영역,모니터,주소   놓았을 때 (셸이 그 영역으로 배치)
영역: left right tl tr bl br max none
  화면 왼쪽·오른쪽 끝 = 반쪽, 네 모서리 = 4분의 1, 위쪽 끝 = 최대화 (윈도우 11 과 같다)
그리고 스냅 레이아웃(최대화 버튼에 마우스를 올리면 배치 그림)을 위해:
  sekaimaxhover>>on,주소,x,y       최대화 버튼에 올림 (x,y = 버튼 아래 가운데, 전체 화면 논리 좌표)
  sekaimaxhover>>off,주소          벗어남
멱등 — 두 부분을 따로 표시한다(SEKAI_SNAP, SEKAI_SNAP_LAYOUT).
patch-hyprbars-hover.py 다음에 적용해야 한다 (그 패치의 m_iSekaiHover 를 쓴다).
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK, MARK2, MARK3 = "SEKAI_SNAP", "SEKAI_SNAP_LAYOUT", "SEKAI_SNAP_DROP"
if MARK3 in s:
    print("이미 적용됨"); sys.exit(0)


def patch(s):
    """1~4: 끌어서 스냅"""

    # 1. 도움 함수
    old = '#include "globals.hpp"\n'
    assert old in s, "include 기준점 없음"
    s = s.replace(old, old + '''#include <hyprland/src/managers/EventManager.hpp>

// ── SEKAI_SNAP: 끌어서 스냅 ─────────────────────────────────────
static std::string sekaiZone = "none";

static std::string sekaiZoneAt(const Vector2D& p, PHLMONITOR& mon) {
    mon = g_pCompositor->getMonitorFromVector(p);
    if (!mon)
        return "none";
    const double x = p.x - mon->m_position.x, y = p.y - mon->m_position.y;
    const double W = mon->m_size.x, H = mon->m_size.y;
    const double E = 4;                                  // 가장자리로 치는 두께
    const double C = std::max(48.0, std::min(W, H) / 8); // 모서리로 치는 길이
    const bool   L = x <= E, R = x >= W - 1 - E, T = y <= E, B = y >= H - 1 - E;
    if ((L && y < C) || (T && x < C))
        return "tl";
    if ((R && y < C) || (T && x > W - C))
        return "tr";
    if ((L && y > H - C) || (B && x < C))
        return "bl";
    if ((R && y > H - C) || (B && x > W - C))
        return "br";
    if (L)
        return "left";
    if (R)
        return "right";
    if (T)
        return "max";
    return "none";
}

static void sekaiSnapTrack() {
    PHLMONITOR mon;
    const auto z = sekaiZoneAt(g_pInputManager->getMouseCoordsInternal(), mon);
    if (z == sekaiZone)
        return;
    sekaiZone = z;
    g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnap", std::format("{},{}", z, mon ? mon->m_name : "")});
}

static void sekaiSnapDrop(PHLWINDOW w) {
    PHLMONITOR mon;
    const auto z = sekaiZoneAt(g_pInputManager->getMouseCoordsInternal(), mon);
    sekaiZone    = "none";
    if (!w)
        return;
    g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", std::format("{},{},{:x}", z, mon ? mon->m_name : "", (uintptr_t)w.get())});
}
''', 1)

    # 2. 끄는 중 — 커서가 닿은 영역 추적
    old = '''    if (!m_bDragPending || m_bTouchEv || !validMapped(m_pWindow))
        return;

    m_bDragPending = false;
    handleMovement();'''
    assert old in s, "onMouseMove 기준점 없음"
    s = s.replace(old, '''    if (m_bDraggingThis && !m_bTouchEv) // SEKAI_SNAP
        sekaiSnapTrack();

    if (!m_bDragPending || m_bTouchEv || !validMapped(m_pWindow))
        return;

    m_bDragPending = false;
    handleMovement();''', 1)

    # 3. 놓음
    old = '''    if (m_bDraggingThis) {
        g_pKeybindManager->m_dispatchers["mouse"]("0movewindow");
        m_bDraggingThis = false;

        Debug::log(LOG, "[hyprbars] Dragging ended on {:x}", (uintptr_t)m_pWindow.lock().get());
    }'''
    assert old in s, "handleUpEvent 기준점 없음"
    s = s.replace(old, '''    if (m_bDraggingThis) {
        g_pKeybindManager->m_dispatchers["mouse"]("0movewindow");
        m_bDraggingThis = false;
        sekaiSnapDrop(m_pWindow.lock()); // SEKAI_SNAP

        Debug::log(LOG, "[hyprbars] Dragging ended on {:x}", (uintptr_t)m_pWindow.lock().get());
    }''', 1)

    # 4. 끌기 시작
    old = '''    g_pKeybindManager->m_dispatchers["mouse"]("1movewindow");
    m_bDraggingThis = true;'''
    assert old in s, "handleMovement 기준점 없음"
    s = s.replace(old, '''    g_pKeybindManager->m_dispatchers["mouse"]("1movewindow");
    m_bDraggingThis = true;
    sekaiZone       = "none"; // SEKAI_SNAP
    g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapstart", std::format("{:x}", (uintptr_t)m_pWindow.lock().get())});''', 1)
    return s


if MARK not in s:
    s = patch(s)

def patch_layout(s):
    """5. 최대화 버튼 올림/벗어남 (스냅 레이아웃) — SEKAI_SNAP_LAYOUT"""
    old = '''        if (hover != WAS) { // SEKAI_BUTTON_HOVER
            if (IDX < 32)
                m_iSekaiHover ^= (1u << IDX);'''
    assert old in s, "hover 패치 기준점 없음 (patch-hyprbars-hover.py 먼저)"
    return s.replace(old, old + '''
            if (b.icon == "sekai:max" && !m_bDraggingThis) { // SEKAI_SNAP_LAYOUT 스냅 레이아웃
                const auto BOX = assignedBoxGlobal();
                const auto PW  = m_pWindow.lock();
                if (hover)
                    g_pEventManager->postEvent(SHyprIPCEvent{"sekaimaxhover", std::format("on,{:x},{},{}", (uintptr_t)PW.get(),
                                                                                           (int)(BOX.x + currentPos.x + b.size / 2.0), (int)(BOX.y + **PHEIGHT))});
                else
                    g_pEventManager->postEvent(SHyprIPCEvent{"sekaimaxhover", std::format("off,{:x}", (uintptr_t)PW.get())});
            }''', 1)


def patch_drop(s):
    """6. 끄는 중에 손을 떼면 커서 밑에 무엇이 있든 끌기를 끝낸다 — SEKAI_SNAP_DROP
    업스트림은 커서가 TOP/OVERLAY 층(스냅 미리보기·레이아웃 바) 위면 입력을 통째로 무시해서
    놓음이 처리되지 않았다."""
    old = '''void CHyprBar::onMouseButton(SCallbackInfo& info, IPointer::SButtonEvent e) {
    if (!inputIsValid())
        return;
'''
    assert old in s, "onMouseButton 기준점 없음"
    return s.replace(old, '''void CHyprBar::onMouseButton(SCallbackInfo& info, IPointer::SButtonEvent e) {
    if (e.state != WL_POINTER_BUTTON_STATE_PRESSED && m_bDraggingThis) { // SEKAI_SNAP_DROP
        handleUpEvent(info);
        return;
    }

    if (!inputIsValid())
        return;
''', 1)


if MARK2 not in s:
    s = patch_layout(s)
s = patch_drop(s)

open(path, "w", encoding="utf-8").write(s)
print("적용함")
