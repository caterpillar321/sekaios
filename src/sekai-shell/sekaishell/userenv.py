"""환경 변수 — 윈도우의 "시스템 속성 → 환경 변수"처럼 사용자 변수와 시스템 변수.

저장은 systemd 의 environment.d 형식 (NAME="값", $NAME·${NAME}·${NAME:-기본}·${NAME:+대체} 는 그 변수 값으로):
  시스템  /etc/environment.d/60-sekai-system.conf        (설정 앱 → /usr/libexec/sekai/sekai-envvars, 관리자)
  사용자  ~/.config/environment.d/70-sekai-user.conf
systemd 사용자 서비스는 이 파일들을 스스로 읽는다. 그래픽 세션(sekai-session)·로그인 셸(/etc/profile.d/sekai-env.sh)·
단축키로 여는 앱(with-env)은 `python3 -m sekaishell.userenv sh` 로, 패널·바탕 화면·탐색기처럼 떠 있는 프로세스는
watch() 로 파일이 바뀌면 자기 환경을 고친다 — 윈도우처럼 저장한 뒤 새로 여는 앱부터 바뀐 값을 받는다.

같은 환경에 여러 번 적용해도 한 번 적용한 것과 같다: 적용하면서 원래 값과 넣은 값을 SEKAI_ENV_BASE 에 적어 두고,
다시 적용할 때 먼저 되돌린다 (로그인 셸 → sekai-session → 패널 순으로 거듭 적용된다).
"""
import json
import os
import re
import shlex

SYSTEM_FILE = "/etc/environment.d/60-sekai-system.conf"
BASE_KEY = "SEKAI_ENV_BASE"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_VALUE = 8192
# 세션이 쓰는 값 — 바꾸면 로그인이 깨지거나 앱이 화면·D-Bus 를 못 찾는다
PROTECTED = {"WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP", "XDG_SEAT", "XDG_VTNR",
             "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE", "HYPRLAND_CMD", "HOME", "USER", "LOGNAME",
             "SHELL", "PWD", "OLDPWD", "SHLVL", "_", "MAIL", "TERM", "INVOCATION_ID", "JOURNAL_STREAM",
             "MANAGERPID", "NOTIFY_SOCKET", "LISTEN_FDS", "LISTEN_PID", "SYSTEMD_EXEC_PID"}
PROTECTED_PREFIX = ("XDG_SESSION_", "SEKAI_")
# 잘못 넣으면 앱이 뜨지 않을 수 있는 값 — 막지는 않고 알린다
RISKY = {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "GTK_MODULES", "GDK_BACKEND", "QT_QPA_PLATFORM", "LANG", "LC_ALL",
         "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "PYTHONPATH", "PYTHONHOME", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR"}


def user_file(env=None):
    env = os.environ if env is None else env
    cfg = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(cfg, "environment.d", "70-sekai-user.conf")


def check_name(name):
    """쓸 수 없는 이름이면 이유를, 괜찮으면 None"""
    if not NAME_RE.match(name or ""):
        return "이름은 영문자 · 숫자 · 밑줄(_)만 쓰고 숫자로 시작할 수 없습니다"
    if name in PROTECTED or name.startswith(PROTECTED_PREFIX):
        return f"{name} 은(는) 세션이 쓰는 값이라 바꿀 수 없습니다"
    return None


def check_value(value):
    if value is None or value == "":
        return "값을 입력하세요 (빈 값은 저장할 수 없습니다)"
    if "\n" in value or "\r" in value or "\0" in value:
        return "값에 줄바꿈을 넣을 수 없습니다"
    if len(value) > MAX_VALUE:
        return f"값이 너무 깁니다 ({MAX_VALUE}자까지)"
    return None


def is_list(name):
    """콜론(:)으로 폴더를 잇는 값 — 목록으로 편집하고, 되돌릴 때 넣은 항목만 뺀다"""
    return name == "PATH" or name.endswith("PATH") or name.endswith("_DIRS")


# ── 파일 읽기·쓰기 ──
def _unquote(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        out, i, s = [], 0, raw[1:-1]
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                i += 1
            out.append(s[i])
            i += 1
        return "".join(out)
    return raw


def parse(text):
    """[(이름, 값)] — 적힌 순서대로 (값은 아직 펼치지 않은 그대로). 이름이 겹치면 뒤의 것"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw = line.split("=", 1)
        name = name.strip()
        if NAME_RE.match(name):
            out.pop(name, None)
            out[name] = _unquote(raw)
    return list(out.items())


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return parse(f.read())
    except (OSError, UnicodeDecodeError):
        return []


HEADER = """# SekaiOS 환경 변수 — 설정 → 환경 변수 에서 고칩니다 (이 파일을 고쳐도 되지만 주석은 저장할 때 지워집니다)
#   형식: systemd environment.d — 이름="값", $이름 · ${이름} 은 그 변수의 값으로 바뀝니다
"""


def dump(items):
    def q(v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`") + '"'
    return HEADER + "".join(f"{n}={q(v)}\n" for n, v in items)


# ── 펼치기·적용 ──
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:[-+])([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")


def expand(value, env):
    def sub(m):
        name = m.group(1) or m.group(4)
        cur = env.get(name, "")
        if m.group(2) == ":-":
            return cur or m.group(3)
        if m.group(2) == ":+":
            return m.group(3) if cur else ""
        return cur
    return _VAR.sub(sub, value)


def _dedupe(value):
    seen, out = set(), []
    for part in value.split(":"):
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return ":".join(out)


def _restore(env):
    """지난번에 넣은 값을 걷어 낸 환경 — 그 뒤 남이 바꾼 값이면 넣은 항목만 뺀다(목록) / 그대로 둔다"""
    base = dict(env)
    try:
        saved = json.loads(base.pop(BASE_KEY, "") or "{}")
    except ValueError:
        saved = {}
    for name, (orig, applied) in saved.items() if isinstance(saved, dict) else ():
        cur = base.get(name)
        if cur == applied:
            if orig is None:
                base.pop(name, None)
            else:
                base[name] = orig
        elif cur is not None and is_list(name) and applied:
            added = set(applied.split(":")) - set((orig or "").split(":"))
            base[name] = ":".join(p for p in cur.split(":") if p not in added)
    return base


def target(env=None):
    """env 에 시스템·사용자 변수를 적용한 새 환경 (dict) — env 는 고치지 않는다"""
    env = os.environ if env is None else env
    base = _restore(env)
    work = dict(base)
    names = []
    for name, raw in read(SYSTEM_FILE) + read(user_file(base)):
        if check_name(name) or not raw:
            continue
        val = expand(raw, work)
        work[name] = _dedupe(val) if is_list(name) else val
        if name not in names:
            names.append(name)
    saved = {n: [base.get(n), work[n]] for n in names if base.get(n) != work[n]}
    if saved:
        work[BASE_KEY] = json.dumps(saved, ensure_ascii=False)
    return work


def diff(old, new):
    """{이름: 새 값 또는 None(지움)} — 바뀐 것만"""
    return {k: new.get(k) for k in set(old) | set(new) if old.get(k) != new.get(k)}


def refresh():
    """이 프로세스의 환경(os.environ)에 지금 파일 내용을 적용 — 바뀐 것을 돌려준다"""
    changes = diff(dict(os.environ), target())
    for k, v in changes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return changes


def push_session(changes):
    """systemd 사용자 관리자·D-Bus 가 띄우는 앱(포털·서비스로 뜨는 앱)에도 — 설정 앱이 저장한 뒤에 부른다"""
    import subprocess
    sets = [f"{k}={v}" for k, v in changes.items() if v is not None and k != BASE_KEY]
    unsets = [k for k, v in changes.items() if v is None and k != BASE_KEY]
    try:
        if sets:
            subprocess.run(["dbus-update-activation-environment", "--systemd"] + sets,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if unsets:
            subprocess.run(["systemctl", "--user", "unset-environment"] + unsets,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            # environment.d 에서 읽은 값은 unset-environment 로 안 지워진다 — 다시 읽게 (지운 파일 내용으로)
            subprocess.run(["systemctl", "--user", "daemon-reload"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass


_monitors = []


def watch(on_change=None):
    """두 파일이 바뀌면 이 프로세스의 환경을 고친다 (GLib 메인 루프가 도는 프로세스에서). 처음에도 한 번 적용"""
    from gi.repository import Gio
    try:
        refresh()
    except Exception:
        pass

    def changed(*_a):
        try:
            ch = refresh()
        except Exception:
            return
        if ch and on_change:
            on_change(ch)
    # 파일을 바꿔치기(rename)로 저장하므로 폴더를 본다 — 폴더가 아직 없어도 Gio 는 생기기를 기다린다
    for path in (SYSTEM_FILE, user_file()):
        try:
            m = Gio.File.new_for_path(os.path.dirname(path)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except Exception:
            continue
        m.connect("changed", lambda _m, f, o, _e, p=path: (f and f.get_path() == p or o and o.get_path() == p) and changed())
        _monitors.append(m)


def shell_lines(env=None):
    """sh 에서 eval 할 export/unset 줄"""
    env = os.environ if env is None else env
    out = []
    for k, v in sorted(diff(dict(env), target(env)).items()):
        out.append(f"unset {k}" if v is None else f"export {k}={shlex.quote(v)}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["sh"]:
        print(shell_lines())
    else:
        print("쓰는 법: python3 -m sekaishell.userenv sh", file=sys.stderr)
        sys.exit(2)
