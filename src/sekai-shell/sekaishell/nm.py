"""NetworkManager(nmcli) — 설정 › 네트워크와 빠른 설정이 함께 쓴다 (GTK 없음, 작업 스레드에서 부른다).

목록 읽기 · Wi-Fi 연결(암호는 명령줄 대신 메모리 파일 passwd-file 로) · VPN 가져오기 · 오류를 한국어 한 줄로 ·
입력 검사 · 속성 요약. 오류를 가려 읽을 수 있게 nmcli 는 LC_ALL=C.UTF-8 로 부른다 (한국어 번역이 깔려 있으면
오류 문구가 바뀐다).
예전에는 설정 페이지(sekaisettings/pages/network.py)에 있었고, 빠른 설정이 nmcli 처리를 따로 한 벌 더 들고 있었다
(카나데 — 중복·층 위반)."""
import ipaddress
import os
import re
import shutil
import subprocess
import tempfile


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


OPENVPN_NAMES = ("/usr/lib/NetworkManager/VPN/nm-openvpn-service.name",
                 "/etc/NetworkManager/VPN/nm-openvpn-service.name")


WG_MAX = 64 * 1024                       # WireGuard 구성 파일은 몇 백 바이트 — 엉뚱한 큰 파일을 막는다


def _env():
    return dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")


def _nmrun(args, timeout=10, pass_fds=()):
    """(작업 스레드) nmcli → (성공?, 표준 출력, 표준 오류)"""
    try:
        r = subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout, env=_env(),
                           pass_fds=pass_fds)
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except OSError as e:
        return False, "", str(e)
    return r.returncode == 0, r.stdout, r.stderr


def _nm(*args, timeout=8):
    """간결 출력(-t)의 표준 출력만 (실패하면 빈 문자열)"""
    return _nmrun(["-t", "-c", "no", *args], timeout)[1]


def _unescape(v):
    """nmcli 간결 출력(-t, -g)의 \\: \\\\ 를 원래 글자로"""
    return re.sub(r"\\(.)", r"\1", v)


def _fields(line):
    """nmcli -t 한 줄 → 칸들. 값 안의 : 와 \\ 는 \\: \\\\ 로 이스케이프되어 온다 (SSID 에 : 가 들어갈 수 있다)"""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_kv(text):
    """nmcli -t 의 '이름:값' 줄들 (connection show <UUID> · device show) → {이름: 값}. 이름에는 : 가 없다.
    이 상세 출력은 값을 이스케이프하지 않는다 (목록 출력·-g 와 다르다 — nmcli 1.52 에서 확인) → 풀지 않는다.
    풀면 'CORP\\hong' 같은 사용자 이름이 'CORPhong' 으로 읽혀, 암호만 바꿔도 사용자 이름을 망가뜨려 저장했다"""
    out = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip()
    return out


def _list(v):
    """목록 값 ('a/24, b/24' · '1.1.1.1,8.8.8.8') → [a, b]"""
    return [x.strip() for x in (v or "").split(",") if x.strip() and x.strip() != "--"]


def _multi(kv, prefix):
    """IP4.ADDRESS[1] · IP4.ADDRESS[2] … → [값…]"""
    items = sorted(((int(m.group(1)), v) for k, v in kv.items()
                    for m in [re.fullmatch(re.escape(prefix) + r"\[(\d+)\]", k)] if m), key=lambda x: x[0])
    return [v for _i, v in items if v]


def _blank(v):
    return "" if v in (None, "--", "") else v


# nmcli 오류(LC_ALL=C) → 사람이 읽을 말 (소문자로 찾는다). 앞의 것이 먼저
NM_ERRORS = (
    ("not authorized", "관리자 인증이 취소되었거나 권한이 없어 바꾸지 못했습니다"),
    ("insufficient privileges", "관리자 인증이 취소되었거나 권한이 없어 바꾸지 못했습니다"),
    ("permission denied", "관리자 인증이 취소되었거나 권한이 없어 바꾸지 못했습니다"),
    ("networkmanager is not running", "네트워크 서비스(NetworkManager)가 실행되고 있지 않습니다"),
    ("no network with ssid", "네트워크를 찾을 수 없습니다 — 가까이에 있는지 확인하세요"),
    ("wi-fi network could not be found", "네트워크를 찾을 수 없습니다 — 가까이에 있는지 확인하세요"),
    ("ip configuration could not be reserved", "IP 주소를 받지 못했습니다 — 공유기(DHCP)가 대답하지 않습니다"),
    ("no carrier", "케이블이 연결되어 있지 않습니다"),
    ("carrier", "케이블이 연결되어 있지 않습니다"),
    ("802.1x supplicant", "인증에 실패했습니다 — 사용자 이름·암호와 인증서 설정을 확인하세요"),
    ("supplicant", "무선 연결에 실패했습니다"),
    ("no suitable device", "이 연결을 쓸 수 있는 장치가 없습니다"),
    ("no valid vpn", "이 VPN 을 쓰는 데 필요한 플러그인이 설치되어 있지 않습니다"),
    ("vpn service", "VPN 서비스를 시작하지 못했습니다 — 서버 주소와 구성 파일을 확인하세요"),
    ("vpn connection", "VPN 에 연결하지 못했습니다 — 서버 주소와 계정을 확인하세요"),
    ("base network connection was interrupted", "기본 네트워크 연결이 끊어졌습니다"),
    ("unknown connection", "없는 연결입니다 — 이미 지워졌을 수 있습니다"),
    ("not an active connection", "연결되어 있지 않습니다"),
    ("timeout", "시간이 초과되었습니다"),
    ("timed out", "시간이 초과되었습니다"),
    ("failed to read", "파일을 읽지 못했습니다"),
    ("failed to import", "구성 파일을 가져오지 못했습니다 — 올바른 VPN 구성 파일인지 확인하세요"),
    ("invalid", "설정 값이 올바르지 않습니다"),
)


def _secrets_error(err):
    low = (err or "").lower()
    return "secrets were required" in low or "no secrets" in low or "802-11-wireless-security" in low \
        or "802-1x" in low or "vpn.secrets" in low


def nm_error(err, fallback="실패했습니다", secrets="암호가 맞지 않습니다"):
    """nmcli 의 오류 출력 → 한국어 한 줄"""
    text = (err or "").strip()
    if not text:
        return fallback
    if _secrets_error(text):
        return secrets
    low = text.lower()
    for key, msg in NM_ERRORS:
        if key in low:
            return msg
    line = text.splitlines()[-1]
    return re.sub(r"^Error:\s*", "", line) or fallback


def parse_devices(text):
    out = []
    for line in (text or "").splitlines():
        f = _fields(line)
        if len(f) >= 4 and f[1] not in ("loopback",):
            out.append({"dev": f[0], "type": f[1], "state": f[2], "conn": _blank(f[3]),
                        "uuid": _blank(f[4]) if len(f) > 4 else ""})
    return out


def parse_connections(text):
    out = []
    for line in (text or "").splitlines():
        f = _fields(line)
        if len(f) >= 6 and UUID_RE.fullmatch(f[1]):
            out.append({"name": f[0], "uuid": f[1], "type": f[2], "dev": _blank(f[3]),
                        "active": f[4] == "yes", "state": _blank(f[5])})
    return out


def parse_wifi(text):
    """nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list → SSID 마다 하나 (가장 센 것).
    연결된 것 먼저, 그다음 신호 센 순서. 숨은 SSID(빈 이름)는 뺀다"""
    nets = {}
    for line in (text or "").splitlines():
        f = _fields(line)
        if len(f) < 4 or not f[1]:
            continue
        try:
            sig = int(f[2])
        except ValueError:
            sig = 0
        sec = f[3].strip()
        sec = "" if sec in ("--", "") else sec
        active = f[0].strip() == "*"
        cur = nets.get(f[1])
        if cur is None:
            nets[f[1]] = {"ssid": f[1], "signal": sig, "sec": sec, "active": active}
        else:
            cur["active"] = cur["active"] or active
            if sig > cur["signal"]:
                cur["signal"], cur["sec"] = sig, sec
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"], n["ssid"].lower()))


def _ip4(dev):
    for line in _nm("-f", "IP4.ADDRESS", "device", "show", dev).splitlines():
        if ":" in line:
            v = line.split(":", 1)[1]
            if v:
                return v
    return ""


def read_state():
    """장치 · 연결 프로필 · Wi-Fi 켜짐 — nmcli 를 여러 번 부르니 작업 스레드에서"""
    running = _nm("-f", "RUNNING", "general").strip() == "running"
    if not running:
        return {"running": False, "devs": [], "conns": [], "radio": False}
    devs = parse_devices(_nm("-f", "DEVICE,TYPE,STATE,CONNECTION,CON-UUID", "device"))
    for d in devs:
        d["ip"] = _ip4(d["dev"]) if d["state"].startswith("connected") else ""
    conns = parse_connections(_nm("-f", "NAME,UUID,TYPE,DEVICE,ACTIVE,STATE", "connection", "show"))
    radio = any(d["type"] == "wifi" for d in devs) and _nm("radio", "wifi").strip() == "enabled"
    return {"running": True, "devs": devs, "conns": conns, "radio": radio}


def read_props(uuid, dev=None):
    """연결 하나의 설정과 (연결되어 있으면) 장치 정보 · 연결된 AP — 작업 스레드에서. 없으면 None"""
    ok, out, _err = _nmrun(["-t", "-c", "no", "connection", "show", uuid])
    if not ok:
        return None
    s = parse_kv(out)
    dev = dev or (_list(s.get("GENERAL.DEVICES", "")) or [""])[0] or s.get("connection.interface-name", "")
    d, ap = {}, {}
    if dev and s.get("GENERAL.STATE"):
        d = parse_kv(_nm("-f", "GENERAL,CAPABILITIES,IP4,IP6", "device", "show", dev))
        if s.get("connection.type") == "802-11-wireless":
            for line in _nm("-f", "IN-USE,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY", "device", "wifi", "list",
                            "ifname", dev, "--rescan", "no").splitlines():
                f = _fields(line)
                if len(f) >= 7 and f[0].strip() == "*":
                    ap = {"ssid": f[1], "chan": f[2], "freq": f[3], "rate": f[4], "signal": f[5], "sec": f[6]}
                    break
    return {"uuid": uuid, "s": s, "d": d, "ap": ap, "dev": dev}


def saved_uuid(ssid):
    """(작업 스레드) 이 SSID 의 저장된 프로필 — 있으면 UUID. 프로필 이름이 SSID 와 다를 수 있어 SSID 를 직접 본다"""
    for c in parse_connections(_nm("-f", "NAME,UUID,TYPE,DEVICE,ACTIVE,STATE", "connection", "show")):
        if c["type"] != "802-11-wireless":
            continue
        ok, out, _e = _nmrun(["-g", "802-11-wireless.ssid", "connection", "show", c["uuid"]])
        # -g 도 간결 출력이라 : 와 \ 가 이스케이프되어 온다 — 풀어야 'Home:5G' 같은 SSID 의 프로필을 찾는다
        if ok and _unescape(out.rstrip("\n")) == ssid:
            return c["uuid"]
    return None


def _key_mgmt(sec):
    """nmcli SECURITY 칸(WPA2·WPA3·WEP…) → 802-11-wireless-security.key-mgmt"""
    u = (sec or "").upper()
    if "802.1X" in u:
        return "wpa-eap"
    if "WPA3" in u and "WPA1" not in u and "WPA2" not in u:
        return "sae"
    if "WPA" in u:
        return "wpa-psk"
    if "WEP" in u:
        return "none"
    return None


def is_enterprise(sec):
    """회사·학교 네트워크 (WPA2/WPA3-Enterprise, 802.1X) — 암호 하나가 아니라 계정으로 로그인한다"""
    return "802.1X" in (sec or "").upper()


def passwd_file_value(secret):
    """nmcli passwd-file 의 값 — 바이트마다 \\ooo(8진수)로 적는다. nmcli 는 이 파일의 값에서 \\ 이스케이프를 풀고
    앞뒤 공백을 지운다 → 그대로 적으면 "my\\pass"·"끝에 공백 " 같은 암호가 바뀌어 "암호가 맞지 않습니다"가 났다"""
    return "".join("\\%03o" % b for b in secret.encode("utf-8"))


def _up_secret(uuid, field, secret, timeout=45):
    """연결을 켠다. 비밀번호는 명령줄에 넣지 않고 `connection up … passwd-file` 로 메모리 파일(memfd)에
    담아 넘긴다 — NetworkManager 는 이렇게 받은 시스템 소유(flags 0) 비밀번호를 프로필에 저장한다.
    반환: None(성공) 또는 nmcli 의 오류 원문"""
    if not field or secret is None:
        ok, _o, err = _nmrun(["-w", str(timeout), "connection", "up", uuid], timeout + 15)
        return None if ok else (err or "알 수 없는 오류")
    fd = os.memfd_create("sekai-net", os.MFD_CLOEXEC)
    try:
        os.write(fd, f"{field}:{passwd_file_value(secret)}\n".encode())
        os.lseek(fd, 0, os.SEEK_SET)
        ok, _o, err = _nmrun(["-w", str(timeout), "connection", "up", uuid, "passwd-file", f"/dev/fd/{fd}"],
                             timeout + 15, pass_fds=(fd,))
    finally:
        os.close(fd)
    return None if ok else (err or "알 수 없는 오류")


def _wep_type(key):
    """WEP: 5·13 글자나 10·26 자리 16진수면 키(1), 아니면 암호문(2)"""
    return "1" if len(key) in (5, 13) or (len(key) in (10, 26) and re.fullmatch(r"[0-9A-Fa-f]+", key)) else "2"


def connect_wifi(spec, dev=None, timeout=45):
    """(작업 스레드) Wi-Fi 에 연결 — 같은 SSID 의 프로필이 있으면 고쳐 쓰고, 없으면 새로 만든다.
    새로 만든 프로필로 연결하지 못하면 그 프로필은 지운다. 반환: None(성공) 또는 한국어 오류 한 줄.
    spec: {"ssid", "hidden", "security": open|wpa-psk|sae|wep|eap, "secret", "autoconnect",
           (eap) "eap": peap|ttls, "phase2", "identity", "anon", "ca": system|none, "domain"}"""
    ssid, sec = spec["ssid"], spec["security"]
    props = ["802-11-wireless.hidden", "yes" if spec.get("hidden") else "no",
             "connection.autoconnect", "yes" if spec.get("autoconnect", True) else "no"]
    field = None
    if sec in ("wpa-psk", "sae"):
        props += ["wifi-sec.key-mgmt", sec, "wifi-sec.psk-flags", "0"]
        field = "802-11-wireless-security.psk"
    elif sec == "wep":
        props += ["wifi-sec.key-mgmt", "none", "wifi-sec.wep-key-type", _wep_type(spec["secret"]),
                  "wifi-sec.wep-key-flags", "0"]
        field = "802-11-wireless-security.wep-key0"
    elif sec == "eap":
        props += ["wifi-sec.key-mgmt", "wpa-eap", "802-1x.eap", spec["eap"], "802-1x.phase2-auth", spec["phase2"],
                  "802-1x.identity", spec["identity"], "802-1x.anonymous-identity", spec.get("anon", ""),
                  "802-1x.password-flags", "0"]
        if spec.get("ca") == "system":
            # 시스템이 믿는 인증 기관 + 서버 이름(도메인) 확인 — 같은 이름의 가짜 네트워크에 암호를 보내지 않게
            props += ["802-1x.ca-path", "/etc/ssl/certs", "802-1x.domain-suffix-match", spec["domain"]]
        else:
            props += ["802-1x.ca-path", "", "802-1x.domain-suffix-match", ""]
        field = "802-1x.password"
    uuid, created = saved_uuid(ssid), False
    if uuid:
        if sec == "open":
            _nmrun(["connection", "modify", uuid, "remove", "802-11-wireless-security"])
        ok, _o, err = _nmrun(["connection", "modify", uuid] + props, 60)
        if not ok:
            return nm_error(err, "저장된 프로필을 고치지 못했습니다")
    else:
        add = ["connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid] + \
              (["ifname", dev] if dev else []) + props
        ok, out, err = _nmrun(add, 60)
        m = UUID_RE.search(out + err)
        if not ok or not m:
            return nm_error(err or out, "프로필을 만들지 못했습니다")
        uuid, created = m.group(0), True
    err = _up_secret(uuid, field, spec.get("secret"), timeout)
    if err is not None and created:
        _nmrun(["connection", "delete", uuid], 15)
    if err is None:
        return None
    return nm_error(err, "연결하지 못했습니다",
                    secrets="사용자 이름 또는 암호가 맞지 않습니다" if sec == "eap" else "암호가 맞지 않습니다")


def wifi_connect_secret(ssid, sec, pw, dev=None, timeout=45):
    """암호가 있는 Wi-Fi 에 연결 (빠른 설정이 부른다) — connect_wifi 의 개인용(WPA·WEP) 줄임.
    반환: None(성공) 또는 오류 한 줄"""
    km = _key_mgmt(sec)
    if km == "wpa-eap":
        return "회사·학교 네트워크는 계정으로 로그인해야 합니다"
    if km is None:
        km = "wpa-psk"
    return connect_wifi({"ssid": ssid, "security": "wep" if km == "none" else km, "secret": pw}, dev, timeout)


def wifi_up_saved(ssid):
    """(작업 스레드) 저장된 프로필로 연결 → ("ok"|"none"|"secrets"|"error", 오류 한 줄)"""
    uuid = saved_uuid(ssid)
    if uuid is None:
        return "none", ""
    err = _up_secret(uuid, None, None)
    if err is None:
        return "ok", ""
    if _secrets_error(err):
        return "secrets", ""
    return "error", nm_error(err, "연결하지 못했습니다")


def openvpn_available():
    return any(os.path.exists(p) for p in OPENVPN_NAMES)


def _wg_ifname(name, taken):
    """WireGuard 인터페이스 이름 — nmcli 는 파일 이름(확장자 앞)을 쓰고, 리눅스 인터페이스 이름 규칙
    (15자, 영문·숫자·_=+.-)을 지켜야 가져온다"""
    base = re.sub(r"[^A-Za-z0-9_=+.-]", "", name or "")[:12] or "wg"
    low = {t.lower() for t in taken}
    cand, n = base, 1
    while cand.lower() in low:
        cand = f"{base[:12]}{n}"
        n += 1
    return cand


def import_vpn(kind, path, name):
    """(작업 스레드) VPN 구성 파일 가져오기 → (UUID|None, 오류|None). 가져온 연결은 자동으로 켜지지 않게"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "파일을 읽을 수 없습니다"
    if size > WG_MAX * (16 if kind == "openvpn" else 1):
        return None, "구성 파일이 너무 큽니다 — 올바른 VPN 구성 파일인지 확인하세요"
    tmpdir = None
    try:
        if kind == "wireguard":
            try:
                with open(path, encoding="utf-8", errors="strict") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                return None, "파일을 읽을 수 없습니다"
            if not re.search(r"(?im)^\s*\[Interface\]", text) or not re.search(r"(?im)^\s*PrivateKey\s*=", text):
                return None, "WireGuard 구성 파일이 아닙니다 ([Interface] 와 PrivateKey 가 있어야 합니다)"
            taken = set(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else set()
            for c in parse_connections(_nm("-f", "NAME,UUID,TYPE,DEVICE,ACTIVE,STATE", "connection", "show")):
                if c["type"] == "wireguard":
                    ok, out, _e = _nmrun(["-g", "connection.interface-name", "connection", "show", c["uuid"]])
                    taken.add(out.strip() if ok else c["name"])
            # 파일 이름(wg0.conf · mullvad-kr1.conf)이 쓸 만하면 그것, 아니면 연결 이름에서
            stem = os.path.splitext(os.path.basename(path))[0]
            iface = _wg_ifname(stem if re.search(r"[A-Za-z0-9]", stem) else name, taken)
            # 개인 키가 든 파일 — 나만 읽을 수 있는 임시 폴더에 인터페이스 이름으로 복사해 가져온 뒤 지운다
            tmpdir = tempfile.mkdtemp(prefix="sekai-wg-")
            src = os.path.join(tmpdir, iface + ".conf")
            fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            src = path
        ok, out, err = _nmrun(["connection", "import", "type", kind, "file", src], 60)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    m = UUID_RE.search(out + err)
    if not ok or not m:
        return None, nm_error(err or out, "구성 파일을 가져오지 못했습니다")
    uuid = m.group(0)
    args = ["connection", "modify", uuid, "connection.autoconnect", "no"]
    if name:
        args += ["connection.id", name]
    _nmrun(args, 60)
    if kind == "wireguard":
        _nmrun(["connection", "down", uuid], 20)       # 가져오자마자 켜졌으면 끈다 — 연결은 사용자가 고를 때
    return uuid, None


def _josa(word, with_final, without):
    ch = word[-1:] or "가"
    return with_final if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0 else without


def check_ipv4(addr, mask, gw):
    """(오류|None, "주소/접두사", 게이트웨이)"""
    try:
        ip = ipaddress.IPv4Address(addr.strip())
    except ValueError:
        return "IPv4 주소가 올바르지 않습니다 (예: 192.168.0.10)", None, None
    m = mask.strip()
    try:
        prefix = int(m) if m.isdigit() else ipaddress.IPv4Network(f"0.0.0.0/{m}").prefixlen
    except ValueError:
        return "서브넷 마스크가 올바르지 않습니다 (예: 255.255.255.0 또는 24)", None, None
    if not 1 <= prefix <= 32:
        return "서브넷 마스크가 올바르지 않습니다 (예: 255.255.255.0 또는 24)", None, None
    net = ipaddress.IPv4Interface(f"{ip}/{prefix}").network
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified or ip == ipaddress.IPv4Address("255.255.255.255"):
        return "이 IPv4 주소는 컴퓨터에 쓸 수 없습니다", None, None
    if prefix <= 30 and ip in (net.network_address, net.broadcast_address):
        return "네트워크 주소나 브로드캐스트 주소는 컴퓨터에 쓸 수 없습니다", None, None
    g = gw.strip()
    if g:
        try:
            gip = ipaddress.IPv4Address(g)
        except ValueError:
            return "게이트웨이 주소가 올바르지 않습니다 (예: 192.168.0.1)", None, None
        if gip == ip:
            return "게이트웨이는 이 컴퓨터의 주소와 달라야 합니다", None, None
        if gip not in net:
            return f"게이트웨이가 같은 서브넷({net})에 있지 않습니다", None, None
    return None, f"{ip}/{prefix}", g


def check_ipv6(addr, prefix, gw):
    try:
        ip = ipaddress.IPv6Address(addr.strip())
    except ValueError:
        return "IPv6 주소가 올바르지 않습니다 (예: 2001:db8::10)", None, None
    p = prefix.strip() or "64"
    if not p.isdigit() or not 1 <= int(p) <= 128:
        return "서브넷 접두사 길이는 1~128 사이의 숫자입니다 (보통 64)", None, None
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified:
        return "이 IPv6 주소는 컴퓨터에 쓸 수 없습니다", None, None
    g = gw.strip()
    if g:
        try:
            gip = ipaddress.IPv6Address(g)
        except ValueError:
            return "IPv6 게이트웨이 주소가 올바르지 않습니다 (예: fe80::1)", None, None
        if gip == ip or gip.is_multicast or gip.is_unspecified:
            return "IPv6 게이트웨이 주소가 올바르지 않습니다", None, None
    return None, f"{ip}/{int(p)}", g


def check_dns(text, what):
    """DNS 서버 한 칸 → (오류|None, 주소|"")"""
    t = text.strip()
    if not t:
        return None, ""
    try:
        ip = ipaddress.ip_address(t)
    except ValueError:
        return f"{what}{_josa(what, '이', '가')} 올바르지 않습니다 (예: 1.1.1.1)", None
    if ip.is_multicast or ip.is_unspecified:
        return f"{what}{_josa(what, '으로', '로')} 쓸 수 없는 주소입니다", None
    return None, str(ip)


def check_text(text, what, limit=64):
    if not text.strip():
        return f"{what}{_josa(what, '을', '를')} 입력하세요"
    if re.search(r"[\x00-\x1f\x7f]", text):
        return f"{what}에 쓸 수 없는 글자가 있습니다"
    if len(text) > limit:
        return f"{what}{_josa(what, '이', '가')} 너무 깁니다 ({limit}자까지)"
    return ""


def check_domain(text):
    t = text.strip().rstrip(".")
    if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*", t):
        return "서버 도메인이 올바르지 않습니다 (예: radius.example.ac.kr)"
    return ""


def check_wifi_key(sec, key):
    if sec == "wpa-psk":
        if re.fullmatch(r"[0-9A-Fa-f]{64}", key) or (8 <= len(key) <= 63 and key.isascii() and key.isprintable()):
            return ""
        return "보안 키는 8~63자여야 합니다 (영문·숫자·기호)"
    if sec == "sae":
        return "" if 1 <= len(key) <= 128 and key.isprintable() else "보안 키를 입력하세요 (128자까지)"
    if sec == "wep":
        return "" if 1 <= len(key) <= 64 and key.isprintable() else "WEP 키를 입력하세요"
    return ""


def _dev_state(s, typ=None):
    s = s or ""
    if s.startswith("connected"):
        return "연결됨"
    if s.startswith("connecting"):
        return "연결 중…"
    if s.startswith("deactivating"):
        return "연결을 끊는 중…"
    if s == "unavailable":
        return "케이블이 연결되어 있지 않음" if typ == "ethernet" else "사용할 수 없음"
    return {"disconnected": "연결 안 됨", "unmanaged": "관리하지 않음"}.get(s, s or "알 수 없음")


def conn_label(name):
    """NetworkManager 가 스스로 만든 프로필 이름("Wired connection 1")은 우리 말로 보인다 (이름 자체는 그대로)"""
    m = re.fullmatch(r"Wired connection (\d+)", name or "")
    return f"유선 연결 {m.group(1)}" if m else (name or "")


def sec_text(sec):
    if not sec:
        return "개방 (보안 없음)"
    if is_enterprise(sec):
        return "회사·학교 (802.1X)"
    kinds = [k for k in ("WPA3", "WPA2", "WPA1", "WEP", "OWE") if k in sec.upper()]
    return "보안 (" + "/".join(kinds or [sec]) + ")"


DEV_TYPES = {"bridge": "브리지", "bond": "본딩", "vlan": "VLAN", "gsm": "모바일 광대역", "cdma": "모바일 광대역",
             "bt": "블루투스", "team": "팀", "infiniband": "InfiniBand", "macvlan": "MACVLAN", "veth": "가상 이더넷",
             "wifi-p2p": "Wi-Fi Direct", "ovs-interface": "Open vSwitch", "modem": "모뎀"}


KEY_MGMT = {"none": "WEP", "wpa-psk": "WPA2-개인", "sae": "WPA3-개인", "wpa-eap": "WPA2/WPA3-엔터프라이즈",
            "owe": "보안 개방 (OWE)", "wpa-eap-suite-b-192": "WPA3-엔터프라이즈 192비트"}


DOT = {"-1": "default", "default": "default", "0": "no", "no": "no", "1": "opportunistic",
       "opportunistic": "opportunistic", "2": "yes", "yes": "yes"}


def _dot(v):
    return DOT.get((v or "").split(" ", 1)[0], "default")


def ip_summary(s):
    """IP 할당 한 줄 — 자동(DHCP) · 수동 (주소) · 끔"""
    parts = []
    for fam, name in (("ipv4", "IPv4"), ("ipv6", "IPv6")):
        m = s.get(f"{fam}.method", "auto")
        if m == "manual":
            addrs = _list(s.get(f"{fam}.addresses"))
            parts.append(f"{name} 수동" + (f" ({addrs[0]})" if addrs else ""))
        elif m in ("disabled", "ignore"):
            parts.append(f"{name} 끔")
    return "자동(DHCP)" if not parts else " · ".join(parts)


def dns_manual(s):
    return s.get("ipv4.ignore-auto-dns") == "yes" or s.get("ipv6.ignore-auto-dns") == "yes" or \
        bool(_list(s.get("ipv4.dns")) or _list(s.get("ipv6.dns")))


def dns_summary(s):
    servers = _list(s.get("ipv4.dns")) + _list(s.get("ipv6.dns"))
    if not dns_manual(s):
        return "자동(DHCP)"
    return "수동 (" + ", ".join(servers) + ")" if servers else "수동 (서버 없음)"


def _freq_band(freq):
    m = re.match(r"(\d+)", freq or "")
    if not m:
        return ""
    f = int(m.group(1))
    return "2.4GHz" if f < 3000 else "5GHz" if f < 5925 else "6GHz"
