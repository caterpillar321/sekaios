#!/usr/bin/env python3
"""기본 화면 모드(X11)용 xfwm4 테마 "Sekai" — Hyprland 의 제목 표시줄(hyprbars)과 같은 모양.

  src/sekai-desktop/usr/share/themes/Sekai/xfwm4/
    themerc, top-*/title-*/left/right/bottom-*.png, close/maximize/hide-*.png
  제목줄 34px, 위 모서리 둥글게(8), 버튼 46×34 (윈도우 11 크기), 닫기에 올리면 빨강.
사용법: scripts/gen-xfwm4-theme.py
"""
import math
import os

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(HERE)
OUT = os.path.join(P, "src", "sekai-desktop", "usr", "share", "themes", "Sekai", "xfwm4")

H = 34          # 제목줄 높이 (hyprbars bar_height 와 같게)
R = 8           # 위 모서리 반지름
BW = 46         # 버튼 너비
B = 2           # 좌우·아래 테두리 두께 (잡아서 크기 바꾸는 곳)
BG = {"active": (0x2c, 0x2c, 0x30), "inactive": (0x24, 0x24, 0x28)}
FG = {"active": (0xe6, 0xe6, 0xea), "inactive": (0x8a, 0x8a, 0x94)}
EDGE = {"active": (0x39, 0xc5, 0xbb, 0.45), "inactive": (0xff, 0xff, 0xff, 0.06)}


def rgb(c, a=1.0):
    return (c[0] / 255, c[1] / 255, c[2] / 255, a)


def save(surf, name):
    surf.write_to_png(os.path.join(OUT, name))


def solid(name, w, h, col):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(s)
    c.set_source_rgba(*rgb(col))
    c.paint()
    save(s, name)


def corner(name, state, right):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, R, H)
    c = cairo.Context(s)
    c.set_source_rgba(*rgb(BG[state]))
    # 둥근 모서리 사각형의 한쪽 조각 (바깥은 투명 — 합성기가 둥글게 보여 준다)
    if right:
        c.move_to(0, 0)
        c.arc(0, R, R, -math.pi / 2, 0)
        c.line_to(R, H)
        c.line_to(0, H)
    else:
        c.move_to(R, 0)
        c.arc_negative(R, R, R, -math.pi / 2, math.pi)
        c.line_to(0, H)
        c.line_to(R, H)
    c.close_path()
    c.fill()
    save(s, name)


def icon(c, kind, col, x0, size=10):
    y0 = (H - size) / 2
    c.set_source_rgba(*col)
    c.set_line_width(1)
    if kind == "hide":
        y = round(H / 2) + 0.5
        c.move_to(x0, y)
        c.line_to(x0 + size, y)
    elif kind == "maximize":
        c.rectangle(x0 + 0.5, y0 + 0.5, size - 1, size - 1)
    elif kind == "maximize-toggled":
        c.rectangle(x0 + 0.5, y0 + 2.5, size - 3, size - 3)
        c.move_to(x0 + 2.5, y0 + 2.5)
        c.line_to(x0 + 2.5, y0 + 0.5)
        c.line_to(x0 + size - 0.5, y0 + 0.5)
        c.line_to(x0 + size - 0.5, y0 + size - 2.5)
        c.line_to(x0 + size - 2.5, y0 + size - 2.5)
    else:  # close
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        c.move_to(x0 + 0.5, y0 + 0.5)
        c.line_to(x0 + size - 0.5, y0 + size - 0.5)
        c.move_to(x0 + size - 0.5, y0 + 0.5)
        c.line_to(x0 + 0.5, y0 + size - 0.5)
    c.stroke()


def button(kind, state, look):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, BW, H)
    c = cairo.Context(s)
    c.set_source_rgba(*rgb(BG[state]))
    c.paint()
    fg = rgb(FG[state])
    if look in ("prelight", "pressed"):
        if kind == "close":
            c.set_source_rgba(0xc4 / 255, 0x2b / 255, 0x1c / 255, 1.0 if look == "prelight" else 0.8)
            fg = (1, 1, 1, 1)
        else:
            c.set_source_rgba(1, 1, 1, 0.10 if look == "prelight" else 0.06)
        c.rectangle(0, 0, BW, H)
        c.fill()
    icon(c, kind, fg, (BW - 10) / 2)
    save(s, f"{kind}-{state}.png" if look == "normal" else f"{kind}-{look}.png")


THEMERC = """# SekaiOS 기본 화면 모드 창 테마 — scripts/gen-xfwm4-theme.py 가 만든다
button_offset=0
button_spacing=0
full_width_title=true
title_horizontal_offset=14
title_vertical_offset_active=0
title_vertical_offset_inactive=0
title_shadow_active=false
title_shadow_inactive=false
active_text_color=#e6e6ea
inactive_text_color=#8a8a94
active_text_shadow_color=#000000
inactive_text_shadow_color=#000000
shadow_delta_height=2
shadow_delta_width=0
shadow_delta_x=0
shadow_delta_y=-4
shadow_opacity=45
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    for st in ("active", "inactive"):
        corner(f"top-left-{st}.png", st, right=False)
        corner(f"top-right-{st}.png", st, right=True)
        for i in range(1, 6):
            solid(f"title-{i}-{st}.png", 2, H, BG[st])
        edge = EDGE[st]
        for n, (w, h) in {"left": (B, 8), "right": (B, 8), "bottom": (8, B),
                          "bottom-left": (B, B), "bottom-right": (B, B)}.items():
            s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            c = cairo.Context(s)
            c.set_source_rgba(edge[0] / 255, edge[1] / 255, edge[2] / 255, edge[3])
            c.paint()
            save(s, f"{n}-{st}.png")
        for kind in ("close", "maximize", "maximize-toggled", "hide"):
            button(kind, st, "normal")
    for kind in ("close", "maximize", "maximize-toggled", "hide"):
        button(kind, "active", "prelight")
        button(kind, "active", "pressed")
    with open(os.path.join(OUT, "themerc"), "w") as f:
        f.write(THEMERC)
    print(f"  {len(os.listdir(OUT))} 개 파일 → {OUT}")


if __name__ == "__main__":
    main()
