"""시스템 정보 + 전원."""
import os
import platform
import shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..util import LOCK_NOW, human_bytes, run, spawn
from ..widgets import Page, button, combo, info, row, switch


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


def build_about(store):
    osr = _os_release()
    p = Page("시스템 정보", "이 컴퓨터와 SekaiOS 에 대한 정보입니다.")

    s = p.section("운영체제")
    row(s, "이름", icon=["sekaios", "distributor-logo", "computer"],
        control=info(osr.get("PRETTY_NAME", "SekaiOS")))
    row(s, "버전", control=info(osr.get("VERSION", "-")))
    row(s, "코드네임", control=info(osr.get("VERSION_CODENAME", "-")))
    row(s, "기반", control=info("Debian %s (%s)" % (
        osr.get("DEBIAN_VERSION_ID", "?"), osr.get("DEBIAN_VERSION_CODENAME", "?"))))
    row(s, "커널", control=info(platform.release()))
    row(s, "데스크탑", control=info("Hyprland + sekai-shell"))

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
    p = Page("전원 및 잠금", "화면을 끄고 잠그는 시간을 정합니다.")
    has_idle = bool(shutil.which("swayidle"))

    s = p.section("자동 동작")
    opts = [(0, "안 함"), (60, "1분"), (180, "3분"), (300, "5분"),
            (600, "10분"), (900, "15분"), (1800, "30분"), (3600, "1시간")]

    row(s, "화면 끄기", "아무 입력이 없을 때 화면을 끕니다",
        icon=["video-display", "preferences-desktop-screensaver"],
        control=combo(opts, store.get("power", "screen_off"),
                      lambda v: store.set("power", "screen_off", int(v))))
    row(s, "화면 잠그기", "잠금 화면을 띄웁니다",
        icon=["system-lock-screen", "changes-prevent"],
        control=combo(opts, store.get("power", "lock"),
                      lambda v: store.set("power", "lock", int(v))))
    row(s, "절전 모드", "시스템을 대기 상태로 보냅니다",
        icon=["system-suspend", "gnome-session-suspend"],
        control=combo(opts, store.get("power", "suspend"),
                      lambda v: store.set("power", "suspend", int(v))))

    if not has_idle:
        w = Gtk.Label(
            label="swayidle 이 설치돼 있지 않아 자동 동작은 적용되지 않습니다.\n"
                  "지금은 값만 저장됩니다.", xalign=0)
        w.get_style_context().add_class("notice")
        w.set_line_wrap(True)
        p.add_widget(w)

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
