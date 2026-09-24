#!/usr/bin/env python3
"""hyprbars 패치 — 창 조작 버튼(최소화·최대화·닫기)을 벡터로 그린다.

업스트림은 버튼 아이콘을 "sans" 글꼴의 글자로 그린다. 그러면
  — ▢ ✕ 가 서로 다른 글꼴에서 와서 굵기·크기·위치가 제각각이 된다.
아이콘 이름이 sekai:min / sekai:max / sekai:close 이면 글꼴 대신
cairo 선으로 윈도우 11 식 아이콘(같은 굵기 1px, 같은 크기)을 그린다.
그 밖의 아이콘 문자열은 원래대로 글자로 그린다. 멱등.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK = "SEKAI_VECTOR_CAPTION_ICONS"
if MARK in s:
    print("이미 적용됨"); sys.exit(0)

anchor = '''    // draw title using Pango
    PangoLayout* layout = pango_cairo_create_layout(CAIRO);
    pango_layout_set_text(layout, text.c_str(), -1);

    PangoFontDescription* fontDesc = pango_font_description_from_string("sans");'''
assert anchor in s, "renderText 기준점 없음 (업스트림이 바뀜)"

vector = '''    // SEKAI_VECTOR_CAPTION_ICONS — 창 조작 버튼은 글꼴이 아니라 선으로 그린다
    if (text == "sekai:min" || text == "sekai:max" || text == "sekai:close") {
        const double S  = std::round(std::min(bufferSize.x, bufferSize.y) * 0.625); // 아이콘 한 변
        const double LW = std::max(1.0, std::round((double)scale));                // 선 굵기 (배율 1 = 1px)
        const double X0 = std::round((bufferSize.x - S) / 2.0);
        const double Y0 = std::round((bufferSize.y - S) / 2.0);
        const double HP = LW / 2.0;                                                  // 픽셀 격자 정렬
        cairo_set_source_rgba(CAIRO, color.r, color.g, color.b, color.a);
        cairo_set_line_width(CAIRO, LW);
        if (text == "sekai:min") {
            cairo_set_line_cap(CAIRO, CAIRO_LINE_CAP_BUTT);
            const double y = std::round(bufferSize.y / 2.0) + HP;
            cairo_move_to(CAIRO, X0, y);
            cairo_line_to(CAIRO, X0 + S, y);
        } else if (text == "sekai:max") {
            cairo_rectangle(CAIRO, X0 + HP, Y0 + HP, S - LW, S - LW);
        } else {
            cairo_set_line_cap(CAIRO, CAIRO_LINE_CAP_ROUND);
            cairo_move_to(CAIRO, X0 + HP, Y0 + HP);
            cairo_line_to(CAIRO, X0 + S - HP, Y0 + S - HP);
            cairo_move_to(CAIRO, X0 + S - HP, Y0 + HP);
            cairo_line_to(CAIRO, X0 + HP, Y0 + S - HP);
        }
        cairo_stroke(CAIRO);
    } else {
'''
s = s.replace(anchor, vector + anchor, 1)

close_anchor = '''    pango_cairo_show_layout(CAIRO, layout);

    g_object_unref(layout);

    cairo_surface_flush(CAIROSURFACE);'''
assert s.count(close_anchor) >= 1, "renderText 끝 기준점 없음"
# renderText 안의 첫 번째(= 버튼 아이콘용) 것만 닫는다
s = s.replace(close_anchor, '''    pango_cairo_show_layout(CAIRO, layout);

    g_object_unref(layout);
    } // SEKAI_VECTOR_CAPTION_ICONS

    cairo_surface_flush(CAIROSURFACE);''', 1)
open(path, "w", encoding="utf-8").write(s)
print("적용")
