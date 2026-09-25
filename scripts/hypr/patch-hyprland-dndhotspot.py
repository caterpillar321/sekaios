#!/usr/bin/env python3
"""Hyprland 패치 — 끌기 아이콘의 기준점(핫스폿)을 지킨다 (SEKAI_DND_HOTSPOT).

wl_surface.attach·offset 의 x, y 는 "지금 자리에서 얼마나 옮길지"라서 쌓아야 한다(Wayland 규칙 —
커서·끌기 아이콘은 이것으로 기준점을 정한다). Hyprland 0.50.1 은 끌기 아이콘을 그릴 때 마지막 커밋의
x, y 만 더했다. GTK 는 누른 점을 기준점으로 한 번만 옮기고(-hot_x, -hot_y) 다음 그림부터는 0 을 보내므로,
끄는 도중 기준점이 사라져 커서가 그림 왼쪽 위에 붙었다 (바탕화면 아이콘·파일 끌기).
고친다: 끌기를 시작할 때의 오프셋에서 출발해 커밋마다 쌓은 값으로 그린다. 헤더는 그대로. 멱등.
사용법: patch-hyprland-dndhotspot.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_DND_HOTSPOT"
dd = root / "src/protocols/core/DataDevice.cpp"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


t = dd.read_text()
if MARK not in t:
    t = sub(t, '''#include "../../xwayland/XWayland.hpp"
''', '''#include "../../xwayland/XWayland.hpp"

// SEKAI_DND_HOTSPOT: 끌기 아이콘 표면을 지금까지 옮긴 양 (attach·offset 의 x, y 를 쌓은 것)
static Vector2D sekaiDndOffset;
''', "누적 변수")
    t = sub(t, '''    surfacePos += m_dnd.dndSurface->m_current.offset;''',
            '''    surfacePos += sekaiDndOffset; // SEKAI_DND_HOTSPOT — 마지막 커밋의 x, y 만이 아니라 쌓은 값''', "그리기")
    t = sub(t, '''    m_dnd.dndSurface    = dragSurface;
    if (dragSurface) {
        m_dnd.dndSurfaceDestroy = dragSurface->m_events.destroy.listen([this] { abortDrag(); });
        m_dnd.dndSurfaceCommit  = dragSurface->m_events.commit.listen([this] {''',
            '''    m_dnd.dndSurface    = dragSurface;
    sekaiDndOffset      = dragSurface ? dragSurface->m_current.offset : Vector2D{}; // SEKAI_DND_HOTSPOT: 시작 전에 커밋한 기준점
    if (dragSurface) {
        m_dnd.dndSurfaceDestroy = dragSurface->m_events.destroy.listen([this] { abortDrag(); });
        m_dnd.dndSurfaceCommit  = dragSurface->m_events.commit.listen([this] {
            if (m_dnd.dndSurface->m_current.updated.bits.offset) // SEKAI_DND_HOTSPOT: 옮긴 양을 쌓는다
                sekaiDndOffset += m_dnd.dndSurface->m_current.offset;
''', "커밋마다 쌓기")
    dd.write_text(t)
    print("    적용: DataDevice.cpp 끌기 아이콘 기준점")
else:
    print("    (DataDevice.cpp 이미 적용됨)")
