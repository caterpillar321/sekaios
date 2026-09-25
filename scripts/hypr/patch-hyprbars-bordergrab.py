#!/usr/bin/env python3
"""hyprbars 패치 — 제목줄 가장자리 4px 누름은 가로채지 않는다 (SEKAI_BORDER_GRAB).

제목줄은 창 위쪽에 붙은 장식이라 창의 위쪽 가장자리가 곧 제목줄 맨 위다. hyprbars 가 누름을
먼저 가로채 끌어 옮기기로 써서, 위쪽으로 크기를 늘릴 수 없었다. 맨 위·왼쪽 끝·오른쪽 끝 4px 은
넘겨서 Hyprland(patch-hyprland-bordergrab.py 의 sekaiBorderAt)가 크기 조절을 하게 한다.
SEKAI_BORDER_GRAB2: Hyprland 가 테두리 크기 조절을 하지 않는 창(최대화·전체 화면, resize_on_border 꺼짐)이면
넘기지 않는다 — 넘기면 아무도 받지 않아 제목줄 맨 위·양 끝이 끌기·더블클릭이 안 되는 띠가 됐다. 멱등.
사용법: patch-hyprbars-bordergrab.py <barDeco.cpp>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
NEW2 = '''    // SEKAI_BORDER_GRAB: 제목줄 가장자리 4px 은 창 테두리 — Hyprland 가 크기 조절하게 넘긴다
    //   SEKAI_BORDER_GRAB2: Hyprland 가 테두리 크기 조절을 하는 창일 때만 (최대화·전체 화면은 아니다)
    static auto* const PSEKAIRESIZE = (Hyprlang::INT* const*)HyprlandAPI::getConfigValue(PHANDLE, "general:resize_on_border")->getDataStaticPtr();
    const auto         SEKAI_C      = cursorRelativeToBar();
    const auto         SEKAI_W      = m_pWindow.lock();
    if (**PSEKAIRESIZE && SEKAI_W && !SEKAI_W->isFullscreen() && !SEKAI_W->isX11OverrideRedirect() &&
        (SEKAI_C.y < 4 || SEKAI_C.x < 4 || SEKAI_C.x > assignedBoxGlobal().w - 4))
        return;
'''
OLD1 = '''    // SEKAI_BORDER_GRAB: 제목줄 가장자리 4px 은 창 테두리 — Hyprland 가 크기 조절하게 넘긴다
    const auto SEKAI_C = cursorRelativeToBar();
    if (SEKAI_C.y < 4 || SEKAI_C.x < 4 || SEKAI_C.x > assignedBoxGlobal().w - 4)
        return;
'''
if "SEKAI_BORDER_GRAB2" in s:
    print("이미 적용됨")
    sys.exit(0)
if "SEKAI_BORDER_GRAB" in s:                      # 예전 판(sekai9)이 적용된 소스
    assert s.count(OLD1) == 1, "예전 SEKAI_BORDER_GRAB 기준점 없음"
    open(p, "w", encoding="utf-8").write(s.replace(OLD1, NEW2, 1))
    print("적용함 (GRAB2)")
    sys.exit(0)
old = '''    if (e.state != WL_POINTER_BUTTON_STATE_PRESSED) {
        handleUpEvent(info);
        return;
    }

    handleDownEvent(info, std::nullopt);
}
'''
assert s.count(old) == 1, "onMouseButton 기준점 없음 (patch-hyprbars-snap.py 먼저)"
s = s.replace(old, '''    if (e.state != WL_POINTER_BUTTON_STATE_PRESSED) {
        handleUpEvent(info);
        return;
    }

''' + NEW2 + '''
    handleDownEvent(info, std::nullopt);
}
''', 1)
open(p, "w", encoding="utf-8").write(s)
print("적용함")
