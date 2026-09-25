#!/usr/bin/env python3
"""SekaiOS 기본 배경화면 생성기.

사진 대신 코드로 그린다 — 저작권 걱정이 없고, 해상도·색을 바꿔 다시 뽑을 수 있다.

구성 (아래에서 위로)
  1. 세로 그라데이션 바탕
  2. 번지는 빛 (방사형 그라데이션, 더하기 합성)
  3. 오선지처럼 흐르는 다섯 줄 — 初音(첫 소리)
  4. 줄 위의 작은 빛 알갱이
  5. 가장자리 어둡게 (비네트)
  6. 디더링 — 실수로 계산한 그림을 8비트로 바꿀 때 계단 현상(밴딩)이 생기지 않게

사용법:  gen-wallpapers.py <출력 디렉터리> [가로 세로]
"""
import math
import os
import random
import sys

import cairo
import numpy as np            # 빌드 도구: python3-numpy python3-pil
from PIL import Image


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
    """부드러운 부분(바탕·빛·비네트)은 64비트 실수로 계산하고, 선·알갱이만 cairo 로 그려 얹는다.
    마지막에 한 번만 디더링하며 8비트로 — cairo(8비트)로 빛을 여러 겹 더하면 겹칠 때마다 반올림돼
    어두운 그라데이션에 계단·얼룩이 생겼다."""
    top, bottom, glows, line_col, spark_col = THEMES[name]
    rnd = random.Random(f"sekai-{name}")      # 매번 같은 그림이 나오게
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    ys += 0.5
    xs += 0.5

    # 1. 바탕 — 세로 그라데이션
    t = (ys / H)[..., None]
    img = np.array(hexrgb(top)) * (1 - t) + np.array(hexrgb(bottom)) * t

    # 2. 빛 — 가우스 감쇠, 더하기
    for col, cx, cy, r, a in glows:
        p2 = ((xs - cx * W) ** 2 + (ys - cy * H) ** 2) / (r * W) ** 2
        img += np.array(hexrgb(col)) * (a * np.exp(-4.2 * p2))[..., None]
    np.clip(img, 0, 1, out=img)

    # 3. 가운데 줄을 따라 번지는 빛띠 — 가우스로 부드럽게 (옅은 선을 겹쳐 그리면 가장자리가 계단진다)
    phase = {"hatsune": 0.4, "sakura": 1.3, "midnight": 2.2}[name]
    glow_col = np.array(hexrgb(glows[0][0]))
    t_ = xs[0] / W
    mid = (H * 0.60 + 2 * H * 0.013
           + np.sin(t_ * math.pi * 1.35 + phase + 2 * 0.10) * H * 0.075
           + np.sin(t_ * math.pi * 3.1 + phase * 0.6) * H * 0.018)      # staff_y(x, k=2)
    band = 0.13 * np.exp(-((ys - mid[None, :]) / (H * 0.018)) ** 2)
    img = img * (1 - band[..., None]) + glow_col * band[..., None]
    del band

    # 4. 흐르는 다섯 줄과 빛 알갱이 — 가는 것들이라 8비트 cairo 로 그려도 계단이 없다
    layer = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(layer)
    lc = hexrgb(line_col)

    def path(k):
        cr.new_path()
        steps = 240
        for i in range(steps + 1):
            x = -W * 0.05 + (W * 1.1) * i / steps
            y = staff_y(x, W, H, k, phase)
            (cr.move_to if i == 0 else cr.line_to)(x, y)

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
    layer.flush()
    # cairo ARGB32 = 미리 곱한(premultiplied) BGRA
    lay = np.frombuffer(layer.get_data(), np.uint8).reshape(H, layer.get_stride() // 4, 4)[:, :W]
    lay = lay.astype(np.float64) / 255
    img = lay[..., 2::-1] + img * (1 - lay[..., 3:4])
    del lay

    # 5. 비네트 — 가장자리 어둡게
    d = np.sqrt((xs - W / 2) ** 2 + (ys - H / 2) ** 2)
    v = np.clip((d - H * 0.35) / (W * 0.72 - H * 0.35), 0, 1) * 0.45
    img *= (1 - v)[..., None]
    del xs, ys, d, v

    # 6. 디더링 — 삼각 분포 노이즈(±1 단계)를 더한 뒤 8비트로. 눈에는 안 보이고 계단만 없앤다
    rng = np.random.default_rng(abs(hash(name)) % (2 ** 32))
    img = img * 255 + rng.random(img.shape) - rng.random(img.shape)
    out = np.clip(np.rint(img), 0, 255).astype(np.uint8)

    # 저장: 4:4:4 JPEG (색 정보를 줄이지 않는다 — 4:2:0 이면 어두운 색 그라데이션이 얼룩진다)
    jpg = os.path.join(out_dir, f"{name}.jpg")
    Image.fromarray(out, "RGB").save(jpg, "JPEG", quality=95, subsampling=0, optimize=True)
    print(f"  {jpg}  {os.path.getsize(jpg) // 1024} KiB")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (3840, 2160)
    os.makedirs(out, exist_ok=True)
    for n in THEMES:
        draw(n, W, H, out)
