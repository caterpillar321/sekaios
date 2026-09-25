#!/usr/bin/env python3
"""Hyprland 패치 — 창이 스스로 그린 제목줄(CSD)을 끌어서 옮기기 (xdg_toplevel.move).

Hyprland 0.50.1 은 xdg_toplevel.move 요청을 아예 처리하지 않는다. 그래서 자기 제목줄을 그리는
Chromium(탭 줄)·GNOME 앱은 끌어도 움직이지 않았다 (다른 창은 hyprbars 가 대신 끌어 준다).

요청이 오면 커서 밑 창을 끌기 시작하고("mouse 1movewindow"), 버튼을 떼면 끝낸다.
윈도우 11 식 스냅도 되게 hyprbars 패치(patch-hyprbars-snap.py)와 같은 IPC 이벤트를 보낸다:
  sekaisnapstart>>주소 / sekaisnap>>영역,모니터 / sekaisnapdrop>>영역,모니터,주소
헤더(클래스 구조)는 건드리지 않는다 — 플러그인(hyprbars·hyprexpo)과 ABI 가 그대로다.
멱등 (SEKAI_CLIENT_MOVE 표시).

사용법: patch-hyprland-clientmove.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_CLIENT_MOVE"
xdg = root / "src/protocols/XDGShell.cpp"
inp = root / "src/managers/input/InputManager.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what}"
    return text.replace(old, new, 1)


t = xdg.read_text()
if MARK not in t:
    t = sub(t, '#include "XDGShell.hpp"\n',
            '#include "XDGShell.hpp"\n'
            'void sekaiClientMoveStart(PHLWINDOW w); // SEKAI_CLIENT_MOVE (InputManager.cpp)\n',
            "XDGShell include")
    t = sub(t, '''    m_resource->setSetAppId([this](CXdgToplevel* r, const char* id) {
        m_state.appid = id;
        m_events.metadataChanged.emit();
    });
''', '''    m_resource->setSetAppId([this](CXdgToplevel* r, const char* id) {
        m_state.appid = id;
        m_events.metadataChanged.emit();
    });

    // SEKAI_CLIENT_MOVE: 창이 그린 제목줄을 끌면 옮긴다
    m_resource->setMove([this](CXdgToplevel* r, wl_resource* seat, uint32_t serial) { sekaiClientMoveStart(m_window.lock()); });
''', "setSetAppId")
    xdg.write_text(t)
    print("    적용: XDGShell.cpp move 요청")
else:
    print("    (XDGShell.cpp 이미 적용됨)")

t = inp.read_text()
if MARK not in t:
    t = sub(t, '#include "../../managers/permissions/DynamicPermissionManager.hpp"\n',
            '#include "../../managers/permissions/DynamicPermissionManager.hpp"\n' + r'''
// ── SEKAI_CLIENT_MOVE: 창이 스스로 그린 제목줄(CSD) 끌기 ─────────────────
//   XDGShell.cpp 의 move 요청에서 시작, 버튼을 떼면 끝. 스냅 이벤트는 hyprbars 패치와 같다.
static bool         sekaiMoving    = false;
static std::string  sekaiMoveZone  = "none";
static PHLWINDOWREF sekaiMoveWin;

static std::string  sekaiMoveZoneAt(const Vector2D& p, PHLMONITOR& mon) {
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

void sekaiClientMoveStart(PHLWINDOW w) {
    if (!w || sekaiMoving || !g_pInputManager->m_currentlyDraggedWindow.expired())
        return;
    g_pKeybindManager->m_dispatchers["mouse"]("1movewindow");
    if (g_pInputManager->m_currentlyDraggedWindow.lock() != w) { // 커서 밑이 다른 창이었다 — 되돌린다
        g_pKeybindManager->m_dispatchers["mouse"]("0movewindow");
        return;
    }
    sekaiMoving   = true;
    sekaiMoveWin  = w;
    sekaiMoveZone = "none";
    g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapstart", std::format("{:x}", (uintptr_t)w.get())});
}
''', "InputManager include")
    t = sub(t, '''void CInputManager::mouseMoveUnified(uint32_t time, bool refocus, bool mouse) {
    m_lastInputMouse = mouse;
''', '''void CInputManager::mouseMoveUnified(uint32_t time, bool refocus, bool mouse) {
    m_lastInputMouse = mouse;

    if (sekaiMoving) { // SEKAI_CLIENT_MOVE — 끄는 중 커서가 닿은 영역 (스냅 미리보기)
        PHLMONITOR mon;
        const auto z = sekaiMoveZoneAt(getMouseCoordsInternal(), mon);
        if (z != sekaiMoveZone) {
            sekaiMoveZone = z;
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnap", std::format("{},{}", z, mon ? mon->m_name : "")});
        }
    }
''', "mouseMoveUnified")
    t = sub(t, '''void CInputManager::onMouseButton(IPointer::SButtonEvent e) {
    EMIT_HOOK_EVENT_CANCELLABLE("mouseButton", e);
''', '''void CInputManager::onMouseButton(IPointer::SButtonEvent e) {
    EMIT_HOOK_EVENT_CANCELLABLE("mouseButton", e);

    if (sekaiMoving && e.state != WL_POINTER_BUTTON_STATE_PRESSED) { // SEKAI_CLIENT_MOVE — 놓음
        sekaiMoving = false;
        g_pKeybindManager->m_dispatchers["mouse"]("0movewindow");
        PHLMONITOR mon;
        const auto z = sekaiMoveZoneAt(getMouseCoordsInternal(), mon);
        if (const auto w = sekaiMoveWin.lock())
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", std::format("{},{},{:x}", z, mon ? mon->m_name : "", (uintptr_t)w.get())});
        sekaiMoveZone = "none";
    }
''', "onMouseButton")
    inp.write_text(t)
    print("    적용: InputManager.cpp 끌기·스냅 이벤트")
else:
    print("    (InputManager.cpp 이미 적용됨)")

# ── SEKAI_CLIENT_MOVE2 (따로 멱등) ─────────────────────────────────────────
#   ① move 요청이 버튼을 뗀 뒤에 오면(앱이 바빠 늦게 보냄) 버튼 없이 창이 커서를 따라다녔다 — 누르고 있을 때만
#   ② 끄는 중 창이 닫히면 놓음 알림이 빠져 셸이 끌기 상태에 갇혔다 — 창이 없어도 "none" 으로 알린다
t = inp.read_text()
if "SEKAI_CLIENT_MOVE2" not in t:
    t = sub(t, '''static PHLWINDOWREF sekaiMoveWin;''', '''static PHLWINDOWREF sekaiMoveWin;
static bool         sekaiButtonHeld = false; // SEKAI_CLIENT_MOVE2: 지금 마우스 버튼이 눌려 있나 (onMouseButton 이 적는다)''', "버튼 상태 변수")
    t = sub(t, '''    if (!w || sekaiMoving || !g_pInputManager->m_currentlyDraggedWindow.expired())
        return;''', '''    if (!w || sekaiMoving || !sekaiButtonHeld || !g_pInputManager->m_currentlyDraggedWindow.expired())
        return;''', "누르고 있을 때만")
    t = sub(t, '''    switch (m_clickBehavior) {
        case CLICKMODE_DEFAULT: processMouseDownNormal(e); break;''', '''    sekaiButtonHeld = !m_currentlyHeldButtons.empty(); // SEKAI_CLIENT_MOVE2

    switch (m_clickBehavior) {
        case CLICKMODE_DEFAULT: processMouseDownNormal(e); break;''', "버튼 상태 적기")
    t = sub(t, '''        if (const auto w = sekaiMoveWin.lock())
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", std::format("{},{},{:x}", z, mon ? mon->m_name : "", (uintptr_t)w.get())});
        sekaiMoveZone = "none";''', '''        if (const auto w = sekaiMoveWin.lock())
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", std::format("{},{},{:x}", z, mon ? mon->m_name : "", (uintptr_t)w.get())});
        else // SEKAI_CLIENT_MOVE2: 끄던 창이 닫혔다 — 셸이 끌기를 끝내게
            g_pEventManager->postEvent(SHyprIPCEvent{"sekaisnapdrop", "none,,0"});
        sekaiMoveZone = "none";''', "창이 없어도 놓음")
    inp.write_text(t)
    print("    적용: InputManager.cpp 누르고 있을 때만 끌기, 창이 닫혀도 놓음 알림")
else:
    print("    (InputManager.cpp 끌기 보강 이미 적용됨)")
