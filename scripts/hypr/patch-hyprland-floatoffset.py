#!/usr/bin/env python3
"""Hyprland 패치 — 창이 제목줄 높이만큼 밀려 그려진 채 굳던 것 (SEKAI_FLOAT_OFFSET).

m_floatingOffset 은 데스크톱(워크스페이스)이 미끄러지는 동안 화면 밖으로 삐져나간 떠 있는 창(스냅한 창의
그림자 등)을 잘라 그리려고 쓰는 "그리기 전용" 오프셋이다. 창 본문과 제목줄(hyprbars)만 이만큼 옮겨 그리고
클릭 판정에는 쓰지 않는다. 그런데 이 값은 그 창이 속한 데스크톱의 애니메이션만 다시 계산한다.
데스크톱이 미끄러져 나가며 값이 남은 창이 애니메이션 없이 다른 데스크톱·모니터로 옮겨지면(작업 보기·
모니터 사이로 끌기 등) 아무도 다시 계산하지 않아, 창이 제목줄 높이쯤 밀려 그려진 채 굳었다 — 닫기 단추는
보이지 않는 원래 자리를 눌러야 먹었다. (VM: 데스크톱 1 → 2 로 넘어간 뒤 스냅한 터미널을 2 로 옮기면 재현)
고친다: 창을 그리기 직전, 그 창의 데스크톱이 미끄러지는 중이 아니면 남은 오프셋을 버린다
(render/Renderer.cpp renderWindow — 제목줄 등 장식도 여기서 그려지므로 함께 바로잡힌다). 헤더는 그대로. 멱등.
사용법: patch-hyprland-floatoffset.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_FLOAT_OFFSET"
ren = root / "src/render/Renderer.cpp"

t = ren.read_text()
if MARK in t:
    print("    (Renderer.cpp 이미 적용됨)")
    sys.exit(0)
old = '''    const auto                       PWORKSPACE = pWindow->m_workspace;
    const auto                       REALPOS    = pWindow->m_realPosition->value() + (pWindow->m_pinned ? Vector2D{} : PWORKSPACE->m_renderOffset->value());
'''
assert t.count(old) == 1, "renderWindow 기준점 없음"
t = t.replace(old, '''    const auto                       PWORKSPACE = pWindow->m_workspace;
    // SEKAI_FLOAT_OFFSET: 데스크톱이 미끄러지는 동안에만 쓰는 그리기 오프셋 — 그 사이 다른 데스크톱·모니터로 옮겨진
    //   창에 남아 창과 제목줄만 밀려 그려졌다(클릭 판정은 제자리). 지금 데스크톱이 움직이지 않으면 버린다
    if (pWindow->m_floatingOffset != Vector2D{} && PWORKSPACE && !PWORKSPACE->m_renderOffset->isBeingAnimated())
        pWindow->m_floatingOffset = Vector2D{};
    const auto                       REALPOS    = pWindow->m_realPosition->value() + (pWindow->m_pinned ? Vector2D{} : PWORKSPACE->m_renderOffset->value());
''', 1)
ren.write_text(t)
print("    적용: Renderer.cpp 멈춘 데스크톱의 창에 남은 그리기 오프셋을 버린다")
