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
print("로고 파일 생성 완료")
