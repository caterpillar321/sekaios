"""키보드 · 마우스.

소리는 pages/sound.py (설정 › 소리) — 예전엔 여기서 wpctl 로 출력 장치·볼륨만 만지고 나머지는 pavucontrol 에 맡겼다.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import Page, button, combo, row, slider, switch

LAYOUTS = [("us", "영어 (미국)"), ("kr", "한국어"), ("jp", "일본어"),
           ("de", "독일어"), ("fr", "프랑스어"), ("es", "스페인어"),
           ("ru", "러시아어"), ("cn", "중국어")]



# 포인터 속도 — 윈도우의 1~20 눈금(기본 10)을 합성기의 sensitivity(-1.0 ~ 1.0)로.
#   libinput 의 0 은 윈도우 10단계보다 빠르게 느껴져(가속 곡선이 다르다 — 천천히 움직일 때도 민감) 10단계를 -0.3 에 둔다
#   (2026-10-06 실제 PC 에서 윈도우와 견줘 정함). 그 아래·위는 -1.0 · 1.0 까지 고르게.
SPEED_MID = -0.3


def sens_of(n):
    n = min(20, max(1, int(round(n))))
    if n <= 10:
        return round(-1.0 + (n - 1) * (SPEED_MID + 1.0) / 9, 3)
    return round(SPEED_MID + (n - 10) * (1.0 - SPEED_MID) / 10, 3)


def speed_of(sens):
    try:
        v = float(sens)
    except (TypeError, ValueError):
        return 10
    return min(range(1, 21), key=lambda n: abs(sens_of(n) - v))


def edited_entry(text, on_change, width=20, placeholder=None):
    """입력 칸 — 엔터나 포커스가 빠질 때, 그리고 사람이 글자를 바꿨을 때만 저장한다.
    포커스가 지나가기만 해도 저장하면, 다른 곳(위 목록·한/영 스위치)에서 바꾼 값을
    이 칸에 남아 있던 옛 값으로 되돌렸다 (키보드 레이아웃이 us 로 돌아가는 등)."""
    e = Gtk.Entry()
    e.set_text(str(text or ""))
    e.set_width_chars(width)
    if placeholder:
        e.set_placeholder_text(placeholder)
    e._applied = str(text or "")

    def apply(w):
        t = w.get_text()
        if t != w._applied:
            w._applied = t
            on_change(t)
    e.connect("activate", apply)
    e.connect("focus-out-event", lambda w, _e: (apply(w), False)[1])
    return e


def set_entry_value(e, text):
    """밖에서 값이 바뀌었을 때 칸을 맞춘다 (저장은 하지 않는다)"""
    t = str(text or "")
    e._applied = t
    if e.get_text() != t:
        e.set_text(t)


# ── 키보드 / 마우스 ─────────────────────────────────────────
def build_input(store):
    p = Page("키보드 및 마우스", "입력 장치의 동작을 설정합니다.")
    i = store.get("input")

    s = p.section("키보드")
    cur_layout = i["kb_layout"] or "us"
    known = [k for k, _ in LAYOUTS]
    items = LAYOUTS + ([(cur_layout, cur_layout)] if cur_layout not in known else [])
    row(s, "레이아웃", "여러 개를 쓰려면 쉼표로 구분 (예: us,kr)",
        icon=["input-keyboard", "preferences-desktop-keyboard"],
        control=combo(items, cur_layout,
                      lambda v: store.set("input", "kb_layout", v)))
    layout_entry = edited_entry(i["kb_layout"], lambda v: store.set("input", "kb_layout", v),
                                width=14, placeholder="us,kr")
    row(s, "직접 입력", "위 목록에 없는 레이아웃", control=layout_entry)
    options_entry = edited_entry(i["kb_options"], lambda v: store.set("input", "kb_options", v),
                                 width=36, placeholder="grp:alt_shift_toggle")   # 기본값(한/영·한자 키)이 다 보이게
    row(s, "전환 단축키", "xkb options (예: grp:alt_shift_toggle)", control=options_entry)

    # 위 목록이나 "시간 및 언어"의 한/영 스위치가 같은 값을 바꾸면 칸도 따라간다
    #   (칸에 옛 값이 남아 있으면 헷갈리고, 예전엔 포커스만 지나가도 그 옛 값으로 되돌렸다)
    def follow(section, key, value):
        if section == "input" and key == "kb_layout":
            set_entry_value(layout_entry, value)
        elif section == "input" and key == "kb_options":
            set_entry_value(options_entry, value)
    store.connect(follow)
    p.connect("destroy", lambda *_: store.disconnect(follow))   # 페이지를 다시 그리면 떼어 낸다
    row(s, "키 반복 속도", "초당 반복 횟수",
        control=slider(i["repeat_rate"], 1, 60, 1,
                       lambda v: store.set("input", "repeat_rate", v)))
    row(s, "반복 시작 지연", "밀리초",
        control=slider(i["repeat_delay"], 150, 1200, 10,
                       lambda v: store.set("input", "repeat_delay", v)))

    s = p.section("마우스")
    row(s, "포인터 속도", "윈도우처럼 1 ~ 20, 기본 10",
        icon=["input-mouse", "preferences-desktop-peripherals"],
        control=slider(speed_of(i["sensitivity"]), 1, 20, 1,
                       lambda v: store.set("input", "sensitivity", sens_of(v))))
    row(s, "포인터 정확도 향상", "빨리 움직일수록 더 멀리 — 끄면 움직인 만큼만 (게임에서 많이 끕니다 · 터치패드에도 적용)",
        control=switch(i.get("accel", True), lambda v: store.set("input", "accel", v)))
    row(s, "스크롤 방향 반대로", "손가락이 움직이는 대로 내용이 따라옵니다",
        control=switch(i["natural_scroll"],
                       lambda v: store.set("input", "natural_scroll", v)))
    row(s, "마우스를 따라 포커스", "커서가 올라간 창이 활성화됩니다",
        control=switch(int(i["follow_mouse"]) == 1,
                       lambda v: store.set("input", "follow_mouse", 1 if v else 2)))

    s = p.section("터치패드")
    row(s, "터치패드", "노트북 키보드의 터치패드 켜기 키(Fn)로도 켜고 끕니다",
        icon=["input-touchpad", "input-mouse"],
        control=switch(i.get("tp_enabled", True), lambda v: store.set("input", "tp_enabled", v)))
    row(s, "입력하는 동안 터치패드 끄기", "손바닥이 닿아 커서가 튀지 않게",
        control=switch(i.get("tp_dwt", True), lambda v: store.set("input", "tp_dwt", v)))
    row(s, "스크롤 방향 반대로",
        control=switch(i["tp_natural_scroll"],
                       lambda v: store.set("input", "tp_natural_scroll", v)))
    row(s, "탭하여 클릭",
        control=switch(i["tp_tap"], lambda v: store.set("input", "tp_tap", v)))
    row(s, "세 손가락 쓸기", "위 = 작업 보기, 아래 = 바탕 화면 보기, 좌우 = 앱 전환",
        control=switch(i.get("gesture3", True), lambda v: store.set("input", "gesture3", v)))
    row(s, "네 손가락 쓸기", "좌우 = 데스크톱 넘기기",
        control=switch(i.get("gesture4", True), lambda v: store.set("input", "gesture4", v)))

    s = p.section("되돌리기")
    row(s, "입력 기본값으로",
        control=button("되돌리기", lambda: store.reset_section("input")))   # 적용·다시 그리기까지 한다
    return p


PAGES = [
    {"id": "input", "title": "키보드 및 마우스",
     "icon": ["input-keyboard", "preferences-desktop-peripherals",
              "input-keyboard-symbolic"],
     "build": build_input, "sections": ("input",)},
]
