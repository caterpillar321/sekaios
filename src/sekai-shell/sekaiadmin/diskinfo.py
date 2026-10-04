"""컴퓨터 관리 — 디스크 관리의 자료 (udisks2 의 GetManagedObjects → 디스크 · 볼륨 · 빈 공간).

GTK 없음 — 화면은 pages/disks.py. udisks2 는 데스크톱 환경과 상관없는 공용 디스크 서비스이고(GNOME 디스크 등도
이것을 쓴다), 관리자 권한이 필요한 동작은 udisks 가 polkit 으로 묻는다 → SekaiOS 의 사용자 계정 컨트롤 창.

build(objects) → [Disk]. objects 는 GetManagedObjects 의 결과를 unpack 한 것 {경로: {인터페이스: {속성: 값}}}.
"""
import os
import re

UD = "org.freedesktop.UDisks2"
I_BLOCK = UD + ".Block"
I_DRIVE = UD + ".Drive"
I_PART = UD + ".Partition"
I_TABLE = UD + ".PartitionTable"
I_FS = UD + ".Filesystem"
I_SWAP = UD + ".Swapspace"
I_LOOP = UD + ".Loop"
I_CRYPT = UD + ".Encrypted"
I_ATA = UD + ".Drive.Ata"
I_NVME = UD + ".NVMe.Controller"

# Manager.CanResize 의 방식 (libblockdev BDFSResizeFlags)
RESIZE_OFFLINE_SHRINK, RESIZE_OFFLINE_GROW, RESIZE_ONLINE_SHRINK, RESIZE_ONLINE_GROW = 2, 4, 8, 16

MIB = 1024 * 1024
MIN_FREE = 8 * MIB              # 이보다 작은 틈은 빈 공간으로 보이지 않는다 (정렬 때문에 생기는 자투리)
SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home")
LIVE_MEDIA = ("/run/live/medium", "/lib/live/mount/medium", "/run/initramfs/live", "/cdrom")

# 파일 시스템 — (udisks 이름, 보이는 이름, 설명). 새 볼륨·포맷 창의 순서
FS_CHOICES = [
    ("ntfs", "NTFS", "윈도우와 함께 쓰는 디스크 — 윈도우에서도 읽고 쓸 수 있습니다"),
    ("exfat", "exFAT", "USB 메모리·외장 디스크 — 윈도우·맥·휴대폰 어디서나"),
    ("ext4", "ext4", "SekaiOS(리눅스) 전용 — 가장 빠르고 안정적입니다"),
    ("vfat", "FAT32", "아주 오래된 기기·카메라·자동차 — 파일 하나가 4GB 를 넘을 수 없습니다"),
]
FS_NAMES = {"ntfs": "NTFS", "exfat": "exFAT", "ext4": "ext4", "ext3": "ext3", "ext2": "ext2", "vfat": "FAT32",
            "btrfs": "Btrfs", "xfs": "XFS", "swap": "스왑", "crypto_LUKS": "암호화(LUKS)",
            "BitLocker": "BitLocker 암호화", "iso9660": "CD/DVD", "udf": "UDF", "squashfs": "squashfs",
            "LVM2_member": "LVM", "linux_raid_member": "RAID", "hfsplus": "HFS+", "apfs": "APFS", "f2fs": "F2FS"}
# 레이블에 쓸 수 있는 길이
LABEL_MAX = {"ntfs": 32, "exfat": 15, "ext4": 16, "vfat": 11}

# 윈도우가 "기본 데이터"로 보는 GPT 종류·MBR 종류 — NTFS·exFAT·FAT32 새 볼륨이 윈도우에서도 보이게
GPT_TYPE = {"ntfs": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "exfat": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            "vfat": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "ext4": "0fc63daf-8483-4772-8e79-3d69d8477de4"}
MBR_TYPE = {"ntfs": "0x07", "exfat": "0x07", "vfat": "0x0c", "ext4": "0x83"}
GPT_NAMES = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EFI 시스템 파티션",
             "e3c9e316-0b5c-4db8-817d-f92df00215ae": "Microsoft 예약 파티션",
             "de94bba4-06d1-4d40-a16a-bfd50179d6ac": "윈도우 복구 파티션",
             "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f": "스왑",
             "21686148-6449-6e6f-744e-656564454649": "BIOS 부트 파티션"}
# 속성 창의 "파티션 종류" — 위 특수 파티션에 더해 흔한 것
PTYPE_NAMES = dict(GPT_NAMES, **{
    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7": "기본 데이터 파티션 (윈도우와 같이 씀)",
    "0fc63daf-8483-4772-8e79-3d69d8477de4": "리눅스 파일 시스템",
    "4f68bce3-e8cd-4db1-96e7-fbcaf984b709": "리눅스 루트 (x86-64)",
    "933ac7e1-2eb4-4f13-b844-0e14e2aef915": "리눅스 홈",
    "e6d6d379-f507-44c2-a23c-238f2a3df928": "리눅스 LVM",
    "ca7d7ccb-63ed-4c53-861c-1742536059cc": "LUKS 암호화",
    "0x07": "NTFS · exFAT", "0x0c": "FAT32 (LBA)", "0x0b": "FAT32", "0x83": "리눅스", "0x82": "리눅스 스왑",
    "0x05": "확장 파티션", "0x0f": "확장 파티션 (LBA)", "0xef": "EFI 시스템 파티션", "0x8e": "리눅스 LVM"})


def _s(v):
    """'ay' (끝에 0 이 붙은 바이트 목록) → 문자열"""
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    elif isinstance(v, (list, tuple)):
        b = bytes(x for x in v if isinstance(x, int))
    else:
        return str(v or "")
    return b.rstrip(b"\0").decode("utf-8", "replace")


class Disk:
    def __init__(self):
        self.path = ""              # udisks 블록 객체 경로
        self.drive = None           # 드라이브 객체 경로 (없으면 None — 루프 장치 등)
        self.dev = ""               # /dev/sda
        self.size = 0
        self.index = 0              # 화면의 "디스크 N"
        self.kind = "하드 디스크"
        self.model = ""
        self.serial = ""
        self.bus = ""
        self.removable = False      # 꺼낼 수 있는 매체 (USB 메모리 · 메모리 카드) — 윈도우의 "이동식 디스크"
        self.detachable = False     # 뗄 수 있는 장치 (USB 외장 디스크 포함) — "꺼내기"를 보인다
        self.ejectable = False
        self.can_poweroff = False
        self.table = None           # "gpt" · "dos" · None (파티션 표 없음)
        self.ro = False
        self.system = False         # SekaiOS 가 설치된 디스크
        self.live = False           # 지금 켜진 설치 USB
        self.segments = []          # [Volume] — 위치 순서 (빈 공간 포함)
        self.whole = None           # 파티션 표 없이 디스크 전체가 볼륨이면 그 Volume
        self.image = ""             # 디스크 이미지(루프 장치)면 그 파일 경로 — 윈도우의 "VHD 연결"
        self.health = ""            # SMART — "정상" · "주의: …" · "" (모름)
        self.health_bad = False
        self.temp = None            # °C
        self.power_on_hours = None

    @property
    def title(self):
        return f"디스크 {self.index}"

    @property
    def label(self):
        """모델 이름 · 종류 (디스크 이미지면 파일 이름)"""
        if self.image:
            return os.path.basename(self.image)
        return self.model or self.kind

    @property
    def state(self):
        if self.live:
            return "설치 USB"
        if self.table is None and self.whole is None:
            return "초기화되지 않음"
        return "온라인"


class Volume:
    def __init__(self, disk):
        self.disk = disk
        self.path = ""              # 블록 객체 경로 (빈 공간이면 "")
        self.dev = ""
        self.free = False           # 할당되지 않은 공간
        self.offset = 0
        self.size = 0
        self.number = 0
        self.fs = ""                # IdType
        self.usage = ""             # IdUsage (filesystem · crypto · other …)
        self.label = ""
        self.uuid = ""
        self.ptype = ""             # 파티션 종류 (GPT GUID · MBR 0x..)
        self.pname = ""             # GPT 파티션 이름
        self.mounts = []
        self.has_fs = False         # Filesystem 인터페이스가 있다 (연결·해제·레이블·검사)
        self.swap_on = False
        self.container = False      # MBR 확장 파티션
        self.encrypted = False
        self.unlocked = None        # 잠금 푼 볼륨의 블록 경로
        self.used = None            # 연결돼 있을 때 쓴 바이트 (statvfs)
        self.avail = None
        self.protect = ""           # 포맷·삭제를 막는 이유 (빈 글자면 막지 않음)
        self.logical = False        # MBR 확장 파티션 안의 논리 볼륨
        self.next_free = None       # 바로 뒤에 붙은 할당되지 않은 공간 (볼륨 확장은 그리로만 — 윈도우처럼)
        self.fstab = None           # 고정 연결 위치 — {"dir", "opts", "boot", "item"} (드라이브 문자 대신)

    @property
    def mount(self):
        """대표 연결 위치 — btrfs 는 한 파티션을 하위 볼륨마다 여러 곳에 연결한다(/ · /home · /var/log …).
        udisks 가 주는 순서는 정해져 있지 않아 첫 것을 쓰면 시스템 파티션이 "로컬 디스크 (/home)" 로 보였다 →
        / 가 있으면 /, 아니면 가장 짧은 경로"""
        if not self.mounts:
            return ""
        if "/" in self.mounts:
            return "/"
        return min(self.mounts, key=lambda m: (m.count("/"), len(m)))

    @property
    def fs_name(self):
        if self.free:
            return ""
        return FS_NAMES.get(self.fs, self.fs or ("" if not self.usage else self.usage))

    @property
    def name(self):
        """윈도우의 "로컬 디스크 (C:)" 처럼 — 레이블, 없으면 쓰임새"""
        if self.free:
            return "할당되지 않음"
        if self.label:
            return self.label
        special = GPT_NAMES.get(self.ptype.lower())
        if special:
            return special
        if self.mount == "/":
            return "SekaiOS"
        if self.fs == "swap":
            return "스왑"
        if self.encrypted:
            return "암호화된 볼륨"
        return "이동식 디스크" if self.disk.removable else "로컬 디스크"

    @property
    def status(self):
        if self.free:
            return "할당되지 않음"
        bits = []
        if self.mount == "/":
            bits.append("시스템")
        elif self.mount in ("/boot/efi", "/efi") or self.ptype.lower() == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b":
            bits.append("EFI 시스템")
        elif self.mount == "/boot":
            bits.append("부팅")
        if self.swap_on:
            bits.append("스왑 사용 중")
        if self.encrypted:
            bits.append("잠금 해제됨" if self.unlocked else "잠김")
        if not self.fs and not self.encrypted and not self.container:
            bits.append("포맷 안 됨")
        return "정상" + (f" ({', '.join(bits)})" if bits else "")


def drive_kind(dr):
    """드라이브 속성 → 보이는 종류 (모델 이름이 없거나 "0x1af4" 같은 번호뿐일 때 대신 쓴다)"""
    bus = (dr.get("ConnectionBus") or "").lower()
    media = (dr.get("Media") or "").lower()
    if dr.get("Optical") or media.startswith("optical"):
        return "CD/DVD 드라이브"
    if media.startswith("flash_sd") or media.startswith("flash_mmc") or "sd" == media:
        return "메모리 카드"
    if bus == "usb":
        # udisks 는 USB 디스크를 모두 Removable(뗄 수 있는 장치)로 본다 — 매체가 빠지는 것(USB 메모리)만 이동식
        return "USB 드라이브" if dr.get("MediaRemovable") else "외장 디스크"
    rot = dr.get("RotationRate", -1)
    if rot == 0:
        return "SSD"
    if rot and rot > 0:
        return "하드 디스크"
    return "디스크"


def drive_model(dr):
    """사람이 읽을 모델 이름 — 비었거나 장치 번호(0x1af4)뿐이면 빈 글자"""
    name = " ".join(x for x in ((dr.get("Vendor") or "").strip(), (dr.get("Model") or "").strip()) if x)
    if not name or re.fullmatch(r"(0x[0-9a-f]+\s*)+", name, re.I):
        return ""
    return name


def _mounts_now():
    """/proc/self/mountinfo 의 연결 위치 → 장치 (udisks 가 모르는 bind 연결 등 견주기용)"""
    out = {}
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2:
                    out[p[1].replace("\\040", " ")] = p[0]
    except OSError:
        pass
    return out


def _usage(v):
    if not v.mount:
        return
    try:
        st = os.statvfs(v.mount)
    except OSError:
        return
    total = st.f_blocks * st.f_frsize
    v.avail = st.f_bavail * st.f_frsize
    v.used = max(0, total - st.f_bfree * st.f_frsize)


def build(objects):
    """GetManagedObjects → [Disk] (디스크 0 부터). 루프·램 디스크·조각난 장치는 뺀다."""
    blocks = {p: ifs for p, ifs in objects.items() if I_BLOCK in ifs}
    drives = {p: ifs[I_DRIVE] for p, ifs in objects.items() if I_DRIVE in ifs}
    live_devs = set()
    mounts = _mounts_now()
    for m in LIVE_MEDIA:
        if m in mounts:
            live_devs.add(os.path.realpath(mounts[m]))

    # 잠금 푼 암호화 볼륨: 원래 볼륨 경로 → 풀린 블록 경로
    unlocked = {}
    for p, ifs in blocks.items():
        cb = ifs[I_BLOCK].get("CryptoBackingDevice", "/")
        if cb and cb != "/":
            unlocked[cb] = p

    def fill(v, p):
        ifs = blocks[p]
        b = ifs[I_BLOCK]
        v.path = p
        v.dev = _s(b.get("PreferredDevice") or b.get("Device"))
        v.size = b.get("Size", 0)
        v.fs = b.get("IdType", "") or ""
        v.usage = b.get("IdUsage", "") or ""
        v.label = b.get("IdLabel", "") or ""
        v.uuid = b.get("IdUUID", "") or ""
        if I_PART in ifs:
            pt = ifs[I_PART]
            v.offset = pt.get("Offset", 0)
            v.size = pt.get("Size", v.size)
            v.number = pt.get("Number", 0)
            v.ptype = pt.get("Type", "") or ""
            v.pname = pt.get("Name", "") or ""
            v.container = bool(pt.get("IsContainer"))
        if I_FS in ifs:
            v.has_fs = True
            v.mounts = [_s(m) for m in ifs[I_FS].get("MountPoints", [])]
        v.fstab = _fstab_item(b)
        if I_SWAP in ifs:
            v.swap_on = bool(ifs[I_SWAP].get("Active"))
        if I_CRYPT in ifs or v.usage == "crypto":
            v.encrypted = True
            cp = unlocked.get(p)
            if cp:
                v.unlocked = cp
                cfs = blocks.get(cp, {})
                if I_FS in cfs:     # 풀린 쪽의 파일 시스템을 이 볼륨의 것으로 보여 준다
                    v.mounts = [_s(m) for m in cfs[I_FS].get("MountPoints", [])]
                    inner = cfs[I_BLOCK]
                    v.label = v.label or inner.get("IdLabel", "")
                    v.fstab = v.fstab or _fstab_item(inner)
        _usage(v)

    disks = []
    uid = os.getuid()
    for p, ifs in blocks.items():
        b = ifs[I_BLOCK]
        if I_PART in ifs:
            continue
        image = ""
        if I_LOOP in ifs:
            # 이 사용자가 연결한 디스크 이미지만 (snap 같은 시스템 루프 장치는 뺀다)
            lp = ifs[I_LOOP]
            if lp.get("SetupByUID") != uid or not _s(lp.get("BackingFile")):
                continue
            image = _s(lp.get("BackingFile"))
        if b.get("CryptoBackingDevice", "/") not in ("", "/"):
            continue                                # 잠금 푼 암호화 볼륨 — 원래 볼륨 쪽에서 보인다
        dev = _s(b.get("Device"))
        if re.match(r"/dev/(zram|ram|dm-|md)", dev) or (not image and dev.startswith("/dev/loop")) \
                or b.get("Size", 0) == 0:
            continue
        drv = b.get("Drive", "/")
        dr = drives.get(drv, {})
        d = Disk()
        d.path, d.drive, d.dev = p, (drv if drv != "/" else None), dev
        d.size = b.get("Size", 0)
        d.model = drive_model(dr)
        d.kind = drive_kind(dr)
        d.serial = dr.get("Serial", "") or ""
        d.bus = dr.get("ConnectionBus", "") or ""
        d.removable = bool(dr.get("MediaRemovable"))
        d.detachable = bool(dr.get("Removable") or dr.get("Ejectable") or dr.get("CanPowerOff")) and d.bus in ("usb", "ieee1394", "sdio")
        d.ejectable = bool(dr.get("Ejectable"))
        d.can_poweroff = bool(dr.get("CanPowerOff"))
        d.ro = bool(b.get("ReadOnly"))
        d.image = image
        if image:
            d.kind = "디스크 이미지"
        if dr.get("Optical"):
            continue                                # CD/DVD 는 디스크 관리에서 다루지 않는다 (탐색기에서)
        _health(d, objects.get(drv, {}))
        if I_TABLE in ifs:
            d.table = ifs[I_TABLE].get("Type") or "gpt"
            parts = []
            for pp in ifs[I_TABLE].get("Partitions", []):
                if pp in blocks:
                    v = Volume(d)
                    fill(v, pp)
                    parts.append(v)
            parts.sort(key=lambda v: v.offset)
            d.segments = _with_free(d, parts)
        elif b.get("IdUsage") or I_FS in ifs:
            v = Volume(d)                           # 파티션 표 없이 디스크 전체가 볼륨 (일부 USB 메모리)
            fill(v, p)
            v.offset = 0
            d.whole = v
            d.segments = [v]
        else:
            d.segments = [_free(d, 0, d.size)] if d.size else []
        d.live = os.path.realpath(dev) in live_devs or any(
            os.path.realpath(v.dev) in live_devs for v in d.segments if v.dev)
        disks.append(d)

    for d in disks:
        for v in d.segments:
            if any(m in SYSTEM_MOUNTS for m in v.mounts):
                d.system = True
        for i, v in enumerate(d.segments):
            v.protect = _protect_reason(d, v)
            nxt = d.segments[i + 1] if i + 1 < len(d.segments) else None
            if not v.free and not v.logical and nxt is not None and nxt.free and not nxt.logical:
                v.next_free = nxt
    # 시스템 디스크가 먼저 (윈도우의 디스크 0 = 윈도우가 깔린 디스크인 경우가 많은 것처럼), 그다음 장치 이름 순
    disks.sort(key=lambda d: (not d.system, bool(d.image), d.detachable, d.dev))
    for i, d in enumerate(disks):
        d.index = i
    return disks


def _free(d, off, size):
    v = Volume(d)
    v.free = True
    v.offset, v.size = off, size
    return v


def _with_free(d, parts):
    """파티션 사이·앞뒤의 빈 공간을 끼워 넣는다 (확장 파티션 안의 논리 파티션은 그 안에 든 것으로)"""
    out = []
    top = [v for v in parts if not _inside_container(v, parts)]
    start = MIB                                     # 첫 1MiB 는 파티션 표와 정렬 자리
    end = d.size - (MIB if d.table == "gpt" else 0)  # GPT 는 끝에 예비 표가 있다
    pos = start
    for v in top:
        if v.offset - pos >= MIN_FREE:
            out.append(_free(d, pos, v.offset - pos))
        if v.container:
            for x in parts:
                if x is not v and _inside_container(x, [v]):
                    x.logical = True
                    out.append(x)
        else:
            out.append(v)
        pos = max(pos, v.offset + v.size)
    if end - pos >= MIN_FREE:
        out.append(_free(d, pos, end - pos))
    return out


def _inside_container(v, parts):
    for c in parts:
        if c is not v and c.container and c.offset <= v.offset < c.offset + c.size:
            return True
    return False


def _protect_reason(d, v):
    if v.free:
        return ""
    if d.live:
        return "지금 켜져 있는 설치 USB 입니다"
    if any(m in SYSTEM_MOUNTS for m in v.mounts):
        return "SekaiOS 가 쓰고 있는 볼륨입니다"
    if v.swap_on:
        return "SekaiOS 가 스왑(가상 메모리)으로 쓰고 있습니다"
    if v.encrypted and v.unlocked and any(m in SYSTEM_MOUNTS for m in v.mounts):
        return "SekaiOS 가 쓰고 있는 볼륨입니다"
    if d.system and v.ptype.lower() == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b":
        return "SekaiOS 를 부팅하는 EFI 시스템 파티션입니다"
    return ""


def disk_protect(d):
    """디스크 전체를 지우는 일(초기화)을 막는 이유"""
    if d.live:
        return "지금 켜져 있는 설치 USB 입니다"
    if d.system:
        return "SekaiOS 가 설치된 디스크입니다"
    if any(v.mounts or v.swap_on for v in d.segments):
        return "연결된 볼륨이 있습니다 — 먼저 연결을 해제하세요"
    return ""


def fmt_size(n):
    if n is None:
        return ""
    for unit, k in (("TB", 1000 ** 4), ("GB", 1000 ** 3), ("MB", 1000 ** 2), ("KB", 1000)):
        if n >= k:
            x = n / k
            return f"{x:.1f} {unit}".replace(".0 ", " ") if x < 100 else f"{x:.0f} {unit}"
    return f"{n} B"


def _fstab_item(b):
    """Block 의 Configuration 에서 fstab 항목 → {"dir", "opts", "boot", "item"} 또는 None"""
    for typ, d in b.get("Configuration", []) or []:
        if typ != "fstab":
            continue
        opts = _s(d.get("opts", b""))
        return {"dir": _s(d.get("dir", b"")), "opts": opts,
                "boot": "noauto" not in opts.split(","), "item": (typ, d)}
    return None


def _health(d, ifs):
    """SMART (SATA · NVMe) → d.health · d.temp · d.power_on_hours. 모르면 그대로 둔다"""
    ata, nv = ifs.get(I_ATA), ifs.get(I_NVME)
    if ata and ata.get("SmartSupported") and ata.get("SmartEnabled") and ata.get("SmartUpdated", 0):
        bad = []
        if ata.get("SmartFailing"):
            bad.append("곧 고장 날 수 있습니다 (제조사 기준을 넘었습니다)")
        if ata.get("SmartNumBadSectors", 0) > 0:
            bad.append(f"불량 섹터 {ata['SmartNumBadSectors']}개")
        if ata.get("SmartNumAttributesFailing", 0) > 0:
            bad.append(f"기준을 넘은 항목 {ata['SmartNumAttributesFailing']}개")
        d.health_bad = bool(bad)
        d.health = ("주의: " + ", ".join(bad)) if bad else "정상"
        k = ata.get("SmartTemperature", 0)
        d.temp = round(k - 273.15) if k else None
        sec = ata.get("SmartPowerOnSeconds", 0)
        d.power_on_hours = sec // 3600 if sec else None
    elif nv and nv.get("SmartUpdated", 0):
        warn = [w for w in nv.get("SmartCriticalWarning", []) or []]
        names = {"spare": "예비 공간 부족", "temperature": "온도", "degraded": "신뢰성 저하",
                 "readonly": "읽기 전용으로 바뀜", "volatile_mem": "백업 메모리", "pmr_readonly": "읽기 전용"}
        d.health_bad = bool(warn)
        d.health = ("주의: " + ", ".join(names.get(w, w) for w in warn)) if warn else "정상"
        k = nv.get("SmartTemperature", 0)
        d.temp = round(k - 273.15) if k else None
        d.power_on_hours = nv.get("SmartPowerOnHours") or None


def shrink_min(v):
    """줄일 수 있는 가장 작은 크기 (바이트, MiB 단위로 올림) — 쓴 공간 + 여유. 연결돼 있어야 안다 (statvfs)"""
    if v.used is None:
        return None
    need = v.used * 1.05 + v.size * 0.02 + 128 * MIB     # 메타데이터(아이노드 표 등)와 여유
    need = min(int(need), v.size)
    return -(-need // MIB) * MIB


FIXED_DIR_BAD = ("/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/proc", "/root", "/run",
                 "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/opt", "/mnt", "/media")


def fixed_dir_problem(path, home):
    """고정 연결 위치로 쓸 수 없으면 그 이유 (쓸 수 있으면 빈 글자)"""
    p = os.path.normpath(path or "")
    if not p.startswith("/"):
        return "전체 경로를 적어 주세요 (예: /mnt/data)"
    if p in FIXED_DIR_BAD or p == os.path.normpath(home) or any(
            p.startswith(b + "/") for b in ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/run", "/sbin",
                                             "/sys", "/usr", "/var")):
        return "SekaiOS 가 쓰는 폴더입니다 — /mnt 아래나 홈 폴더 안의 새 폴더를 골라 주세요"
    if any(c in p for c in " \t\n#\\"):
        return "경로에 빈칸·#·\\ 를 쓸 수 없습니다"
    if os.path.isdir(p):
        try:
            if os.listdir(p):
                return "비어 있지 않은 폴더입니다 — 연결하면 그 안의 파일이 가려집니다"
        except OSError:
            pass
    elif os.path.exists(p):
        return "같은 이름의 파일이 있습니다"
    return ""
