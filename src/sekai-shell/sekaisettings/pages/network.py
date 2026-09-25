"""네트워크 — NetworkManager(nmcli) 기반.

nmcli(장치 상태·무선 검색·연결·Wi-Fi 켜고 끄기)는 작업 스레드에서 — 창이 멈추지 않게.
"""
import os
import re
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import run, run_async, spawn
from ..widgets import Page, button, info, row, switch


def _nm(*args, timeout=8):
    return run(["nmcli", "-t", "-c", "no", *args], timeout=timeout)


def _unescape(v):
    """nmcli 간결 출력(-t, -g)의 \\: \\\\ 를 원래 글자로"""
    return re.sub(r"\\(.)", r"\1", v)


def _fields(line):
    """nmcli -t 한 줄 → 칸들. 값 안의 : 는 \\: 로 이스케이프되어 온다 (SSID 에 : 가 들어갈 수 있다)"""
    return [_unescape(f) for f in re.split(r"(?<!\\):", line)]


def _devices():
    out = []
    for line in _nm("-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines():
        f = _fields(line)
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
        f = _fields(line)
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


def _key_mgmt(sec):
    """nmcli SECURITY 칸(WPA2·WPA3·WEP…) → 802-11-wireless-security.key-mgmt"""
    u = (sec or "").upper()
    if "802.1X" in u:
        return None                                  # 기업용 — 비밀번호 하나로는 안 된다
    if "WPA3" in u and "WPA1" not in u and "WPA2" not in u:
        return "sae"
    if "WPA" in u:
        return "wpa-psk"
    if "WEP" in u:
        return "none"
    return None


def wifi_connect_secret(ssid, sec, pw, dev=None, timeout=45):
    """암호가 있는 Wi-Fi 에 연결 — 비밀번호를 명령줄에 넣지 않는다 (ps 로 누구나 볼 수 있다).
    같은 SSID 의 프로필이 있으면 그것을, 없으면 비밀번호 없이 새로 만들고,
    `nmcli connection up … passwd-file` 로 메모리 파일(memfd)에 담아 넘긴다.
    NetworkManager 는 이렇게 받은 비밀번호를 프로필에 저장한다 (nm-applet 과 같은 길).
    새로 만든 프로필로 연결하지 못하면 그 프로필은 지운다. 반환: None(성공) 또는 오류 한 줄."""
    km = _key_mgmt(sec)
    if km is None:
        return "이 방식의 네트워크는 고급 연결 편집기에서 연결해 주세요"
    uuid, created = None, False
    for line in _nm("-f", "UUID,TYPE", "connection", "show").splitlines():
        f = _fields(line)
        if len(f) >= 2 and f[1] == "802-11-wireless":
            # -g 도 간결 출력이라 : 와 \ 가 이스케이프되어 온다 — 풀어야 'Home:5G' 같은 SSID 의 프로필을 찾는다
            #   (못 찾으면 연결할 때마다 같은 이름의 프로필이 새로 쌓였다)
            got = _unescape(run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show",
                                 "uuid", f[0]]).rstrip("\n"))
            if got == ssid:
                uuid = f[0]
                break
    if uuid is None:
        add = ["nmcli", "connection", "add", "type", "wifi", "con-name", ssid,
               "ssid", ssid, "wifi-sec.key-mgmt", km]
        if km == "none":                             # WEP: 5·13 글자나 10·26 자리 16진수면 키, 아니면 암호문
            add += ["wifi-sec.wep-key-type", "1" if len(pw) in (5, 13, 10, 26) else "2"]
        if dev:
            add += ["ifname", dev]
        r = subprocess.run(add, capture_output=True, text=True, timeout=15)
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r.stdout + r.stderr)
        if r.returncode != 0 or not m:
            return (r.stderr or r.stdout).strip().splitlines()[-1:][0] if (r.stderr or r.stdout).strip() \
                else "프로필을 만들지 못했습니다"
        uuid, created = m.group(0), True
    field = "802-11-wireless-security.wep-key0" if km == "none" else "802-11-wireless-security.psk"
    fd = os.memfd_create("sekai-wifi", os.MFD_CLOEXEC)
    try:
        os.write(fd, f"{field}:{pw}\n".encode())
        os.lseek(fd, 0, os.SEEK_SET)
        cmd = ["nmcli", "-w", str(timeout), "connection", "up", "uuid", uuid, "passwd-file", f"/dev/fd/{fd}"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15, pass_fds=(fd,))
            err = None if r.returncode == 0 else ((r.stderr or r.stdout).strip().splitlines()[-1:] or
                                                  ["알 수 없는 오류"])[0]
        except subprocess.TimeoutExpired:
            err = "시간이 초과되었습니다"
    finally:
        os.close(fd)
    if err is not None and created:
        subprocess.run(["nmcli", "connection", "delete", "uuid", uuid], capture_output=True, timeout=15)
    return err


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
    row(_sect(body, "연결 상태"), "연결 상태를 읽는 중…")
    gen = {"n": 0}                  # 새로 고침이 겹치면 마지막 요청의 결과만 그린다

    def refresh(*_):
        gen["n"] += 1
        n = gen["n"]
        threading.Thread(target=lambda: GLib.idle_add(show, n, *_state()), daemon=True).start()

    def show(n, devs, radio):
        if n != gen["n"]:
            return False
        for c in body.get_children():
            body.remove(c)
        _fill(p, body, store, devs, radio, refresh)
        body.show_all()
        return False

    refresh()
    return p


def _state():
    """장치 목록(IP 포함)과 Wi-Fi 켜짐 — nmcli 를 장치마다 부르니 작업 스레드에서 (창이 멈추지 않게)"""
    devs = _devices()
    for d in devs:
        d["ip"] = _ip4(d["dev"])
    radio = any(d["type"] == "wifi" for d in devs) and _nm("radio", "wifi").strip() == "enabled"
    return devs, radio


def _sect(body, title):
    l = Gtk.Label(label=title, xalign=0)
    l.get_style_context().add_class("section-title")
    body.pack_start(l, False, False, 0)
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    lb.get_style_context().add_class("section")
    body.pack_start(lb, False, False, 0)
    return lb


def _fill(page, body, store, devs, radio, refresh=None):
    # ── 장치 상태 ──
    s = _sect(body, "연결 상태")
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
        row(s, d["dev"], sub, icon=ico, control=info(d["ip"]))

    # ── Wi-Fi ──
    has_wifi = any(d["type"] == "wifi" for d in devs)
    if has_wifi:
        s = _sect(body, "Wi-Fi")
        row(s, "Wi-Fi 사용", None, icon=["network-wireless"],
            control=switch(radio, lambda v: run_async(
                ["nmcli", "radio", "wifi", "on" if v else "off"],
                lambda *_: GLib.timeout_add(1200, lambda: (refresh and refresh(), False)[1]))))

        if radio:
            wifi_dev = next((d["dev"] for d in devs if d["type"] == "wifi"), None)
            status = Gtk.Label(xalign=0)
            status.get_style_context().add_class("row-sub")
            status.set_line_wrap(True)
            body.pack_start(status, False, False, 0)
            waiting = row(s, "무선 네트워크를 찾는 중…")

            def connect(ssid, sec, active):
                win = body.get_toplevel()
                pw = None
                if not active and sec and sec != "열림":
                    pw = _ask_password(win, ssid)
                    if pw is None:
                        return
                status.set_text("연결을 끊는 중…" if active else f"'{ssid}' 에 연결하는 중…")

                def work():
                    if pw:
                        # 비밀번호는 명령줄로 넘기지 않는다 (wifi_connect_secret)
                        e = wifi_connect_secret(ssid, sec, pw, wifi_dev)
                        GLib.idle_add(finish, ssid, None if e is None else [e])
                        return
                    if active:
                        cmd = ["nmcli", "device", "disconnect", wifi_dev] if wifi_dev else \
                              ["nmcli", "connection", "down", "id", ssid]
                    else:
                        cmd = ["nmcli", "device", "wifi", "connect", ssid]
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                        err = None if r.returncode == 0 else (r.stderr or r.stdout).strip().splitlines()[-1:]
                    except subprocess.TimeoutExpired:
                        err = ["시간이 초과되었습니다"]
                    GLib.idle_add(finish, ssid, err)
                threading.Thread(target=work, daemon=True).start()

            def finish(ssid, err):
                if err is not None:
                    msg = err[0] if err else "알 수 없는 오류"
                    if "Secrets were required" in msg or "802-11-wireless-security" in msg:
                        msg = "암호가 맞지 않습니다"
                    status.set_text(f"'{ssid}' 에 연결하지 못했습니다: {msg}")
                elif refresh:
                    refresh()
                return False

            def show(nets, s=s):            # 기본 인자로 묶는다 — 아래에서 s 가 "도구" 섹션으로 바뀐 뒤에 불린다
                s.remove(waiting)
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
                    row(s, n["ssid"], sub, icon=[ico, "network-wireless"],
                        control=button("연결 끊기" if n["active"] else "연결",
                                       lambda n=n: connect(n["ssid"], n["sec"], n["active"])))
                s.show_all()
                return False

            threading.Thread(target=lambda: GLib.idle_add(show, _wifi_list()), daemon=True).start()

    # ── 도구 ──
    s = _sect(body, "도구")
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
