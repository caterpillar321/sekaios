"""스토어 목록 — AppStream(데비안 DEP-11)에서 데스크톱 앱을 읽고, 설치 상태·검색·아이콘·스크린샷을 다룬다.

AppStream 목록은 apt update 때 함께 받아진다 (appstream 패키지의 apt 설정). 아이콘 묶음은 SekaiDE 가
/etc/apt/apt.conf.d/51sekai-store 로 켠다 (64·128px). 묶음에 없는 아이콘은 인터넷 주소(REMOTE)에서 받아
~/.cache/sekai/store 에 둔다.
"""
import hashlib
import html
import os
import re
import subprocess
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("AppStream", "1.0")
from gi.repository import AppStream as AS  # noqa: E402
from gi.repository import GLib  # noqa: E402

from sekaishell import search as S  # noqa: E402

CACHE = os.path.expanduser("~/.cache/sekai/store")
UA = "SekaiOS-Store/1.0"

# 분류 — freedesktop 주 분류를 윈도우 스토어식 이름으로 (게임은 따로 쪽이 있다)
CATEGORIES = [
    ("productivity", "생산성", ("Office",)),
    ("graphics", "그래픽·사진", ("Graphics", "Photography")),
    ("music", "음악·오디오", ("Audio", "Music")),
    ("video", "동영상", ("Video", "AudioVideo", "Player")),
    ("internet", "인터넷", ("Network", "WebBrowser", "Email", "Chat")),
    ("dev", "개발 도구", ("Development",)),
    ("education", "교육", ("Education",)),
    ("science", "과학", ("Science",)),
    ("utility", "유틸리티", ("Utility",)),
    ("system", "시스템", ("System", "Settings")),
]
CAT_NAMES = {k: n for k, n, _ in CATEGORIES}
GAME_CATEGORIES = [
    ("all", "전체", ("Game",)),
    ("action", "액션·아케이드", ("ActionGame", "ArcadeGame", "Shooter")),
    ("strategy", "전략", ("StrategyGame",)),
    ("puzzle", "퍼즐·두뇌", ("LogicGame", "BoardGame")),
    ("cards", "카드", ("CardGame",)),
    ("sim", "시뮬레이션", ("Simulation",)),
    ("sports", "레이싱·스포츠", ("SportsGame",)),
    ("kids", "어린이", ("KidsGame", "Kids")),
]

# 첫 화면 — 배너(돌아가며 크게)와 줄마다 고른 앱 (패키지 이름. 목록에 없으면 건너뛴다)
BANNER = [
    ("libreoffice-writer", "문서·표·발표 자료를 만드는 오피스", "MS 오피스 파일(.docx·.xlsx·.pptx)도 열고 저장합니다."),
    ("com.discordapp.Discord", "친구와 음성 채팅·화면 공유", "게임하면서 이야기하고, 서버에서 함께 어울립니다. (Flathub)"),
    ("gimp", "사진 편집과 그림", "포토샵처럼 레이어·필터·보정으로 사진을 다듬습니다."),
    ("obs-studio", "화면 녹화와 방송", "게임·강의를 녹화하거나 유튜브·치지직으로 바로 방송합니다."),
    ("supertuxkart", "친구와 함께 카트 레이싱", "온라인·화면 나누기로 같이 달리는 3D 레이싱 게임."),
]
SECTIONS = [
    ("인기 앱", "internet",
     ["com.discordapp.Discord", "com.visualstudio.code", "com.spotify.Client", "com.valvesoftware.Steam",
      "org.telegram.desktop", "md.obsidian.Obsidian"]),
    ("필수 앱", "productivity",
     ["firefox-esr", "thunderbird", "libreoffice-writer", "libreoffice-calc", "libreoffice-impress", "vlc",
      "transmission-gtk", "evince"]),
    ("만들기 · 꾸미기", "graphics",
     ["gimp", "inkscape", "krita", "blender", "kdenlive", "obs-studio", "audacity", "shotcut"]),
    ("개발 도구", "dev",
     ["geany", "kate", "meld", "git-cola", "gnome-builder", "virt-manager", "remmina", "filezilla"]),
    ("게임", "games",
     ["supertuxkart", "0ad", "luanti", "steam-installer", "frozen-bubble", "gnome-mines", "aisleriot", "openttd"]),
]
FEATURED = {p for _t, _c, ps in SECTIONS for p in ps} | {p for p, _a, _b in BANNER}

# 윈도우에서 쓰던 이름·흔한 부름말로도 찾게 (목록의 이름·키워드엔 없는 말)
ALIASES = {
    "libreoffice-writer": ("오피스", "워드", "문서 작성", "MS 오피스", "한글 문서"),
    "libreoffice-calc": ("오피스", "엑셀", "스프레드시트", "표 계산"),
    "libreoffice-impress": ("오피스", "파워포인트", "PPT", "발표 자료", "프레젠테이션"),
    "libreoffice-draw": ("오피스", "PDF 편집"),
    "gimp": ("포토샵", "사진 편집", "이미지 편집"),
    "krita": ("그림 그리기", "디지털 페인팅", "페인터", "클립 스튜디오"),
    "inkscape": ("일러스트레이터", "벡터", "로고 만들기"),
    "kdenlive": ("동영상 편집", "영상 편집", "프리미어", "베가스"),
    "shotcut": ("동영상 편집", "영상 편집", "프리미어"),
    "obs-studio": ("방송", "녹화", "화면 녹화", "스트리밍", "치지직", "유튜브"),
    "vlc": ("동영상 재생", "미디어 플레이어", "곰플레이어", "팟플레이어", "동영상 플레이어"),
    "mpv": ("동영상 재생", "미디어 플레이어", "팟플레이어"),
    "audacity": ("녹음", "오디오 편집", "음성 편집"),
    "thunderbird": ("메일", "이메일", "아웃룩", "outlook"),
    "firefox-esr": ("파이어폭스", "웹 브라우저", "인터넷"),
    "chromium": ("크롬", "웹 브라우저", "인터넷"),
    "transmission-gtk": ("토렌트", "torrent"),
    "qbittorrent": ("토렌트", "torrent"),
    "filezilla": ("FTP", "파일 전송", "SFTP"),
    "blender": ("3D", "3D 모델링", "애니메이션"),
    "steam-installer": ("스팀", "게임", "steam"),
    "gnome-mines": ("지뢰찾기", "minesweeper"),
    "aisleriot": ("카드놀이", "솔리테어", "카드 게임"),
    "remmina": ("원격 데스크톱", "원격 접속", "RDP", "VNC"),
    "virt-manager": ("가상 머신", "가상 컴퓨터", "VMware", "버추얼박스", "virtualbox"),
    "gnome-boxes": ("가상 머신", "가상 컴퓨터", "VMware", "버추얼박스"),
    "evince": ("PDF", "PDF 보기", "아크로뱃"),
    "okular": ("PDF", "PDF 보기"),
    "geany": ("코드 편집기", "메모장++", "notepad++", "IDE"),
    "kate": ("코드 편집기", "메모장++", "notepad++"),
    "meld": ("파일 비교", "diff"),
    "luanti": ("마인크래프트", "minecraft", "블록 게임"),
    "supertuxkart": ("카트라이더", "레이싱", "카트"),
    "0ad": ("에이지 오브 엠파이어", "전략 게임", "RTS"),
    "keepassxc": ("비밀번호 관리", "암호 관리"),
    "gparted": ("파티션", "디스크 관리"),
    # Flathub 앱 (앱 id)
    "com.discordapp.Discord": ("디스코드", "음성 채팅", "메신저"),
    "com.visualstudio.code": ("비주얼 스튜디오 코드", "VS Code", "vscode", "코드 편집기"),
    "com.spotify.Client": ("스포티파이", "음악 스트리밍", "멜론"),
    "com.valvesoftware.Steam": ("스팀", "게임"),
    "org.telegram.desktop": ("텔레그램", "메신저"),
    "md.obsidian.Obsidian": ("옵시디언", "노트", "메모"),
    "us.zoom.Zoom": ("줌", "화상 회의"),
    "com.slack.Slack": ("슬랙", "메신저", "업무 채팅"),
    "org.mozilla.firefox": ("파이어폭스", "웹 브라우저", "인터넷"),
    "com.google.Chrome": ("크롬", "구글 크롬", "웹 브라우저"),
}

# 데비안·Flathub 양쪽에 있는 앱은 하나로 보인다 (기본은 데비안 — 시스템 업데이트로 같이 올라가고 런타임이 필요 없다).
#   여기 있는 앱만 Flathub 쪽을 앞에 (데비안 steam-installer 는 32비트 라이브러리·contrib 가 필요해 자주 깨진다)
PREFER_FLATHUB = {"com.valvesoftware.Steam"}


class App:
    """스토어의 앱 하나. pkg 가 열쇠 — 데비안은 패키지 이름, Flathub 는 앱 id (com.discordapp.Discord).
    source: debian · flathub, ref: flatpak ref (app/<id>/<arch>/<branch>), alt: 다른 출처의 같은 앱"""
    __slots__ = ("cid", "pkg", "name", "summary", "cats", "keywords", "comp", "rank", "source", "ref", "alt", "_f")

    def __init__(self, comp, source="debian"):
        self.comp = comp
        self.cid = comp.get_id()
        self.source, self.ref, self.alt = source, None, None
        if source == "flathub":
            self.ref = comp.get_bundle(AS.BundleKind.FLATPAK).get_id()
            self.pkg = self.ref.split("/")[1]
        else:
            self.pkg = comp.get_pkgname()
        self.name = comp.get_name() or self.pkg
        self.summary = comp.get_summary() or ""
        self.cats = list(comp.get_categories() or [])
        self.keywords = list(comp.get_keywords() or [])
        has_shot = bool(comp.get_screenshots_all())
        has_icon = any(i.get_kind() in (AS.IconKind.CACHED, AS.IconKind.REMOTE) for i in comp.get_icons())
        # 늘어놓는 순서 — 고른 앱, 스크린샷·아이콘이 있는 앱이 앞으로 (데비안 목록엔 인기 순위가 없다)
        self.rank = (0 if self.pkg in FEATURED else 1, 0 if has_shot and has_icon else 1 if has_icon else 2,
                     self.name.lower())

    @property
    def is_flatpak(self):
        return self.source == "flathub"

    def variants(self):
        return [self] + ([self.alt] if self.alt is not None else [])

    @property
    def is_game(self):
        return "Game" in self.cats

    def in_cats(self, names):
        return any(c in self.cats for c in names)

    def source_name(self):
        return "Flathub" if self.is_flatpak else "데비안 저장소"

    def category_name(self):
        if self.is_game:
            return "게임"
        for k, n, cs in CATEGORIES:
            if self.in_cats(cs):
                return n
        return "앱"

    # ── 자세히 쪽에서 쓰는 것 (필요할 때 읽는다) ──
    def developer(self):
        try:
            d = self.comp.get_developer()
            n = d.get_name() if d is not None else None
        except (AttributeError, TypeError):
            n = None
        return n or self.comp.get_project_group() or None

    def license(self):
        return self.comp.get_project_license() or None

    def version(self):
        """목록에 적힌 최신 판 (Flathub 는 여기서, 데비안은 apt 가 알려 준다)"""
        try:
            rel = self.comp.get_releases_plain()
            arr = rel.get_entries() if rel is not None else []
            return arr[0].get_version() if arr else None
        except (AttributeError, TypeError):
            return None

    def homepage(self):
        return self.comp.get_url(AS.UrlKind.HOMEPAGE) or None

    def description_markup(self):
        return to_pango(self.comp.get_description() or "")

    def desktop_ids(self):
        lc = self.comp.get_launchable(AS.LaunchableKind.DESKTOP_ID)
        ids = list(lc.get_entries()) if lc is not None else []
        if not ids and self.cid.endswith(".desktop"):
            ids = [self.cid]
        return ids

    def screenshots(self, want=752):
        """[(썸네일 주소, 큰 그림 주소)] — 기본 스크린샷이 먼저"""
        out = []
        for s in self.comp.get_screenshots_all() or []:
            imgs = [i for i in s.get_images() if i.get_url()]
            if not imgs:
                continue
            thumbs = [i for i in imgs if i.get_kind() == AS.ImageKind.THUMBNAIL] or imgs
            small = min(thumbs, key=lambda i: abs((i.get_width() or want) - want))
            big = max(imgs, key=lambda i: i.get_width() or 0)
            out.append((small.get_url(), big.get_url()))
        return out

    def icon(self, size=64):
        """('file', 경로) · ('theme', 이름) · ('url', 주소) · None"""
        files, stock, remote = [], None, []
        for i in self.comp.get_icons():
            k = i.get_kind()
            if k in (AS.IconKind.CACHED, AS.IconKind.LOCAL):
                fn = i.get_filename()
                if fn and os.path.isfile(fn):
                    files.append((i.get_width() or 0, fn))
            elif k == AS.IconKind.STOCK and not stock:
                stock = i.get_name()
            elif k == AS.IconKind.REMOTE and i.get_url():
                remote.append((i.get_width() or 0, i.get_url()))
        if files:
            big = [f for f in files if f[0] >= size]
            return ("file", (min(big) if big else max(files))[1])
        if stock:
            from gi.repository import Gtk
            if Gtk.IconTheme.get_default().has_icon(stock):
                return ("theme", stock)
        if remote:
            big = [r for r in remote if r[0] >= size]
            return ("url", (min(big) if big else max(remote))[1])
        return ("theme", stock) if stock else None


SEKAI_ROOTS = ("sekai-desktop", "sekai-de", "sekai-shell")


def system_packages():
    """SekaiOS 의 구성 요소가 (Depends 로) 꼭 필요로 하는 설치된 패키지 전부 — Recommends(크로뮴 등)는 빼서
    사용자가 지울 수 있게 둔다. 지우는 길은 sekai-apps 가 어차피 막는다 — 이건 단추를 미리 끄는 용도."""
    res = subprocess.run(["apt-cache", "depends", "--recurse", "--no-recommends", "--no-suggests", "--no-conflicts",
                          "--no-breaks", "--no-replaces", "--no-enhances", "--installed", *SEKAI_ROOTS],
                         capture_output=True, text=True)
    return {l.strip() for l in res.stdout.splitlines() if l and not l.startswith(" ")}


def to_pango(desc):
    """AppStream 설명(<p>·<ul>·<ol>·<li>·<em>·<code>) → Pango 마크업"""
    if not desc:
        return ""
    out, n = [], 0
    for m in re.finditer(r"<(p|li)>(.*?)</\1>|<(ol|ul)>|</(ol|ul)>", desc, re.S):
        if m.group(3):
            n = 1 if m.group(3) == "ol" else 0
            continue
        if m.group(4):
            n = 0
            continue
        body = " ".join(m.group(2).split())
        body = html.unescape(re.sub(r"<[^>]+>", "", body))
        body = GLib.markup_escape_text(body)
        if m.group(1) == "li":
            out.append(f"  {n}. {body}" if n else f"  • {body}")
            if n:
                n += 1
        else:
            out.append(("\n" if out else "") + body)
    return "\n".join(out).strip() or GLib.markup_escape_text(re.sub(r"<[^>]+>", "", desc).strip())


class Catalog:
    def __init__(self):
        self.apps = []
        self.by_pkg = {}
        self.installed = set()
        self.fp_installed = {}          # Flathub(flatpak) 로 설치된 앱 id → 브랜치
        self.flatpak = False            # flatpak 을 쓸 수 있나 (없으면 Flathub 앱을 보이지 않는다)
        self.system = set()             # SekaiOS 가 꼭 필요로 하는 패키지 (지우면 데스크톱이 같이 지워진다)
        self.error = None

    def load(self):
        """무거운 일 (1~2초) — 작업 스레드에서 부른다"""
        try:
            pool = AS.Pool()
            pool.load(None)
            comps = pool.get_components().as_array()
        except GLib.Error as e:
            self.error = e.message
            comps = []
        from sekaishell import appmgr
        self.flatpak = appmgr.flatpak_available()
        apps, fps, seen = [], [], set()
        for c in comps:
            if c.get_kind() != AS.ComponentKind.DESKTOP_APP:
                continue
            b = c.get_bundle(AS.BundleKind.FLATPAK)
            if b is not None and b.get_id():
                if not self.flatpak or not b.get_id().startswith("app/") or b.get_id().count("/") != 3:
                    continue
                a = App(c, "flathub")
            else:
                if not c.get_pkgname():
                    continue
                a = App(c)
            if a.pkg in seen:
                continue
            seen.add(a.pkg)
            (fps if a.is_flatpak else apps).append(a)
        # 양쪽에 있는 앱은 하나로 — 목록엔 앞의 것만, 자세히 쪽에서 출처를 고른다
        key = lambda a: a.cid.lower().removesuffix(".desktop")      # noqa: E731
        debs = {key(a): a for a in apps}
        listed = list(apps)
        for f in fps:
            d = debs.get(key(f))
            if d is None:
                listed.append(f)
                continue
            d.alt, f.alt = f, d
            if f.pkg in PREFER_FLATHUB:
                listed[listed.index(d)] = f
        for a in listed:                # 고른 앱 표시를 다시 (Flathub 앱 id 로 고른 것도)
            a.rank = (0 if a.pkg in FEATURED or (a.alt and a.alt.pkg in FEATURED) else 1,) + a.rank[1:]
        listed.sort(key=lambda a: a.rank)
        self.apps = listed
        self.by_pkg = {a.pkg: a for a in apps + fps}
        self.refresh_installed()
        self.system = system_packages()

    def refresh_installed(self):
        res = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Package}\n"],
                             capture_output=True, text=True)
        self.installed = {l.split()[1] for l in res.stdout.splitlines()
                          if l.startswith("ii") and len(l.split()) > 1}
        if self.flatpak:
            from sekaishell import appmgr
            self.fp_installed = appmgr.flatpak_installed_apps()

    def is_installed(self, app):
        return app.pkg in (self.fp_installed if app.is_flatpak else self.installed)

    def installed_variant(self, app):
        """양쪽에 있는 앱 — 설치된 쪽 (없으면 None)"""
        return next((v for v in app.variants() if self.is_installed(v)), None)

    def is_system(self, app):
        return not app.is_flatpak and app.pkg in self.system

    def pick(self, pkgs):
        """고른 이름들 → 목록에 보이는 앱 (Flathub id 로 골랐는데 데비안 쪽이 앞이어도 그 앱)"""
        out = []
        for p in pkgs:
            a = self.by_pkg.get(p)
            if a is None:
                continue
            if a not in self.apps and a.alt is not None:
                a = a.alt
            if a not in out:
                out.append(a)
        return out

    def in_category(self, names):
        return [a for a in self.apps if a.in_cats(names)]

    def apps_only(self):
        return [a for a in self.apps if not a.is_game]

    def games(self):
        return [a for a in self.apps if a.is_game]

    def search(self, text):
        q = S.Query(text)
        if not q.ns:
            return []
        hits = []
        for i, a in enumerate(self.apps):
            s = S.score_fields(q, (a.name, a.pkg), a.keywords + list(ALIASES.get(a.pkg, ())), desc=(a.summary,))
            if s:
                hits.append((-s, i, a))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [h[2] for h in hits]


# ── 그림 받기 (아이콘·스크린샷) ──
def _get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(8 * 1024 * 1024)
    except Exception:
        return None


_MEDIA = re.compile(r"^(https://appstream\.debian\.org/media/[^/]+/.+?/)([0-9a-f]{32})/(.+)$")
_moved_dirs = {}


def _moved(url):
    """데비안 안정판의 앱 목록은 출시 때 만든 것인데 그림 서버(appstream.debian.org/media)는 다시 만들어지며
    앱마다 해시 폴더 이름이 바뀐다 (파일 이름은 그대로) → 404. 부모 폴더 목록에서 지금 폴더를 찾아 같은 파일로."""
    m = _MEDIA.match(url)
    if not m:
        return None
    base, old, rest = m.groups()
    if base not in _moved_dirs:
        page = _get(base)
        dirs = re.findall(rb'href="([0-9a-f]{32})/"', page or b"")
        _moved_dirs[base] = [d.decode() for d in dirs if d.decode() != old]
    for d in _moved_dirs[base]:
        # 같은 파일 이름이 없을 수도 있다 (스크린샷을 다시 만들며 크기가 바뀜) → 같은 번호의 가장 가까운 너비
        mm = re.match(r"^(.*/)?(image-\d+)_(\d+)x\d+@\d+\.png$", rest)
        if not mm:
            return f"{base}{d}/{rest}"
        folder, stem, width = mm.group(1) or "", mm.group(2), int(mm.group(3))
        key = f"{base}{d}/{folder}"
        if key not in _moved_dirs:
            page = _get(key) or b""
            _moved_dirs[key] = [x.decode() for x in re.findall(rb'href="(image-\d+_[^"]+\.png)"', page)]
        names = [n for n in _moved_dirs[key] if n.startswith(stem + "_")]
        sized = []
        for n in names:
            sm = re.match(r"^image-\d+_(\d+)x\d+@\d+\.png$", n)
            if sm:
                sized.append((abs(int(sm.group(1)) - width), n))
        pick = min(sized)[1] if sized else (f"{stem}_orig.png" if f"{stem}_orig.png" in names else None)
        if pick:
            return f"{key}{pick}"
    return None


_pool = ThreadPoolExecutor(max_workers=4)
_lock = threading.Lock()
_waiting = {}


def fetch(url, done):
    """주소의 그림을 캐시로 받고 done(경로 또는 None) 을 GTK 스레드에서 부른다."""
    if not url:
        GLib.idle_add(done, None)
        return
    ext = os.path.splitext(url.split("?")[0])[1][:5] or ".png"
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ext)
    if os.path.exists(path):
        GLib.idle_add(done, path)
        return
    with _lock:
        if url in _waiting:
            _waiting[url].append(done)
            return
        _waiting[url] = [done]

    def work():
        ok = None
        try:
            os.makedirs(CACHE, exist_ok=True)
            data = _get(url)
            if data is None:
                alt = _moved(url)                # 데비안 그림 서버가 폴더를 새로 만들었다 (아래)
                data = _get(alt) if alt else None
            if data is None:
                raise OSError("받지 못함")
            tmp = f"{path}.{os.getpid()}.part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            ok = path
        except Exception:
            ok = None
        with _lock:
            cbs = _waiting.pop(url, [])
        for cb in cbs:
            GLib.idle_add(cb, ok)
    _pool.submit(work)
