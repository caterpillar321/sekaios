"""방화벽 — 윈도우 디펜더 방화벽처럼. 네트워크마다 공용(들어오는 연결을 모두 막음) / 개인(기기 찾기 같은 것만 엶).

엔진은 firewalld — NetworkManager 가 연결마다 존(connection.zone)을 알려 준다: 공용 = public, 개인 = home.
보기(상태·목록)는 사용자 권한의 firewall-cmd · nmcli · systemctl 로, 바꾸기는 /usr/libexec/sekai/sekai-firewall
(pkexec — 인자는 도우미가 엄격히 검사한다).
"""
import os
import re
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sekaishell.firewall import _fw_call, listening_apps  # noqa: E402

from ..util import dbg, failure_reason, run_async  # noqa: E402
from ..widgets import Page, button, combo, row, switch, subsection  # noqa: E402
from sekaishell.nm import conn_label  # noqa: E402

HELPER = "/usr/libexec/sekai/sekai-firewall"
ICON = ["security-high", "security-high-symbolic", "network-wired"]
PROFILES = [("public", "공용 네트워크"), ("home", "개인 네트워크")]

# 개인 네트워크에서 켜고 끌 수 있는 기능 — (이름, 설명, firewalld 서비스들, 보일 조건: 이 파일이 있으면)
FEATURES = [
    ("네트워크 검색", "다른 기기가 이 PC 를 찾고, 이 PC 가 프린터·기기를 찾는다 (mDNS · LLMNR)", ("mdns", "llmnr"), None),
    ("공유 폴더 찾기", "네트워크의 윈도우 공유 폴더를 탐색기에서 본다", ("samba-client",), None),
    ("이 PC 의 폴더 공유", "다른 컴퓨터가 이 PC 의 공유 폴더에 들어온다 (Samba)", ("samba",), "/usr/sbin/smbd"),
    ("휴대폰 연결", "KDE Connect", ("kdeconnect",), "/usr/bin/kdeconnectd"),
    ("Steam 리모트 플레이", "다른 기기에서 이 PC 의 게임을 한다", ("steam-streaming",), "/usr/games/steam"),
]


def _out(cmd, timeout=6):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8"))
        return r.returncode, r.stdout.strip()
    except Exception as e:
        dbg("[방화벽] 실행 실패", cmd, e)
        return 1, ""


def gather():
    """지금 상태 — 작업 스레드에서"""
    st = {"installed": os.path.exists("/usr/sbin/firewalld"), "running": False, "enabled": False,
          "zones": {}, "conns": [], "ssh": None, "ssh_public": False}
    if st["installed"]:
        st["enabled"] = _out(["systemctl", "is-enabled", "firewalld.service"])[1] == "enabled"
        st["running"] = _out(["systemctl", "is-active", "firewalld.service"])[1] == "active"
        if st["running"]:
            try:
                for z in ("public", "home"):
                    svcs = list(_fw_call("getServices", GLib.Variant("(s)", (z,))))
                    ports = [f"{p}/{proto}" for p, proto in _fw_call("getPorts", GLib.Variant("(s)", (z,)))]
                    st["zones"][z] = {"services": svcs, "ports": ports}
            except Exception as e:
                dbg("[방화벽] 상태를 읽지 못함", e)
                st["unreadable"] = str(e)
            st["ssh_public"] = "ssh" in st["zones"].get("public", {}).get("services", [])
            # 허용된 앱 — sekai-app-<id> 서비스 (이름·포트는 서비스 설정에서)
            apps = {}
            for z in ("home", "public"):
                for svc in st["zones"].get(z, {}).get("services", []):
                    if not svc.startswith("sekai-app-"):
                        continue
                    a = apps.setdefault(svc, {"id": svc[len("sekai-app-"):], "zones": [], "name": svc, "ports": []})
                    a["zones"].append(z)
            for svc, a in apps.items():
                try:
                    cfg = _fw_call("getServiceSettings2", GLib.Variant("(s)", (svc,)), iface="org.fedoraproject.FirewallD1")
                    a["name"] = cfg.get("short") or a["name"]
                    a["ports"] = [f"{p}/{proto}" for p, proto in cfg.get("ports", [])]
                except Exception as e:
                    dbg("[방화벽] 앱 서비스를 읽지 못함", svc, e)
            st["apps"] = sorted(apps.values(), key=lambda a: a["name"].lower())
    # 지금 연결된 네트워크와 그 프로필
    rc, out = _out(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"])
    for line in out.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 4 or parts[2] in ("loopback", "bridge") or parts[3] in ("lo", ""):
            continue
        name = parts[0].replace("\\:", ":")
        zone = _out(["nmcli", "-g", "connection.zone", "connection", "show", "uuid", parts[1]])[1].strip()
        st["conns"].append({"name": name, "uuid": parts[1], "type": parts[2], "dev": parts[3],
                            "zone": zone if zone in ("public", "home") else "public"})
    if os.path.exists("/usr/sbin/sshd"):
        st["ssh"] = any(_out(["systemctl", "is-enabled", u])[1] in ("enabled", "enabled-runtime")
                        for u in ("ssh.socket", "ssh.service"))
    return st


class FirewallPage:
    def __init__(self, store):
        self.store = store
        self.p = Page("방화벽", "들어오는 연결을 막아 이 PC 를 지킵니다. 나가는 연결(웹 · 앱 · 업데이트)은 막지 않습니다.")
        self.msg = Gtk.Label(xalign=0)
        self.msg.get_style_context().add_class("row-sub")
        self.msg.set_line_wrap(True)
        self.msg.set_no_show_all(True)
        self.p.add_widget(self.msg)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.p.add_widget(self.body)
        self.reload()

    @property
    def widget(self):
        return self.p

    # ── 그리기 ──
    def reload(self):
        def work():
            st = gather()
            GLib.idle_add(self._draw, st)
        threading.Thread(target=work, daemon=True).start()

    def _sect(self, title=None):
        return subsection(self.body, title)

    def _draw(self, st):
        for ch in self.body.get_children():
            self.body.remove(ch)
        s = self._sect()
        if not st["installed"]:
            row(s, "방화벽이 설치되어 있지 않습니다", "firewalld 패키지가 없습니다 — 업데이트를 받으면 함께 깔립니다", icon=ICON)
            self.body.show_all()
            return False
        on = st["running"]
        row(s, "방화벽", "켜짐 — 허용한 것 말고는 들어오는 연결을 모두 막습니다" if on else
            "꺼짐 — 이 PC 에서 열린 포트가 네트워크의 모든 기기에 보입니다", icon=ICON,
            control=switch(on, lambda v: self._do(["on" if v else "off"], "방화벽을 켰습니다" if v else "방화벽을 껐습니다")))

        s = self._sect("네트워크 프로필")
        if not st["conns"]:
            row(s, "연결된 네트워크가 없습니다", icon=["network-offline", "network-offline-symbolic"])
        for c in st["conns"]:
            kind = {"802-11-wireless": "와이파이", "802-3-ethernet": "유선", "vpn": "VPN", "wireguard": "VPN"}.get(c["type"], c["type"])
            sub = ("공용 — 다른 기기가 이 PC 를 볼 수 없습니다 (카페 · 공항처럼 믿을 수 없는 곳)" if c["zone"] == "public" else
                   "개인 — 프린터 · 기기 찾기와 아래에서 허용한 기능이 됩니다 (집 · 회사처럼 믿는 곳)")
            row(s, conn_label(c["name"]), f"{kind} · {sub}",            # "Wired connection 1" → "유선 연결 1" (네트워크 페이지와 같게)
                icon=["network-wireless", "network-wireless-symbolic"] if kind == "와이파이" else ["network-wired", "network-wired-symbolic"],
                control=combo(PROFILES, c["zone"],
                              lambda z, u=c["uuid"], cur=c["zone"]: z and z != cur and self._do(
                                  ["profile", u, z], f"{dict(PROFILES)[z]}로 바꿨습니다")))

        if on and st.get("unreadable"):
            s = self._sect()
            row(s, "방화벽 규칙을 읽지 못했습니다", st["unreadable"][:120], icon=["dialog-warning", "dialog-warning-symbolic"])
        elif on:
            home = st["zones"].get("home", {"services": [], "ports": []})
            s = self._sect("개인 네트워크에서 허용")
            for title, sub, svcs, need in FEATURES:
                if need and not os.path.exists(need):
                    continue
                cur = all(x in home["services"] for x in svcs)
                row(s, title, sub, control=switch(cur, lambda v, svcs=svcs, title=title: self._do_many(
                    [["service", x, "home", "on" if v else "off"] for x in svcs],
                    f"{title}: {'허용' if v else '막음'}")))
            if st["ssh"] is not None:
                ssub = "다른 컴퓨터에서 ssh 로 이 PC 에 로그인합니다 — 개인 네트워크에서만 열립니다"
                if st["ssh"] and st["ssh_public"]:
                    ssub = "다른 컴퓨터에서 ssh 로 이 PC 에 로그인합니다 — 지금은 공용 네트워크에도 열려 있습니다 (개발용 설치)"
                row(s, "원격 로그인 (SSH)", ssub,
                    control=switch(bool(st["ssh"]), lambda v: self._do(["remote-login", "on" if v else "off"],
                                                                        "원격 로그인을 켰습니다" if v else "원격 로그인을 껐습니다")))
            self._draw_apps(st)
            self._draw_ports(st)
        self.body.show_all()
        return False

    def _draw_apps(self, st):
        s = self._sect("허용된 앱")
        for a in st.get("apps", []):
            where = "모든 네트워크" if "public" in a["zones"] else "개인 네트워크"
            row(s, a["name"], f"{', '.join(a['ports']) or '포트 없음'} · {where}에서 허용",
                icon=["application-x-executable", "application-x-executable-symbolic"],
                control=button("제거", lambda a=a: self._do(["app", "remove", a["id"]], f"{a['name']} 의 허용을 지웠습니다")))
        if not st.get("apps"):
            row(s, "허용된 앱이 없습니다", "게임 서버 · 파일 전송 앱처럼 다른 기기가 이 PC 로 연결해야 하는 앱을 추가하세요")
        row(s, "앱 허용", "지금 연결을 기다리는 앱을 골라 그 포트를 엽니다", control=button("앱 추가…", self._add_app_dialog))

    def _add_app_dialog(self):
        """지금 연결을 기다리는(listen) 앱 — ss 로 내 프로세스의 것만 보인다 (관리자 프로세스는 이미 시스템 서비스)"""
        items = listening_apps()
        win = self.p.get_toplevel()
        d = Gtk.Dialog(title="앱 허용", transient_for=win if isinstance(win, Gtk.Window) else None, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "허용", Gtk.ResponseType.OK)
        ok = d.get_widget_for_response(Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        lab = Gtk.Label(label="다른 기기가 이 앱으로 연결할 수 있게 합니다. 지금 연결을 기다리고 있는 앱:", xalign=0)
        lab.set_line_wrap(True)
        box.add(lab)
        group, radios = None, []
        for it in items:
            r = Gtk.RadioButton.new_with_label_from_widget(group, f"{it['name']}  —  {', '.join(it['ports'])}")
            group = group or r
            r.app = it
            radios.append(r)
            box.add(r)
        if not items:
            box.add(Gtk.Label(label="지금 연결을 기다리는 앱이 없습니다 — 허용할 앱을 먼저 실행하세요.", xalign=0))
            ok.set_sensitive(False)
        where = combo([("home", "개인 네트워크만"), ("all", "모든 네트워크 (공용 포함)")], "home")
        acc = where.get_accessible()
        if acc is not None:
            acc.set_name("허용할 네트워크")
        box.add(where)
        d.show_all()

        def responded(dlg, resp):
            # 값은 창을 닫기 전에 읽는다 — 닫은 뒤의 콤보는 값이 없어(None) 도우미가 "잘못된 인자"로 거절했다
            chosen = next((r.app for r in radios if r.get_active()), None)
            scope = where.get_active_id() or "home"
            dlg.destroy()
            if resp == Gtk.ResponseType.OK and chosen:
                self._do(["app", "add", chosen["id"], chosen["name"], scope, *chosen["ports"]],
                         f"{chosen['name']} 을(를) 허용했습니다")
        d.connect("response", responded)

    def _draw_ports(self, st):
        s = self._sect("직접 연 포트")
        any_port = False
        for z, label in PROFILES:
            for prt in st["zones"].get(z, {}).get("ports", []):
                any_port = True
                row(s, prt, f"{label}에서 열림",
                    control=button("닫기", lambda prt=prt, z=z: self._do(["port", "remove", prt, z], f"{prt} 을(를) 닫았습니다")))
        if not any_port:
            row(s, "직접 연 포트가 없습니다", "게임 서버 · 개발 서버처럼 앱이 쓰는 포트를 열 때만 추가하세요")
        box = Gtk.Box(spacing=6)
        port = Gtk.Entry()
        port.set_width_chars(7)
        port.set_placeholder_text("포트")
        port.set_input_purpose(Gtk.InputPurpose.DIGITS)
        proto = combo([("tcp", "TCP"), ("udp", "UDP")], "tcp")
        where = combo([("home", "개인 네트워크만"), ("public", "모든 네트워크")], "home")

        def add():
            n = port.get_text().strip()
            if not n.isdigit() or not 1 <= int(n) <= 65535:
                self._say("포트 번호는 1~65535 의 숫자입니다")
                return
            p = f"{int(n)}/{proto.get_active_id()}"
            zones = ["home"] if where.get_active_id() == "home" else ["home", "public"]
            self._do_many([["port", "add", p, z] for z in zones], f"{p} 을(를) 열었습니다")
        # 한 줄에 칸이 셋 — 줄 제목("포트 열기") 하나로는 내레이터가 셋을 구별하지 못한다. 칸마다 이름을
        #   (콤보는 고른 항목이 이미 이름으로 붙어 있어 비었을 때만 채우는 accessible.name 으로는 안 바뀐다 — 직접)
        for w, nm in ((port, "포트 번호"), (proto, "프로토콜"), (where, "열 곳")):
            acc = w.get_accessible()
            if acc is not None:
                acc.set_name(nm)
        for w in (port, proto, where, button("추가", add)):
            box.pack_start(w, False, False, 0)
        row(s, "포트 열기", "들어오는 연결을 허용할 포트", control=box)

    # ── 바꾸기 ──
    def _say(self, text):
        self.msg.set_text(text or "")
        self.msg.set_visible(bool(text))

    def _do(self, args, ok_text):
        self._do_many([args], ok_text)

    def _do_many(self, cmds, ok_text):
        """도우미를 차례로 — 하나라도 실패하면 거기서 멈추고 이유를. 끝나면 다시 그린다 (스위치가 실제 상태를 따르게)"""
        if self.p.busy:
            return
        self.p.busy = True
        self._say("바꾸는 중…")
        cmds = list(cmds)

        def step(ok=True, out="", err=""):
            if not ok:
                self.p.busy = False
                self._say(failure_reason(out or err, "바꾸지 못했습니다"))
                self.reload()
                return
            if not cmds:
                self.p.busy = False
                self._say(ok_text)
                self.reload()
                return
            run_async(["pkexec", HELPER] + cmds.pop(0), lambda ok, o, e: step(ok, o, e))
        step()


def build(store):
    return FirewallPage(store).widget


PAGES = [{"id": "firewall", "title": "방화벽", "icon": ICON, "build": build}]
