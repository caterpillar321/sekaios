"""시간 및 언어 — 표시 언어, 시간대, 한글 입력."""
import os
import subprocess

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import run, spawn
from ..widgets import Page, button, combo, info, row, switch

LANGS = [("ko_KR.UTF-8", "한국어"), ("en_US.UTF-8", "English (United States)"),
         ("ja_JP.UTF-8", "日本語"), ("en_GB.UTF-8", "English (United Kingdom)")]

ZONES = [("Asia/Seoul", "서울 (UTC+9)"), ("Asia/Tokyo", "도쿄 (UTC+9)"),
         ("Asia/Shanghai", "베이징 (UTC+8)"), ("Asia/Singapore", "싱가포르 (UTC+8)"),
         ("Europe/London", "런던"), ("Europe/Berlin", "베를린"),
         ("America/New_York", "뉴욕"), ("America/Los_Angeles", "로스앤젤레스"),
         ("Australia/Sydney", "시드니"), ("UTC", "협정 세계시 (UTC)")]

RALT = "korean:ralt_hangul"
RCTRL = "korean:rctrl_hanja"


def _generated():
    out = run(["locale", "-a"]).lower()
    return {x.strip().replace("utf8", "utf-8") for x in out.split()}


def _timedate():
    d = {}
    for line in run(["timedatectl", "show"]).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def build(store):
    p = Page("시간 및 언어", "표시 언어, 시간대, 한글 입력을 설정합니다.")

    # ── 언어 ──
    s = p.section("언어")
    have = _generated()
    items = [(k, v) for k, v in LANGS if k.lower() in have] or LANGS[:1]
    cur = store.get("locale", "lang") or "ko_KR.UTF-8"
    now = os.environ.get("LANG", "")

    note = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    note.get_style_context().add_class("notice")
    note.set_no_show_all(True)
    nl = Gtk.Label(label="다시 로그인하면 새 언어가 적용됩니다.", xalign=0)
    note.pack_start(nl, True, True, 0)
    relog = Gtk.Button(label="지금 로그아웃")
    relog.connect("clicked", lambda *_: spawn(["hyprctl", "dispatch", "exit"]))
    note.pack_end(relog, False, False, 0)

    def on_lang(v):
        store.set("locale", "lang", v)
        (note.show_all if v != now else note.hide)()

    row(s, "표시 언어", "메뉴·대화상자·날짜에 쓰는 언어",
        icon=["preferences-desktop-locale", "config-language", "preferences-desktop-locale-symbolic"],
        control=combo(items, cur, on_lang))
    row(s, "지금 쓰는 로캘", "그래픽 세션의 LANG", control=info(now or "-"))
    p.add_widget(note)

    # ── 시간 ──
    s = p.section("날짜 및 시간")
    td = _timedate()
    tz = td.get("Timezone", "Asia/Seoul")
    zones = ZONES if any(z == tz for z, _ in ZONES) else [(tz, tz)] + ZONES

    def on_zone(v):
        # 시스템 설정이라 관리자 확인(polkit)이 뜬다
        subprocess.Popen(["timedatectl", "set-timezone", v])

    row(s, "시간대", None,
        icon=["preferences-system-time", "clock", "preferences-system-time-symbolic"],
        control=combo(zones, tz, on_zone))
    row(s, "시간 자동 맞춤", "인터넷 시간 서버와 맞춥니다 (NTP)",
        control=switch(td.get("NTP", "no") == "yes",
                       lambda v: subprocess.Popen(["timedatectl", "set-ntp", "true" if v else "false"])))
    row(s, "시계 동기화 상태", None,
        control=info("맞춰짐" if td.get("NTPSynchronized") == "yes" else "맞추는 중이거나 꺼짐"))
    # Windows 는 메인보드 시계(RTC)를 현지 시간으로 읽는다. 리눅스 기본(UTC)으로 두면
    #   Windows 로 넘어갔을 때 시간이 9시간 틀린다 (한국이면 새벽으로)
    row(s, "Windows 와 시간 맞추기", "Windows 와 함께 쓰면 켜 두세요 — 메인보드 시계를 현지 시간으로 씁니다",
        control=switch(td.get("LocalRTC", "no") == "yes",
                       lambda v: subprocess.Popen(["timedatectl", "set-local-rtc", "1" if v else "0"])))

    # ── 한글 입력 ──
    s = p.section("한글 입력")
    opts = [o for o in (store.get("input", "kb_options") or "").split(",") if o]

    def toggle(opt, on):
        cur = [o for o in (store.get("input", "kb_options") or "").split(",") if o and o != opt]
        if on:
            cur.append(opt)
        store.set("input", "kb_options", ",".join(cur))

    row(s, "한/영 전환", "한/영 키 또는 오른쪽 Alt", icon=["input-keyboard", "input-keyboard-symbolic"],
        control=info("ibus · 두벌식"))
    row(s, "오른쪽 Alt 를 한/영 키로", "한/영 키가 없는 키보드용",
        control=switch(RALT in opts, lambda v: toggle(RALT, v)))
    row(s, "오른쪽 Ctrl 을 한자 키로", None,
        control=switch(RCTRL in opts, lambda v: toggle(RCTRL, v)))
    row(s, "입력기 세부 설정", "자판 배열(두벌식·세벌식), 한자 변환 키 등",
        control=button("열기", lambda: spawn("/usr/libexec/ibus-setup-hangul")))
    return p


PAGES = [{"id": "locale", "title": "시간 및 언어",
          "icon": ["preferences-desktop-locale", "config-language", "preferences-system-time",
                   "preferences-desktop-locale-symbolic"],
          "build": build}]
