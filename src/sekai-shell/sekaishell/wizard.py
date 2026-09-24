"""설치 화면·OOBE 가 함께 쓰는 단계형 화면 틀.

    ┌──────────────────────────────────────────────┐
    │ ✦ SekaiOS 설치     환영 · 설치 위치 · 확인 …  │  머리 — 로고, 제목, 단계
    │                                              │
    │   (쪽 내용 — Gtk.Stack, 넘길 때 옆으로 밀림)    │
    │                                              │
    │ 도움말 글                       [뒤로] [다음]  │  바닥 — 단추
    └──────────────────────────────────────────────┘

설정 앱과 같은 디자인 체계(settings.css)를 쓰고, 여기만의 모양을 덧붙인다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import logo as sekai_logo

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COLORS = {"accent": "#39c5bb", "bg": "#151517", "surface": "#1e1e22", "fg": "#f1f1f3"}

WIZ_CSS = """
.wizard { background: @winbg; color: @fg; }
.wiz-head { padding: 18px 28px 10px 28px; }
.wiz-brand { font-size: 15px; font-weight: 700; color: @fg; }
.wiz-step { font-size: 12px; color: @text3; padding: 0 2px; }
.wiz-step.done { color: @text2; }
.wiz-step.current { color: @accent; font-weight: 700; }
.wiz-dot { font-size: 12px; color: @text3; }
.wiz-body { padding: 8px 56px 8px 56px; }
.wiz-foot { padding: 14px 28px 20px 28px; border-top: 1px solid @line; }
.wiz-hint { font-size: 12px; color: @text3; }

.wiz-title { font-size: 30px; font-weight: 700; color: @fg; }
.wiz-sub   { font-size: 15px; color: @text2; }
.wiz-big   { font-size: 40px; font-weight: 700; color: @fg; }

button.wiz-next, button.wiz-back { min-width: 96px; min-height: 34px; padding: 4px 18px; }
button.wiz-next {
    background: @accent; border-color: @accent; color: @on_accent; font-weight: 600;
}
button.wiz-next label { color: @on_accent; }
button.wiz-next:hover { background: mix(@accent, #ffffff, 0.12); }
button.wiz-next:disabled { background: alpha(@accent, 0.35); border-color: transparent; }
button.wiz-next:disabled label { color: alpha(@on_accent, 0.6); }
button.wiz-next.danger { background: #c42b1c; border-color: #c42b1c; }
button.wiz-next.danger label { color: #ffffff; }
button.wiz-next.danger:hover { background: #d63a2a; }
button.wiz-next.danger:disabled { background: alpha(#c42b1c, 0.30); border-color: transparent; }
button.wiz-next.danger:disabled label { color: alpha(#ffffff, 0.45); }

/* 요약 표 — 설정 앱의 행보다 촘촘하게 */
.summary row { padding: 9px 16px; min-height: 0; margin-bottom: 2px; }

/* 고르는 카드 (디스크, 언어 등) */
.choice {
    background: @card;
    border: 1px solid @line;
    border-radius: 10px;
    padding: 14px 18px;
}
.choice:hover { background: @hover; }
.choice.selected { border: 2px solid @accent; padding: 13px 17px; background: alpha(@accent, 0.08); }
.choice-title { font-size: 15px; font-weight: 600; color: @fg; }
.choice-sub   { font-size: 12px; color: @text2; }
.badge {
    font-size: 11px; font-weight: 700; border-radius: 999px; padding: 2px 9px;
    background: alpha(@fg, 0.10); color: @text2;
}
.badge.warn   { background: alpha(#ff6b81, 0.18); color: #ff9aa8; }
.badge.ok     { background: alpha(@accent, 0.18); color: @accent; }

.notice.warn { background: alpha(#ff6b81, 0.10); border-color: alpha(#ff6b81, 0.40); }
.notice.warn label { color: #ffd0d8; }

/* 파티션 계획 막대 */
.plan-seg { border-radius: 6px; padding: 8px 10px; }
.plan-seg label { font-size: 12px; color: #0f1f1e; font-weight: 600; }
.plan-seg.esp  { background: #f0c66e; }
.plan-seg.swap { background: #9aa4ff; }
.plan-seg.root { background: @accent; }
.plan-legend { font-size: 12px; color: @text2; }

/* 설치 중 */
progressbar.wiz-progress trough {
    background: alpha(@fg, 0.12); border: none; border-radius: 999px; min-height: 6px;
}
progressbar.wiz-progress progress {
    background: @accent; border: none; border-radius: 999px; min-height: 6px;
}
.wiz-percent { font-size: 56px; font-weight: 300; color: @fg; font-feature-settings: "tnum"; }
.tip-card {
    background: @card; border: 1px solid @line; border-radius: 12px; padding: 18px 22px;
}
.tip-title { font-size: 14px; font-weight: 700; color: @fg; }
.tip-body  { font-size: 13px; color: @text2; }
"""


def load_css(extra="", colors=None):
    c = dict(DEFAULT_COLORS, **(colors or {}))
    body = ""
    for p in (os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    pre = "".join(f"@define-color {k} {v};\n" for k, v in c.items())
    prov = Gtk.CssProvider()
    prov.load_from_data((pre + body + WIZ_CSS + extra).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_USER)
    st = Gtk.Settings.get_default()
    if st:
        st.set_property("gtk-application-prefer-dark-theme", True)
        if os.path.isdir("/usr/share/icons/Papirus-Dark"):
            st.set_property("gtk-icon-theme-name", "Papirus-Dark")
    return prov


def label(text, cls=None, xalign=0.0, wrap=False, markup=False):
    lb = Gtk.Label(xalign=xalign)
    (lb.set_markup if markup else lb.set_text)(text)
    if cls:
        for c in cls.split():
            lb.get_style_context().add_class(c)
    if xalign == 0.5:
        lb.set_justify(Gtk.Justification.CENTER)       # 여러 줄일 때도 가운데로
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_max_width_chars(70)
    return lb


def icon(names, size):
    theme = Gtk.IconTheme.get_default()
    if isinstance(names, str):
        names = [names]
    for n in names:
        if theme.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.DIALOG)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    return img


def logo_widget(size):
    d = Gtk.DrawingArea()
    d.set_size_request(size, size)
    d.connect("draw", lambda _w, cr: (sekai_logo.draw(cr, size), False)[1])
    return d


class ChoiceList(Gtk.Box):
    """카드 여러 개 중 하나 고르기. on_select(key)"""

    def __init__(self, on_select=None, spacing=8):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.on_select = on_select
        self.cards = {}
        self.selected = None

    def add(self, key, content, sensitive=True):
        ev = Gtk.EventBox()
        box = Gtk.Box()
        box.get_style_context().add_class("choice")
        box.pack_start(content, True, True, 0)
        ev.add(box)
        ev.set_sensitive(sensitive)
        ev.connect("button-press-event", lambda *_: (self.select(key), True)[1])
        ev.connect("enter-notify-event", lambda w, _e: w.get_window() and w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
        self.cards[key] = box
        self.pack_start(ev, False, False, 0)
        return box

    def clear(self):
        for c in self.get_children():
            self.remove(c)
        self.cards = {}
        self.selected = None

    def select(self, key):
        self.selected = key
        for k, b in self.cards.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)("selected")
        if self.on_select:
            self.on_select(key)


class Wizard(Gtk.Box):
    """steps: [(쪽 id, 단계 이름 또는 None)]  — None 이면 단계 표시에 안 나온다."""

    def __init__(self, brand, steps):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("wizard")
        self.steps = steps
        self.pages = {}
        self.on_next = None
        self.on_back = None

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.get_style_context().add_class("wiz-head")
        lg = logo_widget(22)
        lg.set_valign(Gtk.Align.CENTER)
        head.pack_start(lg, False, False, 0)
        head.pack_start(label(brand, "wiz-brand"), False, False, 0)
        self.step_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.pack_end(self.step_box, False, False, 0)
        self.step_labels = {}
        names = [(pid, n) for pid, n in steps if n]
        for i, (pid, n) in enumerate(names):
            if i:
                self.step_box.pack_start(label("·", "wiz-dot"), False, False, 0)
            lb = label(n, "wiz-step")
            self.step_labels[pid] = lb
            self.step_box.pack_start(lb, False, False, 0)
        self.pack_start(head, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(220)
        self.stack.get_style_context().add_class("wiz-body")
        self.pack_start(self.stack, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.get_style_context().add_class("wiz-foot")
        self.hint = label("", "wiz-hint")
        self.hint.set_ellipsize(Pango.EllipsizeMode.END)
        foot.pack_start(self.hint, True, True, 0)
        self.back_btn = Gtk.Button(label="뒤로")
        self.back_btn.get_style_context().add_class("wiz-back")
        self.back_btn.connect("clicked", lambda *_: self.on_back and self.on_back())
        self.next_btn = Gtk.Button(label="다음")
        self.next_btn.get_style_context().add_class("wiz-next")
        self.next_btn.connect("clicked", lambda *_: self.on_next and self.on_next())
        foot.pack_end(self.next_btn, False, False, 0)
        foot.pack_end(self.back_btn, False, False, 0)
        self.foot = foot
        self.pack_start(foot, False, False, 0)

    def add_page(self, pid, widget):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(widget)
        self.pages[pid] = sw
        self.stack.add_named(sw, pid)

    def show_page(self, pid, back=None, next_label="다음", next_enabled=True,
                  danger=False, hint="", footer=True, forward=True):
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT if forward
                                       else Gtk.StackTransitionType.SLIDE_RIGHT)
        self.stack.set_visible_child_name(pid)
        # 단계 표시 — 지금 단계 강조, 지난 단계는 조금 밝게
        order = [p for p, n in self.steps]
        cur = order.index(pid) if pid in order else -1
        for p, lb in self.step_labels.items():
            ctx = lb.get_style_context()
            i = order.index(p)
            for c in ("current", "done"):
                ctx.remove_class(c)
            if i == cur:
                ctx.add_class("current")
            elif i < cur:
                ctx.add_class("done")
        self.back_btn.set_visible(back is not None)
        self.on_back = back
        self.next_btn.set_label(next_label or "")
        self.next_btn.set_visible(bool(next_label))
        self.next_btn.set_sensitive(next_enabled)
        ctx = self.next_btn.get_style_context()
        (ctx.add_class if danger else ctx.remove_class)("danger")
        self.hint.set_text(hint)
        self.foot.set_visible(footer)
        if next_label and next_enabled:
            GLib.idle_add(self.next_btn.grab_focus)

    def set_next_enabled(self, on):
        self.next_btn.set_sensitive(on)


def page_box(title=None, subtitle=None, spacing=14):
    """쪽 하나의 기본 상자 — 제목, 부제, 그 아래 내용"""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.set_margin_top(18)
    box.set_margin_bottom(18)
    if title:
        box.pack_start(label(title, "wiz-title", wrap=True), False, False, 0)
    if subtitle:
        box.pack_start(label(subtitle, "wiz-sub", wrap=True), False, False, 0)
    return box
