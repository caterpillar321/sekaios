#!/usr/bin/env python3
"""SekaiOS 기본 바탕화면 Origami — 머티리얼 디자인 식으로 45° 로 겹친 어두운 색종이 (2026-11-18 사용자와 정함)

코드로 그린다 (저작권 걱정 없이, 해상도를 바꿔 다시 뽑을 수 있게). 구성:
  · 짙은 청록·먹색 종이 여러 장이 45° 로 겹치고, 종이마다 아래로 옅은 그림자
  · 아래 가장자리에서 나와 큰 종이 밑으로 들어가는 민트 강조 띠 하나
  · 왼쪽 위(바탕화면 아이콘 자리)는 바탕만
  · 고운 종이 결 잡음 + 마지막 디더링 — 어두운 면에 계단이 없게 (PNG 로 저장, sekaishell/wallpaint.py 와 짝)

사용법:  gen-wallpaper-origami.py <출력 디렉터리> [가로 세로]
"""
import math
import os
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter


def hexf(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255


def to_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def render(W, H, layers, name, out, base="#0a1416", seed=3):
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    x, y = (xs + 0.5) / H, (ys + 0.5) / H              # 높이 기준 좌표
    s2 = math.sqrt(2)
    u = (x - y) / s2                                   # ↘ 방향
    v = (x + y) / s2                                   # ↗ 에 수직 (왼쪽 위 0 → 오른쪽 아래 큼)
    px = 1.0 / H
    img = np.broadcast_to(to_lin(hexf(base)), (H, W, 3)).copy()
    # 바탕에도 아주 옅은 세로 그라데이션 (위가 조금 밝게)
    img = img * (1.0 + 0.12 * (1 - y))[..., None]

    def half(val, lo=None, hi=None):
        m = np.ones_like(val)
        if lo is not None:
            m = m * np.clip((val - lo) / px + 0.5, 0, 1)
        if hi is not None:
            m = m * np.clip((hi - val) / px + 0.5, 0, 1)
        return m

    for L in layers:
        m = np.ones_like(x)
        for axis, lo, hi in L["cut"]:
            m = m * half({"u": u, "v": v, "x": x, "y": y}[axis], lo, hi)
        if L.get("shadow", 1) > 0:
            # 종이 그림자 — 아래쪽으로 조금 밀고 흐리게, 바탕을 어둡게
            sh = gaussian_filter(m, sigma=H * 0.010)
            k = int(H * 0.006)
            sh = np.concatenate([np.zeros((k, sh.shape[1])), sh[:-k]], axis=0)   # 아래로 밀기 (넘친 것은 버린다)
            img = img * (1 - 0.55 * L.get("shadow", 1) * sh * (1 - m))[..., None]
        col = to_lin(hexf(L["color"]))
        face = np.broadcast_to(col, img.shape).copy()
        if L.get("ribs"):                              # 골판지 줄무늬 — u 방향으로 가는 결
            per = L["ribs"] * px
            ph = (v / per) % 1.0
            rib = 0.5 + 0.5 * np.cos(ph * 2 * math.pi)
            face = face * (0.86 + 0.22 * rib)[..., None]
        # 종이 면에 아주 옅은 위→아래 명암 (빛이 위에서)
        face = face * (1.05 - 0.10 * y)[..., None]
        img = img * (1 - m[..., None]) + face * m[..., None]
    srgb = to_srgb(img)
    rng = np.random.default_rng(seed)
    grain = gaussian_filter(rng.normal(0, 1, (H, W)), 0.6) * (2.2 / 255)     # 종이 결 같은 고운 잡음
    srgb = srgb + grain[..., None]
    o = srgb * 255 + rng.random(srgb.shape) - rng.random(srgb.shape)
    o = np.clip(np.rint(o), 0, 255).astype(np.uint8)
    os.makedirs(out, exist_ok=True)
    Image.fromarray(o, "RGB").save(os.path.join(out, name + ".png"), optimize=True)
    print(os.path.join(out, name + ".png"))


# 색 — 아래(먼) 종이일수록 어둡게. 민트는 가는 띠 하나, 줄무늬 면은 어두운 청록
ORIGAMI = [
    dict(color="#0d1c1f", cut=[("u", 0.05, None)]),                                   # 오른쪽 위 반쪽
    dict(color="#11262a", cut=[("v", 0.95, 1.40), ("u", -0.40, None)]),               # 가로지르는 띠 종이
    dict(color="#2aa69e", cut=[("v", 1.43, 1.465), ("u", None, 0.425)], shadow=0.8),  # 민트 띠 — 아래 가장자리에서 나와 세로 종이 밑으로
    dict(color="#142d31", cut=[("u", 0.45, None), ("v", 1.20, None)]),                # 세로 큰 종이 — 오른쪽 끝까지 (민트 띠를 덮는다)
    dict(color="#183c41", cut=[("u", -0.6, 0.0), ("v", 1.55, None)]),                 # 왼쪽 아래 밝은 종이
    dict(color="#1a4045", cut=[("u", 1.00, 1.045), ("v", 0.95, None)], shadow=0.7),  # 오른쪽 위 — 큰 종이 위 조금 밝은 띠 (틈 없이)
    dict(color="#163539", cut=[("u", 1.045, None), ("v", 0.95, None)], shadow=0.5),  # 그 너머 구석 종이 (한 장)
    dict(color="#12313a", cut=[("u", 0.40, 0.44), ("v", 1.20, None)], shadow=0.5),   # 얇은 띠
]

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (3840, 2160)
    render(W, H, ORIGAMI, "origami", out)
