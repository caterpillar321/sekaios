"""프린터 — CUPS 위에 올린 설정 페이지 (윈도우 11 의 "프린터 및 스캐너").

목록 · 상태 · 기본 프린터 · 인쇄 대기열 · 프린터 추가(드라이버 없이) · 제거 · 테스트 페이지.

읽기(프린터·작업 목록)는 CUPS 에 IPP 로 직접 묻는다 — 로컬 소켓에 요청 하나가 몇 ms 라 몇 초마다 물어도 가볍다.
  lpstat -p 는 부를 때마다 네트워크 프린터를 1초씩 찾고(DNS-SD) 문서 이름도 알려 주지 않는다.
바꾸는 일은 CUPS 명령(lpadmin · lpoptions · cancel · lp)으로. CUPS 관리 권한(@SYSTEM = root·lpadmin 그룹,
  데비안 기본)이 있으면 그대로 부르고 — 로컬 소켓의 PeerCred 로 암호 없이 통한다 — 없으면
  pkexec + /usr/libexec/sekai/sekai-printers 로.
새 프린터는 ippfind(DNS-SD)로 찾는다 — IPP Everywhere·AirPrint 네트워크 프린터와 ipp-usb 가 알리는 USB 프린터.
  드라이버가 필요한 옛 프린터는 system-config-printer 로 넘긴다.
기본 프린터는 윈도우처럼 사용자마다(lpoptions -d, ~/.cups/lpoptions). 기본이 하나도 없을 때 추가한 프린터는
  시스템 기본(lpadmin -d)으로도 정해 다른 계정에도 기본이 생긴다.
페이지가 보이는 동안(또는 인쇄 대기열 창이 열려 있는 동안)만 몇 초마다 새로 읽는다.
"""
import getpass
import grp
import http.client
import os
import pwd
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..util import dbg, failure_reason, run, run_async, spawn
from ..widgets import Page, button, icon_image, info, row

HELPER = "/usr/libexec/sekai/sekai-printers"
TESTPAGE = "/usr/share/cups/data/testprint"          # CUPS 의 테스트 페이지 (system-config-printer 도 이것을 보낸다)
CUPS_SOCK = "/run/cups/cups.sock"
POLL_SECS = 3
SCAN_SECS = 5
PROBE_SECS = 30                                      # 네트워크 프린터가 켜져 있는지 다시 볼 간격
SVC_OP = "//service"                                 # ops 의 "인쇄 서비스 켜는 중" 키 (프린터 이름엔 / 가 없다)
PRINTER_ICONS = ["printer", "printer-network", "preferences-devices-printer", "printer-symbolic"]
# system-config-printer 의 세션 D-Bus 서비스 (system-config-printer-common) — 특정 프린터의 속성 창·새 프린터 창
SCP_BUS = "org.fedoraproject.Config.Printing"
SCP_PATH = "/org/fedoraproject/Config/Printing"


# ── IPP (CUPS 에 직접 묻기) ──────────────────────────────────
OP_GET_JOBS, OP_CUPS_GET_DEFAULT, OP_CUPS_GET_PRINTERS = 0x000A, 0x4001, 0x4002
T_INT, T_BOOL, T_ENUM = 0x21, 0x22, 0x23
T_TEXT_LANG, T_NAME_LANG, T_BEG_COL, T_END_COL = 0x35, 0x36, 0x34, 0x37
T_NAME, T_KEYWORD, T_URI, T_CHARSET, T_LANG = 0x42, 0x44, 0x45, 0x47, 0x48
G_PRINTER, G_JOB, G_END = 0x04, 0x02, 0x03
PRINTER_ATTRS = ("printer-name", "printer-info", "printer-location", "printer-make-and-model",
                 "printer-state", "printer-state-reasons", "printer-state-message",
                 "printer-is-accepting-jobs", "printer-is-temporary", "printer-type", "device-uri")
JOB_ATTRS = ("job-id", "job-name", "job-originating-user-name", "job-state", "job-printer-uri",
             "job-k-octets", "time-at-creation", "job-printer-state-message")
CUPS_PRINTER_CLASS = 0x0001


class CupsDown(Exception):
    """CUPS 에 닿지 못했다 (꺼져 있거나 요청을 받지 못했다)"""


class _UnixHTTP(http.client.HTTPConnection):
    """cupsd 의 로컬 소켓으로 가는 HTTP (Host: localhost)"""

    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self._path)
        except OSError:
            s.close()
            raise
        self.sock = s


def _server():
    """(소켓 경로, None) 또는 (호스트, 포트) — libcups 와 같은 순서 (CUPS_SERVER → 로컬 소켓 → localhost:631)"""
    env = os.environ.get("CUPS_SERVER", "").strip().split("/version=")[0]
    if env.startswith("/"):
        return env, None
    if env:
        host, _, port = env.rpartition(":") if env.count(":") == 1 else (env, "", "")
        return host, int(port) if port.isdigit() else 631
    if os.path.exists(CUPS_SOCK):
        return CUPS_SOCK, None
    return "localhost", 631


def _attr(tag, name, *values):
    """IPP 속성 하나 (값이 여럿이면 둘째부터 이름 길이 0)"""
    out = bytearray()
    for i, v in enumerate(values):
        n = name.encode() if i == 0 else b""
        if tag in (T_INT, T_ENUM):
            b = struct.pack(">i", v)
        elif tag == T_BOOL:
            b = b"\x01" if v else b"\x00"
        else:
            b = str(v).encode("utf-8")
        out += struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(b)) + b
    return bytes(out)


def _request(op, attrs=b""):
    # charset · natural-language 가 맨 앞이어야 한다 (RFC 8011)
    return (struct.pack(">BBHI", 2, 0, op, 1) + b"\x01"
            + _attr(T_CHARSET, "attributes-charset", "utf-8")
            + _attr(T_LANG, "attributes-natural-language", "en") + attrs + bytes([G_END]))


def _value(tag, raw):
    if tag in (T_INT, T_ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == T_BOOL and len(raw) == 1:
        return raw != b"\x00"
    if tag in (T_TEXT_LANG, T_NAME_LANG):
        try:
            ll = struct.unpack_from(">H", raw, 0)[0]
            tl = struct.unpack_from(">H", raw, 2 + ll)[0]
            return raw[4 + ll:4 + ll + tl].decode("utf-8", "replace")
        except struct.error:
            return ""
    if 0x40 <= tag <= 0x4F:
        return raw.decode("utf-8", "replace")
    if 0x10 <= tag <= 0x1F:                      # 대역 밖 값 (no-value, unknown…)
        return None
    return raw


def _decode(data):
    """IPP 응답 → (상태 코드, [(그룹 태그, {이름: [값…]})…]). 컬렉션은 쓰지 않으니 건너뛴다"""
    if len(data) < 8:
        raise CupsDown("IPP 응답이 너무 짧습니다")
    status = struct.unpack_from(">H", data, 2)[0]
    groups, cur, last, depth, pos, n = [], None, None, 0, 8, len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        if tag == G_END:
            break
        if tag < 0x10:
            cur, last = {}, None
            groups.append((tag, cur))
            continue
        if pos + 2 > n:
            break
        nlen = struct.unpack_from(">H", data, pos)[0]
        name = data[pos + 2:pos + 2 + nlen].decode("utf-8", "replace")
        pos += 2 + nlen
        if pos + 2 > n:
            break
        vlen = struct.unpack_from(">H", data, pos)[0]
        raw = data[pos + 2:pos + 2 + vlen]
        pos += 2 + vlen
        if depth:
            depth += 1 if tag == T_BEG_COL else -1 if tag == T_END_COL else 0
            continue
        if tag == T_BEG_COL:
            depth, val = 1, None
        else:
            val = _value(tag, raw)
        if cur is None:
            continue
        if nlen:
            last = name
            cur.setdefault(name, []).append(val)
        elif last is not None:
            cur[last].append(val)
    return status, groups


def _ipp(op, attrs=b"", timeout=5):
    where, port = _server()
    conn = _UnixHTTP(where, timeout) if port is None else \
        http.client.HTTPConnection(where, port, timeout=timeout)
    try:
        conn.request("POST", "/", _request(op, attrs), {"Content-Type": "application/ipp"})
        resp = conn.getresponse()
        data = resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise CupsDown(str(e)) from None
    finally:
        conn.close()
    if resp.status != 200:
        raise CupsDown(f"HTTP {resp.status}")
    return _decode(data)


def _one(d, key, default=None):
    v = d.get(key)
    return v[0] if v and v[0] is not None else default


def _lpoptions_default():
    """사용자가 정한 기본 프린터 (~/.cups/lpoptions, 없으면 /etc/cups/lpoptions) — libcups 와 같은 순서"""
    for path in (os.path.expanduser("~/.cups/lpoptions"), "/etc/cups/lpoptions"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    w = line.split()
                    if len(w) >= 2 and w[0].lower() == "default":
                        return w[1].split("/", 1)[0]
        except OSError:
            continue
    return None


def snapshot(me):
    """CUPS 의 지금 상태 — 작업 스레드에서. 닿지 못하면 CupsDown.
    {"printers": [...], "names": 모든 대기열 이름, "default": 이름|None, "jobs": [...]}"""
    user = _attr(T_NAME, "requesting-user-name", me)
    _st, groups = _ipp(OP_CUPS_GET_PRINTERS, user + _attr(T_KEYWORD, "requested-attributes", *PRINTER_ATTRS))
    printers, names = [], set()
    for tag, g in groups:
        name = _one(g, "printer-name")
        if tag != G_PRINTER or not name:
            continue
        names.add(name.lower())
        # 인쇄 창이 잠깐 만든 네트워크 프린터(1분 뒤 사라진다)와 클래스는 목록에 두지 않는다
        if _one(g, "printer-is-temporary", False) or _one(g, "printer-type", 0) & CUPS_PRINTER_CLASS:
            continue
        printers.append({
            "name": name,
            "info": _one(g, "printer-info", "") or "",
            "location": _one(g, "printer-location", "") or "",
            "model": _one(g, "printer-make-and-model", "") or "",
            "state": _one(g, "printer-state", 3),
            "reasons": tuple(r for r in g.get("printer-state-reasons", ()) if isinstance(r, str)),
            "message": (_one(g, "printer-state-message", "") or "").strip(),
            "accepting": _one(g, "printer-is-accepting-jobs", True),
            "uri": _one(g, "device-uri", "") or "",
        })
    printers.sort(key=lambda p: (p["info"] or p["name"]).casefold())

    default = None
    user_default = _lpoptions_default()
    if user_default and any(p["name"].lower() == user_default.lower() for p in printers):
        default = next(p["name"] for p in printers if p["name"].lower() == user_default.lower())
    else:
        st, groups = _ipp(OP_CUPS_GET_DEFAULT, _attr(T_KEYWORD, "requested-attributes", "printer-name"))
        if st < 0x0100:
            default = next((_one(g, "printer-name") for t, g in groups if t == G_PRINTER), None)

    # 모든 프린터의 끝나지 않은 작업. requesting-user-name 이 있어야 내 문서의 이름이 보인다
    #   (JobPrivateValues — 남의 문서 이름은 관리자에게만)
    _st, groups = _ipp(OP_GET_JOBS, _attr(T_URI, "printer-uri", "ipp://localhost/") + user
                       + _attr(T_KEYWORD, "which-jobs", "not-completed")
                       + _attr(T_KEYWORD, "requested-attributes", *JOB_ATTRS))
    jobs = []
    for tag, g in groups:
        jid = _one(g, "job-id")
        if tag != G_JOB or not jid:
            continue
        puri = _one(g, "job-printer-uri", "") or ""
        jobs.append({
            "id": jid,
            "printer": urllib.parse.unquote(puri.rstrip("/").rsplit("/", 1)[-1]),
            "title": (_one(g, "job-name", "") or "").strip(),
            "user": _one(g, "job-originating-user-name"),
            "state": _one(g, "job-state", 3),
            "kb": _one(g, "job-k-octets", 0) or 0,
            "time": _one(g, "time-at-creation", 0) or 0,
            "message": (_one(g, "job-printer-state-message", "") or "").strip(),
        })
    jobs.sort(key=lambda j: j["id"])
    return {"printers": printers, "names": names, "default": default, "jobs": jobs}


def _me():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return getpass.getuser()


def _installed():
    return bool(shutil.which("lpadmin")) and (os.path.exists("/usr/sbin/cupsd") or
                                              bool(os.environ.get("CUPS_SERVER")))


def _can_admin():
    """CUPS 관리 권한이 있나 — cupsd 는 프로세스가 아니라 그룹 목록(/etc/group)으로 본다
    (데비안 cups-files.conf: SystemGroup root lpadmin). 로그인 뒤 그룹에 넣었어도 된다"""
    if os.geteuid() == 0:
        return True
    try:
        me = pwd.getpwuid(os.getuid())
    except KeyError:
        return False
    for g in ("lpadmin", "root"):
        try:
            gr = grp.getgrnam(g)
        except KeyError:
            continue
        if me.pw_name in gr.gr_mem or me.pw_gid == gr.gr_gid:
            return True
    return False


# ── 새 프린터 찾기 ───────────────────────────────────────────
# ippfind 가 찾은 서비스마다 printf 로 한 줄 — 칸은 \x1f 로 가른다 (이름에 탭·쉼표가 들어갈 수 있다)
_FIND_KEYS = ("{service_scheme}", "{service_uri}", "{service_name}", "{service_hostname}",
              "{txt_ty}", "{txt_pdl}", "{txt_URF}", "{txt_UUID}", "{txt_note}")
_DRIVERLESS_PDL = ("image/pwg-raster", "image/urf", "application/pdf")


def _ippfind_cmd(secs=SCAN_SECS):
    fmt = "\x1f".join(["%s"] * len(_FIND_KEYS)) + "\\n"
    return ["ippfind", "-T", str(secs), "_ipp._tcp", "_ipps._tcp",
            "--exec", "printf", fmt, *_FIND_KEYS, ";"]


def _parse_ippfind(text):
    seen = {}
    for line in text.splitlines():
        f = line.split("\x1f")
        if len(f) != len(_FIND_KEYS):
            continue
        scheme, uri, name, host, ty, pdl, urf, uuid, note = (x.strip() for x in f)
        if scheme not in ("ipp", "ipps") or not uri.startswith(scheme + "://"):
            continue
        local = host.lower() in ("localhost", "localhost.local") or host.startswith("127.")
        # AirPrint(URF) 이나 IPP Everywhere(PWG 래스터) — cupsd 가 드라이버 없이(-m everywhere) 만들 수 있다
        driverless = bool(urf and urf.lower() != "none") or any(t in pdl.lower() for t in _DRIVERLESS_PDL)
        d = {"uri": uri, "name": name or ty or host, "model": ty or name, "uuid": uuid.lower(),
             "host": host, "local": local, "driverless": driverless, "location": note,
             "scheme": scheme, "devid": ""}
        key = (d["uuid"] or d["name"].casefold(), local)
        old = seen.get(key)
        # 같은 프린터가 ipp · ipps 로 둘 다 알린다 — ipp 를 쓴다. ipps 는 프린터의 자체 서명 인증서가
        #   (펌웨어 업데이트·초기화로) 바뀌면 CUPS 가 믿지 않아 인쇄가 막힌다
        if old is None or (old["scheme"] == "ipps" and scheme == "ipp"):
            seen[key] = d
    return list(seen.values())


def _parse_lpinfo(text):
    """lpinfo -l -v 출력 → [{"uri", "class", "info", "make-and-model", "device-id", "location"}]"""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^Device: uri = (.*)$", line)
        if m:
            cur = {"uri": m.group(1).strip()}
            out.append(cur)
            continue
        m = re.match(r"^\s+([a-z-]+) = ?(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2).strip()
    return out


def _squash(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def discover(admin):
    """(찾은 프린터 목록, 안내 한 줄|None) — 작업 스레드에서 (SCAN_SECS 초 넘게 걸린다).
    USB 로만 쓰는 옛 프린터(IPP-over-USB 가 없는 것)는 lpinfo 로만 보여서 CUPS 관리 권한이 있을 때만"""
    box = {}
    t = None
    if admin and shutil.which("lpinfo"):
        # --timeout 과 --include-schemes 는 -v 앞에 와야 먹는다
        t = threading.Thread(target=lambda: box.update(out=run(
            ["env", "LC_ALL=C", "lpinfo", "-l", "--include-schemes", "usb", "--timeout", str(SCAN_SECS), "-v"],
            timeout=SCAN_SECS + 20)), daemon=True)
        t.start()
    found, note = [], None
    if not shutil.which("ippfind"):
        note = "네트워크 프린터를 찾는 도구(ippfind)가 없어 USB 프린터만 찾았습니다."
    else:
        try:
            r = subprocess.run(_ippfind_cmd(), capture_output=True, text=True, timeout=SCAN_SECS + 20,
                               env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r is not None and r.returncode in (0, 1):     # 1 = 하나도 못 찾음
            found = _parse_ippfind(r.stdout)
        else:
            dbg("ippfind 실패", r and r.returncode, r and r.stderr.strip())
            note = ("네트워크 프린터를 찾지 못했습니다 — 네트워크 검색 서비스(avahi-daemon)가 "
                    "꺼져 있거나 설치되어 있지 않습니다.")
    if t is not None:
        t.join(SCAN_SECS + 25)
        ippusb = [_squash(f["model"]) for f in found if f["local"]]
        for d in _parse_lpinfo(box.get("out", "")):
            if not d["uri"].startswith("usb://"):
                continue
            model = d.get("make-and-model") or d.get("info") or ""
            sq = _squash(model)
            # IPP-over-USB 프린터는 ipp-usb 로 이미 나왔다 (그쪽이 드라이버 없이 된다)
            if sq and any(sq in m or m in sq for m in ippusb if m):
                continue
            found.append({"uri": d["uri"], "name": d.get("info") or model or "USB 프린터", "model": model,
                          "uuid": "", "host": "", "local": True, "driverless": False,
                          "location": "", "scheme": "usb", "devid": d.get("device-id", "")})
    found.sort(key=lambda f: (not f["driverless"], f["name"].casefold()))
    return found, note


# ── 보여 주는 말 ─────────────────────────────────────────────
# (printer-state-reasons 키워드에서 -error/-warning/-report 를 뗀 것, 문구, 문제인가) — 앞의 것이 먼저 보인다
REASONS = (
    ("offline", "오프라인", True),
    ("shutdown", "꺼져 있음", True),
    ("media-jam", "용지 걸림", True),
    ("media-empty", "용지 없음", True),
    ("media-needed", "용지를 넣어 주세요", True),
    ("input-tray-missing", "용지함이 빠져 있음", True),
    ("door-open", "덮개가 열려 있음", True),
    ("cover-open", "덮개가 열려 있음", True),
    ("interlock-open", "덮개가 열려 있음", True),
    ("output-area-full", "출력 용지함이 가득 참", True),
    ("toner-empty", "토너 없음", True),
    ("marker-supply-empty", "잉크·토너 없음", True),
    ("marker-waste-full", "폐잉크통이 가득 참", True),
    ("cups-missing-filter", "필요한 드라이버(필터)가 없음", True),
    ("cups-insecure-filter", "드라이버 설치에 문제가 있음", True),
    ("spool-area-full", "인쇄 대기열 공간이 가득 참", True),
    ("connecting-to-device", "프린터에 연결하는 중…", False),
    ("toner-low", "토너 부족", False),
    ("marker-supply-low", "잉크·토너 부족", False),
)
JOB_STATES = {3: "대기 중", 4: "보류됨", 5: "인쇄 중", 6: "멈춤", 7: "취소됨", 8: "중단됨", 9: "완료"}
# CUPS 명령(LC_ALL=C)의 오류 → 사람이 읽을 말 (소문자로 찾는다)
CUPS_ERRORS = (
    ("unable to create ppd", "이 프린터는 드라이버 없이 쓸 수 없습니다 — ‘직접 추가…’로 드라이버를 골라 주세요"),
    ("does not support required", "이 프린터는 드라이버 없이 쓸 수 없습니다 — ‘직접 추가…’로 드라이버를 골라 주세요"),
    ("requires an ipp connection", "이 프린터는 드라이버 없이 쓸 수 없습니다 — ‘직접 추가…’로 드라이버를 골라 주세요"),
    ("couldn't resolve", "프린터를 찾지 못했습니다 — 프린터가 켜져 있는지 확인하세요"),
    ("name or service not known", "프린터 주소를 찾지 못했습니다 — 프린터가 켜져 있고 같은 네트워크에 있는지 확인하세요"),
    ("unable to connect to", "프린터에 연결하지 못했습니다 — 프린터가 켜져 있고 같은 네트워크에 있는지 확인하세요"),
    ("unknown printer", "없는 프린터입니다"),
    ("does not exist", "없는 프린터입니다"),
    ("scheduler is not running", "인쇄 서비스(CUPS)가 꺼져 있습니다"),
    ("unable to connect to server", "인쇄 서비스(CUPS)가 꺼져 있습니다"),
)


def _denied(text):
    low = (text or "").lower()
    return any(k in low for k in ("forbidden", "unauthorized", "not authorized", "not-authorized"))


def _why(out, err, fallback="실패했습니다"):
    text = ((err or "").strip() or (out or "").strip())
    low = text.lower()
    for key, msg in CUPS_ERRORS:
        if key in low:
            return msg
    if "dismissed" in low or "not authorized" in low:
        return failure_reason(text)              # pkexec — 인증 창을 닫았거나 인증하지 못했다
    if _denied(text):
        return "CUPS 관리 권한이 없습니다"
    line = text.splitlines()[-1] if text else ""
    return re.sub(r"^[a-z]+: ", "", line) or fallback


def _status(p, njobs=0, reachable=None):
    """(한 줄 상태, 문제 있음?)"""
    base = [re.sub(r"-(error|warning|report)$", "", r) for r in p["reasons"] if r and r != "none"]
    hits = [(text, bad) for key, text, bad in REASONS if key in base]
    bad = [t for t, b in hits if b]
    soft = [t for t, b in hits if not b]
    problem = False
    if p["state"] == 5:
        s, problem = "일시 중지됨", True
    elif bad:
        s, problem = bad[0], True
    elif p["state"] == 4:
        s = soft[0] if soft and soft[0].endswith("…") else "인쇄 중"
    elif reachable is False:
        s, problem = "오프라인", True
    else:
        s = "대기 중"
    if not p["accepting"]:
        s += " · 새 작업을 받지 않음"
        problem = True
    if njobs:
        s += f" · 문서 {njobs}개"
    warn = [t for t in soft if not t.endswith("…")]
    if warn:
        s += " · " + warn[0]
    return s, problem


def _title(p):
    return p["info"] or p["name"]


def _conn_text(uri):
    scheme = uri.split(":", 1)[0].lower() if ":" in uri else ""
    try:
        u = urllib.parse.urlsplit(uri)
        host, port = u.hostname or "", u.port
    except ValueError:
        host, port = "", None
    if scheme in ("ipp", "ipps", "http", "https"):
        if host in ("localhost", "127.0.0.1", "::1"):
            return "USB (IPP over USB)" if port and 60000 <= port < 60100 else "이 PC"
        return f"네트워크 · {host}" + (" (암호화)" if scheme in ("ipps", "https") else "")
    if scheme == "dnssd":
        svc = urllib.parse.unquote(uri[8:].split("/", 1)[0]).split("._", 1)[0]
        return f"네트워크 · {svc}"
    if scheme == "usb":
        return "USB"
    if scheme in ("socket", "lpd"):
        return f"네트워크 · {host} (옛 방식)"
    if scheme == "smb":
        return f"Windows 공유 프린터 · {host}"
    if scheme == "cups-pdf":
        return "가상 프린터 — PDF 파일로 저장 (홈 폴더의 PDF 폴더)"
    if scheme == "implicitclass":
        return "네트워크 (cups-browsed 가 자동으로 추가)"
    return scheme or "알 수 없음"


def _hpp(uri):
    """(호스트, 포트, 경로) — ipp 와 ipps 는 같은 프린터로 본다"""
    try:
        u = urllib.parse.urlsplit(uri)
        port = u.port or 631
    except ValueError:
        return None
    host = (u.hostname or "").lower().rstrip(".")
    return (host, port, u.path.rstrip("/") or "/") if host else None


def _added_as(f, printers):
    """찾은 프린터가 이미 대기열로 있으면 그 프린터"""
    for p in printers:
        dev = p["uri"]
        if f["uri"] == dev or (f["uuid"] and f["uuid"] in dev.lower()):
            return p
        if dev.startswith("dnssd://"):
            svc = urllib.parse.unquote(dev[8:].split("/", 1)[0]).split("._", 1)[0]
            if svc.casefold() == f["name"].casefold():
                return p
        elif f["scheme"] in ("ipp", "ipps") and dev.startswith(("ipp://", "ipps://")):
            if _hpp(dev) is not None and _hpp(dev) == _hpp(f["uri"]):
                return p
    return None


def _queue_name(model, taken):
    """CUPS 대기열 이름 (영문·숫자·_ — 명령줄·URI 에서 따옴표 없이 쓸 수 있게). CUPS 이름은 대소문자를 가리지 않는다"""
    base = re.sub(r"[^A-Za-z0-9]+", "_", model or "").strip("_")[:48] or "Printer"
    low = {t.lower() for t in taken}
    name, n = base, 2
    while name.lower() in low:
        name = f"{base}_{n}"
        n += 1
    return name


def _probe(uri):
    """네트워크 프린터가 켜져 있나 (TCP 연결) — 모르면 None"""
    ports = {"ipp": 631, "ipps": 631, "http": 80, "https": 443, "socket": 9100, "lpd": 515}
    try:
        u = urllib.parse.urlsplit(uri)
        host, port = u.hostname, u.port or ports[u.scheme]
    except (ValueError, KeyError):
        return None
    if not host:
        return None
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except OSError:
        return False


def _when(ts):
    if not ts:
        return ""
    t = time.localtime(ts)
    hm = f"{'오전' if t.tm_hour < 12 else '오후'} {t.tm_hour % 12 or 12}:{t.tm_min:02d}"
    return hm if time.localtime().tm_yday == t.tm_yday and time.localtime().tm_year == t.tm_year \
        else f"{t.tm_mon}월 {t.tm_mday}일 {hm}"


def _size(kb):
    return f"{kb} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"


def _forget_user_default(name):
    """지운 프린터가 내 기본 프린터였으면 ~/.cups/lpoptions 에서 그 줄을 뺀다 — 남겨 두면 CUPS 가
    기본 프린터를 찾느라 네트워크를 뒤지고(1초) 결국 기본이 없다고 한다"""
    path = os.path.expanduser("~/.cups/lpoptions")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    keep = [ln for ln in lines
            if not (len(ln.split()) >= 2 and ln.split()[0].lower() in ("default", "dest")
                    and ln.split()[1].split("/", 1)[0].lower() == name.lower())]
    if keep == lines:
        return
    try:
        with open(path + ".sekai-tmp", "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(path + ".sekai-tmp", path)
    except OSError as e:
        dbg("lpoptions 정리 실패", e)


# ── 위젯 도우미 ──────────────────────────────────────────────
def _notice(text=""):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def _reveal(w, on):
    """no_show_all 인 위젯 보이기/숨기기 (안쪽에서 따로 no_show_all 인 것은 그대로 숨어 있다)"""
    if on and not w.get_visible():
        w.show()
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                c.show_all()
    elif not on and w.get_visible():
        w.hide()


def _clear(c):
    for w in c.get_children():
        c.remove(w)
        w.destroy()


def _sect(parent, title=None, hidden=False):
    if title:
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.get_style_context().add_class("section-title")
        parent.pack_start(lbl, False, False, 0)
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    lb.get_style_context().add_class("section")
    lb.set_no_show_all(hidden)
    parent.pack_start(lb, False, False, 0)
    return lb


def _busy_box(text):
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    sp = Gtk.Spinner()
    sp.start()
    b.pack_start(sp, False, False, 0)
    b.pack_start(info(text), False, False, 0)
    return b


def _parent(w):
    top = w.get_toplevel()
    return top if isinstance(top, Gtk.Window) and top.is_toplevel() else None


def _confirm(parent, text, sub, ok_label, on_ok):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                          buttons=Gtk.ButtonsType.NONE, text=text)
    d.format_secondary_text(sub)
    d.add_buttons("취소", Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
    d.set_default_response(Gtk.ResponseType.CANCEL)

    def responded(dlg, resp):
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            on_ok()
    d.connect("response", responded)
    d.show_all()


# ── 인쇄 대기열 창 ───────────────────────────────────────────
class _Queue:
    """한 프린터의 인쇄 대기열 (윈도우의 "인쇄 대기열 열기"). 내용은 페이지가 새로 읽을 때마다 따라간다"""

    def __init__(self, page, name):
        self.page, self.name, self.drawn = page, name, None
        p = page.printer(name)
        self.d = Gtk.Dialog(title=f"{_title(p) if p else name} — 인쇄 대기열",
                            transient_for=_parent(page.p), modal=False)
        self.d.set_default_size(620, 420)
        self.d.add_button("닫기", Gtk.ResponseType.CLOSE)
        self.d.connect("response", lambda *_: self.close())
        area = self.d.get_content_area()
        area.set_spacing(10)
        area.set_border_width(14)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.state = Gtk.Label(xalign=0)
        self.state.get_style_context().add_class("row-sub")
        self.state.set_line_wrap(True)
        top.pack_start(self.state, True, True, 0)
        self.pause_btn = button("일시 중지", self._toggle_pause)
        top.pack_start(self.pause_btn, False, False, 0)
        self.all_btn = button("모두 취소", self._cancel_all)
        top.pack_start(self.all_btn, False, False, 0)
        area.pack_start(top, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_min_content_height(260)
        self.lb = Gtk.ListBox()
        self.lb.set_selection_mode(Gtk.SelectionMode.NONE)
        self.lb.get_style_context().add_class("section")
        sc.add(self.lb)
        area.pack_start(sc, True, True, 0)
        self.d.show_all()
        self.update()

    def jobs(self):
        snap = self.page.snap or {}
        return [j for j in snap.get("jobs", ()) if j["printer"].lower() == self.name.lower()]

    def update(self):
        p = self.page.printer(self.name)
        if p is None:                      # 다른 곳에서 지웠다
            self.close()
            return
        jobs = self.jobs()
        busy = self.name in self.page.ops
        text, _bad = _status(p, len(jobs), self.page.reachable(p))
        if p["message"] and p["state"] != 3:
            text += f"\n{p['message']}"
        self.state.set_text(self.page.ops.get(self.name) or text)
        stopped = p["state"] == 5 or not p["accepting"]
        self.pause_btn.set_label("다시 시작" if stopped else "일시 중지")
        self.pause_btn.set_sensitive(not busy)
        self.all_btn.set_sensitive(bool(jobs) and not busy)
        me = self.page.me
        key = tuple((j["id"], j["title"], j["user"], j["state"], j["kb"], j["message"],
                     j["id"] in self.page.cancelling) for j in jobs)
        if key == self.drawn:
            return
        self.drawn = key
        _clear(self.lb)
        if not jobs:
            row(self.lb, "대기 중인 문서가 없습니다", "인쇄한 문서가 여기서 차례를 기다립니다",
                icon=PRINTER_ICONS).show_all()
            return
        for j in jobs:
            mine = j["user"] == me
            title = j["title"] or ("제목 없는 문서" if mine else "다른 사용자의 문서")
            bits = [JOB_STATES.get(j["state"], "알 수 없음")]
            if j["state"] in (5, 6) and j["message"]:
                bits[0] += f" ({j['message']})"
            if not mine:
                bits.append(j["user"] or "다른 사용자")
            if j["kb"]:
                bits.append(_size(j["kb"]))
            if j["time"]:
                bits.append(_when(j["time"]))
            if j["id"] in self.page.cancelling:
                ctl = _busy_box("취소하는 중…")
            else:
                ctl = button("취소", lambda j=j: self.page.cancel(self.name, [j]))
            r = row(self.lb, title, " · ".join(bits),
                    icon=["text-x-generic", "x-office-document", "document"], control=ctl)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            r.show_all()

    def _toggle_pause(self):
        p = self.page.printer(self.name)
        if p is not None:
            self.page.set_paused(self.name, not (p["state"] == 5 or not p["accepting"]))

    def _cancel_all(self):
        jobs = self.jobs()
        if not jobs:
            return
        _confirm(self.d, f"문서 {len(jobs)}개를 모두 취소할까요?",
                 "인쇄 중인 문서도 멈추고 대기열에서 지웁니다.", "모두 취소",
                 lambda: self.page.cancel(self.name, self.jobs()))

    def close(self):
        if self.page.queue is self:
            self.page.queue = None
            self.page.poll_policy()
        self.d.destroy()


# ── 페이지 ───────────────────────────────────────────────────
class PrintersPage:
    def __init__(self, store):
        self.me = _me()
        self.p = Page("프린터", "프린터를 추가하고 기본 프린터와 인쇄 대기열을 관리합니다.")
        self.dead = False
        self.cups = "loading"      # loading | ok | missing | down
        self.snap = None           # 마지막으로 읽은 CUPS 상태 (snapshot)
        self.loading = self.again = False
        self.poll_src = 0
        self.ops = {}              # 프린터 이름(추가 중이면 URI) → 하고 있는 일 문구
        self.cancelling = set()    # 취소하고 있는 작업 번호
        self.open = None           # 자세히 보고 있는 프린터
        self.queue = None          # 열려 있는 인쇄 대기열 창
        self.found, self.scan_note, self.scanning = None, None, False
        self.reach, self.probing = {}, False   # 장치 URI → (켜져 있나, 확인한 때)
        self.drawn = {}            # 목록별 마지막으로 그린 내용 — 같으면 다시 그리지 않는다 (누르던 버튼이 사라지지 않게)
        self.say_src = 0

        # ── 인쇄 서비스 안내 (없음 · 꺼짐) ──
        self.svc = _sect(self.p.box, hidden=True)
        self.svc_btn = button("인쇄 서비스 켜기", self._service_on, cls="accent-btn")
        self.svc_btn.set_no_show_all(True)
        self.svc_row = row(self.svc, " ", " ", icon=PRINTER_ICONS, control=self.svc_btn)

        self.msg = _notice()
        self.msg.set_no_show_all(True)
        self.msg.set_selectable(True)
        self.p.add_widget(self.msg)

        # ── 목록 화면 ──
        self.main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main.set_no_show_all(True)
        self.p.add_widget(self.main)
        s = _sect(self.main)
        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        ctl.pack_start(self.spinner, False, False, 0)
        self.add_btn = button("장치 추가", self.scan, cls="accent-btn")
        self.add_btn.get_style_context().add_class("prn-add")
        ctl.pack_start(self.add_btn, False, False, 0)
        self.add_row = row(s, "프린터 추가", " ", icon=["list-add", "list-add-symbolic"] + PRINTER_ICONS,
                           control=ctl)
        self.found_lb = _sect(self.main, hidden=True)
        self.mine = _sect(self.main, "내 프린터")
        self.mine.connect("row-activated", lambda _lb, r: self.show(getattr(r, "prn", None)))
        row(self.mine, "프린터 목록을 읽는 중…", icon=PRINTER_ICONS)
        s = _sect(self.main, "관련 설정")
        self.tool_btn = button("열기", lambda: self._scp_open())
        self.tool_row = row(s, "인쇄 설정 도구", " ", icon=["system-config-printer", "preferences-system"],
                            control=self.tool_btn)

        # ── 프린터 자세히 (윈도우 11 에서 프린터를 누르면 나오는 화면) ──
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.detail.set_no_show_all(True)
        self.p.add_widget(self.detail)

        self.p.connect("map", lambda *_: self.poll_policy())
        self.p.connect("unmap", lambda *_: self.poll_policy())
        self.p.connect("destroy", self._destroy)
        self._draw()
        self.refresh()

    @property
    def widget(self):
        return self.p

    def _destroy(self, *_):
        if self.dead:
            return
        self.dead = True
        for src in (self.poll_src, self.say_src):
            if src:
                GLib.source_remove(src)
        self.poll_src = self.say_src = 0
        if self.queue is not None:
            self.queue.close()

    # ── 새로 읽기 ──
    def poll_policy(self):
        """보이는 동안이나 인쇄 대기열 창이 열려 있는 동안만 몇 초마다 읽는다"""
        want = not self.dead and (self.p.get_mapped() or self.queue is not None)
        if want and not self.poll_src:
            self.poll_src = GLib.timeout_add_seconds(POLL_SECS, self._tick)
            self.refresh()
        elif not want and self.poll_src:
            GLib.source_remove(self.poll_src)
            self.poll_src = 0

    def _tick(self):
        if self.dead:
            self.poll_src = 0
            return False
        self.refresh()
        return True

    def refresh(self):
        if self.dead:
            return
        if self.loading:
            self.again = True
            return
        self.loading = True
        me = self.me

        def work():
            if not _installed():
                res = ("missing", None)
            else:
                try:
                    res = ("ok", snapshot(me))
                except CupsDown as e:
                    dbg("CUPS 에 닿지 못함", e)
                    res = ("down", None)
                except Exception as e:           # 알 수 없는 응답 — 이번 것만 버린다
                    dbg("CUPS 응답을 읽지 못함", repr(e))
                    res = (None, None)
            GLib.idle_add(self._got, res)
        threading.Thread(target=work, daemon=True).start()

    def _got(self, res):
        self.loading = False
        if self.dead:
            return False
        state, snap = res
        if state is not None:
            self.cups = state
        if snap is not None:
            self.snap = snap
        self._draw()
        self._probe_later()
        if self.again:
            self.again = False
            self.refresh()
        return False

    def _probe_later(self):
        """쉬고 있는 네트워크 프린터가 켜져 있는지 가끔 본다 — CUPS 는 인쇄할 때에야 알아서,
        꺼 둔 프린터도 "대기 중"으로 보인다 (윈도우는 이럴 때 "오프라인")"""
        if self.probing or not self.snap:
            return
        now = time.monotonic()
        uris = [p["uri"] for p in self.snap["printers"]
                if p["state"] == 3 and now - self.reach.get(p["uri"], (None, -1e9))[1] > PROBE_SECS]
        if not uris:
            return
        self.probing = True

        def work():
            res = {u: (_probe(u), time.monotonic()) for u in uris}
            GLib.idle_add(self._probed, res)
        threading.Thread(target=work, daemon=True).start()

    def _probed(self, res):
        self.probing = False
        self.reach.update(res)
        if not self.dead:
            self._draw()
        return False

    def reachable(self, p):
        ok, when = self.reach.get(p["uri"], (None, 0))
        return ok if time.monotonic() - when < PROBE_SECS * 3 else None

    def printer(self, name):
        if not self.snap or not name:
            return None
        return next((p for p in self.snap["printers"] if p["name"].lower() == name.lower()), None)

    def _jobs_of(self, name):
        return [j for j in (self.snap or {}).get("jobs", ()) if j["printer"].lower() == name.lower()]

    # ── 알림 한 줄 ──
    def say(self, text, error=False):
        if self.dead:
            return
        if self.say_src:
            GLib.source_remove(self.say_src)
            self.say_src = 0
        self.msg.set_text(text or "")
        self.msg.set_visible(bool(text))
        if text and not error:                  # 잘 된 소식은 잠깐만

            def hide():
                self.say_src = 0
                self.msg.hide()
                return False
            self.say_src = GLib.timeout_add_seconds(8, hide)

    def _set_busy(self):
        # 일이 도는 동안은 설정 창이 이 페이지를 다시 그리지 않는다 (진행 상태·결과를 잃지 않게)
        self.p.busy = bool(self.ops or self.cancelling)

    # ── 그리기 ──
    def _draw(self):
        if self.dead:
            return
        if self.cups in ("missing", "down"):
            if self.cups == "missing":
                self.svc_row.title_label.set_text("인쇄 기능이 설치되어 있지 않습니다")
                self.svc_row.sub_label.set_text("인쇄 서비스(CUPS)가 없습니다. SekaiOS 업데이트를 받거나 "
                                                "터미널에서 ‘sudo apt install cups’ 로 설치하세요.")
            else:
                self.svc_row.title_label.set_text("인쇄 서비스가 꺼져 있습니다")
                self.svc_row.sub_label.set_text("인쇄 서비스(CUPS)가 실행되고 있지 않아 프린터를 쓸 수 없습니다.")
            _reveal(self.svc, True)
            self.svc_btn.set_visible(self.cups == "down")
            self.svc_btn.set_sensitive(SVC_OP not in self.ops)
            _reveal(self.main, False)
            _reveal(self.detail, False)
            if self.queue is not None:
                self.queue.close()
            self._set_busy()
            return
        _reveal(self.svc, False)
        if self.open is not None and self.cups == "ok" and self.printer(self.open) is None:
            self.open = None                    # 다른 곳에서 지웠다
        _reveal(self.main, self.open is None)
        _reveal(self.detail, self.open is not None)
        if self.snap is not None:
            if self.open is None:
                self._draw_mine()
            else:
                self._draw_detail()
        self._draw_add()
        self._draw_tools()
        if self.queue is not None:
            self.queue.update()
        self._set_busy()

    def _draw_mine(self):
        snap = self.snap
        items = []
        for p in snap["printers"]:
            op = self.ops.get(p["name"])
            text, bad = _status(p, len(self._jobs_of(p["name"])), self.reachable(p))
            if p["name"] == snap["default"]:
                text = "기본 프린터 · " + text
            items.append((p["name"], _title(p), op or text, bad and not op))
        key = tuple(items)
        if self.drawn.get("mine") == key:
            return
        self.drawn["mine"] = key
        _clear(self.mine)
        if not items:
            row(self.mine, "추가된 프린터가 없습니다",
                "위의 ‘장치 추가’로 네트워크·USB 프린터를 찾으세요", icon=PRINTER_ICONS).show_all()
            return
        for name, title, sub, bad in items:
            r = row(self.mine, title, sub, icon=PRINTER_ICONS,
                    control=icon_image(["go-next-symbolic", "pan-end-symbolic"], 16), activatable=True)
            r.prn = name
            r.set_tooltip_text(name)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
            if bad:
                r.sub_label.get_style_context().add_class("prn-problem")
            r.show_all()

    def _draw_add(self):
        admin_hint = "" if _can_admin() else " 추가할 때 관리자 인증을 물을 수 있습니다."
        if self.scanning:
            sub = "같은 네트워크에 연결되어 켜져 있는 프린터와 USB 로 연결한 프린터를 찾고 있습니다…"
        else:
            sub = "네트워크(Wi-Fi·유선)와 USB 프린터를 찾아 드라이버 없이 추가합니다." + admin_hint
        self.add_row.sub_label.set_text(sub)
        self.add_btn.set_label("다시 찾기" if self.found is not None and not self.scanning else "장치 추가")
        self.add_btn.set_sensitive(not self.scanning and self.cups == "ok")
        self.spinner.set_visible(self.scanning)
        (self.spinner.start if self.scanning else self.spinner.stop)()

        printers = self.snap["printers"] if self.snap else []
        items = []
        for f in self.found or ():
            p = _added_as(f, printers)
            items.append((f["uri"], f["name"], f["driverless"], f["local"], f["location"],
                          p["name"] if p else None, self.ops.get(f["uri"])))
        key = (tuple(items), self.scanning, self.found is None, self.scan_note,
               bool(shutil.which("system-config-printer")))
        if self.drawn.get("found") != key:
            self.drawn["found"] = key
            _clear(self.found_lb)
            if self.scan_note:
                row(self.found_lb, "일부만 찾았습니다", self.scan_note,
                    icon=["dialog-warning", "dialog-warning-symbolic"]).show_all()
            if self.found is not None and not self.found and not self.scanning:
                row(self.found_lb, "찾은 프린터가 없습니다",
                    "프린터가 켜져 있고 이 PC 와 같은 네트워크(Wi-Fi·공유기)에 연결되어 있는지 확인하세요. "
                    "USB 프린터는 케이블을 꽂고 켜 두세요.", icon=PRINTER_ICONS).show_all()
            for (uri, name, driverless, local, where, added, op), f in zip(items, self.found or ()):
                kind = "USB" if local else "네트워크"
                if added:
                    sub, ctl = f"{kind} · 이미 추가되어 있습니다", info("추가됨")
                elif op:
                    sub, ctl = f"{kind} · 드라이버 없이 추가하는 중 (프린터에 기능을 묻고 있습니다)", _busy_box(op)
                elif driverless:
                    sub = f"{kind} · 드라이버 없이 쓸 수 있습니다"
                    ctl = button("추가", lambda f=f: self.add(f))
                    ctl.set_sensitive(not any("://" in k for k in self.ops))     # 추가는 한 번에 하나
                else:
                    sub = f"{kind} · 드라이버가 필요합니다"
                    ctl = button("직접 추가…", lambda f=f: self._scp_new(f["uri"], f["devid"]))
                    ctl.set_sensitive(bool(shutil.which("system-config-printer")))
                if where:
                    sub += f" · {where}"
                r = row(self.found_lb, name, sub, icon=PRINTER_ICONS if local else ["printer-network"] + PRINTER_ICONS,
                        control=ctl)
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                r.show_all()
            if self.found is not None and not self.scanning:
                b = button("직접 추가…", lambda: self._scp_open())
                b.set_sensitive(bool(shutil.which("system-config-printer")))
                row(self.found_lb, "원하는 프린터가 목록에 없나요?",
                    "드라이버가 필요한 옛 프린터나 IP 주소로 연결하는 프린터는 인쇄 설정 도구에서 추가합니다.",
                    icon=["list-add", "list-add-symbolic"], control=b).show_all()
        _reveal(self.found_lb, bool(self.found_lb.get_children()))

    def _draw_tools(self):
        has = bool(shutil.which("system-config-printer"))
        self.tool_btn.set_sensitive(has)
        self.tool_row.sub_label.set_text(
            "드라이버가 필요한 프린터 추가, 프린터 공유, 인쇄 서버 설정 (system-config-printer)" if has
            else "system-config-printer 가 설치되어 있지 않습니다.")

    def _draw_detail(self):
        p = self.printer(self.open)
        name = p["name"]
        jobs = self._jobs_of(name)
        is_default = self.snap["default"] == name
        scp = bool(shutil.which("system-config-printer"))
        reach = self.reachable(p)
        key = (tuple(p.items()), is_default, len(jobs), self.ops.get(name), scp, reach)
        if self.drawn.get("detail") == key:
            return
        self.drawn["detail"] = key
        _clear(self.detail)
        op = self.ops.get(name)
        status, bad = _status(p, len(jobs), reach)

        # 머리: ← · 아이콘 · 이름 · 상태
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.get_style_context().add_class("prn-head")
        back = Gtk.Button()
        back.add(icon_image(["go-previous-symbolic", "go-previous"], 16))
        back.set_tooltip_text("모든 프린터")
        back.get_style_context().add_class("prn-back")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", lambda *_: self.show(None))
        head.pack_start(back, False, False, 0)
        head.pack_start(icon_image(PRINTER_ICONS, 48), False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        v.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=_title(p), xalign=0)
        t.get_style_context().add_class("prn-name")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(t, False, False, 0)
        st = Gtk.Label(label=op or (("기본 프린터 · " if is_default else "") + status), xalign=0)
        st.get_style_context().add_class("row-sub")
        if bad and not op:
            st.get_style_context().add_class("prn-problem")
        st.set_line_wrap(True)
        v.pack_start(st, False, False, 0)
        head.pack_start(v, True, True, 0)
        self.detail.pack_start(head, False, False, 0)

        s = _sect(self.detail)
        if is_default:
            ctl = info("기본 프린터입니다")
        else:
            ctl = button("기본값으로 설정", lambda: self.set_default(name))
            ctl.set_sensitive(not op)
        row(s, "기본 프린터", "앱에서 인쇄할 때 먼저 골라지는 프린터입니다 (이 계정에만 적용)",
            icon=["emblem-default", "starred", "emblem-favorite"], control=ctl)
        if p["state"] == 5 or not p["accepting"]:
            b = button("다시 시작", lambda: self.set_paused(name, False), cls="accent-btn")
            b.set_sensitive(not op)
            row(s, "인쇄가 멈춰 있습니다",
                p["message"] or "프린터가 일시 중지되었거나 새 작업을 받지 않습니다. 다시 시작하면 기다리던 문서가 인쇄됩니다.",
                icon=["media-playback-pause", "dialog-warning"], control=b)
        row(s, "인쇄 대기열 열기", f"문서 {len(jobs)}개가 기다리고 있습니다" if jobs else "기다리는 문서가 없습니다",
            icon=["view-list", "view-list-symbolic", "format-justify-left"],
            control=button("열기", lambda: self.open_queue(name)))
        b = button("인쇄", lambda: self.test_page(name))
        b.set_sensitive(not op)
        row(s, "테스트 페이지 인쇄", "프린터가 제대로 인쇄하는지 확인합니다",
            icon=["document-print", "printer"], control=b)
        b = button("열기", lambda: self._scp_props(name))
        b.set_sensitive(scp)
        row(s, "고급 설정", "용지 크기·양면·품질 같은 기본 인쇄 옵션과 드라이버 (system-config-printer)"
            if scp else "system-config-printer 가 설치되어 있지 않습니다.",
            icon=["preferences-system", "document-properties"], control=b)
        b = button("제거", lambda: self.remove(name))
        b.set_sensitive(not op)
        row(s, "프린터 제거", "이 PC 에서 프린터를 지웁니다. 다시 쓰려면 ‘장치 추가’로 새로 추가합니다.",
            icon=["edit-delete", "user-trash"], control=b)

        s = _sect(self.detail, "프린터 정보")
        model = p["model"].replace(" - IPP Everywhere", "").strip()
        if model:
            row(s, "모델", None, control=info(model))
        row(s, "연결", None, control=info(_conn_text(p["uri"])))
        if "IPP Everywhere" in p["model"]:
            row(s, "드라이버", None, control=info("필요 없음 (IPP Everywhere)"))
        if p["location"]:
            row(s, "위치", None, control=info(p["location"]))
        if p["message"]:
            row(s, "프린터가 보낸 메시지", None, control=info(p["message"]))
        row(s, "대기열 이름", "명령줄(lp -d …)이나 다른 프로그램에서 쓰는 이름", control=info(name))
        for w in s.get_children():
            ctl = getattr(w, "control", None)
            if isinstance(ctl, Gtk.Label):
                ctl.set_line_wrap(True)
                ctl.set_max_width_chars(44)
                ctl.set_xalign(1)
        for c in self.detail.get_children():      # self.detail 은 no_show_all — show_all 이 안으로 내려가지 않는다
            c.show_all()

    def show(self, name):
        """프린터 자세히 ↔ 목록"""
        if name is not None and self.printer(name) is None:
            return
        self.open = name
        self.drawn.pop("detail", None)
        self.drawn.pop("mine", None)
        self._draw()
        GLib.idle_add(lambda: (self.p.get_vadjustment().set_value(0), False)[1])

    # ── 명령 실행 ──
    def _admin(self, steps, helper_args, done, direct=None, undo=None):
        """CUPS 관리 명령들을 차례로. steps = [(명령, 꼭 성공해야 하나)].
        관리 권한이 있으면(또는 direct) 직접 — 권한 문제로 막히면 도우미로. 없으면 처음부터 도우미(pkexec).
        undo: 직접 하다 첫 명령이 (권한 말고 다른 이유로) 실패하면 치울 명령. done(성공?, 이유)"""
        if direct is None:
            direct = _can_admin()

        def helper():
            if not os.path.exists(HELPER):
                done(False, "프린터 도우미가 없습니다 — SekaiOS 업데이트를 받아 주세요")
                return
            # 도우미는 실패 이유를 표준 출력에 한 줄로 — 비어 있으면 pkexec 쪽(인증 취소 등)
            run_async(["pkexec", HELPER] + helper_args,
                      lambda ok, out, err: done(ok, "" if ok else (
                          _why(out, "") if out.strip() else failure_reason(err))))

        def step(i):
            if i == len(steps):
                done(True, "")
                return
            cmd, must = steps[i]

            def finished(ok, out, err):
                if ok or not must:
                    step(i + 1)
                elif i == 0 and _denied(err or out):
                    helper()
                else:
                    if i == 0 and undo:
                        run_async(["env", "LC_ALL=C"] + undo, lambda *_: None)
                    done(False, _why(out, err))
            run_async(["env", "LC_ALL=C"] + cmd, finished)

        if direct:
            step(0)
        else:
            helper()

    def _begin(self, key, text):
        self.ops[key] = text
        self._set_busy()
        self._draw()

    def _end(self, key):
        self.ops.pop(key, None)
        if self.dead:
            return False
        self._set_busy()
        self.refresh()
        return True

    # ── 인쇄 서비스 ──
    def _service_on(self):
        self._begin(SVC_OP, "켜는 중…")

        def done(ok, why):
            if not self._end(SVC_OP):
                return
            if ok:
                self.say("인쇄 서비스를 켰습니다")
            else:
                self.say(f"인쇄 서비스를 켜지 못했습니다: {why}", error=True)
        self._admin([], ["service-on"], done, direct=False)

    # ── 찾기 · 추가 ──
    def scan(self):
        if self.scanning or self.dead:
            return
        self.scanning = True
        self.found, self.scan_note = [], None
        self.say(None)
        self._draw()
        admin = _can_admin()

        def work():
            try:
                res = discover(admin)
            except Exception as e:
                dbg("프린터 찾기 실패", repr(e))
                res = ([], "프린터를 찾는 중 오류가 났습니다.")
            GLib.idle_add(self._scanned, res)
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, res):
        self.scanning = False
        if self.dead:
            return False
        self.found, self.scan_note = res
        self._draw()
        return False

    def add(self, f):
        if f["uri"] in self.ops or not self.snap:
            return
        name = _queue_name(f["model"] or f["name"], self.snap["names"])
        desc = re.sub(r"[\x00-\x1f\x7f]", " ", f["name"]).strip()[:120]
        make_default = not self.snap["default"]
        # 내 기본값이 (다른 도구로) 지운 프린터를 가리키면 libcups 는 그것만 찾다 "기본 없음"이 된다 — 새 기본값이 먹게 뺀다
        stale = _lpoptions_default() if make_default else None
        self.say(None)
        self._begin(f["uri"], "추가하는 중…")
        cmd = ["lpadmin", "-p", name, "-v", f["uri"], "-m", "everywhere", "-o", "printer-is-shared=false"]
        if desc:
            cmd += ["-D", desc]
        # -E 는 -p 뒤에 와야 "켜고 작업 받기" (앞에 오면 암호화 옵션이 된다)
        steps = [(cmd + ["-E"], True)]
        if make_default:
            steps.append((["lpadmin", "-d", name], False))

        def done(ok, why):
            if ok and stale:
                _forget_user_default(stale)
            if not self._end(f["uri"]):
                return
            if ok:
                self.say(f"‘{desc or name}’ 프린터를 추가했습니다"
                         + (" — 기본 프린터로 정했습니다" if make_default else ""))
            else:
                self.say(f"‘{desc or name}’ 프린터를 추가하지 못했습니다: {why}", error=True)
        self._admin(steps, ["add", name, f["uri"], desc, "default" if make_default else "keep"], done,
                    undo=["lpadmin", "-x", name])

    # ── 프린터 일 ──
    def set_default(self, name):
        # 사용자마다 (윈도우와 같다) — 관리자 권한이 필요 없다
        self._begin(name, "기본 프린터로 정하는 중…")

        def done(ok, out, err):
            if not self._end(name):
                return
            if ok:
                self.say(f"‘{name}’ 을(를) 기본 프린터로 정했습니다")
            else:
                self.say(f"기본 프린터로 정하지 못했습니다: {_why(out, err)}", error=True)
        run_async(["env", "LC_ALL=C", "lpoptions", "-d", name], done)

    def test_page(self, name):
        if not os.path.exists(TESTPAGE):
            self.say(f"테스트 페이지 파일({TESTPAGE})이 없습니다.", error=True)
            return

        def done(ok, out, err):
            if self.dead:
                return
            if ok:
                self.say("테스트 페이지를 보냈습니다. 인쇄되지 않으면 ‘인쇄 대기열 열기’에서 상태를 확인하세요.")
            else:
                self.say(f"테스트 페이지를 보내지 못했습니다: {_why(out, err)}", error=True)
            self.refresh()
        run_async(["env", "LC_ALL=C", "lp", "-d", name, "-t", "테스트 페이지", TESTPAGE], done)

    def remove(self, name):
        p = self.printer(name)
        if p is None or name in self.ops:
            return
        jobs = self._jobs_of(name)
        _confirm(_parent(self.p), f"‘{_title(p)}’ 프린터를 제거할까요?",
                 (f"기다리던 문서 {len(jobs)}개도 취소됩니다. " if jobs else "")
                 + "다시 쓰려면 ‘장치 추가’로 새로 추가해야 합니다.", "제거",
                 lambda: self._do_remove(name, _title(p)))

    def _do_remove(self, name, title):
        if name in self.ops:
            return
        self._begin(name, "제거하는 중…")

        def done(ok, why):
            if ok:
                _forget_user_default(name)
                if self.open == name:
                    self.open = None
            if not self._end(name):
                return
            if ok:
                self.say(f"‘{title}’ 프린터를 제거했습니다")
            else:
                self.say(f"‘{title}’ 프린터를 제거하지 못했습니다: {why}", error=True)
        self._admin([(["lpadmin", "-x", name], True)], ["remove", name], done)

    def set_paused(self, name, pause):
        if name in self.ops:
            return
        self._begin(name, "일시 중지하는 중…" if pause else "다시 시작하는 중…")
        steps = [(["cupsdisable", name], True)] if pause else \
            [(["cupsenable", name], True), (["cupsaccept", name], True)]

        def done(ok, why):
            if self._end(name) and not ok:
                self.say(f"{'일시 중지하지' if pause else '다시 시작하지'} 못했습니다: {why}", error=True)
        self._admin(steps, ["pause" if pause else "resume", name], done)

    def cancel(self, name, jobs):
        jobs = [j for j in jobs if j["id"] not in self.cancelling]
        if not jobs:
            return
        ids = [str(j["id"]) for j in jobs]
        mine = all(j["user"] == self.me for j in jobs)
        self.cancelling.update(j["id"] for j in jobs)
        self._set_busy()
        self._draw()

        def done(ok, why):
            self.cancelling.difference_update(j["id"] for j in jobs)
            if self.dead:
                return
            self._set_busy()
            if not ok:
                self.say(f"인쇄 작업을 취소하지 못했습니다: {why}", error=True)
            self.refresh()
        # 내 문서는 누구나 취소할 수 있다. 남의 문서는 관리 권한(없으면 도우미)으로
        self._admin([(["cancel"] + ids, True)], ["cancel"] + ids, done, direct=mine or None)

    def open_queue(self, name):
        if self.printer(name) is None:
            return
        if self.queue is not None:
            if self.queue.name.lower() == name.lower():
                self.queue.d.present()
                return
            self.queue.close()
        self.queue = _Queue(self, name)
        self.poll_policy()

    # ── system-config-printer ──
    def _scp_open(self):
        if not spawn(["system-config-printer"]):
            self.say("인쇄 설정 도구(system-config-printer)를 열지 못했습니다.", error=True)

    def _scp_call(self, path, iface, method, params, reply_type, then):
        """system-config-printer 의 D-Bus 서비스 (없으면 자동으로 뜬다). 실패하면 도구의 창만 연다"""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            dbg("세션 버스 없음", e.message)
            self._scp_open()
            return

        def replied(conn, res):
            try:
                out = conn.call_finish(res)
            except GLib.Error as e:
                dbg("system-config-printer D-Bus 실패", method, e.message)
                if not self.dead:
                    self._scp_open()
                return
            then(bus, out.unpack() if out is not None else ())
        bus.call(SCP_BUS, path, iface, method, params, GLib.VariantType(reply_type) if reply_type else None,
                 Gio.DBusCallFlags.NONE, 60000, None, replied)

    def _scp_props(self, name):
        # 창 식별자(xid)는 X11 것이라 Wayland 에선 0
        self._scp_call(SCP_PATH, SCP_BUS, "PrinterPropertiesDialog", GLib.Variant("(us)", (0, name)),
                       "(s)", lambda *_: None)

    def _scp_new(self, uri, devid):
        def made(bus, out):
            path = out[0] if out else None
            if not path:
                self._scp_open()
                return
            self._scp_call(path, SCP_BUS + ".NewPrinterDialog", "NewPrinterFromDevice",
                           GLib.Variant("(uss)", (0, uri, devid or "")), None, lambda *_: None)
        self._scp_call(SCP_PATH, SCP_BUS, "NewPrinterDialog", None, "(s)", made)


def build(store):
    return PrintersPage(store).widget


PAGES = [{"id": "printers", "title": "프린터",
          "icon": PRINTER_ICONS,
          "build": build}]
