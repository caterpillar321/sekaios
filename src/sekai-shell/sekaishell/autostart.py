"""로그인할 때 자동으로 시작하는 앱 (XDG 자동 시작) — 실행기(/usr/lib/sekai/autostart)와 작업 관리자의 시작 앱 탭이 같이 쓴다.

항목은 ~/.config/autostart 와 XDG_CONFIG_DIRS(/etc/xdg …)/autostart 의 .desktop 파일이다.
같은 파일 이름이면 사용자 폴더 → 앞쪽 시스템 폴더 순으로 이긴다 (XDG 규칙 — 사용자가 Hidden=true 로 끈다).
실행하는 것: 켜져 있고(Hidden·X-GNOME-Autostart-enabled), 지금 데스크톱에서 보이고(OnlyShowIn·NotShowIn),
TryExec 가 있고, SekaiOS 세션이 직접 띄우는 것(MANAGED)·일부러 실행하지 않는 것(NEVER)이 아닌 항목.
MANAGED 는 세션(hyprland.conf exec-once, x11/session)이 이미 띄운다 — 여기서 또 띄우면 두 개가 된다.
작업 관리자는 MANAGED 와 NoDisplay=true 인 항목(시스템 구성 요소)은 목록에 보이지 않는다 (윈도우·GNOME 처럼).
"""
import os

from gi.repository import GLib

GROUP = "[Desktop Entry]"

# SekaiOS 세션이 직접 띄우는 것 — 자동 시작으로는 건너뛴다
MANAGED = {
    "nm-applet.desktop",        # 네트워크 트레이 아이콘 (--indicator --no-agent — 암호 창은 nm-agent)
    "im-launch.desktop",        # 입력기 — 세션이 ibus 를 직접 띄운다
}

# SekaiOS 에서는 일부러 실행하지 않는 것
NEVER = {
    # "현재 언어로 표준 폴더 이름을 업데이트할까요?" — 예를 누르면 빈 새 폴더(~/바탕화면 …)를 만들고 그쪽을 쓰지만
    #   안의 파일은 옮기지 않아, 바탕화면·문서가 비어 보였다. 폴더 이름은 계정을 만들 때(첫 설정의 언어) 정한다 (윈도우처럼)
    "user-dirs-update-gtk.desktop",
    # 다른 DE 의 관리자 암호 창 — SekaiOS 는 자체 "사용자 계정 컨트롤"(/usr/lib/sekai/polkit-agent)을 띄운다.
    #   세션마다 하나만 등록할 수 있어서, 이것들이 먼저 뜨면 우리 창 대신 그쪽 창이 나온다
    "lxpolkit.desktop",
    "polkit-gnome-authentication-agent-1.desktop",
    "polkit-mate-authentication-agent-1.desktop",
    "polkit-kde-authentication-agent-1.desktop",
    "lxqt-policykit-agent.desktop",
}


def autostart_dirs():
    home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    sysdirs = [d for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if d]
    return os.path.join(home, "autostart"), [os.path.join(d, "autostart") for d in sysdirs]


def parse_entry(path):
    """[Desktop Entry] 묶음의 키=값 (지역화 키 Name[ko] 도 그대로)"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    out, inside = {}, False
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("["):
            inside = s == GROUP
            continue
        if inside and "=" in s:
            k, _, v = s.partition("=")
            out.setdefault(k.strip(), v.strip())
    return out


def _true(v):
    return (v or "").strip().lower() == "true"


def entry_enabled(d):
    return not _true(d.get("Hidden")) and (d.get("X-GNOME-Autostart-enabled", "true").strip().lower() != "false")


def localized(d, key):
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").split(".")[0]
    cands = []
    if lang and lang not in ("C", "POSIX"):
        cands.append(f"{key}[{lang}]")
        if "_" in lang:
            cands.append(f"{key}[{lang.split('_')[0]}]")
    cands.append(key)
    for c in cands:
        if d.get(c):
            return d[c]
    return ""


def desktops():
    return [x for x in (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":") if x]


def shown_here(d, cur=None):
    """OnlyShowIn·NotShowIn — 지금 데스크톱을 앞에서부터 본다 (GIO 와 같은 규칙)"""
    cur = desktops() if cur is None else cur
    only = [x for x in d.get("OnlyShowIn", "").split(";") if x]
    never = [x for x in d.get("NotShowIn", "").split(";") if x]
    for x in cur:
        if x in only:
            return True
        if x in never:
            return False
    return not only


def installed(d):
    """TryExec 가 있으면 그 프로그램이 있어야 한다"""
    te = d.get("TryExec")
    if not te:
        return True
    if os.path.isabs(te):
        return os.access(te, os.X_OK)
    return GLib.find_program_in_path(te) is not None


def scan():
    """→ 항목 목록. 같은 파일 이름은 사용자 폴더 → 앞쪽 시스템 폴더 순으로 이긴다"""
    user, sysdirs = autostart_dirs()
    found = {}
    for d in reversed(sysdirs):                  # 앞 폴더가 이기도록 거꾸로 채운다
        try:
            for f in os.listdir(d):
                if f.endswith(".desktop"):
                    found.setdefault(f, {})["sys"] = os.path.join(d, f)
        except OSError:
            pass
    try:
        for f in os.listdir(user):
            if f.endswith(".desktop"):
                found.setdefault(f, {})["user"] = os.path.join(user, f)
    except OSError:
        pass
    cur = desktops()
    items = []
    for f, where in sorted(found.items()):
        up, sp = where.get("user"), where.get("sys")
        sd = parse_entry(sp) if sp else None
        ud = parse_entry(up) if up else None
        eff = ud if ud is not None else sd
        if eff is None:
            continue
        # 덮어쓰기가 Hidden=true 한 줄뿐이어도 이름·아이콘·조건은 시스템 항목에서
        show = dict(sd or {})
        show.update(ud or {})
        enabled = entry_enabled(eff)
        here, inst = shown_here(show, cur), installed(show)
        items.append({
            "file": f, "user": up, "sys": sp, "path": up if ud is not None else sp,
            "data": eff, "show": show,
            "name": localized(show, "Name") or f[:-8],
            "comment": localized(show, "Comment"),
            "icon": show.get("Icon", ""), "exec": show.get("Exec", ""),
            "enabled": enabled,
            "sys_enabled": entry_enabled(sd) if sd is not None else None,
            "managed": f in MANAGED or f in NEVER,
            "never": f in NEVER,
            "nodisplay": _true(show.get("NoDisplay")),
            "here": here, "installed": inst,
            # 로그인할 때 실제로 실행되나
            "runs": (enabled and here and inst and f not in MANAGED and f not in NEVER
                     and show.get("Type", "Application") == "Application" and bool(show.get("Exec"))),
        })
    return items


def delay_of(it):
    """X-GNOME-Autostart-Delay (초) — 없거나 이상하면 0, 길어도 60초까지"""
    try:
        return max(0.0, min(60.0, float(it["show"].get("X-GNOME-Autostart-Delay", "0"))))
    except ValueError:
        return 0.0
