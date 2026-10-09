"""시스템 정보 + 전원."""
import os
import platform
import shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..util import LOCK_NOW, human_bytes, spawn
from ..widgets import Page, button, combo, info, row


def _os_release():
    d = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    d[k] = v.strip('"')
    except Exception:
        pass
    return d


def _meminfo():
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _cpu():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "알 수 없음"


def _compositor():
    """창을 그리는 합성기 — WorldLink(worldlink 패키지 판, Hyprland 의 포크 — 처음 이름 SekaiCompose) 또는 기본 화면 모드의 xfwm4"""
    import subprocess
    if os.environ.get("SEKAI_BASIC") == "1" or os.environ.get("XDG_SESSION_TYPE") == "x11":
        return "xfwm4 (기본 화면 모드)"
    try:
        v = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "worldlink"], capture_output=True, text=True,
                           timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = ""
    return f"WorldLink {v.split('-')[0]}" if v else "WorldLink"


def image_build():
    """설치 이미지의 빌드 번호 (/etc/sekai/build — ISO 를 구울 때 들어간다) → "0001" · "0001 (개발용)" 또는 None"""
    try:
        d = dict(ln.strip().split("=", 1) for ln in open("/etc/sekai/build", encoding="utf-8") if "=" in ln)
    except OSError:
        return None
    b = d.get("BUILD", "")
    if not b.isdigit():
        return None
    return b + (" (개발용)" if d.get("KIND") == "dev" else "")


def build_about(store):
    osr = _os_release()
    p = Page("시스템 정보", "이 컴퓨터와 SekaiOS 에 대한 정보입니다.")

    s = p.section("운영체제")
    row(s, "이름", icon=["sekaios", "distributor-logo", "computer"],
        control=info(osr.get("PRETTY_NAME", "SekaiOS")))
    row(s, "버전", control=info(osr.get("VERSION", "-")))
    b = image_build()
    if b:
        row(s, "빌드", "이 PC 를 설치한 이미지", control=info(b))
    row(s, "코드네임", control=info(osr.get("VERSION_CODENAME", "-")))
    row(s, "기반", control=info("Debian %s (%s)" % (
        osr.get("DEBIAN_VERSION_ID", "?"), osr.get("DEBIAN_VERSION_CODENAME", "?"))))
    row(s, "커널", control=info(platform.release()))
    row(s, "데스크톱", control=info("SekaiOS 셸"))
    row(s, "화면 합성기", "창을 화면에 그리는 부품", control=info(_compositor()))

    s = p.section("하드웨어")
    row(s, "프로세서", icon=["cpu", "computer"], control=info(_cpu()))
    row(s, "메모리", control=info(human_bytes(_meminfo())))
    try:
        du = shutil.disk_usage("/")
        row(s, "저장소 (/)", control=info(
            f"{human_bytes(du.used)} 사용 / {human_bytes(du.total)}"))
    except Exception:
        pass
    row(s, "호스트 이름", control=info(platform.node()))

    s = p.section("도구")
    row(s, "시스템 모니터", "실행 중인 프로세스와 자원 사용량",
        icon=["utilities-system-monitor", "xfce4-taskmanager"],
        control=button("열기", lambda: spawn("sekai-taskmgr")))
    row(s, "터미널", "명령줄",
        icon=["utilities-terminal", "terminal"],
        control=button("열기", lambda: spawn(store.get("apps", "terminal", "sekai-terminal"))))
    return p


def build_power(store):
    from sekaishell import power
    from sekaishell.power import battery_text
    p = Page("전원 및 잠금", "전원 모드 · 배터리 · 화면을 끄고 잠그는 시간을 정합니다.")
    has_idle = bool(shutil.which("swayidle"))

    # ── 배터리 (노트북) · 전원 모드 ──
    bat = power.battery()
    profs = power.profiles()
    if bat or profs:
        s = p.section("전원")
        if bat:
            _icons, text = battery_text(bat)
            row(s, f"배터리 {bat['pct']}%", text, icon=_icons + ["battery"])
        if profs:
            cur = power.profile() or "balanced"
            row(s, "전원 모드", "성능과 배터리 사용 시간 사이에서 고릅니다",
                icon=["power-profile-balanced-symbolic", "preferences-system-power"],
                control=combo(profs, cur, lambda v: v and power.set_profile(v)))
        if bat:
            opts = [(0, "켜지 않음"), (10, "10% 이하"), (20, "20% 이하"), (30, "30% 이하"), (50, "50% 이하")]
            row(s, "배터리 절약 모드 저절로 켜기", "배터리를 쓰는 중 잔량이 이만큼 내려가면 최고 전원 효율로 바꿉니다 — "
                "충전기를 꽂으면 되돌립니다",
                icon=["battery-low-symbolic", "battery-caution"],
                control=combo(opts, int(store.get("power", "saver_at") or 0),
                              lambda v: store.set("power", "saver_at", int(v))))

    s = p.section("자동 동작")
    opts = [(0, "안 함"), (60, "1분"), (180, "3분"), (300, "5분"),
            (600, "10분"), (900, "15분"), (1800, "30분"), (3600, "1시간")]

    # 노트북은 배터리 사용 시 / 전원 연결 시 따로 (sekai-idle 이 충전기를 꽂고 뺄 때 바꿔 건다)
    items = [("screen_off", "화면 끄기", "아무 입력이 없을 때 화면을 끕니다",
              ["video-display", "preferences-desktop-screensaver"]),
             ("lock", "화면 잠그기", "잠금 화면을 띄웁니다", ["system-lock-screen", "changes-prevent"]),
             ("suspend", "절전 모드", "시스템을 대기 상태로 보냅니다", ["system-suspend", "gnome-session-suspend"])]
    for key, title, sub, icon in items:
        for k, label in ([(key + "_battery", " — 배터리 사용 시"), (key, " — 전원 연결 시")] if bat else [(key, "")]):
            row(s, title + label, sub, icon=icon,
                control=combo(opts, store.get("power", k), lambda v, k=k: store.set("power", k, int(v))))

    if not has_idle:
        w = Gtk.Label(
            label="swayidle 이 설치돼 있지 않아 자동 동작은 적용되지 않습니다.\n"
                  "지금은 값만 저장됩니다.", xalign=0)
        w.get_style_context().add_class("notice")
        w.set_line_wrap(True)
        p.add_widget(w)

    # ── 전원 단추와 덮개 (윈도우: 제어판 › 전원 옵션) — 노트북은 배터리 사용 시 / 전원 연결 시 따로 ──
    s = p.section("전원 단추와 덮개")
    kinds = [("button", "전원 단추를 누르면", ["system-shutdown-symbolic", "system-shutdown"])]
    if power.lid_present():
        kinds.append(("lid", "덮개를 닫으면", ["computer-laptop-symbolic", "computer-laptop", "computer"]))
    for kind, title, icon in kinds:
        acts = power.actions(kind)
        for src, label in ([("battery", " — 배터리 사용 시"), ("ac", " — 전원 연결 시")] if bat else [("ac", "")]):
            key = f"{kind}_{src}"
            cur = store.get("power", key) or power.default_action(kind, bool(bat))
            row(s, title + label, None, icon=icon,
                control=combo(acts, cur, lambda v, k=key: v and store.set("power", k, v)))

    s = p.section("지금 실행")
    row(s, "화면 잠그기", control=button("잠그기", lambda: spawn(LOCK_NOW)))
    row(s, "로그아웃", control=button("로그아웃", lambda: spawn("hyprctl dispatch exit")))
    row(s, "다시 시작", control=button("다시 시작", lambda: spawn("systemctl reboot")))
    row(s, "시스템 종료", control=button("종료", lambda: spawn("systemctl poweroff")))
    return p


PAGES = [
    {"id": "about", "title": "시스템 정보",
     "icon": ["computer", "distributor-logo", "computer-symbolic"],
     "build": build_about},
    {"id": "power", "title": "전원 및 잠금",
     "icon": ["battery", "preferences-system-power", "gnome-power-manager",
              "battery-symbolic"],
     "build": build_power, "sections": ("power",)},
]
