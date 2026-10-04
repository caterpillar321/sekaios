"""SekaiOS 시스템 복원 — ext4 등 btrfs 가 아닌 설치본의 복원 지점 (파일 복사, 리눅스 민트의 Timeshift 방식).

sekai-restore 가 루트가 btrfs(@)가 아니면 이 모듈로 넘긴다. 명령·출력은 btrfs 쪽과 같다 (설정 › 복구·복구 화면이 그대로 쓴다).

  저장소  /.sekai-restore/          (관리자만)
            points/<번호>/root/     지점의 시스템 사본 — 바뀌지 않은 파일은 앞 지점과 하드 링크(공간을 안 쓴다)
            points/<번호>/info.json 설명·종류·시각
            points/.tmp-<번호>/     만드는 중 (끝나야 이름을 바꾼다 — 강제 종료로 끊긴 것은 다음에 지운다)
            excludes                 넣지 않는 곳 (initramfs 도 같은 목록으로 되돌린다)
            request · log            되돌리기 요청 · 결과 (initramfs scripts/local-bottom/sekai-restore-copy)
  되돌리기: 다음 시작 때 initramfs 가 지점 → / 로 rsync --delete (넣지 않는 곳은 건드리지 않는다).
    끊기면 요청이 남아 다음 시작에 다시 한다 (rsync 는 몇 번 돌아도 같은 결과). 되돌리기 전에 지금 상태를
    "복원 전 상태" 지점으로 남긴다 → 복원 취소 = 그 지점으로 되돌리기.
  공간: 첫 지점은 시스템 크기만큼(보통 5~8GB), 그다음은 바뀐 파일만큼. 자동 지점은 업데이트·드라이버 설치 전과
    주 1회만 (btrfs 처럼 앱을 깔 때마다 하면 너무 느리다). 자동 2개·중요 2개, 디스크의 LIMIT(기본 20%)를 넘으면 정리.
"""
import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import time

STORE = "/.sekai-restore"
POINTS = STORE + "/points"
REQ = STORE + "/request"
LOG = STORE + "/log"
EXCLUDES = STORE + "/excludes"
CONF = STORE + "/config.json"
LOCKF = STORE + "/lock"
USAGE = STORE + "/usage.json"
DPKG_LOCK = "/var/lib/dpkg/lock-frontend"

LIMIT_DEFAULT = 0.2
KEEP_AUTO, KEEP_IMPORTANT = 2, 2
FIRST_NEED = 8 * 1000 ** 3          # 첫 지점에 필요하다고 보는 여유 (시스템 크기 어림)
NEXT_NEED = 1 * 1000 ** 3
AUTO_KINDS = ("update", "driver", "weekly")   # 자동으로 만드는 종류 (앱 설치 전은 만들지 않는다)

# 넣지 않는 곳 — /home·/root(개인 파일), 기록·캐시, Flatpak(btrfs 쪽과 같게), 가상 파일 시스템, 저장소 자신.
#   "/dir/*" 꼴은 폴더는 남기고 안만 뺀다 (연결 지점이 사라지지 않게)
EXCLUDE_LIST = [
    "/dev/*", "/proc/*", "/sys/*", "/run/*", "/tmp/*", "/mnt/*", "/media/*", "/lost+found",
    "/home/*", "/root/*", "/var/log/*", "/var/cache/*", "/var/tmp/*",
    "/var/lib/flatpak/*", "/var/lib/docker/*", "/var/lib/containers/*", "/var/lib/libvirt/images/*",
    "/swapfile", "/swap.img", "/.sekai-restore", "/.snapshots",
    "/var/lib/sekai/restore.json", "/var/lib/sekai/boot-attempts",
]
RSYNC = ["rsync", "-aHAX", "--numeric-ids", "-x", "--delete", "--delete-excluded"]


class Ctx:
    """sekai-restore 가 넘겨주는 것: out · die · run · apt_desc · AUTO_OFF · CACHE · KIND_TEXT · newest_kernel"""


C = Ctx()


def why():
    if os.path.isdir("/run/live/medium"):
        return "설치 USB(라이브)로 켠 상태입니다"
    if not shutil.which("rsync"):
        return "rsync 가 설치되지 않았습니다"
    return None


def ready():
    return os.path.isdir(POINTS) and os.path.isfile(EXCLUDES)


# ── 저장소 ──────────────────────────────────────────────────
def setup():
    os.makedirs(POINTS, exist_ok=True)
    os.chmod(STORE, 0o700)
    with open(EXCLUDES + ".tmp", "w") as f:
        f.write("\n".join(EXCLUDE_LIST) + "\n")
    os.replace(EXCLUDES + ".tmp", EXCLUDES)
    cleanup_tmp()
    live = os.path.isdir("/run/systemd/system")
    C.run(["systemctl", "enable"] + (["--now"] if live else []) + ["sekai-restore-weekly.timer"], check=False)


def conf():
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_conf(**kw):
    c = conf()
    c.update(kw)
    with open(CONF + ".tmp", "w") as f:
        json.dump(c, f)
    os.replace(CONF + ".tmp", CONF)


class Lock:
    """지점 만들기·지우기는 한 번에 하나"""

    def __enter__(self):
        os.makedirs(STORE, exist_ok=True)
        self.fd = os.open(LOCKF, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


class DpkgLock:
    """apt 밖에서 만들 때는 그동안 패키지가 바뀌지 않게 dpkg 잠금을 쥔다 (apt 훅 안이면 apt 가 쥐고 있다)"""

    def __init__(self, take):
        self.take = take
        self.fd = None

    def __enter__(self):
        if not self.take or not os.path.exists(DPKG_LOCK):
            return self
        self.fd = os.open(DPKG_LOCK, os.O_RDWR)
        for _ in range(240):
            try:
                fcntl.lockf(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                time.sleep(0.5)
        os.close(self.fd)
        self.fd = None
        C.die("다른 설치·업데이트가 끝나지 않아 복원 지점을 만들 수 없습니다 — 잠시 뒤 다시 해 보세요")

    def __exit__(self, *a):
        if self.fd is not None:
            fcntl.lockf(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def point_dirs():
    try:
        return sorted((int(n) for n in os.listdir(POINTS) if n.isdigit()), reverse=True)
    except OSError:
        return []


def cleanup_tmp():
    """강제 종료로 끊긴 "만드는 중" 지점을 지운다"""
    try:
        for n in os.listdir(POINTS):
            if n.startswith(".tmp-"):
                shutil.rmtree(os.path.join(POINTS, n), ignore_errors=True)
    except OSError:
        pass


def info(n):
    try:
        with open(f"{POINTS}/{n}/info.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ── 목록 ────────────────────────────────────────────────────
def list_points():
    pts = []
    for n in point_dirs():
        i = info(n)
        if not i:
            continue
        kind = i.get("kind", "manual")
        pts.append({"number": n, "date": i.get("date", ""), "description": i.get("description", ""),
                    "kind": kind, "kind_text": C.KIND_TEXT.get(kind, kind),
                    "important": bool(i.get("important")),
                    "kernel": C.newest_kernel(f"{POINTS}/{n}/root") or ""})
    return pts


# ── 공간 ────────────────────────────────────────────────────
def usage(refresh=False):
    """저장소가 쓰는 바이트 (하드 링크는 한 번만) — du 는 오래 걸려 바뀔 때만 다시 잰다"""
    if not refresh:
        try:
            with open(USAGE) as f:
                return json.load(f).get("bytes")
        except (OSError, ValueError):
            pass
    r = C.run(["du", "-sxb", POINTS], check=False)
    try:
        b = int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return None
    with open(USAGE + ".tmp", "w") as f:
        json.dump({"bytes": b, "at": time.time()}, f)
    os.replace(USAGE + ".tmp", USAGE)
    return b


def space():
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    lim = float(conf().get("limit", LIMIT_DEFAULT))
    return usage(), int(total * lim), lim


def free_bytes():
    st = os.statvfs("/")
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def prune(keep=()):
    """개수(자동 KEEP_AUTO · 중요 KEEP_IMPORTANT · 복원 전 상태 1)와 공간 상한 — 가장 새 지점 하나는 늘 남긴다.
    keep: 지우면 안 되는 번호 (곧 되돌릴 지점 — "복원 전 상태"로 되돌릴 때 새 "복원 전 상태"가 그것을 밀어냈다)"""
    keep = {int(k) for k in keep}
    pts = [p for p in list_points() if p["number"] not in keep]
    drop = []
    autos = [p for p in pts if not p["important"] and p["kind"] != "before-restore"]
    imps = [p for p in pts if p["important"] and p["kind"] != "before-restore"]
    befores = [p for p in pts if p["kind"] == "before-restore"]
    drop += autos[KEEP_AUTO:] + imps[KEEP_IMPORTANT:] + befores[1:]
    for p in drop:
        shutil.rmtree(f"{POINTS}/{p['number']}", ignore_errors=True)
    used = usage(refresh=True)
    _, limit, _ = space()
    left = [p for p in list_points() if p["number"] not in keep]
    # 상한을 넘으면 오래된 것부터 (자동 → 중요), 하나는 남긴다
    order = sorted(left, key=lambda p: (p["important"], p["number"]))
    while used is not None and used > limit and len(left) > 1 and order:
        p = order.pop(0)
        if p["number"] == left[0]["number"]:
            continue
        shutil.rmtree(f"{POINTS}/{p['number']}", ignore_errors=True)
        left = [q for q in left if q["number"] != p["number"]]
        used = usage(refresh=True)


# ── 만들기 ──────────────────────────────────────────────────
def create(desc, kind="manual", auto=False, in_apt=False, keep=()):
    if not ready():
        setup()
    with Lock():
        cleanup_tmp()
        nums = point_dirs()
        prev = next((n for n in nums if info(n)), None)
        need = NEXT_NEED if prev else FIRST_NEED
        free, total = free_bytes()
        if free - need < total * 0.10:
            if auto:
                print("디스크 여유 공간이 부족해 자동 복원 지점을 만들지 않았습니다", flush=True)
                return 0
            C.die("디스크 여유 공간이 부족해 복원 지점을 만들 수 없습니다 — 파일을 정리한 뒤 다시 해 보세요")
        n = (nums[0] if nums else 0) + 1
        tmp = f"{POINTS}/.tmp-{n}"
        os.makedirs(tmp + "/root", exist_ok=True)
        args = RSYNC + [f"--exclude-from={EXCLUDES}"]
        if prev:
            args.append(f"--link-dest={POINTS}/{prev}/root")
        with DpkgLock(take=not in_apt):
            r = C.run(["ionice", "-c", "3", "nice", "-n", "10"] + args + ["/", tmp + "/root/"], check=False)
        # 24 = 복사하는 사이 사라진 파일 (돌고 있는 시스템이라 흔하다)
        if r.returncode not in (0, 24):
            shutil.rmtree(tmp, ignore_errors=True)
            C.die(f"복원 지점을 만들지 못했습니다 (rsync {r.returncode}): {(r.stderr or '').strip().splitlines()[-1:] }")
        important = kind in ("manual", "first", "driver", "before-restore")
        with open(tmp + "/info.json", "w") as f:
            json.dump({"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "description": desc[:120],
                       "kind": kind, "important": important}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        C.run(["sync"], check=False)
        os.rename(tmp, f"{POINTS}/{n}")
        prune(keep=keep)
    return n


def delete(nums):
    with Lock():
        for n in nums:
            shutil.rmtree(f"{POINTS}/{int(n)}", ignore_errors=True)
        usage(refresh=True)


# ── 되돌리기 ────────────────────────────────────────────────
def read_request():
    try:
        with open(REQ) as f:
            p = f.read().split()
        return {"action": p[0], "arg": p[1]} if len(p) >= 2 else None
    except OSError:
        return None


def write_request(action, arg):
    with open(REQ + ".tmp", "w") as f:
        f.write(f"{action} {arg}\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(REQ + ".tmp", REQ)


def restore(n, undo=False):
    if not info(n):
        C.die(f"복원 지점 {n} 이 없습니다")
    if not undo:
        # 지금 상태를 남긴다 — 복원 취소 = 이 지점으로 되돌리기
        create("복원 전 상태", kind="before-restore", keep=(n,))
    write_request("undo" if undo else "restore", str(n))


def last_log():
    try:
        with open(LOG) as f:
            lines = [ln.split() for ln in f if ln.strip()]
        return lines[-1] if lines else None
    except OSError:
        return None


def status(st):
    st["backend"] = "copy"
    st["used"], st["limit"], st["limit_ratio"] = space()
    st["pending"] = read_request()
    ln = last_log()
    if ln and len(ln) >= 3:
        st["last"] = {"time": ln[0], "action": ln[1], "arg": ln[2], "detail": " ".join(ln[3:])}
    # 복원 취소: 마지막 일이 되돌리기였고 "복원 전 상태"가 남아 있으면
    before = next((p for p in list_points() if p["kind"] == "before-restore"), None)
    if st.get("last") and st["last"]["action"] == "restore" and before:
        st["undo"] = str(before["number"])
    return st


# ── 부팅 메뉴 ───────────────────────────────────────────────
def grub_blocks(uuid, opts, esc, maxn):
    blocks = []
    for p in list_points()[:maxn]:
        k = p["kernel"]
        base = f"/.sekai-restore/points/{p['number']}/root/boot"
        if not k or not os.path.isfile(f"{POINTS}/{p['number']}/root/boot/initrd.img-{k}"):
            continue
        title = esc(f"{p['date'][:16]} — {p['description'] or p['kind_text']}")
        blocks.append(
            f"menuentry '{title}' --class sekaios --class os {{\n"
            f"\tload_video\n\tset gfxpayload=keep\n\tinsmod gzio\n\tinsmod part_gpt\n\tinsmod ext2\n"
            f"\tsearch --no-floppy --fs-uuid --set=root {uuid}\n"
            f"\techo '복원 지점 {p['number']}번으로 되돌리는 중...'\n"
            f"\tlinux {base}/vmlinuz-{k} root=UUID={uuid} ro sekai.restore={p['number']} {opts}\n"
            f"\tinitrd {base}/initrd.img-{k}\n}}\n")
    return blocks


def weekly():
    now = datetime.datetime.now()
    pts = list_points()
    if not pts:
        # 첫 지점은 시스템 크기만큼 쓴다 — 디스크가 넉넉할 때만 저절로 (모자라면 사용자가 직접 만들 수 있다)
        free, total = free_bytes()
        if free - FIRST_NEED < total * 0.30:
            return False
    for p in pts:
        try:
            if now - datetime.datetime.fromisoformat(p["date"][:19]) < datetime.timedelta(days=7):
                return False
        except ValueError:
            continue
    return bool(create("정기 복원 지점", kind="weekly", auto=True))
