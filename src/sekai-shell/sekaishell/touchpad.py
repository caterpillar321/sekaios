"""터치패드 켜고 끄기 — 설정 › 키보드 및 마우스 › 터치패드, Fn 키(XF86TouchpadToggle · On · Off).

Hyprland 엔 "터치패드 전부" 스위치가 없어 장치마다 device[<이름>]:enabled 를 건다 (다음 로그인용은
sekaisettings.store 의 조각 파일에 device { } 묶음으로). 터치패드인지는 udev 가 붙인 ID_INPUT_TOUCHPAD 로 —
/run/udev/data 는 누구나 읽을 수 있다. 이름은 Hyprland 가 쓰는 꼴(소문자, 빈칸 → -)로.
"""
import glob
import os
import subprocess

from . import dbg


def hypr_name(name):
    return name.strip().replace(" ", "-").replace("\n", "-").lower()


def touchpads():
    """이 PC 의 터치패드들 — Hyprland 장치 이름"""
    out = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(ev, "dev")) as f:
                dev = f.read().strip()                     # "13:70"
            with open(f"/run/udev/data/c{dev}") as f:
                if "E:ID_INPUT_TOUCHPAD=1" not in f.read().splitlines():
                    continue
            with open(os.path.join(ev, "device", "name")) as f:
                n = hypr_name(f.read())
        except OSError:
            continue
        if n and n not in out:
            out.append(n)
    return out


def apply(enabled):
    """지금 세션에 바로 — 끈 터치패드는 커서를 움직이지 않는다"""
    for n in touchpads():
        try:
            subprocess.run(["hyprctl", "keyword", f"device[{n}]:enabled", "true" if enabled else "false"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except Exception as e:
            dbg("[터치패드]", n, e)


def hypr_lines(enabled):
    """다음 로그인용 조각 — 켜져 있으면 아무것도 쓰지 않는다 (기본이 켜짐)"""
    if enabled:
        return []
    lines = []
    for n in touchpads():
        lines += ["device {", f"    name = {n}", "    enabled = false", "}", ""]
    return lines
