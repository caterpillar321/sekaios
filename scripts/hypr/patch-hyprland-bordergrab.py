#!/usr/bin/env python3
"""Hyprland 패치 — 창 테두리로 크기 조절을 윈도우처럼 (SEKAI_BORDER_GRAB).

Hyprland 0.50.1 에서:
  ① 테두리 위 커서가 안 바뀐다 — setCursorIconOnBorder 첫 줄 조건이 거꾸로다
     ("끌고 있지 않으면 끝낸다"). 보이지 않는 판정 띠를 찾을 길이 없었다.
  ② 가장자리는 창 바깥 16px 띠에서만 잡힌다 (모서리만 창 안쪽 둥근 곳까지 인정).
     테두리 선이나 그 안쪽을 잡으면 모서리는 되고 가장자리는 안 됐다.
  ③ 제목줄(hyprbars)이 그 띠를 덮어 위쪽으로는 늘릴 수 없었다.
  ④ 가장자리를 잡아도 모서리처럼 가로·세로가 함께 바뀐다.
고친다: 판정 함수 하나(sekaiBorderAt)를 누를 때와 커서에 같이 쓴다 — 제목줄을 포함한 창의
바깥 띠 + 안쪽 4px. 제목줄 판정보다 먼저 봐서 제목줄 맨 위도 위쪽 가장자리. 가장자리를 잡으면
그 방향으로만 (g_sekaiResizeAxis → IHyprLayout). 헤더는 그대로라 플러그인 ABI 는 같다. 멱등.

사용법: patch-hyprland-bordergrab.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_BORDER_GRAB"
inp = root / "src/managers/input/InputManager.cpp"
lay = root / "src/layout/IHyprLayout.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = inp.read_text()
if MARK not in t:
    t = sub(t, '#include "../../managers/permissions/DynamicPermissionManager.hpp"\n',
            '#include "../../managers/permissions/DynamicPermissionManager.hpp"\n' + r'''
// ── SEKAI_BORDER_GRAB: 윈도우처럼 창 테두리로 크기 조절 ─────────────────
int                     g_sekaiResizeAxis = 0; // 0 모서리 · 1 세로만(위·아래 가장자리) · 2 가로만(왼쪽·오른쪽) — IHyprLayout 가 읽는다
static constexpr double SEKAI_INNER_GRAB  = 4;  // 창 안쪽으로도 이만큼은 가장자리
static constexpr double SEKAI_CORNER      = 20; // 가장자리 끝에서 이만큼은 모서리

// 커서가 창(제목줄 포함) 테두리 근처면 방향 비트 (1 왼 · 2 오 · 4 위 · 8 아래), 아니면 0
static int sekaiBorderAt(PHLWINDOW w, const Vector2D& p, double outer) {
    const CBox real = {w->m_realPosition->value().x, w->m_realPosition->value().y, w->m_realSize->value().x, w->m_realSize->value().y};
    const auto EXT  = w->getFullWindowReservedArea(); // 제목줄 등 장식이 차지한 몫
    const CBox B    = {real.x - EXT.topLeft.x, real.y - EXT.topLeft.y, real.width + EXT.topLeft.x + EXT.bottomRight.x,
                       real.height + EXT.topLeft.y + EXT.bottomRight.y};
    if (!B.copy().expand(outer).containsPoint(p))
        return 0;

    int e = 0;
    if (!B.containsPoint(p)) { // 바깥 띠
        if (p.x < B.x)
            e |= 1;
        else if (p.x >= B.x + B.width)
            e |= 2;
        if (p.y < B.y)
            e |= 4;
        else if (p.y >= B.y + B.height)
            e |= 8;
    } else { // 안쪽 몇 px
        if (p.x < B.x + SEKAI_INNER_GRAB)
            e |= 1;
        else if (p.x > B.x + B.width - SEKAI_INNER_GRAB)
            e |= 2;
        if (p.y < B.y + SEKAI_INNER_GRAB)
            e |= 4;
        else if (p.y > B.y + B.height - SEKAI_INNER_GRAB)
            e |= 8;
        if (!e && real.containsPoint(p) && w->isInCurvedCorner(p.x, p.y)) // 둥근 모서리 안쪽 (원래 동작)
            e = (p.x < real.x + real.width / 2 ? 1 : 2) | (p.y < real.y + real.height / 2 ? 4 : 8);
    }
    if (!e)
        return 0;
    // 가장자리 끝 가까이면 모서리로
    if (e & 12) {
        if (p.x < B.x + SEKAI_CORNER)
            e |= 1;
        else if (p.x > B.x + B.width - SEKAI_CORNER)
            e |= 2;
    }
    if (e & 3) {
        if (p.y < B.y + SEKAI_CORNER)
            e |= 4;
        else if (p.y > B.y + B.height - SEKAI_CORNER)
            e |= 8;
    }
    return e;
}
''', "include")

    # 누를 때 — 제목줄 판정(checkInputOnDecos)보다 먼저
    t = sub(t, '''void CInputManager::processMouseDownNormal(const IPointer::SButtonEvent& e) {

    // notify the keybind manager''', '''void CInputManager::processMouseDownNormal(const IPointer::SButtonEvent& e) {

    if (e.state == WL_POINTER_BUTTON_STATE_PRESSED) // SEKAI_BORDER_GRAB — 새로 누를 때마다 (Super+끌기 크기 조절은 모서리대로)
        g_sekaiResizeAxis = 0;

    // notify the keybind manager''', "processMouseDownNormal 시작")
    t = sub(t, '''    if (w && !m_lastFocusOnLS && !g_pSessionLockManager->isSessionLocked() && w->checkInputOnDecos(INPUT_TYPE_BUTTON, mouseCoords, e))
        return;
''', '''    // SEKAI_BORDER_GRAB: 테두리를 누르면 크기 조절 — 제목줄 맨 위도 위쪽 가장자리라 제목줄보다 먼저 본다
    if (*PRESIZEONBORDER && w && !w->isFullscreen() && !w->isX11OverrideRedirect() && !g_pSessionLockManager->isSessionLocked() && !m_lastFocusOnLS &&
        e.state == WL_POINTER_BUTTON_STATE_PRESSED && !w->hasPopupAt(mouseCoords)) {
        const int EDGE = sekaiBorderAt(w, mouseCoords, BORDER_GRAB_AREA);
        if (EDGE) {
            const bool H      = EDGE & 3, V = EDGE & 12;
            g_sekaiResizeAxis = H && V ? 0 : (V ? 1 : 2); // 가장자리만 잡았으면 그 방향으로만
            g_pKeybindManager->resizeWithBorder(e);
            return;
        }
    }

    if (w && !m_lastFocusOnLS && !g_pSessionLockManager->isSessionLocked() && w->checkInputOnDecos(INPUT_TYPE_BUTTON, mouseCoords, e))
        return;
''', "checkInputOnDecos")
    # 원래 판정은 위에서 다 했으니 끈다 (한쪽만 고치면 누를 때와 커서가 어긋난다)
    t = sub(t, '''            if ((grab.containsPoint(mouseCoords) && (!real.containsPoint(mouseCoords) || w->isInCurvedCorner(mouseCoords.x, mouseCoords.y))) && !w->hasPopupAt(mouseCoords)) {''',
            '''            if (false && (grab.containsPoint(mouseCoords) && (!real.containsPoint(mouseCoords) || w->isInCurvedCorner(mouseCoords.x, mouseCoords.y))) && !w->hasPopupAt(mouseCoords)) { // SEKAI_BORDER_GRAB''',
            "원래 테두리 판정")

    # 커서 — 조건 버그 + 같은 판정
    a = t.index("void CInputManager::setCursorIconOnBorder(PHLWINDOW w) {")
    b = t.index("    if (direction == m_borderIconDirection)", a)
    t = t[:a] + '''void CInputManager::setCursorIconOnBorder(PHLWINDOW w) {
    // SEKAI_BORDER_GRAB: 끄는 중(마우스 바인드)일 때만 건드리지 않는다.
    //   원래 조건이 거꾸로(expired)라 테두리 위에서 커서가 한 번도 바뀌지 않았다
    if (!g_pInputManager->m_currentlyDraggedWindow.expired())
        return;

    // ignore X11 OR windows, they shouldn't be touched
    if (w->m_isX11 && w->isX11OverrideRedirect())
        return;

    static auto          PEXTENDBORDERGRAB = CConfigValue<Hyprlang::INT>("general:extend_border_grab_area");
    const auto           mouseCoords       = getMouseCoordsInternal();
    eBorderIconDirection direction         = BORDERICON_NONE;

    if (!w->hasPopupAt(mouseCoords) && m_currentlyHeldButtons.empty()) {
        switch (sekaiBorderAt(w, mouseCoords, w->getRealBorderSize() + *PEXTENDBORDERGRAB)) { // 누를 때와 같은 판정
            case 1: direction = BORDERICON_LEFT; break;
            case 2: direction = BORDERICON_RIGHT; break;
            case 4: direction = BORDERICON_UP; break;
            case 8: direction = BORDERICON_DOWN; break;
            case 5: direction = BORDERICON_UP_LEFT; break;
            case 6: direction = BORDERICON_UP_RIGHT; break;
            case 9: direction = BORDERICON_DOWN_LEFT; break;
            case 10: direction = BORDERICON_DOWN_RIGHT; break;
            default: break;
        }
    }

''' + t[b:]
    inp.write_text(t)
    print("    적용: InputManager.cpp 테두리 판정·커서")
else:
    print("    (InputManager.cpp 이미 적용됨)")

t = lay.read_text()
if MARK not in t:
    t = sub(t, '#include "IHyprLayout.hpp"\n',
            '#include "IHyprLayout.hpp"\n'
            'extern int g_sekaiResizeAxis; // SEKAI_BORDER_GRAB (InputManager.cpp) — 1 세로만 · 2 가로만\n',
            "IHyprLayout include")
    t = sub(t, '''            if (m_grabbedCorner == CORNER_BOTTOMRIGHT)
                newSize = newSize + DELTA;
            else if (m_grabbedCorner == CORNER_TOPLEFT)
                newSize = newSize - DELTA;
            else if (m_grabbedCorner == CORNER_TOPRIGHT)
                newSize = newSize + Vector2D(DELTA.x, -DELTA.y);
            else if (m_grabbedCorner == CORNER_BOTTOMLEFT)
                newSize = newSize + Vector2D(-DELTA.x, DELTA.y);
''', '''            // SEKAI_BORDER_GRAB: 가장자리를 잡았으면 그 방향으로만
            Vector2D SDELTA = DELTA;
            if (g_sekaiResizeAxis == 1)
                SDELTA.x = 0;
            else if (g_sekaiResizeAxis == 2)
                SDELTA.y = 0;

            if (m_grabbedCorner == CORNER_BOTTOMRIGHT)
                newSize = newSize + SDELTA;
            else if (m_grabbedCorner == CORNER_TOPLEFT)
                newSize = newSize - SDELTA;
            else if (m_grabbedCorner == CORNER_TOPRIGHT)
                newSize = newSize + Vector2D(SDELTA.x, -SDELTA.y);
            else if (m_grabbedCorner == CORNER_BOTTOMLEFT)
                newSize = newSize + Vector2D(-SDELTA.x, SDELTA.y);
''', "크기 계산")
    t = sub(t, '''    if (g_pInputManager->m_dragMode != MBIND_RESIZE && g_pInputManager->m_dragMode != MBIND_RESIZE_FORCE_RATIO && g_pInputManager->m_dragMode != MBIND_RESIZE_BLOCK_RATIO)
        g_pInputManager->setCursorImageUntilUnset("grabbing");
''', '''    // SEKAI_BORDER_GRAB: 가장자리를 잡았으면 한 방향 화살표
    if (g_pInputManager->m_dragMode == MBIND_RESIZE && g_sekaiResizeAxis == 1)
        g_pInputManager->setCursorImageUntilUnset(m_grabbedCorner & (CORNER_TOPLEFT | CORNER_TOPRIGHT) ? "top_side" : "bottom_side");
    else if (g_pInputManager->m_dragMode == MBIND_RESIZE && g_sekaiResizeAxis == 2)
        g_pInputManager->setCursorImageUntilUnset(m_grabbedCorner & (CORNER_TOPLEFT | CORNER_BOTTOMLEFT) ? "left_side" : "right_side");

    if (g_pInputManager->m_dragMode != MBIND_RESIZE && g_pInputManager->m_dragMode != MBIND_RESIZE_FORCE_RATIO && g_pInputManager->m_dragMode != MBIND_RESIZE_BLOCK_RATIO)
        g_pInputManager->setCursorImageUntilUnset("grabbing");
''', "끌기 시작 커서")
    lay.write_text(t)
    print("    적용: IHyprLayout.cpp 한 방향 크기 조절")
else:
    print("    (IHyprLayout.cpp 이미 적용됨)")

# 커서 모양 — 윈도우처럼 양방향 화살표 (↔ ↕ ⤡ ⤢). 원래는 X11 식 "막대로 향하는 화살표"·한쪽 화살표.
#   따로 멱등 (이름만 바꾼다)
NAMES = [('"top_side"', '"ns-resize"'), ('"bottom_side"', '"ns-resize"'),
         ('"left_side"', '"ew-resize"'), ('"right_side"', '"ew-resize"'),
         ('"top_left_corner"', '"nwse-resize"'), ('"bottom_right_corner"', '"nwse-resize"'),
         ('"top_right_corner"', '"nesw-resize"'), ('"bottom_left_corner"', '"nesw-resize"'),
         ('"nw-resize"', '"nwse-resize"'), ('"se-resize"', '"nwse-resize"'),
         ('"ne-resize"', '"nesw-resize"'), ('"sw-resize"', '"nesw-resize"')]
for f in (inp, lay):
    t = f.read_text()
    n = t
    for a, b in NAMES:
        n = n.replace(f"setCursorImageUntilUnset({a})", f"setCursorImageUntilUnset({b})")
        n = n.replace(f"? {a} : ", f"? {b} : ").replace(f" : {a});", f" : {b});")
    if n != t:
        f.write_text(n)
        print(f"    적용: {f.name} 커서 이름 (양방향 화살표)")

# ── SEKAI_BORDER_FIX: 위 패치의 부작용 세 가지 (따로 멱등) ─────────────────────────────
#   ① 포인터를 창 안에 가두는 앱(게임·가상머신)은 가장자리 4px 누름이 크기 조절로 삼켜졌다 — 갇혀 있으면 앱에 준다
#   ② 크기 조절을 끝내고 손을 떼면 테두리 위인데도 크기 조절 커서가 다시 안 떴다 — 끄는 동안 방향 기억을 비운다
#      (원래 코드의 의도. 다음 움직임에 다시 판정한다)
#   ③ 테두리 커서를 띄운 채 창에 들어가면 앱이 enter 에 답해 보낸 커서 요청이 버려져, 창 안에서 엉뚱한 커서가 남았다
#      — 테두리 커서 동안 온 요청은 적어만 두고, 테두리를 벗어날 때(unsetCursorImage → restoreCursorIconToApp) 띄운다
t = inp.read_text()
if "SEKAI_BORDER_FIX" not in t:
    t = sub(t, '''    if (*PRESIZEONBORDER && w && !w->isFullscreen() && !w->isX11OverrideRedirect() && !g_pSessionLockManager->isSessionLocked() && !m_lastFocusOnLS &&
        e.state == WL_POINTER_BUTTON_STATE_PRESSED && !w->hasPopupAt(mouseCoords)) {''',
            '''    if (*PRESIZEONBORDER && w && !w->isFullscreen() && !w->isX11OverrideRedirect() && !g_pSessionLockManager->isSessionLocked() && !m_lastFocusOnLS &&
        e.state == WL_POINTER_BUTTON_STATE_PRESSED && !w->hasPopupAt(mouseCoords) && (g_pSeatManager->m_mouse.expired() || !isConstrained()) /* SEKAI_BORDER_FIX */) {''',
            "갇힌 포인터")
    t = sub(t, '''    if (!g_pInputManager->m_currentlyDraggedWindow.expired())
        return;

    // ignore X11 OR windows, they shouldn't be touched''',
            '''    if (!g_pInputManager->m_currentlyDraggedWindow.expired()) {
        m_borderIconDirection = BORDERICON_NONE; // SEKAI_BORDER_FIX: 끝나면 다음 움직임에 다시 판정
        return;
    }

    // ignore X11 OR windows, they shouldn't be touched''', "끄는 동안 방향")
    # 앱 커서 요청 두 곳 (wl_pointer.set_cursor · cursor-shape)
    t = sub(t, '''void CInputManager::processMouseRequest(const CSeatManager::SSetCursorEvent& event) {
    if (!cursorImageUnlocked())
        return;
''', '''void CInputManager::processMouseRequest(const CSeatManager::SSetCursorEvent& event) {
    const bool SEKAIDEFER = m_cursorImageOverridden && m_borderIconDirection != BORDERICON_NONE && m_clickBehavior != CLICKMODE_KILL; // SEKAI_BORDER_FIX: 테두리 커서 동안이면 적어만 둔다
    if (!cursorImageUnlocked() && !SEKAIDEFER)
        return;
''', "커서 요청 조건")
    t = sub(t, '''    m_cursorSurfaceInfo.name = "";

    m_cursorSurfaceInfo.inUse = true;
    g_pHyprRenderer->setCursorSurface(m_cursorSurfaceInfo.wlSurface, event.hotspot.x, event.hotspot.y);''',
            '''    m_cursorSurfaceInfo.name = "";

    m_cursorSurfaceInfo.inUse = !SEKAIDEFER; // 미뤘으면 restoreCursorIconToApp 가 띄운다
    if (SEKAIDEFER)
        return;
    g_pHyprRenderer->setCursorSurface(m_cursorSurfaceInfo.wlSurface, event.hotspot.x, event.hotspot.y);''', "커서 요청 적용")
    t = sub(t, '''    m_listeners.setCursorShape = PROTO::cursorShape->m_events.setShape.listen([this](const CCursorShapeProtocol::SSetShapeEvent& event) {
        if (!cursorImageUnlocked())
            return;
''', '''    m_listeners.setCursorShape = PROTO::cursorShape->m_events.setShape.listen([this](const CCursorShapeProtocol::SSetShapeEvent& event) {
        const bool SEKAIDEFER = m_cursorImageOverridden && m_borderIconDirection != BORDERICON_NONE && m_clickBehavior != CLICKMODE_KILL; // SEKAI_BORDER_FIX: 테두리 커서 동안이면 적어만 둔다
        if (!cursorImageUnlocked() && !SEKAIDEFER)
            return;
''', "커서 모양 조건")
    t = sub(t, '''        m_cursorSurfaceInfo.hidden   = false;

        m_cursorSurfaceInfo.inUse = true;
        g_pHyprRenderer->setCursorFromName(m_cursorSurfaceInfo.name);''',
            '''        m_cursorSurfaceInfo.hidden   = false;

        m_cursorSurfaceInfo.inUse = !SEKAIDEFER;
        if (SEKAIDEFER)
            return;
        g_pHyprRenderer->setCursorFromName(m_cursorSurfaceInfo.name);''', "커서 모양 적용")
    inp.write_text(t)
    print("    적용: InputManager.cpp 테두리 부작용 (갇힌 포인터·끝난 뒤 커서·앱 커서 요청)")
else:
    print("    (InputManager.cpp 테두리 부작용 이미 적용됨)")

# ── SEKAI_BORDER_EDGE: 화면 끝에 붙은 변은 안쪽 띠를 끈다 ──
#   스냅한 창의 스크롤바를 잡으려고 커서를 화면 오른쪽 끝으로 던지거나, 위쪽에 붙은 창의 제목줄을 화면 맨 위에서
#   잡으면 스크롤·끌기 대신 크기 조절이 됐다. 윈도우는 테두리가 창 바깥이라 화면 끝 픽셀이 창 몫이다 —
#   모니터 끝·작업 표시줄 끝에 붙은 변은 안쪽 4px 띠(와 둥근 모서리 안쪽)를 크기 조절로 보지 않는다.
#   (hyprbars 쪽 patch-hyprbars-bordergrab.py 도 같은 변은 넘기지 않는다)
t = inp.read_text()
if "SEKAI_BORDER_EDGE" not in t:
    t = sub(t, '''// 커서가 창(제목줄 포함) 테두리 근처면 방향 비트 (1 왼 · 2 오 · 4 위 · 8 아래), 아니면 0
static int sekaiBorderAt(''', '''// SEKAI_BORDER_EDGE: 창(제목줄 포함) 상자 B 의 변 중 화면 끝(모니터 끝·작업 표시줄 끝)에 붙은 변 — 방향 비트
static int sekaiScreenEdges(PHLWINDOW w, const CBox& B) {
    const auto M = w->m_monitor.lock();
    if (!M)
        return 0;
    const double L = M->m_position.x + M->m_reservedTopLeft.x, T = M->m_position.y + M->m_reservedTopLeft.y;
    const double R = M->m_position.x + M->m_size.x - M->m_reservedBottomRight.x, D = M->m_position.y + M->m_size.y - M->m_reservedBottomRight.y;
    return (B.x <= L + 1 ? 1 : 0) | (B.x + B.width >= R - 1 ? 2 : 0) | (B.y <= T + 1 ? 4 : 0) | (B.y + B.height >= D - 1 ? 8 : 0);
}

// 커서가 창(제목줄 포함) 테두리 근처면 방향 비트 (1 왼 · 2 오 · 4 위 · 8 아래), 아니면 0
static int sekaiBorderAt(''', "화면 끝 변 함수")
    t = sub(t, '''        if (!e && real.containsPoint(p) && w->isInCurvedCorner(p.x, p.y)) // 둥근 모서리 안쪽 (원래 동작)
            e = (p.x < real.x + real.width / 2 ? 1 : 2) | (p.y < real.y + real.height / 2 ? 4 : 8);
    }''', '''        if (!e && real.containsPoint(p) && w->isInCurvedCorner(p.x, p.y)) // 둥근 모서리 안쪽 (원래 동작)
            e = (p.x < real.x + real.width / 2 ? 1 : 2) | (p.y < real.y + real.height / 2 ? 4 : 8);
        e &= ~sekaiScreenEdges(w, B); // SEKAI_BORDER_EDGE: 화면 끝에 붙은 변의 안쪽은 창 몫
    }''', "안쪽 띠에서 화면 끝 변 빼기")
    inp.write_text(t)
    print("    적용: InputManager.cpp 화면 끝에 붙은 변")
else:
    print("    (InputManager.cpp 화면 끝 변 이미 적용됨)")

# ── SEKAI_BORDER_TOPONLY: 안쪽 띠는 위쪽만 ──
#   윈도우는 크기 조절 테두리가 창 바깥에 있고(옆·아래), 안쪽을 쓰는 건 제목줄 위쪽뿐이다. 네 변 모두 안쪽 4px 을 크기
#   조절로 잡아서, 창 가장자리의 스크롤바·단추에 마우스를 올리면 앱이 강조를 띄우는데 누르면 크기 조절이 됐다
#   ("강조가 뜬 자리와 눌리는 자리가 어긋난다"). 옆·아래는 바깥 띠(extend_border_grab_area)로 잡는다.
t = inp.read_text()
if "SEKAI_BORDER_TOPONLY" not in t:
    t = sub(t, '''        if (p.x < B.x + SEKAI_INNER_GRAB)
            e |= 1;
        else if (p.x > B.x + B.width - SEKAI_INNER_GRAB)
            e |= 2;
        if (p.y < B.y + SEKAI_INNER_GRAB)
            e |= 4;
        else if (p.y > B.y + B.height - SEKAI_INNER_GRAB)
            e |= 8;
''', '''        // SEKAI_BORDER_TOPONLY: 안쪽 띠는 위쪽만 (윈도우처럼 옆·아래 테두리는 창 바깥 — 창 안은 앱 몫)
        if (p.y < B.y + SEKAI_INNER_GRAB)
            e |= 4;
''', "안쪽 띠 위쪽만")
    inp.write_text(t)
    print("    적용: InputManager.cpp 안쪽 띠는 위쪽만")
else:
    print("    (InputManager.cpp 안쪽 띠 위쪽만 이미 적용됨)")
