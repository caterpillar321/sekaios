"""Windows 앱 (Wine) — 엔진 · 앱 환경 · 실행 · 바로 가기. GTK 를 쓰지 않는다 (창은 sekaiwine.app, 명령은 sekai-wine).

층을 나눈다 (Bottles·Lutris 처럼):
  엔진      /usr/lib/sekai/wine/wine-<판>          SekaiOS 가 빌드한 Wine (sekai-wine-<판> 패키지, WoW64)
            ~/.local/share/sekai/wine/engines/…     내려받은 Proton (게임 — umu 가 Steam 런타임 컨테이너로)
  앱 환경   ~/.local/share/sekai/wine/envs/<id>/   앱마다 하나 (WINEPREFIX = <id>/prefix, 설정 = <id>/env.json).
            btrfs 면 서브볼륨이라 설치 전에 스냅샷을 떠 두고 되돌릴 수 있다 (snapshots/<id>@<시각>)
  바로 가기 앱이 만든 시작 메뉴 바로 가기(.lnk)를 Wine 의 winemenubuilder 가 .desktop·아이콘으로 바꾸게 두되
            (XDG_DATA_HOME 을 환경 안으로 돌려 둔다) 끝나면 우리가 sekai-wine run 으로 고쳐 ~/.local/share 로 옮긴다

정돈: 새 환경의 Z: 는 / 전체가 아니라 홈 폴더 — 설치 파일(다운로드·바탕 화면…)과 그 옆 파일(data.cab 등)이
제대로 된 드라이브 경로로 보이고, 시스템 폴더는 드라이브로 드러나지 않는다. (Z: 를 아예 떼면 홈의 설치 파일이
\\\\?\\unix\\ 경로가 돼 옆 파일을 못 찾는 설치 프로그램이 많다.) USB 는 Wine 이 꽂힐 때 드라이브를 따로 준다.
Wine 은 샌드박스가 아니라서 보안 경계는 아니다 — 실수로 시스템 파일을 건드리지 않게 하는 정도.
"""
import configparser
import datetime
import glob
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess

DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))
ROOT = os.path.join(DATA, "sekai", "wine")
ENVS = os.path.join(ROOT, "envs")
SNAPS = os.path.join(ROOT, "snapshots")
USER_ENGINES = os.path.join(ROOT, "engines")
SYS_ENGINES = "/usr/lib/sekai/wine"
APPS_DIR = os.path.join(DATA, "applications")
ICONS_DIR = os.path.join(DATA, "icons", "hicolor")
QUICK = "quick"                     # "바로 실행" 이 쓰는 공용 환경 (설치 없이 한 번 돌리는 exe)
DESKTOP_PREFIX = "sekaiwine-"

# 한글 — 윈도우 글꼴 이름을 SekaiOS 에 든 글꼴로 (fonts-pretendard · fonts-nanum)
FONT_SUBST = {
    "Malgun Gothic": "Pretendard", "맑은 고딕": "Pretendard",
    "Gulim": "NanumGothic", "굴림": "NanumGothic", "GulimChe": "NanumGothicCoding", "굴림체": "NanumGothicCoding",
    "Dotum": "NanumGothic", "돋움": "NanumGothic", "DotumChe": "NanumGothicCoding", "돋움체": "NanumGothicCoding",
    "Batang": "NanumMyeongjo", "바탕": "NanumMyeongjo", "BatangChe": "NanumMyeongjo", "바탕체": "NanumMyeongjo",
    "Gungsuh": "NanumMyeongjo", "궁서": "NanumMyeongjo",
}
# 한글이 없는 영문 UI 글꼴에 한글을 이어 붙인다 (FontLink) — 안 하면 대화상자의 한글이 네모로
LINK_TO = "NanumGothic.ttf,NanumGothic"
LINKED = ("Tahoma", "MS Shell Dlg", "MS Shell Dlg 2", "Segoe UI", "Microsoft Sans Serif", "Arial", "Verdana",
          "Lucida Sans Unicode", "MS Sans Serif")
UNINSTALL_RE = re.compile(r"(?i)\b(uninstall|uninst|remove)\b|제거|언인스톨")


def dbg(*a):
    if os.environ.get("SEKAI_DEBUG"):
        print("[sekaiwine]", *a, flush=True)


# ───────────────────────────────────────────────────────────────
# 엔진
# ───────────────────────────────────────────────────────────────
def _vkey(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-]", v)]


def engines():
    """[{id, kind, version, path, label}] — 새 판이 앞"""
    out = []
    for d in glob.glob(os.path.join(SYS_ENGINES, "wine-*")):
        if os.access(os.path.join(d, "bin", "wine"), os.X_OK):
            v = os.path.basename(d)[5:]
            out.append({"id": os.path.basename(d), "kind": "wine", "version": v, "path": d, "label": f"Wine {v}"})
    for d in glob.glob(os.path.join(USER_ENGINES, "*")):
        if os.path.exists(os.path.join(d, "proton")):
            n = os.path.basename(d)
            out.append({"id": n, "kind": "proton", "version": n, "path": d, "label": n})
    out.sort(key=lambda e: (e["kind"], _vkey(e["version"])), reverse=True)
    return out


def engine(eid):
    return next((e for e in engines() if e["id"] == eid), None)


def default_engine(kind="app"):
    want = "proton" if kind == "game" else "wine"
    return next((e for e in engines() if e["kind"] == want), None)


# ───────────────────────────────────────────────────────────────
# 앱 환경
# ───────────────────────────────────────────────────────────────
def _fs_type(path):
    try:
        return subprocess.run(["stat", "-f", "-c", "%T", path], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def btrfs():
    os.makedirs(ROOT, exist_ok=True)
    return _fs_type(ROOT) == "btrfs" and bool(shutil.which("btrfs"))


def slug(name):
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-").lower()
    return (s or "app")[:32]


def env_dir(eid):
    return os.path.join(ENVS, eid)


def prefix(env):
    return os.path.join(env_dir(env["id"]), "prefix")


def load(eid):
    try:
        with open(os.path.join(env_dir(eid), "env.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(env):
    p = os.path.join(env_dir(env["id"]), "env.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(env, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def envs():
    out = []
    for d in sorted(glob.glob(os.path.join(ENVS, "*"))):
        e = load(os.path.basename(d))
        if e:
            out.append(e)
    return out


def create(name, kind="app", engine_id=None, eid=None):
    """새 환경의 폴더·설정만 만든다 (Wine 초기화는 init — 오래 걸려 따로)"""
    eng = engine(engine_id) if engine_id else default_engine(kind)
    if not eng:
        raise RuntimeError("Wine 엔진이 없습니다 — sekai-wine-11.0 패키지를 설치하세요")
    eid = eid or f"{slug(name)}-{secrets.token_hex(2)}"
    d = env_dir(eid)
    os.makedirs(ENVS, exist_ok=True)
    if btrfs():
        subprocess.run(["btrfs", "-q", "subvolume", "create", d], check=True)
    else:
        os.makedirs(d)
    env = {"id": eid, "name": name, "kind": kind, "engine": eng["id"], "apps": [],
           "created": datetime.datetime.now().isoformat(timespec="seconds")}
    save(env)
    return env


def environ(env, extra=None):
    """그 환경으로 Wine 을 돌릴 때의 환경 변수"""
    e = dict(os.environ)
    e.update({
        "WINEPREFIX": prefix(env),
        "WINEDEBUG": os.environ.get("WINEDEBUG", "-all"),
        "WINEARCH": "win64",
        # 처음 만들 때 Mono·Gecko 를 받으라는 창을 띄우지 않는다 (구성 요소로 따로 넣는다)
        "WINEDLLOVERRIDES": "mscoree,mshtml=" if not env.get("mono") else "",
    })
    eng = engine(env["engine"])
    if eng and eng["kind"] == "wine":
        e["PATH"] = os.path.join(eng["path"], "bin") + os.pathsep + e.get("PATH", "")
    if extra:
        e.update(extra)
    return e


def wine_bin(env):
    eng = engine(env["engine"])
    if not eng:
        raise RuntimeError(f"엔진 {env['engine']} 이 없습니다")
    if eng["kind"] != "wine":
        raise RuntimeError("Proton 환경은 아직 지원하지 않습니다")
    return os.path.join(eng["path"], "bin", "wine")


def _reg_text():
    lines = ["Windows Registry Editor Version 5.00", "",
             r"[HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\FontSubstitutes]"]
    lines += [f'"{k}"="{v}"' for k, v in FONT_SUBST.items()]
    lines += ["", r"[HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\FontLink\SystemLink]"]
    lines += [f'"{k}"=hex(7):' + ",".join(f"{b:02x},00" for b in (LINK_TO + "\0\0").encode("ascii")) for k in LINKED]
    # 안티에일리어싱 (ClearType 처럼)
    lines += ["", r"[HKEY_CURRENT_USER\Control Panel\Desktop]", '"FontSmoothing"="2"',
              '"FontSmoothingType"=dword:00000002', '"FontSmoothingGamma"=dword:00000578',
              '"FontSmoothingOrientation"=dword:00000001', ""]
    return "\r\n".join(lines)


def init(env, log=None):
    """wineboot 로 C: 드라이브를 만들고 한글 글꼴·드라이브를 맞춘다 (처음 30초~1분). 실패하면 RuntimeError"""
    pfx = prefix(env)
    os.makedirs(pfx, exist_ok=True)
    wine = wine_bin(env)
    # 초기화 중 winemenubuilder 를 끈다 — 켜 두면 Wine 메모장 등의 파일 연결(.txt …)을 사용자의 진짜
    #   ~/.local/share/applications 에 써서, SekaiOS 에서 .txt 가 Wine 메모장으로 열린다
    quiet = {"WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d"}
    r = subprocess.run([wine, "wineboot", "--init"], env=environ(env, quiet), stdout=log or subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, timeout=600)
    _wait_server(env)
    if r.returncode != 0 or not os.path.isdir(os.path.join(pfx, "drive_c", "windows")):
        raise RuntimeError(f"Wine 초기화 실패 (종료 코드 {r.returncode})")
    reg = os.path.join(pfx, "sekai-init.reg")
    with open(reg, "w", encoding="utf-16") as f:
        f.write(_reg_text())
    subprocess.run([wine, "regedit", "/S", reg], env=environ(env, quiet), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120)
    _wait_server(env)
    os.remove(reg)
    z = os.path.join(pfx, "dosdevices", "z:")
    if os.path.islink(z):
        os.remove(z)
    os.symlink(os.path.expanduser("~"), z)   # Z: = 홈 (/ 전체가 아니라)
    env["initialized"] = True
    save(env)


def _wait_server(env, timeout=120):
    eng = engine(env["engine"])
    ws = os.path.join(eng["path"], "bin", "wineserver") if eng else "wineserver"
    try:
        subprocess.run([ws, "-w"], env=environ(env), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass


def kill(env):
    eng = engine(env["engine"])
    if eng:
        subprocess.run([os.path.join(eng["path"], "bin", "wineserver"), "-k"], env=environ(env),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def staging(env):
    """설치하는 동안 winemenubuilder 가 바로 가기를 쓰는 곳 — 끝나면 collect 가 거둔다"""
    s = os.path.join(env_dir(env["id"]), "menu")
    os.makedirs(os.path.join(s, "config"), exist_ok=True)
    os.makedirs(os.path.join(s, "desktop"), exist_ok=True)
    with open(os.path.join(s, "config", "user-dirs.dirs"), "w") as f:
        f.write(f'XDG_DESKTOP_DIR="{os.path.join(s, "desktop")}"\n')
    return {"XDG_DATA_HOME": os.path.join(s, "data"), "XDG_CONFIG_HOME": os.path.join(s, "config")}


def command(env, target, args=()):
    """target: 리눅스 경로(.exe · .msi · .lnk) 또는 윈도우 경로(C:\\…) — wine 이 받는 명령"""
    wine = wine_bin(env)
    low = target.lower()
    if low.endswith(".msi"):
        return [wine, "msiexec", "/i", target, *args]
    if low.endswith(".lnk"):
        return [wine, "start", "/wait", *(["/unix"] if target.startswith("/") else []), target, *args]
    return [wine, target, *args]


def spawn(env, target, args=(), install=False, cwd=None):
    """띄우고 Popen 을 돌려준다. install=True 면 바로 가기를 환경 안에 모은다"""
    extra = staging(env) if install else {}
    if not install:                      # 설치가 아닐 때 만든 바로 가기는 시작 메뉴로 새지 않게 (winemenubuilder 끔)
        extra["WINEDLLOVERRIDES"] = ";".join(x for x in (environ(env)["WINEDLLOVERRIDES"], "winemenubuilder.exe=d") if x)
    cmd = command(env, target, args)
    if cwd is None and target.startswith("/"):
        cwd = os.path.dirname(target)
    dbg("spawn", cmd)
    return subprocess.Popen(cmd, env=environ(env, extra), cwd=cwd or None,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_all(env, timeout=None):
    """그 환경의 Wine 프로그램이 모두 끝날 때까지 (설치 프로그램은 자식을 띄우고 먼저 끝나기도 한다)"""
    _wait_server(env, timeout=timeout or 24 * 3600)


# ───────────────────────────────────────────────────────────────
# 바로 가기 — winemenubuilder 가 환경 안(menu/data)에 쓴 것을 SekaiOS 시작 메뉴로
# ───────────────────────────────────────────────────────────────
def _desktop_id(env, name):
    return f"{DESKTOP_PREFIX}{env['id']}-{slug(name)}"


def collect(env):
    """새로 생긴 바로 가기를 시작 메뉴에 올린다 → 이번에 더한 앱 목록 [{name, lnk, desktop, icon}]"""
    data = os.path.join(env_dir(env["id"]), "menu", "data")
    found = []
    for path in sorted(glob.glob(os.path.join(data, "applications", "wine", "**", "*.desktop"), recursive=True)):
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
            de = cp["Desktop Entry"]
        except (configparser.Error, KeyError, UnicodeDecodeError):
            continue
        name = de.get("Name", "").strip()
        if not name or UNINSTALL_RE.search(name):
            continue                          # 제거 바로 가기는 빼고 — 지우기는 설정 › Windows 앱 에서
        lnk = _lnk_from_exec(de.get("Exec", ""))
        if not lnk:
            continue
        found.append({"name": name, "lnk": lnk, "icon_src": de.get("Icon", ""),
                      "wmclass": de.get("StartupWMClass", ""), "comment": de.get("Comment", "")})
    added = []
    have = {a["lnk"].lower() for a in env.get("apps", [])}
    for f in found:
        if f["lnk"].lower() in have:
            continue
        did = _desktop_id(env, f["name"])
        icon = _copy_icon(env, data, f["icon_src"], did)
        app = {"name": f["name"], "lnk": f["lnk"], "desktop": did, "icon": icon, "wmclass": f["wmclass"]}
        write_desktop(env, app, f["comment"])
        env.setdefault("apps", []).append(app)
        added.append(app)
    save(env)
    _refresh_menu()
    return added


def _lnk_from_exec(ex):
    """winemenubuilder 의 Exec= 에서 .lnk 의 윈도우 경로를 꺼낸다
    예: env WINEPREFIX="/…/prefix" wine C:\\\\ProgramData\\\\Microsoft\\\\…\\\\7-Zip File Manager.lnk"""
    try:
        parts = shlex.split(ex)
    except ValueError:
        return None
    for i, p in enumerate(parts):
        if os.path.basename(p) in ("wine", "wine64") and i + 1 < len(parts):
            rest = parts[i + 1:]
            if rest and rest[0].lower() == "start":
                rest = [r for r in rest[1:] if not r.startswith("/")]
            lnk = " ".join(rest)
            return lnk.replace("\\\\", "\\") if lnk.lower().endswith(".lnk") else None
    return None


def _copy_icon(env, data, icon, did):
    if not icon:
        return "application-x-ms-dos-executable"
    best = None
    for size in ("256x256", "128x128", "64x64", "48x48", "32x32"):
        cand = os.path.join(data, "icons", "hicolor", size, "apps", icon + ".png")
        if os.path.exists(cand):
            best = (size, cand)
            break
    if not best:
        return "application-x-ms-dos-executable"
    dst = os.path.join(ICONS_DIR, best[0], "apps", did + ".png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(best[1], dst)
    # 절대 경로로 — 이름으로 두면 이미 떠 있는 작업 표시줄·시작 메뉴가 아이콘 테마 목록을 다시 읽기 전까지 못 찾는다
    return dst


def write_desktop(env, app, comment=""):
    os.makedirs(APPS_DIR, exist_ok=True)
    lines = ["[Desktop Entry]", "Type=Application", f"Name={app['name']}",
             f"Exec=sekai-wine run {env['id']} --lnk {_desk_quote(app['lnk'])}",
             f"Icon={app['icon']}", "Categories=Wine;", f"X-Sekai-Wine-Env={env['id']}",
             f"Comment={comment or 'Windows 앱'}"]
    if app.get("wmclass"):
        lines.append(f"StartupWMClass={app['wmclass']}")
    path = os.path.join(APPS_DIR, app["desktop"] + ".desktop")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _desk_quote(s):
    # .desktop 의 Exec 인자 — 따옴표 안에서는 " ` $ \ 를 \ 로 (그리고 파일 자체의 \ 이스케이프로 한 번 더)
    q = '"' + re.sub(r'(["`$\\])', r"\\\1", s) + '"'
    return q.replace("\\", "\\\\")


def _refresh_menu():
    try:
        subprocess.run(["update-desktop-database", "-q", APPS_DIR], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ───────────────────────────────────────────────────────────────
# 스냅샷 · 지우기
# ───────────────────────────────────────────────────────────────
def snapshot(env, label=""):
    """btrfs 일 때만 — 읽기 전용 스냅샷 이름을 돌려준다 (아니면 None)"""
    if not btrfs():
        return None
    os.makedirs(SNAPS, exist_ok=True)
    name = f"{env['id']}@{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    subprocess.run(["btrfs", "-q", "subvolume", "snapshot", "-r", env_dir(env["id"]), os.path.join(SNAPS, name)],
                   check=True)
    with open(os.path.join(SNAPS, name + ".label"), "w", encoding="utf-8") as f:
        f.write(label)
    return name


def snapshots(env):
    out = []
    for p in sorted(glob.glob(os.path.join(SNAPS, env["id"] + "@*")), reverse=True):
        if p.endswith(".label"):
            continue
        try:
            with open(p + ".label", encoding="utf-8") as f:
                label = f.read()
        except OSError:
            label = ""
        out.append({"name": os.path.basename(p), "label": label})
    return out


def _rm_subvol(path):
    """사용자 권한으로 서브볼륨 지우기 — 안을 비우면 소유자는 rmdir 로 지울 수 있다 (커널 4.18+).
    읽기 전용 스냅샷은 먼저 쓰기 가능으로"""
    if not os.path.lexists(path):
        return
    if btrfs():
        subprocess.run(["btrfs", "-q", "property", "set", "-ts", path, "ro", "false"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.isdir(path):
        try:
            os.rmdir(path)
        except OSError:
            subprocess.run(["btrfs", "-q", "subvolume", "delete", path], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)


def restore(env, snap):
    """스냅샷으로 되돌린다 — 그 뒤에 생긴 바로 가기는 지우고, 스냅샷의 앱 목록으로 다시 쓴다"""
    kill(env)
    d = env_dir(env["id"])
    old = d + ".old"
    os.rename(d, old)
    subprocess.run(["btrfs", "-q", "subvolume", "snapshot", os.path.join(SNAPS, snap), d], check=True)
    _rm_subvol(old)
    new = load(env["id"])
    keep = {a["desktop"] for a in new.get("apps", [])}
    for a in env.get("apps", []):
        if a["desktop"] not in keep:
            _remove_desktop(a)
    for a in new.get("apps", []):
        write_desktop(new, a)
    _refresh_menu()
    return new


def _remove_desktop(app):
    for p in [os.path.join(APPS_DIR, app["desktop"] + ".desktop")] + \
             glob.glob(os.path.join(ICONS_DIR, "*", "apps", app["desktop"] + ".png")):
        try:
            os.remove(p)
        except OSError:
            pass


def remove(env):
    """환경을 통째로 — 그 안에 깐 앱·설정·바로 가기·스냅샷까지"""
    kill(env)
    for a in env.get("apps", []):
        _remove_desktop(a)
    for s in snapshots(env):
        _rm_subvol(os.path.join(SNAPS, s["name"]))
        try:
            os.remove(os.path.join(SNAPS, s["name"] + ".label"))
        except OSError:
            pass
    _rm_subvol(env_dir(env["id"]))
    _refresh_menu()


def size(env):
    try:
        out = subprocess.run(["du", "-sb", env_dir(env["id"])], capture_output=True, text=True, timeout=30).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0


def guess_name(path):
    """설치 파일 이름에서 앱 이름을 — "7z2408-x64.exe" → "7z2408", "SetupFoo_1.2.exe" → "Foo" """
    n = os.path.splitext(os.path.basename(path))[0]
    n = re.sub(r"(?i)[-_ .]*(setup|install(er)?|x64|x86|win(32|64)?|amd64|portable)[-_ .]*", " ", n)
    n = re.sub(r"[-_ .]*v?\d+(\.\d+)+[-_ .]*", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" -_.")
    return n or os.path.splitext(os.path.basename(path))[0]


_INSTALLER_MARKS = (b"Nullsoft", b"Inno Setup", b"InstallShield", b"WixBundle", b"Squirrel", b"Setup Factory",
                    b"requireAdministrator", b"InstallAware", b"Advanced Installer")
_MANIFEST_RE = re.compile(rb"<(?:description|assemblyIdentity)[^>]*?>?[^<]{0,200}?(?:[Ii]nstall|[Ss]etup)")


def looks_like_installer(path):
    """설치 프로그램인가 — 윈도우의 설치 프로그램 감지처럼: 이름(setup·install), 매니페스트(관리자 권한 요구 ·
    설명의 Installer/Setup), 설치 도구가 남기는 표시(NSIS·Inno·InstallShield·WiX·Squirrel …)"""
    n = os.path.basename(path).lower()
    if n.endswith(".msi") or re.search(r"setup|install|inst\b", n):
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(8 << 20)              # 앞 8MB — 매니페스트·설치 도구 표시는 앞쪽 리소스에 있다
    except OSError:
        return False
    return any(m in head for m in _INSTALLER_MARKS) or bool(_MANIFEST_RE.search(head))
