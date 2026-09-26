#!/usr/bin/env python3
"""SekaiOS 로고 파일 생성 — 모양은 src/sekai-shell/sekaishell/logo.py 한 곳에서 온다.

  아이콘 테마:  usr/share/icons/hicolor/{scalable,16..256}/apps/sekaios.{svg,png}
  부팅 메뉴:    usr/share/sekai/refind/os_sekai.png (rEFInd OS 아이콘, 128px)
"""
import os
import sys

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(P, "src", "sekai-shell"))
from sekaishell import logo  # noqa: E402

OUT = os.path.join(P, "src", "sekai-desktop", "usr", "share")


def png(path, size, pad=0.0):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    inner = size * (1 - 2 * pad)
    cr.translate(size * pad, size * pad)
    logo.draw(cr, inner)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    surf.write_to_png(path)


svg_dir = os.path.join(OUT, "icons", "hicolor", "scalable", "apps")
os.makedirs(svg_dir, exist_ok=True)
for name in ("sekaios", "distributor-logo-sekaios"):
    with open(os.path.join(svg_dir, f"{name}.svg"), "w") as f:
        f.write(logo.svg(128))
for n in (16, 22, 24, 32, 48, 64, 128, 256):
    png(os.path.join(OUT, "icons", "hicolor", f"{n}x{n}", "apps", "sekaios.png"), n, pad=0.04)
# rEFInd 는 128px OS 아이콘을 쓴다. 다른 OS 아이콘들과 비슷한 여백을 준다.
png(os.path.join(OUT, "sekai", "refind", "os_sekai.png"), 128, pad=0.14)
# Plymouth 부팅 화면 — 로고와 진행 점
pdir = os.path.join(OUT, "plymouth", "themes", "sekai")
png(os.path.join(pdir, "logo.png"), 160, pad=0.06)
dot = cairo.ImageSurface(cairo.FORMAT_ARGB32, 12, 12)
c = cairo.Context(dot)
c.arc(6, 6, 4.5, 0, 6.2832)
c.set_source_rgb(*logo.TEAL)
c.fill()
dot.write_to_png(os.path.join(pdir, "dot.png"))


# fastfetch 로고 — 글자 한 칸을 2×2 로 나눈 사분면 블록으로. $1 = 강조색(틸), $2 = 분홍 (색은
#   etc/xdg/fastfetch/config.jsonc 가 정한다). 터미널 글자 칸은 폭이 높이의 절반쯤이라 세로를 반으로 줄인다
QUAD = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"          # 번호 = 왼위 1 | 오른위 2 | 왼아래 4 | 오른아래 8


def fastfetch_logo(cols=38, aspect=0.5, scale=10):
    sw = cols * 2                              # 가로 작은 칸 수
    sh = round(sw * aspect)                    # 세로 작은 칸 수
    R = sw * scale
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, R, R)
    logo.draw(cairo.Context(surf), R)
    surf.flush()
    data, stride = surf.get_data(), surf.get_stride()
    cw, ch = R / sw, R / sh

    def sample(i, j):
        """작은 칸 (i, j) → None · 1(틸) · 2(분홍)"""
        a = r = g = n = 0
        for y in range(int(j * ch), int((j + 1) * ch)):
            row = y * stride
            for x in range(int(i * cw), int((i + 1) * cw)):
                b0, g0, r0, a0 = data[row + x * 4:row + x * 4 + 4]
                a, r, g, n = a + a0, r + r0, g + g0, n + 1
        if not n or a / n < 0.45 * 255:
            return None
        return 2 if r > g else 1

    grid = [[sample(i, j) for i in range(sw)] for j in range(sh)]
    lines = []
    for cy in range(sh // 2):
        out, cur = [], None
        for cx in range(cols):
            cells = [grid[cy * 2][cx * 2], grid[cy * 2][cx * 2 + 1],
                     grid[cy * 2 + 1][cx * 2], grid[cy * 2 + 1][cx * 2 + 1]]
            filled = [c for c in cells if c]
            if not filled:
                out.append(" ")
                continue
            color = max((1, 2), key=filled.count)      # 한 칸에 두 색이면 많은 쪽만
            bits = sum(1 << k for k, c in enumerate(cells) if c == color)
            if color != cur:
                out.append(f"${color}")
                cur = color
            out.append(QUAD[bits])
        lines.append("".join(out).rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


fdir = os.path.join(OUT, "sekai", "fastfetch")
os.makedirs(fdir, exist_ok=True)
with open(os.path.join(fdir, "logo.txt"), "w", encoding="utf-8") as f:
    f.write(fastfetch_logo())
print("로고 파일 생성 완료")
