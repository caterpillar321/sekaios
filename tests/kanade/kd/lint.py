"""기본 린트 — pyflakes(정의 안 된 이름·안 쓰는 import 따위), shellcheck(셸 스크립트)."""
import json
import os
import shutil
import subprocess
import sys

from . import code
from .finding import Finding

PYFLAKES_LEVEL = {"UndefinedName": "error", "UndefinedExport": "error", "UndefinedLocal": "error",
                  "DuplicateArgument": "error", "ReturnOutsideFunction": "error", "YieldOutsideFunction": "error",
                  "ContinueOutsideLoop": "error", "BreakOutsideLoop": "error", "TooManyExpressionsInStarredAssignment": "error",
                  "PercentFormatInvalidFormat": "error", "StringDotFormatInvalidFormat": "error",
                  "FStringMissingPlaceholders": "info", "UnusedImport": "info", "UnusedVariable": "info",
                  "UnusedAnnotation": "info", "RedefinedWhileUnused": "warn", "ImportShadowedByLoopVar": "warn",
                  "ImportStarUsed": "warn", "ImportStarUsage": "warn", "IsLiteral": "warn", "MultiValueRepeatedKeyLiteral": "warn",
                  "MultiValueRepeatedKeyVariable": "warn"}


def check_pyflakes():
    try:
        from pyflakes import checker
    except ImportError:
        return [Finding("lint", "error", "pyflakes 없음", "pyflakes 를 못 찾아 검사를 못 했다 — 빠진 채 통과하지 않게 오류 (uv 로 돌리면 저절로 깔린다)")]
    fs = []
    for u in code.py_units():
        if u.package and not u.shipped:
            continue
        tree = u.tree
        if isinstance(tree, SyntaxError):
            fs.append(Finding("lint", "error", "문법 오류", f"{tree.msg}", u.rel, tree.lineno))
            continue
        w = checker.Checker(tree, filename=u.rel)
        for m in w.messages:
            kind = type(m).__name__
            lvl = PYFLAKES_LEVEL.get(kind, "warn")
            text = m.message % m.message_args
            fs.append(Finding("lint", lvl, f"pyflakes {kind}", text, u.rel, m.lineno,
                              key=f"lint|py|{u.rel}|{kind}|{text}"))
    return fs


def check_shellcheck():
    sc = shutil.which("shellcheck")
    if not sc:
        return [Finding("lint", "error", "shellcheck 없음", "shellcheck 를 못 찾아 검사를 못 했다 — 빠진 채 통과하지 않게 오류 (uv 로 돌리면 저절로 깔린다)")]
    fs = []
    units = [u for u in code.units() if u.lang == "sh" and u.shipped]
    if not units:
        return fs
    r = subprocess.run([sc, "-f", "json", "-S", "warning", "-x"] + [u.path for u in units],
                       capture_output=True, text=True, cwd=code.REPO)
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        return [Finding("lint", "warn", "shellcheck 실패", (r.stderr or "")[:200])]
    for it in items:
        lvl = {"error": "error", "warning": "warn"}.get(it.get("level"), "info")
        rel = os.path.relpath(it["file"], code.REPO)
        fs.append(Finding("lint", lvl, f"shellcheck SC{it['code']}", it["message"], rel, it.get("line"),
                          key=f"lint|sh|{rel}|SC{it['code']}|{it['message']}"))
    return fs


def check_python():
    """SekaiOS 는 3.13 — 3.12 밑에서는 새 문법을 문법 오류로 잘못 보거나 못 읽는다 (uv 가 없어 서버 3.10 으로 돈 경우)"""
    if sys.version_info >= (3, 12):
        return []
    v = ".".join(map(str, sys.version_info[:3]))
    return [Finding("lint", "error", "파이썬 버전", f"파이썬 {v} 로 돌았다 — 3.12 이상(uv)이 있어야 제대로 읽는다")]


def check():
    return check_python() + check_pyflakes() + check_shellcheck()
