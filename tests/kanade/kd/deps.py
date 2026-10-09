"""바깥 의존성 — 코드가 부르는 명령·GObject 라이브러리(typelib)·파이썬 모듈이 SekaiOS 를 깔면 함께 깔리는가.

rootfs 의 dpkg 기록으로 주인 패키지를 찾고, sekai-desktop 의 의존성(Depends·Pre-Depends·Recommends)을 따라 깔리는
패키지 안에 있는지 본다. 없으면 새로 깐 PC 나 옛 판에서 올린 PC 에서 그 기능이 깨진다.
which/find_program_in_path 로 먼저 확인하거나 try/except ImportError 로 감싼 것은 "있으면 쓰는 것"으로 보고 낮춘다."""
import ast
import os
import re
import shlex
import sys
from collections import defaultdict

from . import code, pkgdb
from .finding import Finding

# 진짜로 프로세스를 띄우는 함수 — 여기 직접 넘긴 목록의 첫 낱말은 틀림없는 명령이다
SPAWN = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput", "system", "popen",
         "execv", "execvp", "execvpe", "execl", "execlp", "spawnv", "spawnvp", "spawn_async", "spawn_sync",
         "spawn_async_with_pipes", "spawn_command_line_async", "spawn_command_line_sync", "spawnv_async"}
SPAWN_MODS = {"subprocess", "sp", "os", "GLib", "Gio", "pty", "asyncio", "Vte"}
CMD_VAR = re.compile(r"(^|_)(cmd|cmds|argv|args|command|exe|prog)s?($|_)", re.I)
RUNNER = re.compile(r"^(run|Popen|call|check_call|check_output|getoutput|getstatusoutput|system|popen|"
                    r"spawn\w*|exec\w*|_?run\w*|_?sh|_?spawn\w*|_?launch\w*|_?exec\w*|_?cmd|_?call\w*|"
                    r"_?out(put)?|_?helper\w*|_?pk\w*|_?root\w*|_?pipe\w*|_?capture\w*|_?start\w*)$")
SHELLISH = re.compile(r"^(system|getoutput|getstatusoutput|popen|spawn_command_line\w*|_?sh)$")
WRAPPERS = {"pkexec", "sudo", "env", "flatpak-spawn", "systemd-run", "timeout", "nice", "ionice", "setsid",
            "nohup", "stdbuf", "chrt", "taskset", "dbus-run-session", "xvfb-run", "unshare", "runuser"}
GUARD_CALLS = {"which", "find_program_in_path", "have", "_have", "has_cmd", "_has", "command_exists", "_which",
               "exists", "isfile", "access"}
SH_SKIP = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in",
           "function", "select", "time", "!", "{", "}", "[[", "]]", "[", "]", "export", "local", "readonly",
           "declare", "typeset", "return", "exit", "break", "continue", "shift", "set", "unset", "trap", "eval",
           "exec", "source", ".", "cd", "pwd", "echo", "printf", "read", "test", "true", "false", ":", "wait",
           "kill", "command", "type", "hash", "getopts", "let", "alias", "umask", "ulimit", "jobs", "fg", "bg",
           "builtin", "caller", "mapfile", "readarray", "pushd", "popd", "dirs", "disown", "suspend", "logout",
           "shopt", "enable", "help", "history", "times", "compgen", "complete", "esac;;", ";;"}
CMD_RE = re.compile(r"^[A-Za-z][\w.+-]*$")


def _ours(name):
    b = os.path.basename(name)
    return (b.startswith(("sekai", "nenerobo", "worldlink")) or name.startswith(("/usr/lib/sekai", "/usr/libexec/sekai",
            "/usr/share/sekai", "/etc/sekai", "/var/lib/sekai")))


def _callee(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _spawn(call):
    """진짜 프로세스를 띄우는 호출인가 — subprocess.run(…) 은 예, self.run(…) 은 우리 메서드라 아니오"""
    f = call.func
    if isinstance(f, ast.Attribute):
        base = f.value
        while isinstance(base, ast.Attribute):
            base = base.value
        return f.attr in SPAWN and isinstance(base, ast.Name) and base.id in SPAWN_MODS
    return isinstance(f, ast.Name) and f.id in ("Popen", "check_output", "check_call", "execvp", "execv")


def _cmd_var(node, parents):
    """함수에 바로 넘기지 않은 목록 — cmd = [...] 처럼 명령임이 드러나는 이름에 담긴 것만"""
    p = parents.get(node)
    if isinstance(p, ast.Call):
        return False
    tgt = []
    if isinstance(p, ast.Assign):
        tgt = p.targets
    elif isinstance(p, (ast.AnnAssign, ast.AugAssign)):
        tgt = [p.target]
    elif isinstance(p, ast.keyword):
        return bool(p.arg and CMD_VAR.search(p.arg))
    for t in tgt:
        name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else "")
        if name and CMD_VAR.search(name):
            return True
    return False


def _str(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def _seq_cmds(seq):
    """['pkexec', '/usr/libexec/x', …] → ['pkexec', '/usr/libexec/x'] — 감싸는 명령 뒤의 진짜 명령까지"""
    out = []
    for e in seq.elts:
        s = _str(e)
        if s is None:
            break
        out.append(s)
        if os.path.basename(s) not in WRAPPERS or s.startswith("-"):
            break
    return [s for s in out if not s.startswith("-") and "=" not in s]


def _words(cmdline):
    try:
        w = shlex.split(cmdline, comments=True)
    except ValueError:
        w = cmdline.split()
    while w and re.match(r"^\w+=", w[0]):                # FOO=1 명령
        w = w[1:]
    out = []
    for x in w:
        out.append(x)
        if os.path.basename(x) not in WRAPPERS:
            break
    return [x for x in out if not x.startswith("-")]


def local_mods():
    """스크립트 옆에 함께 깔리는 우리 파이썬 모듈 (sekai-restore 옆의 restore_copy.py 따위)"""
    return {os.path.basename(u.path)[:-3] for u in code.units() if u.path.endswith(".py")}


class Use:
    def __init__(self, kind, name, unit, line, guarded=False, strong=True):
        self.kind, self.name, self.unit, self.line = kind, name, unit, line
        self.guarded, self.strong = guarded, strong


def _try_guarded(node, parents):
    """try: … except ImportError/Exception 안인가"""
    p, c = parents.get(node), node
    while p is not None:
        if isinstance(p, ast.Try) and c in p.body:
            for h in p.handlers:
                names = []
                if h.type is None:
                    return True
                for t in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
                    names.append(getattr(t, "id", getattr(t, "attr", "")))
                if any(n in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException", "ValueError",
                             "OSError", "FileNotFoundError", "GLib.Error", "Error") for n in names):
                    return True
        c, p = p, parents.get(p)
    return False


def py_uses(u):
    tree = u.tree
    if not isinstance(tree, ast.Module):
        return []
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    text = u.text
    guards = set(re.findall(r"command -v ([\w.+-]+)", text))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _callee(n) in GUARD_CALLS and n.args:
            s = _str(n.args[0])
            if s:
                guards.add(os.path.basename(s))
                guards.add(s)
    uses = []
    gi_ver = {}
    ours = code.our_packages()
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for n in ast.walk(tree):
        # 명령
        if isinstance(n, ast.Call):
            nm = _callee(n)
            if n.args and isinstance(n.args[0], (ast.List, ast.Tuple)):
                for c in _seq_cmds(n.args[0]):
                    uses.append(Use("cmd", c, u, n.lineno, strong=_spawn(n)))
            elif n.args and SHELLISH.match(nm) and _str(n.args[0]):
                for c in _words(_str(n.args[0])):
                    uses.append(Use("cmd", c, u, n.lineno, strong=True))
            elif nm == "split" and n.args and _str(n.args[0]) and isinstance(n.func, ast.Attribute) \
                    and getattr(n.func.value, "id", "") == "shlex":
                for c in _words(_str(n.args[0])):
                    uses.append(Use("cmd", c, u, n.lineno, strong=False))
            elif nm == "require_version" and len(n.args) >= 2 and _str(n.args[0]) and _str(n.args[1]):
                gi_ver[_str(n.args[0])] = _str(n.args[1])
                uses.append(Use("gi", (_str(n.args[0]), _str(n.args[1])), u, n.lineno,
                                guarded=_try_guarded(n, parents)))
        elif isinstance(n, (ast.List, ast.Tuple)) and _cmd_var(n, parents):
            cs = _seq_cmds(n)
            if cs and (cs[0].startswith("/") or cs[0] in pkgdb.commands()):
                for c in cs:
                    uses.append(Use("cmd", c, u, getattr(n, "lineno", None), strong=False))
        # 파이썬 모듈
        if isinstance(n, ast.Import):
            for a in n.names:
                top = a.name.split(".")[0]
                if top not in stdlib and top not in ours and top != "gi" and top not in local_mods():
                    uses.append(Use("py", top, u, n.lineno, guarded=_try_guarded(n, parents)))
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            top = n.module.split(".")[0]
            if n.module == "gi.repository":
                for a in n.names:
                    uses.append(Use("gi", (a.name, None), u, n.lineno, guarded=_try_guarded(n, parents)))
            elif top not in stdlib and top not in ours and top != "gi" and top not in local_mods():
                uses.append(Use("py", top, u, n.lineno, guarded=_try_guarded(n, parents)))
        if isinstance(n, ast.Import) and any(a.name == "gi" for a in n.names):
            uses.append(Use("py", "gi", u, n.lineno))
    for x in uses:
        if x.kind == "cmd" and (x.name in guards or os.path.basename(x.name) in guards):
            x.guarded = True
        if x.kind == "gi" and x.name[1] is None and x.name[0] in gi_ver:
            x.name = (x.name[0], gi_ver[x.name[0]])
    return uses


def sh_uses(u):
    text = u.text
    funcs = set(re.findall(r"^\s*(?:function\s+)?([\w-]+)\s*\(\)\s*\{?", text, re.M))
    guards = set(re.findall(r"command -v ([\w.+/-]+)", text)) | set(re.findall(r"which ([\w.+/-]+)", text)) \
        | set(re.findall(r"\[ -x ([\w./-]+) \]", text))
    uses = []
    heredoc = None
    for i, raw in enumerate(text.splitlines(), 1):
        if heredoc:
            if raw.strip() == heredoc:
                heredoc = None
            continue
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", raw)
        if m:
            heredoc = m.group(1)
        line = re.sub(r"(^|\s)#.*$", "", raw)
        line = re.sub(r"\"[^\"]*\"|'[^']*'", lambda m: " " if "$(" not in m.group(0) else m.group(0), line)
        for seg in re.split(r"\|\||&&|[;|&`]|\$\(|\(|\)", line):
            w = seg.strip().split()
            while w and (re.match(r"^\w+=", w[0]) or w[0] in ("!", "{", "}", "then", "do", "else", "time")):
                w = w[1:]
            if not w:
                continue
            c = w[0]
            if c in SH_SKIP or c in funcs or c.startswith(("$", "-", "<", ">", "[", "\"", "'", "~", "*", "=")) \
                    or not (CMD_RE.match(c) or c.startswith("/")):
                continue
            if c.endswith(")") or c.endswith(";;"):
                continue
            uses.append(Use("cmd", c, u, i, guarded=c in guards or os.path.basename(c) in guards, strong=False))
            if c in WRAPPERS and len(w) > 1 and (CMD_RE.match(w[1]) or w[1].startswith("/")):
                uses.append(Use("cmd", w[1], u, i, guarded=w[1] in guards, strong=False))
    return uses


def conf_uses():
    """설정 파일 속 명령 — hyprland.conf 의 exec, .desktop 의 Exec=, systemd 의 ExecStart= 따위"""
    out = []
    for top in code.TREE_PKG:
        root = os.path.join(code.SRC, top)
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in code.SKIP_DIRS]
            for fn in files:
                p = os.path.join(d, fn)
                if not fn.endswith((".conf", ".desktop", ".service", "sxhkdrc")) or os.path.islink(p):
                    continue
                u = code.Unit(p, "conf", os.path.relpath(p, root), None, code.TREE_PKG[top])
                try:
                    lines = u.text.splitlines()
                except OSError:
                    continue
                for i, raw in enumerate(lines, 1):
                    s = raw.strip()
                    if s.startswith("#"):
                        continue
                    m = (re.match(r"^exec(?:-once|-shutdown)?\s*=\s*(.+)$", s)
                         or re.match(r"^bind\w*\s*=.*?,\s*exec\s*,\s*(.+)$", s)
                         or re.match(r"^(?:Exec|TryExec|ExecStart|ExecStartPre|ExecStop|ExecReload)=[-@+!:]*(.+)$", s))
                    if not m and fn == "sxhkdrc" and raw.startswith((" ", "\t")):
                        m = re.match(r"^\s+(.+)$", raw)
                    if not m:
                        continue
                    cmdline = re.sub(r"\s+#.*$", "", m.group(1))
                    if cmdline.startswith("$"):
                        continue
                    for c in _words(cmdline):
                        if c.startswith("$") or not (CMD_RE.match(c) or c.startswith("/")):
                            continue
                        out.append(Use("cmd", c, u, i, strong=True))
    return out


def all_uses():
    uses = []
    for u in code.units():
        if not u.shipped:
            continue
        uses.extend(py_uses(u) if u.lang == "py" else sh_uses(u))
    uses.extend(conf_uses())
    return uses


def _owner(x):
    """((패키지, …), 설명)"""
    if x.kind == "cmd":
        c = x.name
        if c.startswith("/"):
            return pkgdb.owner_of(c), c
        return pkgdb.commands().get(c, ()), c
    if x.kind == "py":
        p = "python3-gi" if x.name == "gi" else pkgdb.pymodules().get(x.name)
        return ((p,) if p else ()), f"파이썬 모듈 {x.name}"
    ns, ver = x.name
    vers = pkgdb.typelibs().get(ns, {})
    if ver:
        return ((vers[ver],) if ver in vers else ()), f"{ns}-{ver}.typelib"
    return tuple(vers.values()), f"{ns}.typelib (버전 지정 없음)"


def check():
    if not pkgdb.available():
        return [Finding("deps", "warn", "rootfs 없음", f"{pkgdb.ROOTFS} 에 dpkg 기록이 없어 바깥 의존성을 못 본다 "
                        "(KANADE_ROOTFS 로 다른 설치본을 줄 수 있다)")]
    fs = []
    grouped = defaultdict(list)              # (판정, 주인, 설명) → [Use]
    for x in all_uses():
        if x.kind == "cmd" and _ours(x.name):
            continue
        # 설치기는 라이브 ISO 에서 돈다 — sekai-installer 의 의존성으로 본다
        root = "sekai-installer" if x.unit.deb == "sekai-installer" else pkgdb.ROOT_PKG
        full, strict = pkgdb.closure(root), pkgdb.closure(root, recommends=False)
        pkgs, what = _owner(x)
        if not pkgs:
            if x.kind == "cmd" and not x.strong:
                continue                     # 목록·문자열이 명령인지 확실치 않다
            if x.kind == "cmd" and x.name.startswith("/") and not x.name.startswith(("/usr/", "/bin/", "/sbin/")):
                continue                     # /proc/… /sys/… 따위
            verdict, pkg = "unknown", None
        elif any(p in pkgdb.OURS or p == "(alternatives)" or p in strict for p in pkgs):
            continue
        elif any(p in full for p in pkgs):
            verdict, pkg = "recommends", next(p for p in pkgs if p in full)
        else:
            verdict, pkg = "undeclared", " / ".join(pkgs)
        grouped[(verdict, pkg, what, x.guarded)].append(x)
    for (verdict, pkg, what, guarded), xs in sorted(grouped.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), kv[0][2])):
        where = sorted({(x.unit.rel, x.line) for x in xs})
        files = ", ".join(sorted({os.path.basename(r) for r, _ in where})[:6])
        p0, l0 = where[0]
        if verdict == "undeclared":
            lvl = "info" if guarded else "error"
            msg = (f"{what} — 주인 {pkg} 이(가) sekai-desktop 의존성으로 깔리지 않는다"
                   + (" (먼저 있는지 확인하고 쓴다 — 선택 기능이면 Suggests 로라도)" if guarded
                      else " — 새로 깐 PC·옛 판에서 올린 PC 에서 깨진다") + f" [{files}]")
            fs.append(Finding("deps", lvl, "선언 안 된 의존성", msg, p0, l0, key=f"deps|undeclared|{what}"))
        elif verdict == "recommends":
            lvl = "info"
            fs.append(Finding("deps", lvl, "Recommends 로만 깔림",
                              f"{what} — 주인 {pkg} 이(가) Recommends 로만 깔린다 (--no-install-recommends 면 없음)"
                              + (" · 먼저 확인함" if guarded else "") + f" [{files}]", p0, l0,
                              key=f"deps|recommends|{what}"))
        else:
            lvl = "info" if guarded else "warn"
            fs.append(Finding("deps", lvl, "주인 모를 명령",
                              f"{what} — rootfs 의 어느 패키지에도 없다 (오타·다른 배포판 명령·외부 설치?)"
                              + (" · 먼저 확인함" if guarded else "") + f" [{files}]", p0, l0,
                              key=f"deps|unknown|{what}"))
    return fs
