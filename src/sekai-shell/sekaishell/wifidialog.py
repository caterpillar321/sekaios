"""Wi-Fi 연결 창 — 숨겨진 네트워크·회사·학교(802.1X) 로그인까지 (설정 › 네트워크와 빠른 설정이 함께 쓴다).
예전에는 설정 페이지에 있어 빠른 설정(공용)이 설정 앱을 불러야 했다 (카나데 층 위반)."""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .ui import text_entry  # noqa: E402
from . import dbg  # noqa: E402,F401
from .nm import check_domain, check_text, check_wifi_key, connect_wifi  # noqa: E402
from .ui import combo  # noqa: E402


_entry = text_entry


def _pw_entry(placeholder=None):
    e = Gtk.Entry()
    e.set_visibility(False)
    e.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    e.set_width_chars(28)
    if placeholder:
        e.set_placeholder_text(placeholder)
    return e


class _Form:
    """입력 창의 공통 틀 — 설명 · 칸(격자) · 오류 한 줄 · 진행 표시 · [취소][확인].
    확인을 누르면 on_ok(self) — 검사·작업은 부르는 쪽이, 끝나면 done(오류|None). 오류는 창 안에 보인다"""

    def __init__(self, parent, title, intro, ok_label, on_ok, width=480):
        self.alive, self.on_ok = True, on_ok
        d = self.d = Gtk.Dialog(title=title, transient_for=parent, modal=parent is not None,
                                destroy_with_parent=True)
        d.set_default_size(width, -1)
        d.set_resizable(False)
        box = self.box = d.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)
        if intro:
            l = Gtk.Label(label=intro, xalign=0)
            l.set_line_wrap(True)
            l.set_max_width_chars(56)
            box.pack_start(l, False, False, 0)
        self.grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(self.grid, False, False, 0)
        self.n = 0
        self.err = Gtk.Label(xalign=0)
        self.err.get_style_context().add_class("net-error")
        self.err.set_line_wrap(True)
        self.err.set_max_width_chars(56)
        self.err.set_no_show_all(True)
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spin = Gtk.Spinner()
        self.spin.set_no_show_all(True)
        foot.pack_start(self.spin, False, False, 0)
        foot.pack_start(self.err, True, True, 0)
        box.pack_start(foot, False, False, 0)
        d.add_button("취소", Gtk.ResponseType.CANCEL)
        self.ok = d.add_button(ok_label, Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("accent-btn")
        d.set_default_response(Gtk.ResponseType.OK)
        d.connect("response", self._response)
        d.connect("delete-event", lambda *_: self.spin.get_visible())      # 작업 중엔 닫지 않는다
        d.connect("destroy", lambda *_: setattr(self, "alive", False))

    def add(self, label, widget, note=None):
        """한 줄 (이름표 · 칸). 돌려주는 것: 숨기고 보일 때 쓰는 [위젯…]"""
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_valign(Gtk.Align.CENTER)
        self.grid.attach(lab, 0, self.n, 1, 1)
        widget.set_hexpand(True)
        self.grid.attach(widget, 1, self.n, 1, 1)
        ws = [lab, widget]
        self.n += 1
        if note:
            nl = Gtk.Label(label=note, xalign=0)
            nl.get_style_context().add_class("row-sub")
            nl.set_line_wrap(True)
            nl.set_max_width_chars(44)
            self.grid.attach(nl, 1, self.n, 1, 1)
            ws.append(nl)
            self.n += 1
        return ws

    def add_wide(self, widget):
        self.grid.attach(widget, 0, self.n, 2, 1)
        self.n += 1
        return [widget]

    def show(self):
        self.d.show_all()

    def error(self, text):
        self.err.set_text(text or "")
        self.err.set_visible(bool(text))

    def busy(self, on, text=None):
        self.spin.set_visible(on)
        (self.spin.start if on else self.spin.stop)()
        self.ok.set_sensitive(not on)
        self.grid.set_sensitive(not on)
        ctx = self.err.get_style_context()
        if on:
            ctx.remove_class("net-error")
            self.error(text or "")
        else:
            ctx.add_class("net-error")

    def done(self, err):
        if not self.alive:
            return
        self.busy(False)
        if err:
            self.error(err)
        else:
            self.d.destroy()

    def _response(self, _d, resp):
        if resp == Gtk.ResponseType.OK:
            self.error("")
            self.on_ok(self)
        elif not self.spin.get_visible():
            self.d.destroy()


def _show_rows(rows, on):
    for w in rows:
        w.set_visible(on)


SEC_CHOICES = [("wpa-psk", "WPA2-개인"), ("sae", "WPA3-개인"), ("eap", "WPA2/WPA3-엔터프라이즈 (회사·학교)"),
               ("open", "없음 (개방)"), ("wep", "WEP (오래된 방식)")]


PHASE2 = {"peap": [("mschapv2", "MSCHAPv2"), ("gtc", "GTC")],
          "ttls": [("pap", "PAP"), ("mschapv2", "MSCHAPv2")]}


class WifiDialog:
    """Wi-Fi 연결 창.
      ssid=None          숨겨진 네트워크 연결 (네트워크 이름 · 보안 종류 · 키)
      security="eap"     회사·학교 네트워크 로그인 (EAP 방법 · 2단계 인증 · 사용자 이름 · 암호 · CA 인증서 · 도메인)
      security=그 밖     암호 입력 (WPA·WEP)
    parent 가 없으면(빠른 설정) 따로 뜨는 창. 연결은 작업 스레드에서, 성공하면 창을 닫고 on_done() 을 부른다.
    prefill: 저장된 프로필에서 읽은 값 (사용자 이름 · EAP 방법 · 도메인 …)"""

    def __init__(self, parent=None, ssid=None, dev=None, security=None, on_done=None, prefill=None):
        self.ssid, self.dev, self.on_done = ssid, dev, on_done
        self.hidden = ssid is None
        pre = prefill or {}
        if self.hidden:
            title, intro, ok = "숨겨진 네트워크 연결", ("이름을 알리지 않는 네트워크입니다. 네트워크 이름(SSID)과 "
                                                   "보안 정보를 입력하세요."), "연결"
        elif security == "eap":
            title, intro, ok = f"{ssid} 에 로그인", (f"‘{ssid}’ 은(는) 회사·학교 네트워크입니다. 기관에서 받은 "
                                                   "계정으로 로그인하세요."), "로그인"
        else:
            title, intro, ok = f"{ssid} 연결", f"‘{ssid}’ 의 네트워크 보안 키를 입력하세요.", "연결"
        f = self.f = _Form(parent, title, intro, ok, self._go)
        self.rows = {}
        if self.hidden:
            self.ssid_e = _entry(placeholder="네트워크 이름 (SSID)", max_len=32)
            f.add("네트워크 이름", self.ssid_e)
            self.sec_c = combo(SEC_CHOICES, "wpa-psk", on_change=lambda _v: self._sec_changed())
            f.add("보안 종류", self.sec_c)
        else:
            self.sec_c = None
        self.security = security or "wpa-psk"
        self.key_e = _pw_entry("네트워크 보안 키")
        self.rows["key"] = f.add("보안 키", self.key_e)
        self.eap_c = combo([("peap", "PEAP"), ("ttls", "TTLS")], pre.get("eap", "peap"),
                           on_change=lambda _v: self._eap_changed())
        self.rows["eap"] = f.add("EAP 방법", self.eap_c)
        self.p2_c = Gtk.ComboBoxText()
        self.rows["p2"] = f.add("2단계 인증", self.p2_c)
        self.id_e = _entry(pre.get("identity", ""), placeholder="예: hong@example.ac.kr", max_len=128)
        self.rows["id"] = f.add("사용자 이름", self.id_e)
        self.pw_e = _pw_entry()
        self.rows["pw"] = f.add("암호", self.pw_e)
        self.anon_e = _entry(pre.get("anon", ""), placeholder="적지 않아도 됩니다", max_len=128)
        self.rows["anon"] = f.add("익명 ID", self.anon_e)
        self.ca_c = combo([("system", "시스템 인증서 사용"), ("none", "확인 안 함")], pre.get("ca", "system"),
                          on_change=lambda _v: self._ca_changed())
        self.rows["ca"] = f.add("CA 인증서", self.ca_c)
        self.dom_e = _entry(pre.get("domain", ""), placeholder="예: radius.example.ac.kr", max_len=253)
        self.rows["dom"] = f.add("서버 도메인", self.dom_e, note="기관이 알려 준 인증 서버의 이름 — 이 이름의 "
                                                          "인증서를 가진 서버에만 암호를 보냅니다")
        self.warn = Gtk.Label(label="서버를 확인하지 않으면 같은 이름의 가짜 네트워크에 암호가 새어 나갈 수 있습니다.",
                              xalign=0)
        self.warn.get_style_context().add_class("row-sub")
        self.warn.set_line_wrap(True)
        self.warn.set_max_width_chars(56)
        self.rows["warn"] = f.add_wide(self.warn)
        self.show_chk = Gtk.CheckButton(label="암호 표시")
        self.show_chk.connect("toggled", lambda w: (self.key_e.set_visibility(w.get_active()),
                                                     self.pw_e.set_visibility(w.get_active())))
        self.rows["show"] = f.add_wide(self.show_chk)
        self.auto_chk = Gtk.CheckButton(label="자동으로 연결")
        self.auto_chk.set_active(True)
        f.add_wide(self.auto_chk)
        for e in (self.key_e, self.pw_e, self.id_e, self.dom_e):
            e.set_activates_default(True)
        f.show()
        self._eap_changed(pre.get("phase2"))
        self._sec_changed()
        (self.ssid_e if self.hidden else self.id_e if self.security == "eap" else self.key_e).grab_focus()

    def _sec(self):
        return self.sec_c.get_active_id() if self.sec_c is not None else self.security

    def _sec_changed(self):
        sec = self._sec()
        eap = sec == "eap"
        _show_rows(self.rows["key"], sec in ("wpa-psk", "sae", "wep"))
        for k in ("eap", "p2", "id", "pw", "anon", "ca"):
            _show_rows(self.rows[k], eap)
        _show_rows(self.rows["show"], sec != "open")
        self._ca_changed()

    def _eap_changed(self, want=None):
        cur = want or self.p2_c.get_active_id()
        self.p2_c.remove_all()
        opts = PHASE2.get(self.eap_c.get_active_id(), PHASE2["peap"])
        for k, t in opts:
            self.p2_c.append(k, t)
        if not (cur and self.p2_c.set_active_id(cur)):
            self.p2_c.set_active(0)

    def _ca_changed(self):
        eap = self._sec() == "eap"
        system = self.ca_c.get_active_id() == "system"
        _show_rows(self.rows["dom"], eap and system)
        _show_rows(self.rows["warn"], eap and not system)

    def _spec(self):
        """입력 검사 → (오류, spec)"""
        sec = self._sec()
        ssid = self.ssid_e.get_text() if self.hidden else self.ssid
        if self.hidden:
            if not ssid.strip():
                return "네트워크 이름(SSID)을 입력하세요", None
            if len(ssid.encode()) > 32 or "\x00" in ssid:
                return "네트워크 이름은 32바이트(한글 10자)까지입니다", None
        spec = {"ssid": ssid, "hidden": self.hidden, "security": sec, "autoconnect": self.auto_chk.get_active()}
        if sec in ("wpa-psk", "sae", "wep"):
            key = self.key_e.get_text()
            err = check_wifi_key(sec, key)
            if err:
                return err, None
            spec["secret"] = key
        elif sec == "eap":
            ident, pw = self.id_e.get_text().strip(), self.pw_e.get_text()
            err = check_text(ident, "사용자 이름", 128) or (check_text(self.anon_e.get_text(), "익명 ID", 128)
                                                        if self.anon_e.get_text().strip() else "")
            if err:
                return err, None
            if not pw:
                return "암호를 입력하세요", None
            if "\n" in pw or len(pw) > 256:
                return "암호에 쓸 수 없는 글자가 있습니다", None
            ca = self.ca_c.get_active_id()
            if ca == "system":
                err = check_domain(self.dom_e.get_text())
                if err:
                    return err, None
            spec.update(eap=self.eap_c.get_active_id(), phase2=self.p2_c.get_active_id() or "mschapv2",
                        identity=ident, anon=self.anon_e.get_text().strip(), secret=pw, ca=ca,
                        domain=self.dom_e.get_text().strip().rstrip("."))
        return None, spec

    def _go(self, f):
        err, spec = self._spec()
        if err:
            f.error(err)
            return
        f.busy(True, f"‘{spec['ssid']}’ 에 연결하는 중…")
        dev = self.dev

        def work():
            try:
                e = connect_wifi(spec, dev)
            except Exception as ex:                   # 스레드에서 새면 창이 영영 잠긴다
                dbg("Wi-Fi 연결 실패", repr(ex))
                e = "연결하는 중 오류가 났습니다"
            GLib.idle_add(finish, e)

        def finish(e):
            f.done(e)
            if e is None and self.on_done:
                self.on_done()
            return False
        threading.Thread(target=work, daemon=True).start()
