"""데비안 패키지 정보 — 어떤 파일이 어느 패키지 것인지, SekaiOS 를 깔면 무엇이 따라 깔리는지.

기준은 저장소의 rootfs/ (ISO 를 만든 완전 설치본의 dpkg 기록). 우리 패키지(sekai-*)의 의존성은 rootfs 의 옛 기록 말고
지금 scripts/pack-shell.sh 에 적힌 것을 쓴다 — 고친 뒤 바로 다시 돌려 볼 수 있게."""
import os
import re
from functools import lru_cache

from . import code

ROOTFS = os.environ.get("KANADE_ROOTFS", os.path.join(code.REPO, "rootfs"))
STATUS = os.path.join(ROOTFS, "var/lib/dpkg/status")
INFO = os.path.join(ROOTFS, "var/lib/dpkg/info")
OURS = ("sekai-shell", "sekai-de", "sekaios-base", "sekai-desktop", "sekai-installer")
ROOT_PKG = "sekai-desktop"           # SekaiOS 가 깔린 PC 라면 반드시 있는 것


def available():
    return os.path.isfile(STATUS) and os.path.isdir(INFO)


def _stanzas(text):
    for block in re.split(r"\n\s*\n", text):
        d, key = {}, None
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and key:
                d[key] += " " + line.strip()
            elif ":" in line:
                key, _, v = line.partition(":")
                key = key.strip()
                d[key] = v.strip()
        if d.get("Package"):
            yield d


def _rel(field):
    """'a (>= 1), b | c' → [[a], [b, c]] (버전 조건·아키텍처는 버린다)"""
    out = []
    for grp in (field or "").split(","):
        alts = [re.sub(r"[\s(\[].*$", "", a.strip()).split(":")[0] for a in grp.split("|")]
        alts = [a for a in alts if a]
        if alts:
            out.append(alts)
    return out


@lru_cache(maxsize=None)
def ours_control():
    """pack-shell.sh 의 control 블록들 {이름: stanza}"""
    t = code.pack_text()
    out = {}
    for m in re.finditer(r"<<\s*'?(\w+)'?\n(Package: [\s\S]*?)\n\1\n", t):
        for st in _stanzas(m.group(2)):
            out[st["Package"]] = st
    return out


@lru_cache(maxsize=None)
def db():
    """{패키지: stanza} — 설치된 것만, 우리 것은 pack-shell.sh 로 덮는다"""
    pk = {}
    with open(STATUS, encoding="utf-8", errors="replace") as f:
        for st in _stanzas(f.read()):
            if "installed" in st.get("Status", "") and "not-installed" not in st.get("Status", ""):
                pk[st["Package"]] = st
    for n, st in ours_control().items():
        pk[n] = dict(pk.get(n, {}), **st)
    return pk


@lru_cache(maxsize=None)
def provides():
    out = {}
    for n, st in db().items():
        for grp in _rel(st.get("Provides")):
            for v in grp:
                out.setdefault(v, set()).add(n)
    return out


def _resolve(name):
    d = db()
    if name in d:
        return {name}
    return provides().get(name, set())


@lru_cache(maxsize=None)
def closure(root=ROOT_PKG, recommends=True):
    """root 를 깔면 따라 깔리는 패키지 전부 (apt 기본처럼 Recommends 까지) + 데비안 기본(required·important·Essential).
    대안(a | b)은 설치된 쪽을 따라간다 — rootfs 가 고른 쪽"""
    d = db()
    todo = [root] + [n for n, st in d.items()
                     if st.get("Priority") in ("required", "important") or st.get("Essential") == "yes"]
    seen = set()
    while todo:
        n = todo.pop()
        for p in _resolve(n):
            if p in seen:
                continue
            seen.add(p)
            st = d[p]
            fields = ["Depends", "Pre-Depends"] + (["Recommends"] if recommends else [])
            for f in fields:
                for alts in _rel(st.get(f)):
                    hit = [a for a in alts if _resolve(a)]
                    todo.extend(hit[:1] if hit else [])
    return frozenset(seen)


@lru_cache(maxsize=None)
def owners():
    """{경로: (패키지, …)} — 한 파일에 주인이 여럿일 수 있다 (dpkg-divert 로 덮는 live-tools 의 update-initramfs 따위).
    /usr 합치기(usrmerge)로 /bin·/usr/bin 둘 다"""
    out = {}

    def add(p, pkg):
        cur = out.get(p, ())
        if pkg not in cur:
            out[p] = cur + (pkg,)
    for fn in sorted(os.listdir(INFO)):
        if not fn.endswith(".list"):
            continue
        pkg = fn[:-5].split(":")[0]
        with open(os.path.join(INFO, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.rstrip("\n")
                add(p, pkg)
                if p.startswith(("/bin/", "/sbin/", "/lib/", "/lib64/")):
                    add("/usr" + p, pkg)
                elif p.startswith(("/usr/bin/", "/usr/sbin/")):
                    add(p[4:], pkg)
    return out


@lru_cache(maxsize=None)
def commands():
    """{명령 이름: (패키지, …)} — PATH 에 있는 실행 파일"""
    out = {}
    for p, pkgs in owners().items():
        d, _, b = p.rpartition("/")
        if d in ("/usr/bin", "/usr/sbin", "/bin", "/sbin") and b:
            out[b] = tuple(dict.fromkeys(out.get(b, ()) + pkgs))
    # update-alternatives 로 생기는 이름 (x-terminal-emulator 등)
    alt = os.path.join(ROOTFS, "var/lib/dpkg/alternatives")
    if os.path.isdir(alt):
        for n in os.listdir(alt):
            out.setdefault(n, ("(alternatives)",))
    return out


def owner_of(path):
    return owners().get(path) or owners().get(os.path.realpath(path)) or ()


@lru_cache(maxsize=None)
def typelibs():
    """{네임스페이스: {버전: 패키지}}"""
    out = {}
    for p, pkg in owners().items():
        if "/girepository-1.0/" in p and p.endswith(".typelib"):
            ns, _, ver = os.path.basename(p)[:-8].partition("-")
            out.setdefault(ns, {})[ver] = pkg[0]
    return out


@lru_cache(maxsize=None)
def pymodules():
    """{최상위 파이썬 모듈: 패키지} — dist-packages 와 표준 라이브러리 밖 /usr/lib/python3*"""
    out = {}
    for p, pkg in owners().items():
        m = re.match(r"^/usr/lib/python3(?:\.\d+)?/(?:dist|site)-packages/([A-Za-z_]\w*)(?:/|\.py$|\.cpython|\.abi3|\.so$)", p)
        if m:
            out.setdefault(m.group(1), pkg[0])
    return out
