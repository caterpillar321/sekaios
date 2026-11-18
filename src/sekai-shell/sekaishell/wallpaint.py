"""배경 그림을 화면 크기로 — 계단(밴딩) 없이.

어두운 그라데이션은 밝기 단계가 몇 개뿐이라 한 단계가 수십~수백 px 띠가 된다. 그림 파일에는 디더링(눈에 안 보이는
잡음)을 넣어 두지만, 화면 크기로 줄이며 8비트로 다시 반올림하면 잡음이 평균으로 사라져 띠가 되살아났다
(4K 그림 → 2560x1440, 2026-11-18 "모니터 따라 계단으로 보인다").

그래서: 실수(RGB96F) 그림에 고품질 필터로 줄여 소수점 밝기를 지키고, 8비트 그림에 옮길 때 cairo 의 디더링
(blue noise, Dither.GOOD)을 켠다. 메모리를 아끼려 128줄씩 나눠 한다 (4K 한 장을 실수로 들면 100MB).
VM 에서 잰 계단 세기 (PNG 원본, 4K → 2560x1440): HYPER 축소 0.105 → 이 방식 0.046, 가장 큰 계단 0.34 → 0.23 단계.

cairo 1.18 미만(Dither·RGB96F 없음)이면 예전처럼 GdkPixbuf HYPER 로 줄인다.
"""
import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

_FLOAT = getattr(cairo.Format, "RGB96F", None)
_DITHER = getattr(getattr(cairo, "Dither", None), "GOOD", None)
STRIP = 128


def placement(bw, bh, W, H, mode="fill"):
    """(가로 배율, 세로 배율, 왼쪽 위 x, y) — fill: 꽉 채움(잘림), fit: 다 보이게, stretch: 늘림, center: 그대로"""
    if mode == "fit":
        sx = sy = min(W / bw, H / bh)
    elif mode == "stretch":
        sx, sy = W / bw, H / bh
    elif mode == "center":
        sx = sy = 1.0
    else:
        sx = sy = max(W / bw, H / bh)
    return sx, sy, (W - bw * sx) / 2, (H - bh * sy) / 2


def cover(pb, W, H, mode="fill", bg=(0.08, 0.08, 0.09), surface=None, fast=False):
    """pb 를 W x H(장치 픽셀) RGB24 그림으로. surface 를 주면 거기에 그린다 (크기는 W x H 여야).
    fast: 줄이는 필터를 BILINEAR 로 — 계단 세기는 같고(0.046 → 0.047) 2560 에서 0.58 → 0.33초 (VM).
      사진의 잔무늬는 GOOD(상자 평균)보다 조금 거칠 수 있어, 곧바로 떠야 하는 잠금 화면에만 쓴다"""
    W, H = max(1, int(W)), max(1, int(H))
    surf = surface or cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    out = cairo.Context(surf)
    out.set_source_rgb(*bg)
    out.paint()
    if pb is None:
        return surf
    bw, bh = pb.get_width(), pb.get_height()
    sx, sy, ox, oy = placement(bw, bh, W, H, mode)
    if sx == sy == 1.0 and float(ox).is_integer() and float(oy).is_integer():
        Gdk.cairo_set_source_pixbuf(out, pb, ox, oy)
        out.paint()
        return surf
    if _FLOAT is None or _DITHER is None:
        nw, nh = max(1, int(bw * sx)), max(1, int(bh * sy))
        Gdk.cairo_set_source_pixbuf(out, pb.scale_simple(nw, nh, GdkPixbuf.InterpType.HYPER),
                                    (W - nw) / 2, (H - nh) / 2)
        out.paint()
        return surf
    for y0 in range(0, H, STRIP):
        h = min(STRIP, H - y0)
        fs = cairo.ImageSurface(_FLOAT, W, h)
        cr = cairo.Context(fs)
        cr.set_source_rgb(*bg)
        cr.paint()
        cr.translate(ox, oy - y0)
        cr.scale(sx, sy)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR if fast else cairo.FILTER_GOOD)  # GOOD: 줄일 땐 상자 평균
        cr.paint()
        fs.flush()
        out.save()
        out.rectangle(0, y0, W, h)
        out.clip()
        out.set_source_surface(fs, 0, y0)
        out.get_source().set_dither(_DITHER)
        out.paint()
        out.restore()
        fs.finish()
    surf.flush()
    return surf
