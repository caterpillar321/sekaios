"""죽은 코드 — 아무도 부르지 않는 함수·클래스(vulture), CSS 에만 있고 코드가 쓰지 않는 클래스."""
import os
import re

from . import code, dupes
from .finding import Finding

# GTK 가상 함수(do_*), 이름으로 불리는 D-Bus/AT-SPI 처리기 따위 — vulture 가 못 보는 쓰임
IGNORE = re.compile(r"^(do_\w+|__\w+__|on_\w+|_on_\w+|handle_\w+|Get\w*|Set\w*|Activate\w*|vfunc_\w+|test_\w+)$")


def check_vulture():
    try:
        import vulture
    except ImportError:
        return [Finding("dead", "error", "vulture 없음", "vulture 를 못 찾아 검사를 못 했다 — 빠진 채 통과하지 않게 오류 (uv 로 돌리면 저절로 깔린다)")]
    v = vulture.Vulture(verbose=False)
    units = [u for u in code.py_units() if not (u.package and not u.shipped)]
    for u in units:
        v.scan(u.text, filename=u.path)
    fs = []
    text = _code_text()
    for item in v.get_unused_code(min_confidence=60):
        if item.typ in ("import", "variable", "attribute", "property", "unreachable_code") and item.confidence < 100:
            continue
        name = item.name or ""
        if IGNORE.match(name) or name.startswith("_") and item.typ == "variable":
            continue                         # 함수 모양이 정한 인자(_signum 따위)는 일부러 안 쓴다
        if item.typ in ("function", "method") and re.search(r"[\"']" + re.escape(name) + r"[\"']", text):
            continue                         # 이름(문자열)으로 부른다 — getattr·D-Bus 처리기 (블루투스 ask_code 등)
        rel = os.path.relpath(str(item.filename), code.REPO)
        kind = {"function": "안 쓰는 함수", "method": "안 쓰는 메서드", "class": "안 쓰는 클래스",
                "unreachable_code": "닿지 않는 코드", "import": "안 쓰는 import", "variable": "안 쓰는 변수",
                "attribute": "안 쓰는 속성", "property": "안 쓰는 속성"}.get(item.typ, item.typ)
        lvl = "warn" if item.typ == "unreachable_code" else "info"
        size = getattr(item, "size", 1) or 1
        fs.append(Finding("dead", lvl, kind, f"{item.name} ({size}줄, 확신 {item.confidence}%)", rel,
                          item.first_lineno, key=f"dead|{rel}|{item.typ}|{item.name}"))
    return fs


def _code_text():
    out = []
    for top in code.TREE_PKG:
        root = os.path.join(code.SRC, top)
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in code.SKIP_DIRS]
            if "/themes/" in d + "/":
                continue
            for fn in files:
                p = os.path.join(d, fn)
                if fn.endswith((".css", ".png", ".svg", ".jpg", ".mo", ".po", ".ttf", ".wav", ".oga")) \
                        or os.path.islink(p):
                    continue
                try:
                    out.append(code._read(p))
                except (OSError, UnicodeDecodeError):
                    pass
    return "\n".join(out)


def check_css():
    """우리 CSS 의 클래스 중 코드 어디에도 이름이 안 나오는 것 — 지운 위젯의 찌꺼기"""
    text = _code_text()
    words = set(re.findall(r"[A-Za-z_][\w-]*", text))
    fs = []
    for path in dupes.css_files():
        rel = os.path.relpath(path, code.REPO)
        seen = set()
        for sel, _props, line, _g in dupes._css_rules(path):
            for cls in re.findall(r"\.([A-Za-z_][\w-]*)", sel):
                if cls in seen or cls in words:
                    continue
                seen.add(cls)
                # 이름을 조립해 쓰는 경우(f"level-{n}" 따위) — 앞부분이 코드에 있으면 넘어간다
                stem = re.sub(r"-[\w]+$", "-", cls)
                if stem != cls and re.search(re.escape(stem) + r"\{|" + re.escape(stem) + r"\"\s*\+|"
                                             + re.escape(stem) + r"%", text):
                    continue
                fs.append(Finding("dead", "info", "안 쓰는 CSS 클래스",
                                  f".{cls} — CSS 에만 있고 코드가 쓰지 않는다", rel, line, key=f"dead|css|{rel}|{cls}"))
    return fs


def check():
    return check_vulture() + check_css()
