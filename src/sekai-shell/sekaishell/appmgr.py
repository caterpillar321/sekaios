"""앱 설치·제거 — 앱 설치 관리자(sekai-appinstall)와 설정 › 설치된 앱이 같이 쓴다.

실제 일은 /usr/libexec/sekai/sekai-apps 가 한다 (설치·제거는 pkexec 로, 미리 보기는 보통 권한으로).
여기서는 그 도우미를 돌리고 출력 줄(INFO/STATE/ADD/UPG/DEL/BLOCK/PROGRESS/APP/DONE/ERROR)을 읽는다.
"""
import configparser
import os
import re
import subprocess
import tarfile
import threading

from gi.repository import GLib

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = next((p for p in ("/usr/libexec/sekai/sekai-apps",
                           os.path.join(HERE, "..", "..", "sekai-de", "usr", "libexec", "sekai", "sekai-apps"))
               if os.path.exists(p)), "/usr/libexec/sekai/sekai-apps")
CANCELLED = (126, 127)          # pkexec: 인증 창을 닫았거나 인증에 실패


def run_helper(args, on_line, on_done, root=False, stdin_path=None):
    """도우미를 작업 스레드에서 돌린다. on_line(종류, 나머지)·on_done(종료 코드) 은 GTK 스레드에서 불린다."""
    def work():
        stdin = None
        try:
            if stdin_path is not None:
                stdin = open(stdin_path, "rb")
            cmd = (["pkexec", HELPER] if root else [HELPER]) + list(args)
            p = subprocess.Popen(cmd, stdin=stdin or subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace")
        except OSError as e:
            GLib.idle_add(on_line, "ERROR", f"실행하지 못했습니다: {e}")
            GLib.idle_add(on_done, 1)
            return
        finally:
            if stdin is not None:
                stdin.close()                   # 자식이 물려받았다 — 우리 쪽은 닫는다
        for line in p.stdout:
            kind, _, rest = line.rstrip("\n").partition(" ")
            if kind.isupper():
                GLib.idle_add(on_line, kind, rest)
        GLib.idle_add(on_done, p.wait())
    threading.Thread(target=work, daemon=True).start()


class Plan:
    """inspect · plan-remove 의 결과"""

    def __init__(self):
        self.info = {}
        self.desc = []
        self.state, self.installed = None, None
        self.add, self.upg, self.dele = [], [], []
        self.blocks, self.error = [], None
        self.done = False

    def feed(self, kind, rest):
        if kind == "INFO":
            k, _, v = rest.partition(" ")
            self.info[k] = v
        elif kind == "DESC":
            self.desc.append(rest)
        elif kind == "STATE":
            parts = rest.split()
            self.state = parts[0] if parts else None
            self.installed = parts[1] if len(parts) > 1 else None
        elif kind == "ADD":
            self.add.append(rest.split()[0])
        elif kind == "UPG":
            self.upg.append(rest.split()[0])
        elif kind == "DEL":
            self.dele.append(rest.split()[0])
        elif kind == "BLOCK":
            self.blocks.append(rest)
        elif kind == "ERROR":
            self.error = rest
        elif kind == "DONE":
            self.done = True


def human_size(kb):
    try:
        kb = int(kb)
    except (TypeError, ValueError):
        return None
    if kb < 1024:
        return f"{kb} KB"
    if kb < 1024 * 1024:
        return f"{kb / 1024:.1f} MB".replace(".0 MB", " MB")
    return f"{kb / 1024 / 1024:.1f} GB"


def publisher(maintainer):
    """'이름 <메일>' → 이름"""
    m = re.match(r"^\s*(.*?)\s*<[^>]*>\s*$", maintainer or "")
    return (m.group(1) if m else (maintainer or "")).strip() or None


# ── .deb 안의 앱 이름·아이콘 (설치 전에 보여 주려고) ──
def _lang_keys():
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").split(".")[0]
    keys = []
    if lang and lang not in ("C", "POSIX"):
        keys.append(f"Name[{lang}]")
        if "_" in lang:
            keys.append(f"Name[{lang.split('_')[0]}]")
    return keys + ["Name"]


def _members(deb):
    """dpkg-deb 로 꾸러미 안의 파일을 차례로 (멤버, 파일 객체) — 스트림이라 한 번만 지나간다."""
    p = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", deb], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|") as tf:
            for m in tf:
                yield m, tf
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def _norm(name):
    return "/" + name.lstrip("./")


ICON_RANK = ["scalable", "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48"]


def deb_appinfo(deb, icon_dir):
    """(앱 이름, 아이콘 파일 경로 또는 아이콘 이름) — 꾸러미에 보이는 앱(.desktop)이 없으면 (None, None).
    큰 꾸러미도 두 번 훑을 뿐이다 (앱 목록 → 고른 아이콘 하나 꺼내기)."""
    entries = []
    try:
        for m, tf in _members(deb):
            path = _norm(m.name)
            if m.isfile() and re.match(r"^/usr/(local/)?share/applications/[^/]+\.desktop$", path) and m.size < 65536:
                cp = configparser.RawConfigParser(strict=False, interpolation=None)
                cp.optionxform = str
                try:
                    cp.read_string(tf.extractfile(m).read().decode("utf-8", "replace"))
                    e = dict(cp["Desktop Entry"])
                except (configparser.Error, KeyError, UnicodeError):
                    continue
                if e.get("NoDisplay", "").lower() == "true" or e.get("Type", "Application") != "Application":
                    continue
                entries.append(e)
    except (tarfile.TarError, OSError, EOFError):
        return None, None
    if not entries:
        return None, None
    e = entries[0]
    name = next((e[k] for k in _lang_keys() if e.get(k)), None)
    icon = e.get("Icon") or ""
    if not icon:
        return name, None
    # 아이콘 — 절대 경로면 그 파일, 이름이면 꾸러미 안의 hicolor·pixmaps 에서 가장 큰 것
    want, best = None, 99
    try:
        for m, tf in _members(deb):
            if not m.isfile() or m.size > 4 * 1024 * 1024:
                continue
            path = _norm(m.name)
            if os.path.isabs(icon):
                if path == icon:
                    want = (m.name, tf.extractfile(m).read())
                    break
                continue
            base = os.path.basename(path)
            stem, ext = os.path.splitext(base)
            if stem != icon or ext not in (".png", ".svg"):
                continue
            rank = 50
            mm = re.match(r"^/usr/share/icons/[^/]+/([^/]+)/apps/", path)
            if mm:
                rank = ICON_RANK.index(mm.group(1)) if mm.group(1) in ICON_RANK else 40
            elif path.startswith("/usr/share/pixmaps/"):
                rank = 45
            if rank < best:
                best = rank
                want = (m.name, tf.extractfile(m).read())
    except (tarfile.TarError, OSError, EOFError):
        pass
    if want is None:
        return name, (icon if not os.path.isabs(icon) else None)      # 설치된 테마에 같은 이름이 있으면 그걸로
    ext = os.path.splitext(want[0])[1] or ".png"
    os.makedirs(icon_dir, exist_ok=True)
    out = os.path.join(icon_dir, "icon" + ext)
    with open(out, "wb") as f:
        f.write(want[1])
    return name, out


# ── 설치된 앱 → 패키지 ──
def packages_of(paths):
    """{.desktop 경로: 패키지 이름} — 패키지가 아닌 것(사용자가 만든 것·flatpak 등)은 빠진다."""
    paths = [p for p in paths if p]
    if not paths:
        return {}
    res = subprocess.run(["dpkg-query", "-S"] + paths, capture_output=True, text=True,
                         env=dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8", LANGUAGE=""))
    found = {}
    for line in res.stdout.splitlines():
        if line.startswith(("diversion by", "local diversion")):
            continue
        pkgs, sep, path = line.partition(": ")
        if sep and path in paths:
            found[path] = pkgs.split(",")[0].strip().split(":")[0]
    return found
