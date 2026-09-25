"""SekaiOS 설정 — 재사용 위젯.

윈도우 10/11 설정처럼 "한 줄 = 한 항목" 형태의 행(row)을 만든다.
   [아이콘]  제목                              [컨트롤]
             설명
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, GdkPixbuf, Gdk  # noqa: E402


def icon_image(names, size=16):
    """후보 이름들을 순서대로 시도해 첫 번째로 찾아지는 아이콘을 쓴다."""
    theme = Gtk.IconTheme.get_default()
    if isinstance(names, str):
        names = [names]
    for n in names:
        if not n:
            continue
        if os.path.isabs(n) and os.path.exists(n):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(n, size, size)
                return Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                continue
        if theme.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


class Page(Gtk.ScrolledWindow):
    """설정 한 페이지. 제목 + 섹션들."""

    # 오래 걸리는 작업(관리자 권한 설치 등)이 도는 동안 True — 설정 창이 이 페이지를 다시 그리지(없애지) 않는다.
    #   없애면 진행 상태와 한 번만 나오는 결과(MOK 비밀번호 등)를 잃고, 새 페이지에서 같은 작업을 또 띄울 수 있다
    busy = False

    def __init__(self, title, subtitle=None):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.get_style_context().add_class("page")
        self.add(self.box)

        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("page-title")
        self.box.pack_start(head, False, False, 0)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.get_style_context().add_class("page-sub")
            sub.set_line_wrap(True)
            self.box.pack_start(sub, False, False, 0)

    def section(self, title=None):
        """섹션을 만들고, 그 안에 행을 담을 리스트박스를 돌려준다."""
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("section-title")
            self.box.pack_start(lbl, False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        self.box.pack_start(lb, False, False, 0)
        return lb

    def add_widget(self, w):
        self.box.pack_start(w, False, False, 0)
        return w


def row(listbox, title, subtitle=None, icon=None, control=None, activatable=False):
    """항목 한 줄을 만들어 리스트박스에 붙이고, 그 Row 를 돌려준다."""
    r = Gtk.ListBoxRow()
    r.set_activatable(activatable)
    r.get_style_context().add_class("row")

    h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    if icon:
        img = icon_image(icon, 20)
        img.set_valign(Gtk.Align.CENTER)
        h.pack_start(img, False, False, 0)

    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    v.set_valign(Gtk.Align.CENTER)
    t = Gtk.Label(label=title, xalign=0)
    t.get_style_context().add_class("row-title")
    v.pack_start(t, False, False, 0)
    r.title_label = t
    if subtitle:
        s = Gtk.Label(label=subtitle, xalign=0)
        s.get_style_context().add_class("row-sub")
        s.set_line_wrap(True)
        s.set_max_width_chars(60)
        v.pack_start(s, False, False, 0)
        r.sub_label = s
    h.pack_start(v, True, True, 0)

    if control is not None:
        control.set_valign(Gtk.Align.CENTER)
        h.pack_end(control, False, False, 0)
        r.control = control

    r.add(h)
    listbox.add(r)
    return r


# ── 컨트롤 팩토리 ───────────────────────────────────────────
def switch(value, on_change):
    sw = Gtk.Switch()
    sw.set_active(bool(value))
    sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    return sw


def spin(value, lo, hi, step=1, on_change=None, digits=0):
    sb = Gtk.SpinButton.new_with_range(lo, hi, step)
    sb.set_digits(digits)
    sb.set_value(value)
    sb.set_width_chars(6)
    if on_change:
        sb.connect("value-changed",
                   lambda s: on_change(s.get_value() if digits else int(s.get_value())))
    return sb


def slider(value, lo, hi, step=1, on_change=None, digits=0, width=220):
    sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    sc.set_digits(digits)
    sc.set_value(value)
    sc.set_size_request(width, -1)
    sc.set_value_pos(Gtk.PositionType.RIGHT)
    if on_change:
        # 끄는 동안 값이 수십 번 바뀐다 — 매번 저장·적용(파일 쓰기, hyprctl)하면 버벅인다.
        #   멈춘 뒤 150ms 에 한 번만
        pending = {"src": 0}

        def fire():
            pending["src"] = 0
            on_change(sc.get_value() if digits else int(sc.get_value()))
            return False

        def changed(_s):
            if pending["src"]:
                GLib.source_remove(pending["src"])
            pending["src"] = GLib.timeout_add(150, fire)
        sc.connect("value-changed", changed)
    return sc


def combo(items, active=None, on_change=None):
    """items = [(id, 표시이름), ...]
    지금 값이 목록에 없으면(지운 앱, 손으로 고친 설정 등) 첫 항목이 아니라 그 값을 그대로 보인다 —
    예전엔 첫 항목이 골라진 것처럼 보여 실제 설정과 화면이 달랐다. 목록이 비면 "없음"을 흐리게."""
    c = Gtk.ComboBoxText()
    for i, (key, label) in enumerate(items):
        c.append(str(key), label)
    if active is not None and str(active) != "" and not c.set_active_id(str(active)):
        c.append(str(active), f"{active} (지금 값)")
        c.set_active_id(str(active))
    if c.get_active() < 0:
        if items:
            c.set_active(0)
        else:
            c.append("", "없음")
            c.set_active(0)
            c.set_sensitive(False)
    if on_change:
        c.connect("changed", lambda w: on_change(w.get_active_id()))
    return c


def entry(text, on_change=None, width=20, placeholder=None):
    e = Gtk.Entry()
    e.set_text(str(text or ""))
    e.set_width_chars(width)
    if placeholder:
        e.set_placeholder_text(placeholder)
    if on_change:
        # 입력 중마다 적용하면 요란하다. 포커스가 빠지거나 엔터일 때만.
        e.connect("activate", lambda w: on_change(w.get_text()))
        e.connect("focus-out-event", lambda w, _e: (on_change(w.get_text()), False)[1])
    return e


def color_button(hexstr, on_change=None):
    rgba = Gdk.RGBA()
    rgba.parse(hexstr)
    b = Gtk.ColorButton.new_with_rgba(rgba)
    b.set_use_alpha(False)
    if on_change:
        def changed(w):
            c = w.get_rgba()
            on_change("#%02x%02x%02x" % (int(c.red * 255 + .5),
                                         int(c.green * 255 + .5),
                                         int(c.blue * 255 + .5)))
        b.connect("color-set", changed)
    return b


def button(label, on_click=None, cls="row-btn"):
    b = Gtk.Button(label=label)
    b.get_style_context().add_class(cls)
    if on_click:
        b.connect("clicked", lambda *_: on_click())
    return b


def info(text):
    """오른쪽에 회색으로 값만 보여주는 라벨."""
    l = Gtk.Label(label=str(text))
    l.get_style_context().add_class("row-value")
    l.set_selectable(True)
    return l
