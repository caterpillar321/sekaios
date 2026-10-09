"""우리 코드끼리의 import 그래프 — 순환, 패키지 사이 방향(층), 깔리지 않는 모듈을 부르는 곳."""
import ast
import os
from collections import defaultdict

from . import code
from .finding import Finding

# 층 — 아래 층은 위 층을 부르면 안 된다. 같은 층끼리도 서로 부르지 않는다(앱은 앱을 모른다).
#   0 공용(sekaishell) · 1 설정 저장소(sekaisettings — 설정 앱이지만 store 를 다른 앱이 읽는다) · 2 앱들 · 3 실행 스크립트
LAYER = {"sekaishell": 0, "sekaisettings": 1}
APP_LAYER = 2
# 앱 패키지 안의 GTK 없는 라이브러리 — 다른 앱(설정 페이지 등)이 써도 되는 것. 이유와 함께 적는다
LIBRARIES = {
    "sekaiwine.core": "Wine 엔진·앱 환경 — GTK 없음, 설정 › Windows 앱도 쓴다",
}
SCRIPT_LAYER = 3


def layer(pkg):
    if pkg is None:
        return SCRIPT_LAYER
    return LAYER.get(pkg, APP_LAYER)


def _is_lazy(node, parents):
    """함수 안에서 하는 import 인가 (불릴 때만 — 순환을 피하려고 일부러 미룬 경우가 많다)"""
    p = parents.get(node)
    while p is not None:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return True
        p = parents.get(p)
    return False


def edges():
    """[(보낸 Unit, 받는 모듈 이름, 줄, 함수 안인가)] — 우리 패키지로 가는 것만"""
    ours = code.our_packages()
    mods = {u.module for u in code.py_units() if u.package}
    out = []
    for u in code.py_units():
        tree = u.tree
        if not isinstance(tree, ast.Module):
            continue
        parents = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parents[c] = n
        for n in ast.walk(tree):
            targets = []
            if isinstance(n, ast.Import):
                targets = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                if n.level:                                  # from . import x  /  from .x import y
                    base = (u.module or "").split(".")
                    base = base[: len(base) - n.level + (1 if u.path.endswith("__init__.py") else 0)]
                    mod = ".".join(base + ([n.module] if n.module else []))
                else:
                    mod = n.module or ""
                subs = [f"{mod}.{a.name}" for a in n.names if f"{mod}.{a.name}" in mods]
                # from sekaishell import search → sekaishell.search (패키지 자체가 아니라 그 모듈을 부른다)
                targets = subs + ([mod] if len(subs) < len(n.names) else [])
            for t in targets:
                if t.split(".")[0] in ours:
                    out.append((u, t, n.lineno, _is_lazy(n, parents)))
    return out


def _sccs(graph):
    """강하게 연결된 묶음 (Tarjan) — 크기 2 이상이면 순환"""
    index, low, stack, on, out = {}, {}, [], set(), []
    counter = [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))
    import sys
    sys.setrecursionlimit(10000)
    for v in list(graph):
        if v not in index:
            visit(v)
    return out


def check():
    fs = []
    E = edges()
    mods = {u.module: u for u in code.py_units() if u.package}
    shipped = {u.module for u in code.py_units() if u.package and u.shipped}

    # 1) 없는 모듈 · 싣지 않는 모듈을 부른다 — 새로 깐 PC 에서 ImportError
    for u, t, line, lazy in E:
        known = t in mods or any(m.startswith(t + ".") for m in mods)
        if not known:
            fs.append(Finding("imports", "error", "없는 모듈", f"{t} 을(를) import 하는데 저장소에 그런 모듈이 없다",
                              u.rel, line))
        elif u.shipped and t in mods and t not in shipped:
            fs.append(Finding("imports", "error", "싣지 않는 모듈",
                              f"{t} 은(는) pack-shell.sh 가 패키지에 싣지 않는다 — 깔린 PC 에서 ImportError",
                              u.rel, line))
    for u in code.py_units():
        if u.package and not u.shipped:
            fs.append(Finding("imports", "warn", "싣지 않는 파일",
                              f"{u.module} 은(는) 저장소에만 있고 패키지에 실리지 않는다 (안 쓰면 지우기, 쓰면 싣기)", u.rel))
        elif not u.package and u.deb == "sekai-shell" and not u.shipped:
            fs.append(Finding("imports", "warn", "싣지 않는 파일",
                              f"{u.module} 은(는) pack-shell.sh 가 설치하지 않는다", u.rel))

    # 2) 순환 — 모듈 단위. 맨 위 import 만으로 생긴 순환은 불러오는 순서에 따라 깨진다(error),
    #    함수 안 import 를 넣어야 생기는 순환은 지금은 돌지만 얽힘(info)
    def graph(include_lazy):
        g = defaultdict(set)
        for u, t, _line, lazy in E:
            if (lazy and not include_lazy) or not u.package:
                continue
            if t in mods:
                g[u.module].add(t)
            elif (t + ".__init__") in mods or any(m == t for m in mods):
                g[u.module].add(t)
        return g
    hard = _sccs(graph(False))
    for comp in hard:
        fs.append(Finding("imports", "error", "순환 import (맨 위)",
                          "서로 맨 위에서 부른다 — 먼저 불리는 쪽에 따라 반쯤 만들어진 모듈을 본다: " + " ↔ ".join(comp),
                          mods[comp[0]].rel))
    hard_set = {frozenset(c) for c in hard}
    for comp in _sccs(graph(True)):
        if frozenset(comp) in hard_set:
            continue
        fs.append(Finding("imports", "info", "순환 import (함수 안 포함)",
                          "함수 안 import 로 피한 순환 — 돌지만 두 모듈이 서로 안다: " + " ↔ ".join(comp),
                          mods[comp[0]].rel))

    # 3) 층 — 공용이 앱을, 앱이 다른 앱을 부르면 공용으로 올리거나 방향을 바꿔야 한다
    seen = set()
    for u, t, line, lazy in E:
        src, dst = u.package, t.split(".")[0]
        if src is None or src == dst:
            continue
        ls, ld = layer(src), layer(dst)
        bad = ls < ld or (ls == ld == APP_LAYER)
        if t in LIBRARIES and ls >= 1:
            bad = False
        if not bad or (src, dst, u.rel) in seen:
            continue
        seen.add((src, dst, u.rel))
        why = "공용 모듈이 위 층을 부른다" if ls < ld else "앱이 다른 앱을 부른다"
        fs.append(Finding("imports", "warn", "층 위반", f"{why}: {src} → {t}" + (" (함수 안)" if lazy else ""),
                          u.rel, line))
    return fs


def matrix():
    """패키지 → 패키지 import 수 (보고서용)"""
    m = defaultdict(int)
    for u, t, _l, _lz in edges():
        src = u.package or "(스크립트)"
        dst = t.split(".")[0]
        if src != dst:
            m[(src, dst)] += 1
    return dict(m)


def script_users():
    """실행 스크립트가 부르는 패키지 — 스크립트 이름 → {패키지}"""
    out = defaultdict(set)
    for u, t, _l, _lz in edges():
        if not u.package:
            out[os.path.basename(u.rel)].add(t.split(".")[0])
    return dict(out)
