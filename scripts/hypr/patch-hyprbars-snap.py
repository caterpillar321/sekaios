#!/usr/bin/env python3
"""hyprbars 패치 — 윈도우 11 식 "끌어서 스냅"을 위해 제목줄 끌기를 셸에 알린다.

셸(sekai-panel)은 마우스 끌기를 볼 수 없다. 제목줄 끌기는 hyprbars 가 하므로 여기서 알린다.
Hyprland IPC 이벤트(socket2)로:
  sekaisnapstart>>주소            끌기 시작 (스냅된 창이면 셸이 원래 크기로 되돌린다)
  sekaisnap>>영역,모니터           끄는 중 커서가 닿은 영역이 바뀔 때 (미리보기)
  sekaisnapdrop>>영역,모니터,주소   놓았을 때 (셸이 그 영역으로 배치)
영역: left right tl tr bl br max none
  화면 왼쪽·오른쪽 끝 = 반쪽, 네 모서리 = 4분의 1, 위쪽 끝 = 최대화 (윈도우 11 과 같다)
멱등.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK = "SEKAI_SNAP"
if MARK in s:
    print("이미 적용됨"); sys.exit(0)

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

open(path, "w", encoding="utf-8").write(s)
print("적용함")
