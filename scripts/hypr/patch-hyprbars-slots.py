#!/usr/bin/env python3
"""hyprbars 패치 — 창 단추의 판정 칸 = 그려지는 칸 (SEKAI_BUTTON_SLOT).

단추 강조는 아이콘을 가운데 두고 그리는데(SEKAI_BUTTON_HOVER), 판정 칸(마우스 올림·누름)은 아이콘 왼쪽으로 단추 간격만큼
밀린 칸이었고 세로로는 가운데 16px 뿐이었다 — 강조가 뜬 자리를 눌러도 안 먹거나, 강조가 없는 옆자리에서 눌렸다.
윈도우처럼 단추끼리 틈 없이 붙은 칸(아이콘 ± 간격의 반)을 제목줄 높이 전체로 판정하고, 강조도 그 칸에 그린다.
맨 위 4px 이 창 테두리(크기 조절)로 넘어가는 창이면 그 4px 은 판정에서 뺀다 — 눌리는 곳에서만 강조가 뜨게.
또 제목줄 양 끝 4px 은 더 넘기지 않는다 (patch-hyprland-bordergrab.py SEKAI_BORDER_TOPONLY — 윈도우처럼 옆 테두리는 창 바깥).
patch-hyprbars-bordergrab.py(EDGE2)·inputfix 다음에 적용. 멱등.
사용법: patch-hyprbars-slots.py <barDeco.cpp>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SEKAI_BUTTON_SLOT" in s:
    print("    (이미 적용됨)")
    sys.exit(0)


def sub(old, new, what):
    global s
    assert s.count(old) == 1, f"기준점 없음/중복: {what} ({s.count(old)})"
    s = s.replace(old, new, 1)


# 도움 함수 — sekaiBarScreenEdges(EDGE2) 바로 뒤
sub('''void CHyprBar::onMouseButton(SCallbackInfo& info, IPointer::SButtonEvent e) {
''', '''// SEKAI_BUTTON_SLOT: 단추 칸 — 아이콘(바 좌표 iconX = currentPos.x + 간격) ± 간격의 반, 제목줄 높이 전체.
//   맨 위 4px 이 창 테두리로 넘어가는 창(크기 조절이 되고 화면 맨 위에 붙지 않은 창)이면 그 4px 은 뺀다 (onMouseButton 과 같은 판정)
static bool sekaiInButton(PHLWINDOW w, const Vector2D& c, const Vector2D& currentPos, double size, double pad, double barH) {
    static auto* const PSEKAIRESIZE = (Hyprlang::INT* const*)HyprlandAPI::getConfigValue(PHANDLE, "general:resize_on_border")->getDataStaticPtr();
    const bool         TOPBORDER    = **PSEKAIRESIZE && w && !w->isFullscreen() && !w->isX11OverrideRedirect() && !(sekaiBarScreenEdges(w) & 4);
    const double       X0 = currentPos.x + pad / 2.0, X1 = currentPos.x + pad + size + pad / 2.0;
    return c.x >= X0 && c.x < X1 && c.y >= (TOPBORDER ? 4 : 0) && c.y < barH;
}

void CHyprBar::onMouseButton(SCallbackInfo& info, IPointer::SButtonEvent e) {
''', "도움 함수")

sub('''        bool       hovering   = VECINRECT(COORDS, currentPos.x, currentPos.y, currentPos.x + button.size + **PBARBUTTONPADDING, currentPos.y + button.size);
''', '''        bool       hovering   = sekaiInButton(m_pWindow.lock(), COORDS, currentPos, button.size, **PBARBUTTONPADDING, BARBUF.y); // SEKAI_BUTTON_SLOT
''', "그리기 강조 판정")
sub('''        bool       hover = VECINRECT(COORDS, currentPos.x, currentPos.y, currentPos.x + b.size + **PBARBUTTONPADDING, currentPos.y + b.size);
''', '''        bool       hover = sekaiInButton(m_pWindow.lock(), COORDS, currentPos, b.size, **PBARBUTTONPADDING, BARBUF.y); // SEKAI_BUTTON_SLOT
''', "올림 판정")
sub('''        if (VECINRECT(COORDS, currentPos.x, currentPos.y, currentPos.x + b.size + **PBARBUTTONPADDING, currentPos.y + b.size)) {
''', '''        if (sekaiInButton(m_pWindow.lock(), COORDS, currentPos, b.size, **PBARBUTTONPADDING, BARBUF.y)) { // SEKAI_BUTTON_SLOT
''', "누름 판정")
# 제목줄 양 끝 4px 은 넘기지 않는다 — 위쪽만
sub('''        if ((SEKAI_C.y < 4 && !(SEKAI_E & 4)) || (SEKAI_C.x < 4 && !(SEKAI_E & 1)) || (SEKAI_C.x > SEKAI_B.w - 4 && !(SEKAI_E & 2)))
            return;
''', '''        // SEKAI_BUTTON_SLOT: 위쪽 4px 만 창 테두리 — 옆 테두리는 창 바깥 (Hyprland SEKAI_BORDER_TOPONLY 와 같게)
        (void)SEKAI_B;
        if (SEKAI_C.y < 4 && !(SEKAI_E & 4))
            return;
''', "양 끝 넘기기")
open(p, "w", encoding="utf-8").write(s)
print("    적용: 단추 판정 칸 = 그려지는 칸, 제목줄 양 끝은 넘기지 않음")
