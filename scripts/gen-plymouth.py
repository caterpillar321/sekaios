#!/usr/bin/env python3
"""SekaiOS 부팅 화면(Plymouth two-step) 이미지 생성기.

two-step 플러그인은 화면(모니터)마다 따로 가운데에 그린다 — script 테마는 0번 화면 크기로만
가운데를 잡아서, 해상도가 다른 모니터가 둘이면 로고가 한쪽(우하단)으로 쏠렸다.
two-step 은 로고를 스스로 움직일 수 없으므로, 로고가 숨 쉬고 점 세 개가 차례로 빛나는 모습을
throbber 프레임으로 미리 그린다. throbber 는 모든 프레임을 2초에 한 바퀴 돈다
(plymouth ply-throbber.c THROBBER_DURATION) — 로고 한 번, 점 두 번 숨 쉬게.

사용법: gen-plymouth.py <테마 디렉터리>   (logo.png·dot.png 가 있는 곳)
"""
import math
import os
import sys

from PIL import Image, ImageDraw

d = sys.argv[1]
logo = Image.open(os.path.join(d, "logo.png")).convert("RGBA")
dot = Image.open(os.path.join(d, "dot.png")).convert("RGBA")
N = 48                                   # 2초에 48장 (24fps)
LW, LH = logo.size
GAP = 60                                 # 로고 아래와 점 사이
W, H = LW, LH + GAP + dot.size[1]


def with_alpha(im, a):
    r, g, b, al = im.split()
    return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a + 0.5))))


for old in os.listdir(d):
    if old.startswith("throbber-"):
        os.remove(os.path.join(d, old))

for f in range(N):
    t = f / N                            # 0..1 (2초)
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    frame.alpha_composite(with_alpha(logo, 0.78 + 0.22 * math.sin(2 * math.pi * t)), (0, 0))
    for i in range(3):
        phase = max(0.0, math.sin(2 * math.pi * 2 * t - i * 1.1))
        x = W // 2 + (i - 1) * 20 - dot.size[0] // 2
        frame.alpha_composite(with_alpha(dot, 0.2 + 0.8 * phase), (x, LH + GAP))
    frame.save(os.path.join(d, f"throbber-{f + 1:04d}.png"), optimize=True)

# 암호 입력(디스크 암호화 등)에 쓰는 그림 — two-step 은 이것들이 없으면 테마를 불러오지 못한다
S = 4                                    # 크게 그려 줄여서 가장자리를 매끄럽게


def smooth(w, h, draw_fn):
    big = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(big), S)
    return big.resize((w, h), Image.LANCZOS)


smooth(300, 36, lambda g, s: g.rounded_rectangle((0, 0, 300 * s - 1, 36 * s - 1), radius=8 * s,
                                                  fill=(30, 30, 34, 235), outline=(57, 197, 187, 200),
                                                  width=2 * s)).save(os.path.join(d, "entry.png"))
smooth(10, 10, lambda g, s: g.ellipse((0, 0, 10 * s - 1, 10 * s - 1), fill=(241, 241, 243, 255))
       ).save(os.path.join(d, "bullet.png"))


def lock(g, s):
    g.rounded_rectangle((3 * s, 10 * s, 21 * s, 24 * s), radius=3 * s, fill=(200, 200, 208, 255))
    g.arc((6 * s, 1 * s, 18 * s, 17 * s), 180, 360, fill=(200, 200, 208, 255), width=3 * s)
    g.line((6 * s + s, 9 * s, 6 * s + s, 11 * s), fill=(200, 200, 208, 255), width=3 * s)
    g.line((18 * s - s, 9 * s, 18 * s - s, 11 * s), fill=(200, 200, 208, 255), width=3 * s)


smooth(24, 24, lock).save(os.path.join(d, "lock.png"))
print(f"  throbber {N}장 ({W}x{H}), entry·bullet·lock")
