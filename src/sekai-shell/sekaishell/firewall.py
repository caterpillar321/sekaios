"""방화벽 — 설정 › 방화벽과 "허용할까요?" 창(fwprompt)이 함께 쓰는 것: firewalld D-Bus 읽기, 연결을 기다리는 앱 찾기.
바꾸기는 언제나 /usr/libexec/sekai/sekai-firewall (pkexec)."""
import os
import re
import subprocess

from gi.repository import GLib

from . import dbg

HELPER = "/usr/libexec/sekai/sekai-firewall"


def _out(cmd, timeout=6):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8"))
        return r.returncode, r.stdout.strip()
    except Exception as e:
        dbg("[방화벽] 실행 실패", cmd, e)
        return 1, ""


FW_BUS, FW_PATH = "org.fedoraproject.FirewallD1", "/org/fedoraproject/FirewallD1"


def _fw_call(method, args=None, iface="org.fedoraproject.FirewallD1.zone"):
    """firewalld 의 D-Bus 읽기 함수를 직접 — firewall-cmd 는 실행마다 설정 변경 권한부터 청해 인증 창을 띄웠다.
    읽기(info)는 polkit 규칙(50-sekai-firewall-info.rules)이 로그인한 사람에게 허용한다. 인증을 묻지 않는다"""
    from gi.repository import Gio
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    r = bus.call_sync(FW_BUS, FW_PATH, iface, method, args, None, Gio.DBusCallFlags.NONE, 4000, None)
    return r.unpack()[0]


def listening_apps():
    """밖에서 들어오는 연결을 기다리는 내 앱들 — [{id, name, ports}] (127.0.0.1·::1 에만 묶인 것은 빼고)"""
    _, out = _out(["ss", "-ltnupH"])
    apps = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        proto = "tcp" if parts[0].startswith("tcp") else "udp"
        local = parts[4]
        addr, _, port = local.rpartition(":")
        if not port.isdigit() or addr.strip("[]") in ("127.0.0.1", "::1") or addr.startswith("127."):
            continue
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if not m:
            continue                                     # 다른 사용자(관리자)의 프로세스 — 이름을 볼 수 없다
        name, pid = m.group(1), m.group(2)
        a = apps.setdefault(name, {"name": name, "ports": [], "pids": set()})
        a["pids"].add(pid)
        p = f"{port}/{proto}"
        if p not in a["ports"]:
            a["ports"].append(p)
    out = []
    for name, a in apps.items():
        app_id = re.sub(r"[^a-z0-9.-]", "-", name.lower()).strip("-.")[:40] or "app"
        try:
            exe = os.readlink(f"/proc/{next(iter(a['pids']))}/exe")
        except OSError:
            exe = name
        out.append({"id": app_id, "name": _pretty_name(name, a["pids"]), "ports": sorted(a["ports"])[:16], "exe": exe})
    return sorted(out, key=lambda x: x["name"].lower())


def _pretty_name(proc, pids):
    """프로세스 이름 → 시작 메뉴의 앱 이름 (실행 파일이 같은 .desktop 이 있으면). 없으면 프로세스 이름"""
    try:
        from gi.repository import Gio
        exe = os.path.basename(os.readlink(f"/proc/{next(iter(pids))}/exe"))
        for info in Gio.AppInfo.get_all():
            ex = (info.get_executable() or "")
            if ex and os.path.basename(ex) in (exe, proc):
                return re.sub(r"[\x00-\x1f<>&\"']", "", info.get_display_name() or proc)[:60] or proc
    except Exception:
        pass
    return re.sub(r"[\x00-\x1f<>&\"']", "", proc)[:60] or "앱"


def running():
    return _out(["systemctl", "is-active", "firewalld.service"])[1] == "active"


def zone_ports(zone):
    """그 존에서 열린 포트 — 서비스의 포트까지 펼쳐서 {"8765/tcp", …}"""
    out = set()
    try:
        out |= {f"{p}/{proto}" for p, proto in _fw_call("getPorts", GLib.Variant("(s)", (zone,)))}
        for svc in _fw_call("getServices", GLib.Variant("(s)", (zone,))):
            cfg = _fw_call("getServiceSettings2", GLib.Variant("(s)", (svc,)), iface="org.fedoraproject.FirewallD1")
            out |= {f"{p}/{proto}" for p, proto in cfg.get("ports", [])}
    except Exception as e:
        dbg("[방화벽] 존을 읽지 못함", zone, e)
    return out


def active_zones():
    """지금 연결된 네트워크들의 존 — 비어 있으면(존을 안 정한 연결) public"""
    zones = set()
    _, out = _out(["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"])
    for line in out.splitlines():
        uuid, _, dev = line.partition(":")
        if not uuid or dev in ("", "lo"):
            continue
        z = _out(["nmcli", "-g", "connection.zone", "connection", "show", "uuid", uuid])[1].strip()
        zones.add(z if z in ("public", "home") else "public")
    return zones or {"public"}
