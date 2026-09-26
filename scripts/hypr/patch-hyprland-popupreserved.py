#!/usr/bin/env python3
"""Hyprland 패치 — 창의 메뉴(팝업)는 작업 표시줄 같은 예약 영역을 빼고 화면 안으로 맞춘다 (SEKAI_POPUP_RESERVED).

xdg_popup 을 화면 안으로 옮기거나 뒤집을 때(xdg_positioner 의 constraint_adjustment) Hyprland 0.50.1 은
모니터 전체를 기준으로 삼았다. 그래서 화면 아래쪽에서 연 항목 많은 오른쪽 클릭 메뉴가 화면 끝까지는 맞춰지지만
작업 표시줄(레이어 셸의 exclusive zone = 모니터의 예약 영역) 밑으로 들어갔다 (윈도우는 작업 표시줄을 피한다).
고친다: 창이 가진 팝업이면 기준 상자에서 예약 영역을 뺀다. 전체 화면 창(작업 표시줄이 가려진다)과
레이어(작업 표시줄 자신의 메뉴 등)의 팝업은 그대로. 헤더는 그대로. 멱등.
사용법: patch-hyprland-popupreserved.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_POPUP_RESERVED"
pp = root / "src/desktop/Popup.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = pp.read_text()
if MARK not in t:
    t = sub(t, '''    CBox box = {PMONITOR->m_position.x, PMONITOR->m_position.y, PMONITOR->m_size.x, PMONITOR->m_size.y};
    m_resource->applyPositioning(box, COORDS);''', '''    CBox box = {PMONITOR->m_position.x, PMONITOR->m_position.y, PMONITOR->m_size.x, PMONITOR->m_size.y};
    // SEKAI_POPUP_RESERVED: 창의 메뉴는 작업 표시줄 같은 예약 영역을 피한다 (전체 화면 창·레이어의 팝업은 그대로)
    if (const auto W = m_windowOwner.lock(); W && !W->isEffectiveInternalFSMode(FSMODE_FULLSCREEN)) {
        box.x += PMONITOR->m_reservedTopLeft.x;
        box.y += PMONITOR->m_reservedTopLeft.y;
        box.w -= PMONITOR->m_reservedTopLeft.x + PMONITOR->m_reservedBottomRight.x;
        box.h -= PMONITOR->m_reservedTopLeft.y + PMONITOR->m_reservedBottomRight.y;
    }
    m_resource->applyPositioning(box, COORDS);''', "팝업 기준 상자")
    pp.write_text(t)
    print("    적용: Popup.cpp 팝업 기준 상자에서 예약 영역 빼기")
else:
    print("    (Popup.cpp 이미 적용됨)")
