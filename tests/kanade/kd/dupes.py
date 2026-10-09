"""중복 구현 — 똑같은·거의 같은 함수, 모듈마다 다시 만든 도우미, 두 군데서 다르게 정의한 CSS 클래스."""
import ast
import hashlib
import os
import re
from collections import defaultdict

from . import code
from .finding import Finding

MIN_STMTS = 4            # 이보다 짧은 함수는 같아도 우연일 수 있다 (게터 따위)
NEAR = 0.80              # 거의 같다 — 정규화한 토큰 7-그램 자카드 유사도
SHINGLE = 7
# 알고 남겨 두는 중복 — (짝, 이유). 경고 대신 참고로만 보인다. 이유가 사라지면 지울 것
ALLOWED = {
    frozenset({"sekai-apps.run_status", "sekai_apt.run_status"}):
        "sekai-apps 는 sekai-de, sekai_apt 는 sekaios-base — 두 패키지는 서로 의존하지 않아 공용 모듈을 둘 곳이 없다",
    frozenset({"sekai-apps.guard", "sekai-update.guard"}):
        "sekai-apps 는 sekai-de, sekai-update 는 sekaios-base — 두 패키지는 서로 의존하지 않아 공용 모듈을 둘 곳이 없다",
}
BOILER = {"__init__", "main", "do_startup", "do_activate", "do_command_line"}


class _Norm(ast.NodeTransformer):
    """이름·문자열 내용에 상관없이 모양만 비교하게 — 지역 이름은 순서대로 v0, v1 …"""

    def __init__(self):
        self.names = {}

    def _n(self, name):
        if name not in self.names:
            self.names[name] = f"v{len(self.names)}"
        return self.names[name]

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id=self._n(node.id), ctx=node.ctx), node)

    def visit_arg(self, node):
        node.arg = self._n(node.arg)
        node.annotation = None
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="S"), node)
        return node


def _body(fn):
    b = list(fn.body)
    if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
            and isinstance(b[0].value.value, str):
        b = b[1:]                                    # 설명 글은 빼고
    return b


def _count(stmts):
    return sum(1 for s in stmts for n in ast.walk(s) if isinstance(n, ast.stmt))


def _tokens(fn):
    norm = _Norm()
    mod = ast.Module(body=[norm.visit(ast.parse(ast.unparse(s)).body[0]) for s in _body(fn)], type_ignores=[])
    txt = ast.dump(mod, annotate_fields=False, include_attributes=False)
    return re.findall(r"\w+|[^\w\s]", txt)


def functions():
    """[(Unit, 함수 노드, 이름, 정규화 토큰)]"""
    out = []
    for u in code.py_units():
        if not u.shipped and u.package:
            continue
        tree = u.tree
        if not isinstance(tree, ast.Module):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = _body(n)
                if _count(body) < MIN_STMTS:
                    continue
                try:
                    toks = _tokens(n)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                out.append((u, n, n.name, toks))
    return out


def _sh(toks):
    return {hash(tuple(toks[i:i + SHINGLE])) for i in range(max(1, len(toks) - SHINGLE + 1))}


def _short(u):
    return u.module if u.package else os.path.basename(u.rel)


def check_functions():
    fs = []
    fns = functions()
    # 1) 똑같은 함수 (모양이 같다)
    exact = defaultdict(list)
    for u, n, name, toks in fns:
        exact[hashlib.sha1(" ".join(toks).encode()).hexdigest()].append((u, n, name, toks))
    grouped = set()
    for h, xs in exact.items():
        files = {x[0].rel for x in xs}
        if len(xs) < 2 or len(files) < 2:
            continue
        for x in xs:
            grouped.add(id(x[1]))
        names = ", ".join(sorted({f"{_short(u)}.{name}" for u, _n, name, _t in xs}))
        u0, n0 = xs[0][0], xs[0][1]
        lvl = "warn" if _count(_body(n0)) >= 8 else "info"
        fs.append(Finding("dupes", lvl, "똑같은 함수", f"{len(xs)}곳에 같은 모양 ({_count(_body(n0))}문장): {names}",
                          u0.rel, n0.lineno, key=f"dupes|exact|{names}"))
    # 2) 거의 같은 함수 — 다른 파일끼리, 자카드 유사도
    sig = [(u, n, name, _sh(toks)) for u, n, name, toks in fns if id(n) not in grouped and len(toks) > 60]
    index = defaultdict(list)
    for i, (_u, _n, _name, sh) in enumerate(sig):
        for s in sh:
            index[s].append(i)
    pairs = defaultdict(int)
    for lst in index.values():
        if len(lst) > 40:                            # 흔한 조각(GTK 위젯 만들기 따위)은 후보 찾기에서 뺀다
            continue
        for a in range(len(lst)):
            for b in range(a + 1, len(lst)):
                pairs[(lst[a], lst[b])] += 1
    seen = set()
    for (i, j), common in sorted(pairs.items(), key=lambda kv: -kv[1]):
        ui, ni, nmi, si = sig[i]
        uj, nj, nmj, sj = sig[j]
        if ui.rel == uj.rel:
            continue
        if nmi in BOILER and nmj in BOILER:
            continue                     # 생성자·main 은 원래 비슷하게 생겼다 — 똑같을 때만(위) 본다
        jac = len(si & sj) / len(si | sj)
        if jac < NEAR:
            continue
        key = tuple(sorted([f"{ui.rel}:{nmi}", f"{uj.rel}:{nmj}"]))
        if key in seen:
            continue
        seen.add(key)
        size = max(_count(_body(ni)), _count(_body(nj)))
        why = ALLOWED.get(frozenset({f"{_short(ui).removesuffix('.py')}.{nmi}", f"{_short(uj).removesuffix('.py')}.{nmj}"}))
        fs.append(Finding("dupes", "warn" if size >= 8 and not why else "info", "거의 같은 함수",
                          f"{_short(ui)}.{nmi} ≈ {_short(uj)}.{nmj} ({jac:.0%} 같음, {size}문장) — "
                          f"{uj.rel}:{nj.lineno}" + (f" · 알고 남김: {why}" if why else ""),
                          ui.rel, ni.lineno, key="dupes|near|" + "|".join(key)))
    return fs


def check_helpers():
    """같은 이름의 작은 도우미를 여러 모듈이 따로 만든 것 (_read · rd · num · _int · run …) — 공용으로 올릴 후보"""
    fs = []
    by = defaultdict(list)
    for u in code.py_units():
        tree = u.tree
        if not isinstance(tree, ast.Module) or (u.package and not u.shipped):
            continue
        for n in tree.body:                          # 모듈 맨 위 함수만
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _count(_body(n)) <= 12:
                by[n.name.lstrip("_")].append((u, n))
    for name, xs in sorted(by.items()):
        files = {u.rel for u, _ in xs}
        if len(files) < 3 or name in ("main", "init", "setup", "run", "build", "close"):
            continue
        where = ", ".join(sorted({_short(u) for u, _ in xs})[:8]) + (" …" if len(files) > 8 else "")
        u0, n0 = xs[0]
        fs.append(Finding("dupes", "info", "모듈마다 다시 만든 도우미",
                          f"{name}() 를 {len(files)}곳이 따로 만든다: {where}", u0.rel, n0.lineno,
                          key=f"dupes|helper|{name}"))
    return fs


# ── CSS ──
def _css_rules(path):
    """[(선택자, {속성: 값}, 줄, 묶음 크기)] — @media 안의 규칙도 펼친다(어떤 @media 인지는 선택자 앞에 붙인다)"""
    txt = code._read(path)
    txt = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), txt)
    out = []
    pos, media = 0, []
    for m in re.finditer(r"([^{}]*)\{|\}", txt):
        if m.group(0) == "}":
            if media and media[-1][1] == "open-rule":
                media.pop()
            elif media:
                media.pop()
            continue
        sel = m.group(1).strip()
        line = txt.count("\n", 0, m.start(1) + len(m.group(1)) - len(m.group(1).lstrip())) + 1
        if sel.startswith("@"):
            media.append((sel, "at"))
            continue
        end = txt.find("}", m.end())
        body = txt[m.end():end]
        props = {}
        for decl in body.split(";"):
            k, _, v = decl.partition(":")
            if k.strip() and v.strip():
                props[k.strip()] = re.sub(r"\s+", " ", v.strip())
        prefix = " ".join(x[0] for x in media if x[1] == "at")
        sels = [x for x in sel.split(",") if x.strip()]
        for s in sels:
            out.append(((prefix + " " if prefix else "") + re.sub(r"\s+", " ", s.strip()), props, line, len(sels)))
        media.append((sel, "open-rule"))
    return out


def css_files():
    """우리가 손으로 쓴 CSS — share/themes/ 는 Fluent 를 빌드해 나온 결과물(scripts/build-theme.sh)이라 뺀다"""
    out = []
    for d, dirs, files in os.walk(code.SRC):
        dirs[:] = [x for x in dirs if x not in code.SKIP_DIRS]
        for fn in files:
            if fn.endswith(".css") and "/themes/" not in d + "/":
                out.append(os.path.join(d, fn))
    return sorted(out)


def check_css():
    """같은 파일 안에서 같은 선택자를 두 번 정의하며 같은 속성에 다른 값을 준 것 — 뒤엣것이 앞엣것을 몰래 덮는다.
    (2026-10-09 검색 창 .preview-title 13pt 가 작업 표시줄 미리보기의 .preview-title 9pt 에 덮인 사고)"""
    fs = []
    for path in css_files():
        rules = _css_rules(path)
        first = {}
        for sel, props, line, group in rules:
            if sel not in first:
                first[sel] = (props, line, group)
                continue
            p0, l0, g0 = first[sel]
            clash = sorted(k for k in props if k in p0 and p0[k] != props[k])
            # 여럿을 한꺼번에 초기화한 묶음 규칙(.a, .b, .c { … })을 뒤에서 하나씩 다듬는 건 일부러다 —
            #   둘 다 그 선택자 하나만 쓴 규칙인데 멀리 떨어져 있으면 서로 모르고 겹친 것
            if clash and line - l0 > 15 and g0 == 1 and group == 1:
                rel = os.path.relpath(path, code.REPO)
                fs.append(Finding("dupes", "warn", "CSS 클래스 겹침",
                                  f"{sel} 를 {l0}줄과 {line}줄에서 따로 정의 — {', '.join(clash)} 가 다르다 "
                                  f"(뒤엣것이 앞엣것을 덮는다: {', '.join(f'{k} {p0[k]} → {props[k]}' for k in clash[:3])})",
                                  rel, line, key=f"dupes|css|{rel}|{sel}"))
            first[sel] = ({**p0, **props}, line, group)
    return fs


def check():
    return check_functions() + check_helpers() + check_css()
