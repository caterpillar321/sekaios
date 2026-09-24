"""SekaiOS 로고 — 두 별.

큰 별(강조색)과 작은 별(분홍)이 겹친다. 세카이에서 미쿠를 만나는 이야기를
두 빛의 만남으로 옮긴 것. 프로세카의 네 갈래 반짝임을 쓰되 로고·그림은 쓰지 않는다.

모양은 여기 한 곳에서 정한다. 작업 표시줄·시작 메뉴·아이콘 파일(SVG/PNG)이
모두 이 값을 쓴다 (scripts/gen-logo.py).
"""
import colorsys
import math

TEAL = (0.224, 0.773, 0.733)     # #39c5bb
PINK = (1.000, 0.420, 0.616)     # #ff6b9d

# 단위 정사각형(0..1) 기준 — (중심 x, 중심 y, 반지름)
BIG = (0.40, 0.57, 0.40)
SMALL = (0.73, 0.28, 0.25)
INNER = 0.24                     # 안쪽 꼭짓점 비율 (작을수록 날렵한 별)
GAP = 0.055                      # 두 별 사이 틈 (작은 크기에서도 둘로 읽히게)


def star_points(cx, cy, r, k=INNER):
    pts = []
    for i in range(8):
        a = -math.pi / 2 + i * math.pi / 4
        rr = r if i % 2 == 0 else r * k
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def second_color(accent):
    """작은 별 색. 강조색이 분홍 계열이면 두 별이 같아지므로 그때만 틸로."""
    h, _l, s = colorsys.rgb_to_hls(*accent)
    deg = h * 360
    pinkish = s > 0.25 and (deg >= 300 or deg <= 25)
    return TEAL if pinkish else PINK


def draw(cr, size, accent=TEAL):
    """cairo 컨텍스트의 (0,0)-(size,size) 에 로고를 그린다."""
    import cairo
    S = size

    def path(cx, cy, r):
        pts = star_points(cx * S, cy * S, r * S)
        cr.new_path()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()

    cr.save()
    cr.push_group()
    path(*BIG)
    cr.set_source_rgb(*accent)
    cr.fill()
    # 작은 별 둘레를 투명하게 도려낸다 — 배경이 무엇이든 틈이 생긴다
    cr.set_operator(cairo.OPERATOR_CLEAR)
    path(*SMALL)
    cr.set_line_width(GAP * S * 2)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke()
    cr.set_operator(cairo.OPERATOR_OVER)
    path(*SMALL)
    cr.set_source_rgb(*second_color(accent))
    cr.fill()
    cr.pop_group_to_source()
    cr.paint()
    cr.restore()


def svg(size=128, accent=TEAL):
    """같은 모양의 SVG 문자열 (아이콘 테마·os-release 로고용)."""
    def hexc(c):
        return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)

    def poly(cx, cy, r):
        return " ".join(f"{x * size:.2f},{y * size:.2f}"
                        for x, y in star_points(cx, cy, r))

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <!-- SekaiOS — 두 별 -->
  <defs>
    <mask id="gap">
      <rect width="{size}" height="{size}" fill="#fff"/>
      <polygon points="{poly(*SMALL)}" fill="#000" stroke="#000"
               stroke-width="{GAP * size * 2:.2f}" stroke-linejoin="round"/>
    </mask>
  </defs>
  <polygon points="{poly(*BIG)}" fill="{hexc(accent)}" mask="url(#gap)"/>
  <polygon points="{poly(*SMALL)}" fill="{hexc(second_color(accent))}"/>
</svg>
'''
