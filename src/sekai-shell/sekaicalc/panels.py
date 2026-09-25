"""계산기 — 기록·메모리 목록 (창이 넓으면 오른쪽 칸, 좁으면 자판 위로 올라오는 칸).

같은 목록이 두 곳(오른쪽 칸 · 올라오는 칸)에 있으므로 목록은 모델(창의 history · memory)을 그리기만 하고,
바뀔 때마다 창이 둘 다 refresh() 한다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from .engine import fmt  # noqa: E402


def icon(names, size=16):
    th = Gtk.IconTheme.get_default()
    for n in names:
        if th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name(names[-1], Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def flat_button(child, tooltip=None, cb=None, css="c-flat"):
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


class _List(Gtk.Box):
    """목록 + 비었을 때의 글 + 오른쪽 아래 휴지통 단추"""

    EMPTY = ""
    CLEAR_TIP = ""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.stack = Gtk.Stack()
        self.empty = Gtk.Label(label=self.EMPTY, xalign=0, yalign=0)
        self.empty.set_line_wrap(True)
        self.empty.get_style_context().add_class("c-empty")
        self.stack.add_named(self.empty, "empty")
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("c-list")
        self.listbox.connect("row-activated", lambda _l, row: self.activated(row))
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.listbox)
        self.stack.add_named(sc, "list")
        self.pack_start(self.stack, True, True, 0)
        bar = Gtk.Box()
        self.trash = flat_button(icon(["user-trash-symbolic", "edit-delete-symbolic"], 16), self.CLEAR_TIP,
                                 self.clear_all)
        bar.pack_end(self.trash, False, False, 0)
        bar.get_style_context().add_class("c-listbar")
        self.pack_end(bar, False, False, 0)

    def set_items(self, rows):
        for c in self.listbox.get_children():
            c.destroy()
        for r in rows:
            self.listbox.add(r)
        self.listbox.show_all()
        self.stack.set_visible_child_name("list" if rows else "empty")
        self.trash.set_visible(bool(rows))


class HistoryList(_List):
    EMPTY = "아직 기록이 없습니다"
    CLEAR_TIP = "기록 지우기 (Ctrl+Shift+D)"

    def refresh(self):
        rows = []
        for i, (expr, val) in enumerate(reversed(self.win.history)):
            row = Gtk.ListBoxRow()
            row.index = len(self.win.history) - 1 - i
            row.get_style_context().add_class("c-item")
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            e = Gtk.Label(label=expr, xalign=1)
            e.set_line_wrap(True)
            e.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            e.get_style_context().add_class("c-item-expr")
            r = Gtk.Label(label=fmt(val), xalign=1)
            r.set_line_wrap(True)
            r.set_line_wrap_mode(Pango.WrapMode.CHAR)
            r.get_style_context().add_class("c-item-value")
            v.pack_start(e, False, False, 0)
            v.pack_start(r, False, False, 0)
            row.add(v)
            row.set_tooltip_text("누르면 이 계산으로 돌아갑니다")
            rows.append(row)
        self.set_items(rows)

    def activated(self, row):
        self.win.restore_history(row.index)

    def clear_all(self):
        self.win.clear_history()


class MemoryList(_List):
    EMPTY = "메모리에 저장된 항목이 없습니다"
    CLEAR_TIP = "메모리 지우기"

    def refresh(self):
        rows = []
        for i, val in enumerate(self.win.memory):
            row = Gtk.ListBoxRow()
            row.index = i
            row.get_style_context().add_class("c-item")
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            r = Gtk.Label(label=fmt(val), xalign=1)
            r.set_line_wrap(True)
            r.set_line_wrap_mode(Pango.WrapMode.CHAR)
            r.get_style_context().add_class("c-item-value")
            v.pack_start(r, False, False, 0)
            # 항목마다 MC · M+ · M- (마우스를 올리면 보인다 — 윈도우처럼)
            btns = Gtk.Box(spacing=4)
            btns.set_halign(Gtk.Align.END)
            btns.get_style_context().add_class("c-mem-btns")
            for label, op, tip in (("MC", "mc", "이 항목 지우기"), ("M+", "m+", "이 항목에 더하기"),
                                   ("M-", "m-", "이 항목에서 빼기")):
                b = flat_button(label, tip, lambda op=op, i=i: self.win.memory_item(op, i), css="c-mem-item")
                btns.pack_start(b, False, False, 0)
            v.pack_start(btns, False, False, 0)
            row.add(v)
            rows.append(row)
        self.set_items(rows)

    def activated(self, row):
        self.win.memory_item("mr", row.index)

    def clear_all(self):
        self.win.memory_op("mc")


class SidePanel(Gtk.Box):
    """넓은 창의 오른쪽 칸 — 기록 | 메모리"""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_style_context().add_class("c-side")
        self.set_size_request(290, -1)
        tabs = Gtk.Box(spacing=4)
        tabs.get_style_context().add_class("c-tabs")
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.history = HistoryList(win)
        self.memory = MemoryList(win)
        self.stack.add_named(self.history, "history")
        self.stack.add_named(self.memory, "memory")
        self.tab_btns = {}
        for name, label in (("history", "기록"), ("memory", "메모리")):
            b = flat_button(label, None, lambda n=name: self.show(n), css="c-tab")
            tabs.pack_start(b, False, False, 0)
            self.tab_btns[name] = b
        self.pack_start(tabs, False, False, 0)
        self.pack_start(self.stack, True, True, 0)
        self.show("history")

    def show(self, name):
        self.stack.set_visible_child_name(name)
        for n, b in self.tab_btns.items():
            ctx = b.get_style_context()
            if n == name:
                ctx.add_class("on")
            else:
                ctx.remove_class("on")

    def refresh(self):
        self.history.refresh()
        self.memory.refresh()


class NavPane(Gtk.Box):
    """☰ 로 여는 왼쪽 칸 — 계산기 종류"""

    MODES = [("standard", "표준", ["accessories-calculator-symbolic", "accessories-calculator"]),
             ("scientific", "공학용", ["applications-science-symbolic", "accessories-calculator-symbolic",
                                     "accessories-calculator"])]

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.get_style_context().add_class("c-nav")
        self.set_size_request(250, -1)
        head = Gtk.Box()
        head.pack_start(flat_button(icon(["open-menu-symbolic", "view-more-symbolic"], 16), "탐색 닫기",
                                    win.toggle_nav), False, False, 0)
        self.pack_start(head, False, False, 0)
        cap = Gtk.Label(label="계산기", xalign=0)
        cap.get_style_context().add_class("c-nav-cap")
        self.pack_start(cap, False, False, 0)
        self.rows = {}
        for mode, label, icons in self.MODES:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_can_focus(False)
            b.get_style_context().add_class("c-nav-row")
            h = Gtk.Box(spacing=12)
            h.pack_start(icon(icons, 16), False, False, 0)
            h.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            b.add(h)
            b.connect("clicked", lambda _b, m=mode: win.set_mode(m))
            b.set_tooltip_text(f"{label} (Alt+{1 if mode == 'standard' else 2})")
            self.pack_start(b, False, False, 0)
            self.rows[mode] = b

    def sync(self, mode):
        for m, b in self.rows.items():
            ctx = b.get_style_context()
            if m == mode:
                ctx.add_class("on")
            else:
                ctx.remove_class("on")


def key_is(ev, *names):
    return ev.keyval in tuple(getattr(Gdk, "KEY_" + n) for n in names)
