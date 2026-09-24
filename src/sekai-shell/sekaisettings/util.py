"""SekaiOS 설정 — 공용 유틸리티."""
import json
import os
import shlex
import subprocess

DEBUG = os.environ.get("SEKAI_DEBUG") == "1"


def dbg(*a):
    if DEBUG:
        print("[sekai-settings]", *a, flush=True)


def run(cmd, timeout=5, check=False):
    """명령을 실행하고 stdout 을 돌려준다. 실패하면 빈 문자열."""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            dbg("실패", cmd, p.stderr.strip())
        return p.stdout
    except Exception as e:
        dbg("예외", cmd, e)
        return ""


def spawn(cmd):
    """백그라운드로 띄우고 잊는다 (설정 창이 닫혀도 살아남게)."""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        dbg("spawn 실패", cmd, e)
        return False


# ── Hyprland IPC ────────────────────────────────────────────
def hyprctl(*args, js=False):
    cmd = ["hyprctl"]
    if js:
        cmd.append("-j")
    cmd += [str(a) for a in args]
    out = run(cmd)
    if not js:
        return out.strip()
    try:
        return json.loads(out)
    except Exception:
        return None


def keyword(key, value):
    """설정 값을 즉시 반영. 성공하면 True."""
    out = hyprctl("keyword", key, str(value))
    okay = out.strip() == "ok"
    if not okay:
        dbg("keyword 실패", key, value, out.strip())
    return okay


def hypr_running():
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) or \
        bool(hyprctl("version"))


# ── 색 변환 ─────────────────────────────────────────────────
def hex_to_rgba(hexstr, alpha=1.0):
    """'#00e5ff' → 'rgba(00e5ffcc)'  (Hyprland 표기)"""
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    a = max(0, min(255, int(round(alpha * 255))))
    return f"rgba({h[:6]}{a:02x})"


def gdk_to_hex(rgba):
    return "#%02x%02x%02x" % (int(rgba.red * 255 + 0.5),
                              int(rgba.green * 255 + 0.5),
                              int(rgba.blue * 255 + 0.5))


def human_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
