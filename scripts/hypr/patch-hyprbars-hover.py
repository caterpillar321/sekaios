#!/usr/bin/env python3
"""hyprbars 패치 — 창 조작 버튼에 마우스를 올리면 배경이 생긴다 (윈도우 11 처럼).

  닫기(sekai:close)  빨간 배경 #c42b1c
  그 밖             옅은 흰색 배경

업스트림은 icon_on_hover 를 켰을 때만 마우스 이동으로 다시 그린다.
그래서 마우스 이동 때마다 버튼 위인지 확인해 다시 그리게 하고,
아이콘을 그리기 직전에 둥근 사각형 배경을 먼저 그린다. 멱등.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK = "SEKAI_BUTTON_HOVER"
if MARK in s:
    print("이미 적용됨"); sys.exit(0)

# 1. 마우스가 움직이면 항상 버튼 위인지 확인 → 바뀌었으면 다시 그리기
old = '''    if (**PICONONHOVER)
        damageOnButtonHover();
'''
assert old in s, "onMouseMove 기준점 없음"
s = s.replace(old, '''    (void)PICONONHOVER;
    damageOnButtonHover(); // SEKAI_BUTTON_HOVER — 배경 효과를 위해 언제나
''', 1)

# 2. 아이콘 앞에 배경
old = '''        if (!**PICONONHOVER || (**PICONONHOVER && m_iButtonHoverState > 0))
            g_pHyprOpenGL->renderTexture(button.iconTex, pos, a);'''
assert old in s, "renderBarButtonsText 기준점 없음"
s = s.replace(old, '''        if (hovering) { // SEKAI_BUTTON_HOVER
            const double INSETY = std::round(4.0 * scale);
            const double PADX   = std::round(scaledButtonsPad / 2.0 - 2.0 * scale);
            CBox         bg     = {pos.x - PADX, barBox->y + INSETY, pos.w + PADX * 2, barBox->height - INSETY * 2};
            CHyprColor   hc     = button.icon == "sekai:close" ? CHyprColor(0xc4 / 255.0, 0x2b / 255.0, 0x1c / 255.0, 1.0) : CHyprColor(1.0, 1.0, 1.0, 0.10);
            hc.a *= a;
            g_pHyprOpenGL->renderRect(bg.round(), hc, (int)std::round(6.0 * scale), 2.0f);
        }

        if (!**PICONONHOVER || (**PICONONHOVER && m_iButtonHoverState > 0))
            g_pHyprOpenGL->renderTexture(button.iconTex, pos, a);''', 1)

# 3. damageOnButtonHover 는 "어느 버튼이든 위에 있나" 하나만 기억해서
#    버튼 사이를 옮겨 다닐 때 다시 그리지 않는다 → 버튼별 상태로 비교
old = '''        if (hover != m_bButtonHovered) {
            m_bButtonHovered = hover;
            damageEntire();
        }'''
assert old in s, "damageOnButtonHover 기준점 없음"
s = s.replace(old, '''        const size_t IDX = &b - &g_pGlobalState->buttons[0];
        const bool   WAS = IDX < 32 && (m_iSekaiHover & (1u << IDX));
        if (hover != WAS) { // SEKAI_BUTTON_HOVER
            if (IDX < 32)
                m_iSekaiHover ^= (1u << IDX);
            m_bButtonHovered = hover;
            damageEntire();
        }''', 1)
# 창 밖으로 나가면 상태 초기화는 필요 없다 — 다음 이동 때 다시 비교한다
open(path, "w", encoding="utf-8").write(s)

# 헤더에 상태 변수
hpath = path.replace("barDeco.cpp", "barDeco.hpp")
h = open(hpath, encoding="utf-8").read()
if "m_iSekaiHover" not in h:
    old = "    bool                      m_bButtonHovered"
    if old not in h:
        old = "m_bButtonHovered"
        i = h.index(old)
        line_start = h.rfind("\n", 0, i) + 1
        line_end = h.index("\n", i)
        h = h[:line_end + 1] + "    uint32_t m_iSekaiHover = 0; // SEKAI_BUTTON_HOVER\n" + h[line_end + 1:]
    else:
        i = h.index(old)
        line_end = h.index("\n", i)
        h = h[:line_end + 1] + "    uint32_t                  m_iSekaiHover = 0; // SEKAI_BUTTON_HOVER\n" + h[line_end + 1:]
    open(hpath, "w", encoding="utf-8").write(h)
print("적용")
