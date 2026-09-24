"""네트워크 — NetworkManager(nmcli) 기반."""
import shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import run, spawn
from ..widgets import Page, button, info, row, switch


def _nm(*args, timeout=8):
    return run(["nmcli", "-t", "-c", "no", *args], timeout=timeout)


def _devices():
    out = []
    for line in _nm("-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines():
        f = line.split(":")
        if len(f) >= 4 and f[1] not in ("loopback",):
            out.append({"dev": f[0], "type": f[1], "state": f[2], "conn": f[3]})
    return out


def _ip4(dev):
    for line in _nm("-f", "IP4.ADDRESS", "device", "show", dev).splitlines():
        if ":" in line:
            v = line.split(":", 1)[1]
            if v:
                return v
    return "-"


def _wifi_list():
    out = []
    for line in _nm("-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi",
                    "list", timeout=15).splitlines():
        f = line.split(":")
        if len(f) >= 4 and f[1]:
            out.append({"active": f[0] == "yes", "ssid": f[1],
                        "signal": f[2], "sec": f[3] or "열림"})
    # 신호 센 것부터, 중복 SSID 제거
    out.sort(key=lambda d: -int(d["signal"] or 0))
    seen, uniq = set(), []
    for d in out:
        if d["ssid"] in seen:
            continue
        seen.add(d["ssid"])
        uniq.append(d)
    return uniq


def _ask_password(parent, ssid):
    d = Gtk.Dialog(title=f"{ssid} 연결", transient_for=parent, modal=True)
    d.add_buttons("취소", Gtk.ResponseType.CANCEL, "연결", Gtk.ResponseType.OK)
    box = d.get_content_area()
    box.set_spacing(8)
    box.set_border_width(14)
    box.add(Gtk.Label(label=f"'{ssid}' 의 암호를 입력하세요.", xalign=0))
    e = Gtk.Entry()
    e.set_visibility(False)
    e.set_activates_default(True)
    box.add(e)
    show = Gtk.CheckButton(label="암호 보기")
    show.connect("toggled", lambda w: e.set_visibility(w.get_active()))
    box.add(show)
    d.set_default_response(Gtk.ResponseType.OK)
    d.show_all()
    resp = d.run()
    pw = e.get_text()
    d.destroy()
    return pw if resp == Gtk.ResponseType.OK else None


def build(store):
    p = Page("네트워크", "유선과 무선 연결을 관리합니다.")

    if not shutil.which("nmcli"):
        p.add_widget(_notice("NetworkManager(nmcli) 가 설치돼 있지 않습니다."))
        return p

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    p.box.pack_start(body, False, False, 0)

    def refresh(*_):
        for c in body.get_children():
            body.remove(c)
        _fill(p, body, store, refresh)
        body.show_all()

    _fill(p, body, store, refresh)
    return p


def _fill(page, body, store, refresh=None):
    def sect(title):
        l = Gtk.Label(label=title, xalign=0)
        l.get_style_context().add_class("section-title")
        body.pack_start(l, False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        body.pack_start(lb, False, False, 0)
        return lb

    # ── 장치 상태 ──
    s = sect("연결 상태")
    devs = _devices()
    if not devs:
        row(s, "장치 없음", "네트워크 인터페이스를 찾지 못했습니다")
    for d in devs:
        state = {"connected": "연결됨", "disconnected": "연결 안 됨",
                 "unavailable": "사용 불가", "connecting": "연결 중"}.get(
                     d["state"], d["state"])
        ico = ["network-wired", "network-wired-symbolic"] if d["type"] == "ethernet" \
            else ["network-wireless", "network-wireless-symbolic"]
        sub = f"{d['type']}  ·  {state}"
        if d["conn"]:
            sub += f"  ·  {d['conn']}"
        row(s, d["dev"], sub, icon=ico, control=info(_ip4(d["dev"])))

    # ── Wi-Fi ──
    has_wifi = any(d["type"] == "wifi" for d in devs)
    if has_wifi:
        radio = _nm("radio", "wifi").strip() == "enabled"
        s = sect("Wi-Fi")
        row(s, "Wi-Fi 사용", None, icon=["network-wireless"],
            control=switch(radio, lambda v: (
                run(["nmcli", "radio", "wifi", "on" if v else "off"]),
                GLib.timeout_add(1200, lambda: (refresh and refresh(), False)[1]))))

        if radio:
            nets = _wifi_list()
            if not nets:
                row(s, "검색된 네트워크 없음", "잠시 후 새로 고쳐 보세요")
            for n in nets[:20]:
                bars = int(n["signal"] or 0)
                ico = "network-wireless-signal-" + (
                    "excellent" if bars > 75 else "good" if bars > 50
                    else "ok" if bars > 25 else "weak")
                sub = f"신호 {n['signal']}%  ·  {n['sec']}"
                if n["active"]:
                    sub = "연결됨  ·  " + sub

                def connect(ssid=n["ssid"], sec=n["sec"], active=n["active"]):
                    win = body.get_toplevel()
                    if active:
                        run(["nmcli", "connection", "down", "id", ssid])
                    else:
                        pw = None
                        if sec and sec != "열림":
                            pw = _ask_password(win, ssid)
                            if pw is None:
                                return
                        cmd = ["nmcli", "device", "wifi", "connect", ssid]
                        if pw:
                            cmd += ["password", pw]
                        run(cmd, timeout=30)
                    if refresh:
                        GLib.timeout_add(800, lambda: (refresh(), False)[1])

                row(s, n["ssid"], sub, icon=[ico, "network-wireless"],
                    control=button("연결 끊기" if n["active"] else "연결", connect))

    # ── 도구 ──
    s = sect("도구")
    row(s, "고급 연결 편집기", "고정 IP, VPN, 프로파일 관리",
        icon=["preferences-system-network", "network-workgroup"],
        control=button("nm-connection-editor 열기",
                       lambda: spawn("nm-connection-editor")))
    if refresh:
        row(s, "새로 고침", "장치와 Wi-Fi 목록을 다시 읽습니다",
            control=button("새로 고침", refresh))


def _notice(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


PAGES = [{"id": "network", "title": "네트워크",
          "icon": ["network-wired", "network-workgroup", "preferences-system-network",
                    "network-wired-symbolic"],
          "build": build}]
