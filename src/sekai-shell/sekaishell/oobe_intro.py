"""첫 부팅 설정(OOBE)의 시작 애니메이션 — "세카이로 들어가기" (윈도우의 첫 화면 애니메이션처럼).

  0.0 ~ 0.8  어둠 속 가운데 작은 빛이 숨 쉰다
  0.8 ~ 2.4  빛이 두 별(틸 · 분홍 — SekaiOS 로고)로 갈라져 꼬리를 그리며 돈다, 반짝이가 떠오른다
  1.6 ~ 4.2  별에서 빛의 물결(배경 그림의 그 선들)이 화면 끝까지 흘러간다
  3.6 ~ 5.0  가운데서 빛의 고리가 퍼져 화면을 덮는다 — 세카이로 통과하는 순간
  5.0 ~ 7.2  빛이 걷히며 배경 그림이 드러나고 "세카이에 오신 것을 환영합니다"
아무 키나 누르거나 누르면 바로 건너뛴다. 프레임은 시간으로 계산해서 느린 PC 는 장면을 건너뛸 뿐 늘어지지 않는다.
"""
import math
import random

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402

TEAL = (57 / 255, 197 / 255, 187 / 255)
PINK = (240 / 255, 109 / 255, 154 / 255)
DUR = 7.2
TITLE = "세카이에 오신 것을 환영합니다"
SUB = "SekaiOS"


def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _seg(t, a, b):
    """t 가 a→b 사이에서 0→1"""
    return max(0.0, min(1.0, (t - a) / (b - a))) if b > a else float(t >= b)


def _star(cr, x, y, r, color, alpha):
    """네 갈래 별 (로고) + 번짐"""
    glow = r * 3.2
    import cairo
    g = cairo.RadialGradient(x, y, 0, x, y, glow)
    g.add_color_stop_rgba(0, *color, 0.45 * alpha)
    g.add_color_stop_rgba(1, *color, 0)
    cr.set_source(g)
    cr.arc(x, y, glow, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgba(*color, alpha)
    w = r * 0.22
    cr.move_to(x, y - r)
    cr.curve_to(x + w * 0.3, y - w, x + w, y - w * 0.3, x + r, y)
    cr.curve_to(x + w, y + w * 0.3, x + w * 0.3, y + w, x, y + r)
    cr.curve_to(x - w * 0.3, y + w, x - w, y + w * 0.3, x - r, y)
    cr.curve_to(x - w, y - w * 0.3, x - w * 0.3, y - w, x, y - r)
    cr.close_path()
    cr.fill()


class Intro(Gtk.DrawingArea):
    def __init__(self, on_done, background=None):
        super().__init__()
        self.on_done = on_done
        self.bg_draw = background            # cr, w, h → 배경 그림 (드러날 때)
        self.t0 = None
        self.done = False
        self.skip_at = None
        rnd = random.Random(39)
        self.parts = [(rnd.random(), rnd.random(), 0.6 + rnd.random() * 1.8, 0.02 + rnd.random() * 0.06,
                       rnd.random() * 6.28, TEAL if rnd.random() < 0.6 else PINK) for _ in range(90)]
        self.set_can_focus(True)
        self.add_events(0x100 | 0x400)        # BUTTON_PRESS · KEY_PRESS
        self.connect("draw", self._draw)
        self.connect("button-press-event", lambda *_: self.skip())
        self.connect("key-press-event", lambda *_: (self.skip(), True)[1])
        self.add_tick_callback(self._tick)

    def elapsed(self):
        if self.t0 is None:
            return 0.0
        return (GLib.get_monotonic_time() - self.t0) / 1e6

    def skip(self):
        if self.skip_at is None and not self.done:
            self.skip_at = self.elapsed()

    def _tick(self, _w, _clock):
        if self.t0 is None:
            self.t0 = GLib.get_monotonic_time()
        t = self.elapsed()
        if (self.skip_at is not None and t - self.skip_at > 0.45) or t > DUR:
            if not self.done:
                self.done = True
                GLib.idle_add(lambda: (self.on_done(), False)[1])
            return False
        self.queue_draw()
        return True

    # ── 그리기 ──
    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        t = self.elapsed()
        cx, cy = W / 2, H * 0.46
        fade_skip = 1.0 if self.skip_at is None else max(0.0, 1 - (t - self.skip_at) / 0.45)
        reveal = _ease(_seg(t, 4.7, 5.6))     # 빛이 걷히며 배경이 드러나는 정도
        import cairo

        # 바탕 — 깊은 남색 어둠 (드러나는 동안 배경 그림 위로 옅어진다)
        if reveal > 0 and self.bg_draw is not None:
            self.bg_draw(cr, W, H)
        g = cairo.LinearGradient(0, 0, 0, H)
        g.add_color_stop_rgba(0, 0.024, 0.031, 0.043, (1 - reveal) * fade_skip + (1 - fade_skip) * 0)
        g.add_color_stop_rgba(1, 0.035, 0.07, 0.08, (1 - reveal) * fade_skip)
        cr.set_source(g)
        cr.paint()
        if fade_skip <= 0:
            return False

        dim = (1 - reveal) * fade_skip

        # 반짝이 — 아래에서 위로 천천히
        pa = _ease(_seg(t, 0.9, 2.0)) * dim
        if pa > 0:
            for (px, py, pr, sp, ph, col) in self.parts:
                y = (py - t * sp) % 1.0
                tw = 0.5 + 0.5 * math.sin(t * 2.2 + ph)
                cr.set_source_rgba(*col, pa * (0.25 + 0.6 * tw))
                cr.arc(px * W, y * H, pr, 0, 2 * math.pi)
                cr.fill()

        # 빛의 물결 — 별에서 양옆으로 흘러나간다
        wv = _seg(t, 1.6, 3.6)
        if wv > 0 and dim > 0:
            reach = _ease(wv) * (W / 2 + 40)
            for i in range(6):
                col = TEAL if i % 3 else PINK
                cr.set_line_width(1.2 + (i == 2) * 0.8)
                for side in (-1, 1):
                    steps = 60
                    cr.new_path()
                    for k in range(steps + 1):
                        d = reach * k / steps
                        x = cx + side * d
                        y = (cy + H * 0.08 + (i - 2.5) * 9
                             + math.sin(d * 0.006 + t * 1.4 + i * 0.7) * H * 0.05 * (d / (W / 2 + 1)))
                        cr.line_to(x, y)
                    lg = cairo.LinearGradient(cx, 0, cx + side * reach, 0)
                    lg.add_color_stop_rgba(0, *col, 0.0)
                    lg.add_color_stop_rgba(0.3, *col, 0.55 * dim)
                    lg.add_color_stop_rgba(1, *col, 0.0)
                    cr.set_source(lg)
                    cr.stroke()

        # 처음의 빛 → 두 별
        if t < 0.9:
            br = 0.5 + 0.5 * math.sin(t * 6)
            _star(cr, cx, cy, 5 + 4 * br * _ease(_seg(t, 0, 0.6)), (1, 1, 1), _ease(_seg(t, 0.05, 0.5)) * dim)
        else:
            sp = _ease(_seg(t, 0.8, 2.4))
            radius = 60 * sp + 8 * math.sin(t * 1.3)
            ang = t * 2.1
            size = 14 + 16 * sp
            # 꼬리 — 지난 자리를 옅게
            for k in range(14, 0, -1):
                tt = t - k * 0.035
                if tt < 0.8:
                    continue
                r2 = 60 * _ease(_seg(tt, 0.8, 2.4)) + 8 * math.sin(tt * 1.3)
                for off, col in ((0, TEAL), (math.pi, PINK)):
                    x = cx + math.cos(tt * 2.1 + off) * r2
                    y = cy + math.sin(tt * 2.1 + off) * r2 * 0.55
                    cr.set_source_rgba(*col, 0.25 * (1 - k / 15) * dim)
                    cr.arc(x, y, 2.5, 0, 2 * math.pi)
                    cr.fill()
            for off, col, s in ((0, TEAL, size), (math.pi, PINK, size * 0.72)):
                x = cx + math.cos(ang + off) * radius
                y = cy + math.sin(ang + off) * radius * 0.55
                _star(cr, x, y, s, col, dim)

        # 빛의 고리 — 퍼지며 화면을 덮는다 (세카이로 통과)
        rp = _seg(t, 3.6, 5.0)
        if rp > 0:
            diag = math.hypot(W, H)
            R = _ease(rp) ** 1.6 * diag * 0.75
            flash = _ease(_seg(t, 4.3, 4.9)) * (1 - _ease(_seg(t, 5.0, 5.8)))
            rg = cairo.RadialGradient(cx, cy, max(1, R * 0.6), cx, cy, max(2, R))
            rg.add_color_stop_rgba(0, 0.85, 0.98, 0.97, 0.0)
            rg.add_color_stop_rgba(0.85, *TEAL, 0.55 * fade_skip * (1 - reveal))
            rg.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(rg)
            cr.paint()
            if flash > 0:
                # 눈부시지 않게 — 흰빛이 아니라 옅은 틸 빛이 화면을 덮었다 걷힌다
                fg = cairo.RadialGradient(cx, cy, 0, cx, cy, diag * 0.7)
                fg.add_color_stop_rgba(0, 0.70, 0.93, 0.90, 0.55 * flash * fade_skip)
                fg.add_color_stop_rgba(1, *TEAL, 0.30 * flash * fade_skip)
                cr.set_source(fg)
                cr.paint()

        # 환영 글
        ta = _ease(_seg(t, 5.2, 5.9)) * (1 - _ease(_seg(t, 6.6, 7.2))) * fade_skip
        if ta > 0:
            cr.set_source_rgba(0, 0, 0, 0.25 * ta)
            cr.paint()
            for text, font, dy, alpha in ((TITLE, "Pretendard Light 30", -14, 1.0), (SUB, "Pretendard 13", 34, 0.75)):
                lay = PangoCairo.create_layout(cr)
                lay.set_font_description(Pango.FontDescription(font))
                lay.set_text(text, -1)
                tw, th = lay.get_pixel_size()
                cr.move_to(W / 2 - tw / 2, H / 2 + dy - th / 2 - (1 - _ease(_seg(t, 5.2, 5.9))) * 10)
                cr.set_source_rgba(1, 1, 1, ta * alpha)
                PangoCairo.show_layout(cr, lay)
        return False
