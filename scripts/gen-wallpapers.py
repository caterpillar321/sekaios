#!/usr/bin/env python3
"""SekaiOS 기본 배경화면 생성기.

사진 대신 코드로 그린다 — 저작권 걱정이 없고, 해상도·색을 바꿔 다시 뽑을 수 있다.

구성 (아래에서 위로)
  1. 세로 그라데이션 바탕
  2. 번지는 빛 (방사형 그라데이션, 더하기 합성)
  3. 오선지처럼 흐르는 다섯 줄 — 初音(첫 소리)
  4. 줄 위의 작은 빛 알갱이
  5. 가장자리 어둡게 (비네트)
  6. 미세한 노이즈 — 어두운 그라데이션의 계단 현상(밴딩)을 없앤다

사용법:  gen-wallpapers.py <출력 디렉터리> [가로 세로]
"""
import math
import os
import random
import sys

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


THEMES = {
    # 이름: (바탕 위, 바탕 아래, [(빛 색, 중심 x, 중심 y, 반지름, 세기)], 줄 색, 알갱이 색)
    "hatsune": ("#111216", "#0a0b0d",
                [("#39c5bb", 0.16, 0.30, 0.66, 0.34),
                 ("#2a6fdb", 0.66, 0.02, 0.46, 0.15),
                 ("#e0467c", 0.95, 0.98, 0.48, 0.12)],
                "#bff5f0", "#e8fffc"),
    "sakura":  ("#141114", "#0c0a0c",
                [("#e0467c", 0.20, 0.28, 0.62, 0.28),
                 ("#f29bb8", 0.70, 0.04, 0.40, 0.10),
                 ("#39c5bb", 0.94, 0.96, 0.46, 0.13)],
                "#ffd6e4", "#fff0f5"),
    "midnight": ("#0f1117", "#08090d",
                 [("#4b5bdc", 0.18, 0.26, 0.64, 0.30),
                  ("#8a5cf5", 0.72, 0.06, 0.44, 0.16),
                  ("#39c5bb", 0.92, 0.96, 0.44, 0.10)],
                 "#d6dcff", "#f0f2ff"),
}


def staff_y(x, W, H, k, phase):
    """k 번째 줄(0..4)의 x 위치에서의 높이. 두 사인파를 겹쳐 자연스럽게 흐르게."""
    t = x / W
    base = H * 0.60 + k * H * 0.013
    return (base
            + math.sin(t * math.pi * 1.35 + phase + k * 0.10) * H * 0.075
            + math.sin(t * math.pi * 3.1 + phase * 0.6) * H * 0.018)


def draw(name, W, H, out_dir):
    top, bottom, glows, line_col, spark_col = THEMES[name]
    rnd = random.Random(f"sekai-{name}")      # 매번 같은 그림이 나오게
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)

    # 1. 바탕
    g = cairo.LinearGradient(0, 0, 0, H)
    g.add_color_stop_rgb(0, *hexrgb(top))
    g.add_color_stop_rgb(1, *hexrgb(bottom))
    cr.set_source(g)
    cr.paint()

    # 2. 빛
    cr.set_operator(cairo.OPERATOR_ADD)
    for col, cx, cy, r, a in glows:
        rgb = hexrgb(col)
        rg = cairo.RadialGradient(cx * W, cy * H, 0, cx * W, cy * H, r * W)
        # 가우스에 가까운 감쇠 — 선형이면 가장자리에 둥근 테가 보인다
        for i in range(11):
            p = i / 10
            rg.add_color_stop_rgba(p, *rgb, a * math.exp(-4.2 * p * p))
        cr.set_source(rg)
        cr.paint()

    # 3. 흐르는 다섯 줄 — 먼저 넓고 옅은 빛띠, 그 위에 가는 선
    phase = {"hatsune": 0.4, "sakura": 1.3, "midnight": 2.2}[name]
    lc = hexrgb(line_col)
    glow_col = hexrgb(glows[0][0])

    def path(k):
        cr.new_path()
        steps = 240
        for i in range(steps + 1):
            x = -W * 0.05 + (W * 1.1) * i / steps
            y = staff_y(x, W, H, k, phase)
            (cr.move_to if i == 0 else cr.line_to)(x, y)

    for width, alpha in ((H * 0.070, 0.035), (H * 0.034, 0.045), (H * 0.014, 0.065)):
        path(2)
        cr.set_source_rgba(*glow_col, alpha)
        cr.set_line_width(width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.stroke()

    cr.set_operator(cairo.OPERATOR_OVER)
    for k in range(5):
        path(k)
        # 가운데 줄이 가장 밝고 바깥으로 갈수록 옅게
        a = 0.24 - abs(k - 2) * 0.04
        lg = cairo.LinearGradient(0, 0, W, 0)       # 양 끝은 사라지게
        lg.add_color_stop_rgba(0.00, *lc, 0)
        lg.add_color_stop_rgba(0.18, *lc, a)
        lg.add_color_stop_rgba(0.82, *lc, a)
        lg.add_color_stop_rgba(1.00, *lc, 0)
        cr.set_source(lg)
        cr.set_line_width(max(1.0, H / 1200))
        cr.stroke()

    # 4. 빛 알갱이 — 줄 위에 드문드문 (음표 자리)
    sc = hexrgb(spark_col)
    cr.set_operator(cairo.OPERATOR_ADD)
    for _ in range(30):
        x = rnd.uniform(0.12, 0.92) * W
        k = rnd.randrange(5)
        y = staff_y(x, W, H, k, phase)
        r = rnd.uniform(0.8, 2.0) * H / 1080
        a = rnd.uniform(0.25, 0.75)
        halo = cairo.RadialGradient(x, y, 0, x, y, r * 7)
        halo.add_color_stop_rgba(0, *sc, a * 0.30)
        halo.add_color_stop_rgba(1, *sc, 0)
        cr.set_source(halo)
        cr.arc(x, y, r * 7, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*sc, a)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.fill()

    # 5. 비네트
    cr.set_operator(cairo.OPERATOR_OVER)
    vg = cairo.RadialGradient(W * 0.5, H * 0.5, H * 0.35, W * 0.5, H * 0.5, W * 0.72)
    vg.add_color_stop_rgba(0, 0, 0, 0, 0)
    vg.add_color_stop_rgba(1, 0, 0, 0, 0.45)
    cr.set_source(vg)
    cr.paint()

    # 6. 노이즈 (±1~2 단계) — 밴딩 제거
    tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, 256, 256)
    buf = tile.get_data()
    noise = os.urandom(256 * 256)
    for i in range(256 * 256):
        v = noise[i]
        on = 255 if v & 1 else 0          # 흰 점 또는 검은 점
        a = 5 + (v >> 5)                  # 아주 옅게 (5~12 / 255)
        c = on * a // 255                 # premultiplied
        o = i * 4
        buf[o] = buf[o + 1] = buf[o + 2] = c
        buf[o + 3] = a
    tile.mark_dirty()
    pat = cairo.SurfacePattern(tile)
    pat.set_extend(cairo.EXTEND_REPEAT)
    cr.set_source(pat)
    cr.paint()

    # 저장: PNG 로 그리고 JPEG 로 (노이즈가 있는 그림은 PNG 가 수십 MB 가 된다)
    png = os.path.join(out_dir, f".{name}.png")
    surf.write_to_png(png)
    pb = GdkPixbuf.Pixbuf.new_from_file(png)
    jpg = os.path.join(out_dir, f"{name}.jpg")
    pb.savev(jpg, "jpeg", ["quality"], ["92"])
    os.remove(png)
    print(f"  {jpg}  {os.path.getsize(jpg) // 1024} KiB")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (3840, 2160)
    os.makedirs(out, exist_ok=True)
    for n in THEMES:
        draw(n, W, H, out)
