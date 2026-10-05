"""폴더 색 — 탐색기·바탕 화면·파일 고르기 창의 폴더 아이콘을 강조색으로 (설정 › 색 및 모양).

Papirus 의 폴더 아이콘(folder-blue* · user-blue* · inode-directory)은 SVG 이고 색이 셋이다:
    #5294e2 앞면 · #4877b1 뒷면 · #1d344f 안의 그림
그것만 강조색으로 다시 칠한 사본을 사용자 아이콘 테마 ~/.local/share/icons/Sekai-Folders-{Dark,Light} 에 쓰고
(Papirus-Dark·Light 를 이어받는다 — 나머지 아이콘은 그대로), theme.icon_theme 이 그 테마를 고른다.
Papirus 의 정해진 색 목록(papirus-folders)이 아니라 사용자가 고른 강조색 그대로. 색이 같으면 다시 쓰지 않는다.
"""
import configparser
import os
import re
import shutil

BASE = "/usr/share/icons/Papirus"
DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "icons")
NAMES = {"dark": ("Sekai-Folders-Dark", "Papirus-Dark"), "light": ("Sekai-Folders-Light", "Papirus-Light")}
FRONT, BACK, GLYPH = "#5294e2", "#4877b1", "#1d344f"
SUBDIRS = ("places", "mimetypes")
_BLUE = re.compile(r"^(folder|user)-blue(-|\.svg$)")


def theme_dir(mode):
    return os.path.join(DATA, NAMES["light" if mode == "light" else "dark"][0])


def theme_name(mode):
    """쓸 수 있으면 폴더 색 테마 이름, 아니면 None"""
    d = theme_dir(mode)
    return os.path.basename(d) if os.path.isfile(os.path.join(d, "index.theme")) else None


def _shade(hexcol, f):
    h = hexcol.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * f))) for c in (r, g, b))


def _recolor(svg, accent):
    out = svg
    for old, new in ((FRONT, accent), (BACK, _shade(accent, 0.82)), (GLYPH, _shade(accent, 0.32))):
        out = re.sub(re.escape(old), new, out, flags=re.I)
    return out


def _targets():
    """(크기 폴더/하위, 이름, 원본 실제 경로) — 폴더 아이콘만"""
    for size in sorted(os.listdir(BASE)):
        for sub in SUBDIRS:
            d = os.path.join(BASE, size, sub)
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if not name.endswith(".svg"):
                    continue
                real = os.path.realpath(os.path.join(d, name))
                base = os.path.basename(real)
                if _BLUE.match(base) or (sub == "mimetypes" and name.startswith("inode-directory")):
                    yield f"{size}/{sub}", name, real


def generate(accent, force=False):
    """두 테마를 (강조색이 바뀌었으면) 새로 쓴다 → 바뀌었으면 True"""
    accent = (accent or "").lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", accent) or not os.path.isdir(BASE):
        return False
    stamp = os.path.join(theme_dir("dark"), ".accent")
    try:
        if not force and open(stamp).read().strip() == accent and theme_name("light"):
            return False
    except OSError:
        pass
    base_index = configparser.RawConfigParser(strict=False)
    base_index.optionxform = str
    base_index.read(os.path.join(BASE, "index.theme"), encoding="utf-8")
    cache = {}
    dirs = set()
    for mode in ("dark", "light"):
        name, parent = NAMES[mode]
        root = os.path.join(DATA, name)
        tmp = root + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, fname, real in _targets():
            try:
                if real not in cache:
                    with open(real, encoding="utf-8") as f:
                        src = f.read()
                    cache[real] = _recolor(src, accent) if (FRONT in src.lower() or BACK in src.lower()) else None
            except (OSError, UnicodeDecodeError):
                cache[real] = None
            if cache[real] is None:
                continue
            os.makedirs(os.path.join(tmp, rel), exist_ok=True)
            with open(os.path.join(tmp, rel, fname), "w", encoding="utf-8") as f:
                f.write(cache[real])
            dirs.add(rel)
        idx = configparser.RawConfigParser()
        idx.optionxform = str
        idx["Icon Theme"] = {"Name": name, "Comment": "SekaiOS — 폴더를 강조색으로 (Papirus 바탕)",
                             "Inherits": f"{parent},hicolor", "Directories": ",".join(sorted(dirs)), "Hidden": "true"}
        for rel in sorted(dirs):
            if base_index.has_section(rel):
                idx[rel] = dict(base_index[rel])
        with open(os.path.join(tmp, "index.theme"), "w", encoding="utf-8") as f:
            idx.write(f)
        with open(os.path.join(tmp, ".accent"), "w") as f:
            f.write(accent + "\n")
        shutil.rmtree(root + ".old", ignore_errors=True)
        if os.path.isdir(root):
            os.rename(root, root + ".old")
        os.rename(tmp, root)
        shutil.rmtree(root + ".old", ignore_errors=True)
    return True


def remove():
    for mode in ("dark", "light"):
        shutil.rmtree(theme_dir(mode), ignore_errors=True)
