"""접근성 이름 — 글자 없는 단추(아이콘·색 견본·그림)는 화면 읽기(내레이터)에 "단추"라고만 읽힌다.

GTK3 는 툴팁을 접근성 이름으로 쓰지 않는다. 그래서
  · 툴팁을 줄 때 이름이 비어 있으면 같은 글을 이름으로 (set_tooltip_text·markup 을 감싼다)
  · 창이 뜰 때 한 번 훑어 툴팁만 있고 이름이 없는 위젯을 채운다 (감싸기 전에 만든 위젯까지)
install() 은 GTK 앱이 시작할 때 한 번 (theme.apply_contrast_css 가 부른다 — 모든 SekaiOS 앱이 CSS 를 올린 뒤 부르는 곳).
MafuyuMom 의 "이름 없는 단추" 발견이 이것을 잰다.
"""
import re

_installed = False


def _plain(markup):
    return re.sub(r"<[^>]+>", "", markup or "").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def name(widget, text):
    """이름이 비어 있으면 text 로 (이미 있으면 그대로)"""
    try:
        acc = widget.get_accessible()
        if acc is not None and text and not (acc.get_name() or "").strip():
            acc.set_name(text.strip().split("\n")[0][:120])
    except Exception:
        pass


def fill(widget):
    """widget 과 그 아래에서 툴팁만 있고 이름이 없는 것을 채운다"""
    from gi.repository import Gtk
    try:
        tip = widget.get_tooltip_text()
        if tip:
            name(widget, tip)
    except Exception:
        pass
    if isinstance(widget, Gtk.Container):
        try:
            widget.forall(fill)
        except Exception:
            pass


def install():
    global _installed
    if _installed:
        return
    _installed = True
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GObject, Gtk

    orig_text = Gtk.Widget.set_tooltip_text
    orig_markup = Gtk.Widget.set_tooltip_markup

    def set_tooltip_text(self, text):
        orig_text(self, text)
        if text:
            name(self, text)

    def set_tooltip_markup(self, markup):
        orig_markup(self, markup)
        if markup:
            name(self, _plain(markup))

    Gtk.Widget.set_tooltip_text = set_tooltip_text
    Gtk.Widget.set_tooltip_markup = set_tooltip_markup

    def on_map(win, *_):
        fill(win)
        return True
    try:
        GObject.add_emission_hook(Gtk.Window, "map", on_map)
    except Exception:
        pass
