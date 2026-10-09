"""네트워크 — NetworkManager(nmcli) 기반 (윈도우 11 의 "네트워크 및 인터넷").

이더넷 · Wi-Fi(켜고 끄기 · 사용 가능한 네트워크 · 숨겨진 네트워크 · 알려진 네트워크 관리) · VPN(WireGuard·
OpenVPN 구성 파일 가져오기 · 연결 · 끊기 · 삭제) · 연결마다 "속성" 화면 (자동 연결 · 데이터 통신 연결 ·
IP 할당 · DNS 서버 할당 · 네트워크 보안 키 보기 · 이 네트워크 저장 안 함 · 주소·MAC 같은 속성).
다른 데스크톱의 도구(nm-connection-editor · nm-applet)는 쓰지 않는다. 회사·학교(802.1X) Wi-Fi 도 이 모듈의
로그인 창(WifiDialog — 빠른 설정도 같이 쓴다)으로 연결하고, 비밀번호는 연결 프로필에 저장해(시스템 소유,
flags 0) 다음부터는 비밀 에이전트 없이 연결된다.
비밀번호는 명령줄에 넣지 않는다 (ps 로 누구나 볼 수 있다) — nmcli 의 passwd-file 을 메모리 파일로 넘긴다.
nmcli 는 모두 작업 스레드나 비동기로 — 창이 멈추지 않게. 시스템 연결을 바꿀 때 필요한 관리자 확인은
NetworkManager 가 polkit 으로 직접 묻는다 (세션의 인증 창). 오류를 가려 읽을 수 있게 nmcli 는 LC_ALL=C.UTF-8
로 부르고 (한국어 번역이 깔려 있으면 오류 문구가 바뀐다), 알려진 오류는 한국어로 바꿔 보인다.
"""
import ipaddress
import os
import re
import shutil
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..util import dbg
from sekaishell.nm import (DEV_TYPES,
                           KEY_MGMT,
                           _blank,
                           _dev_state,
                           _dot,
                           _freq_band,
                           _key_mgmt,
                           _list,
                           _multi,
                           _nm,
                           _nmrun,
                           _secrets_error,
                           _unescape,
                           _up_secret,
                           check_dns,
                           check_ipv4,
                           check_ipv6,
                           check_text,
                           conn_label,
                           dns_manual,
                           dns_summary,
                           import_vpn,
                           ip_summary,
                           is_enterprise,
                           nm_error,
                           openvpn_available,
                           parse_wifi,
                           read_props,
                           read_state,
                           sec_text,
                           wifi_up_saved)  # noqa: F401
from sekaishell.wifidialog import WifiDialog, _Form, _entry, _pw_entry, _show_rows  # noqa: F401
from ..widgets import Page, button, combo, icon_image, info, row, switch
from ..widgets import (notice as _notice, subsection as _sect, clear as _clear, reveal as _reveal,
                       busy_box as _busy_box, parent_window as _parent, confirm as _confirm, StatusLine)  # 공용 (카나데)

POLL_SECS = 5
WIFI_ICONS = ["network-wireless", "network-wireless-symbolic"]
WIRED_ICONS = ["network-wired", "network-wired-symbolic"]
VPN_ICONS = ["network-vpn", "network-vpn-symbolic", "network-workgroup"]


# ───────────────────────────────────────────────────────────────
# nmcli (작업 스레드에서)
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# 상태 읽기 (작업 스레드에서)
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# Wi-Fi 연결 (작업 스레드에서)
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# VPN (작업 스레드에서)
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# 입력 검사
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# 보여 주는 말
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# 위젯 도우미
# ───────────────────────────────────────────────────────────────


def _buttons(*btns):
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for x in btns:
        b.pack_start(x, False, False, 0)
    return b


# ───────────────────────────────────────────────────────────────
# Wi-Fi 연결 창 (숨겨진 네트워크 · 회사·학교 로그인 · 암호) — 설정 앱과 빠른 설정이 같이 쓴다
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# IP · DNS 편집 창
# ───────────────────────────────────────────────────────────────
class _IpDialog:
    """IP 설정 편집 (윈도우의 "IP 설정 편집") — IPv4 자동(DHCP)/수동/끔, IPv6 자동/수동/끔"""

    def __init__(self, page, props):
        s = props["s"]
        self.page, self.props = page, props
        f = self.f = _Form(_parent(page.p), "IP 설정 편집", None, "저장", self._save)
        a4 = (_list(s.get("ipv4.addresses")) or [""])[0]
        a6 = (_list(s.get("ipv6.addresses")) or [""])[0]
        self.m4 = combo([("auto", "자동(DHCP)"), ("manual", "수동"), ("disabled", "끄기")],
                        "disabled" if s.get("ipv4.method") in ("disabled",) else
                        "manual" if s.get("ipv4.method") == "manual" else "auto",
                        on_change=lambda _v: self._changed())
        f.add("IPv4", self.m4)
        self.a4 = _entry(a4.split("/")[0], placeholder="예: 192.168.0.10")
        p4 = a4.split("/")[1] if "/" in a4 else ""
        self.k4 = _entry(str(ipaddress.IPv4Network(f"0.0.0.0/{p4}").netmask) if p4.isdigit() else "",
                         placeholder="예: 255.255.255.0 또는 24")
        self.g4 = _entry(_blank(s.get("ipv4.gateway")), placeholder="예: 192.168.0.1 (적지 않아도 됩니다)")
        self.r4 = f.add("IP 주소", self.a4) + f.add("서브넷 마스크", self.k4) + f.add("게이트웨이", self.g4)
        self.m6 = combo([("auto", "자동"), ("manual", "수동"), ("disabled", "끄기")],
                        "disabled" if s.get("ipv6.method") in ("disabled", "ignore") else
                        "manual" if s.get("ipv6.method") == "manual" else "auto",
                        on_change=lambda _v: self._changed())
        f.add("IPv6", self.m6)
        self.a6 = _entry(a6.split("/")[0], placeholder="예: 2001:db8::10")
        self.p6 = _entry(a6.split("/")[1] if "/" in a6 else "64", width=6, placeholder="64")
        self.g6 = _entry(_blank(s.get("ipv6.gateway")), placeholder="적지 않아도 됩니다")
        self.r6 = f.add("IPv6 주소", self.a6) + f.add("서브넷 접두사 길이", self.p6) + f.add("게이트웨이", self.g6)
        self.hint = Gtk.Label(xalign=0)
        self.hint.get_style_context().add_class("row-sub")
        self.hint.set_line_wrap(True)
        self.hint.set_max_width_chars(56)
        f.add_wide(self.hint)
        f.show()
        self._changed()

    def _changed(self):
        _show_rows(self.r4, self.m4.get_active_id() == "manual")
        _show_rows(self.r6, self.m6.get_active_id() == "manual")
        manual = "manual" in (self.m4.get_active_id(), self.m6.get_active_id())
        hint = ""
        if manual and not dns_manual(self.props["s"]):
            hint = "수동으로 정하면 DNS 서버도 ‘DNS 서버 할당’에서 수동으로 정해야 인터넷 주소를 찾을 수 있습니다."
        if len(_list(self.props["s"].get("ipv4.addresses"))) > 1 and self.m4.get_active_id() == "manual":
            hint += (" " if hint else "") + "이 연결에는 IPv4 주소가 여러 개 있습니다 — 저장하면 위의 주소 하나만 남습니다."
        self.hint.set_text(hint)
        self.hint.set_visible(bool(hint))

    def _save(self, f):
        m4, m6 = self.m4.get_active_id(), self.m6.get_active_id()
        if m4 == "disabled" and m6 == "disabled":
            f.error("IPv4 와 IPv6 를 모두 끌 수는 없습니다")
            return
        args = []
        if m4 == "manual":
            err, addr, gw = check_ipv4(self.a4.get_text(), self.k4.get_text(), self.g4.get_text())
            if err:
                f.error(err)
                return
            args += ["ipv4.method", "manual", "ipv4.addresses", addr, "ipv4.gateway", gw]
        else:
            args += ["ipv4.method", m4, "ipv4.addresses", "", "ipv4.gateway", ""]
            if m4 == "disabled":                  # 꺼진 IPv4 에는 DNS 를 둘 수 없다
                args += ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"]
        if m6 == "manual":
            err, addr, gw = check_ipv6(self.a6.get_text(), self.p6.get_text(), self.g6.get_text())
            if err:
                f.error(err)
                return
            args += ["ipv6.method", "manual", "ipv6.addresses", addr, "ipv6.gateway", gw]
        else:
            args += ["ipv6.method", m6, "ipv6.addresses", "", "ipv6.gateway", ""]
            if m6 == "disabled":
                args += ["ipv6.dns", "", "ipv6.ignore-auto-dns", "no"]
        f.busy(True, "변경 사항을 적용하는 중…")
        self.page.apply(self.props, args, f.done)


class _DnsDialog:
    """DNS 서버 설정 편집 — 자동(DHCP)/수동 (기본 설정 DNS · 대체 DNS), 암호화된 DNS (DNS over TLS)"""

    def __init__(self, page, props):
        s = props["s"]
        self.page, self.props = page, props
        f = self.f = _Form(_parent(page.p), "DNS 서버 설정 편집", None, "저장", self._save)
        servers = _list(s.get("ipv4.dns")) + _list(s.get("ipv6.dns"))
        self.mode = combo([("auto", "자동(DHCP)"), ("manual", "수동")], "manual" if dns_manual(s) else "auto",
                          on_change=lambda _v: self._changed())
        f.add("DNS 서버 할당", self.mode)
        self.d1 = _entry(servers[0] if servers else "", placeholder="예: 1.1.1.1")
        self.d2 = _entry(servers[1] if len(servers) > 1 else "", placeholder="예: 8.8.8.8 (적지 않아도 됩니다)")
        self.rows = f.add("기본 설정 DNS", self.d1) + f.add("대체 DNS", self.d2)
        self.dot = None
        if "connection.dns-over-tls" in s:            # 이 NetworkManager 가 아는 설정일 때만
            self.dot = combo([("default", "시스템 기본값"), ("no", "끄기"), ("opportunistic", "가능하면 사용"),
                              ("yes", "켜기 (암호화하지 못하면 연결하지 않음)")], _dot(s.get("connection.dns-over-tls")))
            f.add("암호화된 DNS", self.dot, note="DNS over TLS — DNS 서버가 지원해야 합니다")
        f.show()
        self._changed()

    def _changed(self):
        _show_rows(self.rows, self.mode.get_active_id() == "manual")

    def _save(self, f):
        s = self.props["s"]
        args = []
        if self.mode.get_active_id() == "manual":
            e1, a1 = check_dns(self.d1.get_text(), "기본 설정 DNS")
            e2, a2 = check_dns(self.d2.get_text(), "대체 DNS")
            if e1 or e2:
                f.error(e1 or e2)
                return
            if not a1:
                f.error("기본 설정 DNS 서버를 입력하세요")
                return
            if a1 == a2:
                f.error("대체 DNS 는 기본 설정 DNS 와 달라야 합니다")
                return
            v4 = [a for a in (a1, a2) if a and ipaddress.ip_address(a).version == 4]
            v6 = [a for a in (a1, a2) if a and ipaddress.ip_address(a).version == 6]
            off4 = s.get("ipv4.method") in ("disabled",)
            off6 = s.get("ipv6.method") in ("disabled", "ignore")
            if v4 and off4:
                f.error("IPv4 가 꺼져 있어 IPv4 DNS 서버를 쓸 수 없습니다 — ‘IP 할당’에서 IPv4 를 켜세요")
                return
            if v6 and off6:
                f.error("IPv6 가 꺼져 있어 IPv6 DNS 서버를 쓸 수 없습니다 — ‘IP 할당’에서 IPv6 를 켜세요")
                return
            # 수동이면 DHCP 가 알려 주는 서버는 쓰지 않는다 (두 주소 체계 모두 — 한쪽만 막으면 그쪽 서버가 섞인다)
            if not off4:
                args += ["ipv4.ignore-auto-dns", "yes", "ipv4.dns", ",".join(v4)]
            if not off6:
                args += ["ipv6.ignore-auto-dns", "yes", "ipv6.dns", ",".join(v6)]
        else:
            args += ["ipv4.ignore-auto-dns", "no", "ipv4.dns", "", "ipv6.ignore-auto-dns", "no", "ipv6.dns", ""]
        if self.dot is not None and self.dot.get_active_id() != _dot(s.get("connection.dns-over-tls")):
            args += ["connection.dns-over-tls", self.dot.get_active_id()]
        f.busy(True, "변경 사항을 적용하는 중…")
        self.page.apply(self.props, args, f.done)


# ───────────────────────────────────────────────────────────────
# VPN 창
# ───────────────────────────────────────────────────────────────
class _VpnDialog:
    """VPN 연결 추가 — 구성 파일 가져오기 (WireGuard .conf · OpenVPN .ovpn)"""

    def __init__(self, page):
        self.page = page
        f = self.f = _Form(_parent(page.p), "VPN 연결 추가",
                           "VPN 제공 업체나 회사에서 받은 구성 파일을 가져옵니다.", "추가", self._save)
        self.kind = combo([("wireguard", "WireGuard"), ("openvpn", "OpenVPN")], "wireguard",
                          on_change=lambda _v: self._changed())
        f.add("VPN 종류", self.kind)
        self.file = Gtk.FileChooserButton(title="VPN 구성 파일 선택", action=Gtk.FileChooserAction.OPEN)
        self.file.connect("file-set", lambda *_: self._file_set())
        f.add("구성 파일", self.file)
        self.name_e = _entry(placeholder="예: 회사 VPN", max_len=64)
        f.add("연결 이름", self.name_e)
        self.hint = Gtk.Label(xalign=0)
        self.hint.get_style_context().add_class("row-sub")
        self.hint.set_line_wrap(True)
        self.hint.set_max_width_chars(56)
        f.add_wide(self.hint)
        f.show()
        self._changed()

    def _changed(self):
        kind = self.kind.get_active_id()
        flt = Gtk.FileFilter()
        if kind == "wireguard":
            flt.set_name("WireGuard 구성 파일 (*.conf)")
            flt.add_pattern("*.conf")
            hint = "WireGuard 구성 파일(.conf)에는 개인 키가 들어 있습니다 — 가져온 뒤에는 원본 파일을 안전하게 보관하세요."
            ok = True
        else:
            flt.set_name("OpenVPN 구성 파일 (*.ovpn, *.conf)")
            flt.add_pattern("*.ovpn")
            flt.add_pattern("*.conf")
            ok = openvpn_available()
            hint = ("구성 파일이 인증서 파일(.crt · .key)을 따로 가리키면 그 파일들도 같은 폴더에 두세요." if ok else
                    "OpenVPN 을 쓰려면 network-manager-openvpn 패키지가 필요합니다 — 터미널에서 "
                    "‘sudo apt install network-manager-openvpn’ 으로 설치한 뒤 다시 여세요.")
        self.file.set_filter(flt)
        self.hint.set_text(hint)
        self.f.ok.set_sensitive(ok)
        self.file.set_sensitive(ok)

    def _file_set(self):
        path = self.file.get_filename() or ""
        if path and not self.name_e.get_text().strip():
            self.name_e.set_text(os.path.splitext(os.path.basename(path))[0][:64])

    def _save(self, f):
        path = self.file.get_filename()
        kind = self.kind.get_active_id()
        if not path:
            f.error("구성 파일을 고르세요")
            return
        if kind == "openvpn" and not openvpn_available():
            return
        name = self.name_e.get_text().strip()
        err = check_text(name, "연결 이름", 64) if name else ""
        if err:
            f.error(err)
            return
        f.busy(True, "가져오는 중…")
        page = self.page

        def work():
            try:
                uuid, e = import_vpn(kind, path, name)
            except Exception as ex:
                dbg("VPN 가져오기 실패", repr(ex))
                e = "가져오는 중 오류가 났습니다"
            GLib.idle_add(finish, e)

        def finish(e):
            f.done(e)
            if e is None and not page.dead:
                page.say(f"‘{name or os.path.basename(path)}’ VPN 을 추가했습니다 — ‘연결’을 눌러 연결하세요")
                page.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()


class _VpnLogin:
    """VPN 이 사용자 이름·암호를 물을 때 (OpenVPN 암호 인증) — 받은 암호는 프로필에 저장한다 (flags 0)"""

    def __init__(self, page, c):
        self.page, self.c = page, c
        f = self.f = _Form(_parent(page.p), f"{c['name']} 로그인", f"‘{c['name']}’ VPN 의 계정을 입력하세요.",
                           "연결", self._go)
        self.user = _entry(max_len=128)
        f.add("사용자 이름", self.user)
        self.pw = _pw_entry()
        self.pw.set_activates_default(True)
        f.add("암호", self.pw)
        f.show()

    def _go(self, f):
        user, pw = self.user.get_text().strip(), self.pw.get_text()
        # vpn.data 는 "키=값, 키=값" 목록이라 쉼표·= 가 들어간 이름은 받을 수 없다
        if not re.fullmatch(r"[^\s,=\x00-\x1f\x7f]{1,128}", user):
            f.error("사용자 이름을 확인하세요 (공백·쉼표·= 는 쓸 수 없습니다)")
            return
        if not pw or "\n" in pw:
            f.error("암호를 입력하세요")
            return
        f.busy(True, "연결하는 중…")
        uuid, page = self.c["uuid"], self.page

        def work():
            ok, _o, err = _nmrun(["connection", "modify", uuid, "+vpn.data", f"username={user}",
                                  "+vpn.data", "password-flags=0"], 60)
            e = None if ok else nm_error(err)
            if e is None:
                raw = _up_secret(uuid, "vpn.secrets.password", pw, 60)
                e = None if raw is None else nm_error(raw, "연결하지 못했습니다",
                                                      secrets="사용자 이름 또는 암호가 맞지 않습니다")
            GLib.idle_add(finish, e)

        def finish(e):
            f.done(e)
            if not page.dead:
                page.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()


# ───────────────────────────────────────────────────────────────
# 페이지
# ───────────────────────────────────────────────────────────────
class NetworkPage(StatusLine):
    def __init__(self, store):
        self.p = Page("네트워크", "유선·무선 연결과 VPN 을 관리합니다.")
        self.dead = False
        self.st = None                 # read_state() 결과
        self.nets = None               # Wi-Fi 목록 (parse_wifi)
        self.wifi_sel = None           # 무선 어댑터가 여럿일 때 고른 것 (장치 이름)
        self.scan_again = False
        self.loading = self.again = self.scanning = False
        self.ops = {}                  # 키(UUID · "wifi:SSID" · "//radio") → 하고 있는 일 문구
        self.view = "main"             # main | known | props
        self.props = None              # 속성 화면의 연결 (read_props 결과)
        self.props_loading = None      # 읽고 있는 UUID
        self.key_text = None           # 보여 준 네트워크 보안 키
        self.drawn = {}
        self.poll_src = self.say_src = 0
        self.tick = 0

        self.msg = _notice()
        self.msg.set_no_show_all(True)
        self.msg.set_selectable(True)
        self.p.add_widget(self.msg)
        self.main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main.set_no_show_all(True)
        self.p.add_widget(self.main)
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.detail.set_no_show_all(True)
        self.p.add_widget(self.detail)
        s = _sect(self.main)
        row(s, "연결 상태를 읽는 중…", icon=WIRED_ICONS)
        _reveal(self.main, True)

        self.p.connect("map", lambda *_: self._poll_policy())
        self.p.connect("unmap", lambda *_: self._poll_policy())
        self.p.connect("destroy", self._destroy)
        self.refresh()
        self.scan_wifi(rescan=True)

    @property
    def widget(self):
        return self.p

    def _destroy(self, *_):
        self.dead = True
        for src in (self.poll_src, self.say_src):
            if src:
                GLib.source_remove(src)
        self.poll_src = self.say_src = 0

    # ── 새로 읽기 ──
    def _poll_policy(self):
        want = not self.dead and self.p.get_mapped()
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
        self.tick += 1
        self.refresh()
        if self.tick % 3 == 0:
            self.scan_wifi(rescan=False)
        return True

    def refresh(self):
        if self.dead:
            return
        if self.loading:
            self.again = True
            return
        self.loading = True

        def work():
            try:
                st = read_state()
            except Exception as e:
                dbg("네트워크 상태를 읽지 못함", repr(e))
                st = None
            GLib.idle_add(self._got, st)
        threading.Thread(target=work, daemon=True).start()

    def _got(self, st):
        self.loading = False
        if self.dead:
            return False
        if st is not None:
            self.st = st
        self._draw()
        if self.again:
            self.again = False
            self.refresh()
        return False

    def scan_wifi(self, rescan=False):
        if self.dead:
            return
        if self.scanning:
            self.scan_again = True            # 찾는 중에 어댑터를 바꿨다 — 끝나면 바로 다시
            return
        self.scanning = True
        # 무선 어댑터가 여럿이면 고른 어댑터가 찾은 것만 (다른 어댑터의 연결이 "연결됨" 으로 섞이지 않게)
        wifi = [d for d in (self.st or {}).get("devs", []) if d["type"] == "wifi"]
        dev = self._pick_wifi(wifi)["dev"] if len(wifi) > 1 else None

        def work():
            out = _nm("-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                      *(("ifname", dev) if dev else ()),
                      "--rescan", "yes" if rescan else "no", timeout=25 if rescan else 8)
            GLib.idle_add(self._got_wifi, parse_wifi(out), dev)
        threading.Thread(target=work, daemon=True).start()

    def _got_wifi(self, nets, dev=None):
        self.scanning = False
        if self.dead:
            return False
        wifi = [d for d in (self.st or {}).get("devs", []) if d["type"] == "wifi"]
        again, self.scan_again = self.scan_again, False
        if dev != (self._pick_wifi(wifi)["dev"] if len(wifi) > 1 else None):
            # 찾는 사이 다른 어댑터를 골랐거나, 어댑터가 여럿인 것을 찾은 뒤에 알았다 (처음 찾기는 상태를 읽기 전)
            self.scan_wifi()
            return False
        if again:
            self.scan_wifi()
        self.nets = nets
        self._draw()
        return False

    # ── 알림 한 줄 ──
    def _begin(self, key, text):
        self.ops[key] = text
        self.p.busy = True
        self._draw()

    def _end(self, key):
        self.ops.pop(key, None)
        self.p.busy = bool(self.ops)
        if self.dead:
            return False
        self.refresh()
        return True

    def _run(self, key, text, args, ok_msg=None, fail_msg="실패했습니다", timeout=60, then=None):
        """nmcli 한 번 (작업 스레드) — 도는 동안 key 의 줄에 text. then(성공?, 오류 원문)"""
        if key in self.ops:
            return
        self._begin(key, text)

        def work():
            ok, _o, err = _nmrun(args, timeout)
            GLib.idle_add(done, ok, err)

        def done(ok, err):
            if not self._end(key):
                return False
            if then is not None:
                then(ok, err)
            elif ok:
                if ok_msg:
                    self.say(ok_msg)
            else:
                self.say(f"{fail_msg}: {nm_error(err)}", error=True)
            return False
        threading.Thread(target=work, daemon=True).start()

    # ── 그리기 ──
    def _conns(self, *types):
        return [c for c in (self.st or {}).get("conns", ()) if c["type"] in types]

    def _draw(self):
        if self.dead:
            return
        _reveal(self.main, self.view == "main")
        _reveal(self.detail, self.view != "main")
        if self.view == "main":
            self._draw_main()
        elif self.view == "known":
            self._draw_known()
        else:
            self._draw_props()

    def _draw_main(self):
        st = self.st
        if st is None:
            return
        key = (repr(st), repr(self.nets), tuple(sorted(self.ops.items())))
        if self.drawn.get("main") == key:
            return
        self.drawn["main"] = key
        _clear(self.main)
        if not st["running"]:
            self.main.pack_start(_notice("네트워크 서비스(NetworkManager)가 실행되고 있지 않습니다. "
                                         "컴퓨터를 다시 시작해 보세요."), False, False, 0)
            self._show_children(self.main)
            return
        devs = st["devs"]
        eth = [d for d in devs if d["type"] == "ethernet"]
        wifi = [d for d in devs if d["type"] == "wifi"]
        other = [d for d in devs if d["type"] not in ("ethernet", "wifi", "wireguard", "tun", "loopback",
                                                      "wifi-p2p", "dummy")]
        if not devs:
            row(_sect(self.main, "연결 상태"), "네트워크 장치가 없습니다", "네트워크 어댑터를 찾지 못했습니다",
                icon=WIRED_ICONS)

        # ── 이더넷 ──
        if eth:
            s = _sect(self.main, "이더넷")
            for d in eth:
                uuid = d["uuid"] or self._eth_profile(d["dev"])
                sub = _dev_state(d["state"], "ethernet")
                if d["conn"]:
                    sub += f" · {conn_label(d['conn'])}"
                if d["ip"]:
                    sub += f" · IPv4 {d['ip'].split('/')[0]}"
                b = button("속성", lambda u=uuid, dv=d["dev"]: self.open_props(u, dv))
                b.set_sensitive(bool(uuid))
                row(s, "이더넷" if len(eth) == 1 else f"이더넷 ({d['dev']})", sub, icon=WIRED_ICONS, control=b)

        # ── Wi-Fi ──
        if wifi:
            self._draw_wifi(self._pick_wifi(wifi), st["radio"], wifi)

        # ── VPN ──
        s = _sect(self.main, "VPN")
        for c in sorted(self._conns("vpn", "wireguard"), key=lambda c: c["name"].casefold()):
            op = self.ops.get(c["uuid"])
            kind = "WireGuard" if c["type"] == "wireguard" else "VPN"
            if op:
                sub, ctl = f"{kind} · {op}", _busy_box(op)
            else:
                state = "연결됨" if c["state"] == "activated" else "연결 중…" if c["state"] == "activating" \
                    else "연결 안 됨"
                sub = f"{kind} · {state}"
                main_b = button("연결 끊기" if c["active"] else "연결",
                                lambda c=c: self.vpn_down(c) if c["active"] else self.vpn_up(c))
                del_b = button("삭제", lambda c=c: self.delete_conn(c, vpn=True))
                ctl = _buttons(main_b, del_b)
            r = row(s, c["name"], sub, icon=VPN_ICONS, control=ctl)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        row(s, "VPN 연결 추가", "WireGuard · OpenVPN 구성 파일을 가져옵니다",
            icon=["list-add", "list-add-symbolic"], control=button("추가…", lambda: _VpnDialog(self)))

        # ── 다른 장치 ──
        if other:
            s = _sect(self.main, "다른 네트워크 장치")
            for d in other:
                sub = f"{DEV_TYPES.get(d['type'], d['type'])} · {_dev_state(d['state'])}"
                if d["conn"]:
                    sub += f" · {conn_label(d['conn'])}"
                row(s, d["dev"], sub, icon=["network-workgroup", "network-wired"],
                    control=info(d["ip"].split("/")[0]) if d["ip"] else None)

        s = _sect(self.main, "관련 설정")
        row(s, "새로 고침", "장치와 Wi-Fi 목록을 다시 읽습니다", icon=["view-refresh", "view-refresh-symbolic"],
            control=button("새로 고침", lambda: (self.refresh(), self.scan_wifi(rescan=True))))
        self._show_children(self.main)

    @staticmethod
    def _show_children(box):
        # main · detail 은 no_show_all — show_all 이 안으로 내려가지 않으니 안쪽을 하나씩
        for c in box.get_children():
            c.show_all()

    def _eth_profile(self, dev):
        """연결되지 않은 이더넷 장치의 저장된 프로필 (속성을 미리 바꿀 수 있게)"""
        for c in self._conns("802-3-ethernet"):
            if c["dev"] in ("", dev):
                return c["uuid"]
        return ""

    def _pick_wifi(self, wifi):
        """무선 어댑터가 여럿이면 (내장 + USB 동글 등) 하나를 골라 보인다 — 고른 것, 없으면 연결된 것,
        그다음 쓸 수 있는 것. 목록·연결·끊기는 그 어댑터로"""
        names = [d["dev"] for d in wifi]
        if self.wifi_sel not in names:
            pick = (next((d for d in wifi if d["state"].startswith("connected")), None)
                    or next((d for d in wifi if d["state"] not in ("unavailable", "unmanaged")), None) or wifi[0])
            self.wifi_sel = pick["dev"]
        return wifi[names.index(self.wifi_sel)]

    def _choose_wifi(self, dev):
        if not dev or dev == self.wifi_sel:
            return
        self.wifi_sel = dev
        self.nets = None

        def later():
            # 콤보의 changed 안에서 다시 그리면 그 콤보를 지우게 된다 — 신호가 끝난 뒤에
            self.drawn.pop("main", None)
            self._draw()
            self.scan_wifi()                  # 어댑터가 받아 둔 목록 (새로 찾기는 몇 초 — 주기 검사가 한다)
            return False
        GLib.idle_add(later)

    def _draw_wifi(self, wd, radio, wifi=()):
        s = _sect(self.main, "Wi-Fi")
        if len(wifi) > 1:
            # 윈도우는 어댑터마다 "Wi-Fi", "Wi-Fi 2" 를 따로 보인다 — 여기선 하나를 골라 그 목록을
            items = [(d["dev"], f"{d['dev']} — {_dev_state(d['state'], 'wifi')}"
                      + (f" ({d['conn']})" if d["conn"] and d["state"].startswith("connected") else ""))
                     for d in wifi]
            row(s, "무선 어댑터", f"이 컴퓨터에 무선 어댑터가 {len(wifi)}개 있습니다", icon=WIFI_ICONS,
                control=combo(items, wd["dev"], self._choose_wifi))
        cur = wd["conn"] if wd["state"].startswith("connected") else ""
        sub = "꺼짐" if not radio else (f"연결됨 — {cur}" + (f" · IPv4 {wd['ip'].split('/')[0]}" if wd["ip"] else "")
                                       if cur else _dev_state(wd["state"], "wifi"))
        if "//radio" in self.ops:
            ctl = _busy_box(self.ops["//radio"])
        else:
            ctl = switch(radio, self.set_radio)
        row(s, "Wi-Fi", sub, icon=WIFI_ICONS, control=ctl)
        if not radio:
            return
        dev = wd["dev"]
        if self.nets is None:
            row(s, "무선 네트워크를 찾는 중…", icon=WIFI_ICONS, control=_busy_box(""))
        elif not self.nets:
            row(s, "찾은 네트워크가 없습니다", "잠시 후 ‘새로 고침’을 눌러 보세요", icon=WIFI_ICONS)
        for n in (self.nets or [])[:20]:
            key = "wifi:" + n["ssid"]
            ico = "network-wireless-signal-" + ("excellent" if n["signal"] > 75 else "good" if n["signal"] > 50
                                                else "ok" if n["signal"] > 25 else "weak")
            sub = f"{sec_text(n['sec'])} · 신호 {n['signal']}%"
            if n["active"]:
                sub = "연결됨 · " + sub
            op = self.ops.get(key)
            if op:
                ctl = _busy_box(op)
            elif n["active"]:
                uuid = wd["uuid"]
                ctl = _buttons(button("속성", lambda u=uuid: self.open_props(u, dev)),
                               button("연결 끊기", lambda n=n: self.wifi_disconnect(n, dev)))
            else:
                ctl = button("연결", lambda n=n: self.wifi_connect(n, dev))
            r = row(s, n["ssid"], sub, icon=[ico, "network-wireless"], control=ctl)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        row(s, "숨겨진 네트워크 연결", "이름(SSID)을 알리지 않는 네트워크에 연결합니다",
            icon=["list-add", "list-add-symbolic"],
            control=button("연결…", lambda: WifiDialog(_parent(self.p), None, dev, on_done=self._after_wifi)))
        n = len(self._conns("802-11-wireless"))
        b = button("관리", lambda: self.show("known"))
        b.set_sensitive(bool(n))
        row(s, "알려진 네트워크 관리", f"저장된 Wi-Fi 네트워크 {n}개" if n else "저장된 Wi-Fi 네트워크가 없습니다",
            icon=["document-properties", "preferences-system"], control=b)

    def _head(self, title, sub, icons):
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.get_style_context().add_class("net-head")
        back = Gtk.Button()
        back.add(icon_image(["go-previous-symbolic", "go-previous"], 16))
        back.set_tooltip_text("네트워크")
        back.get_style_context().add_class("net-back")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", lambda *_: self.show("main"))
        head.pack_start(back, False, False, 0)
        head.pack_start(icon_image(icons, 40), False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        v.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("net-name")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(t, False, False, 0)
        if sub:
            sl = Gtk.Label(label=sub, xalign=0)
            sl.get_style_context().add_class("row-sub")
            v.pack_start(sl, False, False, 0)
        head.pack_start(v, True, True, 0)
        self.detail.pack_start(head, False, False, 0)

    def _draw_known(self):
        conns = sorted(self._conns("802-11-wireless"), key=lambda c: c["name"].casefold())
        key = ("known", repr(conns), tuple(sorted(self.ops.items())))
        if self.drawn.get("detail") == key:
            return
        self.drawn["detail"] = key
        _clear(self.detail)
        self._head("알려진 네트워크 관리", "이 PC 에 저장된 Wi-Fi 네트워크", WIFI_ICONS)
        s = _sect(self.detail)
        if not conns:
            row(s, "저장된 Wi-Fi 네트워크가 없습니다", icon=WIFI_ICONS)
        for c in conns:
            op = self.ops.get(c["uuid"])
            ctl = _busy_box(op) if op else _buttons(
                button("속성", lambda c=c: self.open_props(c["uuid"], c["dev"])),
                button("저장 안 함", lambda c=c: self.delete_conn(c)))
            r = row(s, c["name"], "연결됨" if c["active"] else None, icon=WIFI_ICONS, control=ctl)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._show_children(self.detail)

    def _draw_props(self):
        pr = self.props
        if pr is None:
            key = ("props-loading", self.props_loading)
            if self.drawn.get("detail") != key:
                self.drawn["detail"] = key
                _clear(self.detail)
                self._head("속성", None, WIRED_ICONS)
                s = _sect(self.detail)
                r = Gtk.ListBoxRow()
                r.get_style_context().add_class("row")
                r.add(_busy_box("연결 정보를 읽는 중…"))
                s.add(r)
                self._show_children(self.detail)
            return
        s, d, ap = pr["s"], pr["d"], pr["ap"]
        uuid = pr["uuid"]
        key = ("props", repr(pr), self.ops.get(uuid), self.key_text)
        if self.drawn.get("detail") == key:
            return
        self.drawn["detail"] = key
        _clear(self.detail)
        wifi = s.get("connection.type") == "802-11-wireless"
        active = bool(s.get("GENERAL.STATE"))
        name = s.get("802-11-wireless.ssid") if wifi else s.get("connection.id", "")
        state = "연결됨" if s.get("GENERAL.STATE") == "activated" else "연결 중…" if active else "연결 안 됨"
        if wifi:
            self._head(name or s.get("connection.id", ""), f"Wi-Fi · {state}", WIFI_ICONS)
        else:                                     # 윈도우처럼 어댑터 이름을 제목으로, 프로필 이름은 아래에
            self._head("이더넷", f"{conn_label(name)} · {state}", WIRED_ICONS)
        op = self.ops.get(uuid)
        if op:
            n = _notice(op)
            self.detail.pack_start(n, False, False, 0)

        sec = _sect(self.detail)
        auto = switch(s.get("connection.autoconnect") == "yes",
                      lambda v: self.set_prop(pr, ["connection.autoconnect", "yes" if v else "no"], reapply=False))
        auto.set_sensitive(not op)
        row(sec, "범위 내에 있으면 자동으로 연결" if wifi else "자동으로 연결",
            None if wifi else "케이블을 꽂으면 이 설정으로 연결합니다", control=auto)
        metered = switch(s.get("connection.metered") == "yes",
                         lambda v: self.set_prop(pr, ["connection.metered", "yes" if v else "no"]))
        metered.set_sensitive(not op)
        msub = "이 네트워크에 연결되어 있으면 일부 앱이 데이터 사용량을 줄이도록 동작을 바꿀 수 있습니다"
        if (d.get("GENERAL.METERED") or "").startswith("yes") and s.get("connection.metered") != "yes":
            msub += " (지금은 자동으로 데이터 통신 연결로 감지되었습니다)"
        row(sec, "데이터 통신 연결", msub, control=metered)

        sec = _sect(self.detail, "IP 설정")
        b = button("편집", lambda: _IpDialog(self, pr))
        b.set_sensitive(not op)
        row(sec, "IP 할당", ip_summary(s), control=b)
        b = button("편집", lambda: _DnsDialog(self, pr))
        b.set_sensitive(not op)
        row(sec, "DNS 서버 할당", dns_summary(s), control=b)

        if wifi:
            km = s.get("802-11-wireless-security.key-mgmt", "")
            sec = _sect(self.detail, "보안")
            row(sec, "보안 종류", None, control=info(KEY_MGMT.get(km, "없음 (개방)" if not _blank(km) else km)))
            if km in ("wpa-psk", "sae", "none"):
                if self.key_text is None:
                    ctl = button("보기", lambda: self.show_key(pr))
                    ctl.set_sensitive(not op)
                    val = "●●●●●●●●"
                else:
                    ctl = button("숨기기", self.hide_key)
                    val = self.key_text
                v = info(val)
                row(sec, "네트워크 보안 키", None, control=_buttons(v, ctl))
            elif km == "wpa-eap":
                ident = s.get("802-1x.identity", "")
                b = button("변경…", lambda: self.enterprise_edit(pr))
                b.set_sensitive(not op)
                row(sec, "로그인 정보", f"사용자 이름: {ident}" if ident else None, control=b)
            b = button("저장 안 함", lambda: self.delete_conn({"uuid": uuid, "name": name or s.get("connection.id"),
                                                               "type": "802-11-wireless"}))
            b.set_sensitive(not op)
            row(sec, "이 네트워크 저장 안 함", "저장된 암호와 설정을 지웁니다", control=b)

        # ── 속성 (읽기 전용 — 윈도우의 "속성") ──
        items = self._facts(s, d, ap, wifi)
        if items:
            sec = _sect(self.detail, "속성")
            for t, v in items:
                ctl = info(v)
                ctl.set_line_wrap(True)
                ctl.set_max_width_chars(40)
                ctl.set_xalign(1)
                row(sec, t, None, control=ctl)
            text = "\n".join(f"{t}: {v}" for t, v in items)
            row(sec, "속성 복사", "위의 정보를 클립보드에 복사합니다", control=button("복사", lambda: self._copy(text)))
        elif not active:
            sec = _sect(self.detail, "속성")
            row(sec, "연결되어 있지 않습니다", "연결하면 주소와 장치 정보가 여기에 보입니다")
        self._show_children(self.detail)

    @staticmethod
    def _facts(s, d, ap, wifi):
        out = []
        if wifi and ap:
            out.append(("SSID", ap.get("ssid", "")))
            out.append(("보안 종류", sec_text(ap.get("sec", ""))))
            band = _freq_band(ap.get("freq"))
            if band:
                out.append(("네트워크 대역", band))
            if ap.get("chan"):
                out.append(("네트워크 채널", ap["chan"]))
            if ap.get("rate"):
                out.append(("링크 속도", ap["rate"].replace("Mbit/s", "Mbps")))
        speed = d.get("CAPABILITIES.SPEED", "")
        if not wifi and speed and speed != "unknown":
            out.append(("링크 속도", speed.replace("Mb/s", "Mbps")))
        v6 = _multi(d, "IP6.ADDRESS")
        glob6 = [a for a in v6 if not a.lower().startswith("fe80")]
        if glob6:
            out.append(("IPv6 주소", ", ".join(glob6)))
        link6 = [a for a in v6 if a.lower().startswith("fe80")]
        if link6:
            out.append(("링크-로컬 IPv6 주소", ", ".join(link6)))
        if _multi(d, "IP6.DNS"):
            out.append(("IPv6 DNS 서버", ", ".join(_multi(d, "IP6.DNS"))))
        if _multi(d, "IP4.ADDRESS"):
            out.append(("IPv4 주소", ", ".join(_multi(d, "IP4.ADDRESS"))))
        if _blank(d.get("IP4.GATEWAY")):
            out.append(("IPv4 게이트웨이", d["IP4.GATEWAY"]))
        if _multi(d, "IP4.DNS"):
            out.append(("IPv4 DNS 서버", ", ".join(_multi(d, "IP4.DNS"))))
        if _blank(d.get("GENERAL.VENDOR")):
            out.append(("제조업체", d["GENERAL.VENDOR"]))
        if _blank(d.get("GENERAL.PRODUCT")):
            out.append(("설명", d["GENERAL.PRODUCT"]))
        drv = _blank(d.get("GENERAL.DRIVER"))
        if drv:
            ver = _blank(d.get("GENERAL.DRIVER-VERSION"))
            out.append(("드라이버", f"{drv} {ver}".strip()))
        if _blank(d.get("GENERAL.HWADDR")):
            out.append(("물리적 주소(MAC)", d["GENERAL.HWADDR"]))
        return out

    def _copy(self, text):
        from gi.repository import Gdk
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.say("속성을 클립보드에 복사했습니다")

    # ── 화면 바꾸기 ──
    def show(self, view):
        self.view = view
        if view != "props":
            self.props = None
            self.key_text = None
        self.drawn.pop("detail", None)
        self.drawn.pop("main", None)
        self._draw()
        GLib.idle_add(lambda: (self.p.get_vadjustment().set_value(0), False)[1])

    def open_props(self, uuid, dev=None):
        if not uuid:
            return
        self.props, self.key_text, self.props_loading = None, None, uuid
        self.back_to = self.view if self.view != "props" else "main"
        self.show("props")
        self._load_props(uuid, dev)

    def _load_props(self, uuid, dev=None):
        def work():
            try:
                pr = read_props(uuid, dev)
            except Exception as e:
                dbg("연결 속성을 읽지 못함", repr(e))
                pr = None
            GLib.idle_add(got, pr)

        def got(pr):
            if self.dead or self.view != "props" or self.props_loading != uuid:
                return False
            if pr is None:
                self.say("연결 정보를 읽지 못했습니다 — 연결이 지워졌을 수 있습니다", error=True)
                self.show("main")
                return False
            self.props = pr
            self._draw()
            return False
        threading.Thread(target=work, daemon=True).start()

    # ── 연결 속성 바꾸기 ──
    def apply(self, pr, args, done=None, reapply=True):
        """nmcli connection modify → (연결되어 있으면) device reapply, 안 되면 connection up.
        done(오류|None) — 창이 결과를 보여 준다. 없으면 페이지 알림으로"""
        uuid, dev = pr["uuid"], pr.get("dev")
        active = bool(pr["s"].get("GENERAL.STATE"))
        if uuid in self.ops:
            return
        self._begin(uuid, "변경 사항을 적용하는 중…")

        def work():
            ok, _o, err = _nmrun(["connection", "modify", uuid] + args, 90)
            e = None if ok else nm_error(err, "바꾸지 못했습니다")
            if e is None and active and reapply and dev:
                ok, _o, err = _nmrun(["device", "reapply", dev], 30)
                if not ok:
                    # 다시 적용할 수 없는 변경(연결을 새로 해야 하는 것) — 연결을 다시 시작한다
                    ok, _o, err = _nmrun(["-w", "45", "connection", "up", uuid], 60)
                    if not ok:
                        e = "저장했지만 다시 연결하지 못했습니다: " + nm_error(err)
            GLib.idle_add(finish, e)

        def finish(e):
            live = self._end(uuid)
            if done is not None:
                done(e)
            elif e and live:
                self.say(e, error=True)
            if live and self.view == "props" and self.props_loading == uuid:
                self._load_props(uuid, dev)
            return False
        threading.Thread(target=work, daemon=True).start()

    def set_prop(self, pr, args, reapply=True):
        self.apply(pr, args, None, reapply=reapply)

    def show_key(self, pr):
        """저장된 네트워크 보안 키 — NetworkManager 가 권한을 확인한다 (관리자가 아니면 인증 창)"""
        uuid = pr["uuid"]
        km = pr["s"].get("802-11-wireless-security.key-mgmt")
        field = "802-11-wireless-security.wep-key0" if km == "none" else "802-11-wireless-security.psk"
        self._begin(uuid, "보안 키를 읽는 중…")

        def work():
            ok, out, err = _nmrun(["-s", "-g", field, "connection", "show", uuid], 60)
            GLib.idle_add(done, ok, _unescape(out.rstrip("\n")), err)

        def done(ok, key, err):
            if not self._end(uuid):
                return False
            if not ok:
                self.say(f"보안 키를 볼 수 없습니다: {nm_error(err)}", error=True)
            elif not key:
                self.say("저장된 보안 키가 없습니다 — 연결할 때마다 묻는 네트워크입니다", error=True)
            elif self.props is not None and self.props["uuid"] == uuid:
                self.key_text = key
                self._draw()
            return False
        threading.Thread(target=work, daemon=True).start()

    def hide_key(self):
        self.key_text = None
        self._draw()

    def enterprise_edit(self, pr):
        s = pr["s"]
        pre = {"identity": s.get("802-1x.identity", ""), "anon": _blank(s.get("802-1x.anonymous-identity")),
               "eap": (_list(s.get("802-1x.eap")) or ["peap"])[0], "phase2": _blank(s.get("802-1x.phase2-auth")),
               "ca": "system" if _blank(s.get("802-1x.ca-path")) or s.get("802-1x.system-ca-certs") == "yes"
               else "none", "domain": _blank(s.get("802-1x.domain-suffix-match"))}
        WifiDialog(_parent(self.p), s.get("802-11-wireless.ssid", ""), pr.get("dev") or None, security="eap",
                   on_done=lambda: self._load_props(pr["uuid"], pr.get("dev")) if not self.dead else None,
                   prefill=pre)

    def delete_conn(self, c, vpn=False):
        name = c["name"]
        if vpn:
            text, sub, ok = (f"‘{name}’ VPN 을 삭제할까요?", "이 VPN 연결과 저장된 설정이 지워집니다.", "삭제")
        else:
            text, sub, ok = (f"‘{name}’ 을(를) 저장 안 할까요?",
                             "저장된 암호와 설정이 지워집니다. 다시 연결하려면 암호를 다시 입력해야 합니다.", "저장 안 함")

        def go():
            def then(okk, err):
                if okk:
                    self.say(f"‘{name}’ 을(를) 지웠습니다")
                    if self.view == "props" and self.props_loading == c["uuid"]:
                        self.show(getattr(self, "back_to", "main"))
                else:
                    self.say(f"지우지 못했습니다: {nm_error(err)}", error=True)
            self._run(c["uuid"], "지우는 중…", ["connection", "delete", c["uuid"]], then=then)
        _confirm(_parent(self.p), text, sub, ok, go)

    # ── Wi-Fi ──
    def set_radio(self, on):
        def then(ok, err):
            if not ok:
                self.say(f"Wi-Fi 를 {'켜지' if on else '끄지'} 못했습니다: {nm_error(err)}", error=True)
            self.nets = None if on else []
            if on:
                GLib.timeout_add(1500, lambda: (self.scan_wifi(rescan=True), False)[1])
        self._run("//radio", "켜는 중…" if on else "끄는 중…", ["radio", "wifi", "on" if on else "off"], then=then)

    def _after_wifi(self):
        if not self.dead:
            self.refresh()
            self.scan_wifi(rescan=False)

    def wifi_disconnect(self, n, dev):
        self._run("wifi:" + n["ssid"], "연결을 끊는 중…", ["device", "disconnect", dev],
                  fail_msg="연결을 끊지 못했습니다", then=lambda ok, err: (
                      self._after_wifi() if ok else self.say(f"연결을 끊지 못했습니다: {nm_error(err)}", error=True)))

    def wifi_connect(self, n, dev):
        """저장된 프로필이 있으면 그것으로, 없으면(또는 암호가 틀리면) 암호·로그인 창.
        개방 네트워크는 바로 연결한다"""
        key = "wifi:" + n["ssid"]
        if key in self.ops:
            return
        self._begin(key, "연결하는 중…")
        ssid, sec = n["ssid"], n["sec"]

        def work():
            try:
                if _key_mgmt(sec) is None:            # 개방 · OWE(보안 개방) — 암호가 없다
                    st, e = wifi_up_saved(ssid)
                    if st == "none":
                        ok, _o, err = _nmrun(["-w", "45", "device", "wifi", "connect", ssid, "ifname", dev], 60)
                        st, e = ("ok", "") if ok else ("error", nm_error(err, "연결하지 못했습니다"))
                else:
                    st, e = wifi_up_saved(ssid)
            except Exception as ex:
                dbg("Wi-Fi 연결 실패", repr(ex))
                st, e = "error", "연결하는 중 오류가 났습니다"
            GLib.idle_add(done, st, e)

        def done(st, e):
            if not self._end(key):
                return False
            if st == "ok":
                self._after_wifi()
            elif st in ("none", "secrets"):
                kind = "eap" if is_enterprise(sec) else ("wep" if _key_mgmt(sec) == "none" else _key_mgmt(sec))
                WifiDialog(_parent(self.p), ssid, dev, security=kind, on_done=self._after_wifi)
            else:
                self.say(f"‘{ssid}’ 에 연결하지 못했습니다: {e}", error=True)
            return False
        threading.Thread(target=work, daemon=True).start()

    # ── VPN ──
    def vpn_up(self, c):
        uuid = c["uuid"]
        if uuid in self.ops:
            return
        self._begin(uuid, "연결하는 중…")

        def work():
            raw = _up_secret(uuid, None, None, 60)
            GLib.idle_add(done, raw)

        def done(raw):
            if not self._end(uuid):
                return False
            if raw is None:
                self.say(f"‘{c['name']}’ 에 연결했습니다")
            elif _secrets_error(raw) and c["type"] == "vpn":
                _VpnLogin(self, c)
            else:
                self.say(f"‘{c['name']}’ 에 연결하지 못했습니다: {nm_error(raw)}", error=True)
            return False
        threading.Thread(target=work, daemon=True).start()

    def vpn_down(self, c):
        self._run(c["uuid"], "연결을 끊는 중…", ["connection", "down", c["uuid"]], fail_msg="연결을 끊지 못했습니다")


def build(store):
    if not shutil.which("nmcli"):
        p = Page("네트워크", "유선·무선 연결과 VPN 을 관리합니다.")
        p.add_widget(_notice("네트워크 관리 도구(NetworkManager)가 설치되어 있지 않습니다."))
        return p
    return NetworkPage(store).widget


PAGES = [{"id": "network", "title": "네트워크",
          "icon": ["network-wired", "network-workgroup", "preferences-system-network",
                    "network-wired-symbolic"],
          "build": build}]
