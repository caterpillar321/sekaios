"""장치 관리자 — 장치 정보 모으기 (GTK 없음 · 작업 스레드에서 부른다).

표준 자료만 읽는다:
  /sys             장치 나무, 드라이버 연결(driver 링크), 클래스 장치(net·sound·drm·input·block·rfkill …)
  /run/udev/data   udev 데이터베이스 — hwdb 가 붙인 이름(ID_VENDOR_FROM_DATABASE·ID_MODEL_FROM_DATABASE)과
                   입력 장치 종류(ID_INPUT_KEYBOARD …). libudev 와 같은 파일 이름 규칙으로 바로 읽는다
                   (장치마다 udevadm 을 띄우지 않게)
  pci.ids·usb.ids  hwdb 보다 새 이름이 들어 있을 수 있어 먼저 본다 (없으면 hwdb 만)
  /lib/modules     모듈 파일·버전, 별칭(드라이버 없는 장치에 맞는 모듈이 이 커널에 있는지)
  journalctl -k    펌웨어 불러오기 성공·실패 — 권한이 없으면 dmesg, 그것도 안 되면 건너뛴다
  /proc            cpuinfo, asound (소리 끝점)

시험: SEKAI_DEVROOT=<가짜 루트> 이면 그 아래의 sys·proc·run/udev/data·lib/modules·etc/modprobe.d·usr/share 를 읽고,
      커널 기록은 <가짜 루트>/journal.json (journalctl -o json 한 줄에 하나) 에서 읽는다.
"""
import fnmatch
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import traceback

ROOT = os.environ.get("SEKAI_DEVROOT", "").rstrip("/")
SYS = ROOT + "/sys"
PROC = ROOT + "/proc"
UDEV_DATA = ROOT + "/run/udev/data"
DEVICES = SYS + "/devices"
VIRT = SYS + "/devices/virtual"
RELEASE = os.uname().release
ENV_C = dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8")

# ── 종류 (윈도우 장치 관리자의 분류, 가나다순 — 영문으로 시작하는 것은 뒤) ─────
CATEGORIES = [
    ("other", "기타 장치", ["dialog-question", "preferences-other", "applications-other"]),
    ("network", "네트워크 어댑터", ["network-wired", "network-card"]),
    ("disk", "디스크 드라이브", ["drive-harddisk"]),
    ("display", "디스플레이 어댑터", ["video-display", "preferences-desktop-display"]),
    ("mouse", "마우스 및 기타 포인팅 장치", ["input-mouse"]),
    ("memtech", "메모리 기술 장치", ["media-flash", "media-memory-sd"]),
    ("monitor", "모니터", ["video-display", "display"]),
    ("battery", "배터리", ["battery", "battery-full"]),
    ("security", "보안 장치", ["security-high", "channel-secure"]),
    ("bluetooth", "블루투스", ["bluetooth", "bluetooth-active"]),
    ("sound", "사운드, 비디오 및 게임 컨트롤러", ["audio-card"]),
    ("biometric", "생체 인식 장치", ["auth-fingerprint", "fingerprint", "dialog-password"]),
    ("software", "소프트웨어 장치", ["application-x-executable", "applications-system"]),
    ("smartcard", "스마트 카드 판독기", ["media-flash", "dialog-password"]),
    ("system", "시스템 장치", ["computer", "applications-system"]),
    ("audio", "오디오 입력 및 출력", ["audio-speakers", "audio-card"]),
    ("queue", "인쇄 대기열", ["printer"]),
    ("storage", "저장소 컨트롤러", ["drive-multidisk", "drive-harddisk-system", "drive-harddisk"]),
    ("camera", "카메라", ["camera-web", "camera-photo"]),
    ("computer", "컴퓨터", ["computer"]),
    ("keyboard", "키보드", ["input-keyboard"]),
    ("ports", "포트(COM 및 LPT)", ["serial-port", "network-wired"]),
    ("processor", "프로세서", ["cpu", "computer"]),
    ("printer", "프린터", ["printer"]),
    ("portable", "휴대용 장치", ["phone", "multimedia-player", "pda"]),
    ("hid", "휴먼 인터페이스 장치(HID)", ["preferences-desktop-peripherals", "input-gaming", "input-tablet"]),
    ("cdrom", "DVD/CD-ROM 드라이브", ["drive-optical", "media-optical"]),
    ("usb", "USB 컨트롤러", ["device_usb", "drive-removable-media-usb"]),
]
CAT_TITLE = {c: t for c, t, _i in CATEGORIES}
CAT_ICON = {c: i for c, _t, i in CATEGORIES}
CAT_ORDER = {c: n for n, (c, _t, _i) in enumerate(CATEGORIES)}

# 종류별로 설정 앱의 어느 페이지를 여는지 (sekai-settings --page=…)
CAT_ACTIONS = {"display": ["graphics"], "monitor": ["display"], "network": ["network"],
               "bluetooth": ["bluetooth"], "printer": ["printers"], "queue": ["printers"],
               "sound": ["sound"], "audio": ["sound"], "keyboard": ["input"], "mouse": ["input"],
               "battery": ["power"]}


# ── 파일 읽기 ────────────────────────────────────────────────
def rd(path, default=""):
    try:
        with open(path, "rb") as f:
            return f.read(1 << 16).decode("utf-8", "replace").strip()
    except OSError:
        return default


def rd_all(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def rd_bytes(path, limit=1 << 16):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""


def lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def link(path):
    try:
        return os.path.basename(os.readlink(path))
    except OSError:
        return ""


def real(path):
    return os.path.realpath(path)


def ls(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def hexint(s, default=None):
    try:
        return int(s, 16)
    except (TypeError, ValueError):
        return default


def num(s, default=None):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def shown(path):
    """화면에 보일 경로 — 시험용 가짜 루트는 뗀다"""
    return path[len(ROOT):] if ROOT and path.startswith(ROOT) else path


def fmt_size(n):
    """윈도우처럼 1024 단위에 GB 표기 (1 TB 디스크 → 931.5 GB)"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def udev_props(path, subsystem=None):
    """udev 데이터베이스의 E: 값들. 파일 이름은 libudev 규칙 —
    문자·블록 장치는 c/b<주:부>, 네트워크는 n<ifindex>, 나머지는 +<subsystem>:<sysname>"""
    subsystem = subsystem or link(path + "/subsystem")
    devnum = rd(path + "/dev")
    if devnum:
        name = ("b" if subsystem == "block" else "c") + devnum
    elif subsystem == "net":
        name = "n" + rd(path + "/ifindex")
    else:
        name = f"+{subsystem}:{os.path.basename(path)}"
    out = {}
    for line in lines(os.path.join(UDEV_DATA, name)):
        if line.startswith("E:"):
            k, _, v = line[2:].partition("=")
            out[k] = v
    return out


# ── 이름표 (pci.ids · usb.ids) ───────────────────────────────
ID_FILES = {"pci": ("/usr/share/misc/pci.ids", "/usr/share/hwdata/pci.ids"),
            "usb": ("/usr/share/misc/usb.ids", "/usr/share/hwdata/usb.ids", "/var/lib/usbutils/usb.ids")}
_VENDOR_LINE = re.compile(r"^[0-9a-fA-F]{4}  ")


class IdFiles:
    """필요한 ID 만 파일에서 한 번에 찾아 기억한다 (1 MB 넘는 파일을 통째로 들고 있지 않게)"""

    def __init__(self):
        self.cache = {"pci": {}, "usb": {}}
        self.asked = {"pci": set(), "usb": set()}

    def want(self, bus, keys):
        keys = {k for k in keys if k not in self.asked[bus]}
        if not keys:
            return
        self.asked[bus] |= keys
        path = next((ROOT + p for p in ID_FILES[bus] if os.path.exists(ROOT + p)), None)
        if path:
            try:
                self._load(path, self.cache[bus], keys)
            except OSError:
                pass

    @staticmethod
    def _load(path, c, keys):
        vens = {k[0] for k in keys}
        devs = {k[:2] for k in keys}
        subs = {k for k in keys if len(k) == 4 and k[2]}
        cur_v = cur_d = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip() or line[0] == "#":
                    continue
                if line[0] != "\t":
                    if not _VENDOR_LINE.match(line):
                        break                      # 제조사 목록이 끝나고 클래스 목록(C …)이 시작된다
                    v = line[:4].lower()
                    cur_v = v if v in vens else None
                    cur_d = None
                    if cur_v:
                        c[(v,)] = line[6:].strip()
                elif cur_v:
                    if line[1] != "\t":
                        d = line[1:5].lower()
                        cur_d = d if (cur_v, d) in devs else None
                        if cur_d:
                            c[(cur_v, d)] = line[7:].strip()
                    elif cur_d:
                        parts = line[2:].split(None, 2)
                        if len(parts) == 3:
                            k = (cur_v, cur_d, parts[0].lower(), parts[1].lower())
                            if k in subs:
                                c[k] = parts[2].strip()

    def get(self, bus, *key):
        return self.cache[bus].get(tuple(key), "")


# 짧은 제조사 이름 — 이름표의 긴 법인 이름 대신 (윈도우 장치 이름처럼 "Intel Wi-Fi 6E …")
VENDOR_SHORT = {
    "pci": {"8086": "Intel", "10de": "NVIDIA", "1002": "AMD", "1022": "AMD", "14c3": "MediaTek",
            "14e4": "Broadcom", "10ec": "Realtek", "168c": "Qualcomm Atheros", "17cb": "Qualcomm",
            "1af4": "VirtIO", "1b36": "QEMU", "1234": "QEMU", "15ad": "VMware", "1414": "Microsoft",
            "80ee": "VirtualBox", "1b21": "ASMedia", "1c5c": "SK hynix", "144d": "Samsung", "c0a9": "Micron",
            "1344": "Micron", "1e0f": "KIOXIA", "15b7": "SanDisk", "2646": "Kingston", "126f": "Silicon Motion",
            "1987": "Phison", "1cc1": "ADATA", "1b4b": "Marvell", "11ab": "Marvell", "1106": "VIA",
            "1912": "Renesas", "1217": "O2 Micro", "1180": "Ricoh", "1179": "Toshiba", "1bb1": "Seagate",
            "1d6a": "Aquantia", "1969": "Qualcomm Atheros", "106b": "Apple", "5853": "Xen"},
    "usb": {"8087": "Intel", "0bda": "Realtek", "0e8d": "MediaTek", "0cf3": "Qualcomm Atheros",
            "0a5c": "Broadcom", "1d6b": "Linux", "046d": "Logitech", "045e": "Microsoft", "05ac": "Apple",
            "04e8": "Samsung", "0781": "SanDisk", "0951": "Kingston", "13d3": "AzureWave", "0489": "Foxconn",
            "1532": "Razer", "1b1c": "Corsair", "3434": "Keychron", "28de": "Valve", "054c": "Sony",
            "057e": "Nintendo", "0b05": "ASUS", "17ef": "Lenovo", "03f0": "HP", "413c": "Dell",
            "04a9": "Canon", "04b8": "Epson", "04f9": "Brother", "27c6": "Goodix", "06cb": "Synaptics",
            "138a": "Validity", "2357": "TP-Link", "0b95": "ASIX", "1a86": "QinHeng", "0403": "FTDI",
            "10c4": "Silicon Labs", "067b": "Prolific", "05e3": "Genesys Logic", "2109": "VIA Labs",
            "0e0f": "VMware", "0627": "QEMU", "18d1": "Google", "1038": "SteelSeries", "0c45": "Microdia",
            "04f2": "Chicony", "5986": "Bison", "0424": "Microchip", "148f": "MediaTek (Ralink)",
            "2001": "D-Link", "0846": "NETGEAR", "7392": "Edimax"},
}
_CORP = re.compile(r"[,.]?\s+(?:inc\.?|incorporated|corp\.?|corporation|co\.?,?\s*ltd\.?|co\.?|ltd\.?|limited|"
                   r"gmbh|ag|s\.a\.?|b\.v\.?|llc|plc|technology|technologies|semiconductor|electronics?|"
                   r"international|systems|computer|company|group|holdings?)$", re.I)


def short_vendor(name):
    if not name:
        return ""
    m = re.search(r"\[([^\]]+)\]\s*$", name)       # "Advanced Micro Devices, Inc. [AMD/ATI]" → AMD
    if m:
        return m.group(1).split("/")[0].strip()
    prev = None
    while prev != name:
        prev = name
        name = _CORP.sub("", name).strip(" ,.")
    return name


def vendor_of(bus, vid, full):
    return VENDOR_SHORT.get(bus, {}).get(vid) or short_vendor(full)


def display_name(vendor, model):
    if not model:
        return vendor
    if not vendor or model.casefold().startswith(vendor.casefold()):
        return model
    return f"{vendor} {model}"


def product_bracket(name):
    """GPU 이름표는 "칩 이름 [제품 이름]" — 사람이 아는 제품 이름만 ("GB206 [GeForce RTX 5060 Ti]")"""
    m = re.fullmatch(r"([^\[\]()]*?)\s*\[([^\]]+)\]", name or "")
    return m.group(2) if m else name


# ── PCI ──────────────────────────────────────────────────────
PCI_BASE_KO = {0x00: "분류되지 않은 장치", 0x01: "저장소 컨트롤러", 0x02: "네트워크 컨트롤러", 0x03: "디스플레이 컨트롤러",
               0x04: "멀티미디어 컨트롤러", 0x05: "메모리 컨트롤러", 0x06: "브리지", 0x07: "통신 컨트롤러",
               0x08: "시스템 주변 장치", 0x09: "입력 장치 컨트롤러", 0x0a: "도킹 스테이션", 0x0b: "프로세서",
               0x0c: "직렬 버스 컨트롤러", 0x0d: "무선 컨트롤러", 0x0e: "지능형 I/O 컨트롤러",
               0x0f: "위성 통신 컨트롤러", 0x10: "암호화 컨트롤러", 0x11: "신호 처리 컨트롤러",
               0x12: "처리 가속기", 0x13: "비필수 계측 장치", 0x40: "보조 프로세서", 0xff: "지정되지 않은 장치"}
PCI_SUB_KO = {0x0100: "SCSI 저장소 컨트롤러", 0x0101: "IDE 컨트롤러", 0x0104: "RAID 컨트롤러", 0x0105: "ATA 컨트롤러",
              0x0106: "SATA 컨트롤러", 0x0107: "SAS 컨트롤러", 0x0108: "NVMe 컨트롤러", 0x0180: "저장소 컨트롤러",
              0x0200: "이더넷 컨트롤러", 0x0280: "네트워크 컨트롤러", 0x0300: "VGA 호환 디스플레이 컨트롤러",
              0x0302: "3D 컨트롤러", 0x0380: "디스플레이 컨트롤러", 0x0400: "비디오 장치", 0x0401: "오디오 장치",
              0x0403: "HD 오디오 컨트롤러", 0x0480: "멀티미디어 컨트롤러", 0x0600: "호스트 브리지",
              0x0601: "ISA 브리지", 0x0604: "PCI 브리지", 0x0700: "직렬 포트 컨트롤러", 0x0805: "SD 호스트 컨트롤러",
              0x0806: "IOMMU", 0x0880: "시스템 주변 장치", 0x0c00: "FireWire(IEEE 1394) 컨트롤러",
              0x0c03: "USB 컨트롤러", 0x0c05: "SMBus 컨트롤러", 0x0c80: "직렬 버스 컨트롤러",
              0x0d11: "블루투스 컨트롤러", 0x0d80: "무선 컨트롤러", 0x1080: "암호화 컨트롤러",
              0x1180: "신호 처리 컨트롤러", 0x1200: "처리 가속기", 0x1300: "비필수 계측 장치"}
USB_HCI_KO = {0x00: "UHCI (USB 1.1)", 0x10: "OHCI (USB 1.1)", 0x20: "EHCI (USB 2.0)", 0x30: "xHCI (USB 3.x)",
              0x40: "USB4"}
WIFI_RE = re.compile(r"wi-?fi|wireless|802\.11|wlan|\b(?:AX|BE|AC)\d", re.I)
CAMERA_RE = re.compile(r"camera|webcam|imaging signal processor|\bipu\d*\b|image processing unit", re.I)
CARDREADER_RE = re.compile(r"card ?reader|sd host|sd/mmc|mmc host|sdxc|memory stick", re.I)


def pci_kind(cls):
    k = PCI_SUB_KO.get(cls >> 8) or PCI_BASE_KO.get(cls >> 16, "PCI 장치")
    if cls >> 8 == 0x0c03:
        k = f"USB 컨트롤러 — {USB_HCI_KO.get(cls & 0xff, 'USB')}"
    return k


def pci_category(cls, text):
    """(종류, 드라이버가 꼭 있어야 하는지) — 없으면 쓸 수 없는 종류만 ⚠ 로 알린다.
    SMBus·신호 처리·계측 같은 장치는 드라이버가 없어도 컴퓨터가 멀쩡한 경우가 흔하다"""
    base, sub = cls >> 16, (cls >> 8) & 0xff
    if base == 0x03:
        return "display", True
    if base == 0x02:
        return "network", True
    if base == 0x0d:
        return ("bluetooth" if sub == 0x11 else "network"), True
    if base == 0x04:
        if CAMERA_RE.search(text):
            return "camera", True
        # 기타 멀티미디어(0x0480) — AMD 오디오 보조 프로세서(ACP)처럼 디지털 마이크가 없으면 일부러 비워 두는 장치가 있다
        return "sound", sub != 0x80
    if base == 0x01:
        return "storage", True
    if base == 0x0c and sub == 0x03:
        return "usb", True
    if (base == 0x08 and sub == 0x05) or CARDREADER_RE.search(text):
        return "memtech", True
    if base == 0x07:
        return "ports", False
    if base in (0x00, 0xff):
        return "other", False
    return "system", False


def pci_location(n):
    m = re.match(r"^([0-9a-f]+):([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])$", n, re.I)
    if not m:
        return n
    dom, bus, dev, fn = (int(x, 16) for x in m.groups())
    s = f"PCI 버스 {bus}, 장치 {dev}, 기능 {fn}"
    return (f"PCI 도메인 {dom:x}, " if dom else "") + s + f" ({n})"


_PCIE_GEN = {"2.5": "1.0", "5.0": "2.0", "8.0": "3.0", "16.0": "4.0", "32.0": "5.0", "64.0": "6.0"}
_POWER_KO = {"D0": "켜짐 (D0)", "D1": "절전 (D1)", "D2": "절전 (D2)", "D3hot": "절전 (D3hot)",
             "D3cold": "절전·전원 끔 (D3cold)", "unknown": ""}


def pci_resources(p):
    out = []
    cs, cw = rd(p + "/current_link_speed"), rd(p + "/current_link_width")
    if cs and not cs.lower().startswith("unknown") and cw not in ("", "0"):
        gen = _PCIE_GEN.get(cs.split()[0], cs.split()[0] + " GT/s")
        s = f"PCIe {gen} x{cw}"
        ms, mw = rd(p + "/max_link_speed"), rd(p + "/max_link_width")
        if ms and mw and (ms != cs or mw != cw) and not ms.lower().startswith("unknown"):
            s += f" (최대 PCIe {_PCIE_GEN.get(ms.split()[0], ms.split()[0])} x{mw})"
        out.append(("PCIe 연결", s))
    irq = rd(p + "/irq")
    msi = len(ls(p + "/msi_irqs"))
    if irq not in ("", "0") or msi:
        s = f"IRQ {irq}" if irq not in ("", "0") else ""
        if msi:
            s = (s + " · " if s else "") + f"MSI 인터럽트 {msi}개"
        out.append(("인터럽트", s))
    mem, io = [], []
    for line in lines(p + "/resource")[:7]:
        parts = line.split()
        if len(parts) != 3:
            continue
        start, end, flags = (hexint(x, 0) for x in parts)
        if not start or end <= start:
            continue
        rng = f"{start:#x} – {end:#x}"
        (io if flags & 0x100 else mem).append(rng)
    if mem:
        out.append(("메모리 범위", "\n".join(mem)))
    if io:
        out.append(("I/O 범위", "\n".join(io)))
    ps = rd(p + "/power_state")
    if _POWER_KO.get(ps, ps):
        out.append(("전원 상태", _POWER_KO.get(ps, ps)))
    return out


# ── USB ──────────────────────────────────────────────────────
USB_IF_KO = {0x01: "오디오", 0x02: "통신(CDC)", 0x03: "HID", 0x05: "물리 장치", 0x06: "이미지(MTP/PTP)",
             0x07: "프린터", 0x08: "대용량 저장소", 0x09: "허브", 0x0a: "CDC 데이터", 0x0b: "스마트 카드",
             0x0d: "콘텐츠 보안", 0x0e: "비디오", 0x0f: "개인 건강", 0x10: "오디오/비디오", 0x11: "빌보드",
             0x12: "USB-C 브리지", 0xdc: "진단", 0xe0: "무선 컨트롤러", 0xef: "기타", 0xfe: "특수 용도",
             0xff: "제조사 전용"}
USB_SPEED_KO = {"1.5": "USB 1.x 저속 (1.5 Mbps)", "12": "USB 1.x 전속 (12 Mbps)", "480": "USB 2.0 (480 Mbps)",
                "5000": "USB 3.x (5 Gbps)", "10000": "USB 3.x (10 Gbps)", "20000": "USB 3.2 2x2 (20 Gbps)",
                "40000": "USB4 (40 Gbps)", "80000": "USB4 (80 Gbps)"}
# 제조사 전용(0xff) 장치는 기능을 이름으로 짐작한다 — 드라이버가 없을 때 무엇인지라도 알리게
USB_GUESS = [(re.compile(r"802\.11|wlan|wi-?fi|wireless (?:lan|network)", re.I), "network", "Wi-Fi"),
             (re.compile(r"bluetooth", re.I), "bluetooth", ""),
             (re.compile(r"ethernet|gigabit|\blan\b|10/100", re.I), "network", "이더넷"),
             (re.compile(r"modem|\blte\b|\b5g\b|wwan|mobile broadband", re.I), "network", "모바일 광대역"),
             (re.compile(r"fingerprint|biometric", re.I), "biometric", ""),
             (re.compile(r"webcam|camera", re.I), "camera", "")]


def usb_location(n):
    bus, _, path = n.partition("-")
    return f"USB 버스 {bus}, 포트 {path.replace('.', ' › ')} ({n})" if path else n


def usb_version(v):
    v = (v or "").strip()
    return "USB 3.x" if v.startswith("3") else "USB 2.0" if v.startswith("2") else "USB 1.1" if v else "USB"


# ── ACPI 장치 (윈도우의 "시스템 장치"에 보이던 것들 중 사람이 알아볼 만한 것) ──
_FAN = ("system", "ACPI 팬", False)
_EVF = ("system", "Intel HID 이벤트 필터", False)
ACPI_KNOWN = {
    "PNP0C0C": ("system", "ACPI 전원 단추", False), "LNXPWRBN": ("system", "ACPI 고정 기능 전원 단추", False),
    "PNP0C0E": ("system", "ACPI 절전 단추", False), "LNXSLPBN": ("system", "ACPI 고정 기능 절전 단추", False),
    "PNP0C0D": ("system", "ACPI 덮개 스위치", False),
    "PNP0C0B": _FAN, "INT3404": _FAN, "INTC1044": _FAN, "INTC1048": _FAN, "INTC10A2": _FAN,
    "LNXTHERM": ("system", "ACPI 열 영역", False),
    "PNP0B00": ("system", "시스템 CMOS/실시간 시계", False), "PNP0B01": ("system", "시스템 CMOS/실시간 시계", False),
    "PNP0B02": ("system", "시스템 CMOS/실시간 시계", False),
    "PNP0103": ("system", "고정밀 이벤트 타이머", False), "PNP0C09": ("system", "임베디드 컨트롤러", False),
    "ACPI000E": ("system", "ACPI 시간 및 알람 장치", False), "ACPI0008": ("system", "주변 조도 센서", False),
    "INT33D5": _EVF, "INTC1051": _EVF, "INTC1054": _EVF, "INTC1070": _EVF, "INTC1078": _EVF,
    "PNP0C50": ("hid", "I2C HID 장치", False), "ACPI0C50": ("hid", "I2C HID 장치", False),
    "PNP0C14": ("system", "WMI 인터페이스", True), "PNP0A08": ("system", "PCI Express 루트 컴플렉스", True),
    "PNP0A03": ("system", "PCI 버스", True), "PNP0C01": ("system", "시스템 보드", True),
    "PNP0C02": ("system", "마더보드 리소스", True), "PNP0000": ("system", "프로그래밍 가능한 인터럽트 컨트롤러", True),
    "PNP0100": ("system", "시스템 타이머", True), "PNP0200": ("system", "DMA 컨트롤러", True),
    "PNP0800": ("system", "시스템 스피커", True), "PNP0C04": ("system", "수치 데이터 프로세서", True),
    "LNXVIDEO": ("system", "ACPI 비디오 버스", True), "PNP0C80": ("system", "메모리 장치", True),
}

# 컴퓨터 제조사 (DMI) — 대문자·법인 이름 대신 흔히 부르는 이름
DMI_VENDOR = {"lenovo": "Lenovo", "asustek computer inc.": "ASUS", "micro-star international co., ltd.": "MSI",
              "gigabyte technology co., ltd.": "Gigabyte", "hewlett-packard": "HP", "hp": "HP", "dell inc.": "Dell",
              "samsung electronics co., ltd.": "Samsung", "lg electronics": "LG", "apple inc.": "Apple",
              "microsoft corporation": "Microsoft", "vmware, inc.": "VMware", "innotek gmbh": "VirtualBox",
              "qemu": "QEMU", "acer": "Acer", "toshiba": "Toshiba", "fujitsu": "Fujitsu", "huawei": "Huawei",
              "xiaomi": "Xiaomi", "framework": "Framework", "asrock": "ASRock"}

INPUT_BUS_KO = {"0001": "PCI", "0003": "USB", "0005": "블루투스", "0006": "가상", "0010": "ISA", "0011": "PS/2",
                "0018": "I2C", "0019": "시스템(ACPI)", "001c": "SPI", "001e": "I2C"}

# 펌웨어 파일 → 들어 있을 데비안 패키지 (알려 주기만 한다)
FW_PACKAGES = [("iwlwifi", "firmware-iwlwifi"), ("intel/ibt", "firmware-iwlwifi"),
               ("intel/sof", "firmware-sof-signed"), ("i915/", "firmware-intel-graphics"),
               ("xe/", "firmware-intel-graphics"), ("intel/", "firmware-intel-misc"),
               ("amdgpu/", "firmware-amd-graphics"), ("radeon/", "firmware-amd-graphics"),
               ("nvidia/", "firmware-nvidia-graphics"), ("rtl_nic/", "firmware-realtek"),
               ("rtw88/", "firmware-realtek"), ("rtw89/", "firmware-realtek"), ("rtl_bt/", "firmware-realtek"),
               ("rtlwifi/", "firmware-realtek"), ("mediatek/", "firmware-mediatek"), ("mt76", "firmware-mediatek"),
               ("ath", "firmware-atheros"), ("qca/", "firmware-atheros"), ("brcm/", "firmware-brcm80211"),
               ("cypress/", "firmware-brcm80211"), ("qcom/", "firmware-qcom-soc"), ("", "firmware-misc-nonfree")]


def fw_package(path):
    return next(pkg for pre, pkg in FW_PACKAGES if path.startswith(pre) or (pre and f"/{pre}" in path))


# ── 모듈 ─────────────────────────────────────────────────────
def _norm(m):
    return m.replace("-", "_")


_ALIAS_VEN = re.compile(r"^[a-z0-9_]+:v([0-9A-Fa-f]{4,8})(?=[^0-9A-Fa-f*?\[])")


class Modules:
    """/lib/modules/<커널>/ 의 목록 — 모듈 파일, 내장 여부, 별칭(장치 → 맞는 모듈)"""

    def __init__(self):
        self.dir = next((d for d in (f"{ROOT}/lib/modules/{RELEASE}", f"{ROOT}/usr/lib/modules/{RELEASE}")
                         if os.path.isdir(d)), "")
        self._dep = None
        self._builtin = set()
        self._alias = None

    def _load_dep(self):
        self._dep = {}
        for line in lines(self.dir + "/modules.dep"):
            rel = line.split(":", 1)[0]
            if rel:
                self._dep[_norm(os.path.basename(rel).split(".ko")[0])] = rel
        for line in lines(self.dir + "/modules.builtin"):
            if line.strip():
                self._builtin.add(_norm(os.path.basename(line.strip()).split(".ko")[0]))

    def info(self, mod):
        if self._dep is None:
            self._load_dep()
        n = _norm(mod)
        rel = self._dep.get(n)
        return {"file": shown(os.path.join(self.dir, rel)) if rel else "",
                "rel": rel or "",
                "version": rd(f"{SYS}/module/{n}/version"),
                "taint": rd(f"{SYS}/module/{n}/taint"),
                "builtin": n in self._builtin or (not rel and not os.path.exists(f"{SYS}/module/{n}/initstate"))}

    def loaded(self, mod):
        n = _norm(mod)
        if self._dep is None:
            self._load_dep()
        return os.path.exists(f"{SYS}/module/{n}/initstate") or n in self._builtin

    def _load_alias(self):
        # 제조사 ID 로 먼저 나눠 둔다 — 별칭이 수만 개라 장치마다 전부 맞춰 보면 느리다
        self._alias = {}
        for line in lines(self.dir + "/modules.alias"):
            if not line.startswith("alias "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            pat, mod = parts[1], parts[2]
            prefix = pat.split(":", 1)[0]
            m = _ALIAS_VEN.match(pat)
            key = (prefix, m.group(1).upper()) if m else (prefix, "*")
            self._alias.setdefault(key, []).append((pat, mod))

    def match(self, modalias):
        """이 장치를 맡을 수 있는 모듈 이름들 (modprobe 가 고를 후보)"""
        if not modalias:
            return []
        if self._alias is None:
            self._load_alias()
        prefix = modalias.split(":", 1)[0]
        m = _ALIAS_VEN.match(modalias)
        cands = list(self._alias.get((prefix, "*"), ()))
        if m:
            cands += self._alias.get((prefix, m.group(1).upper()), ())
        out = []
        for pat, mod in cands:
            if mod not in out and fnmatch.fnmatchcase(modalias, pat):
                out.append(mod)
        return out

    @staticmethod
    def blacklist():
        """modprobe 가 자동으로 불러오지 않는 모듈 (blacklist · install … /bin/false · 커널 인자)"""
        out = set()
        for d in ("/etc/modprobe.d", "/run/modprobe.d", "/usr/local/lib/modprobe.d", "/usr/lib/modprobe.d",
                  "/lib/modprobe.d"):
            for f in sorted(glob.glob(ROOT + d + "/*.conf")):
                for line in lines(f):
                    w = line.split("#", 1)[0].split()
                    if len(w) >= 2 and w[0] == "blacklist":
                        out.add(_norm(w[1]))
                    elif len(w) >= 3 and w[0] == "install" and os.path.basename(w[2]) in ("false", "true"):
                        out.add(_norm(w[1]))
        for tok in rd(PROC + "/cmdline").split():
            for key in ("module_blacklist=", "modprobe.blacklist="):
                if tok.startswith(key):
                    out |= {_norm(x) for x in tok[len(key):].split(",") if x}
        return out


# ── 커널 기록 (펌웨어) ───────────────────────────────────────
FW_OK = re.compile(r"firmware: direct-loading firmware (\S+)")            # 데비안 커널의 성공 기록
FW_FAIL = (re.compile(r"firmware: failed to load (\S+?)(?: \(-?\d+\))?\s*$"),
           re.compile(r"Direct firmware load for (\S+) failed with error"),
           re.compile(r"firmware (\S+): fetch failed"),
           re.compile(r"(?:unable|failed) to (?:load|request) (?:\w+ )?firmware(?: file)?:? ['\"]?"
                      r"([\w./+-]+\.(?:bin|fw|ucode|sfi|ddc|pnvm|hcd|dat|img|mbn|elf|signed|zst|xz|sbin))", re.I))
FW_FATAL = re.compile(r"no suitable firmware|(?:failed|unable) to (?:load|request|download|get)\b[^:]*\bfirmware|"
                      r"firmware (?:load(?:ing)?|download|request) failed|fatal error during gpu init|"
                      r"early_init of ip block|no firmware image found", re.I)
_BT_DEV = re.compile(r"^Bluetooth: (hci\d+): ")
_KDEV_TEXT = re.compile(r"^(\S+) (\S+?): ")


class KernelLog:
    """이번 부팅의 커널 기록 중 펌웨어 줄만 (처음 한 번 다 읽고, 그 뒤로는 커서 다음 것만)"""

    def __init__(self):
        self.cursor = None
        self.events = []        # [(_KERNEL_DEVICE 또는 "", 글)]
        self._seen = set()
        self.state = None       # "journal" | "dmesg" | "denied" | "none"

    def update(self):
        try:
            if ROOT:
                self._feed(lines(ROOT + "/journal.json"), reset=True)
                self.state = "journal" if os.path.exists(ROOT + "/journal.json") else "none"
                return
            self._journal()
        except Exception:
            traceback.print_exc()

    def _journal(self):
        exe = shutil.which("journalctl")
        if not exe:
            self._dmesg()
            return
        argv = [exe, "-k", "-b", "0", "-o", "json", "--no-pager", "-q",
                "--output-fields=MESSAGE,_KERNEL_DEVICE"]
        if self.cursor:
            argv.append("--after-cursor=" + self.cursor)
        try:
            r = subprocess.run(argv, capture_output=True, timeout=25, env=ENV_C)
        except (OSError, subprocess.SubprocessError):
            self._dmesg()
            return
        if r.returncode != 0 and self.cursor:        # 기록이 정리돼 커서를 못 찾는다 — 처음부터
            self.cursor, self.events, self._seen = None, [], set()
            self._journal()
            return
        out = r.stdout.splitlines()
        if not out and self.cursor is None:
            # 이번 부팅의 커널 기록이 하나도 안 보인다 = 읽을 권한이 없다 (일반 사용자 · adm 그룹 아님)
            self._dmesg()
            return
        self.state = "journal"
        self._feed(out)

    def _feed(self, raw_lines, reset=False):
        if reset:
            self.events, self._seen = [], set()
        for raw in raw_lines:
            try:
                j = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(j, dict):
                continue
            self.cursor = j.get("__CURSOR") or self.cursor
            msg = j.get("MESSAGE")
            if isinstance(msg, list):                  # 글자가 아닌 바이트가 섞이면 숫자 배열로 온다
                try:
                    msg = bytes(msg).decode("utf-8", "replace")
                except (TypeError, ValueError):
                    continue
            if isinstance(msg, str) and "irmware" in msg:
                kd = j.get("_KERNEL_DEVICE")
                ev = (kd if isinstance(kd, str) else "", msg)
                if ev not in self._seen:               # 같은 줄을 되풀이하는 드라이버가 있어도 목록이 불지 않게
                    self._seen.add(ev)
                    self.events.append(ev)

    def _dmesg(self):
        exe = shutil.which("dmesg")
        try:
            r = subprocess.run([exe, "--notime"], capture_output=True, text=True, timeout=10,
                               env=ENV_C) if exe else None
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is None or r.returncode != 0 or not r.stdout:
            self.state = "denied"                      # dmesg_restrict — 관리자만
            self.events = []
            return
        self.state = "dmesg"
        self.events = list(dict.fromkeys(("", ln) for ln in r.stdout.splitlines() if "irmware" in ln))


def kdev_path(kdev):
    """journald 의 _KERNEL_DEVICE (+pci:0000:00:14.3 · c189:1 · b8:0 · n3) → /sys 경로"""
    if not kdev:
        return None
    t = kdev[0]
    if t == "+":
        sub, _, name = kdev[1:].partition(":")
        for c in (f"{SYS}/bus/{sub}/devices/{name}", f"{SYS}/class/{sub}/{name}"):
            if os.path.exists(c):
                return real(c)
    elif t in "cb":
        c = f"{SYS}/dev/{'char' if t == 'c' else 'block'}/{kdev[1:]}"
        if os.path.exists(c):
            return real(c)
    elif t == "n":
        for n in ls(SYS + "/class/net"):
            if rd(f"{SYS}/class/net/{n}/ifindex") == kdev[1:]:
                return real(f"{SYS}/class/net/{n}")
    return None


def name_path(name):
    """커널 기록 글 앞머리의 장치 이름("iwlwifi 0000:00:14.3: …") → /sys 경로"""
    for base in ("/bus/pci/devices", "/bus/usb/devices", "/class/bluetooth", "/class/net", "/bus/hdaudio/devices",
                 "/bus/i2c/devices", "/bus/platform/devices", "/bus/sdio/devices", "/bus/mmc/devices"):
        c = f"{SYS}{base}/{name}"
        if os.path.exists(c):
            return real(c)
    return None


# ── 모니터 (EDID) ────────────────────────────────────────────
def parse_edid(b):
    if len(b) < 128 or b[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    m = (b[8] << 8) | b[9]
    pnp = "".join(chr(((m >> s) & 0x1f) + 64) for s in (10, 5, 0))
    out = {"pnp": pnp, "code": b[10] | (b[11] << 8), "serial": int.from_bytes(b[12:16], "little"),
           "week": b[16], "year": b[17] + 1990, "wcm": b[21], "hcm": b[22], "name": "", "sn": "", "pref": None}
    for off in (54, 72, 90, 108):
        blk = b[off:off + 18]
        if blk[0] or blk[1]:                           # 해상도 정보 — 처음 것이 기본(권장) 해상도
            if out["pref"] is None:
                ha = blk[2] | ((blk[4] & 0xf0) << 4)
                va = blk[5] | ((blk[7] & 0xf0) << 4)
                htot = ha + (blk[3] | ((blk[4] & 0x0f) << 8))
                vtot = va + (blk[6] | ((blk[7] & 0x0f) << 8))
                clock = (blk[0] | (blk[1] << 8)) * 10000
                out["pref"] = (ha, va, clock / (htot * vtot) if htot and vtot else 0)
            continue
        text = blk[5:18].split(b"\n")[0].decode("cp437", "replace").strip()
        if blk[3] == 0xfc:
            out["name"] = text
        elif blk[3] == 0xff:
            out["sn"] = text
    return out


PNP_COMMON = {"GSM": "LG Electronics", "SAM": "Samsung", "DEL": "Dell", "AUS": "ASUS", "ACR": "Acer",
              "BNQ": "BenQ", "AOC": "AOC", "HWP": "HP", "LEN": "Lenovo", "MSI": "MSI", "GBT": "Gigabyte",
              "PHL": "Philips", "VSC": "ViewSonic", "SNY": "Sony", "APP": "Apple", "BOE": "BOE",
              "AUO": "AU Optronics", "CMN": "Innolux", "SHP": "Sharp", "LGD": "LG Display",
              "SDC": "Samsung Display", "IVM": "iiyama", "XMI": "Xiaomi", "VMW": "VMware"}


class PnpNames:
    """모니터 제조사 세 글자(PNP ID) → 이름 — udev hwdb 원본(20-acpi-vendor.hwdb)이나 hwdata 의 pnp.ids"""

    def __init__(self):
        self._m = None

    def get(self, code):
        if self._m is None:
            self._m = {}
            for p in ("/usr/lib/udev/hwdb.d/20-acpi-vendor.hwdb", "/lib/udev/hwdb.d/20-acpi-vendor.hwdb"):
                key = None
                for line in lines(ROOT + p):
                    mm = re.match(r"^acpi:([A-Z@][A-Z0-9@]{2})\*:$", line)
                    if mm:
                        key = mm.group(1)
                    elif key and line.startswith(" ID_VENDOR_FROM_DATABASE="):
                        self._m.setdefault(key, line.split("=", 1)[1].strip())
                        key = None
                if self._m:
                    break
            for line in lines(ROOT + "/usr/share/hwdata/pnp.ids"):
                k, _, v = line.partition("\t")
                if len(k) == 3 and v:
                    self._m.setdefault(k, v.strip())
        return self._m.get(code) or PNP_COMMON.get(code, "")


# ── 장치 한 줄 ───────────────────────────────────────────────
class Dev:
    """트리의 한 줄. 작업 스레드에서 만들어 화면에 넘긴 뒤에는 바꾸지 않는다."""

    def __init__(self, key, cat, name, sys=None, bus="", kname=""):
        self.key = key
        self.cat = cat
        self.name = (name or "").strip() or "알 수 없는 장치"
        self.sys = sys                 # 실제 /sys 경로 (조상 찾기에 쓴다) — 없으면 None
        self.bus = bus
        self.kname = kname             # 커널 기록에서 이 장치를 찾을 말
        self.vendor = self.model = self.kind = ""
        self.icon = None
        self.suffix = ""               # 트리에서 이름 뒤의 흐린 글 (Wi-Fi · 1.8 TB · NVMe …)
        self.hidden = False
        self.order = None
        self.physical = False          # 드라이버가 붙는 버스 장치 (PCI·USB·HDA·ACPI…)
        self.general, self.ids, self.res, self.extra_drv = [], [], [], []
        self.drivers = []              # [(드라이버, 모듈)]
        self.bound = False
        self.needs_driver = False
        self.fn = {}                   # 이 장치가 만든 것: net · hci · sound · drm · video · input · block · tty
        self.rfkill = []               # [(종류, soft, hard)]
        self.modaliases = []
        self.authorized = True
        self.fw_ok, self.fw_fail, self.fw_fatal = [], [], []
        self.fw_driver = ""            # 커널 기록 앞머리의 드라이버 이름 (드라이버가 떨어져 나간 뒤에도 알게)
        self.level = "ok"              # ok · info · off · warn
        self.state = ""                # 짧은 상태 (드라이버 없음 · 펌웨어 오류 · 꺼짐)
        self.title = "이 장치는 올바르게 작동하고 있습니다."
        self.detail = ""
        self.hints = []
        self.actions = []
        self.parent = None
        self.parent_name = ""
        self.driver_rows = []
        self.search = ""

    def sections(self):
        gen = list(self.general)
        if self.parent_name:
            gen.append(("연결된 곳", self.parent_name))
        out = [("일반", gen), ("드라이버", self.driver_rows), ("장치 ID", self.ids), ("자원", self.res)]
        return [(t, [(k, str(v)) for k, v in rows if v not in (None, "")])
                for t, rows in out if any(v not in (None, "") for _k, v in rows)]

    def as_text(self):
        out = [self.name, f"종류: {CAT_TITLE.get(self.cat, '')}", f"상태: {self.title}"]
        if self.detail:
            out.append(self.detail)
        out += self.hints
        for title, rows in self.sections():
            out.append("")
            out.append(f"[{title}]")
            out += [f"{k}: {v}" for k, v in rows]
        return "\n".join(out)


class Snapshot:
    def __init__(self, devs, klog):
        self.devs = devs
        self.by_key = {d.key: d for d in devs}
        self.klog = klog
        self.problems = [d for d in devs if d.level == "warn"]


# ── 수집 ─────────────────────────────────────────────────────
def _safe(fn):
    """한 부분이 실패해도 나머지는 보여 준다 (sysfs 는 장치·커널마다 모양이 조금씩 다르다)"""
    def run(self, *a):
        try:
            return fn(self, *a)
        except Exception:
            print(f"[sekai-admin] 장치 관리자: {fn.__name__} 수집 실패", file=sys.stderr, flush=True)
            traceback.print_exc()
            return None
    run.__name__ = fn.__name__
    return run


def drivers_upto(path, stop=None, limit=3):
    """path 에서 위로 올라가며 붙은 드라이버들 (입력 장치 → hid-generic → usbhid). stop 경로에 닿으면 멈춘다"""
    out = []
    p = path
    while p and p.startswith(DEVICES + "/") and p != stop and len(out) < limit:
        drv = link(p + "/driver")
        if drv and drv != "usb" and all(drv != o[0] for o in out):
            out.append((drv, link(p + "/driver/module")))
        p = os.path.dirname(p)
    return out


class Collector:
    """캐시(이름표·모듈 목록·커널 기록 커서)를 들고 있는 수집기 — 한 번에 한 작업 스레드만 쓴다"""

    def __init__(self):
        self.ids = IdFiles()
        self.mods = Modules()
        self.klog = KernelLog()
        self.pnp = PnpNames()

    def scan(self):
        return _Scan(self).run()


class _Scan:
    def __init__(self, col):
        self.col = col
        self.ids = col.ids
        self.mods = col.mods
        self.devs = []
        self.by_path = {}

    def run(self):
        for step in (self.pci, self.usb, self.hdaudio, self.acpi, self.attach, self.classify_usb,
                     self.disks, self.inputs, self.monitors, self.audio, self.cpus, self.power, self.tpm,
                     self.ports, self.computer, self.queues, self.firmware, self.judge, self.finish):
            step()
        return Snapshot(self.devs, self.col.klog.state)

    def add(self, d, physical=True):
        self.devs.append(d)
        d.physical = physical
        if physical and d.sys:
            self.by_path.setdefault(d.sys, d)
        return d

    def ancestor(self, path, self_ok=True):
        if not path:
            return None
        p = path if self_ok else os.path.dirname(path)
        while p.startswith(DEVICES + "/"):
            d = self.by_path.get(p)
            if d is not None:
                return d
            p = os.path.dirname(p)
        return None

    def bind(self, d, path):
        drv = link(path + "/driver")
        if drv:
            d.drivers.append((drv, link(path + "/driver/module")))
            d.bound = True

    # ── 버스 장치 ──
    @_safe
    def pci(self):
        base = SYS + "/bus/pci/devices"
        items = []
        for n in ls(base):
            p = real(os.path.join(base, n))
            items.append((n, p, rd(p + "/vendor")[2:].lower(), rd(p + "/device")[2:].lower(),
                          rd(p + "/subsystem_vendor")[2:].lower(), rd(p + "/subsystem_device")[2:].lower()))
        self.ids.want("pci", {it[2:] for it in items})
        for n, p, ven, dev, sv, sd in items:
            self._pci_one(n, p, ven, dev, sv, sd)

    def _pci_one(self, n, p, ven, dev, sv, sd):
        cls = hexint(rd(p + "/class"), 0)
        base = cls >> 16
        props = udev_props(p, "pci")
        vname = self.ids.get("pci", ven) or props.get("ID_VENDOR_FROM_DATABASE", "")
        dname = self.ids.get("pci", ven, dev) or props.get("ID_MODEL_FROM_DATABASE", "")
        sname = self.ids.get("pci", ven, dev, sv, sd)
        kind = pci_kind(cls)
        model = dname
        if base == 0x03:
            model = product_bracket(dname)
        elif sname and base in (0x02, 0x0d) and WIFI_RE.search(sname):
            model = sname                           # 무선 카드는 서브시스템 이름이 제품 이름 (Wi-Fi 6E AX211 …)
        if not model:
            model = f"{kind} ({ven}:{dev})"         # 이름표에 아직 없는 새 장치
        cat, needs = pci_category(cls, f"{dname} {sname}")
        d = Dev(p, cat, display_name(vendor_of("pci", ven, vname), model), p, "pci", n)
        d.vendor, d.model, d.kind, d.needs_driver = vname, dname or model, kind, needs
        d.hidden = base in (0x06, 0x13) or "dummy" in dname.lower()     # 브리지·계측 — 윈도우처럼 숨김
        d.icon = ["device_pci"] if cat in ("system", "other") else None
        d.general = [("종류", kind), ("제조사", vname), ("모델", dname), ("제품 이름", sname if sname != dname else ""),
                     ("위치", pci_location(n))]
        rev = rd(p + "/revision")[2:]
        d.ids = [("하드웨어 ID", f"PCI {ven}:{dev}"), ("서브시스템", f"{sv}:{sd}" if sv else ""),
                 ("클래스", f"{cls:06x}"), ("개정", rev)]
        ma = rd(p + "/modalias")
        if ma:
            d.modaliases = [ma]
            d.ids.append(("modalias", ma))
        self.bind(d, p)
        d.res = pci_resources(p)
        self.add(d)

    @_safe
    def usb(self):
        base = SYS + "/bus/usb/devices"
        items = []
        for n in ls(base):
            if ":" in n:
                continue
            p = real(os.path.join(base, n))
            items.append((n, p, rd(p + "/idVendor").lower(), rd(p + "/idProduct").lower()))
        self.ids.want("usb", {(v, pr) for _n, _p, v, pr in items})
        for n, p, ven, prod in items:
            props = udev_props(p, "usb")
            ifaces = []
            for s in ls(p):
                if not s.startswith(n + ":"):
                    continue
                ip = p + "/" + s
                ifaces.append({"name": s, "cls": hexint(rd(ip + "/bInterfaceClass"), -1),
                               "sub": hexint(rd(ip + "/bInterfaceSubClass"), -1),
                               "proto": hexint(rd(ip + "/bInterfaceProtocol"), -1),
                               "driver": link(ip + "/driver"), "module": link(ip + "/driver/module"),
                               "modalias": rd(ip + "/modalias"), "label": rd(ip + "/interface")})
            d = Dev(p, "usb", "", p, "usb", n)
            d.usb = {"root": n.startswith("usb"), "dcls": hexint(rd(p + "/bDeviceClass"), -1), "ifaces": ifaces,
                     "ven": ven, "prod": prod, "product": rd(p + "/product"), "manu": rd(p + "/manufacturer"),
                     "vname": props.get("ID_VENDOR_FROM_DATABASE") or self.ids.get("usb", ven),
                     "mname": props.get("ID_MODEL_FROM_DATABASE") or self.ids.get("usb", ven, prod),
                     "speed": rd(p + "/speed"), "version": rd(p + "/version"), "bcd": rd(p + "/bcdDevice")}
            d.authorized = rd(p + "/authorized", "1") != "0"
            for i in ifaces:
                if i["driver"] and all(i["driver"] != x[0] for x in d.drivers):
                    d.drivers.append((i["driver"], i["module"]))
            d.bound = any(i["driver"] for i in ifaces)
            d.modaliases = [i["modalias"] for i in ifaces if i["modalias"]]
            self.add(d)

    @_safe
    def hdaudio(self):
        base = SYS + "/bus/hdaudio/devices"
        for n in ls(base):
            p = real(os.path.join(base, n))
            vn, chip = rd(p + "/vendor_name"), rd(p + "/chip_name")
            vid = rd(p + "/vendor_id")
            d = Dev(p, "sound", f"{vn} {chip}".strip() or "HD 오디오 코덱", p, "hdaudio", n)
            d.kind, d.suffix, d.vendor, d.model, d.needs_driver = "HD 오디오 코덱", "오디오 코덱", vn, chip, True
            m = re.search(r"C(\d+)D(\d+)$", n)
            d.general = [("종류", "HD 오디오 코덱"), ("제조사", vn), ("모델", chip),
                         ("위치", f"HD 오디오 버스 {m.group(1)}, 코덱 주소 {m.group(2)}" if m else n)]
            v = hexint(vid)
            d.ids = [("하드웨어 ID", f"HDAUDIO {v >> 16:04x}:{v & 0xffff:04x}" if v is not None else ""),
                     ("서브시스템", rd(p + "/subsystem_id")), ("개정", rd(p + "/revision_id"))]
            ma = rd(p + "/modalias")
            if ma:
                d.modaliases = [ma]
                d.ids.append(("modalias", ma))
            self.bind(d, p)
            self.add(d)

    @_safe
    def acpi(self):
        base = SYS + "/bus/acpi/devices"
        for n in ls(base):
            hid = n.rsplit(":", 1)[0]
            known = ACPI_KNOWN.get(hid)
            if not known:
                continue
            a = real(os.path.join(base, n))
            st = rd(a + "/status")
            if st and not (num(st, 15) & 1):            # _STA — 없는 장치
                continue
            phys = real(a + "/physical_node") if os.path.exists(a + "/physical_node") else None
            cat, title, hidden = known
            p = phys or a
            if p in self.by_path:
                continue
            if hid in ("PNP0C50", "ACPI0C50"):
                title = f"{title} ({(rd(p + '/name') or os.path.basename(p)).split(':')[0].replace('i2c-', '')})"
            d = Dev(p, cat, title, p, "acpi", os.path.basename(p))
            d.kind = title if cat == "system" else "I2C HID 장치"
            d.hidden = hidden
            d.needs_driver = cat == "hid"               # I2C 터치패드 — 드라이버가 없으면 터치패드가 안 된다
            d.general = [("종류", d.kind), ("ACPI 경로", rd(a + "/path")), ("위치", os.path.basename(p))]
            d.ids = [("하드웨어 ID", f"ACPI\\{hid}")]
            ma = rd(p + "/modalias") or rd(a + "/modalias")
            if ma:
                d.modaliases = [ma]
            self.bind(d, p)
            if not d.bound and phys:
                self.bind(d, a)
            self.add(d)

    def orphan(self, devpath, cat, name, kind="", hidden=False):
        """클래스 장치(net·sound·drm…)의 주인이 위 버스들에 없을 때 — 그 장치로 한 줄 만든다"""
        d = self.by_path.get(devpath)
        if d is not None:
            return d
        d = Dev(devpath, cat, name, devpath, link(devpath + "/subsystem") or "platform", os.path.basename(devpath))
        d.kind = kind or CAT_TITLE.get(cat, "")
        d.hidden = hidden or devpath.startswith(VIRT)
        d.general = [("종류", d.kind), ("위치", os.path.basename(devpath))]
        ma = rd(devpath + "/modalias")
        if ma:
            d.modaliases = [ma]
            d.ids = [("modalias", ma)]
        self.bind(d, devpath)
        return self.add(d)

    # ── 클래스 장치 → 주인 장치 ──
    @_safe
    def attach(self):
        # 네트워크 인터페이스
        for n in ls(SYS + "/class/net"):
            p = real(f"{SYS}/class/net/{n}")
            ue = dict(x.partition("=")[::2] for x in lines(p + "/uevent"))
            info = {"name": n, "wireless": os.path.exists(p + "/wireless") or os.path.exists(p + "/phy80211")
                    or ue.get("DEVTYPE") == "wlan", "devtype": ue.get("DEVTYPE", ""), "oper": rd(p + "/operstate")}
            owner = self.ancestor(p, False)
            if owner is None:
                if p.startswith(VIRT) or not os.path.exists(p + "/device"):
                    self._virtual_net(n, p, info)
                    continue
                dp = real(p + "/device")
                owner = self.orphan(dp, "network", udev_props(dp).get("ID_MODEL_FROM_DATABASE") or
                                    f"네트워크 어댑터 ({n})", "네트워크 어댑터")
            owner.fn.setdefault("net", []).append(info)
        # 블루투스
        for n in ls(SYS + "/class/bluetooth"):
            if ":" in n:
                continue                                # hci0:12 — 연결 하나하나
            p = real(f"{SYS}/class/bluetooth/{n}")
            owner = self.ancestor(p, False)
            if owner is None and os.path.exists(p + "/device"):
                owner = self.orphan(real(p + "/device"), "bluetooth", f"블루투스 어댑터 ({n})", "블루투스 어댑터")
            if owner is not None:
                owner.fn.setdefault("hci", []).append(n)
        # 사운드 카드
        cards = self._asound_cards()
        for n in ls(SYS + "/class/sound"):
            m = re.fullmatch(r"card(\d+)", n)
            if not m:
                continue
            p = real(f"{SYS}/class/sound/{n}")
            owner = self.ancestor(p, False)
            if owner is None and os.path.exists(p + "/device"):
                dp = real(p + "/device")
                drv = link(dp + "/driver")
                owner = self.orphan(dp, "sound", cards.get(m.group(1), ("", "", f"사운드 카드 {m.group(1)}"))[2],
                                    "사운드 장치", hidden=drv in ("snd_aloop", "snd_dummy", "snd-aloop", "snd-dummy"))
            if owner is not None:
                owner.fn.setdefault("sound", []).append(int(m.group(1)))
        # 그래픽 (DRM 카드)
        for n in ls(SYS + "/class/drm"):
            if not re.fullmatch(r"card\d+", n):
                continue
            p = real(f"{SYS}/class/drm/{n}")
            owner = self.ancestor(p, False)
            if owner is None and os.path.exists(p + "/device"):
                dp = real(p + "/device")
                drv = link(dp + "/driver")
                if drv == "simple-framebuffer" or "simple-framebuffer" in dp or "simpledrm" in drv:
                    owner = self.orphan(dp, "display", "기본 디스플레이 어댑터", "펌웨어 화면 (드라이버 없음)")
                    owner.suffix = "펌웨어 화면"
                    owner.extra_note = ("그래픽 카드 드라이버가 화면을 맡기 전까지 펌웨어(UEFI)가 준비해 둔 화면을 씁니다. "
                                        "해상도를 바꿀 수 없고 3D 가속이 없습니다.")
                else:
                    owner = self.orphan(dp, "display", f"디스플레이 어댑터 ({drv or n})", "디스플레이 어댑터",
                                        hidden=drv == "vkms")
            if owner is not None:
                owner.fn.setdefault("drm", []).append(n)
        # 비디오(카메라·캡처)
        for n in ls(SYS + "/class/video4linux"):
            p = real(f"{SYS}/class/video4linux/{n}")
            owner = self.ancestor(p, False)
            if owner is None:
                dp = real(p + "/device") if os.path.exists(p + "/device") else p
                owner = self.orphan(dp, "camera", rd(p + "/name") or f"비디오 장치 ({n})", "비디오 장치",
                                    hidden=p.startswith(VIRT))
                if dp.startswith(VIRT) or p.startswith(VIRT):
                    owner.cat = "software"
            owner.fn.setdefault("video", []).append(rd(p + "/name") or n)
        # 직렬 (USB 직렬 변환기·모뎀)
        for n in ls(SYS + "/class/tty"):
            if re.fullmatch(r"tty(USB|ACM)\d+", n):
                owner = self.ancestor(real(f"{SYS}/class/tty/{n}"), False)
                if owner is not None:
                    owner.fn.setdefault("tty", []).append(n)
        # 무선 끄기 스위치
        for n in ls(SYS + "/class/rfkill"):
            p = real(f"{SYS}/class/rfkill/{n}")
            owner = self.ancestor(p, False)
            if owner is not None:
                owner.rfkill.append((rd(p + "/type"), rd(p + "/soft") == "1", rd(p + "/hard") == "1"))
        # 입력 (USB 장치의 종류를 가를 때 쓴다 — 줄은 inputs 에서)
        for n in ls(SYS + "/class/input"):
            if n.startswith("input"):
                owner = self.ancestor(real(f"{SYS}/class/input/{n}"), False)
                if owner is not None:
                    owner.fn.setdefault("input", []).append(n)

    def _virtual_net(self, n, p, info):
        dt = info["devtype"]
        if n == "lo":
            title = "루프백 어댑터"
        elif dt == "bridge":
            title = "가상 브리지"
        elif dt == "wireguard":
            title = "WireGuard 터널"
        elif dt == "vlan":
            title = "VLAN"
        elif n.startswith(("tun", "tap")) or os.path.exists(p + "/tun_flags"):
            title = "가상 터널 어댑터"
        elif n.startswith("veth"):
            title = "가상 이더넷 (컨테이너)"
        else:
            title = "가상 네트워크 어댑터"
        d = Dev(p, "network", f"{title} ({n})", None, "net", n)
        d.kind, d.hidden, d.icon = title, True, ["network-wired"]
        d.general = [("종류", title), ("네트워크 인터페이스", n), ("상태", _oper_ko(info["oper"]))]
        d.bound = True
        self.add(d, physical=False)

    @staticmethod
    def _asound_cards():
        """/proc/asound/cards → {번호: (id, 드라이버, 이름, 긴 이름)}"""
        out = {}
        ls_ = lines(PROC + "/asound/cards")
        for i, line in enumerate(ls_):
            m = re.match(r"^\s*(\d+) \[(\S+)\s*\]: (\S+) - (.+)$", line)
            if m:
                longname = ls_[i + 1].strip() if i + 1 < len(ls_) else ""
                out[m.group(1)] = (m.group(2), m.group(3), m.group(4).strip(), longname)
        return out

    # ── USB 장치의 종류 ──
    @_safe
    def classify_usb(self):
        for d in self.devs:
            if d.bus == "usb" and hasattr(d, "usb"):
                self._usb_one(d)

    def _usb_one(self, d):
        u = d.usb
        f = d.fn
        ifs = u["ifaces"]
        classes = {i["cls"] for i in ifs}
        vshort = vendor_of("usb", u["ven"], u["vname"] or u["manu"])
        model = u["mname"] or u["product"]
        text = " ".join(x for x in (u["mname"], u["product"], u["vname"]) if x)
        needs, suffix, cat, kind, icon = True, "", "other", "USB 장치", None

        def has(cls, sub=None, proto=None):
            return any(i["cls"] == cls and (sub is None or i["sub"] == sub) and (proto is None or i["proto"] == proto)
                       for i in ifs)

        if u["root"]:
            cat, kind = "usb", "USB 루트 허브"
            d.name = f"USB 루트 허브 ({usb_version(u['version'])})"
            d.hidden = True
            model = ""
        elif u["dcls"] == 9 or 9 in classes:
            cat, kind, suffix = "usb", "USB 허브", "USB 허브"
        elif "hci" in f or has(0xe0, 1, 1):
            cat, kind = "bluetooth", "블루투스 어댑터"
        elif "net" in f or has(0x02, 0x06) or has(0x02, 0x0d) or has(0x02, 0x0e) or has(0xe0, 1, 3) or has(0xef, 4, 1):
            cat, kind, suffix = "network", "USB 네트워크 어댑터", _net_suffix(f.get("net"), "이더넷")
            if suffix == "Wi-Fi":
                kind = "USB 무선 네트워크 어댑터"
        elif "video" in f or 0x0e in classes:
            cat, kind = "camera", "USB 비디오 장치"
        elif "sound" in f or 0x01 in classes:
            cat, kind, suffix = "sound", "USB 오디오 장치", "USB 오디오"
        elif 0x07 in classes:
            cat, kind, needs = "printer", "USB 프린터", False          # CUPS 가 드라이버 없이 직접 다룬다
        elif "block" in f or 0x08 in classes:
            cat, kind, suffix = "usb", "USB 대용량 저장 장치", "대용량 저장 장치"
        elif 0x0b in classes:
            cat, kind, needs = "smartcard", "스마트 카드 판독기", False   # pcscd 가 직접 다룬다
        elif 0x06 in classes:
            cat, kind, needs = "portable", "휴대용 장치 (MTP/PTP)", False
        elif "input" in f or 0x03 in classes:
            cat, kind, suffix = "hid", "USB 입력 장치", "USB 입력 장치"
        elif "tty" in f or 0x02 in classes or 0x0a in classes:
            cat, kind = "ports", "USB 직렬 장치"
        else:
            # 제조사 전용 — 지문 인식기·RGB 조명처럼 드라이버 없이 프로그램이 직접 다루는 장치가 많다.
            # 이름으로 무선 랜·블루투스처럼 커널 드라이버가 꼭 있어야 하는 것만 가려낸다
            needs = False
            for rx, gcat, gsuf in USB_GUESS:
                if rx.search(text):
                    cat, suffix = gcat, gsuf
                    needs = gcat in ("network", "bluetooth", "camera")
                    kind = {"network": "USB 네트워크 어댑터", "bluetooth": "블루투스 어댑터",
                            "biometric": "지문 인식기", "camera": "USB 비디오 장치"}[gcat]
                    break
        if f.get("tty"):
            suffix = ", ".join(f["tty"]) if cat == "ports" else suffix
        if not u["root"]:
            d.name = display_name(vshort, model) if model else f"USB 장치 ({u['ven']}:{u['prod']})"
        d.cat, d.kind, d.suffix, d.icon = cat, kind, suffix, icon
        d.needs_driver = needs and not u["root"]
        d.vendor, d.model = u["vname"] or u["manu"], model
        told = " ".join(x for x in (u["manu"], u["product"]) if x)
        d.general = [("종류", kind), ("제조사", u["vname"] or u["manu"]), ("모델", u["mname"]),
                     ("장치가 알려 준 이름", told if told and told != d.name else ""),
                     ("위치", usb_location(os.path.basename(d.sys))),
                     ("속도", USB_SPEED_KO.get(u["speed"], f"{u['speed']} Mbps" if u["speed"] else ""))]
        ifrows = []
        for i in ifs:
            c = i["cls"]
            what = USB_IF_KO.get(c, "") if c >= 0 else ""
            ifrows.append(f"{i['name']}  {c:02x}/{i['sub']:02x}/{i['proto']:02x} {what} · "
                          f"{('드라이버 ' + i['driver']) if i['driver'] else '드라이버 없음'}")
        d.ids = [("하드웨어 ID", f"USB {u['ven']}:{u['prod']}"),
                 ("개정", f"{u['bcd'][:2]}.{u['bcd'][2:]}" if len(u["bcd"]) == 4 else u["bcd"]),
                 ("인터페이스", "\n".join(ifrows))]

    # ── 따로 보이는 줄들 ──
    @_safe
    def disks(self):
        for n in ls(SYS + "/block"):
            if n.startswith("ram"):
                continue                                # 램 디스크는 장치가 아니다
            p = real(f"{SYS}/block/{n}")
            size = (num(rd(p + "/size"), 0) or 0) * 512
            removable = rd(p + "/removable") == "1"
            virt = p.startswith(VIRT)
            if size == 0 and (virt or not (removable or n.startswith("sr"))):
                continue                                # 비어 있는 loop 따위
            devp = real(p + "/device") if os.path.exists(p + "/device") else None
            owner = self.ancestor(p, False)
            if owner is not None:
                owner.fn.setdefault("block", []).append(n)
            props = udev_props(p, "block")
            model = rd(p + "/device/model")
            vendor = rd(p + "/device/vendor").strip(" ,")
            mtype = ""
            if n.startswith("mmcblk"):
                model, mtype = rd(p + "/device/name"), rd(p + "/device/type")
            if not model:
                model = props.get("ID_MODEL", "").replace("_", " ")
            if vendor.upper() in ("ATA", "NVME") or model.casefold().startswith(vendor.casefold()):
                vendor = ""
            rot = rd(p + "/queue/rotational") == "1"
            if n.startswith("nvme"):
                transport = "NVMe"
            elif n.startswith("mmcblk"):
                transport = mtype or "SD/MMC"
            elif n.startswith("vd"):
                transport = "VirtIO"
            elif owner is not None and owner.bus == "usb":
                transport = "USB"
            elif "/ata" in p:
                transport = "SATA"
            elif virt:
                transport = "가상"
            else:
                transport = (props.get("ID_BUS") or "SCSI").upper()
            if virt:
                cat = "software"
                if n.startswith("loop"):
                    title = f"루프 장치 ({n})"
                elif n.startswith("zram"):
                    title = f"압축 메모리 스왑 ({n})"
                elif n.startswith("dm-"):
                    title = f"장치 매퍼 볼륨 ({rd(p + '/dm/name') or n})"
                elif n.startswith("md"):
                    title = f"소프트웨어 RAID ({n})"
                else:
                    title = f"가상 디스크 ({n})"
                kind = "가상 디스크"
            else:
                cat = "cdrom" if n.startswith("sr") else "disk"
                title = " ".join(x for x in (vendor, model) if x) or \
                    ("DVD/CD-ROM 드라이브" if cat == "cdrom" else "디스크") + f" ({n})"
                kind = ("광 드라이브" if cat == "cdrom" else "USB 저장 장치" if transport == "USB" else
                        "SD/MMC 카드" if n.startswith("mmcblk") else "하드 디스크" if rot else "SSD")
            d = Dev(p, cat, title, p, "block", n)
            d.kind = kind
            d.hidden = virt or bool(re.search(r"boot\d+$|rpmb$", n))
            d.suffix = " · ".join(x for x in ((fmt_size(size) if size else ("미디어 없음" if cat == "cdrom" else "")),
                                               transport if not virt else "") if x)
            d.icon = (["drive-optical", "media-optical"] if cat == "cdrom" else
                      ["drive-removable-media-usb", "drive-harddisk-usb", "drive-harddisk"] if transport == "USB" else
                      ["media-flash", "media-memory-sd"] if n.startswith("mmcblk") else
                      ["drive-harddisk"] if rot or virt else ["drive-harddisk-solidstate", "drive-harddisk"])
            parts = sum(1 for x in ls(p) if os.path.exists(f"{p}/{x}/partition"))
            rev = rd(p + "/device/rev") or rd(p + "/device/firmware_rev")
            d.general = [("종류", kind), ("모델", model), ("용량", f"{fmt_size(size)} ({size:,} 바이트)" if size else ""),
                         ("연결 방식", transport), ("펌웨어 버전", rev), ("파티션", f"{parts}개" if parts else ""),
                         ("이동식", "예" if removable else "")]
            d.ids = [("장치 파일", f"/dev/{n}"), ("주:부 번호", rd(p + "/dev")),
                     ("WWN", props.get("ID_WWN", "")), ("일련 번호", props.get("ID_SERIAL_SHORT", ""))]
            d.drivers = drivers_upto(devp or p, owner.sys if owner else None, 2)
            if owner is not None:
                d.drivers += [x for x in owner.drivers if x not in d.drivers][:1]
            d.bound = True
            d.parent = owner.key if owner else None
            self.add(d, physical=False)

    @_safe
    def inputs(self):
        for n in ls(SYS + "/class/input"):
            if not n.startswith("input"):
                continue
            p = real(f"{SYS}/class/input/{n}")
            name = rd(p + "/name") or n
            props = udev_props(p, "input")
            bus = rd(p + "/id/bustype").lower()
            phys = rd(p + "/phys")
            owner = self.ancestor(p, False)
            low = name.lower()
            one = lambda k: props.get(k) == "1"  # noqa: E731
            if one("ID_INPUT_JOYSTICK"):
                cat, kind, icon = "hid", "게임 컨트롤러", ["input-gaming"]
            elif one("ID_INPUT_TOUCHPAD"):
                cat, kind, icon = "mouse", "터치패드", ["input-touchpad", "input-mouse"]
            elif one("ID_INPUT_MOUSE") or one("ID_INPUT_POINTINGSTICK"):
                cat, kind, icon = "mouse", "마우스", ["input-mouse"]
            elif one("ID_INPUT_TABLET"):
                cat, kind, icon = "hid", "펜 태블릿", ["input-tablet"]
            elif one("ID_INPUT_TOUCHSCREEN"):
                cat, kind, icon = "hid", "터치 스크린", ["input-touchscreen", "input-tablet"]
            elif one("ID_INPUT_KEYBOARD"):
                cat, kind, icon = "keyboard", "키보드", ["input-keyboard"]
            elif not props and "keyboard" in low:              # udev 자료가 없을 때 (시험·컨테이너)
                cat, kind, icon = "keyboard", "키보드", ["input-keyboard"]
            elif not props and ("mouse" in low or "touchpad" in low):
                cat, kind, icon = "mouse", "마우스", ["input-mouse"]
            elif one("ID_INPUT_KEY"):
                cat, kind, icon = "hid", "단추·특수 키", ["input-keyboard"]
            elif one("ID_INPUT_SWITCH"):
                cat, kind, icon = "hid", "스위치", None
            elif one("ID_INPUT_ACCELEROMETER"):
                cat, kind, icon = "hid", "가속도계", None
            else:
                cat, kind, icon = "hid", "입력 장치", None
            if bus == "0011" and kind == "키보드" and "translated" in low:
                name = "표준 PS/2 키보드"
            d = Dev(p, cat, name, p, "input", rd(p + "/name") or n)
            d.kind, d.icon = kind, icon
            # 시스템 단추·소리 잭 감지·가상 입력(uinput)은 숨긴다 — 블루투스 입력(uhid)은 가상 경로라도 보인다
            d.hidden = (bus in ("0019", "0006", "0010") or phys.startswith("ALSA") or p.startswith(VIRT + "/input")
                        or (p.startswith(VIRT) and bus not in ("0005", "0003")))
            d.suffix = INPUT_BUS_KO.get(bus, "") if bus in ("0005", "0011", "0018") else ""
            ven, prod = rd(p + "/id/vendor"), rd(p + "/id/product")
            d.general = [("종류", kind), ("연결", INPUT_BUS_KO.get(bus, bus)), ("장치 이름", rd(p + "/name")),
                         ("물리 경로", phys)]
            d.ids = [("하드웨어 ID", f"{INPUT_BUS_KO.get(bus, bus)} {ven}:{prod}" if ven else ""),
                     ("커널 이름", n), ("장치 파일", ", ".join(f"/dev/input/{x}" for x in ls(p)
                                                          if re.fullmatch(r"(event|mouse|js)\d+", x)))]
            d.drivers = drivers_upto(p, owner.sys if owner else None)
            d.bound = True
            d.parent = owner.key if owner else None
            self.add(d, physical=False)

    @_safe
    def monitors(self):
        for n in ls(SYS + "/class/drm"):
            m = re.fullmatch(r"(card\d+)-(.+)", n)
            if not m:
                continue
            p = real(f"{SYS}/class/drm/{n}")
            if rd(p + "/status") != "connected":
                continue
            e = parse_edid(rd_bytes(p + "/edid", 32768)) or {}
            gpu = self.ancestor(p, False)
            conn = m.group(2)
            internal = conn.startswith(("eDP", "LVDS", "DSI"))
            vname = self.col.pnp.get(e["pnp"]) if e else ""
            name = e.get("name") or (f"{short_vendor(vname)} 모니터" if vname else
                                     "내장 디스플레이" if internal else "일반 PnP 모니터")
            d = Dev("mon:" + p, "monitor", name, p, "drm", conn)
            d.kind = "내장 디스플레이" if internal else "모니터"
            d.icon = ["computer-laptop", "video-display"] if internal else ["video-display"]
            d.suffix = conn + (" · 내장" if internal else "")
            size = ""
            if e.get("wcm") and e.get("hcm"):
                size = f"{((e['wcm'] ** 2 + e['hcm'] ** 2) ** 0.5) / 2.54:.1f}인치 ({e['wcm']} × {e['hcm']} cm)"
            pref = e.get("pref")
            made = ""
            if e.get("year") and e["year"] > 1990:
                made = f"{e['year']}년" + (f" {e['week']}주" if 0 < e.get("week", 0) <= 53 else "")
            d.general = [("종류", d.kind), ("제조사", vname), ("모델", e.get("name", "")),
                         ("크기", size), ("기본 해상도", f"{pref[0]} × {pref[1]}" + (f" @ {pref[2]:.0f} Hz" if pref[2] else "")
                                       if pref else ""),
                         ("제조 시기", made), ("연결", f"{conn} ({m.group(1)})"),
                         ("사용", "사용 안 함 (화면 꺼짐)" if rd(p + "/enabled") == "disabled" else "")]
            d.ids = [("하드웨어 ID", f"MONITOR\\{e['pnp']}{e['code']:04X}" if e else ""),
                     ("일련 번호", e.get("sn") or (str(e["serial"]) if e.get("serial") else ""))]
            if gpu is not None:
                d.drivers = list(gpu.drivers)
                d.parent = gpu.key
                d.kname = gpu.kname                        # 커널 기록에는 커넥터보다 그래픽 카드 이름이 남는다
            d.bound = True
            self.add(d, physical=False)

    @_safe
    def audio(self):
        cards = self._asound_cards()
        for num_, (cid, _drv, cname, longname) in cards.items():
            cdir = f"{PROC}/asound/card{num_}"
            csys = real(f"{SYS}/class/sound/card{num_}") if os.path.exists(f"{SYS}/class/sound/card{num_}") else None
            owner = self.ancestor(csys, False) if csys else None
            for pd in ls(cdir):
                m = re.fullmatch(r"pcm(\d+)([pc])", pd)
                if not m:
                    continue
                info = {}
                for line in lines(f"{cdir}/{pd}/info"):
                    k, _, v = line.partition(":")
                    info[k.strip()] = v.strip()
                pname = info.get("name") or info.get("id") or f"PCM {m.group(1)}"
                out = m.group(2) == "p"
                hdmi = bool(re.search(r"hdmi|displayport|\bdp\b", pname, re.I))
                d = Dev(f"pcm:{num_}:{m.group(1)}{m.group(2)}", "audio", pname, None, "sound", f"card{num_}")
                d.kind = "오디오 출력" if out else "오디오 입력"
                d.suffix = f"{'출력' if out else '입력'} · {cname}"
                d.icon = ["audio-speakers"] if out else ["audio-input-microphone"]
                # HDMI·DP 출력은 모니터가 꽂힌 것만 아래 ELD 로 보인다 — 번호만 있는 빈 출력이 여럿이라
                d.hidden = hdmi or owner is None or owner.hidden
                d.order = (num_, int(m.group(1)), m.group(2))
                d.general = [("종류", d.kind), ("사운드 카드", longname or cname), ("ALSA 장치", f"hw:{num_},{m.group(1)}")]
                if owner is not None:
                    d.drivers = list(owner.drivers)
                    d.parent = owner.key
                    d.kname = owner.kname
                d.bound = True
                self.add(d, physical=False)
            for e in ls(cdir):
                if not e.startswith("eld#"):
                    continue
                kv = {}
                for line in lines(f"{cdir}/{e}"):
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        kv[parts[0]] = parts[1].strip()
                if kv.get("monitor_present") != "1" or kv.get("eld_valid") != "1":
                    continue
                ct = {"DisplayPort": "DP"}.get(kv.get("connection_type", ""), kv.get("connection_type", "HDMI"))
                d = Dev(f"eld:{num_}:{e}", "audio", kv.get("monitor_name") or "HDMI/DP 오디오", None, "sound",
                        f"card{num_}")
                d.kind = "오디오 출력 (모니터)"
                d.suffix = f"{ct} 출력 · {cname}"
                d.icon = ["video-display", "audio-speakers"]
                d.hidden = owner is None or owner.hidden
                d.order = (num_, 100, e)
                d.general = [("종류", d.kind), ("모니터", kv.get("monitor_name", "")), ("연결", ct),
                             ("사운드 카드", longname or cname)]
                if owner is not None:
                    d.drivers = list(owner.drivers)
                    d.parent = owner.key
                    d.kname = owner.kname
                d.bound = True
                self.add(d, physical=False)

    @_safe
    def cpus(self):
        blocks = [b for b in rd_all(PROC + "/cpuinfo").split("\n\n") if b.strip()]
        cpus = []
        for b in blocks:
            kv = {}
            for line in b.splitlines():
                k, _, v = line.partition(":")
                kv.setdefault(k.strip(), v.strip())
            if "processor" in kv and num(kv["processor"]) is not None:
                cpus.append(kv)
        if not cpus:
            return
        cores = {(c.get("physical id", "0"), c.get("core id", c["processor"])) for c in cpus}
        vendors = {"GenuineIntel": "Intel", "AuthenticAMD": "AMD", "HygonGenuine": "Hygon"}
        for c in cpus:
            idx = int(c["processor"])
            model = re.sub(r"\s+", " ", c.get("model name") or c.get("Processor") or c.get("cpu model") or "프로세서")
            sp = f"{SYS}/devices/system/cpu/cpu{idx}"
            d = Dev(f"cpu:{idx}", "processor", model, None, "cpu", f"CPU{idx}")
            d.kind, d.order = "프로세서", (idx,)
            maxf = num(rd(sp + "/cpufreq/cpuinfo_max_freq"))
            d.general = [("종류", "프로세서"), ("제조사", vendors.get(c.get("vendor_id", ""), c.get("vendor_id", ""))),
                         ("모델", model), ("구성", f"물리 코어 {len(cores)}개 · 논리 프로세서 {len(cpus)}개"),
                         ("최대 속도", f"{maxf / 1e6:.2f} GHz" if maxf else ""),
                         ("위치", f"소켓 {c.get('physical id', '0')}, 코어 {c.get('core id', '?')}, "
                                  f"논리 프로세서 {idx}"),
                         ("마이크로코드", c.get("microcode", ""))]
            drv = link(sp + "/driver")
            d.drivers = [(drv, "")] if drv else []
            d.bound = True
            d.extra_drv = [("주파수 드라이버", rd(sp + "/cpufreq/scaling_driver")),
                           ("전원 정책", rd(sp + "/cpufreq/scaling_governor"))]
            if rd(sp + "/online", "1") == "0":
                d.level, d.state, d.title = "off", "오프라인", "이 논리 프로세서는 꺼져 있습니다(오프라인)."
            self.add(d, physical=False)

    @_safe
    def power(self):
        status_ko = {"Charging": "충전 중", "Discharging": "방전 중", "Full": "완전히 충전됨",
                     "Not charging": "충전하지 않음", "Unknown": "알 수 없음"}
        for n in ls(SYS + "/class/power_supply"):
            p = real(f"{SYS}/class/power_supply/{n}")
            typ, scope = rd(p + "/type"), rd(p + "/scope")
            manu, model = rd(p + "/manufacturer"), rd(p + "/model_name")
            if typ == "Battery":
                dev_scope = scope == "Device"
                name = (f"{model or n} 배터리" if dev_scope else
                        " ".join(x for x in (manu, model) if x) or "배터리")
                d = Dev(p, "battery", name, p, "power_supply", n)
                d.kind = "장치 배터리 (무선 마우스·키보드 등)" if dev_scope else "배터리"
                d.hidden = dev_scope
                d.suffix = n if not dev_scope else ""
                full = num(rd(p + "/energy_full")) or num(rd(p + "/charge_full"))
                design = num(rd(p + "/energy_full_design")) or num(rd(p + "/charge_full_design"))
                health = full / design if full and design else None
                d.general = [("종류", d.kind), ("제조사", manu), ("모델", model), ("기술", rd(p + "/technology")),
                             ("상태", status_ko.get(rd(p + "/status"), rd(p + "/status"))),
                             ("충전량", f"{rd(p + '/capacity')}%" if rd(p + "/capacity") else ""),
                             ("배터리 수명", f"{health:.0%} (설계 용량 대비)" if health else ""),
                             ("충전 횟수", rd(p + "/cycle_count") if rd(p + "/cycle_count") not in ("", "0") else "")]
                d.icon = ["battery", "battery-full"]
                if health is not None and health < 0.6 and not dev_scope:
                    d.level, d.state = "info", "많이 닳음"
                    d.title = "이 배터리는 처음 용량의 60%도 담지 못합니다."
                    d.detail = "배터리가 많이 닳았습니다. 전원 없이 쓰는 시간이 짧다면 배터리 교체를 생각해 보세요."
            elif typ == "Mains":
                d = Dev(p, "battery", "AC 어댑터", p, "power_supply", n)
                d.kind, d.icon = "AC 어댑터", ["ac-adapter", "battery"]
                d.general = [("종류", "AC 어댑터"), ("상태", "연결됨" if rd(p + "/online") == "1" else "연결 안 됨")]
            else:
                d = Dev(p, "battery", {"UPS": "무정전 전원 장치(UPS)"}.get(typ, f"{typ} 전원 공급 ({n})"), p,
                        "power_supply", n)
                d.kind = typ
                d.hidden = typ != "UPS"
                d.general = [("종류", typ), ("제조사", manu), ("모델", model)]
            owner = self.ancestor(p, False)
            dp = real(p + "/device") if os.path.exists(p + "/device") else None
            d.drivers = drivers_upto(dp, owner.sys if owner else None, 1) if dp else []
            d.bound = True
            d.parent = owner.key if owner else None
            self.add(d, physical=False)

    @_safe
    def tpm(self):
        for n in ls(SYS + "/class/tpm"):
            if not re.fullmatch(r"tpm\d+", n):
                continue
            p = real(f"{SYS}/class/tpm/{n}")
            ver = rd(p + "/tpm_version_major")
            dp = real(p + "/device") if os.path.exists(p + "/device") else p
            d = Dev(p, "security", f"신뢰할 수 있는 플랫폼 모듈 {ver}.0" if ver else "신뢰할 수 있는 플랫폼 모듈(TPM)",
                    p, "tpm", n)
            d.kind, d.icon = "보안 장치 (TPM)", ["security-high", "channel-secure"]
            d.general = [("종류", d.kind), ("TPM 버전", f"{ver}.0" if ver else ""),
                         ("위치", rd(dp + "/hid") or os.path.basename(dp))]
            d.drivers = drivers_upto(dp, None, 1)
            d.bound = bool(d.drivers)
            self.add(d, physical=False)

    @_safe
    def ports(self):
        uart = {"1": "8250", "2": "16450", "3": "16550", "4": "16550A", "5": "Cirrus", "6": "ST16650",
                "7": "ST16650V2", "8": "16750", "9": "Startech", "10": "16C950"}
        for n in ls(SYS + "/class/tty"):
            if not re.fullmatch(r"ttyS\d+", n):
                continue
            p = real(f"{SYS}/class/tty/{n}")
            t = rd(p + "/type")
            if t in ("", "0"):
                continue                                # 커널이 미리 만들어 둔 빈 자리 — 실제 포트 아님
            d = Dev(p, "ports", f"통신 포트 ({n})", p, "tty", n)
            d.kind, d.icon = "직렬 포트", ["serial-port"]
            d.general = [("종류", "직렬 포트"), ("장치 파일", f"/dev/{n}"), ("UART", uart.get(t, t))]
            d.res = [("I/O 주소", rd(p + "/port")), ("IRQ", rd(p + "/irq"))]
            dp = real(p + "/device") if os.path.exists(p + "/device") else None
            d.drivers = drivers_upto(dp, None, 1) if dp else []
            d.bound = True
            self.add(d, physical=False)

    @_safe
    def computer(self):
        dmi = SYS + "/class/dmi/id"
        g = {k: rd(f"{dmi}/{k}") for k in ("sys_vendor", "product_name", "product_version", "board_vendor",
                                             "board_name", "bios_vendor", "bios_version", "bios_date", "chassis_type")}

        def junk(s):
            return not s or re.search(r"to be filled|o\.?e\.?m\.?|system (?:product name|manufacturer|version)|"
                                      r"default string|not specified|not applicable|^none$|^x\.x$", s, re.I)
        vendor = "" if junk(g["sys_vendor"]) else DMI_VENDOR.get(g["sys_vendor"].lower(), short_vendor(g["sys_vendor"]))
        if vendor == "Lenovo" and not junk(g["product_version"]) and " " in g["product_version"]:
            name = display_name(vendor, g["product_version"])      # 레노버는 제품 이름이 version 칸에 있다
        elif not junk(g["product_name"]):
            name = display_name(vendor, g["product_name"])
        elif not junk(g["board_name"]):
            name = display_name(DMI_VENDOR.get(g["board_vendor"].lower(), short_vendor(g["board_vendor"])),
                                g["board_name"])
        else:
            name = "이 컴퓨터"
        efi = os.path.isdir(SYS + "/firmware/efi")
        sb = ""
        if efi:
            b = rd_bytes(SYS + "/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c", 16)
            sb = "켜짐" if len(b) >= 5 and b[4] == 1 else "꺼짐" if len(b) >= 5 else ""
        chassis = {"3": "데스크톱", "4": "로우 프로필 데스크톱", "6": "미니 타워", "7": "타워", "8": "휴대용",
                   "9": "노트북", "10": "노트북", "13": "일체형", "14": "서브노트북", "17": "서버", "23": "서버",
                   "30": "태블릿", "31": "컨버터블", "32": "분리형", "35": "미니 PC", "36": "스틱 PC",
                   "1": "기타"}.get(g["chassis_type"], "")
        d = Dev("computer", "computer", name, None, "dmi", "DMI:")
        d.kind, d.suffix = "컴퓨터", "UEFI" if efi else "BIOS"
        d.icon = ["computer-laptop", "computer"] if chassis in ("노트북", "휴대용", "서브노트북", "컨버터블") else ["computer"]
        d.general = [("제조사", g["sys_vendor"] if not junk(g["sys_vendor"]) else ""),
                     ("모델", " ".join(x for x in (g["product_name"], g["product_version"]) if not junk(x))),
                     ("형태", chassis),
                     ("메인보드", " ".join(x for x in (g["board_vendor"], g["board_name"]) if not junk(x))),
                     ("BIOS", " ".join(x for x in (g["bios_vendor"], g["bios_version"]) if x) +
                      (f" ({g['bios_date']})" if g["bios_date"] else "")),
                     ("펌웨어 방식", "UEFI" if efi else "레거시 BIOS"), ("보안 부팅", sb), ("커널", f"Linux {RELEASE}")]
        d.bound = True
        self.add(d, physical=False)

    @_safe
    def queues(self):
        exe = None if ROOT else shutil.which("lpstat")
        if not exe:
            return
        try:
            r = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=4, env=ENV_C)
        except (OSError, subprocess.SubprocessError):
            return
        for line in r.stdout.splitlines():
            m = re.match(r"^device for (\S+): (.+)$", line)
            if not m:
                continue
            uri = re.sub(r"//[^/@]*@", "//***@", m.group(2).strip())     # 주소에 든 계정·비밀번호는 가린다
            d = Dev("queue:" + m.group(1), "queue", m.group(1), None, "cups", "")
            d.kind, d.icon = "인쇄 대기열 (CUPS)", ["printer"]
            d.general = [("종류", d.kind), ("연결", uri)]
            d.bound = True
            self.add(d, physical=False)

    # ── 펌웨어 · 상태 ──
    @_safe
    def firmware(self):
        kl = self.col.klog
        kl.update()
        for kdev, msg in kl.events:
            path = kdev_path(kdev)
            if path is None:
                m = _BT_DEV.match(msg) or _KDEV_TEXT.match(msg)
                if m:
                    path = name_path(m.group(m.lastindex))
            d = self.ancestor(path) if path else None
            if d is None:
                continue
            head = _KDEV_TEXT.match(msg)
            if head and not d.fw_driver and head.group(1) not in ("Bluetooth:", "platform"):
                d.fw_driver = head.group(1)
            ok = FW_OK.search(msg)
            if ok:
                if ok.group(1) not in d.fw_ok:
                    d.fw_ok.append(ok.group(1))
                continue
            for rx in FW_FAIL:
                f = rx.search(msg)
                if f:
                    if f.group(1) not in d.fw_fail:
                        d.fw_fail.append(f.group(1))
                    break
            if FW_FATAL.search(msg) and msg not in d.fw_fatal:
                d.fw_fatal.append(msg)
        for d in self.devs:                            # 나중에 읽힌 파일은 실패 목록에서 뺀다
            d.fw_fail = [f for f in d.fw_fail if f not in d.fw_ok]

    @_safe
    def judge(self):
        black = None
        for d in self.devs:
            if not d.physical:
                continue
            fn_key = {"network": "net", "bluetooth": "hci", "display": "drm", "camera": "video"}.get(d.cat)
            if d.cat == "sound" and d.bus in ("pci", "usb"):
                fn_key = "sound"
            has_fn = (fn_key in d.fn) if fn_key else d.bound
            main_drv = d.drivers[0][0] if d.drivers else d.fw_driver
            fw_bad = bool(d.fw_fail or d.fw_fatal)
            if fw_bad and (not d.bound or not has_fn or (d.cat == "bluetooth" and d.fw_fatal and not d.fw_ok)):
                d.level, d.state = "warn", "펌웨어 오류"
                d.title = "장치의 펌웨어를 불러오지 못했습니다."
                d.detail = (f"드라이버({main_drv or '알 수 없음'})가 장치를 시작하는 데 필요한 펌웨어 파일을 "
                            "찾지 못해 장치가 동작하지 않을 수 있습니다.")
                if d.fw_fail:
                    d.hints.append("찾지 못한 파일: " + ", ".join(d.fw_fail[:6]))
                    pkgs = sorted({fw_package(f) for f in d.fw_fail})
                    d.hints.append(f"이 파일은 데비안 패키지 {', '.join(pkgs)} 에 들어 있을 수 있습니다. "
                                   "설치한 뒤 컴퓨터를 다시 시작하세요.")
                elif d.fw_fatal:
                    d.hints.append("커널 기록: " + d.fw_fatal[-1][:300])
            elif d.needs_driver and not d.bound and d.authorized:
                if black is None:
                    black = self.mods.blacklist()
                d.level, d.state = "warn", "드라이버 없음"
                d.title = "이 장치의 드라이버가 설치되지 않았습니다."
                d.detail, extra = self._nodriver(d, black)
                d.hints += extra
            elif not d.authorized:
                d.level, d.state = "off", "사용 안 함"
                d.title = "이 USB 장치는 사용이 허용되지 않았습니다 (authorized=0)."
            elif any(soft or hard for _t, soft, hard in d.rfkill):
                hard = any(h for _t, _s, h in d.rfkill)
                d.level, d.state = "off", "꺼짐"
                d.title = "무선이 꺼져 있습니다."
                d.detail = ("컴퓨터의 무선 스위치(또는 펌웨어 설정)로 꺼져 있습니다. 스위치·Fn 키를 확인하세요." if hard else
                            "비행기 모드나 설정에서 꺼 두었습니다. 빠른 설정이나 설정 앱에서 켤 수 있습니다.")
            elif d.cat == "network" and d.bus == "pci" and d.bound and not has_fn and \
                    main_drv not in ("vfio-pci", "pci-stub"):
                # 드라이버는 붙었는데 인터페이스가 없다 — 펌웨어·초기화 문제일 때가 많다 (커널 기록을 못 읽을 때도 보이게)
                d.level, d.state = "warn", "시작 안 됨"
                d.title = "장치를 시작하지 못했습니다."
                d.detail = (f"드라이버({main_drv})는 연결됐지만 네트워크 인터페이스가 만들어지지 않았습니다. "
                            "펌웨어가 없거나 초기화에 실패했을 수 있습니다 — 커널 메시지를 확인하세요.")
            elif fw_bad and d.fw_ok and not d.fw_fatal:
                pass            # 새 판 파일을 먼저 찾아보고 없으면 옛 판을 쓰는 드라이버(iwlwifi 등) — 정상
            elif fw_bad:
                d.level, d.state = "info", ""
                d.title = "이 장치는 동작하고 있습니다."
                d.detail = ("일부 펌웨어 파일을 찾지 못했지만 드라이버가 다른 파일로 장치를 시작했습니다. "
                            "대개 문제가 없습니다.")
                if d.fw_fail:
                    d.hints.append("찾지 못한 파일: " + ", ".join(d.fw_fail[:6]))
            elif not d.bound and d.bus in ("pci", "usb"):
                d.level, d.state = "info", ""
                d.title = "드라이버 없이 연결되어 있습니다."
                d.detail = ("이 종류의 장치는 드라이버가 없어도 컴퓨터가 정상적으로 동작하거나, "
                            "드라이버 대신 프로그램이 직접 다루는 경우가 많습니다.")
            note = getattr(d, "extra_note", "")
            if note:
                d.level = "info" if d.level == "ok" else d.level
                d.detail = (d.detail + " " + note).strip()
            if d.cat == "display" and d.bus == "pci" and d.level == "warn" and "nvidia" in (d.vendor or "").lower():
                d.hints.append("설정 › 그래픽 에서 NVIDIA 공식 드라이버를 설치할 수 있습니다.")

    def _nodriver(self, d, black):
        cands = []
        for ma in d.modaliases:
            for m in self.mods.match(ma):
                if m not in cands:
                    cands.append(m)
        if not cands:
            return (f"지금 커널(Linux {RELEASE})에는 이 장치를 위한 드라이버가 들어 있지 않습니다.",
                    ["더 새 커널이나 제조사가 제공하는 드라이버가 필요할 수 있습니다."])
        blocked = [m for m in cands if _norm(m) in black]
        if blocked and len(blocked) == len(cands):
            return (f"커널에 드라이버({', '.join(blocked)})가 있지만 자동으로 불러오지 않도록 막혀 있습니다 "
                    "(modprobe 차단 목록).", [])
        loaded = [m for m in cands if self.mods.loaded(m)]
        if loaded:
            return (f"드라이버({', '.join(loaded)})를 불러왔지만 이 장치를 맡지 못했습니다. 장치를 초기화하다 "
                    "실패했을 수 있습니다 — 커널 메시지를 확인하세요.", [])
        return (f"커널에 드라이버({', '.join(cands)})가 있지만 불러오지 않았습니다. "
                "컴퓨터를 다시 시작하면 해결될 수 있습니다.", [])

    @_safe
    def finish(self):
        self._keymap = {d.key: d for d in self.devs}      # 부모 이름을 찾을 때
        for d in self.devs:
            try:
                self._finish_one(d)
            except Exception:
                traceback.print_exc()

    def _finish_one(self, d):
        if d.parent is None and d.physical and d.sys:
            up = self.ancestor(d.sys, False)
            d.parent = up.key if up else None
        if d.parent:
            up = self._keymap.get(d.parent)
            if up is not None and up.bus == "usb" and getattr(up, "usb", {}).get("root") and up.parent:
                up = self._keymap.get(up.parent, up)         # 숨겨진 루트 허브 대신 USB 컨트롤러
            d.parent_name = up.name if up else ""
        if d.icon is None:
            if d.cat == "network":
                wl = any(n["wireless"] for n in d.fn.get("net", ())) or d.suffix == "Wi-Fi" or \
                    (d.bus == "pci" and not d.fn.get("net") and WIFI_RE.search(f"{d.name} {d.model} {d.kind}"))
                d.icon = ["network-wireless", "network-wired"] if wl else ["network-wired"]
            else:
                d.icon = CAT_ICON.get(d.cat)
        if d.cat == "network" and not d.suffix and d.physical:
            d.suffix = _net_suffix(d.fn.get("net"), "Wi-Fi" if d.icon and d.icon[0] == "network-wireless" else
                                   "이더넷")
        # 드라이버 칸
        rows = []
        if d.drivers:
            names = dict.fromkeys(x[0] for x in d.drivers)
            rows.append(("드라이버", ", ".join(f"{n} (프로그램이 직접 다루는 중)" if n == "usbfs" else n
                                              for n in names)))
            drv, mod = d.drivers[0]
            mi = self.mods.info(mod) if mod else None
            if mi and mi["builtin"] and not mi["file"]:
                rows.append(("커널 모듈", f"{mod} — 커널에 내장됨"))
            elif mi:
                rows.append(("커널 모듈", mod))
                rows.append(("버전", mi["version"] or f"Linux {RELEASE} (커널과 함께 배포)"))
                rows.append(("파일", mi["file"]))
                src = []
                if "O" in mi["taint"] or "/updates/" in mi["rel"] or "/extra/" in mi["rel"]:
                    src.append("커널 밖에서 만든 모듈 (DKMS 등)")
                if "P" in mi["taint"]:
                    src.append("독점 모듈")
                if "E" in mi["taint"]:
                    src.append("서명되지 않음")
                rows.append(("출처", " · ".join(src)))
            elif d.physical or d.bus in ("cpu",):
                rows.append(("커널 모듈", f"{drv} — 커널에 내장됨"))
            others = [x[1] or x[0] for x in d.drivers[1:]]
            if others:
                rows.append(("함께 쓰는 모듈", ", ".join(dict.fromkeys(others))))
        elif d.physical:
            rows.append(("드라이버", "없음"))
        rows += d.extra_drv
        if d.fw_ok:
            rows.append(("불러온 펌웨어", "\n".join(d.fw_ok[:12])))
        if d.fw_fail:
            rows.append(("찾지 못한 펌웨어", "\n".join(d.fw_fail[:12])))
        d.driver_rows = rows
        # 네트워크 인터페이스 · 사운드 카드 같은 "만든 것"
        net = d.fn.get("net")
        if net:
            d.general.append(("네트워크 인터페이스", ", ".join(f"{n['name']} ({_oper_ko(n['oper'])})" for n in net)))
        if d.fn.get("hci"):
            d.general.append(("블루투스 장치", ", ".join(d.fn["hci"])))
        if d.fn.get("drm") and d.cat == "display":
            d.general.append(("그래픽 장치", ", ".join(f"/dev/dri/{x}" for x in d.fn["drm"])))
        if d.fn.get("sound") and d.bus in ("pci", "usb", "platform"):
            d.general.append(("사운드 카드", ", ".join(f"카드 {x}" for x in d.fn["sound"])))
        if d.fn.get("video"):
            d.general.append(("비디오 장치", ", ".join(dict.fromkeys(d.fn["video"]))))
        if d.fn.get("block") and d.bus == "usb":
            d.general.append(("디스크", ", ".join(f"/dev/{x}" for x in d.fn["block"])))
        if d.rfkill:
            kinds = {"wlan": "Wi-Fi", "bluetooth": "블루투스", "wwan": "모바일 광대역", "nfc": "NFC", "gps": "GPS"}
            d.general.append(("무선", ", ".join(
                f"{kinds.get(t, t)} " + ("꺼짐 (스위치)" if h else "꺼짐" if s else "켜짐") for t, s, h in d.rfkill)))
        acts = list(CAT_ACTIONS.get(d.cat, []))
        if d.cat == "system" and d.bus == "power_supply":
            acts = ["power"]
        d.actions = acts
        parts = [d.name, d.vendor, d.model, d.kind, d.suffix, d.kname, CAT_TITLE.get(d.cat, ""), d.state]
        parts += [v for _k, v in d.ids] + [x for dr in d.drivers for x in dr]
        d.search = " ".join(p for p in parts if p).casefold()


def _oper_ko(s):
    return {"up": "연결됨", "down": "연결 안 됨", "dormant": "대기", "lowerlayerdown": "연결 안 됨"}.get(s, s or "")


def _net_suffix(nets, default):
    if nets:
        if any(n["wireless"] for n in nets):
            return "Wi-Fi"
        if any(n["devtype"] == "wwan" for n in nets):
            return "모바일 광대역"
        return "이더넷"
    return default


# ── 빠른 변화 감지 (GUdev 가 없을 때 몇 초마다) ──────────────
_SIG_DIRS = ("/bus/pci/devices", "/bus/usb/devices", "/bus/hdaudio/devices", "/class/net", "/class/block",
             "/class/input", "/class/sound", "/class/drm", "/class/power_supply", "/class/bluetooth",
             "/class/video4linux", "/class/rfkill", "/class/tpm")


def signature():
    """장치를 꽂고 뽑거나 드라이버가 붙고 떨어지면 바뀌는 값 — 목록·링크만 읽어 가볍다"""
    parts = []
    for rel in _SIG_DIRS:
        names = ls(SYS + rel)
        parts.append(tuple(names))
        if rel in ("/bus/pci/devices", "/bus/usb/devices", "/bus/hdaudio/devices"):
            parts.append(tuple(link(f"{SYS}{rel}/{n}/driver") for n in names))
        elif rel == "/class/drm":
            parts.append(tuple(rd(f"{SYS}{rel}/{n}/status") for n in names if "-" in n))
        elif rel == "/class/rfkill":
            parts.append(tuple(rd(f"{SYS}{rel}/{n}/soft") + rd(f"{SYS}{rel}/{n}/hard") for n in names))
    return hash(tuple(parts))


if __name__ == "__main__":                     # 시험: python3 -m sekaiadmin.devinfo [--all] [-v] [--show 글자]
    snap = Collector().scan()
    show_all = "--all" in sys.argv
    if "--show" in sys.argv:
        q = sys.argv[sys.argv.index("--show") + 1].casefold()
        for d in snap.devs:
            if q in d.search:
                print(d.as_text(), "\n  동작:", d.actions, "· 부모:", d.parent, "· 커널 이름:", d.kname, "\n")
        sys.exit(0)
    print(f"커널 기록: {snap.klog} · 장치 {len(snap.devs)}개 · 문제 {len(snap.problems)}개")
    for cat, title, _i in CATEGORIES:
        items = [d for d in snap.devs if d.cat == cat and (show_all or not d.hidden)]
        if not items:
            continue
        print(f"\n{title}")
        for d in sorted(items, key=lambda x: (x.order or (), x.name.casefold())):
            mark = {"warn": "⚠ ", "off": "↓ ", "info": "i "}.get(d.level, "  ")
            extra = f"  [{d.suffix}]" if d.suffix else ""
            hid = "  (숨김)" if d.hidden else ""
            drv = ",".join(x[0] for x in d.drivers)
            print(f"  {mark}{d.name}{extra}{hid}  — {drv or '-'}  {d.state}")
            if d.level in ("warn", "off") or "-v" in sys.argv:
                print(f"       {d.title} {d.detail}")
                for h in d.hints:
                    print(f"       · {h}")
