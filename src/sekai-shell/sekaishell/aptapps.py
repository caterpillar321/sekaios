"""apt 업데이트를 시스템과 앱으로 가른다 — 시스템은 설정 › 업데이트, 앱은 스토어 (윈도우처럼).
GTK 를 가져오지 않는다 (관리자 권한 업데이트 도우미 sekai-update 도 이 모듈을 쓴다).

앱 = 시작 메뉴에 보이는 .desktop 을 싣는 패키지 중 SekaiOS 가 Depends 로 꼭 필요로 하지 않는 것
     (크로뮴·VS Code 처럼 지울 수 있는 것). 스토어로 깔았든 터미널에서 apt install 로 깔았든 같다 —
     apt 는 누가 깔았는지 기록하지 않는다. SekaiOS 저장소에서 오는 업데이트는 앱이어도 시스템 쪽이다.
앱 묶음 = 그 패키지와 같은 소스에서 나온 설치된 패키지 (chromium + chromium-common) — 판이 서로 묶여 있어
     같이 올라가야 한다. 묶음 안에 시스템 패키지가 섞여 있으면 따로 올릴 수 없으니 앱으로 치지 않는다.
시스템 쪽 계산은 업데이트가 있는 앱 묶음을 지금 판에 잠깐 묶어 두고(apt 선호 파일, 우선순위 1001)
시뮬레이션한다 — 묶지 않으면 full-upgrade 가 chromium-common 을 올리려고 chromium 을 지워 버린다.
"""
import configparser
import os
import re
import subprocess
import tempfile

SEKAI_ROOTS = ("sekai-desktop", "sekai-de", "sekai-shell")
APP_DIR = "/usr/share/applications"
PREFS = "/etc/apt/preferences"
ENV = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8", LANGUAGE="")
# Inst 이름 [지금버전] (새버전 출처:묶음[, 출처:묶음…] [아키])  /  Inst 이름 (새버전 …)  ← 새로 설치
INST = re.compile(r"Inst (\S+) (?:\[(\S+)\] )?\((\S+) ([^\[)]*)")


def group_of(origins):
    """시뮬레이션 줄의 출처 부분("SekaiOS:hatsune" · "Debian-Security:13/stable-security, Debian:13.1/stable")
    → (묶음 sekai·debian·other, 보안?). 여러 저장소에 같은 판이 있으면 SekaiOS 가 먼저다 (우리가 고친 판)"""
    names = [o.strip().split(":", 1)[0] for o in origins.split(",") if o.strip()]
    if "SekaiOS" in names:
        return "sekai", False
    if any(n.startswith("Debian") for n in names):
        return "debian", "Debian-Security" in names
    return "other", False


def simulate(args=("full-upgrade",), prefs=None, env=None):
    """→ ((설치될 것 [(이름, 지금, 새, 묶음, 보안?)], 지워질 것 [이름]), None) · 실패하면 (None, 오류).
    실패(다른 프로그램이 dpkg 를 잡고 있음 등)를 "지울 것 없음"으로 읽으면 안 된다 — 보호 패키지 검사가
    빈 목록을 그대로 통과한다(fail-open)."""
    cmd = ["apt-get", "-s", "-q"] + (["-o", f"Dir::Etc::Preferences={prefs}"] if prefs else []) + list(args)
    try:
        res = subprocess.run(cmd, env=env or ENV, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if res.returncode != 0:
        return None, next((l for l in reversed((res.stderr or res.stdout).splitlines())
                           if l.startswith(("E:", "W:"))), f"apt-get 종료 코드 {res.returncode}")
    pk, gone = [], []
    for line in res.stdout.splitlines():
        m = INST.match(line)
        if m:
            pk.append((m.group(1), m.group(2) or "-", m.group(3)) + group_of(m.group(4)))
        elif line.startswith(("Remv ", "Purg ")) and len(line.split()) > 1:
            gone.append(line.split()[1])
    return (pk, gone), None


def system_packages():
    """SekaiOS 의 구성 요소가 (Depends 로) 꼭 필요로 하는 설치된 패키지 전부 — Recommends(크로뮴 등)는 빼서
    사용자의 앱으로 둔다"""
    res = subprocess.run(["apt-cache", "depends", "--recurse", "--no-recommends", "--no-suggests", "--no-conflicts",
                          "--no-breaks", "--no-replaces", "--no-enhances", "--installed", *SEKAI_ROOTS],
                         capture_output=True, text=True, env=ENV)
    return {l.strip() for l in res.stdout.splitlines() if l and not l.startswith(" ")}


def _visible_desktops():
    out = []
    try:
        names = sorted(os.listdir(APP_DIR))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".desktop"):
            continue
        path = os.path.join(APP_DIR, fn)
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        try:
            cp.read(path, encoding="utf-8")
            e = cp["Desktop Entry"]
        except (configparser.Error, KeyError, UnicodeDecodeError):
            continue
        if (e.get("Type", "Application") == "Application" and e.get("NoDisplay", "").lower() != "true"
                and e.get("Hidden", "").lower() != "true"):
            out.append(path)
    return out


def app_units():
    """{앱 패키지: [(묶음 패키지, 설치된 판)]} — 위 설명의 '앱'과 그 묶음"""
    files = _visible_desktops()
    if not files:
        return {}
    res = subprocess.run(["dpkg", "-S"] + files, capture_output=True, text=True, env=ENV)
    owners = set()
    for line in res.stdout.splitlines():
        head = line.split(": /", 1)[0]
        owners.update(p.strip().split(":")[0] for p in head.split(","))
    system = system_packages()
    res = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\t${source:Package}\t${Version}\n"],
                         capture_output=True, text=True, env=ENV)
    src_of, members = {}, {}
    for line in res.stdout.splitlines():
        f = line.split("\t")
        if len(f) == 4 and f[0].startswith("ii"):
            src_of[f[1]] = f[2]
            members.setdefault(f[2], {})[f[1]] = f[3]
    units = {}
    for app in owners:
        if app not in src_of or app in system or app.startswith(("sekai-", "sekaios-")):
            continue
        unit = sorted(members[src_of[app]].items())
        if any(p in system for p, _v in unit):
            continue                            # 시스템 패키지와 같은 소스 — 따로 올릴 수 없다
        units[app] = unit
    return units


def pin_text(units):
    """앱 묶음을 지금 판에 묶는 선호 파일 내용 — 원래 /etc/apt/preferences 가 있으면 그 내용을 앞에 그대로"""
    try:
        with open(PREFS, encoding="utf-8") as f:
            base = f.read().rstrip() + "\n\n"
    except OSError:
        base = ""
    seen, parts = set(), []
    for unit in units.values():
        for p, v in unit:
            if p not in seen:
                seen.add(p)
                parts.append(f"Package: {p}\nPin: version {v}\nPin-Priority: 1001\n")
    return base + "\n".join(parts)


def split(env=None, tmpdir=None):
    """apt 업데이트를 가른다 → (시스템 (설치될 것, 지워질 것), 앱 {앱 패키지: [(이름, 지금, 새)]}, 선호 파일 내용)
    · 실패하면 (None, 오류, None). 선호 파일 내용은 시스템 쪽을 실제로 설치할 때 같은 선호로 쓰라고 ("" 면 묶을 것 없음).
    앱을 묶었더니 시스템 업데이트가 그 앱을 지우려 하면(시스템 쪽 의존성이 새 앱을 요구) 그 앱은 묶지 않는다."""
    full, err = simulate(env=env)
    if full is None:
        return None, err, None
    pk, _gone = full
    changing = {p[0]: p for p in pk}
    units = app_units()
    apps = {}
    for app, unit in units.items():
        ch = [changing[m] for m, _v in unit if m in changing]
        if ch and all(c[3] != "sekai" for c in ch):
            apps[app] = ch
    for _try in range(3):
        if not apps:
            return full, {}, ""
        text = pin_text({a: units[a] for a in apps})
        fd, path = tempfile.mkstemp(prefix="sekai-pin-", dir=tmpdir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            sysd, err = simulate(prefs=path, env=env)
        finally:
            os.unlink(path)
        if sysd is None:
            return full, {}, ""                 # 묶어서 셀 수 없었다 — 가르지 않고 전부 시스템 쪽으로
        gone = set(sysd[1])
        stuck = [a for a in apps if any(m in gone for m, _v in units[a])]
        if not stuck:
            return sysd, {a: [c[:3] for c in ch] for a, ch in apps.items()}, text
        for a in stuck:
            del apps[a]
    return full, {}, ""


def app_entry(pkg):
    """앱 패키지 → (시작 메뉴 이름 (.desktop 의 Name, 한국어 먼저), .desktop 경로) · 못 찾으면 (패키지 이름, None)"""
    global _vis
    if _vis is None:
        _vis = set(_visible_desktops())
    res = subprocess.run(["dpkg", "-L", pkg], capture_output=True, text=True, env=ENV)
    for path in res.stdout.splitlines():
        if path in _vis:
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(path, encoding="utf-8")
                e = cp["Desktop Entry"]
                return e.get("Name[ko]") or e.get("Name") or pkg, path
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
    return pkg, None


_vis = None                                     # 시작 메뉴에 보이는 .desktop (app_entry 가 처음 쓸 때 읽는다)
