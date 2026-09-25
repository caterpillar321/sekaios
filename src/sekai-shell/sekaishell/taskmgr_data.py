"""작업 관리자 — 자료 모으기 (/proc · /sys).

psutil 없이 직접 읽는다 — 필요한 파일이 몇 개뿐이고, 한 파일에서 여러 값을 한꺼번에 얻는다
(프로세스마다 stat·status·io 세 파일, 400개에 몇 ms).
GTK 를 모른다: Collector 스레드에서 돌고 결과(스냅숏 사전)를 콜백으로 넘긴다.
받는 쪽이 GLib.idle_add 로 메인 스레드에 옮긴다. 스냅숏은 만든 뒤 고치지 않는다.
"""
import fcntl
import json
import os
import pwd
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import traceback

CLK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 1
ME = os.getuid()
PF_KTHREAD = 0x00200000
SECTOR = 512
MIB = 1024 * 1024

# 이름으로 알아보는 세션 핵심 — 끝내면 작업 표시줄·바탕화면이 사라지거나 로그아웃된다.
#   이들의 조상(greetd → sekai-session → Hyprland …)도 핵심으로 본다 (core_pids)
CORE_NAMES = {
    "Hyprland", "hyprland", "Xwayland", "Xorg", "X", "xinit", "xfwm4", "sxhkd", "xcape",
    "sekai-session", "sekai-panel", "sekai-desk", "sekai-idle", "sekai-lock", "keep-running",
    "sekai-greeter", "sekai-greeter-session", "greetd", "systemd", "dbus-daemon", "dbus-broker",
    "lxpolkit", "polkit-agent", "nm-agent", "automount",
}


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def _int(s, default=0):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def _num(s):
    """nvidia-smi 의 값 — "[N/A]"·"[Not Supported]" 는 None"""
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ── 프로세스 ─────────────────────────────────────────────────
class Proc:
    __slots__ = ("pid", "ppid", "start", "comm", "name", "state", "uid", "user", "threads",
                 "cpu", "mem", "disk", "argv", "kthread")

    def key(self):
        return f"{self.pid}:{self.start}"

    def cmdline(self):
        return " ".join(self.argv) if self.argv else f"[{self.comm}]"


def nice_name(comm, argv):
    """보여 줄 이름. comm 은 15자에서 잘리고, 스크립트는 comm 이 스크립트 이름이다
    (python3 /usr/bin/sekai-panel → comm "sekai-panel"). argv 에서 comm 으로 시작하는
    온전한 이름을 찾는다. argv[0] 을 제목으로 덮어쓴 것(sshd: user@pts/0)은 comm 으로."""
    for a in (argv or [])[:3]:
        if not a or " " in a or a.startswith("-"):
            continue
        b = os.path.basename(a)
        if b and b.startswith(comm):
            return b
    return comm


class ProcSampler:
    def __init__(self):
        self.prev = {}        # pid → (시작 시각, CPU 틱, 디스크 바이트 또는 None, 잰 때)
        self.cache = {}       # (pid, 시작) → (comm, argv, 이름) — exec 로 comm 이 바뀌면 다시 읽는다
        self.users = {}

    def user(self, uid):
        u = self.users.get(uid)
        if u is None:
            try:
                u = pwd.getpwuid(uid).pw_name
            except (KeyError, OverflowError):
                u = str(uid)
            self.users[uid] = u
        return u

    def sample(self, now):
        out = {}
        nxt = {}
        prev = self.prev
        try:
            names = os.listdir("/proc")
        except OSError:
            return out
        for d in names:
            if not d.isdigit():
                continue
            st = _read(f"/proc/{d}/stat")
            if not st:
                continue
            l, r = st.find("("), st.rfind(")")
            f = st[r + 2:].split()
            try:
                pid = int(d)
                state = f[0]
                ppid = int(f[1])
                flags = int(f[6])
                ticks = int(f[11]) + int(f[12])
                threads = int(f[17])
                start = int(f[19])
            except (IndexError, ValueError):
                continue
            comm = st[l + 1:r]
            kthread = bool(flags & PF_KTHREAD)

            # 사용자는 status 의 실제 uid 로 — /proc/<pid> 의 소유자는 dump 금지 프로세스
            #   (크로미움 샌드박스 등)에서 root 로 보인다
            uid, mem = -1, 0
            s = _read(f"/proc/{d}/status")
            if s:
                i = s.find("\nUid:")
                if i >= 0:
                    uid = _int(s[i + 5:s.find("\n", i + 5)].split()[0], -1)
                # 개인 메모리 = RssAnon (윈도우의 "개인 작업 집합"에 가깝다; 공유 라이브러리·파일 캐시는 뺀다)
                i = s.find("\nRssAnon:")
                if i >= 0:
                    mem = _int(s[i + 9:s.find("\n", i + 9)].split()[0]) * 1024
            if state == "Z":
                mem = 0

            # 디스크 — 남의 프로세스는 커널이 읽기를 막는다 (None = 알 수 없음)
            io = None
            if not kthread and (uid == ME or ME == 0):
                t = _read(f"/proc/{d}/io")
                if t:
                    rb = wb = 0
                    for line in t.splitlines():
                        if line.startswith("read_bytes:"):
                            rb = _int(line[11:])
                        elif line.startswith("write_bytes:"):
                            wb = _int(line[12:])
                    io = rb + wb

            pv = prev.get(pid)
            cpu, disk = 0.0, (0.0 if io is not None else None)
            if pv and pv[0] == start:                  # 같은 프로세스 (pid 재사용이 아니다)
                dt = now - pv[3]
                if dt > 0:
                    cpu = max(0.0, min(100.0, (ticks - pv[1]) / CLK / dt / NCPU * 100))
                    if io is not None and pv[2] is not None:
                        disk = max(0.0, (io - pv[2]) / dt)
            nxt[pid] = (start, ticks, io, now)

            ck = (pid, start)
            c = self.cache.get(ck)
            if c is None or c[0] != comm:
                raw = _read(f"/proc/{d}/cmdline") or ""
                argv = [a for a in raw.split("\0")]
                while argv and argv[-1] == "":
                    argv.pop()
                c = (comm, argv, nice_name(comm, argv))
                self.cache[ck] = c

            p = Proc()
            p.pid, p.ppid, p.start, p.comm = pid, ppid, start, comm
            p.name, p.argv = c[2], c[1]
            p.state, p.uid, p.user = state, uid, (self.user(uid) if uid >= 0 else "")
            p.threads, p.cpu, p.mem, p.disk, p.kthread = threads, cpu, mem, disk, kthread
            out[pid] = p
        self.prev = nxt
        live = {(pid, v[0]) for pid, v in nxt.items()}
        for k in [k for k in self.cache if k not in live]:
            del self.cache[k]
        return out


def children_map(procs):
    ch = {}
    for p in procs.values():
        ch.setdefault(p.ppid, []).append(p.pid)
    return ch


def descendants(procs, pids, ch=None):
    """pids 와 그 자손 전부"""
    ch = ch if ch is not None else children_map(procs)
    out, stack = set(), list(pids)
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(ch.get(pid, ()))
    return out


def core_pids(procs):
    """세션 핵심 프로세스와 그 조상 (pid 1 은 빼고) — 끝내기 전에 확인을 묻는다"""
    core = set()
    for p in procs.values():
        if p.kthread or (p.name not in CORE_NAMES and p.comm not in CORE_NAMES):
            continue
        q = p
        while q is not None and q.pid > 1 and q.pid not in core:
            core.add(q.pid)
            q = procs.get(q.ppid)
    return core


def proc_alive(pid, start):
    """아직 같은 프로세스가 살아 있나 (좀비는 죽은 것으로)"""
    st = _read(f"/proc/{pid}/stat")
    if not st:
        return False
    f = st[st.rfind(")") + 2:].split()
    try:
        return f[0] not in ("Z", "X") and int(f[19]) == start
    except (IndexError, ValueError):
        return False


def exe_path(p):
    """파일 위치 열기용 실행 파일 경로. 파이썬 등 해석기로 도는 스크립트는 스크립트 파일을."""
    exe = None
    try:
        exe = os.readlink(f"/proc/{p.pid}/exe")
        if exe.endswith(" (deleted)"):
            exe = exe[:-10]
    except OSError:
        pass
    argv = p.argv or []
    interp = re.compile(r"^(python[0-9.]*|perl|ruby|node|bash|sh|dash|lua[0-9.]*)$")
    if argv and (exe is None or interp.match(os.path.basename(exe))):
        for a in argv[1:3]:
            if a.startswith("-"):
                continue
            if os.path.isabs(a) and os.path.isfile(a):
                return a
            break
    if exe:
        return exe
    if argv:
        a0 = argv[0]
        if os.path.isabs(a0) and os.path.exists(a0):
            return a0
        return shutil.which(a0)
    return None


# ── CPU ──────────────────────────────────────────────────────
def _cache_kb(s):
    s = (s or "").strip().upper()
    m = re.match(r"^(\d+)([KMG]?)", s)
    if not m:
        return 0
    return int(m.group(1)) * {"": 1 / 1024, "K": 1, "M": 1024, "G": 1024 * 1024}[m.group(2)]


def cpu_static():
    """바뀌지 않는 CPU 정보 — 모델, 소켓·코어·논리 프로세서, 기본 속도, 캐시, 가상화"""
    info = {"model": "", "sockets": 1, "cores": NCPU, "logical": NCPU, "base": None, "base_label": "기본 속도",
            "l1": 0, "l2": 0, "l3": 0, "virt": None, "vm": False, "mhz": None}
    txt = _read("/proc/cpuinfo") or ""
    mhz = []
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "model name" and not info["model"]:
            info["model"] = re.sub(r"\s+", " ", v)
        elif k == "cpu MHz":
            try:
                mhz.append(float(v))
            except ValueError:
                pass
        elif k == "flags" and info["virt"] is None:
            fl = set(v.split())
            info["virt"] = "지원" if fl & {"vmx", "svm"} else "지원 안 함"
            info["vm"] = "hypervisor" in fl
    if mhz:
        info["mhz"] = sum(mhz) / len(mhz)
    base = "/sys/devices/system/cpu"
    try:
        cpus = sorted(int(n[3:]) for n in os.listdir(base) if re.match(r"^cpu\d+$", n))
    except OSError:
        cpus = []
    pk, cores = set(), set()
    caches = set()
    for c in cpus:
        t = f"{base}/cpu{c}/topology"
        pkg = _read(f"{t}/physical_package_id")
        core = _read(f"{t}/core_id")
        if pkg is not None:
            pk.add(pkg.strip())
            cores.add((pkg.strip(), (core or str(c)).strip()))
        cdir = f"{base}/cpu{c}/cache"
        try:
            idx = os.listdir(cdir)
        except OSError:
            idx = []
        for i in idx:
            if not i.startswith("index"):
                continue
            lv = (_read(f"{cdir}/{i}/level") or "").strip()
            ty = (_read(f"{cdir}/{i}/type") or "").strip()
            sh = (_read(f"{cdir}/{i}/shared_cpu_list") or str(c)).strip()
            caches.add((lv, ty, sh, _cache_kb(_read(f"{cdir}/{i}/size"))))
    if pk:
        info["sockets"] = len(pk)
        info["cores"] = len(cores)
    info["logical"] = len(cpus) or NCPU
    for lv, _ty, _sh, kb in caches:
        if lv in ("1", "2", "3"):
            info["l" + lv] += kb
    for name, label in (("base_frequency", "기본 속도"), ("amd_pstate_nominal_freq", "기본 속도"),
                        ("cpuinfo_max_freq", "최대 속도")):
        v = _read(f"{base}/cpu0/cpufreq/{name}")
        if v and _int(v) > 0:
            info["base"] = _int(v) / 1e6            # kHz → GHz
            info["base_label"] = label
            break
    if info["base"] is None and info["mhz"]:
        info["base"] = info["mhz"] / 1000
    return info


def cpu_freq_ghz():
    """지금 평균 속도 — cpufreq 가 없으면(가상 머신 등) None"""
    base = "/sys/devices/system/cpu"
    vals = []
    try:
        names = os.listdir(base)
    except OSError:
        return None
    for n in names:
        if re.match(r"^cpu\d+$", n):
            v = _read(f"{base}/{n}/cpufreq/scaling_cur_freq")
            if v:
                vals.append(_int(v))
    vals = [v for v in vals if v > 0]
    return sum(vals) / len(vals) / 1e6 if vals else None


def _stat_cpu():
    """/proc/stat → {"cpu": (전체, 쉼), "cpu0": …}"""
    out = {}
    for line in (_read("/proc/stat") or "").splitlines():
        if not line.startswith("cpu"):
            break
        f = line.split()
        v = [int(x) for x in f[1:9]]            # guest·guest_nice 는 user·nice 에 이미 들어 있다
        v += [0] * (8 - len(v))
        out[f[0]] = (sum(v), v[3] + v[4])       # idle + iowait
    return out


# ── 메모리 ───────────────────────────────────────────────────
def meminfo():
    m = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        f = v.split()
        if f:
            m[k] = _int(f[0]) * 1024
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", m.get("MemFree", 0))
    cache = m.get("Buffers", 0) + m.get("Cached", 0) + m.get("SReclaimable", 0)
    return {"total": total, "avail": avail, "used": max(0, total - avail), "free": m.get("MemFree", 0),
            "cached": cache, "shmem": m.get("Shmem", 0),
            "commit": m.get("Committed_AS", 0), "commit_limit": m.get("CommitLimit", 0),
            "swap_total": m.get("SwapTotal", 0),
            "swap_used": max(0, m.get("SwapTotal", 0) - m.get("SwapFree", 0))}


# ── 디스크 ───────────────────────────────────────────────────
def _unescape_mount(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def disk_inventory():
    """물리 디스크 목록 — 모델·종류·크기·마운트 위치. 루프·램·dm(LVM·LUKS) 같은 가상 장치는 빼고,
    그 위의 마운트는 밑에 깔린 디스크로 돌려 셈한다."""
    disks = []
    owner = {}                                   # 파티션·디스크 이름 → 디스크 이름
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        names = []
    for name in names:
        if name.startswith(("loop", "ram", "zram", "fd", "dm-", "md", "nbd")) or \
                re.match(r"^nvme\d+c\d+n\d+$", name):
            continue
        b = f"/sys/block/{name}"
        if not os.path.exists(f"{b}/device") and not name.startswith(("nvme", "mmcblk", "vd", "xvd")):
            continue
        size = _int(_read(f"{b}/size")) * SECTOR
        if size <= 0:                            # 빈 카드 리더·광학 드라이브
            continue
        model = ((_read(f"{b}/device/model") or _read(f"{b}/device/name") or "").strip())
        real = os.path.realpath(b)
        if name.startswith("sr"):
            kind = "광학 드라이브"
        elif "/usb" in real:
            kind = "이동식 (USB)"
        elif name.startswith("nvme"):
            kind = "SSD (NVMe)"
        elif name.startswith("mmcblk"):
            kind = "SD·eMMC"
        elif (_read(f"{b}/queue/rotational") or "1").strip() == "0":
            kind = "SSD"
        else:
            kind = "HDD"
        owner[name] = name
        try:
            for part in os.listdir(b):
                if os.path.exists(f"{b}/{part}/partition"):
                    owner[part] = name
        except OSError:
            pass
        disks.append({"id": name, "dev": "/dev/" + name, "model": model, "kind": kind, "size": size,
                      "mounts": [], "system": False, "swap": False})

    def base_disks(dev, depth=0):
        """dm-N(LVM·LUKS) → 밑의 파티션 → 디스크"""
        if dev in owner:
            return {owner[dev]}
        if depth > 6:
            return set()
        out = set()
        try:
            for s in os.listdir(f"/sys/class/block/{dev}/slaves"):
                out |= base_disks(s, depth + 1)
        except OSError:
            pass
        return out

    by = {d["id"]: d for d in disks}
    for line in (_read("/proc/self/mounts") or "").splitlines():
        f = line.split()
        if len(f) < 2 or not f[0].startswith("/dev/"):
            continue
        dev = os.path.basename(os.path.realpath(f[0]))
        mp = _unescape_mount(f[1])
        for dn in base_disks(dev):
            d = by[dn]
            if mp not in d["mounts"]:
                d["mounts"].append(mp)
            if mp == "/":
                d["system"] = True
    for line in (_read("/proc/swaps") or "").splitlines()[1:]:
        f = line.split()
        if f and f[0].startswith("/dev/"):
            for dn in base_disks(os.path.basename(os.path.realpath(f[0]))):
                by[dn]["swap"] = True
    for d in disks:
        d["mounts"].sort(key=lambda m: (m != "/", m.count("/"), m))
    # 번호 — 윈도우처럼 "디스크 0, 1 …" (NVMe 를 먼저)
    disks.sort(key=lambda d: (not d["id"].startswith("nvme"), d["id"].startswith("sr"), d["id"]))
    for i, d in enumerate(disks):
        d["index"] = i
    return disks


def _diskstats():
    out = {}
    for line in (_read("/proc/diskstats") or "").splitlines():
        f = line.split()
        if len(f) >= 14:
            try:
                out[f[2]] = (int(f[3]), int(f[5]), int(f[6]), int(f[7]), int(f[9]), int(f[10]), int(f[12]))
            except ValueError:
                pass
    return out


# ── 네트워크 ─────────────────────────────────────────────────
def _ipv4(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        r = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", ifname[:15].encode()))  # SIOCGIFADDR
        return socket.inet_ntoa(r[20:24])
    except OSError:
        return ""
    finally:
        s.close()


def _ipv6_all():
    out = {}
    for line in (_read("/proc/net/if_inet6") or "").splitlines():
        f = line.split()
        if len(f) < 6:
            continue
        try:
            addr = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(f[0]))
        except (ValueError, OSError):
            continue
        scope = f[3]
        rank = 0 if scope == "00" else 1              # 전역 주소를 링크 로컬보다 먼저
        out.setdefault(f[5], []).append((rank, addr))
    return {k: [a for _r, a in sorted(v)] for k, v in out.items()}


def net_inventory():
    """물리 네트워크 어댑터 — lo·브리지·veth·tun·docker 같은 가상 장치(/sys/devices/virtual)는 뺀다"""
    out = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return out
    v6 = _ipv6_all()
    for n in names:
        p = f"/sys/class/net/{n}"
        if "/virtual/" in os.path.realpath(p):
            continue
        wifi = os.path.isdir(f"{p}/wireless") or os.path.exists(f"{p}/phy80211")
        oper = (_read(f"{p}/operstate") or "").strip()
        speed = _int(_read(f"{p}/speed"), -1)             # 연결이 없으면 읽기 오류(-1)
        drv = os.path.basename(os.path.realpath(f"{p}/device/driver")) if os.path.exists(f"{p}/device/driver") else ""
        out.append({"id": n, "kind": "Wi-Fi" if wifi else "이더넷", "up": oper in ("up", "unknown"),
                    "speed": speed if speed > 0 else None, "driver": drv,
                    "ipv4": _ipv4(n), "ipv6": (v6.get(n) or [""])[0]})
    return out


def _netdev():
    out = {}
    for line in (_read("/proc/net/dev") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            out[name.strip()] = (_int(f[0]), _int(f[8]))       # 받은·보낸 바이트
    return out


# ── GPU ──────────────────────────────────────────────────────
_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel", "0x15ad": "VMware",
            "0x1af4": "Virtio", "0x1234": "QEMU", "0x80ee": "VirtualBox", "0x1414": "Microsoft"}


def _lspci_name(slot, vendor):
    """lspci 의 이름 — "GA106 [GeForce RTX 3060]" 처럼 대괄호 안이 상품 이름"""
    try:
        out = subprocess.run(["lspci", "-vmm", "-s", slot], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    dev = ""
    for line in out.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "Device":
            dev = v.strip()
            break
    if not dev:
        return None
    m = re.search(r"\[([^\]]+)\]\s*$", dev)
    return f"{_VENDORS.get(vendor, '')} {m.group(1) if m else dev}".strip()


def gpu_inventory():
    """PCI 의 디스플레이 장치 (VGA·3D·기타). drm 카드가 없어도(NVIDIA 드라이버만) 잡힌다.
    느린 lspci 를 부르므로 작업 스레드에서 한 번만."""
    out = []
    root = "/sys/bus/pci/devices"
    try:
        slots = sorted(os.listdir(root))
    except OSError:
        return out
    for slot in slots:
        d = f"{root}/{slot}"
        cls = (_read(f"{d}/class") or "").strip()
        if not cls.startswith(("0x0300", "0x0302", "0x0380")):
            continue
        drv = os.path.basename(os.path.realpath(f"{d}/driver")) if os.path.exists(f"{d}/driver") else ""
        if drv == "dxgkrnl":                     # WSL 의 가상 장치 — 실제 GPU 는 nvidia-smi 쪽에서 잡힌다
            continue
        vendor = (_read(f"{d}/vendor") or "").strip()
        name = _lspci_name(slot, vendor) or f"{_VENDORS.get(vendor, '')} GPU".strip()
        hw = None
        try:
            for h in os.listdir(f"{d}/hwmon"):
                if os.path.exists(f"{d}/hwmon/{h}/temp1_input"):
                    hw = f"{d}/hwmon/{h}/temp1_input"
                    break
        except OSError:
            pass
        out.append({"id": slot, "dir": d, "vendor": vendor, "driver": drv, "name": name, "temp_path": hw})
    return out


def nvidia_driver_version():
    t = _read("/proc/driver/nvidia/version") or ""
    m = re.search(r"\s(\d+\.\d+(?:\.\d+)?)\s", t)
    return m.group(1) if m else ""


def gpu_sample(inv):
    """sysfs 로 읽을 수 있는 값 (amdgpu). NVIDIA 는 NvidiaPoller 가 따로."""
    out = []
    for g in inv:
        d = g["dir"]
        busy = _read(f"{d}/gpu_busy_percent")
        used = _read(f"{d}/mem_info_vram_used")
        total = _read(f"{d}/mem_info_vram_total")
        temp = _read(g["temp_path"]) if g["temp_path"] else None
        out.append({"id": g["id"], "name": g["name"], "vendor": g["vendor"], "driver": g["driver"],
                    "busy": float(_int(busy)) if busy is not None else None,
                    "vram_used": _int(used) if used is not None else None,
                    "vram_total": _int(total) if total is not None else None,
                    "temp": _int(temp) / 1000 if temp is not None else None, "sleep": False})
    return out


def _norm_bus(bus):
    """nvidia-smi 의 00000000:01:00.0 → sysfs 의 0000:01:00.0"""
    m = re.match(r"^([0-9a-fA-F]+):([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$", bus.strip())
    return f"{int(m.group(1), 16):04x}:{m.group(2).lower()}" if m else bus.strip().lower()


def parse_nvidia_smi(text):
    """--query-gpu=pci.bus_id,name,utilization.gpu,memory.used,memory.total,temperature.gpu
    --format=csv,noheader,nounits 의 출력 → {pci 슬롯: 값}"""
    out = {}
    for line in (text or "").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 6:
            continue
        bus, nums = f[0], f[-4:]
        name = ", ".join(f[1:-4])
        used, total = _num(nums[1]), _num(nums[2])
        out[_norm_bus(bus)] = {"name": name, "busy": _num(nums[0]),
                               "vram_used": int(used * MIB) if used is not None else None,
                               "vram_total": int(total * MIB) if total is not None else None,
                               "temp": _num(nums[3]), "sleep": False}
    return out


class NvidiaPoller(threading.Thread):
    """nvidia-smi 를 1초마다 (성능 탭이 보일 때만 — 한 번에 수십 ms 의 CPU 를 쓴다).
    런타임 절전(노트북의 꺼진 dGPU)이면 부르지 않는다 — nvidia-smi 가 GPU 를 깨운다."""
    QUERY = "pci.bus_id,name,utilization.gpu,memory.used,memory.total,temperature.gpu"

    def __init__(self, slots):
        super().__init__(daemon=True, name="taskmgr-nvidia")
        self.slots = list(slots)                  # PCI 에서 찾은 NVIDIA 장치 (WSL 등에서는 빈 목록)
        self.active = False
        self.period = 1.0
        self.latest = {}
        self._ev = threading.Event()
        self._quit = False
        self._fails = 0

    def set_active(self, on, period=None):
        if period is not None:
            self.period = max(1.0, period)
        if on != self.active:
            self.active = on
            self._ev.set()

    def stop(self):
        self._quit = True
        self._ev.set()

    def _run_smi(self, ids=None):
        cmd = ["nvidia-smi", "--query-gpu=" + self.QUERY, "--format=csv,noheader,nounits"]
        if ids:
            cmd += ["-i", ids]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            raise OSError(p.stderr.strip() or f"nvidia-smi {p.returncode}")
        return parse_nvidia_smi(p.stdout)

    def poll(self):
        asleep = [s for s in self.slots
                  if (_read(f"/sys/bus/pci/devices/{s}/power/runtime_status") or "").strip() == "suspended"]
        res = {s: {"sleep": True} for s in asleep}
        if self.slots and len(asleep) == len(self.slots):
            return res
        if asleep:
            for s in self.slots:
                if s not in asleep:
                    res.update(self._run_smi(s))
        else:
            res.update(self._run_smi())
        return res

    def run(self):
        while not self._quit:
            self._ev.clear()
            if not self.active:
                self._ev.wait()
                continue
            t0 = time.monotonic()
            try:
                self.latest = self.poll()
                self._fails = 0
            except (OSError, subprocess.SubprocessError) as e:
                self._fails += 1
                if self._fails == 1:
                    print("[sekai-taskmgr] nvidia-smi:", e, flush=True)
            # 계속 실패하면(드라이버 없음 등) 30초에 한 번만 다시 시도
            wait = 30.0 if self._fails >= 3 else self.period
            self._ev.wait(max(0.2, wait - (time.monotonic() - t0)))


# ── Hyprland 창 목록 ─────────────────────────────────────────
def _hypr_socket():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{ME}")
    return f"{rt}/hypr/{sig}/.socket.sock"


def hypr_request(payload, timeout=1.5):
    """hyprctl 과 같은 요청을 소켓으로 (프로세스를 띄우지 않는다)"""
    path = _hypr_socket()
    if not path:
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(payload.encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf
    except OSError:
        return None
    finally:
        s.close()


def hypr_clients():
    """hyprctl -j clients 의 pid·class·title·주소. Hyprland 가 아니면 None"""
    raw = hypr_request("j/clients")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    out = []
    for c in data if isinstance(data, list) else []:
        if not c.get("mapped", True) or not c.get("pid") or c.get("pid", 0) <= 0:
            continue
        ws = (c.get("workspace") or {}).get("name", "")
        out.append({"pid": c["pid"], "class": c.get("class") or c.get("initialClass") or "",
                    "title": c.get("title") or c.get("initialTitle") or "",
                    "address": c.get("address") or "", "minimized": ws == "special:min"})
    return out


# ── 수집기 ───────────────────────────────────────────────────
class Collector(threading.Thread):
    """정해진 간격(또는 wake)마다 스냅숏을 만들어 deliver(snap) 를 부른다 — 작업 스레드에서.

    snap = {"t", "cpu", "percpu", "freq", "uptime", "nproc", "nthreads", "mem",
            "disks": [...], "nets": [...], "gpus": [...],
            "procs": {pid: Proc} (want_procs 일 때만), "clients": [...] (want_clients 이고 Hyprland 일 때)}
    """

    def __init__(self, deliver):
        super().__init__(daemon=True, name="taskmgr-collector")
        self.deliver = deliver
        self.interval = 1.0              # None = 일시 중지
        self.want_procs = False
        self.want_clients = False
        self._ev = threading.Event()
        self._quit = False
        self.procs = ProcSampler()
        self.cpu_info = None
        self._cpu_prev = None
        self._disk_prev, self._net_prev = {}, {}
        self._disk_inv, self._disk_inv_t = [], -1e9
        self._net_inv, self._net_inv_t = [], -1e9
        self._gpu_inv = None
        self.nvidia = None
        self._last = None

    def wake(self):
        self._ev.set()

    def stop(self):
        self._quit = True
        self._ev.set()
        if self.nvidia:
            self.nvidia.stop()

    def run(self):
        while not self._quit:
            self._ev.clear()
            t0 = time.monotonic()
            try:
                snap = self.sample()
            except Exception:
                traceback.print_exc()
                snap = None
            if snap is not None and not self._quit:
                self.deliver(snap)
            iv = self.interval
            if iv is None:
                self._ev.wait()
            else:
                self._ev.wait(max(0.05, iv - (time.monotonic() - t0)))

    # ── 한 번 재기 ──
    def sample(self):
        now = time.monotonic()
        dt = (now - self._last) if self._last else None
        self._last = now
        if self.cpu_info is None:
            self.cpu_info = cpu_static()
        snap = {"t": now, "cpuinfo": self.cpu_info}

        cur = _stat_cpu()
        prev = self._cpu_prev or {}
        self._cpu_prev = cur

        def pct(name):
            a, b = cur.get(name), prev.get(name)
            if not a or not b or a[0] <= b[0]:
                return 0.0
            return max(0.0, min(100.0, 100.0 * (1 - (a[1] - b[1]) / (a[0] - b[0]))))
        snap["cpu"] = pct("cpu")
        snap["percpu"] = [pct(f"cpu{i}") for i in range(self.cpu_info["logical"])]
        snap["freq"] = cpu_freq_ghz()
        up = (_read("/proc/uptime") or "0").split()
        snap["uptime"] = float(up[0]) if up else 0.0
        la = (_read("/proc/loadavg") or "").split()
        snap["nthreads"] = _int(la[3].split("/")[1]) if len(la) > 3 and "/" in la[3] else 0
        try:
            snap["nproc"] = sum(1 for d in os.listdir("/proc") if d.isdigit())
        except OSError:
            snap["nproc"] = 0
        snap["mem"] = meminfo()

        # 디스크 — 목록(모델·마운트)은 10초마다, 새 장치가 보이면 바로
        ds = _diskstats()
        if now - self._disk_inv_t > 10 or any(d["id"] not in ds for d in self._disk_inv):
            self._disk_inv, self._disk_inv_t = disk_inventory(), now
        disks = []
        for d in self._disk_inv:
            cur_d, prev_d = ds.get(d["id"]), self._disk_prev.get(d["id"])
            busy = rd = wr = resp = 0.0
            if cur_d and prev_d and dt:
                dr, dw = cur_d[0] - prev_d[0], cur_d[3] - prev_d[3]
                rd = max(0, cur_d[1] - prev_d[1]) * SECTOR / dt
                wr = max(0, cur_d[4] - prev_d[4]) * SECTOR / dt
                busy = max(0.0, min(100.0, (cur_d[6] - prev_d[6]) / (dt * 1000) * 100))
                if dr + dw > 0:
                    resp = max(0.0, ((cur_d[2] - prev_d[2]) + (cur_d[5] - prev_d[5])) / (dr + dw))
            disks.append(dict(d, busy=busy, rd=rd, wr=wr, resp=resp))
        self._disk_prev = ds
        snap["disks"] = disks

        # 네트워크 — 주소는 5초마다
        nd = _netdev()
        if now - self._net_inv_t > 5 or any(n["id"] not in nd for n in self._net_inv):
            self._net_inv, self._net_inv_t = net_inventory(), now
        nets = []
        for n in self._net_inv:
            c, p = nd.get(n["id"]), self._net_prev.get(n["id"])
            rx = tx = 0.0
            if c and p and dt:
                rx = max(0, c[0] - p[0]) * 8 / dt
                tx = max(0, c[1] - p[1]) * 8 / dt
            nets.append(dict(n, rx=rx, tx=tx))
        self._net_prev = nd
        snap["nets"] = nets

        # GPU — 목록은 처음 한 번 (lspci)
        if self._gpu_inv is None:
            self._gpu_inv = gpu_inventory()
            nv_slots = [g["id"] for g in self._gpu_inv if g["vendor"] == "0x10de"]
            if shutil.which("nvidia-smi") and (nv_slots or os.path.exists("/proc/driver/nvidia")
                                               or os.path.exists("/dev/dxg")):
                self.nvidia = NvidiaPoller(nv_slots)
                self.nvidia.start()
        gpus = gpu_sample(self._gpu_inv)
        if self.nvidia:
            latest = self.nvidia.latest
            drv = nvidia_driver_version()
            seen = set()
            for g in gpus:
                v = latest.get(g["id"])
                if v is not None:
                    seen.add(g["id"])
                    g.update({k: val for k, val in v.items() if k != "name" or val})
                if g["vendor"] == "0x10de" and drv:
                    g["driver"] = f"{g['driver'] or 'nvidia'} {drv}"
            for bus, v in sorted(latest.items()):          # PCI 목록에 없는 GPU (WSL 등)
                if bus not in seen and not v.get("sleep"):
                    gpus.append(dict({"id": bus, "vendor": "0x10de", "driver": f"nvidia {drv}".strip()}, **v))
        snap["gpus"] = gpus

        if self.want_procs:
            snap["procs"] = self.procs.sample(time.monotonic())
        if self.want_clients:
            snap["clients"] = hypr_clients()
        return snap


# ── 형식 ─────────────────────────────────────────────────────
def fmt_pct(v):
    return "0%" if v is None or v < 0.05 else f"{v:.1f}%"


def fmt_mb(b):
    return f"{(b or 0) / MIB:.1f}MB"


def fmt_rate_mb(b):
    """프로세스 목록의 디스크 열 (윈도우처럼 늘 MB/s)"""
    if b is None:
        return ""
    v = b / MIB
    return "0MB/s" if v < 0.05 else f"{v:.1f}MB/s"


def fmt_rate(b):
    """성능 탭의 디스크 속도 (KB/s · MB/s)"""
    b = b or 0
    if b < MIB:
        return f"{b / 1024:.0f}KB/s"
    return f"{b / MIB:.1f}MB/s"


def fmt_bits(bps):
    """네트워크 (윈도우처럼 1000 단위 Kbps · Mbps · Gbps)"""
    bps = bps or 0
    if bps < 50:
        return "0 Kbps"
    if bps < 1e6:
        return f"{bps / 1e3:.1f} Kbps"
    if bps < 1e9:
        return f"{bps / 1e6:.1f} Mbps"
    return f"{bps / 1e9:.1f} Gbps"


def fmt_gb(b, digits=1):
    return f"{(b or 0) / (1024 ** 3):.{digits}f}GB"


def fmt_size(b):
    """디스크 용량 — 1 TB 가 넘으면 TB"""
    b = b or 0
    if b >= 1024 ** 4:
        return f"{b / 1024 ** 4:.1f}TB"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.0f}GB"
    return f"{b / MIB:.0f}MB"


def fmt_kb(kb):
    kb = kb or 0
    return f"{kb / 1024:.1f}MB" if kb >= 1024 else f"{kb:.0f}KB"


def fmt_uptime(sec):
    sec = int(sec or 0)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}:{h:02d}:{m:02d}:{s:02d}"
