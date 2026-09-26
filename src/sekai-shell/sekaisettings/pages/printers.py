"""프린터 — CUPS 위에 올린 설정 페이지 (윈도우 11 의 "프린터 및 스캐너").

목록 · 상태 · 기본 프린터 · 인쇄 대기열 · 프린터 추가 · 제거 · 테스트 페이지 · 프린터 속성 · 인쇄 기본 설정.

읽기(프린터·작업 목록)는 CUPS 에 IPP 로 직접 묻는다 — 로컬 소켓에 요청 하나가 몇 ms 라 몇 초마다 물어도 가볍다.
  lpstat -p 는 부를 때마다 네트워크 프린터를 1초씩 찾고(DNS-SD) 문서 이름도 알려 주지 않는다.
바꾸는 일은 CUPS 명령(lpadmin · lpoptions · cancel · lp)으로. CUPS 관리 권한(@SYSTEM = root·lpadmin 그룹,
  데비안 기본)이 있으면 그대로 부르고 — 로컬 소켓의 PeerCred 로 암호 없이 통한다 — 없으면
  pkexec + /usr/libexec/sekai/sekai-printers 로.
새 프린터는 ippfind(DNS-SD)로 찾는다 — IPP Everywhere·AirPrint 네트워크 프린터와 ipp-usb 가 알리는 USB 프린터.
  드라이버가 필요한 옛 프린터와 IP 주소로 연결하는 프린터는 ‘프린터 추가’ 마법사(윈도우의 "원하는 프린터가
  목록에 없습니다")로 — 장치(lpinfo -v) → 드라이버(lpinfo -m, 한 번 읽어 둔다) → 이름 → 테스트 페이지.
  다른 데스크톱의 도구(system-config-printer)는 쓰지 않는다 — 속성·기본 인쇄 설정·드라이버 변경도 이 페이지에서.
기본 프린터는 윈도우처럼 사용자마다(lpoptions -d, ~/.cups/lpoptions). 기본이 하나도 없을 때 추가한 프린터는
  시스템 기본(lpadmin -d)으로도 정해 다른 계정에도 기본이 생긴다.
기본 인쇄 설정(용지 크기·양면·색…)은 윈도우의 "인쇄 기본값"처럼 이 PC 의 모든 사용자에게 (lpadmin -o).
페이지가 보이는 동안(또는 인쇄 대기열 창이 열려 있는 동안)만 몇 초마다 새로 읽는다.
"""
import getpass
import grp
import http.client
import ipaddress
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
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..util import dbg, failure_reason, run, run_async
from ..widgets import Page, button, combo, icon_image, info, row

HELPER = "/usr/libexec/sekai/sekai-printers"
TESTPAGE = "/usr/share/cups/data/testprint"          # CUPS 의 테스트 페이지
CUPS_SOCK = "/run/cups/cups.sock"
POLL_SECS = 3
SCAN_SECS = 5
PROBE_SECS = 30                                      # 네트워크 프린터가 켜져 있는지 다시 볼 간격
SVC_OP = "//service"                                 # ops 의 "인쇄 서비스 켜는 중" 키 (프린터 이름엔 / 가 없다)
PRINTER_ICONS = ["printer", "printer-network", "preferences-devices-printer", "printer-symbolic"]
DEVICE_SECS = 8                                      # 마법사의 장치 찾기 — lpinfo 의 네트워크 백엔드가 기다리는 시간
DRIVER_TTL = 600                                     # 읽어 둔 드라이버 목록을 쓰는 시간 (드라이버 패키지를 새로 깔 수 있다)


# ── IPP (CUPS 에 직접 묻기) ──────────────────────────────────
OP_GET_JOBS, OP_GET_PRINTER_ATTRS, OP_CUPS_GET_DEFAULT, OP_CUPS_GET_PRINTERS = 0x000A, 0x000B, 0x4001, 0x4002
T_INT, T_BOOL, T_ENUM = 0x21, 0x22, 0x23
T_TEXT_LANG, T_NAME_LANG, T_BEG_COL, T_END_COL = 0x35, 0x36, 0x34, 0x37
T_NAME, T_KEYWORD, T_URI, T_CHARSET, T_LANG = 0x42, 0x44, 0x45, 0x47, 0x48
G_PRINTER, G_JOB, G_END = 0x04, 0x02, 0x03
PRINTER_ATTRS = ("printer-name", "printer-info", "printer-location", "printer-make-and-model",
                 "printer-state", "printer-state-reasons", "printer-state-message",
                 "printer-is-accepting-jobs", "printer-is-temporary", "printer-type", "device-uri",
                 "printer-is-shared")
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
            "shared": bool(_one(g, "printer-is-shared", False)),
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
    # PATH 에 /usr/sbin 이 없는 세션(데비안의 일반 사용자)에서도 — lpadmin 은 /usr/sbin 에 있다
    return bool(shutil.which("lpadmin") or os.path.exists("/usr/sbin/lpadmin")) and (os.path.exists("/usr/sbin/cupsd") or
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
        m = re.match(r"^\s+([a-z_-]+) = ?(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2).strip()
    return out


def _squash(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ── 프린터 추가 마법사: 장치 · 드라이버 ─────────────────────────
# 장치 주소가 이런 스킴이면 네트워크 프린터 (나머지는 이 PC 에 꽂힌 것 — usb, hp:/usb, parallel …)
_NET_SCHEMES = ("socket", "lpd", "ipp", "ipps", "http", "https", "dnssd", "smb", "bjnp")
# 드라이버 없이(-m everywhere, driverless:…) 만들 수 있는 연결 — cupsd 가 IPP 로 프린터에 기능을 묻는다
_IPP_SCHEMES = ("ipp", "ipps", "dnssd", "http", "https")


def _scheme(uri):
    return uri.split(":", 1)[0].lower() if ":" in uri else ""


def _devid(text):
    """IEEE 1284 장치 ID ("MFG:HP;MDL:LaserJet 1020;CMD:PJL,…;") → (제조사, 모델, {키: 값})"""
    d = {}
    for part in (text or "").split(";"):
        k, sep, v = part.partition(":")
        if sep:
            d[k.strip().upper()] = v.strip()
    return (d.get("MFG") or d.get("MANUFACTURER") or "", d.get("MDL") or d.get("MODEL") or "", d)


def wizard_devices(text, printers=()):
    """lpinfo -l -v 출력 → 마법사에 보일 장치들. 주소가 없는 항목(socket · ipp · lpd 같은 '직접 입력' 틀)은 뺀다.
    이미 대기열로 있는 장치에는 "added" (그 프린터 이름)"""
    used = {p["uri"]: p["name"] for p in printers}
    out, seen = [], set()
    for d in _parse_lpinfo(text):
        uri = d["uri"]
        if ":" not in uri or uri in seen or not re.fullmatch(r"[\x21-\x7e]{3,1000}", uri):
            continue
        seen.add(uri)
        scheme = _scheme(uri)
        mm = d.get("make-and-model", "")
        mm = "" if mm.lower() in ("unknown", "") else mm
        mfg, mdl, _ = _devid(d.get("device-id", ""))
        if not mm and mfg and mdl:
            mm = mdl if mdl.lower().startswith(mfg.lower()) else f"{mfg} {mdl}"
        net = scheme in _NET_SCHEMES or uri.startswith("hp:/net/")
        name = d.get("info") or mm or ("네트워크 프린터" if net else "USB 프린터")
        if scheme == "cups-pdf":
            name = "PDF 로 저장하는 가상 프린터"
        out.append({"uri": uri, "name": name, "model": mm, "devid": d.get("device-id", ""),
                    "net": net, "driverless": False, "added": used.get(uri),
                    "virtual": scheme == "cups-pdf"})
    # 이 PC 에 꽂힌 것 → 네트워크 순서로, 같은 모델끼리 모이게
    out.sort(key=lambda x: (x["virtual"], x["net"], x["name"].casefold(), x["uri"]))
    return out


def _parse_models(text):
    """lpinfo -l -m 출력 → [{"name": ppd-name, "mm": 제조사·모델, "devid", "lang"}] (같은 이름은 하나만)"""
    out, cur, seen = [], None, set()
    keys = {"natural_language": "lang", "make-and-model": "mm", "device-id": "devid"}
    for line in text.splitlines():
        m = re.match(r"^Model:\s+name = (.*)$", line)
        if m:
            cur = {"name": m.group(1).strip(), "mm": "", "devid": "", "lang": ""}
            if cur["name"] in seen:
                cur = None
                continue
            seen.add(cur["name"])
            out.append(cur)
            continue
        m = re.match(r"^\s+(natural_language|make-and-model|device-id) = ?(.*)$", line)
        if m and cur is not None:
            cur[keys[m.group(1)]] = m.group(2).strip()
    for d in out:
        d["make"], d["model"] = _split_mm(d["mm"])
    return [d for d in out if d["name"] and d["mm"]]


# 제조사 이름을 하나로 (드라이버마다 "HP" · "Hewlett-Packard" · "hp" 로 제각각이다)
_MAKES = {
    "hp": "HP", "hewlett-packard": "HP", "hewlett packard": "HP", "epson": "Epson", "seiko epson": "Epson",
    "canon": "Canon", "brother": "Brother", "samsung": "Samsung", "xerox": "Xerox", "fuji xerox": "Fuji Xerox",
    "fujifilm": "FUJIFILM", "lexmark": "Lexmark", "ricoh": "Ricoh", "kyocera": "Kyocera", "kyocera mita": "Kyocera",
    "konica minolta": "Konica Minolta", "konica": "Konica Minolta", "minolta": "Minolta", "oki": "OKI",
    "okidata": "OKI", "oki data": "OKI", "sharp": "Sharp", "toshiba": "Toshiba", "dell": "Dell",
    "generic": "Generic", "sindoh": "Sindoh", "pantum": "Pantum", "zebra": "Zebra", "dymo": "DYMO",
    "panasonic": "Panasonic", "citizen": "Citizen", "gestetner": "Gestetner", "lanier": "Lanier",
    "savin": "Savin", "infotec": "Infotec", "nrg": "NRG", "olivetti": "Olivetti", "tally": "Tally",
    "star": "Star", "ibm": "IBM", "apple": "Apple", "fuji": "FUJIFILM", "kodak": "Kodak", "mitsubishi": "Mitsubishi",
}
_MULTI_MAKES = ("hewlett packard", "seiko epson", "fuji xerox", "kyocera mita", "konica minolta", "oki data")


def _split_mm(mm):
    """드라이버의 make-and-model → (제조사, 나머지)"""
    low = (mm or "").lower()
    for mk in _MULTI_MAKES:
        if low.startswith(mk + " "):
            return _MAKES[mk], mm[len(mk) + 1:].strip()
    first, _, rest = (mm or "").strip().partition(" ")
    return _MAKES.get(first.lower(), first), rest.strip()


def _make_key(make):
    return _MAKES.get((make or "").strip().lower(), (make or "").strip()).lower()


def _model_core(model):
    """모델 이름에서 드라이버 꼬리("…, hpcups 3.22" · " - CUPS+Gutenprint v5.3" · " Foomatic/…" · "(recommended)")
    와 "series" 를 떼고 글자·숫자만"""
    core = re.split(r",\s|\s-\s|\sFoomatic/|\s\(|\susing\s|\sPostScript\b|\sPS\b|\sPCL", model or "")[0]
    return _squash(re.sub(r"(?i)\bseries\b", "", core))


def driver_score(drv, mfg, mdl):
    """장치(제조사·모델)에 이 드라이버가 얼마나 맞나 — 0 이면 아니다"""
    if not mdl:
        return 0
    dm, dd, _ = _devid(drv["devid"])
    if mfg and dm and dd and _make_key(dm) == _make_key(mfg) and _squash(dd) == _squash(mdl):
        score = 100                                   # 드라이버가 알리는 장치 ID 와 똑같다
    else:
        if mfg and _make_key(drv["make"]) != _make_key(mfg):
            return 0
        want = _model_core(mdl[len(mfg):] if mfg and mdl.lower().startswith(mfg.lower()) else mdl)
        got = _model_core(drv["model"])
        if len(want) < 4 or not got:
            return 0
        if got == want:
            score = 90
        elif got.startswith(want) or (want.startswith(got) and len(got) >= 5):
            score = 60                                # "LaserJet 1020" ↔ "LaserJet 1020 Plus" — 가까운 모델
        else:
            return 0
    if "recommended" in drv["mm"].lower():
        score += 5
    if drv["lang"] in ("ko", "en"):
        score += 1
    return score


def best_driver(drivers, dev):
    """(추천 드라이버|None, 설명) — IPP Everywhere 를 지원하면 그것, 아니면 장치 ID·모델이 가장 맞는 것.
    그래도 없으면 프린터가 PostScript 를 알아들을 때만 일반 PostScript 드라이버"""
    mfg, mdl, ids = _devid(dev.get("devid", ""))
    cmd = ids.get("CMD") or ids.get("COMMAND SET") or ""
    # 드라이버 없이 쓰는 프린터(IPP Everywhere) — 장치 ID 의 명령어에 URF·PWG 래스터·PCLm 이 있으면 그렇다
    #   ('직접 추가' 마법사의 장치 목록은 driverless 표시를 모르므로 명령어로도 본다)
    driverless = dev.get("driverless") or re.search(r"(?i)\b(urf|pwgraster|pwg|pclm)\b", cmd.replace(",", " "))
    if driverless and (_scheme(dev["uri"]) in _IPP_SCHEMES or "_ipp" in dev["uri"]):
        d = next((x for x in drivers if x["name"] == "everywhere"), None)
        if d is not None:
            return d, "everywhere"
    if not mdl and dev.get("model"):
        mfg, mdl = _split_mm(dev["model"])
        mdl = f"{mfg} {mdl}".strip()
    best, top = None, 0
    for d in drivers:
        if d["name"] == "everywhere" or d["name"].startswith("driverless:"):
            continue
        sc = driver_score(d, mfg, mdl)
        if sc > top:
            best, top = d, sc
    if best is not None:
        return best, "match"
    # 일반 PostScript 드라이버는 PostScript 를 알아들을 때만 — PDF 만 받는 프린터(드라이버 없이 쓰는 것)에
    #   PostScript 를 보내면 인쇄가 깨지거나 아예 안 된다
    if re.search(r"(?i)\b(postscript|ps)\b", cmd.replace(",", " ")):
        d = next((x for x in drivers if x["name"] == "drv:///sample.drv/generic.ppd"), None)
        if d is not None:
            return d, "generic"
    return None, "none"


# 드라이버를 찾지 못했을 때 알려 줄 패키지 (데비안) — 설치는 사용자가 한다 (자동으로 깔지 않는다)
_DRIVER_PKGS = {
    "hp": ("hplip", "HP 프린터·복합기 드라이버"),
    "brother": ("printer-driver-brlaser", "브라더 레이저 프린터 드라이버"),
    "epson": ("printer-driver-escpr", "엡손 잉크젯 드라이버"),
    "canon": ("printer-driver-gutenprint", "캐논·엡손 잉크젯 드라이버"),
    "samsung": ("printer-driver-splix", "삼성·제록스 레이저 프린터 드라이버"),
    "xerox": ("openprinting-ppds", "PostScript 프린터 드라이버 모음"),
    "kyocera": ("openprinting-ppds", "PostScript 프린터 드라이버 모음"),
    "ricoh": ("openprinting-ppds", "PostScript 프린터 드라이버 모음"),
    "lexmark": ("openprinting-ppds", "PostScript 프린터 드라이버 모음"),
    "konica minolta": ("printer-driver-foo2zjs", "미놀타·HP 일부 레이저 프린터 드라이버"),
}


def driver_hint(dev):
    """드라이버가 없을 때의 안내 한 줄 (제조사에 맞는 드라이버 패키지)"""
    mfg, _mdl, _ = _devid(dev.get("devid", ""))
    if not mfg and dev.get("model"):
        mfg = _split_mm(dev["model"])[0]
    pkg, what = _DRIVER_PKGS.get(_make_key(mfg), ("printer-driver-all", "여러 제조사의 드라이버 모음"))
    return ("이 프린터에 맞는 드라이버를 찾지 못했습니다. 드라이버 패키지를 설치하면 목록에 나타납니다 — "
            f"터미널에서 ‘sudo apt install {pkg}’ ({what}). "
            "같은 제조사의 비슷한 모델이나 ‘Generic’ 드라이버를 골라 볼 수도 있습니다.")


def driver_label(mm):
    return re.sub(r"\s*\(recommended\)", " (권장)", mm or "")


def _display_name(dev, drv=None):
    """새 프린터의 이름 (사람이 보는 설명) — 장치 모델, 없으면 드라이버의 모델 부분"""
    for s in (dev.get("model"), dev.get("name") if not dev.get("manual") else "", drv and drv["mm"]):
        s = re.split(r",\s|\s-\s|\sFoomatic/|\s\(", s or "")[0].strip()
        if s and s.lower() not in ("unknown", "ipp everywhere"):
            return s[:100]
    return "프린터"


def _clean_host(text):
    """주소 칸 → (URI 에 넣을 호스트, 사람이 보는 호스트) 또는 None. 주소 전체를 붙여 넣어도 호스트만 쓴다"""
    t = (text or "").strip()
    if "://" in t:
        try:
            t = urllib.parse.urlsplit(t).hostname or ""
        except ValueError:
            return None
    t = t.strip().strip("[]")
    try:
        ip = ipaddress.ip_address(t)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_multicast or ip.is_unspecified or (ip.version == 6 and ip.is_link_local):
            return None
        return (f"[{ip}]" if ip.version == 6 else str(ip)), str(ip)
    if len(t) <= 253 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?", t):
        if not re.fullmatch(r"[0-9.]+", t):           # 999.1.1.1 같은 틀린 IP 주소를 이름으로 받지 않는다
            return t, t
    return None


def probe_ipp(host, port=631, timeout=4):
    """IP 주소로 추가: 프린터에 IPP 로 기능을 묻는다 (작업 스레드에서). 대답한 경로를 찾으면
    {"uri", "model", "devid", "driverless"}, 아니면 None"""
    for path in ("ipp/print", "ipp", "ipp/printer", ""):
        uri = f"ipp://{host}{'' if port == 631 else f':{port}'}/{path}"
        attrs = (_attr(T_URI, "printer-uri", uri)
                 + _attr(T_KEYWORD, "requested-attributes", "printer-make-and-model", "printer-device-id",
                         "document-format-supported", "printer-info"))
        conn = http.client.HTTPConnection(host.strip("[]"), port, timeout=timeout)
        try:
            conn.request("POST", "/" + path, _request(OP_GET_PRINTER_ATTRS, attrs),
                         {"Content-Type": "application/ipp"})
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as e:
            dbg("IPP 확인 실패", uri, e)
            if isinstance(e, (ConnectionRefusedError, socket.timeout, TimeoutError)):
                return None                           # 631 이 닫혀 있다 — 다른 경로도 마찬가지
            continue
        finally:
            conn.close()
        if resp.status != 200:
            continue
        try:
            st, groups = _decode(data)
        except CupsDown:
            continue
        if st >= 0x0100:
            continue
        g = next((g for t, g in groups if t == G_PRINTER), {})
        fmts = [f for f in g.get("document-format-supported", ()) if isinstance(f, str)]
        return {"uri": uri,
                "model": _one(g, "printer-make-and-model", "") or "",
                "devid": _one(g, "printer-device-id", "") or "",
                "driverless": any(f in _DRIVERLESS_PDL for f in fmts)}
    return None


# ── 기본 인쇄 설정 (lpoptions -l) ────────────────────────────
OPT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")        # 도우미와 같은 규칙
OPT_VAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}")


def parse_lpoptions(text):
    """lpoptions -p 이름 -l 출력 → [{"key", "label", "choices": [...], "default"}].
    한 줄: '키/설명: 값 *기본값 값…' — 설명에 : 가 들어갈 수 있어 마지막 : 로 가른다.
    PageRegion(PageSize 를 따라가는 옛 옵션)과 사용자 지정 크기(Custom.…)는 뺀다"""
    out = []
    for line in text.splitlines():
        head, sep, tail = line.rpartition(":")
        if not sep:
            continue
        key, _, label = head.partition("/")
        key = key.strip()
        if not OPT_KEY_RE.fullmatch(key) or key == "PageRegion":
            continue
        choices, default = [], None
        for c in tail.split():
            v = c.lstrip("*")
            if v.startswith("Custom.") or not OPT_VAL_RE.fullmatch(v):
                continue
            if c.startswith("*"):
                default = v
            if v not in choices:
                choices.append(v)
        if choices:
            out.append({"key": key, "label": label.strip() or key, "choices": choices, "default": default})
    return out


# 옵션 → (한국어 이름, 묶음). 묶음: paper(용지) · print(인쇄) — 나머지는 "고급"
OPTION_NAMES = {
    "PageSize": ("용지 크기", "paper"), "media": ("용지 크기", "paper"),
    "MediaType": ("용지 종류", "paper"), "media-type": ("용지 종류", "paper"),
    "InputSlot": ("용지 공급", "paper"), "media-source": ("용지 공급", "paper"),
    "Duplex": ("양면 인쇄", "print"), "sides": ("양면 인쇄", "print"),
    "ColorModel": ("색", "print"), "print-color-mode": ("색", "print"), "ColorMode": ("색", "print"),
    "cupsPrintQuality": ("인쇄 품질", "print"), "print-quality": ("인쇄 품질", "print"),
    "OutputMode": ("인쇄 품질", "print"), "StpQuality": ("인쇄 품질", "print"),
    "Quality": ("인쇄 품질", "print"), "PrintQuality": ("인쇄 품질", "print"),
    "Resolution": ("해상도", "print"),
    "OutputBin": ("출력 용지함", None), "output-bin": ("출력 용지함", None),
    "Collate": ("한 부씩 인쇄", None), "TonerSaveMode": ("토너 절약", None), "EconoMode": ("토너 절약", None),
    "TonerSave": ("토너 절약", None), "Toner": ("토너 절약", None), "TonerDensity": ("토너 농도", None),
    "Darkness": ("인쇄 농도", None), "Brightness": ("밝기", None), "print-scaling": ("크기 조정", None),
    "Staple": ("스테이플", None), "StapleLocation": ("스테이플", None), "PrintoutMode": ("인쇄 모드", None),
    "BindEdge": ("제본 방향", None), "Booklet": ("소책자", None), "Watermark": ("워터마크", None),
}
PAGE_SIZES = {
    "A3": "A3 (297 × 420 mm)", "A4": "A4 (210 × 297 mm)", "A5": "A5 (148 × 210 mm)", "A6": "A6 (105 × 148 mm)",
    "B4": "B4 (257 × 364 mm)", "B5": "B5 (182 × 257 mm)", "ISOB5": "ISO B5 (176 × 250 mm)",
    "Letter": "레터 (216 × 279 mm)", "Legal": "리갈 (216 × 356 mm)", "Executive": "이그제큐티브 (184 × 267 mm)",
    "Statement": "스테이트먼트 (140 × 216 mm)", "Tabloid": "타블로이드 (279 × 432 mm)",
    "Ledger": "레저 (432 × 279 mm)", "Oficio": "오피시오 (216 × 340 mm)", "FanFoldGermanLegal": "폴리오 (216 × 330 mm)",
    "Env10": "봉투 #10", "EnvDL": "봉투 DL", "EnvC5": "봉투 C5", "EnvC6": "봉투 C6", "EnvMonarch": "봉투 모나크",
    "EnvB5": "봉투 B5", "4x6": "사진 10 × 15 cm (4 × 6 in)", "5x7": "사진 13 × 18 cm (5 × 7 in)",
    "3.5x5": "사진 L (89 × 127 mm)", "L": "사진 L (89 × 127 mm)", "8x10": "사진 20 × 25 cm (8 × 10 in)",
    "Postcard": "엽서 (100 × 148 mm)", "DoublePostcardRotated": "왕복 엽서 (148 × 200 mm)",
}
CHOICE_NAMES = {
    "Duplex": {"None": "사용 안 함 (단면)", "DuplexNoTumble": "긴 쪽으로 넘기기", "DuplexTumble": "짧은 쪽으로 넘기기"},
    "sides": {"one-sided": "사용 안 함 (단면)", "two-sided-long-edge": "긴 쪽으로 넘기기",
              "two-sided-short-edge": "짧은 쪽으로 넘기기"},
    "color": {"Gray": "흑백", "Grayscale": "흑백", "Grey": "흑백", "Mono": "흑백", "Monochrome": "흑백",
              "monochrome": "흑백", "Black": "흑백", "BlackWhite": "흑백", "KGray": "흑백 (검정 잉크만)",
              "CMYGray": "흑백 (컬러 잉크 섞음)", "RGB": "컬러", "CMYK": "컬러", "Color": "컬러", "color": "컬러",
              "FullColor": "컬러", "AdobeRGB": "컬러 (Adobe RGB)", "auto": "자동", "Auto": "자동",
              "process-monochrome": "흑백 (컬러 잉크 섞음)", "bi-level": "흑백 (농담 없이)"},
    "quality": {"Draft": "초안 (빠르게)", "FastDraft": "빠른 초안", "Fast": "빠르게", "Normal": "보통",
                "Standard": "보통", "High": "높음", "Best": "최고", "Photo": "사진", "Enhanced": "향상됨",
                "3": "초안 (빠르게)", "4": "보통", "5": "높음", "None": "드라이버 기본값"},
    "MediaType": {"Plain": "일반 용지", "plain": "일반 용지", "stationery": "일반 용지", "Stationery": "일반 용지",
                  "Photo": "사진 용지", "photographic": "사진 용지", "Glossy": "광택 사진 용지",
                  "photographic-glossy": "광택 사진 용지", "photographic-matte": "무광 사진 용지",
                  "Matte": "무광 용지", "Transparency": "OHP 필름", "transparency": "OHP 필름",
                  "Envelope": "봉투", "envelope": "봉투", "Labels": "라벨", "labels": "라벨",
                  "Cardstock": "두꺼운 용지", "cardstock": "두꺼운 용지", "Thick": "두꺼운 용지",
                  "Heavy": "두꺼운 용지", "Thin": "얇은 용지", "Recycled": "재생지", "Letterhead": "레터헤드",
                  "Bond": "본드지", "Auto": "자동", "auto": "자동"},
    "InputSlot": {"Auto": "자동 선택", "auto": "자동 선택", "Default": "기본 용지함", "Manual": "수동 공급",
                  "ManualFeed": "수동 공급", "manual": "수동 공급", "Upper": "위쪽 용지함", "Middle": "가운데 용지함",
                  "Lower": "아래쪽 용지함", "Main": "기본 용지함", "main": "기본 용지함", "MultiPurpose": "다목적 용지함",
                  "MPTray": "다목적 용지함", "by-pass-tray": "다목적 용지함", "Envelope": "봉투 공급기",
                  "Cassette": "카세트", "Rear": "뒤쪽 공급", "Front": "앞쪽 공급", "Photo": "사진 용지함"},
    "any": {"True": "켜기", "False": "끄기", "On": "켜기", "Off": "끄기", "Yes": "예", "No": "아니요",
            "None": "없음", "PrinterDefault": "프린터 기본값", "Default": "기본값", "Auto": "자동"},
}
_CHOICE_GROUP = {"ColorModel": "color", "print-color-mode": "color", "ColorMode": "color",
                 "cupsPrintQuality": "quality", "print-quality": "quality", "OutputMode": "quality",
                 "StpQuality": "quality", "Quality": "quality", "PrintQuality": "quality",
                 "media-type": "MediaType", "media-source": "InputSlot"}


def option_name(opt):
    """(한국어 이름, 묶음) — 모르는 옵션은 드라이버가 붙인 이름 그대로"""
    return OPTION_NAMES.get(opt["key"], (opt["label"], None))


def _page_size(v):
    base, _, suffix = v.partition(".")
    text = PAGE_SIZES.get(base)
    if text is None:
        m = re.fullmatch(r"w(\d+(?:\.\d+)?)h(\d+(?:\.\d+)?)", base)          # 포인트(1/72 in) 단위 크기
        if not m:
            return None
        text = f"{float(m.group(1)) / 72 * 25.4:.0f} × {float(m.group(2)) / 72 * 25.4:.0f} mm"
    low = suffix.lower()
    if "borderless" in low or low in ("fb", "fullbleed"):
        text += " · 테두리 없음"
    elif "transverse" in low or "rotated" in low:
        text += " · 가로"
    elif low:
        text += f" · {suffix}"
    return text


def choice_name(key, v):
    if key in ("PageSize", "media"):
        t = _page_size(v)
        if t:
            return t
    if key == "Resolution":
        m = re.fullmatch(r"(\d+)(?:x(\d+))?dpi", v)
        if m:
            return f"{m.group(1)} × {m.group(2)} dpi" if m.group(2) and m.group(2) != m.group(1) \
                else f"{m.group(1)} dpi"
    t = CHOICE_NAMES.get(_CHOICE_GROUP.get(key, key), {}).get(v)
    if t:
        return t
    m = re.fullmatch(r"(?i)tray-?(\d+)", v)
    if m and key in ("InputSlot", "media-source"):
        return f"용지함 {m.group(1)}"
    return CHOICE_NAMES["any"].get(v, v)


def discover(admin):
    """(찾은 프린터 목록, 안내 한 줄|None) — 작업 스레드에서 (SCAN_SECS 초 넘게 걸린다).
    USB 로만 쓰는 옛 프린터(IPP-over-USB 가 없는 것)는 lpinfo 로만 보여서 CUPS 관리 권한이 있을 때만"""
    box = {}
    t = None
    if admin and (shutil.which("lpinfo") or os.path.exists("/usr/sbin/lpinfo")):
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


# ── 드라이버 목록 (lpinfo -l -m) ─────────────────────────────
_drv_cache = {"list": None, "time": 0.0, "waiters": []}


def load_drivers(then, force=False):
    """설치된 드라이버 목록 — 한 번 읽어 두고 DRIVER_TTL 동안 쓴다 (드라이버가 많으면 읽는 데 몇 초 걸린다).
    then(목록|None, 이유) 는 메인 스레드에서. 드라이버 목록은 보통 누구나 읽을 수 있고(CUPS-Get-PPDs),
    막아 둔 설치본에서만 도우미(pkexec)로 읽는다. 여러 창이 한꺼번에 불러도 lpinfo 는 한 번만"""
    c = _drv_cache
    if not force and c["list"] is not None and time.monotonic() - c["time"] < DRIVER_TTL:
        GLib.idle_add(lambda: (then(c["list"], ""), False)[1])
        return
    c["waiters"].append(then)
    if len(c["waiters"]) > 1:
        return

    def finish(lst, why):
        if lst is not None:
            c["list"], c["time"] = lst, time.monotonic()
        waiters, c["waiters"] = c["waiters"], []
        for w in waiters:
            try:
                w(lst, why)
            except Exception as e:                    # 한 창의 오류가 다른 창의 목록을 막지 않게
                dbg("드라이버 목록 콜백 실패", repr(e))
        return False

    def parse(out):
        # 드라이버가 수만 개일 수 있다 — 읽기는 작업 스레드에서
        threading.Thread(target=lambda: GLib.idle_add(finish, _parse_models(out), ""), daemon=True).start()

    def via_helper():
        if not os.path.exists(HELPER):
            finish(None, "프린터 도우미가 없습니다 — SekaiOS 업데이트를 받아 주세요")
            return
        run_async(["pkexec", HELPER, "drivers"],
                  lambda ok, out, err: parse(out) if ok else
                  finish(None, _why(out, "") if out.strip() else failure_reason(err)))

    def direct(ok, out, err):
        if ok:
            parse(out)
        elif _denied(err or out):
            via_helper()
        else:
            finish(None, _why(out, err, "드라이버 목록을 읽지 못했습니다"))
    run_async(["env", "LC_ALL=C", "lpinfo", "-l", "-m"], direct)


def _clear_user_options(name, keys):
    """내 ~/.cups/lpoptions 에 이 프린터의 같은 옵션이 있으면 지운다 — 남아 있으면 방금 정한
    모든 사용자의 기본값보다 먼저 쓰여, 바꾼 설정이 나에게는 먹지 않는 것처럼 보인다"""
    try:
        with open(os.path.expanduser("~/.cups/lpoptions"), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    hit = False
    for ln in lines:
        w = ln.split()
        if len(w) >= 3 and w[0].lower() in ("dest", "default") and w[1].split("/", 1)[0].lower() == name.lower():
            hit = hit or any(x.split("=", 1)[0] in keys for x in w[2:])
    if hit:
        run_async(["env", "LC_ALL=C", "lpoptions", "-p", name] + [x for k in keys for x in ("-r", k)],
                  lambda *_: None)


# ── 대화상자 도우미 ──────────────────────────────────────────
def _head(title, sub=None):
    """대화상자 한 장의 제목 (큰 글씨) + 설명. (상자, 제목 라벨, 설명 라벨)"""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    t = Gtk.Label(label=title, xalign=0)
    t.get_style_context().add_class("prn-name")
    t.set_line_wrap(True)
    box.pack_start(t, False, False, 0)
    s = Gtk.Label(label=sub or "", xalign=0)
    s.get_style_context().add_class("row-sub")
    s.set_line_wrap(True)
    s.set_max_width_chars(80)
    box.pack_start(s, False, False, 0)
    return box, t, s


class _Foot:
    """대화상자 아래의 진행 표시 + 상태 한 줄 (오류는 붉게)"""

    def __init__(self, parent):
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spin = Gtk.Spinner()
        self.spin.set_no_show_all(True)
        self.box.pack_start(self.spin, False, False, 0)
        self.label = Gtk.Label(xalign=0)
        self.label.get_style_context().add_class("row-sub")
        self.label.set_line_wrap(True)
        self.label.set_selectable(True)
        self.label.set_max_width_chars(80)
        self.box.pack_start(self.label, True, True, 0)
        parent.pack_start(self.box, False, False, 0)

    def say(self, text, error=False, busy=False):
        self.label.set_text(text or "")
        ctx = self.label.get_style_context()
        (ctx.add_class if error else ctx.remove_class)("prn-problem")
        self.spin.set_visible(busy)
        (self.spin.start if busy else self.spin.stop)()


def _entry(text="", width=30, placeholder=None, max_len=100):
    e = Gtk.Entry()
    e.set_text(text or "")
    e.set_width_chars(width)
    e.set_max_length(max_len)
    if placeholder:
        e.set_placeholder_text(placeholder)
    return e


def _josa(word, with_final, without):
    """받침에 맞는 조사 (이름은 · 위치는)"""
    ch = word[-1:] or "가"
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    return with_final if has else without


def _text_error(text, what):
    """설명·위치 칸 검사 — CUPS 의 text(127) 규칙. 문제가 없으면 빈 문자열"""
    if re.search(r"[\x00-\x1f\x7f]", text):
        return f"{what}에 쓸 수 없는 글자가 있습니다"
    if len(text.encode()) > 127:
        return f"{what}{_josa(what, '은', '는')} 한글 42자, 영문 127자까지 쓸 수 있습니다"
    return ""


# ── 드라이버 고르기 ──────────────────────────────────────────
class _DriverPicker(Gtk.Box):
    """드라이버 고르기 — 검색 칸 · 제조업체 목록 · 모델 목록 (윈도우의 "프린터 드라이버 설치").
    on_change(드라이버|None). 모델이 수천 개일 수 있어 목록은 TreeView 로"""
    DRIVERLESS = "//driverless"
    SEARCH_MAX = 300

    def __init__(self, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_change = on_change
        self.by_make, self.shown, self.selected, self._guard = {}, [], None, False
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("모델 이름으로 찾기 (예: LaserJet 1020)")
        self.search.connect("search-changed", lambda *_: self._fill_models())
        self.pack_start(self.search, False, False, 0)
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.mk_store = Gtk.ListStore(str, str)            # 보이는 이름, 키
        self.mk_view, sc = self._list(self.mk_store, "제조업체", 210)
        h.pack_start(sc, False, False, 0)
        self.md_store = Gtk.ListStore(str, int)            # 보이는 이름, self.shown 의 번호
        self.md_view, sc = self._list(self.md_store, "프린터", 360)
        h.pack_start(sc, True, True, 0)
        self.pack_start(h, True, True, 0)
        self.mk_view.get_selection().connect("changed", lambda *_: self._make_clicked())
        self.md_view.get_selection().connect("changed", self._picked)

    @staticmethod
    def _list(store, title, width):
        v = Gtk.TreeView(model=store)
        v.get_style_context().add_class("prn-list")
        cell = Gtk.CellRendererText()
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        cell.set_padding(8, 4)
        v.append_column(Gtk.TreeViewColumn(title, cell, text=0))
        v.set_enable_search(False)
        sc = Gtk.ScrolledWindow()
        sc.get_style_context().add_class("prn-list-box")
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_size_request(width, 240)
        sc.set_vexpand(True)
        sc.add(v)
        return v, sc

    @staticmethod
    def label(d):
        if d["name"] == "everywhere":
            return "IPP Everywhere — 드라이버가 필요 없습니다 (권장)"
        return driver_label(d["mm"])

    def set_drivers(self, drivers, dev, current=None):
        """dev(장치)에 맞게 목록을 채우고 지금 드라이버(current: ppd 이름) 또는 추천 드라이버를 고른다.
        돌려주는 것: 추천 종류 (current · everywhere · match · generic · none)"""
        ipp = _scheme(dev.get("uri", "")) in _IPP_SCHEMES
        self.by_make, names = {}, {}
        for d in drivers:
            if d["name"] == "everywhere" or d["name"].startswith("driverless:"):
                if not ipp:                           # IPP 로 말하는 프린터에만 (USB·RAW 로는 기능을 물을 수 없다)
                    continue
                key, text = self.DRIVERLESS, "드라이버 없이 (IPP Everywhere)"
            else:
                key, text = _make_key(d["make"]), d["make"]
            self.by_make.setdefault(key, []).append(d)
            names.setdefault(key, text)
        for lst in self.by_make.values():
            lst.sort(key=lambda d: (d["name"] != "everywhere", self.label(d).casefold()))
        self._guard = True
        self.mk_view.set_model(None)
        self.mk_store.clear()
        for k in sorted(names, key=lambda k: (k != self.DRIVERLESS, names[k].casefold())):
            self.mk_store.append([names[k], k])
        self.mk_view.set_model(self.mk_store)
        self._guard = False
        visible = [d for lst in self.by_make.values() for d in lst]
        kind = "current"
        pick = next((d for d in visible if d["name"] == current), None) if current else None
        if pick is None:
            pick, kind = best_driver(visible, dev)
        self.select(pick)
        return kind if pick is not None else "none"

    def select(self, drv):
        """그 드라이버의 제조업체와 모델을 고른다 (검색은 비운다)"""
        key = None
        if drv is not None:
            key = next((k for k, lst in self.by_make.items() if drv in lst), None)
        # 이미 골라져 있던 제조업체면 changed 가 오지 않는다 — 신호는 막고 목록은 여기서 직접 채운다
        self._guard = True
        self.search.set_text("")
        sel = self.mk_view.get_selection()
        sel.unselect_all()
        for r in self.mk_store:
            if r[1] == key:
                sel.select_iter(r.iter)
                self._scroll(self.mk_view, r.path)
                break
        self._guard = False
        self.selected = drv
        self._fill_models()

    def _make_clicked(self):
        """제조업체를 누르면 검색을 비우고 그 제조업체의 모델을 보인다"""
        if self._guard:
            return
        if self.search.get_text():
            self._guard = True
            self.search.set_text("")
            self._guard = False
        self._fill_models()

    @staticmethod
    def _scroll(view, path):
        # 아직 크기가 정해지지 않았으면 스크롤이 먹지 않는다 — 한 번 쉰 뒤에
        GLib.idle_add(lambda: (view.scroll_to_cell(path, None, True, 0.3, 0.0), False)[1])

    def _fill_models(self):
        if self._guard:
            return
        q = self.search.get_text().strip().casefold()
        if q:
            words = q.split()
            found = [d for lst in self.by_make.values() for d in lst
                     if all(w in d["mm"].casefold() or w in d["name"].casefold() for w in words)]
            found.sort(key=lambda d: self.label(d).casefold())
            self.shown = found[:self.SEARCH_MAX]
        else:
            model, it = self.mk_view.get_selection().get_selected()
            self.shown = self.by_make.get(model[it][1], []) if it is not None else []
        self._guard = True
        self.md_view.set_model(None)
        self.md_store.clear()
        for i, d in enumerate(self.shown):
            self.md_store.append([self.label(d), i])
        self.md_view.set_model(self.md_store)
        self._guard = False
        if self.selected is not None and self.selected in self.shown:
            path = Gtk.TreePath.new_from_indices([self.shown.index(self.selected)])
            self.md_view.get_selection().select_path(path)
            self._scroll(self.md_view, path)
        else:
            self.selected = None
            self.on_change(None)

    def _picked(self, sel):
        if self._guard:
            return
        model, it = sel.get_selected()
        self.selected = self.shown[model[it][1]] if it is not None else None
        self.on_change(self.selected)


# ── 프린터 추가 마법사 ───────────────────────────────────────
class _AddWizard:
    """프린터 추가 — 윈도우의 "원하는 프린터가 목록에 없습니다". 장치 → 드라이버 → 이름 → 완료(테스트 페이지).
    device 를 주면(찾은 프린터의 ‘직접 추가…’) 드라이버 고르기부터"""

    def __init__(self, page, device=None):
        self.page, self.dev, self.drv = page, device, None
        self.start = "driver" if device else "device"
        self.devices, self.scan_err, self.scanning = None, None, False
        self.step, self.working, self.alive = None, False, True
        self.drv_for = None                   # 드라이버 목록을 채운 장치 (같은 장치면 다시 채우지 않는다)
        self.named_for = None
        self.added = None                     # 추가한 대기열 이름
        d = self.d = Gtk.Dialog(title="프린터 추가", transient_for=_parent(page.p), modal=True,
                                destroy_with_parent=True)
        d.set_default_size(740, 600)
        area = d.get_content_area()
        area.set_border_width(18)
        area.set_spacing(12)
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        area.pack_start(self.stack, True, True, 0)
        self.foot = _Foot(area)
        self.b_cancel = d.add_button("취소", Gtk.ResponseType.CANCEL)
        self.b_back = d.add_button("뒤로", 1)
        self.b_next = d.add_button("다음", 2)
        self.b_next.get_style_context().add_class("accent-btn")
        d.connect("response", self._response)
        # 추가하는 동안은 닫지 않는다 (반쪽 대기열을 치우는 일까지 끝나야 한다)
        d.connect("delete-event", lambda *_: self.working and self.step == "name")
        d.connect("destroy", self._destroyed)
        self._build_device()
        self._build_driver()
        self._build_name()
        self._build_done()
        d.show_all()
        self._go(self.start)

    def _destroyed(self, *_):
        self.alive = False
        if self.page.wizard is self:
            self.page.wizard = None

    def present(self):
        self.d.present()

    # ── 1. 장치 ──
    def _build_device(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        head, _t, _s = _head("프린터 선택", "이 PC 에 연결된 프린터와 네트워크에서 찾은 프린터입니다. "
                                        "원하는 프린터가 없으면 IP 주소나 호스트 이름으로 추가하세요.")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.pack_start(head, True, True, 0)
        self.rescan_b = button("다시 찾기", self._scan)
        self.rescan_b.set_valign(Gtk.Align.START)
        top.pack_start(self.rescan_b, False, False, 0)
        box.pack_start(top, False, False, 0)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        sc.set_min_content_height(180)
        self.dev_lb = Gtk.ListBox()
        self.dev_lb.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dev_lb.get_style_context().add_class("section")
        self.dev_lb.connect("row-activated", lambda _lb, r: getattr(r, "radio", None) and r.radio.set_active(True))
        sc.add(self.dev_lb)
        box.pack_start(sc, True, True, 0)

        self.ip_radio = Gtk.RadioButton(label="IP 주소 또는 호스트 이름으로 프린터 추가")
        self.ip_radio.connect("toggled", self._dev_changed)
        box.pack_start(self.ip_radio, False, False, 0)
        g = self.ip_form = Gtk.Grid(column_spacing=12, row_spacing=8)
        g.set_margin_start(28)
        self.host_e = _entry(width=30, placeholder="예: 192.168.0.20 또는 printer.local", max_len=260)
        self.host_e.connect("changed", lambda *_: self._update_buttons())
        self.host_e.connect("activate", lambda *_: self._next())
        self.proto = combo([("auto", "자동 감지"), ("ipp", "IPP (인터넷 인쇄 프로토콜)"),
                            ("socket", "RAW (TCP 포트 9100)"), ("lpd", "LPD (LPR)")], "auto",
                           on_change=lambda _v: self._proto_changed())
        self.extra_l = Gtk.Label(xalign=0)
        self.extra_e = _entry(width=16, max_len=128)
        self.extra_e.connect("changed", lambda *_: self._update_buttons())
        for i, (t, w) in enumerate((("호스트 이름 또는 IP 주소", self.host_e), ("연결 방식", self.proto))):
            g.attach(Gtk.Label(label=t, xalign=0), 0, i, 1, 1)
            w.set_halign(Gtk.Align.START)
            g.attach(w, 1, i, 1, 1)
        g.attach(self.extra_l, 0, 2, 1, 1)
        self.extra_e.set_halign(Gtk.Align.START)
        g.attach(self.extra_e, 1, 2, 1, 1)
        box.pack_start(g, False, False, 0)
        self.stack.add_named(box, "device")

    def _proto_changed(self):
        proto = self.proto.get_active_id()
        text, default, tip = {"ipp": ("경로", "ipp/print", "보통 ipp/print — 프린터 설명서를 확인하세요"),
                              "socket": ("포트", "9100", "보통 9100"),
                              "lpd": ("대기열 이름", "", "프린터 설명서에 있는 이름 (예: lp)")}.get(proto, (None, "", ""))
        self.extra_l.set_text(text or "")
        self.extra_e.set_text(default)
        self.extra_e.set_placeholder_text(tip)
        for w in (self.extra_l, self.extra_e):
            w.set_visible(text is not None)
        self._update_buttons()

    def _scan(self):
        if self.scanning or not self.alive:
            return
        self.scanning = True
        self.devices, self.scan_err = None, None
        self._fill_devices()
        self._update_buttons()

        def done(ok, out, why):
            self.scanning = False
            if not self.alive:
                return
            printers = self.page.snap["printers"] if self.page.snap else []
            self.devices = wizard_devices(out, printers) if ok else []
            if not ok:
                self.scan_err = f"장치를 찾지 못했습니다: {why}. 아래에서 주소로 추가할 수 있습니다."
            self._fill_devices()
            self._update_buttons()
        self.page._admin_read(["lpinfo", "-l", "--timeout", str(DEVICE_SECS), "-v"], ["devices"], done)

    def _fill_devices(self):
        _clear(self.dev_lb)
        if self.devices is None:
            r = Gtk.ListBoxRow()
            r.get_style_context().add_class("row")
            r.set_activatable(False)
            r.add(_busy_box("이 PC 와 네트워크에서 프린터를 찾는 중… (10초쯤 걸립니다)"))
            self.dev_lb.add(r)
        elif not self.devices:
            row(self.dev_lb, "찾은 프린터가 없습니다", self.scan_err or
                "프린터가 켜져 있고 USB 케이블이나 같은 네트워크에 연결되어 있는지 확인하세요. "
                "아래에서 IP 주소로 추가할 수도 있습니다.", icon=PRINTER_ICONS)
        else:
            first = None
            for dv in self.devices:
                r = self._dev_row(dv)
                if first is None and not dv["added"]:
                    first = r
            (first.radio if first is not None else self.ip_radio).set_active(True)
        self.dev_lb.show_all()
        self._dev_changed()

    def _dev_row(self, dv):
        r = Gtk.ListBoxRow()
        r.get_style_context().add_class("row")
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        rb = Gtk.RadioButton.new_from_widget(self.ip_radio)
        rb.set_valign(Gtk.Align.CENTER)
        rb.connect("toggled", self._dev_changed)
        h.pack_start(rb, False, False, 0)
        img = icon_image(["printer-network"] + PRINTER_ICONS if dv["net"] else
                         (["document-save", "x-office-document"] if dv["virtual"] else PRINTER_ICONS), 20)
        h.pack_start(img, False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        v.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=dv["name"], xalign=0)
        t.get_style_context().add_class("row-title")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(t, False, False, 0)
        sub = _conn_text(dv["uri"])
        if dv["model"] and dv["model"] != dv["name"]:
            sub = f"{dv['model']} · {sub}"
        if dv["added"]:
            sub += f" · 이미 추가되어 있습니다 ({dv['added']})"
        s = Gtk.Label(label=sub, xalign=0)
        s.get_style_context().add_class("row-sub")
        s.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(s, False, False, 0)
        h.pack_start(v, True, True, 0)
        r.add(h)
        r.set_tooltip_text(dv["uri"])
        r.radio, rb.dev = rb, dv
        self.dev_lb.add(r)
        return r

    def _dev_changed(self, *_):
        self.ip_form.set_sensitive(self.ip_radio.get_active())
        self._update_buttons()

    def _chosen_device(self):
        for r in self.dev_lb.get_children():
            rb = getattr(r, "radio", None)
            if rb is not None and rb.get_active():
                return rb.dev
        return None

    def _manual(self):
        """주소 칸 검사 → (호스트, 보이는 호스트, 방식, 덧붙일 값) 또는 오류 한 줄"""
        host = _clean_host(self.host_e.get_text())
        if host is None:
            return "호스트 이름이나 IP 주소가 올바르지 않습니다 (예: 192.168.0.20)"
        proto, extra = self.proto.get_active_id(), self.extra_e.get_text().strip()
        if proto == "socket" and not (extra.isdigit() and 0 < int(extra) < 65536):
            return "포트는 1~65535 사이의 숫자로 입력하세요"
        if proto == "lpd" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", extra):
            return "LPD 대기열 이름을 입력하세요 (영문·숫자·_ . - 만)"
        if proto == "ipp":
            extra = extra.lstrip("/")
            if not re.fullmatch(r"[A-Za-z0-9_./~-]{0,128}", extra) or ".." in extra:
                return "IPP 경로가 올바르지 않습니다 (예: ipp/print)"
        return host[0], host[1], proto, extra

    def _probe_manual(self):
        m = self._manual()
        if isinstance(m, str):
            self.foot.say(m, error=True)
            return
        host, shown, proto, extra = m
        self._busy(True, f"{shown} 에 연결하는 중…")

        def work():
            dev, err = None, None
            try:
                ipp = None
                if proto in ("auto", "ipp"):
                    ipp = probe_ipp(host) if proto == "auto" else None
                    if proto == "ipp":
                        uri = f"ipp://{host}/{extra}"
                        found = probe_ipp(host) or {}
                        ipp = dict(found, uri=uri) if found else {"uri": uri, "model": "", "devid": "",
                                                                  "driverless": False}
                if ipp is not None:
                    dev = ipp
                elif proto == "socket":
                    dev = {"uri": f"socket://{host}" + ("" if extra == "9100" else f":{extra}")}
                elif proto == "lpd":
                    dev = {"uri": f"lpd://{host}/{extra}"}
                elif _probe(f"socket://{host}"):              # 자동: IPP 가 아니면 RAW(9100) → LPD 차례로
                    dev = {"uri": f"socket://{host}"}
                elif _probe(f"lpd://{host}/"):
                    dev = {"uri": f"lpd://{host}/lp"}
                else:
                    err = (f"{shown} 에서 프린터를 찾지 못했습니다 — 프린터가 켜져 있고 주소가 맞는지 확인하거나, "
                           "연결 방식을 직접 골라 보세요")
            except Exception as e:                          # 스레드에서 새면 마법사가 멈춘 채로 남는다
                dbg("주소 확인 실패", repr(e))
                err = "프린터에 연결하는 중 오류가 났습니다"
            if dev is not None:
                dev = dict({"model": "", "devid": "", "driverless": False}, **dev)
                dev.update(name=shown, net=True, manual=True, added=None, virtual=False)
            GLib.idle_add(self._probed, dev, err)
        threading.Thread(target=work, daemon=True).start()

    def _probed(self, dev, err):
        if not self.alive:
            return False
        self._busy(False)
        if err:
            self.foot.say(err, error=True)
        else:
            self.dev = dev
            self._go("driver")
        return False

    # ── 2. 드라이버 ──
    def _build_driver(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        head, _t, self.drv_sub = _head("드라이버 설치")
        box.pack_start(head, False, False, 0)
        self.picker = _DriverPicker(self._drv_changed)
        box.pack_start(self.picker, True, True, 0)
        self.drv_hint = _notice()
        self.drv_hint.set_no_show_all(True)
        box.pack_start(self.drv_hint, False, False, 0)
        self.stack.add_named(box, "driver")

    def _enter_driver(self):
        dev = self.dev
        self.drv_sub.set_text(f"‘{dev['name']}’ 의 제조업체와 모델을 고르세요." +
                              (" 이 프린터는 드라이버 없이 쓸 수 있습니다." if dev.get("driverless") else ""))
        if self.drv_for == dev["uri"]:
            return
        self.drv = None
        self.drv_hint.hide()
        self._busy(True, "드라이버 목록을 읽는 중…")
        load_drivers(self._drivers_loaded)

    def _drivers_loaded(self, lst, why):
        if not self.alive:
            return
        self._busy(False)
        if lst is None:
            self.foot.say(f"드라이버 목록을 읽지 못했습니다: {why}", error=True)
            return
        self.drv_for = self.dev["uri"]
        kind = self.picker.set_drivers(lst, self.dev)
        hint = {"none": driver_hint(self.dev),
                "generic": "정확한 드라이버를 찾지 못해 일반 PostScript 드라이버를 골랐습니다. " + driver_hint(self.dev)
                }.get(kind)
        self.drv_hint.set_text(hint or "")
        self.drv_hint.set_visible(bool(hint))
        self.foot.say("추천 드라이버를 골라 두었습니다." if kind in ("match", "everywhere") else "")

    def _drv_changed(self, drv):
        self.drv = drv
        self._update_buttons()

    # ── 3. 이름 ──
    def _build_name(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        head, _t, _s = _head("프린터 이름 입력", "목록과 앱의 인쇄 창에 보이는 이름입니다.")
        box.pack_start(head, False, False, 0)
        g = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.name_e = _entry(width=34)
        self.queue_e = _entry(width=34, max_len=64)
        self.loc_e = _entry(width=34, placeholder="예: 2층 사무실 (적지 않아도 됩니다)")
        self.queue_touched = False
        self.name_e.connect("changed", self._name_changed)
        self.queue_e.connect("key-press-event", lambda *_: setattr(self, "queue_touched", True))
        for i, (t, w) in enumerate((("프린터 이름", self.name_e), ("대기열 이름", self.queue_e), ("위치", self.loc_e))):
            g.attach(Gtk.Label(label=t, xalign=0), 0, i * 2, 1, 1)
            g.attach(w, 1, i * 2, 1, 1)
        note = Gtk.Label(label="영문·숫자·_·- 만 — 명령줄(lp -d …)과 다른 프로그램에서 쓰는 이름입니다", xalign=0)
        note.get_style_context().add_class("row-sub")
        g.attach(note, 1, 3, 1, 1)
        box.pack_start(g, False, False, 0)
        self.def_chk = Gtk.CheckButton(label="기본 프린터로 설정")
        box.pack_start(self.def_chk, False, False, 0)
        self.share_chk = Gtk.CheckButton(label="이 프린터 공유 — 같은 네트워크의 다른 컴퓨터가 이 프린터로 인쇄할 수 있습니다")
        box.pack_start(self.share_chk, False, False, 0)
        self.stack.add_named(box, "name")

    def _taken(self):
        return (self.page.snap or {}).get("names", set())

    def _name_changed(self, *_):
        if not self.queue_touched:
            self.queue_e.set_text(_queue_name(self.name_e.get_text(), self._taken()))

    def _enter_name(self):
        key = (self.dev["uri"], self.drv["name"])
        if self.named_for == key:
            return
        self.named_for = key
        self.queue_touched = False
        self.name_e.set_text(_display_name(self.dev, self.drv))
        if not re.search(r"[A-Za-z0-9]", self.name_e.get_text()):       # 한글 이름이면 대기열 이름은 모델에서
            self.queue_e.set_text(_queue_name(_display_name({}, self.drv), self._taken()))
        self.def_chk.set_active(not (self.page.snap or {}).get("default"))
        self.share_chk.set_active(False)

    def _name_error(self):
        info_, q, loc = self.name_e.get_text().strip(), self.queue_e.get_text().strip(), self.loc_e.get_text().strip()
        if not info_:
            return "프린터 이름을 입력하세요"
        err = _text_error(info_, "프린터 이름") or _text_error(loc, "위치")
        if err:
            return err
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", q):
            return "대기열 이름은 영문·숫자로 시작하고 영문·숫자·_·- 만 쓸 수 있습니다 (64자까지)"
        if q.lower() in {t.lower() for t in self._taken()}:
            return f"‘{q}’ 이라는 프린터가 이미 있습니다 — 다른 대기열 이름을 쓰세요"
        return ""

    def _add(self):
        err = self._name_error()
        if err:
            self.foot.say(err, error=True)
            return
        page = self.page
        info_, q, loc = self.name_e.get_text().strip(), self.queue_e.get_text().strip(), self.loc_e.get_text().strip()
        uri, drv, shared = self.dev["uri"], self.drv["name"], self.share_chk.get_active()
        want_default = self.def_chk.get_active()
        sys_default = want_default and not (page.snap or {}).get("default")   # 기본이 하나도 없다 — 시스템 기본으로
        stale = _lpoptions_default() if sys_default else None
        cmd = ["lpadmin", "-p", q, "-v", uri, "-m", drv, "-o", f"printer-is-shared={'true' if shared else 'false'}"]
        if info_:
            cmd += ["-D", info_]
        if loc:
            cmd += ["-L", loc]
        steps = [(cmd + ["-E"], True)]            # -E 는 -p 뒤에 (앞에 오면 암호화 옵션)
        if shared:
            steps.append((["cupsctl", "--share-printers"], False))
        if sys_default:
            steps.append((["lpadmin", "-d", q], False))
        self._busy(True, "프린터를 추가하는 중…" + (" (프린터에 기능을 묻고 있습니다)" if drv == "everywhere" else ""))
        page._begin(uri, "추가하는 중…")

        def done(ok, why):
            if ok and stale:
                _forget_user_default(stale)
            if ok and want_default and not sys_default:        # 이미 기본이 있다 — 나에게만 (윈도우처럼)
                run_async(["env", "LC_ALL=C", "lpoptions", "-d", q],
                          lambda *_: None if page.dead else page.refresh())
            live = page._end(uri)
            if not self.alive:
                if live:
                    page.say(f"‘{info_}’ 프린터를 추가했습니다" if ok else
                             f"‘{info_}’ 프린터를 추가하지 못했습니다: {why}", error=not ok)
                return
            self._busy(False)
            if ok:
                self.added, self.added_title = q, info_
                self._go("done")
            else:
                self.foot.say(f"프린터를 추가하지 못했습니다: {why}", error=True)
        page._admin(steps, ["add-driver", q, uri, drv, info_, loc, "shared" if shared else "private",
                            "default" if sys_default else "keep"], done, undo=["lpadmin", "-x", q])

    # ── 4. 완료 ──
    def _build_done(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        head, self.done_t, _s = _head("", "프린터가 제대로 작동하는지 확인하려면 테스트 페이지를 인쇄해 보세요.")
        box.pack_start(head, False, False, 0)
        b = button("테스트 페이지 인쇄", self._test)
        b.set_halign(Gtk.Align.START)
        box.pack_start(b, False, False, 0)
        self.stack.add_named(box, "done")

    def _test(self):
        self.foot.say("테스트 페이지를 보내는 중…", busy=True)
        self.page.test_page(self.added, then=lambda ok, msg: self.alive and self.foot.say(msg, error=not ok))

    # ── 넘기기 ──
    def _busy(self, on, text=None):
        self.working = on
        self.foot.say(text if on else "", busy=on)
        self._update_buttons()

    def _go(self, step):
        self.step = step
        self.stack.set_visible_child_name(step)
        self.foot.say("")
        if step == "device":
            if self.devices is None and not self.scanning:
                self._scan()
            self._proto_changed()
        elif step == "driver":
            self._enter_driver()
        elif step == "name":
            self._enter_name()
            self.name_e.grab_focus()
        elif step == "done":
            self.done_t.set_text(f"‘{self.added_title}’ 을(를) 추가했습니다")
        self._update_buttons()

    def _update_buttons(self):
        if not self.alive or self.step is None:
            return
        step = self.step
        self.b_back.set_visible((step == "driver" and self.start == "device") or step == "name")
        self.b_cancel.set_visible(step != "done")
        self.b_next.set_label({"name": "추가", "done": "마침"}.get(step, "다음"))
        if step == "device":
            ok = (bool(self.host_e.get_text().strip()) if self.ip_radio.get_active()
                  else self._chosen_device() is not None)
        elif step == "driver":
            ok = self.drv is not None
        else:
            ok = True
        self.b_next.set_sensitive(ok and not self.working)
        self.b_back.set_sensitive(not self.working)
        self.b_cancel.set_sensitive(not (self.working and step == "name"))
        self.rescan_b.set_sensitive(not self.scanning)

    def _next(self):
        if self.working or not self.b_next.get_sensitive():
            return
        if self.step == "device":
            if self.ip_radio.get_active():
                self._probe_manual()
            else:
                self.dev = self._chosen_device()
                self._go("driver")
        elif self.step == "driver":
            self._go("name")
        elif self.step == "name":
            self._add()
        else:
            self.d.destroy()

    def _response(self, _d, resp):
        if resp == 2:
            self._next()
        elif resp == 1:
            if not self.working:
                self._go("driver" if self.step == "name" else "device")
        elif not (self.working and self.step == "name"):
            self.d.destroy()


# ── 드라이버 변경 ───────────────────────────────────────────
class _DriverDialog:
    """이미 있는 프린터의 드라이버를 바꾼다 (윈도우 프린터 속성의 "새 드라이버…")"""

    def __init__(self, page, name, parent, on_done=None):
        p = page.printer(name)
        self.page, self.name, self.on_done, self.alive, self.drv = page, name, on_done, True, None
        d = self.d = Gtk.Dialog(title="드라이버 변경", transient_for=parent, modal=True, destroy_with_parent=True)
        d.set_default_size(720, 540)
        area = d.get_content_area()
        area.set_border_width(18)
        area.set_spacing(12)
        head, _t, _s = _head("드라이버 변경", f"‘{_title(p)}’ 에 쓸 드라이버를 고르세요. 바꾸면 기본 인쇄 설정이 "
                                            "새 드라이버의 기본값으로 돌아갑니다.")
        area.pack_start(head, False, False, 0)
        self.picker = _DriverPicker(self._changed)
        area.pack_start(self.picker, True, True, 0)
        self.hint = _notice()
        self.hint.set_no_show_all(True)
        area.pack_start(self.hint, False, False, 0)
        self.foot = _Foot(area)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        self.ok = d.add_button("바꾸기", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("accent-btn")
        self.ok.set_sensitive(False)
        d.connect("response", self._response)
        d.connect("destroy", lambda *_: setattr(self, "alive", False))
        everywhere = "IPP Everywhere" in p["model"]
        self.dev = {"uri": p["uri"], "model": p["model"].replace(" - IPP Everywhere", "").strip(), "devid": "",
                    "driverless": everywhere}
        self.current = "everywhere" if everywhere else None
        self.current_mm = p["model"]
        d.show_all()
        self.foot.say("드라이버 목록을 읽는 중…", busy=True)
        load_drivers(self._loaded)

    def _loaded(self, lst, why):
        if not self.alive:
            return
        if lst is None:
            self.foot.say(f"드라이버 목록을 읽지 못했습니다: {why}", error=True)
            return
        self.foot.say("")
        # 지금 드라이버를 먼저 정해 둔다 — set_drivers 가 고르는 순간 _changed 가 불려 [바꾸기]를 정한다
        self.current = self.current or next((d["name"] for d in lst if d["mm"] == self.current_mm), None)
        kind = self.picker.set_drivers(lst, self.dev, current=self.current)
        if kind == "none":
            self.hint.set_text(driver_hint(self.dev))
            self.hint.show()

    def _changed(self, drv):
        self.drv = drv
        self.ok.set_sensitive(drv is not None and drv["name"] != self.current)

    def _response(self, _d, resp):
        if resp != Gtk.ResponseType.OK:
            self.d.destroy()
            return
        if self.drv is None or self.name in self.page.ops:
            return
        drv, page, name = self.drv, self.page, self.name
        self.ok.set_sensitive(False)
        self.foot.say("드라이버를 바꾸는 중…", busy=True)
        page._begin(name, "드라이버를 바꾸는 중…")

        def done(ok, why):
            live = page._end(name)
            if self.alive:
                if ok:
                    self.d.destroy()
                else:
                    self.foot.say(f"드라이버를 바꾸지 못했습니다: {why}", error=True)
                    self.ok.set_sensitive(True)
            if live and ok:
                page.say(f"드라이버를 ‘{driver_label(drv['mm'])}’ (으)로 바꿨습니다")
            if ok and self.on_done:
                self.on_done(drv)
        page._admin([(["lpadmin", "-p", name, "-m", drv["name"]], True)], ["driver", name, drv["name"]], done)


def _dialog_body(d, width, height):
    """설정 페이지처럼 섹션을 쌓는 대화상자 몸통 (스크롤) — 안쪽 상자를 돌려준다"""
    d.set_default_size(width, height)
    area = d.get_content_area()
    area.set_spacing(10)
    sc = Gtk.ScrolledWindow()
    sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sc.set_vexpand(True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.set_border_width(18)
    sc.add(box)
    area.pack_start(sc, True, True, 0)
    return box


# ── 프린터 속성 ─────────────────────────────────────────────
class _Props:
    """프린터 속성 — 이름(설명) · 위치 · 공유 · 드라이버 (윈도우의 "프린터 속성")"""

    def __init__(self, page, name):
        p = page.printer(name)
        self.page, self.name, self.p, self.alive = page, name, dict(p), True
        d = self.d = Gtk.Dialog(title=f"{_title(p)} — 프린터 속성", transient_for=_parent(page.p), modal=True,
                                destroy_with_parent=True)
        box = _dialog_body(d, 620, 600)
        head, _t, _s = _head(_title(p), driver_label(p["model"].replace(" - IPP Everywhere", "")) or None)
        box.pack_start(head, False, False, 0)
        s = _sect(box, "일반")
        # 길이 제한은 저장할 때(_text_error, 127바이트) 본다 — 칸이 글자 수로 자르면 100자가 넘는 기존 이름이
        #   잘린 채 함께 저장되었다
        self.info_e = _entry(p["info"] or name, width=28, max_len=0)
        row(s, "프린터 이름", "목록과 앱의 인쇄 창에 보이는 이름", control=self.info_e)
        self.loc_e = _entry(p["location"], width=28, placeholder="예: 2층 사무실", max_len=0)
        row(s, "위치", None, control=self.loc_e)
        row(s, "대기열 이름", "명령줄(lp -d …)과 다른 프로그램에서 쓰는 이름 — 바꿀 수 없습니다", control=info(name))
        row(s, "연결", None, control=info(_conn_text(p["uri"])))
        s = _sect(box, "공유")
        self.share_sw = Gtk.Switch()
        self.share_sw.set_active(p["shared"])
        row(s, "이 프린터 공유", "같은 네트워크의 다른 컴퓨터가 이 프린터로 인쇄할 수 있습니다", control=self.share_sw)
        s = _sect(box, "드라이버")
        self.drv_row = row(s, self._drv_text(p["model"]), "지금 쓰는 드라이버", control=button("변경…", self._driver))
        self.drv_row.title_label.set_line_wrap(True)
        self.foot = _Foot(d.get_content_area())
        self.foot.box.set_border_width(8)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        self.ok = d.add_button("저장", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("accent-btn")
        d.connect("response", self._response)
        d.connect("destroy", lambda *_: setattr(self, "alive", False))
        d.show_all()

    @staticmethod
    def _drv_text(model):
        return "IPP Everywhere — 드라이버가 필요 없습니다" if "IPP Everywhere" in model else \
            (driver_label(model) or "알 수 없음")

    def _driver(self):
        def changed(drv):
            if self.alive:
                self.drv_row.title_label.set_text(self._drv_text(drv["mm"] if drv["name"] != "everywhere"
                                                                 else "IPP Everywhere"))
        if self.page.printer(self.name) is None:
            self.foot.say("이 프린터는 그 사이 제거되었습니다.", error=True)
            return
        _DriverDialog(self.page, self.name, self.d, changed)

    def _response(self, _d, resp):
        if resp != Gtk.ResponseType.OK:
            self.d.destroy()
            return
        p, name, page = self.p, self.name, self.page
        info_, loc, shared = self.info_e.get_text().strip(), self.loc_e.get_text().strip(), self.share_sw.get_active()
        err = _text_error(info_, "프린터 이름") or _text_error(loc, "위치")
        if err:
            self.foot.say(err, error=True)
            return
        if page.printer(name) is None:
            # 창을 연 사이 다른 곳에서 지워졌다 — lpadmin -p 는 없는 이름이면 빈 대기열을 새로 만든다
            self.foot.say("이 프린터는 그 사이 제거되었습니다.", error=True)
            self.ok.set_sensitive(False)
            return
        args, pairs = [], []
        if info_ != (p["info"] or name):
            args += ["-D", info_]
            pairs.append(f"info={info_}")
        if loc != p["location"]:
            args += ["-L", loc]
            pairs.append(f"location={loc}")
        if shared != p["shared"]:
            args += ["-o", f"printer-is-shared={'true' if shared else 'false'}"]
            pairs.append(f"shared={'yes' if shared else 'no'}")
        if not args:
            self.d.destroy()
            return
        if name in page.ops:
            return
        steps = [(["lpadmin", "-p", name] + args, True)]
        if shared and not p["shared"]:
            steps.append((["cupsctl", "--share-printers"], False))
        self.ok.set_sensitive(False)
        self.foot.say("저장하는 중…", busy=True)
        page._begin(name, "속성을 저장하는 중…")

        def done(ok, why):
            live = page._end(name)
            if self.alive:
                if ok:
                    self.d.destroy()
                else:
                    self.foot.say(f"저장하지 못했습니다: {why}", error=True)
                    self.ok.set_sensitive(True)
            if live and ok:
                page.say("프린터 속성을 저장했습니다")
        page._admin(steps, ["modify", name] + pairs, done)


# ── 인쇄 기본 설정 ──────────────────────────────────────────
class _Prefs:
    """인쇄 기본 설정 — 용지 크기 · 양면 · 색 · 품질 … (lpoptions -l). 윈도우의 "인쇄 기본값"처럼
    이 PC 의 모든 사용자에게 (lpadmin -o). 앱의 인쇄 창에서 문서마다 바꿀 수 있다"""

    def __init__(self, page, name):
        p = page.printer(name)
        self.page, self.name, self.alive, self.combos = page, name, True, {}
        d = self.d = Gtk.Dialog(title=f"{_title(p)} — 인쇄 기본 설정", transient_for=_parent(page.p), modal=True,
                                destroy_with_parent=True)
        self.box = _dialog_body(d, 620, 640)
        head, _t, _s = _head("인쇄 기본 설정", "이 PC 의 모든 사용자가 이 프린터로 인쇄할 때 먼저 골라지는 설정입니다. "
                                            "앱의 인쇄 창에서 문서마다 바꿀 수도 있습니다.")
        self.box.pack_start(head, False, False, 0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.pack_start(self.body, False, False, 0)
        s = _sect(self.body)
        r = Gtk.ListBoxRow()
        r.get_style_context().add_class("row")
        r.add(_busy_box("인쇄 설정을 읽는 중…"))
        s.add(r)
        self.foot = _Foot(d.get_content_area())
        self.foot.box.set_border_width(8)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        self.ok = d.add_button("저장", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("accent-btn")
        self.ok.set_sensitive(False)
        d.connect("response", self._response)
        d.connect("destroy", lambda *_: setattr(self, "alive", False))
        d.show_all()
        run_async(["env", "LC_ALL=C", "lpoptions", "-p", name, "-l"], self._loaded)

    def _loaded(self, ok, out, err):
        if not self.alive:
            return
        _clear(self.body)
        opts = parse_lpoptions(out) if ok else []
        if not opts:
            self.body.pack_start(_notice("이 프린터에는 바꿀 수 있는 인쇄 설정이 없습니다." if ok else
                                         f"인쇄 설정을 읽지 못했습니다: {_why(out, err)}"), False, False, 0)
            self.body.show_all()
            return
        groups = {"paper": [], "print": [], None: []}
        for o in opts:
            groups[option_name(o)[1]].append(o)
        for g, title in (("paper", "용지"), ("print", "인쇄")):
            if groups[g]:
                s = _sect(self.body, title)
                for o in groups[g]:
                    self._opt_row(s, o)
        if groups[None]:
            exp = Gtk.Expander(label=f"고급 옵션 ({len(groups[None])}개)")
            exp.get_style_context().add_class("section-title")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            s = _sect(inner)
            for o in groups[None]:
                self._opt_row(s, o)
            exp.add(inner)
            self.body.pack_start(exp, False, False, 0)
        self.body.show_all()
        self.ok.set_sensitive(True)

    def _opt_row(self, s, o):
        c = combo([(v, choice_name(o["key"], v)) for v in o["choices"]], o["default"])
        r = row(s, option_name(o)[0], None, control=c)
        r.set_tooltip_text(o["key"])
        self.combos[o["key"]] = (c, c.get_active_id())

    def _response(self, _d, resp):
        if resp != Gtk.ResponseType.OK:
            self.d.destroy()
            return
        page, name = self.page, self.name
        changed = [(k, c.get_active_id()) for k, (c, base) in self.combos.items()
                   if c.get_active_id() and c.get_active_id() != base and OPT_VAL_RE.fullmatch(c.get_active_id())]
        if not changed:
            self.d.destroy()
            return
        if name in page.ops:
            return
        self.ok.set_sensitive(False)
        self.foot.say("저장하는 중…", busy=True)
        page._begin(name, "인쇄 기본 설정을 저장하는 중…")

        def done(ok, why):
            if ok:
                _clear_user_options(name, [k for k, _v in changed])
            live = page._end(name)
            if self.alive:
                if ok:
                    self.d.destroy()
                else:
                    self.foot.say(f"저장하지 못했습니다: {why}", error=True)
                    self.ok.set_sensitive(True)
            if live and ok:
                page.say("인쇄 기본 설정을 저장했습니다")
        page._admin([(["lpadmin", "-p", name] + [x for k, v in changed for x in ("-o", f"{k}={v}")], True)],
                    ["options", name] + [f"{k}={v}" for k, v in changed], done)


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
        self.wizard = None         # 열려 있는 프린터 추가 마법사
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
        key = (tuple(items), self.scanning, self.found is None, self.scan_note)
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
                    ctl = button("직접 추가…", lambda f=f: self.open_wizard(f))
                    ctl.set_sensitive(not any("://" in k for k in self.ops))
                if where:
                    sub += f" · {where}"
                r = row(self.found_lb, name, sub, icon=PRINTER_ICONS if local else ["printer-network"] + PRINTER_ICONS,
                        control=ctl)
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                r.show_all()
            if self.found is not None and not self.scanning:
                row(self.found_lb, "원하는 프린터가 목록에 없나요?",
                    "드라이버가 필요한 옛 프린터, IP 주소나 호스트 이름으로 연결하는 프린터를 직접 추가합니다.",
                    icon=["list-add", "list-add-symbolic"],
                    control=button("직접 추가…", lambda: self.open_wizard(None))).show_all()
        _reveal(self.found_lb, bool(self.found_lb.get_children()))

    def _draw_detail(self):
        p = self.printer(self.open)
        name = p["name"]
        jobs = self._jobs_of(name)
        is_default = self.snap["default"] == name
        reach = self.reachable(p)
        key = (tuple(p.items()), is_default, len(jobs), self.ops.get(name), reach)
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
        b = button("열기", lambda: self.open_prefs(name))
        b.set_sensitive(not op)
        row(s, "인쇄 기본 설정", "용지 크기·양면 인쇄·색·인쇄 품질 (이 PC 의 모든 사용자)",
            icon=["document-page-setup", "preferences-desktop", "document-properties"], control=b)
        b = button("열기", lambda: self.open_props(name))
        b.set_sensitive(not op)
        row(s, "프린터 속성", "이름·위치·공유·드라이버",
            icon=["document-properties", "preferences-system"], control=b)
        b = button("제거", lambda: self.remove(name))
        b.set_sensitive(not op)
        row(s, "프린터 제거", "이 PC 에서 프린터를 지웁니다. 다시 쓰려면 ‘장치 추가’로 새로 추가합니다.",
            icon=["edit-delete", "user-trash"], control=b)

        s = _sect(self.detail, "프린터 정보")
        model = driver_label(p["model"].replace(" - IPP Everywhere", "")).strip()
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
        """_admin_run 앞에서 — 있는 프린터를 고치는 명령(lpadmin -p 이름 …, -v 없이)이면 그 프린터가 아직 있는지
        먼저 본다. lpadmin -p 는 없는 이름이면 빈 대기열(file:///dev/null)을 새로 만들어 버린다
        (속성 창을 연 사이 다른 곳에서 지웠을 때)"""
        modify = next((c for c, _m in steps if c[:2] == ["lpadmin", "-p"] and len(c) > 2
                       and "-v" not in c and "-x" not in c), None)
        if modify is None:
            self._admin_run(steps, helper_args, done, direct, undo)
            return

        def checked(ok, _out, err):
            # (CUPS 가 멈춘 것 같은 다른 실패는 그대로 진행해 lpadmin 이 까닭을 알리게 한다)
            if not ok and re.search(r"(?i)invalid destination|not exist|없", err or ""):
                done(False, "이 프린터는 그 사이 제거되었습니다")
                return
            self._admin_run(steps, helper_args, done, direct, undo)
        run_async(["env", "LC_ALL=C", "lpstat", "-p", modify[2]], checked)

    def _admin_run(self, steps, helper_args, done, direct=None, undo=None):
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

    def test_page(self, name, then=None):
        """then(성공?, 알림 문구): 마법사처럼 결과를 제 창에도 보여 줄 때"""
        if not os.path.exists(TESTPAGE):
            msg = f"테스트 페이지 파일({TESTPAGE})이 없습니다."
            self.say(msg, error=True)
            if then:
                then(False, msg)
            return

        def done(ok, out, err):
            if self.dead:
                return
            msg = ("테스트 페이지를 보냈습니다. 인쇄되지 않으면 ‘인쇄 대기열 열기’에서 상태를 확인하세요." if ok
                   else f"테스트 페이지를 보내지 못했습니다: {_why(out, err)}")
            self.say(msg, error=not ok)
            if then:
                then(ok, msg)
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

    # ── 우리 창들 (추가 마법사 · 속성 · 인쇄 기본 설정) ──
    def _admin_read(self, cmd, helper_args, done):
        """출력이 필요한 CUPS 관리 명령(lpinfo -v) — 관리 권한이 있으면 직접, 막히거나 없으면 도우미(pkexec).
        done(성공?, 출력, 이유)"""
        def helper():
            if not os.path.exists(HELPER):
                done(False, "", "프린터 도우미가 없습니다 — SekaiOS 업데이트를 받아 주세요")
                return
            run_async(["pkexec", HELPER] + helper_args,
                      lambda ok, out, err: done(ok, out if ok else "", "" if ok else (
                          _why(out, "") if out.strip() else failure_reason(err))))

        def finished(ok, out, err):
            if ok:
                done(True, out, "")
            elif _denied(err or out):
                helper()
            else:
                done(False, "", _why(out, err))
        if _can_admin():
            run_async(["env", "LC_ALL=C"] + cmd, finished)
        else:
            helper()

    def open_wizard(self, found=None):
        """프린터 추가 마법사. found: 찾은 프린터(드라이버가 필요한 것) — 있으면 드라이버 고르기부터"""
        if self.cups != "ok":
            return
        if self.wizard is not None:
            self.wizard.present()
            return
        dev = None
        if found is not None:
            dev = {"uri": found["uri"], "name": found["name"], "model": found["model"], "devid": found["devid"],
                   "net": not found["local"], "driverless": found["driverless"], "added": None, "virtual": False}
        self.wizard = _AddWizard(self, dev)

    def open_props(self, name):
        if self.printer(name) is not None and name not in self.ops:
            _Props(self, name)

    def open_prefs(self, name):
        if self.printer(name) is not None and name not in self.ops:
            _Prefs(self, name)


def build(store):
    return PrintersPage(store).widget


PAGES = [{"id": "printers", "title": "프린터",
          "icon": PRINTER_ICONS,
          "build": build}]
