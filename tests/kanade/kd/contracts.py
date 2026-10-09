"""계약 — 여러 곳이 같은 약속을 따로 들고 있는 것이 어긋나지 않았나.

  설정 키   settings.json 을 읽는 모든 곳의 (섹션, 키, 기본값) ↔ 설정 저장소 sekaisettings/store.py 의 DEFAULTS
  D-Bus     org.sekai.Shell 의 정의(sekaishell/shell.py XML) ↔ 패널의 구현(ShellService 표) ↔ sekai-ctl 의 call
  sekai-ctl 하위 명령을 부르는 곳(설정 파일·코드) ↔ sekai-ctl 이 아는 하위 명령"""
import ast
import os
import re

from . import code
from .finding import Finding

STORE = os.path.join(code.SHELL, "sekaisettings", "store.py")
SHELL_PY = os.path.join(code.SHELL, "sekaishell", "shell.py")
PANEL = os.path.join(code.SHELL, "sekai-panel")
CTL = os.path.join(code.SHELL, "sekai-ctl")
READERS = {"sekai_conf", "settings", "conf", "_conf", "get_conf", "setting", "cfg"}
MISSING = object()


def defaults():
    tree = ast.parse(code._read(STORE))
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULTS":
            return ast.literal_eval(n.value)
    return {}


def _lit(n):
    try:
        return ast.literal_eval(n)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return MISSING


def _s(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def reads():
    """[(Unit, 줄, 섹션, 키, 기본값 | MISSING, 함수 이름)] — 설정을 읽는 곳"""
    D = defaults()
    out = []
    for u in code.py_units():
        if u.path == STORE or not isinstance(u.tree, ast.Module):
            continue
        for n in ast.walk(u.tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            # sekai_conf("panel", "height", 48) · settings("panel", "x") · store.get("panel", "x", d)
            if (name in READERS or (name == "get" and isinstance(f, ast.Attribute)
                                    and re.search(r"store|Store", ast.unparse(f.value)))) \
                    and len(n.args) >= 2 and _s(n.args[0]) in D and _s(n.args[1]):
                dflt = _lit(n.args[2]) if len(n.args) >= 3 else MISSING
                out.append((u, n.lineno, _s(n.args[0]), _s(n.args[1]), dflt, name))
            # X.get("panel", {}).get("height", 48)
            elif name == "get" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) \
                    and isinstance(f.value.func, ast.Attribute) and f.value.func.attr == "get" \
                    and f.value.args and _s(f.value.args[0]) in D and n.args and _s(n.args[0]):
                dflt = _lit(n.args[1]) if len(n.args) >= 2 else MISSING
                out.append((u, n.lineno, _s(f.value.args[0]), _s(n.args[0]), dflt, "get().get"))
    return out


def check_settings():
    fs = []
    D = defaults()
    for u, line, sec, key, dflt, how in reads():
        if key not in D.get(sec, {}):
            fs.append(Finding("contracts", "warn", "설정 기본값에 없는 키",
                              f"{sec}.{key} 를 읽는데 설정 저장소 DEFAULTS 에 없다 — 설정 앱이 모르는 값 (오타? 옮겨진 키?)",
                              u.rel, line, key=f"contracts|nokey|{sec}.{key}|{u.rel}"))
            continue
        want = D[sec][key]
        if dflt is not MISSING and dflt != want and not (dflt is None and how != "get().get"):
            fs.append(Finding("contracts", "warn", "기본값 어긋남",
                              f"{sec}.{key} 의 기본값을 {dflt!r} 로 따로 들고 있다 — 설정 저장소는 {want!r} "
                              f"(설정 파일에 값이 없을 때 두 곳이 다르게 동작한다)", u.rel, line,
                              key=f"contracts|default|{sec}.{key}|{u.rel}"))
    # 기본값 표 사본 — 저장소 밖에서 DEFAULTS 를 통째로 다시 적은 곳
    for u in code.py_units():
        if u.path == STORE or not isinstance(u.tree, ast.Module):
            continue
        for n in u.tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict) and len(n.targets) == 1:
                val = _lit(n.value)
                if not isinstance(val, dict):
                    continue
                shared = [s for s in val if s in D and isinstance(val[s], dict)]
                if len(shared) < 2:
                    continue
                diff = [f"{s}.{k}={v!r}(저장소 {D[s][k]!r})" for s in shared for k, v in val[s].items()
                        if k in D[s] and D[s][k] != v]
                miss = [f"{s}.{k}" for s in shared for k in val[s] if k not in D[s]]
                name = getattr(n.targets[0], "id", "?")
                fs.append(Finding("contracts", "warn" if diff or miss else "info", "기본값 표 사본",
                                  f"{name} 이(가) 설정 기본값을 따로 적어 둔다 ({', '.join(shared)})"
                                  + (f" — 어긋남: {', '.join(diff[:4])}" if diff else "")
                                  + (f" — 저장소에 없는 키: {', '.join(miss[:4])}" if miss else ""),
                                  u.rel, n.lineno, key=f"contracts|copy|{u.rel}|{name}"))
    return fs


def check_dbus():
    fs = []
    xml = set(re.findall(r'<method name="(\w+)"', code._read(SHELL_PY)))
    m = re.search(r"ShellService\(\{([\s\S]*?)\}\)", code._read(PANEL))
    impl = set(re.findall(r'"(\w+)"\s*:', m.group(1))) if m else set()
    ctl = set(re.findall(r"\bcall (\w+)", code._read(CTL)))
    rel = os.path.relpath(SHELL_PY, code.REPO)
    for name in sorted(xml - impl):
        fs.append(Finding("contracts", "error", "D-Bus 구현 없음",
                          f"org.sekai.Shell.{name} 이 인터페이스에 있는데 패널 ShellService 표에 없다 — 부르면 오류",
                          rel, key=f"contracts|dbus-noimpl|{name}"))
    for name in sorted(impl - xml):
        fs.append(Finding("contracts", "error", "D-Bus 정의 없음",
                          f"패널이 {name} 을 구현하는데 인터페이스 XML 에 없다 — 밖에서 부를 수 없다",
                          os.path.relpath(PANEL, code.REPO), key=f"contracts|dbus-noxml|{name}"))
    for name in sorted(ctl - xml):
        fs.append(Finding("contracts", "error", "sekai-ctl 이 없는 메서드를 부른다",
                          f"sekai-ctl 이 {name} 을 부르는데 org.sekai.Shell 에 없다",
                          os.path.relpath(CTL, code.REPO), key=f"contracts|dbus-ctl|{name}"))
    for name in sorted(xml - ctl):
        fs.append(Finding("contracts", "info", "sekai-ctl 로 못 부르는 메서드",
                          f"org.sekai.Shell.{name} 은 sekai-ctl 에 하위 명령이 없다 (다른 길로만 부르면 괜찮다)",
                          rel, key=f"contracts|dbus-noctl|{name}"))
    return fs


def ctl_subcommands():
    """sekai-ctl 맨 바깥 case "$1" 의 갈래 — 안쪽 case "$2" 의 갈래(prev·commit 등)는 더 깊이 들여 써 있다"""
    t = code._read(CTL)
    body = t[t.index('case "$1" in'):]
    labels = re.findall(r"^( *)([\w|*-]+)\)", body, re.M)
    if not labels:
        return set()
    top = min(len(ind) for ind, _ in labels)
    subs = set()
    for ind, lab in labels:
        if len(ind) == top:
            subs.update(x for x in lab.split("|") if x and x != "*")
    return subs


def check_ctl():
    fs = []
    subs = ctl_subcommands()
    pat = re.compile(r"sekai-ctl\s+([a-z][\w-]*)")
    seen = set()
    for top in code.TREE_PKG:
        root = os.path.join(code.SRC, top)
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in code.SKIP_DIRS]
            for fn in files:
                p = os.path.join(d, fn)
                if p == CTL or os.path.islink(p) or os.path.splitext(fn)[1] in (".png", ".svg", ".mo", ".jpg"):
                    continue
                try:
                    txt = code._read(p)
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(txt.splitlines(), 1):
                    for sub in pat.findall(line):
                        if sub in subs or (p, sub) in seen:
                            continue
                        seen.add((p, sub))
                        fs.append(Finding("contracts", "error", "없는 sekai-ctl 하위 명령",
                                          f"sekai-ctl {sub} 을 부르는데 sekai-ctl 이 모른다 (도움말만 나온다)",
                                          os.path.relpath(p, code.REPO), i, key=f"contracts|ctl|{sub}|{fn}"))
    return fs


def check():
    return check_settings() + check_dbus() + check_ctl()
