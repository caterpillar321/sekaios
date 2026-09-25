"""메모장 — 설정 페이지 (⚙). 윈도우 11 메모장처럼 창 안에서 글 대신 보이고 ← 로 돌아간다.

바꾸면 곧바로 앱의 모든 창에 적용되고 ~/.local/state/sekai/notepad.json 에 남는다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .common import APP_NAME, font_css, font_label, icon, system_mono_font  # noqa: E402

PREVIEW = "다람쥐 헌 쳇바퀴에 타고파. The quick brown fox jumps over the lazy dog. 0123456789"


def _row(title, sub, control):
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    row.set_selectable(False)
    h = Gtk.Box(spacing=16)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    v.set_valign(Gtk.Align.CENTER)
    t = Gtk.Label(label=title, xalign=0)
    t.get_style_context().add_class("row-title")
    v.pack_start(t, False, False, 0)
    s = None
    if sub is not None:
        s = Gtk.Label(label=sub, xalign=0)
        s.set_line_wrap(True)
        s.get_style_context().add_class("row-sub")
        v.pack_start(s, False, False, 0)
    h.pack_start(v, True, True, 0)
    if control is not None:
        control.set_valign(Gtk.Align.CENTER)
        h.pack_end(control, False, False, 0)
    row.add(h)
    row.sub = s
    return row


def _section(title, rows):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    lbl = Gtk.Label(label=title, xalign=0)
    lbl.get_style_context().add_class("section-title")
    box.pack_start(lbl, False, False, 0)
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    lb.get_style_context().add_class("section")
    for r in rows:
        lb.add(r)
    box.pack_start(lb, False, False, 0)
    return box


class SettingsPage(Gtk.ScrolledWindow):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.app = win.app
        self._sync = False
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.get_style_context().add_class("np-settings")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("page")
        clamp = Gtk.Box()
        clamp.set_halign(Gtk.Align.CENTER)
        outer.set_size_request(560, -1)
        clamp.pack_start(outer, True, True, 0)
        self.add(clamp)

        head = Gtk.Box(spacing=12)
        back = Gtk.Button()
        back.add(icon(["go-previous-symbolic"], 16))
        back.set_relief(Gtk.ReliefStyle.NONE)
        back.get_style_context().add_class("np-flat")
        back.set_tooltip_text("뒤로 (Esc)")
        back.connect("clicked", lambda *_: win.show_editor())
        head.pack_start(back, False, False, 0)
        title = Gtk.Label(label="설정", xalign=0)
        title.get_style_context().add_class("page-title")
        head.pack_start(title, False, False, 0)
        outer.pack_start(head, False, False, 0)

        # 글꼴
        fbtns = Gtk.Box(spacing=6)
        self.btn_reset = Gtk.Button(label="기본값")
        self.btn_reset.connect("clicked", lambda *_: self.app.set_pref("font", None))
        fbtns.pack_start(self.btn_reset, False, False, 0)
        change = Gtk.Button(label="변경…")
        change.connect("clicked", lambda *_: self._choose_font())
        fbtns.pack_start(change, False, False, 0)
        self.font_row = _row("글꼴", "", fbtns)
        prev_row = Gtk.ListBoxRow()
        prev_row.set_activatable(False)
        prev_row.set_selectable(False)
        self.preview = Gtk.Label(label=PREVIEW, xalign=0)
        self.preview.set_line_wrap(True)
        self.preview.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.preview.get_style_context().add_class("np-preview")
        self._preview_css = Gtk.CssProvider()
        self.preview.get_style_context().add_provider(self._preview_css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        prev_row.add(self.preview)

        # 텍스트 서식
        self.sw_wrap = Gtk.Switch()
        self.sw_wrap.connect("notify::active", lambda s, _p: self._pref("wrap", s.get_active()))
        wrap_row = _row("자동 줄 바꿈", "창 너비에 맞춰 긴 줄을 다음 줄로 넘겨 보여 줍니다", self.sw_wrap)
        self.sw_status = Gtk.Switch()
        self.sw_status.connect("notify::active", lambda s, _p: self._pref("statusbar", s.get_active()))
        status_row = _row("상태 표시줄", "창 아래에 줄·열 위치, 확대 비율, 줄 끝, 인코딩을 표시합니다", self.sw_status)

        # 파일 열기
        self.cb_open = Gtk.ComboBoxText()
        self.cb_open.append("tab", "기존 창의 새 탭에서 열기")
        self.cb_open.append("window", "새 창에서 열기")
        self.cb_open.connect("changed", lambda c: self._pref("open_mode", c.get_active_id()))
        open_row = _row("파일 열기", "메모장이 이미 열려 있을 때 다른 앱에서 연 파일", self.cb_open)

        about = _row(APP_NAME, "SekaiOS 메모장", None)

        outer.pack_start(_section("글꼴", [self.font_row, prev_row]), False, False, 0)
        outer.pack_start(_section("텍스트 서식", [wrap_row, status_row]), False, False, 0)
        outer.pack_start(_section("메모장 열기", [open_row]), False, False, 0)
        outer.pack_start(_section("이 앱 정보", [about]), False, False, 0)
        self.connect("key-press-event", self._on_key)
        self.refresh()

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.win.show_editor()
            return True
        return False

    def _pref(self, key, value):
        if not self._sync and value is not None:
            self.app.set_pref(key, value)

    def refresh(self):
        """앱 설정 → 위젯 (다른 창에서 바꿨거나 시스템 글꼴이 바뀌었을 때도)"""
        self._sync = True
        try:
            custom = self.app.prefs.get("font")
            font = custom or system_mono_font()
            self.font_row.sub.set_text(font_label(font) + ("" if custom else " (시스템 기본값)"))
            self.btn_reset.set_sensitive(bool(custom))
            css = font_css(font, 100).replace("textview.np-text, textview.np-text text", "label.np-preview")
            try:
                self._preview_css.load_from_data(css.encode())
            except GLib.Error:
                pass
            self.sw_wrap.set_active(bool(self.app.prefs.get("wrap", True)))
            self.sw_status.set_active(bool(self.app.prefs.get("statusbar", True)))
            self.cb_open.set_active_id(self.app.prefs.get("open_mode") or "tab")
        finally:
            self._sync = False

    def _choose_font(self):
        d = Gtk.FontChooserDialog(title="글꼴", transient_for=self.win, modal=True)
        d.set_font(self.app.prefs.get("font") or system_mono_font())
        d.set_preview_text(PREVIEW)

        def resp(dlg, r):
            font = dlg.get_font() if r == Gtk.ResponseType.OK else None
            dlg.destroy()
            if font:
                self.app.set_pref("font", font)
        d.connect("response", resp)
        d.show()
