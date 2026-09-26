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
    """사용자가 고른 주 디스플레이 이름. 로그인 화면(_greetd)은 설정을 못 읽으므로
    sekai-greeter-session 이 넘겨주는 SEKAI_PRIMARY 를 쓴다."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            v = str((json.load(f).get("layout") or {}).get("primary") or "")
            if v:
                return v
    except (OSError, ValueError, AttributeError):
        pass
    return os.environ.get("SEKAI_PRIMARY", "")


def _largest(hmons):
    """고른 게 없을 때의 주 디스플레이 — 가장 큰(화소가 많은) 모니터. 같으면 먼저 연결된 것"""
    live = [m for m in hmons if not m.get("disabled")]
    if not live:
        return None
    return max(live, key=lambda m: (int(m.get("width", 0)) * int(m.get("height", 0)), -int(m.get("id", 0))))


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
    hmons = hmons if hmons is not None else hypr_monitors()
    name = primary_name()
    hm = next((m for m in hmons if m.get("name") == name), None) if name else None
    g = gdk_for(hm or _largest(hmons), disp)
    if g is not None:
        return g
    return disp.get_primary_monitor() or disp.get_monitor(0)


def primary_hypr_name(hmons=None):
    """주 디스플레이의 Hyprland 이름 (설정이 없으면 첫 모니터)"""
    hmons = hmons if hmons is not None else hypr_monitors()
    name = primary_name()
    if name and any(m.get("name") == name for m in hmons):
        return name
    big = _largest(hmons)
    return big.get("name", "") if big else ""


def publish_modes(hmons=None):
    """지금 쓰는 모니터 모드를 공용 폴더에 적는다 — sekai-bootmode(root)가 읽어 부팅 옵션(video=)으로.
    부팅 화면·콘솔·로그인 화면·바탕화면이 모두 같은 모드면 모니터 신호가 끊기지 않는다
    (모드가 바뀔 때마다 연결을 다시 맺느라 화면이 꺼졌다 켜진다).
    형식: "DP-1 2560x1440@164.96" 한 줄에 하나. 바뀌었을 때만 쓴다 (그래야 path 유닛이 헛돌지 않는다)."""
    d = "/var/lib/sekai/displays"
    if not os.path.isdir(d) or not os.access(d, os.W_OK):
        return
    hmons = hmons if hmons is not None else hypr_monitors()
    lines = []
    for m in hmons:
        if m.get("disabled"):
            continue
        try:
            lines.append(f"{m['name']} {int(m['width'])}x{int(m['height'])}@{float(m['refreshRate']):.2f}\n")
        except (KeyError, TypeError, ValueError):
            continue
    if not lines:
        return
    import pwd
    import stat
    import tempfile
    path = os.path.join(d, pwd.getpwuid(os.getuid()).pw_name + ".modes")
    text = "".join(lines)
    # 누구나 쓰는 폴더다 — 남이 같은 이름으로 FIFO·링크를 먼저 만들어 둘 수 있다.
    #   비교용 읽기도 링크를 따라가지 않고(O_NOFOLLOW) 막히지 않게(O_NONBLOCK), 내 일반 파일일 때만.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        fd = -1
    if fd >= 0:
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() \
                    and os.read(fd, 8192).decode("utf-8", "replace") == text:
                return
        except OSError:
            pass
        finally:
            os.close(fd)
    # 새 파일은 예측할 수 없는 이름으로 새로 만들어(mkstemp — O_EXCL) 바꿔 넣는다.
    #   남의 파일이 그 이름에 있으면 바꿔 넣기가 거부된다 (sticky 폴더) — 그땐 그냥 둔다.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".modes.", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o644)             # 부팅 옵션을 만드는 쪽(root)·로그인 화면이 읽는다
            f.write(text)
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def migrate_display_keys():
    """예전 화면 설정(단자 이름)을 지금 꽂힌 모니터의 설정으로 옮긴다 (sekaisettings.store.migrate_display).
    화면 설정을 윈도우처럼 모니터마다 기억하게 바꾼 뒤 처음 로그인할 때 한 번 일어난다 — 그 뒤엔 옮길 것이 없다"""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=3).stdout
        mons = json.loads(out)
        from sekaisettings.store import Store
        s = Store()
        if isinstance(mons, list) and s.migrate_display(mons):
            s.save()
    except Exception:
        pass
