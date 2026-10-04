"""접근성 — 윈도우 11 의 "접근성"처럼 시각(텍스트 크기·시각 효과·포인터·돋보기·색 필터·대비 테마)과
상호 작용(키보드: 고정 키·필터 키·화상 키보드), 내레이터(Orca).

저장은 store 의 a11y 섹션 (투명·애니메이션·커서 크기는 appearance 와 같은 값). 합성기 쪽은 store.apply_a11y 가
hyprctl keyword 로 바로 걸고 ~/.config/hypr/sekai.conf 에도 적어 다음 로그인에도 남는다.
돋보기·색 필터·화상 키보드의 단축키는 작업 표시줄이 받는다 (sekaishell/a11y.py).
"""
import subprocess

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import Page, button, combo, row, slider, spin, switch

ICON = ["preferences-desktop-accessibility", "preferences-desktop-accessibility-symbolic", "accessibility"]
FILTERS = [("grayscale", "회색조"), ("inverted", "반전"), ("grayscale-inverted", "회색조 반전"),
           ("deuteranopia", "적록 (녹색약)"), ("protanopia", "적록 (적색약)"), ("tritanopia", "청황 (청색약)")]
STEPS = [("0.25", "25%"), ("0.5", "50%"), ("1.0", "100%"), ("2.0", "200%")]
DELAYS = [("0", "끔"), ("300", "0.3초"), ("500", "0.5초"), ("1000", "1초"), ("2000", "2초")]


def _ctl(what, action):
    subprocess.Popen(["sekai-ctl", what, action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _osk(action):
    _ctl("osk", action)


def build(store):
    p = Page("접근성", "보기·듣기·입력을 쉽게 — 텍스트 크기, 돋보기, 색 필터, 대비 테마, 키보드 도움")
    x = store.get("a11y")
    a = store.get("appearance")

    # ── 시각 ──
    s = p.section("텍스트 크기")
    pct = Gtk.Label(label=f"{round(float(x['text_scale']) * 100)}%")

    def set_scale(v):
        pct.set_text(f"{round(v * 100)}%")
        store.set("a11y", "text_scale", round(v, 2))
    ctl = Gtk.Box(spacing=10)
    sc = slider(float(x["text_scale"]), 1.0, 2.0, 0.05, set_scale, digits=2, width=240)
    sc.set_draw_value(False)                          # 옆의 "100%" 로 보인다 — 끄는 동안에도 바로
    sc.connect("value-changed", lambda w: pct.set_text(f"{round(w.get_value() * 100)}%"))
    ctl.pack_start(sc, False, False, 0)
    ctl.pack_start(pct, False, False, 0)
    row(s, "텍스트 크기", "앱과 작업 표시줄의 글자 — 열려 있는 앱도 바로 바뀝니다",
        icon=["format-text-larger", "zoom-in"], control=ctl)

    s = p.section("시각 효과")
    row(s, "투명 효과", "창·작업 표시줄 뒤를 흐리게 보입니다 — 끄면 글자가 더 또렷합니다",
        icon=["preferences-desktop-theme", "applications-graphics"],
        control=switch(bool(a["blur"]), lambda v: store.set("appearance", "blur", v)))
    row(s, "애니메이션 효과", "창이 열리고 닫힐 때 움직임",
        icon=["preferences-desktop-effects", "applications-graphics"],
        control=switch(bool(a["animations"]), lambda v: store.set("appearance", "animations", v)))

    s = p.section("마우스 포인터")
    row(s, "크기", None, icon=["input-mouse", "preferences-desktop-peripherals"],
        control=spin(a["cursor_size"], 12, 96, 4, lambda v: store.set("appearance", "cursor_size", v)))
    row(s, "색", None, icon=["input-mouse", "preferences-desktop-peripherals"],
        control=combo([("white", "흰색"), ("black", "검은색")], x["cursor_color"],
                      lambda v: store.set("a11y", "cursor_color", v)))

    s = p.section("돋보기")
    b = Gtk.Box(spacing=6)
    b.pack_start(button("켜기", lambda: subprocess.Popen(["sekai-ctl", "magnify", "in"])), False, False, 0)
    b.pack_start(button("끄기", lambda: subprocess.Popen(["sekai-ctl", "magnify", "off"])), False, False, 0)
    row(s, "돋보기", "Win + = 로 키우고 Win + - 로 줄입니다 · Win + Esc 로 끕니다 — 마우스를 따라 확대합니다",
        icon=["zoom-in", "zoom-in-symbolic"], control=b)
    row(s, "한 번에 키우는 만큼", None, icon=["zoom-in", "zoom-in-symbolic"],
        control=combo(STEPS, {0.25: "0.25", 0.5: "0.5", 1.0: "1.0", 2.0: "2.0"}.get(float(x["magnifier_step"]), "1.0"),
                      lambda v: store.set("a11y", "magnifier_step", float(v))))

    s = p.section("색 필터")
    row(s, "색 필터", "Win + Ctrl + C 로 켜고 끕니다", icon=["color-select", "applications-graphics"],
        control=switch(bool(x["color_filter"]), lambda v: store.set("a11y", "color_filter", v)))
    row(s, "필터", "색약 필터는 구별하기 어려운 색을 볼 수 있는 쪽으로 옮깁니다",
        icon=["color-select", "applications-graphics"],
        control=combo(FILTERS, x["color_filter_kind"], lambda v: store.set("a11y", "color_filter_kind", v)))

    s = p.section("대비 테마")
    row(s, "대비 테마", "검정 바탕·흰 글자·노란 선택으로 경계를 뚜렷하게 — 끄면 원래 모드와 강조색으로 돌아갑니다",
        icon=["preferences-desktop-theme", "applications-graphics"],
        control=combo([("off", "없음"), ("night", "야간 하늘")], "night" if a.get("mode") == "contrast" else "off",
                      lambda v: (store.set_contrast(v == "night"), store.request_rebuild(delay_ms=200))))

    # ── 상호 작용 ──
    s = p.section("키보드")
    row(s, "고정 키", "Shift·Ctrl·Alt·Windows 키를 누르고 있지 않고 하나씩 눌러 단축키를 씁니다 "
        "(두 번 누르면 계속 걸림) — 걸린 키는 화면 오른쪽 아래에 보입니다",
        icon=["input-keyboard", "preferences-desktop-keyboard"],
        control=switch(bool(x["sticky_keys"]), lambda v: store.set("a11y", "sticky_keys", v)))
    row(s, "필터 키", "짧게 여러 번 눌리거나 스친 키를 무시합니다",
        icon=["input-keyboard", "preferences-desktop-keyboard"],
        control=switch(bool(x["filter_keys"]), lambda v: store.set("a11y", "filter_keys", v)))
    row(s, "    반복 입력 무시", "같은 키를 이 시간 안에 다시 누르면 무시합니다", icon=None,
        control=combo(DELAYS, str(int(x["bounce_ms"])), lambda v: store.set("a11y", "bounce_ms", int(v))))
    row(s, "    누르고 있어야 입력", "이만큼 누르고 있어야 들어갑니다", icon=None,
        control=combo(DELAYS, str(int(x["slow_ms"])), lambda v: store.set("a11y", "slow_ms", int(v))))
    row(s, "바로 가기 키로 켜기", "Shift 를 다섯 번 누르면 고정 키, 오른쪽 Shift 를 8초 누르면 필터 키를 켤지 묻습니다",
        icon=["input-keyboard", "preferences-desktop-keyboard"],
        control=switch(bool(x["a11y_shortcuts"]), lambda v: store.set("a11y", "a11y_shortcuts", v)))

    s = p.section("화상 키보드")
    b = Gtk.Box(spacing=6)
    b.pack_start(button("열기", lambda: _osk("on")), False, False, 0)
    b.pack_start(button("닫기", lambda: _osk("off")), False, False, 0)
    row(s, "화상 키보드", "마우스나 터치로 글자를 입력합니다 · Win + Ctrl + O — 한글 입력기가 켜져 있으면 한글로",
        icon=["input-keyboard", "preferences-desktop-keyboard"], control=b)
    row(s, "로그인할 때 화상 키보드 열기", None, icon=["input-keyboard", "preferences-desktop-keyboard"],
        control=switch(bool(x["osk"]), lambda v: store.set("a11y", "osk", v, apply=False)))

    s = p.section("내레이터")
    b = Gtk.Box(spacing=6)
    b.pack_start(button("켜기", lambda: _ctl("narrator", "on")), False, False, 0)
    b.pack_start(button("끄기", lambda: _ctl("narrator", "off")), False, False, 0)
    row(s, "내레이터", "화면의 글자·단추를 소리 내어 읽습니다 (Orca) · Win + Ctrl + Enter — "
        "Insert 키를 누른 채 다른 키로 명령합니다 (Insert + H 도움말)",
        icon=["audio-speakers", "audio-volume-high"], control=b)
    row(s, "로그인할 때 내레이터 켜기", None, icon=["audio-speakers", "audio-volume-high"],
        control=switch(bool(x.get("narrator")), lambda v: store.set("a11y", "narrator", v, apply=False)))
    row(s, "내레이터 설정", "목소리·빠르기·읽을 내용·키보드 배치(데스크톱 / 노트북)",
        icon=["preferences-desktop-accessibility", "preferences-system"],
        control=button("열기", lambda: subprocess.Popen(["orca", "--replace", "-s"], start_new_session=True,
                                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
    return p


PAGES = [{"id": "a11y", "title": "접근성", "icon": ICON, "build": build, "sections": ("a11y",)}]
