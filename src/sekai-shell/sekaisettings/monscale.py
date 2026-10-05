"""모니터 배율 — 합성기(WorldLink/Hyprland)가 받아들이는 값만.

합성기 규칙 (src/helpers/Monitor.cpp): 배율은 1/120 단위, 그리고 해상도를 배율로 나눈 논리 크기가
가로·세로 모두 정수여야 한다. 아니면 "Invalid scale … failed to find a clean divisor" 오류를 띄우고
기본 배율로 돌아간다 — 2560×1600 에서 150%·175% 를 고르면 화면은 200% 그대로, 빨간 오류만 남았다.
그래서 고르는 목록부터 그 모니터에 되는 값으로 만들고, 적용·저장할 때도 한 번 더 맞춘다.
"""
import re

# 윈도우가 보여 주는 단계 — 모니터마다 이 근처의 "되는 값"으로 바꿔 보여 준다
TARGETS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)
MIN_LOGICAL_H = 600          # 이보다 작아지는 배율은 목록에서 뺀다 (창·설정이 화면에 안 들어간다)


def _clean(w, h, k):
    """k/120 배율에서 논리 크기가 정수인가 — 합성기와 같은 double 계산으로"""
    s = k / 120.0
    lw, lh = w / s, h / s
    return lw == int(lw) and lh == int(lh)


def fit(w, h, scale):
    """합성기의 searchScale 과 같은 순서로 가장 가까운 되는 배율 (못 찾으면 정수로 반올림)"""
    try:
        w, h, scale = int(w), int(h), float(scale)
    except (TypeError, ValueError):
        return 1.0
    if w <= 0 or h <= 0 or scale <= 0:
        return 1.0
    k = round(scale * 120.0)
    if k > 0 and _clean(w, h, k):
        return k / 120.0
    for i in range(1, 90):
        if _clean(w, h, k + i):
            return (k + i) / 120.0
        if k - i > 0 and _clean(w, h, k - i):
            return (k - i) / 120.0
    return float(max(1, round(scale)))


def fmt(scale):
    """설정 파일·hyprctl 에 쓰는 글자 — 소수 셋째 자리까지 (로그인 화면이 그 꼴만 받는다)"""
    t = f"{float(scale):.3f}".rstrip("0").rstrip(".")
    return t if "." in t else t + ".0"


def label(scale):
    return f"{round(float(scale) * 100)}%"


def choices(w, h, current=None):
    """[(id, "160%"), …] — 이 해상도에서 되는 배율만, 작은 것부터"""
    out = []
    for t in TARGETS:
        s = fit(w, h, t)
        if t > 1.0 and h / s < MIN_LOGICAL_H:
            continue
        if all(abs(s - x) > 0.01 for x in out):
            out.append(s)
    if current is not None:
        c = fit(w, h, current)
        if all(abs(c - x) > 0.01 for x in out):
            out.append(c)
    return [(fmt(s), label(s)) for s in sorted(out)]


def match_id(items, scale):
    """저장된 배율에 가장 가까운 목록 id"""
    try:
        v = float(scale)
    except (TypeError, ValueError):
        v = 1.0
    best = min(items, key=lambda it: abs(float(it[0]) - v), default=None)
    return best[0] if best else fmt(v)


def mode_size(mode, fallback=None):
    """"2560x1600@144Hz" → (2560, 1600) · "preferred" 등은 fallback (지금 모니터 크기)"""
    m = re.match(r"^(\d+)x(\d+)", str(mode or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    return fallback
