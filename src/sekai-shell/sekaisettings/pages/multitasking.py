"""멀티태스킹 — 가상 데스크톱과 창 전환 (윈도우 11 의 설정 › 시스템 › 멀티태스킹).

데스크톱 = Hyprland 워크스페이스 1…N. 개수·이름은 sekaishell/desktops.py 가 desktops.json 에
(단축키 Win+Ctrl+D 등으로 작업 표시줄도 바꾸므로 settings.json 과 따로). 추가·제거·이름 바꾸기는
지금 세션에 바로, 다음 로그인에는 Hyprland 조각의 workspace 규칙으로 남는다.
작업 표시줄·Alt+Tab 에 보일 창은 settings.json 의 "multitasking".
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sekaishell import desktops
from sekaishell import keybinds as kb
from ..widgets import Page, button, combo, row

WHICH = [("current", "지금 사용 중인 데스크톱만"), ("all", "모든 데스크톱")]
# 이 페이지에 알려 줄 단축키 (hyprland.conf 의 기본 조합 = 항목 이름)
KEYS = [("SUPER+CTRL+D", "새 데스크톱 만들기"), ("SUPER+CTRL+F4", "지금 데스크톱 닫기"),
        ("SUPER+CTRL+left", "왼쪽 데스크톱으로"), ("SUPER+CTRL+right", "오른쪽 데스크톱으로"),
        ("SUPER+Tab", "작업 보기")]


class MultitaskingPage:
    def __init__(self, store):
        self.store = store
        self.p = Page("멀티태스킹", "가상 데스크톱을 만들고 이름을 붙입니다. 작업 표시줄과 Alt+Tab 에 "
                                  "어느 데스크톱의 창을 보일지도 정합니다.")
        self.live = desktops.hyprland()
        if not self.live:
            self.p.add_widget(_notice("기본 화면 모드에서는 가상 데스크톱을 쓸 수 없습니다. "
                                      "아래 설정은 보통 화면 모드에서 적용됩니다."))
        self.list = self.p.section("가상 데스크톱")
        self.msg = Gtk.Label(xalign=0)
        self.msg.get_style_context().add_class("row-sub")
        self.msg.set_line_wrap(True)
        self.msg.set_no_show_all(True)
        self.p.add_widget(self.msg)

        m = store.get("multitasking")
        s = self.p.section("창 보이기")
        row(s, "작업 표시줄에 표시할 창", "다른 데스크톱의 창도 작업 표시줄에 보일지",
            icon=["view-list", "preferences-system-windows"],
            control=combo(WHICH, m.get("taskbar", "current"),
                          lambda v: store.set("multitasking", "taskbar", v)))
        row(s, "Alt+Tab 을 누르면 보일 창", "Alt+Tab 창 전환기에 어느 데스크톱의 창을 보일지",
            icon=["view-grid", "preferences-system-windows"],
            control=combo(WHICH, m.get("alttab", "current"),
                          lambda v: store.set("multitasking", "alttab", v)))

        self.keys = self.p.section("단축키")
        self.p.connect("map", lambda *_: self.refresh())
        self.refresh()

    @property
    def widget(self):
        return self.p

    def say(self, text):
        self.msg.set_text(text or "")
        self.msg.set_visible(bool(text))

    # ── 목록 ──
    def refresh(self):
        for r in self.list.get_children():
            self.list.remove(r)
        names = desktops.load()
        if self.live:
            live = desktops.Live()
            n = live.count(names)
        else:
            live, n = None, len(names)
        for i in range(1, n + 1):
            wins = (live.ws.get(i) or {}).get("windows", 0) if live else 0
            sub = f"창 {wins}개" if wins else "비어 있음"
            if live and live.active == i:
                sub = "지금 보고 있는 데스크톱 · " + sub
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            ren = button("이름 바꾸기", lambda k=i: self._rename(k))
            rm = button("제거", lambda k=i: self._remove(k))
            rm.set_sensitive(self.live and n > 1)
            rm.set_tooltip_text("이 데스크톱의 창은 왼쪽 데스크톱으로 옮겨집니다 (첫 데스크톱이면 오른쪽)")
            ren.set_sensitive(self.live)
            box.pack_start(ren, False, False, 0)
            box.pack_start(rm, False, False, 0)
            row(self.list, desktops.display(names, i), sub,
                icon=["user-desktop", "desktop", "video-display"], control=box)
        add = button("추가", self._add)
        add.set_sensitive(self.live and n < desktops.MAX)
        row(self.list, "데스크톱 추가", f"빈 데스크톱을 맨 뒤에 만듭니다 (최대 {desktops.MAX}개)",
            icon=["list-add", "window-new"], control=add)
        self.list.show_all()
        self._fill_keys()

    def _fill_keys(self):
        for r in self.keys.get_children():
            self.keys.remove(r)
        eff = {e.id: c for e, c in kb.bindings(self.store.get("keybinds")) if e.custom is None}
        for kid, what in KEYS:
            if kid in eff:
                row(self.keys, what, None, control=_value(kb.pretty(eff[kid]) if eff[kid] else "없음"))
        row(self.keys, "단축키 바꾸기", "설정 › 단축키 에서 바꿀 수 있습니다",
            icon=["preferences-desktop-keyboard-shortcuts", "input-keyboard"],
            control=button("열기", self._open_shortcuts))
        self.keys.show_all()

    def _open_shortcuts(self):
        top = self.p.get_toplevel()
        if hasattr(top, "_select"):
            top._select("shortcuts")

    def _later(self):
        # Hyprland 가 창을 옮기고 워크스페이스를 정리할 틈을 준다
        GLib.timeout_add(200, lambda: self.refresh() and False)

    # ── 동작 ──
    def _add(self):
        text = desktops.new(switch=False)
        self.say(text if isinstance(text, desktops.Note) else f"'{text}' 를 만들었습니다." if text else "")
        self._later()

    def _remove(self, k):
        names = desktops.load()
        name = desktops.display(names, k)
        desktops.remove(k)
        self.say(f"'{name}' 를 닫았습니다. 그 안의 창은 옆 데스크톱으로 옮겼습니다.")
        self._later()

    def _rename(self, k):
        names = desktops.load()
        top = self.p.get_toplevel()
        d = Gtk.Dialog(title="데스크톱 이름 바꾸기", transient_for=top if top.is_toplevel() else None,
                       modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "바꾸기", Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        l = Gtk.Label(label="비워 두면 기본 이름(데스크톱 번호)으로 돌아갑니다. 쉼표는 쓸 수 없습니다.", xalign=0)
        l.set_line_wrap(True)
        box.add(l)
        e = Gtk.Entry()
        e.set_text(desktops.display(names, k))
        e.set_max_length(desktops.NAME_MAX)
        e.set_activates_default(True)
        box.add(e)
        d.set_default_response(Gtk.ResponseType.OK)
        d.show_all()
        r = d.run()
        name = e.get_text()
        d.destroy()
        if r == Gtk.ResponseType.OK:
            desktops.rename(k, name)
            self.say("")
            self._later()


def _value(text):
    l = Gtk.Label(label=text)
    l.get_style_context().add_class("row-value")
    return l


def _notice(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def build(store):
    return MultitaskingPage(store).widget


PAGES = [{"id": "multitasking", "title": "멀티태스킹",
          "icon": ["preferences-system-windows", "view-grid", "window-new"],
          "build": build, "sections": ("multitasking",)}]
