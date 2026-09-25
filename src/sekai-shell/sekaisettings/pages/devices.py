"""소리 · 키보드 · 마우스."""
import re

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import run, spawn
from ..widgets import (Page, button, combo, info, row, slider, spin, switch,
                       entry)

LAYOUTS = [("us", "영어 (미국)"), ("kr", "한국어"), ("jp", "일본어"),
           ("de", "독일어"), ("fr", "프랑스어"), ("es", "스페인어"),
           ("ru", "러시아어"), ("cn", "중국어")]


# ── 소리 ────────────────────────────────────────────────────
def _sinks():
    """wpctl status 에서 출력 장치 목록을 뽑는다."""
    out = run(["wpctl", "status"])
    sinks, cur = [], None
    sect = None
    for line in out.splitlines():
        if "Sinks:" in line:
            sect = "sink"
            continue
        if sect == "sink":
            if re.match(r"^\s*[├└│]?\s*$", line) or "Sources:" in line \
                    or "Filters" in line or "Streams" in line:
                if "Sources:" in line or "Filters" in line or "Streams" in line:
                    break
                continue
            m = re.search(r"(\*?)\s*(\d+)\.\s+(.+?)\s*\[", line)
            if m:
                star, sid, name = m.groups()
                sinks.append((sid, name.strip()))
                if star:
                    cur = sid
    return sinks, cur


def _volume():
    out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    parts = out.split()
    vol = float(parts[1]) * 100 if len(parts) > 1 else 0.0
    return round(vol), ("MUTED" in out)


def build_sound(store):
    p = Page("소리", "출력 장치와 볼륨을 조정합니다.")
    vol, muted = _volume()

    s = p.section("출력")
    sinks, cur = _sinks()
    if sinks:
        row(s, "출력 장치", "소리가 나갈 곳",
            icon=["audio-speakers", "audio-card"],
            control=combo(sinks, cur,
                          lambda v: run(["wpctl", "set-default", str(v)])))
    else:
        p.add_widget(_notice("오디오 장치를 찾지 못했습니다. "
                             "wireplumber 가 실행 중인지 확인하세요."))

    vslider = slider(vol, 0, 100, 1,
                     lambda v: run(["wpctl", "set-volume",
                                    "@DEFAULT_AUDIO_SINK@", f"{int(v)}%"]))
    row(s, "볼륨", None, icon=["audio-volume-high"], control=vslider)
    row(s, "음소거", None,
        control=switch(muted, lambda v: run(["wpctl", "set-mute",
                                             "@DEFAULT_AUDIO_SINK@",
                                             "1" if v else "0"])))

    s = p.section("도구")
    row(s, "고급 소리 설정", "장치별 볼륨, 입력, 프로파일",
        icon=["multimedia-volume-control", "pavucontrol"],
        control=button("pavucontrol 열기", lambda: spawn("pavucontrol")))
    return p


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
    row(s, "직접 입력", "위 목록에 없는 레이아웃",
        control=entry(i["kb_layout"],
                      lambda v: store.set("input", "kb_layout", v),
                      width=14, placeholder="us,kr"))
    row(s, "전환 단축키", "xkb options (예: grp:alt_shift_toggle)",
        control=entry(i["kb_options"],
                      lambda v: store.set("input", "kb_options", v),
                      width=22, placeholder="grp:alt_shift_toggle"))
    row(s, "키 반복 속도", "초당 반복 횟수",
        control=slider(i["repeat_rate"], 1, 60, 1,
                       lambda v: store.set("input", "repeat_rate", v)))
    row(s, "반복 시작 지연", "밀리초",
        control=slider(i["repeat_delay"], 150, 1200, 10,
                       lambda v: store.set("input", "repeat_delay", v)))

    s = p.section("마우스")
    row(s, "포인터 속도", "-1.0 느리게 ~ 1.0 빠르게",
        icon=["input-mouse", "preferences-desktop-peripherals"],
        control=slider(i["sensitivity"], -1.0, 1.0, 0.05,
                       lambda v: store.set("input", "sensitivity", v), digits=2))
    row(s, "스크롤 방향 반대로", "손가락이 움직이는 대로 내용이 따라옵니다",
        control=switch(i["natural_scroll"],
                       lambda v: store.set("input", "natural_scroll", v)))
    row(s, "마우스를 따라 포커스", "커서가 올라간 창이 활성화됩니다",
        control=switch(int(i["follow_mouse"]) == 1,
                       lambda v: store.set("input", "follow_mouse", 1 if v else 2)))

    s = p.section("터치패드")
    row(s, "스크롤 방향 반대로",
        icon=["input-touchpad", "input-mouse"],
        control=switch(i["tp_natural_scroll"],
                       lambda v: store.set("input", "tp_natural_scroll", v)))
    row(s, "탭하여 클릭",
        control=switch(i["tp_tap"], lambda v: store.set("input", "tp_tap", v)))

    s = p.section("되돌리기")
    row(s, "입력 기본값으로",
        control=button("되돌리기", lambda: (store.reset_section("input"),
                                          store.apply_all())))
    return p


def _notice(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


PAGES = [
    {"id": "sound", "title": "소리",
     "icon": ["audio-volume-high", "multimedia-volume-control",
              "audio-volume-high-symbolic"],
     "build": build_sound},
    {"id": "input", "title": "키보드 및 마우스",
     "icon": ["input-keyboard", "preferences-desktop-peripherals",
              "input-keyboard-symbolic"],
     "build": build_input},
]
