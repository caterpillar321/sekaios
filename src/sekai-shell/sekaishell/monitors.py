"""주 디스플레이 — 작업 표시줄(트레이·알림)·바탕화면 아이콘·잠금 화면 입력 칸이 뜨는 모니터.

설정 앱(디스플레이 → 주 디스플레이로 사용)이 ~/.config/sekai/settings.json 의
layout.primary 에 모니터 이름(DP-1 등)을 적는다. Hyprland 에는 "주 모니터"라는 개념이 없어서
이름 → Hyprland 모니터 위치 → 같은 위치의 Gdk 모니터로 찾는다.
설정이 없거나 그 모니터가 없으면 GDK 가 정한 주 모니터(Wayland 에선 첫 모니터).
"""
import json
import os
import subprocess

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

SETTINGS = os.path.expanduser("~/.config/sekai/settings.json")


def primary_name():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return str((json.load(f).get("layout") or {}).get("primary") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def hypr_monitors():
    """hyprctl monitors (Hyprland 가 아니면 빈 목록)"""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return []
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=3).stdout
        v = json.loads(out)
        return v if isinstance(v, list) else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def gdk_for(hmon, disp=None):
    """Hyprland 모니터 항목 → 같은 위치의 Gdk 모니터"""
    disp = disp or Gdk.Display.get_default()
    if disp is None or not hmon:
        return None
    for i in range(disp.get_n_monitors()):
        m = disp.get_monitor(i)
        g = m.get_geometry()
        if (g.x, g.y) == (hmon.get("x"), hmon.get("y")):
            return m
    return None


def primary_gdk(disp=None, hmons=None):
    disp = disp or Gdk.Display.get_default()
    if disp is None or disp.get_n_monitors() == 0:
        return None
    name = primary_name()
    if name:
        hm = next((m for m in (hmons if hmons is not None else hypr_monitors())
                   if m.get("name") == name), None)
        g = gdk_for(hm, disp)
        if g is not None:
            return g
    return disp.get_primary_monitor() or disp.get_monitor(0)


def primary_hypr_name(hmons=None):
    """주 디스플레이의 Hyprland 이름 (설정이 없으면 첫 모니터)"""
    hmons = hmons if hmons is not None else hypr_monitors()
    name = primary_name()
    if name and any(m.get("name") == name for m in hmons):
        return name
    return hmons[0].get("name") if hmons else ""
