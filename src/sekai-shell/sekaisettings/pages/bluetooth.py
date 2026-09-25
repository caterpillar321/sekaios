"""블루투스 — sekaishell.bluetooth(bluez) 위에 올린 설정 페이지.

켜고 끄기 · 이 PC 의 이름 · 내 장치(연결/끊기/제거) · 장치 추가(검색 → 짝 맺기 → 연결).
페이지가 있는 동안은 짝 맺기 에이전트를 기본으로 등록해 두고(암호 확인·입력은 이 창의 대화상자로),
페이지가 없어질 때(창을 닫을 때) 해제한다. D-Bus 는 모두 비동기라 창이 멈추지 않는다.
블루투스 오디오 장치는 연결만 하면 WirePlumber 가 알아서 출력으로 잡는다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from sekaishell import bluetooth

from ..widgets import Page, button, info, row, switch


def _has_adapter():
    """커널이 블루투스 어댑터를 알고 있나 — 서비스가 없을 때 "장치 없음"과 "서비스 꺼짐"을 가른다"""
    try:
        return bool(os.listdir("/sys/class/bluetooth"))
    except OSError:
        return False

SCAN_SECS = 60
FOUND_MAX = 30
BT_ICONS = ["bluetooth", "bluetooth-active", "preferences-system-bluetooth", "blueman",
            "bluetooth-active-symbolic", "bluetooth-symbolic"]
# bluez 의 Icon(freedesktop 이름) → 테마에서 찾을 후보
DEV_ICONS = {
    "audio-headset": ["audio-headset", "audio-headphones"],
    "audio-headphones": ["audio-headphones", "audio-headset"],
    "audio-card": ["audio-speakers", "audio-card", "audio-headphones"],
    "input-keyboard": ["input-keyboard"],
    "input-mouse": ["input-mouse"],
    "input-gaming": ["input-gaming", "applications-games"],
    "input-tablet": ["input-tablet"],
    "phone": ["phone", "smartphone", "phone-symbolic"],
    "computer": ["computer", "laptop"],
    "video-display": ["video-display"],
    "multimedia-player": ["multimedia-player"],
    "camera-photo": ["camera-photo"],
    "camera-video": ["camera-video", "camera-web"],
    "printer": ["printer"],
    "scanner": ["scanner"],
    "modem": ["modem", "network-wireless"],
    "network-wireless": ["network-wireless"],
}
OP_TEXT = {"pair": "짝 맺는 중…", "connect": "연결하는 중…", "disconnect": "연결을 끊는 중…",
           "remove": "제거하는 중…"}


def _dev_icon(name):
    return DEV_ICONS.get(name, [name]) + BT_ICONS


def _signal(rssi):
    if rssi is None:
        return ""
    return "신호 강함" if rssi >= -60 else "신호 보통" if rssi >= -75 else "신호 약함"


def _notice(text=""):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def _reveal(w, on):
    """no_show_all 인 위젯 보이기/숨기기 — show_all 이 안쪽으로 내려가지 않으니 안쪽은 따로
    (안쪽에서 따로 no_show_all 을 켠 위젯은 그대로 숨어 있다)"""
    if on and not w.get_visible():
        w.show()
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                c.show_all()
    elif not on and w.get_visible():
        w.hide()


def _clear(lb):
    for c in lb.get_children():
        lb.remove(c)
        c.destroy()


class _Asker:
    """짝 맺기 에이전트의 질문 → 설정 창의 대화상자 (sekaishell.bluetooth 의 ui 규약)"""

    def __init__(self, page):
        self.page = page
        self.dlg = None
        self.path = None          # 대화상자가 묻고 있는 장치
        self.kind = None
        self.typed = None         # 표시 대화상자의 "입력한 자리" 줄

    def _parent(self):
        w = self.page.p.get_toplevel()
        return w if isinstance(w, Gtk.Window) and w.is_toplevel() else None

    def close(self, path=None):
        """떠 있는 대화상자를 닫는다 (path 를 주면 그 장치의 것만)"""
        if self.dlg is None or (path is not None and path != self.path):
            return
        d, self.dlg = self.dlg, None
        self.path = self.kind = self.typed = None
        d.destroy()

    def _open(self, dev, kind, text, sub, code=None, buttons=(), entry=None, on_response=None):
        self.close()
        d = Gtk.MessageDialog(transient_for=self._parent(), modal=True,
                              message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=text)
        d.set_title("블루투스")
        if sub:
            d.format_secondary_text(sub)
        area = d.get_message_area()
        if code is not None:
            big = Gtk.Label(xalign=0)
            big.get_style_context().add_class("bt-code")
            big.set_markup(f"<span size='xx-large' weight='bold' letter_spacing='3000'>"
                           f"{GLib.markup_escape_text(code)}</span>")
            big.set_selectable(True)
            area.pack_start(big, False, False, 0)
        if entry is not None:
            area.pack_start(entry, False, False, 0)
        for label, resp in buttons:
            d.add_button(label, resp)
        if buttons:
            d.set_default_response(buttons[-1][1])

        def responded(dlg, resp):
            if dlg is not self.dlg:
                return
            self.dlg = None
            self.path = self.kind = self.typed = None
            text_ = entry.get_text() if entry is not None else None
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                self.page.declined = dev["path"]
            if on_response:
                on_response(resp, text_)
        d.connect("response", responded)
        self.dlg, self.path, self.kind = d, dev["path"], kind
        d.show_all()
        return d

    # ── ui 규약 ──
    def confirm(self, dev, code, reply):
        self._open(dev, "confirm", f"‘{dev['name']}’ 장치에 이 숫자가 보이나요?",
                   "장치에 보이는 숫자와 같으면 ‘짝 맺기’를 누르세요. "
                   "휴대폰이면 휴대폰에서도 확인을 눌러야 합니다.", code=code,
                   buttons=(("취소", Gtk.ResponseType.CANCEL), ("짝 맺기", Gtk.ResponseType.OK)),
                   on_response=lambda resp, _t: reply(resp == Gtk.ResponseType.OK))

    def display(self, dev, code, entered):
        if self.dlg is not None and self.kind == "display" and self.path == dev["path"]:
            if self.typed is not None and entered is not None:
                self.typed.set_text(f"입력한 자리: {entered}")
            return
        path = dev["path"]

        def resp(_r, _t):                           # 취소를 누르거나 창을 닫았다 — 짝 맺기도 그만둔다
            self.page._cancel_pair(path)
        self._open(dev, "display", f"‘{dev['name']}’ 장치에서 이 숫자를 입력하세요",
                   "장치(키보드 등)에서 아래 숫자를 치고 Enter 키를 누르면 짝이 맺어집니다.", code=code,
                   buttons=(("취소", Gtk.ResponseType.CANCEL),), on_response=resp)
        if entered is not None and self.dlg is not None:
            self.typed = Gtk.Label(label=f"입력한 자리: {entered}", xalign=0)
            self.typed.get_style_context().add_class("row-sub")
            self.dlg.get_message_area().pack_start(self.typed, False, False, 0)
            self.typed.show()

    def ask_code(self, dev, numeric, reply):
        e = Gtk.Entry()
        e.set_activates_default(True)
        e.set_max_length(6 if numeric else 16)
        e.set_width_chars(18)
        if numeric:
            e.set_input_purpose(Gtk.InputPurpose.DIGITS)
            text = f"‘{dev['name']}’ 장치에 보이는 6자리 숫자를 입력하세요"
            sub = "장치 화면에 짝 맺기 숫자(암호 키)가 표시됩니다."
        else:
            text = f"‘{dev['name']}’ 장치의 PIN 을 입력하세요"
            sub = "장치 설명서에 적혀 있습니다. 없으면 보통 0000 이나 1234 입니다."
        self._open(dev, "code", text, sub, entry=e,
                   buttons=(("취소", Gtk.ResponseType.CANCEL), ("확인", Gtk.ResponseType.OK)),
                   on_response=lambda r, t: reply(t if r == Gtk.ResponseType.OK and t else None))

    def authorize(self, dev, uuid, reply):
        if uuid is None:
            text = f"‘{dev['name']}’ 장치가 이 PC 와 짝을 맺으려고 합니다"
            sub = "직접 짝 맺기를 시작한 게 아니라면 ‘거부’를 누르세요."
        else:
            text = f"‘{dev['name']}’ 장치가 이 PC 에 연결하려고 합니다"
            sub = f"기능: {bluetooth.service_name(uuid)}\n모르는 장치라면 ‘거부’를 누르세요."
        self._open(dev, "authorize", text, sub,
                   buttons=(("거부", Gtk.ResponseType.CANCEL), ("허락", Gtk.ResponseType.OK)),
                   on_response=lambda resp, _t: reply(resp == Gtk.ResponseType.OK))

    def cancel(self):
        self.close()


class BluetoothPage:
    def __init__(self, store):
        self.bt = bluetooth.get()
        self.p = Page("블루투스", "헤드폰·스피커·키보드·마우스·휴대폰 같은 블루투스 장치를 연결합니다.")
        self.dead = False
        self.ops = {}              # 장치 경로 → 하고 있는 일 (OP_TEXT 의 키)
        self.pairing = None        # 짝 맺고 연결까지 하는 중인 장치 (한 번에 하나)
        self.declined = None       # 사람이 짝 맺기 질문에 "취소/거부"로 답한 장치 — 실패를 오류로 보이지 않게
        self.seen = {}             # 이번 검색에서 본 장치 → 가장 센 신호 — 신호가 흔들려도 순서가 춤추지 않게 최댓값
        self.scan_on = False       # 이 페이지가 검색을 켰나
        self.scan_src = 0
        self.power_want = None     # 켜고 끄는 중에 바라는 값 — bluez 가 따라올 때까지 스위치가 옛 값으로 튀지 않게
        self.quiet = False         # 스위치를 코드로 맞추는 중 (사람이 누른 게 아니다)
        self.made_visible = False  # 이 페이지가 "찾을 수 있게"를 켰나 (떠날 때 끈다)
        self.drawn = {}            # 목록별 마지막으로 그린 내용 — 같으면 다시 그리지 않는다 (누르던 버튼이 사라지지 않게)
        self.say_src = 0

        self.notice = _notice()
        self.notice.set_no_show_all(True)
        self.p.add_widget(self.notice)

        # ── 켜기 · 이름 · 찾을 수 있게 ──
        self.top = self._sect(None)
        self.power_sw = switch(False, self._on_power)
        self.power_row = row(self.top, "블루투스", " ", icon=BT_ICONS, control=self.power_sw)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.name_label = info("")
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.set_max_width_chars(28)
        box.pack_start(self.name_label, False, False, 0)
        box.pack_start(button("이름 바꾸기…", self._rename), False, False, 0)
        self.name_row = row(self.top, "이 PC 의 이름", "다른 장치에서 이 PC 를 찾을 때 보이는 이름",
                            icon=["computer", "computer-symbolic"], control=box)
        self.name_row.set_no_show_all(True)
        self.vis_sw = switch(False, self._on_visible)
        self.vis_row = row(self.top, "다른 장치가 이 PC 를 찾을 수 있게", " ",
                           icon=["view-visible", "view-reveal-symbolic", "network-wireless"],
                           control=self.vis_sw)
        self.vis_row.set_no_show_all(True)

        self.msg = _notice()
        self.msg.set_no_show_all(True)
        self.msg.set_selectable(True)
        self.p.add_widget(self.msg)

        # ── 내 장치 ──
        self.mine_title, self.mine = self._sect("내 장치", titled=True)

        # ── 장치 추가 ──
        self.add_title, self.add = self._sect("장치 추가", titled=True)
        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        ctl.pack_start(self.spinner, False, False, 0)
        self.scan_btn = button("장치 추가", self._toggle_scan, cls="accent-btn")
        self.scan_btn.get_style_context().add_class("bt-scan")
        ctl.pack_start(self.scan_btn, False, False, 0)
        self.add_row = row(self.add, "블루투스 장치 추가", " ", icon=["list-add", "list-add-symbolic"] + BT_ICONS,
                           control=ctl)
        self.found = self._sect(None)

        self.asker = _Asker(self)
        self.bt.register_agent(self.asker)
        self.cb_id = self.bt.on_change(self.refresh)
        self.p.connect("unmap", lambda *_: self.stop_scan())      # 다른 페이지로 옮기면 검색을 멈춘다
        self.p.connect("destroy", self._destroy)
        self.refresh()

    @property
    def widget(self):
        return self.p

    def _sect(self, title, titled=False):
        lbl = None
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("section-title")
            lbl.set_no_show_all(True)
            self.p.add_widget(lbl)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        lb.set_no_show_all(True)
        self.p.add_widget(lb)
        return (lbl, lb) if titled else lb

    def _destroy(self, *_):
        if self.dead:
            return
        self.dead = True                        # 먼저 — 아래 stop_scan 이 없어지는 위젯을 다시 그리지 않게
        self.stop_scan()
        if self.say_src:
            GLib.source_remove(self.say_src)
            self.say_src = 0
        self.bt.off(self.cb_id)
        self.bt.unregister_agent(self.asker)
        self.asker.close()
        if self.made_visible and self.bt.discoverable:
            self.bt.set_discoverable(False)

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
        self.p.busy = bool(self.ops)

    # ── 그리기 ──
    def refresh(self):
        if self.dead:
            return
        bt = self.bt
        if self.scan_on and not bt.powered:     # 꺼졌다 — bluez 가 이미 멈췄다 (stop_scan 이 다시 그린다)
            self.stop_scan()
            return
        devs = bt.devices() if bt.available else []
        if not bt.powered:
            self.seen = {}                      # 꺼지면 찾은 목록도 끝 — 다시 켜고 새로 찾는다
        self._update_seen(devs)
        if self.asker.kind == "display":        # 장치 쪽에서 시작한 짝 맺기가 끝났다 — 안내 창을 닫는다
            d = bt.device(self.asker.path)
            if d is None or d["paired"]:
                self.asker.close()

        if not bt.ready:
            note = "블루투스 상태를 읽는 중…"
        elif not bt.service and _has_adapter():
            note = ("블루투스 서비스가 꺼져 있습니다. "
                    "PC 를 다시 시작해도 이 안내가 계속 보이면 SekaiOS 업데이트를 받아 주세요.")
        elif not bt.available and not bt.blocked:
            # 어댑터가 없으면 서비스도 뜨지 않는다(bluetooth.service 는 어댑터가 있을 때만 켜진다) — 장치 없음으로 안내
            note = ("이 PC 에서 블루투스를 찾을 수 없습니다. "
                    "블루투스 어댑터(USB 동글 등)를 꽂으면 여기에 나타납니다.")
        else:
            note = None
        self.notice.set_text(note or "")
        _reveal(self.notice, note is not None)
        if note is not None:
            self.say(None)                      # 지난 일의 결과는 이제 맞지 않는다
        # 어댑터가 안 보여도 무선 차단 때문이면(노트북 무선 스위치가 USB 어댑터를 끈 경우) 켜기 스위치는 둔다
        _reveal(self.top, note is None)
        for w in (self.mine_title, self.mine, self.add_title, self.add):
            _reveal(w, note is None and bt.available)
        if note is not None:
            _reveal(self.found, False)
            return
        self._draw_top()
        if not bt.available:
            _reveal(self.found, False)
            return
        self._draw_mine(devs)
        self._draw_add(devs)
        self._set_busy()

    def _draw_top(self):
        bt = self.bt
        want = self.power_want
        if want is not None and want == bt.powered:
            self.power_want = want = None       # bluez 가 따라왔다
        on = bt.powered if want is None else want
        self.quiet = True
        self.power_sw.set_active(on)
        self.quiet = False
        self.power_sw.set_sensitive(not bt.hard_blocked)
        if bt.hard_blocked:
            sub = bluetooth.HARD_BLOCKED
        elif want is not None:
            sub = "켜는 중…" if want else "끄는 중…"
        elif bt.powered:
            sub = f"켜짐 — 다른 장치에 ‘{bt.adapter_name}’(으)로 보입니다" if bt.discoverable else "켜짐"
        else:
            sub = "꺼짐"
        self.power_row.sub_label.set_text(sub)

        _reveal(self.name_row, bt.available)
        self.name_label.set_text(bt.adapter_name or "-")
        self.name_label.set_tooltip_text(bt.adapter_address or None)

        _reveal(self.vis_row, bt.available and bt.powered)
        self.quiet = True
        self.vis_sw.set_active(bt.discoverable)
        self.quiet = False
        t = bt.discoverable_timeout
        sub = "휴대폰 등에서 이 PC 를 찾아 짝을 맺을 때 켜세요."
        if t:
            sub += f" {max(1, t // 60)}분 뒤 저절로 꺼집니다." if t >= 60 else f" {t}초 뒤 저절로 꺼집니다."
        self.vis_row.sub_label.set_text(sub)

    def _draw_mine(self, devs):
        bt = self.bt
        paired = [d for d in devs if d["paired"]]
        items = []
        for d in paired:
            op = self.ops.get(d["path"])
            if op:
                sub = OP_TEXT[op]
            elif d["blocked"]:
                sub = "차단됨"
            elif d["connected"]:
                sub = "연결됨" + (f" · 배터리 {d['battery']}%" if d["battery"] is not None else "")
            else:
                sub = "짝 맺음"
            items.append((d["path"], d["name"], d["icon"], sub, d["connected"],
                          bt.powered and not op and not d["blocked"], not op))
        key = (tuple(items), bool(paired))
        if self.drawn.get("mine") == key:
            return
        self.drawn["mine"] = key
        _clear(self.mine)
        if not paired:
            r = row(self.mine, "짝 맺은 장치가 없습니다",
                    "아래 ‘장치 추가’로 헤드폰·키보드·마우스·휴대폰을 연결하세요", icon=BT_ICONS)
            r.show_all()
            return
        for path, name, icon, sub, connected, can_link, can_remove in items:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            link = button("연결 끊기" if connected else "연결",
                          lambda p=path, c=connected: self._link(p, not c))
            link.set_sensitive(can_link)
            box.pack_start(link, False, False, 0)
            rm = button("제거", lambda p=path: self._remove(p))
            rm.set_sensitive(can_remove)
            box.pack_start(rm, False, False, 0)
            r = row(self.mine, name, sub, icon=_dev_icon(icon), control=box)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
            r.show_all()

    def _update_seen(self, devs):
        present = {d["path"] for d in devs}
        for p in list(self.seen):
            if p not in present:
                del self.seen[p]
        if self.scan_on:
            for d in devs:
                if not d["paired"] and d["rssi"] is not None:
                    self.seen[d["path"]] = max(self.seen.get(d["path"], -999), d["rssi"])

    def _draw_add(self, devs):
        bt = self.bt
        scanning = self.scan_on
        if not bt.powered:
            title, sub = "블루투스 장치 추가", "블루투스를 켜면 장치를 추가할 수 있습니다."
        elif scanning:
            title, sub = "장치를 찾는 중…", f"짝 맺을 장치의 ‘짝 맺기’를 누르세요. {SCAN_SECS}초 뒤 저절로 멈춥니다."
        else:
            title, sub = ("블루투스 장치 추가",
                          "장치를 짝 맺기 모드로 두고 누르세요 (보통 전원 버튼이나 블루투스 버튼을 길게 누릅니다).")
        self.add_row.title_label.set_text(title)
        self.add_row.sub_label.set_text(sub)
        self.scan_btn.set_label("검색 멈추기" if scanning else "장치 추가")
        self.scan_btn.set_sensitive(bt.powered and (scanning or self.pairing is None))
        self.spinner.set_visible(scanning)
        (self.spinner.start if scanning else self.spinner.stop)()

        found = [d for d in devs if d["path"] in self.seen and not d["paired"] and not d["blocked"]]
        found.sort(key=lambda d: (-self.seen[d["path"]], d["name"].casefold()))
        found = found[:FOUND_MAX]
        items = []
        for d in found:
            path = d["path"]
            op = self.ops.get(path)
            sub = OP_TEXT[op] if op else _signal(self.seen[path])
            if not d["named"]:
                sub += " · 이름을 알려 주지 않는 장치"
            items.append((path, d["name"], d["icon"], sub, op is not None,
                          bt.powered and self.pairing is None))
        key = (tuple(items), scanning)
        if self.drawn.get("found") != key:
            self.drawn["found"] = key
            _clear(self.found)
            if scanning and not found:
                row(self.found, "아직 찾은 장치가 없습니다",
                    "장치가 켜져 있고 짝 맺기 모드인지 확인하세요", icon=BT_ICONS).show_all()
            for path, name, icon, sub, busy, can in items:
                if busy:
                    b = button("취소", lambda p=path: self._cancel_pair(p))
                else:
                    b = button("짝 맺기", lambda p=path: self.pair(p))
                    b.set_sensitive(can)
                r = row(self.found, name, sub, icon=_dev_icon(icon), control=b)
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                r.show_all()
        _reveal(self.found, bool(self.found.get_children()))

    # ── 켜기 · 이름 · 찾을 수 있게 ──
    def _on_power(self, on):
        if self.quiet:
            return
        self.power_want = on
        self.say(None)

        def done(ok, err):
            if self.dead:
                return
            if self.power_want == on and not ok:
                self.power_want = None
            if not ok and err:
                self.say(f"블루투스를 {'켜지' if on else '끄지'} 못했습니다 — {err}", error=True)
            elif ok and self.power_want == on:
                # 답이 속성 바뀜 알림보다 먼저 올 수 있다 — 잠깐 기다려도 안 따라오면 실제 값을 보인다
                GLib.timeout_add(1500, lambda: (self._settle(on), False)[1])
            self.refresh()
        self.bt.set_powered(on, done)
        self.refresh()

    def _settle(self, on):
        if not self.dead and self.power_want == on:
            self.power_want = None
            self.refresh()

    def _on_visible(self, on):
        if self.quiet:
            return

        def done(ok, err):
            if ok:
                self.made_visible = on
            elif err and not self.dead:
                self.say(f"바꾸지 못했습니다 — {err}", error=True)
                self.refresh()
        self.bt.set_discoverable(on, done)

    def _rename(self):
        win = self.p.get_toplevel()
        d = Gtk.Dialog(title="이 PC 의 블루투스 이름", transient_for=win if win.is_toplevel() else None,
                       modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "바꾸기", Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        l = Gtk.Label(label="다른 장치에서 이 PC 를 찾을 때 보이는 이름입니다.\n"
                            "비워 두면 기본 이름(컴퓨터 이름)으로 돌아갑니다.", xalign=0)
        l.set_line_wrap(True)
        box.add(l)
        e = Gtk.Entry()
        e.set_text(self.bt.adapter_name)
        e.set_max_length(60)                  # 블루투스 이름은 248바이트까지 — 한글 60자면 넉넉히 들어간다
        e.set_activates_default(True)
        box.add(e)
        d.set_default_response(Gtk.ResponseType.OK)

        def responded(dlg, resp):
            name = e.get_text().strip()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK or name == self.bt.adapter_name:
                return
            self.bt.set_adapter_name(name, lambda ok, err: ok or self.say(
                f"이름을 바꾸지 못했습니다 — {err}", error=True))
        d.connect("response", responded)
        d.show_all()

    # ── 검색 ──
    def _toggle_scan(self):
        if self.scan_on:
            self.stop_scan()
        else:
            self.start_scan()

    def start_scan(self):
        if self.scan_on or self.dead or not self.bt.powered:
            return
        self.say(None)
        self.seen = {}
        self.scan_on = True
        self.scan_src = GLib.timeout_add_seconds(SCAN_SECS, self._scan_timeout)

        def started(ok, err):
            if ok or not self.scan_on:
                return
            self.stop_scan()
            self.say(f"장치를 찾지 못했습니다 — {err}", error=True)
        self.bt.start_discovery(started)
        self.refresh()

    def _scan_timeout(self):
        self.scan_src = 0
        self.stop_scan()
        return False

    def stop_scan(self):
        if self.scan_src:
            GLib.source_remove(self.scan_src)
            self.scan_src = 0
        if not self.scan_on:
            return
        self.scan_on = False
        self.bt.stop_discovery()
        self.refresh()                           # 찾은 목록은 bluez 가 그 장치들을 지울 때까지 남는다

    # ── 장치 일 ──
    def _name(self, path):
        d = self.bt.device(path)
        return d["name"] if d else path

    def _start(self, path, op):
        self.ops[path] = op
        self._set_busy()
        self.refresh()

    def _end(self, path):
        self.ops.pop(path, None)
        if self.dead:
            return False
        self._set_busy()
        self.refresh()
        return True

    def _link(self, path, on):
        if path in self.ops:
            return
        name = self._name(path)
        self.say(None)
        self._start(path, "connect" if on else "disconnect")

        def done(ok, err):
            if not self._end(path):
                return
            if not ok:
                self.say(f"{name} — {'연결하지' if on else '연결을 끊지'} 못했습니다: {err}", error=True)
        (self.bt.connect if on else self.bt.disconnect)(path, done)

    def _remove(self, path):
        if path in self.ops:
            return
        name = self._name(path)
        win = self.p.get_toplevel()
        d = Gtk.MessageDialog(transient_for=win if win.is_toplevel() else None, modal=True,
                              message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
                              text=f"‘{name}’ 장치를 제거할까요?")
        d.format_secondary_text("다시 쓰려면 장치를 짝 맺기 모드로 두고 ‘장치 추가’로 짝을 새로 맺어야 합니다.")
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "제거", Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.CANCEL)

        def responded(dlg, resp):
            dlg.destroy()
            if resp != Gtk.ResponseType.OK or self.dead or path in self.ops:
                return
            self._start(path, "remove")

            def done(ok, err):
                if self._end(path) and not ok:
                    self.say(f"{name} — 제거하지 못했습니다: {err}", error=True)
            self.bt.remove(path, done)
        d.connect("response", responded)
        d.show_all()

    def _cancel_pair(self, path):
        self.declined = path
        self.bt.cancel_pairing(path)

    def pair(self, path):
        """짝 맺기 → 믿는 장치로 → 연결. 암호 확인·입력은 에이전트(_Asker)가 대화상자로 묻는다"""
        if self.pairing is not None or path in self.ops:
            return
        name = self._name(path)
        self.pairing = path
        self.declined = None
        self.say(None)
        # 검색하면서 짝을 맺으면 느려지거나 실패하는 컨트롤러가 있다 — 멈춘다 (찾은 목록은 남는다)
        self.stop_scan()
        self._start(path, "pair")

        def paired(ok, err):
            self.asker.close(path)               # "장치에서 숫자를 입력하세요" 창이 떠 있으면 닫는다
            if not ok:
                self.pairing = None
                if not self._end(path):
                    return
                if self.declined == path:
                    self.say(f"{name} — 짝 맺기를 취소했습니다")
                else:
                    self.say(f"{name} — 짝을 맺지 못했습니다: {err}", error=True)
                return
            # 믿는 장치로 — 다음부터 장치가 먼저 연결해 와도 묻지 않는다 (윈도우와 같다)
            self.bt.set_trusted(path, True)
            self.ops[path] = "connect"
            if not self.dead:
                self.refresh()
            self.bt.connect(path, connected)

        def connected(ok, err):
            self.pairing = None
            if not self._end(path):
                return
            if ok:
                self.say(f"{name} — 연결했습니다")
            elif err == bluetooth.NO_PROFILE:    # 휴대폰 등 — 짝만 맺으면 되는 장치
                self.say(f"{name} — 짝을 맺었습니다")
            else:
                self.say(f"{name} — 짝을 맺었지만 연결하지 못했습니다: {err}", error=True)
        self.bt.pair(path, paired)


def build(store):
    return BluetoothPage(store).widget


PAGES = [{"id": "bluetooth", "title": "블루투스",
          "icon": BT_ICONS,
          "build": build}]
