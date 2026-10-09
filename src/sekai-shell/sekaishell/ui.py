"""SekaiOS 공용 GTK 도우미 — 셸·설정 앱·다른 앱이 함께 쓰는 작은 위젯 (설정 앱의 widgets 가 다시 내보낸다)."""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402


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


def icon_image(names, size=16, fallback="application-x-executable"):
    """후보(아이콘 이름 또는 그림 파일 경로) 중 처음 있는 것 — 하나도 없으면 fallback
    (fallback=None 이면 마지막 후보 이름 그대로: 기호 아이콘이 테마에 없어도 Adwaita 가 그린다)"""
    names = [names] if isinstance(names, str) else list(names or [])
    th = Gtk.IconTheme.get_default()
    for n in names:
        if not n:
            continue
        if os.path.isabs(n) and os.path.isfile(n):
            try:
                return Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(n, size, size))
            except GLib.Error:
                continue
        if th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name(fallback or (names[-1] if names else "image-missing"), Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def text_label(text="", cls=None, wrap=True, selectable=False, max_chars=60):
    """왼쪽 맞춤 글 — 길면 글자 단위로도 줄을 바꾼다 (한글 긴 낱말이 창을 넓히지 않게)"""
    lb = Gtk.Label(label=text, xalign=0)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_max_width_chars(max_chars)
    lb.set_selectable(selectable)
    if cls:
        lb.get_style_context().add_class(cls)
    return lb


def text_entry(text="", width=28, placeholder=None, max_len=0):
    """한 줄 입력 칸"""
    e = Gtk.Entry()
    e.set_text(text or "")
    e.set_width_chars(width)
    if max_len:
        e.set_max_length(max_len)
    if placeholder:
        e.set_placeholder_text(placeholder)
    return e


def flat_button(child, tooltip=None, cb=None, css="flat"):
    """테두리 없는 단추 — child 는 글이나 위젯. 초점을 받지 않는다 (도구 막대·계산기 단추처럼 눌러도 초점이 남게)"""
    b = Gtk.Button()
    if isinstance(child, str):
        b.set_label(child)
    else:
        b.add(child)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.set_can_focus(False)
    b.get_style_context().add_class(css)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if cb:
        b.connect("clicked", lambda *_: cb())
    return b
