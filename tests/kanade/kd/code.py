"""저장소의 코드 찾기 — 파이썬 모듈·스크립트·셸 스크립트, 어디에 깔리는지(어느 deb), 구문 트리."""
import ast
import os
import re
from functools import lru_cache

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # tests/kanade
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
SHELL = os.path.join(SRC, "sekai-shell")
PACK = os.path.join(REPO, "scripts", "pack-shell.sh")

# src/ 아래 폴더 → 그 내용이 실리는 deb (pack-shell.sh 를 따른다)
TREE_PKG = {"sekai-shell": "sekai-shell", "sekai-de": "sekai-de", "sekaios-base": "sekaios-base",
            "sekai-installer": "sekai-installer"}
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_EXT = {".png", ".svg", ".jpg", ".jpeg", ".mo", ".po", ".pot", ".ttf", ".otf", ".wav", ".oga", ".ogg",
            ".ico", ".gz", ".xcf", ".css", ".json", ".xml", ".desktop", ".conf", ".rules", ".policy",
            ".service", ".md", ".txt", ".ini", ".theme", ".svgz", ".pkla", ".preset", ".socket", ".timer",
            ".target", ".path", ".frag", ".vert", ".glsl", ".c", ".h", ".cpp"}


class Unit:
    """코드 파일 하나"""

    def __init__(self, path, lang, module=None, package=None, deb=None, shipped=True):
        self.path = path                        # 절대 경로
        self.rel = os.path.relpath(path, REPO)
        self.lang = lang                        # "py" | "sh"
        self.module = module                    # 파이썬: sekaishell.search · 스크립트: sekai-panel
        self.package = package                  # 파이썬 패키지 이름 (sekaishell) · 스크립트면 None
        self.deb = deb                          # 실리는 deb
        self.shipped = shipped                  # pack-shell.sh 가 실제로 싣는가

    def __repr__(self):
        return f"<{self.lang} {self.module or self.rel}>"

    @property
    def text(self):
        return _read(self.path)

    @property
    def tree(self):
        return _parse(self.path) if self.lang == "py" else None


@lru_cache(maxsize=None)
def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


@lru_cache(maxsize=None)
def _parse(path):
    try:
        return ast.parse(_read(path), filename=path)
    except SyntaxError as e:
        return e


def _lang(path):
    if path.endswith(".py"):
        return "py"
    if os.path.splitext(path)[1] in SKIP_EXT:
        return None
    try:
        with open(path, "rb") as f:
            head = f.read(120)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    line = head.split(b"\n", 1)[0]
    if b"python" in line:
        return "py"
    if re.search(rb"\b(ba|da)?sh\b", line):
        return "sh"
    return None


def pack_text():
    return _read(PACK)


@lru_cache(maxsize=None)
def shipped_shell_items():
    """pack-shell.sh 가 sekai-shell 에 싣는 것: (스크립트 파일 이름 집합, 파이썬 패키지 폴더 집합)"""
    t = pack_text()
    scripts = set(re.findall(r'"\$SRC/((?:lib/)?[\w.-]+)"\s+"\$STAGE/', t))
    pkgs = set()
    for m in re.finditer(r'"\$SRC/(\w+)(?:/pages)?"/\*\.py', t):
        pkgs.add(m.group(1) + ("/pages" if "/pages" in m.group(0) else ""))
    for m in re.finditer(r"for pkg in ([\w ]+); do", t):
        pkgs.update(m.group(1).split())
    xdir = re.search(r'for f in "\$SRC"/lib/x11/\*', t)
    if xdir:
        scripts.update("lib/x11/" + n for n in os.listdir(os.path.join(SHELL, "lib", "x11")))
    return scripts, pkgs


@lru_cache(maxsize=None)
def units():
    out = []
    scripts, pkgs = shipped_shell_items()
    for top, deb in TREE_PKG.items():
        root = os.path.join(SRC, top)
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for fn in sorted(files):
                p = os.path.join(d, fn)
                if os.path.islink(p):
                    continue
                lang = _lang(p)
                if not lang:
                    continue
                rel = os.path.relpath(p, root)
                module, package, shipped = None, None, True
                if top == "sekai-shell":
                    parts = rel.split(os.sep)
                    if lang == "py" and fn.endswith(".py") and len(parts) >= 2 and re.match(r"^\w+$", parts[0]):
                        package = parts[0]
                        sub = "/".join(parts[:-1])
                        module = ".".join(parts[:-1] + ([] if fn == "__init__.py" else [fn[:-3]]))
                        shipped = sub in pkgs
                    else:
                        module = rel
                        shipped = rel in scripts
                else:
                    module = rel                    # sekai-de 등은 트리를 통째로 싣는다
                out.append(Unit(p, lang, module, package, deb, shipped))
    return out


def py_units():
    return [u for u in units() if u.lang == "py"]


def our_packages():
    return {u.package for u in units() if u.package}
