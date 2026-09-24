#!/usr/bin/env python3
"""SekaiOS GRUB 부팅 메뉴 테마 생성기.

  src/sekai-desktop/usr/share/grub/themes/sekai/
    theme.txt            배치
    background.png       배경 (배경화면 hatsune 을 어둡게 + 가운데 빛)
    logo.png             두 별 로고
    select_*.png         고른 항목의 둥근 강조 상자 (9조각)
    icons/*.png          항목 아이콘 (sekaios, windows, recovery, efi, os)
    *.pf2                Pretendard — 메뉴에 쓰는 글자만 골라 담는다 (한글 전체를 넣으면 수 MB)

필요: python3-cairo, grub-mkfont, Pretendard (fonts-pretendard 또는 rootfs 안)
사용법: scripts/gen-grub-theme.py
"""
import math
import os
import subprocess
import sys
import tempfile

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(P, "src", "sekai-shell"))
from sekaishell import logo  # noqa: E402

OUT = os.path.join(P, "src", "sekai-desktop", "usr", "share", "grub", "themes", "sekai")
WALL = os.path.join(P, "src", "sekai-desktop", "usr", "share", "backgrounds", "sekai", "hatsune.jpg")
FONT_DIRS = [os.path.join(P, "rootfs", "usr", "share", "fonts", "opentype", "pretendard"),
             "/usr/share/fonts/opentype/pretendard"]
ACCENT = (0x39 / 255, 0xc5 / 255, 0xbb / 255)
W, H = 1920, 1080

# 메뉴·안내에 나오는 글 — 이 글자들만 글꼴에 넣는다 (09_sekaios 와 theme.txt 의 문구)
TEXTS = [
    "SekaiOS", "복구 모드", "사용해 보기 · 설치", "자세한 부팅 기록", "(UEFI)", "기본 화면 모드", "이전 커널", "펌웨어 설정", "UEFI Firmware Settings",
    "Windows Boot Manager", "Windows", "초 후 자동으로 시작합니다", "선택", "시작", "편집",
    "↑↓ Enter e c", "0123456789", "()[]-—·.,:/_+%",
]


def fonts():
    for d in FONT_DIRS:
        if os.path.exists(os.path.join(d, "Pretendard-Regular.otf")):
            return d
    sys.exit("Pretendard 글꼴을 찾지 못했습니다 (fonts-pretendard 또는 rootfs)")


def mkfont(src, size, out, name):
    # ASCII 전부 + 문구에 나오는 글자
    chars = set(chr(c) for c in range(0x20, 0x7f))
    for t in TEXTS:
        chars.update(t)
    ranges = []
    for c in sorted(ord(x) for x in chars):
        ranges.append(f"0x{c:x}-0x{c:x}")
    subprocess.run(["grub-mkfont", "-n", name, "-s", str(size), "-o", out, "-r", ",".join(ranges), src],
                   check=True)


def background():
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    try:
        img = cairo.ImageSurface.create_from_png(os.path.join(P, "src", "sekai-desktop", "usr",
                                                               "share", "sekai", "refind", "background.png"))
        cr.scale(W / img.get_width(), H / img.get_height())
        cr.set_source_surface(img, 0, 0)
        cr.paint()
        cr.identity_matrix()
    except Exception:
        cr.set_source_rgb(0.08, 0.08, 0.09)
        cr.paint()
    # 메뉴가 놓일 가운데를 살짝 어둡게 — 글자가 잘 읽히게
    g = cairo.RadialGradient(W / 2, H * 0.55, 0, W / 2, H * 0.55, W * 0.45)
    g.add_color_stop_rgba(0, 0, 0, 0, 0.35)
    g.add_color_stop_rgba(1, 0, 0, 0, 0.0)
    cr.set_source(g)
    cr.paint()
    surf.write_to_png(os.path.join(OUT, "background.png"))


def logo_png(path, size):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    logo.draw(cr, size)
    surf.write_to_png(path)


def select_pieces():
    """고른 항목 상자 — 9조각 (모서리 반지름 12, 강조색 테두리)"""
    R = 12
    size = R * 2 + 4
    big = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(big)
    x = y = 0.5
    w = h = size - 1
    cr.new_sub_path()
    cr.arc(x + w - R, y + R, R, -math.pi / 2, 0)
    cr.arc(x + w - R, y + h - R, R, 0, math.pi / 2)
    cr.arc(x + R, y + h - R, R, math.pi / 2, math.pi)
    cr.arc(x + R, y + R, R, math.pi, 1.5 * math.pi)
    cr.close_path()
    cr.set_source_rgba(*ACCENT, 0.16)
    cr.fill_preserve()
    cr.set_source_rgba(*ACCENT, 0.85)
    cr.set_line_width(1.5)
    cr.stroke()
    m = size - 2 * R
    parts = {"nw": (0, 0, R, R), "n": (R, 0, m, R), "ne": (R + m, 0, R, R),
             "w": (0, R, R, m), "c": (R, R, m, m), "e": (R + m, R, R, m),
             "sw": (0, R + m, R, R), "s": (R, R + m, m, R), "se": (R + m, R + m, R, R)}
    for name, (px, py, pw, ph) in parts.items():
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, pw, ph)
        c = cairo.Context(s)
        c.set_source_surface(big, -px, -py)
        c.paint()
        s.write_to_png(os.path.join(OUT, f"select_{name}.png"))


def icon(name, draw):
    S = 64
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    cr = cairo.Context(surf)
    draw(cr, S)
    surf.write_to_png(os.path.join(OUT, "icons", f"{name}.png"))


def draw_windows(cr, S):
    g, pad = S * 0.05, S * 0.14
    c = (S - 2 * pad - g) / 2
    cr.set_source_rgb(0.30, 0.64, 0.95)
    for ix in (0, 1):
        for iy in (0, 1):
            cr.rectangle(pad + ix * (c + g), pad + iy * (c + g), c, c)
    cr.fill()


def draw_recovery(cr, S):
    cr.set_source_rgb(0.95, 0.78, 0.43)
    cr.set_line_width(S * 0.09)
    cr.arc(S / 2, S / 2, S * 0.3, math.radians(-60), math.radians(250))
    cr.stroke()
    cr.move_to(S * 0.62, S * 0.14)
    cr.line_to(S * 0.78, S * 0.26)
    cr.line_to(S * 0.60, S * 0.36)
    cr.close_path()
    cr.fill()


def draw_efi(cr, S):
    cr.set_source_rgb(0.75, 0.75, 0.8)
    cr.set_line_width(S * 0.07)
    cr.rectangle(S * 0.24, S * 0.24, S * 0.52, S * 0.52)
    cr.stroke()
    for k in range(4):
        o = S * (0.32 + k * 0.12)
        for (a, b, c2, d) in ((o, S * 0.12, o, S * 0.24), (o, S * 0.76, o, S * 0.88),
                              (S * 0.12, o, S * 0.24, o), (S * 0.76, o, S * 0.88, o)):
            cr.move_to(a, b)
            cr.line_to(c2, d)
    cr.set_line_width(S * 0.05)
    cr.stroke()


def draw_os(cr, S):
    cr.set_source_rgb(0.7, 0.7, 0.75)
    cr.arc(S / 2, S / 2, S * 0.32, 0, 2 * math.pi)
    cr.set_line_width(S * 0.08)
    cr.stroke()


THEME = """# SekaiOS 부팅 메뉴 — scripts/gen-grub-theme.py 가 만든다
title-text: ""
desktop-image: "background.png"
desktop-image-scale-method: "stretch"
desktop-color: "#0f1113"
terminal-font: "SekaiText Regular 18"
terminal-box: "select_*.png"

+ image {
    left = 50%-40
    top = 16%
    width = 80
    height = 80
    file = "logo.png"
}

+ boot_menu {
    left = 50%-320
    top = 32%
    width = 640
    height = 46%
    item_font = "SekaiText Regular 22"
    selected_item_font = "SekaiBold Regular 22"
    item_color = "#c8c8d0"
    selected_item_color = "#ffffff"
    icon_width = 32
    icon_height = 32
    item_icon_space = 16
    item_height = 52
    item_padding = 14
    item_spacing = 6
    selected_item_pixmap_style = "select_*.png"
    scrollbar = false
}

+ label {
    id = "__timeout__"
    left = 0
    top = 84%
    width = 100%
    align = "center"
    color = "#8a8a94"
    font = "SekaiText Regular 18"
    text = "%d초 후 자동으로 시작합니다"
}

+ label {
    left = 0
    top = 89%
    width = 100%
    align = "center"
    color = "#5c5c66"
    font = "SekaiText Regular 18"
    text = "↑↓ 선택 · Enter 시작 · e 편집"
}
"""


def main():
    os.makedirs(os.path.join(OUT, "icons"), exist_ok=True)
    fd = fonts()
    background()
    logo_png(os.path.join(OUT, "logo.png"), 80)
    select_pieces()
    logo_png(os.path.join(OUT, "icons", "sekaios.png"), 64)
    icon("windows", draw_windows)
    icon("recovery", draw_recovery)
    icon("efi", draw_efi)
    icon("os", draw_os)
    mkfont(os.path.join(fd, "Pretendard-Regular.otf"), 22, os.path.join(OUT, "pretendard-regular-22.pf2"), "SekaiText")
    mkfont(os.path.join(fd, "Pretendard-SemiBold.otf"), 22, os.path.join(OUT, "pretendard-semibold-22.pf2"), "SekaiBold")
    mkfont(os.path.join(fd, "Pretendard-Regular.otf"), 18, os.path.join(OUT, "pretendard-regular-18.pf2"), "SekaiText")
    with open(os.path.join(OUT, "theme.txt"), "w", encoding="utf-8") as f:
        f.write(THEME)
    for n in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, n)
        if os.path.isfile(p):
            print(f"  {n:32s} {os.path.getsize(p) // 1024} KiB")


if __name__ == "__main__":
    main()
