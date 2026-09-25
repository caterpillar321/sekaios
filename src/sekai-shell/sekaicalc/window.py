"""계산기 — 창 (☰ · 모드 이름 · 식 줄 · 결과 · 메모리 줄 · 자판 · 기록/메모리 칸).

자판 단추는 표(KEYS·STD_ROWS·SCI_ROWS)에서 만든다. 단추와 키보드가 같은 동작 이름(press("op:+"))을 쓴다.
창이 넓으면(WIDE) 오른쪽에 기록 | 메모리 칸, 좁으면 위 오른쪽 시계 단추·M▾ 로 자판 위에 올라오는 칸.
☰ 는 왼쪽에서 나오는 칸(표준 · 공학용). 올라오는 칸·왼쪽 칸은 창 전체를 덮는 Gtk.Overlay 에 둔다.
"""
from decimal import Decimal

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import engine as eng  # noqa: E402
from .panels import HistoryList, MemoryList, NavPane, SidePanel, flat_button, icon  # noqa: E402

TITLE = "계산기"
MODE_NAMES = {"standard": "표준", "scientific": "공학용"}
WIDE = 620                  # 이보다 넓으면 기록 칸을 오른쪽에 늘 보인다
HISTORY_MAX = 200
DEFAULT_SIZES = {"standard": [330, 520], "scientific": [400, 620]}

# 단추 — id: (글(마크업), 모양, 동작, 풍선 도움말)
#   모양: num(숫자 — 밝은 카드) · fn(기능·연산자) · eq(= — 강조색)
KEYS = {
    "pct": ("%", "fn", "pct", "퍼센트 (%)"),
    "ce": ("CE", "fn", "ce", "입력 지우기 (Delete)"),
    "c": ("C", "fn", "c", "모두 지우기 (Esc)"),
    "bs": ("⌫", "fn", "bs", "백스페이스 (Backspace)"),
    "recip": ("<sup>1</sup>/<sub>x</sub>", "fn", "un:recip", "역수 (R)"),
    "sqr": ("x<sup>2</sup>", "fn", "un:sqr", "제곱 (Q)"),
    "sqrt": ("<sup>2</sup>√x", "fn", "un:sqrt", "제곱근 (@)"),
    "div": ("÷", "fn", "op:/", "나누기 (/)"),
    "mul": ("×", "fn", "op:*", "곱하기 (*)"),
    "sub": ("−", "fn", "op:-", "빼기 (-)"),
    "add": ("+", "fn", "op:+", "더하기 (+)"),
    "neg": ("<sup>+</sup>/<sub>−</sub>", "num", "neg", "양수/음수 (F9)"),
    "point": (".", "num", "point", "소수점 (.)"),
    "eq": ("=", "eq", "eq", "계산 (Enter)"),
    "2nd": ("2<sup>nd</sup>", "fn", "2nd", "두 번째 기능"),
    "pi": ("π", "fn", "const:pi", "파이 (P)"),
    "e": ("e", "fn", "const:e", "자연로그의 밑 (E)"),
    "abs": ("|x|", "fn", "un:abs", "절댓값"),
    "exp": ("exp", "fn", "exp", "지수 입력 — 1.5 exp 3 = 1,500 (X)"),
    "mod": ("mod", "fn", "op:mod", "나머지 (%)"),
    "lp": ("(", "fn", "lp", "여는 괄호 (()"),
    "rp": (")", "fn", "rp", "닫는 괄호 ())"),
    "fact": ("n!", "fn", "un:fact", "계승 (!)"),
    "pow": ("x<sup>y</sup>", "fn", "op:pow", "거듭제곱 (^)"),
    "pow10": ("10<sup>x</sup>", "fn", "un:pow10", "10의 거듭제곱"),
    "log": ("log", "fn", "un:log", "상용로그 (L)"),
    "ln": ("ln", "fn", "un:ln", "자연로그 (N)"),
}
for _d in "0123456789":
    KEYS["d" + _d] = (_d, "num", "d:" + _d, None)

# 2nd 를 누르면 바뀌는 단추
SECOND = {
    "sqr": ("x<sup>3</sup>", "un:cube", "세제곱 (#)"),
    "sqrt": ("<sup>3</sup>√x", "un:cbrt", "세제곱근"),
    "pow": ("<sup>y</sup>√x", "op:root", "y제곱근"),
    "pow10": ("2<sup>x</sup>", "un:pow2", "2의 거듭제곱"),
    "log": ("log<sub>y</sub>x", "op:logy", "밑이 y인 로그"),
    "ln": ("e<sup>x</sup>", "un:exp", "e의 거듭제곱"),
}

STD_ROWS = [["pct", "ce", "c", "bs"],
            ["recip", "sqr", "sqrt", "div"],
            ["d7", "d8", "d9", "mul"],
            ["d4", "d5", "d6", "sub"],
            ["d1", "d2", "d3", "add"],
            ["neg", "d0", "point", "eq"]]
SCI_ROWS = [["2nd", "pi", "e", "c", "bs"],
            ["sqr", "recip", "abs", "exp", "mod"],
            ["sqrt", "lp", "rp", "fact", "div"],
            ["pow", "d7", "d8", "d9", "mul"],
            ["pow10", "d4", "d5", "d6", "sub"],
            ["log", "d1", "d2", "d3", "add"],
            ["ln", "neg", "d0", "point", "eq"]]

# 오류 뒤에도 되는 단추 (윈도우처럼 숫자 · C · CE · ⌫ 만)
AFTER_ERROR = {"c", "ce", "bs", "point"} | {"d" + d for d in "0123456789"}

# 키보드 — 키 이름 → 동작 (Ctrl·Alt 없이; Shift 는 상관없다 — '+' 는 Shift+= 로 온다)
_KEYS_ANY = {
    "period": "point", "comma": "point", "KP_Decimal": "point", "KP_Separator": "point",
    "plus": "op:+", "KP_Add": "op:+", "minus": "op:-", "KP_Subtract": "op:-",
    "asterisk": "op:*", "KP_Multiply": "op:*", "slash": "op:/", "KP_Divide": "op:/",
    "Return": "eq", "KP_Enter": "eq", "ISO_Enter": "eq", "equal": "eq",
    "Escape": "c", "Delete": "ce", "KP_Delete": "ce", "BackSpace": "bs",
    "F9": "neg", "r": "un:recip", "R": "un:recip", "at": "un:sqrt", "q": "un:sqr", "Q": "un:sqr",
}
_KEYS_STD = {"percent": "pct"}
_KEYS_SCI = {
    "percent": "op:mod", "parenleft": "lp", "parenright": "rp", "s": "un:sin", "o": "un:cos", "t": "un:tan",
    "l": "un:log", "n": "un:ln", "exclam": "un:fact", "asciicircum": "op:pow", "y": "op:pow",
    "p": "const:pi", "P": "const:pi", "e": "const:e", "x": "exp", "X": "exp", "v": "fe", "V": "fe",
    "numbersign": "un:cube", "bar": "un:abs",
}


def _keytable(names):
    out = {}
    for name, act in names.items():
        kv = getattr(Gdk, "KEY_" + name, None)
        if kv is not None:
            out[kv] = act
    return out


KEYMAP_ANY = _keytable(_KEYS_ANY)
for _d in "0123456789":
    KEYMAP_ANY[getattr(Gdk, "KEY_" + _d)] = "d:" + _d
    KEYMAP_ANY[getattr(Gdk, "KEY_KP_" + _d)] = "d:" + _d
KEYMAP_STD = _keytable(_KEYS_STD)
KEYMAP_SCI = _keytable(_KEYS_SCI)


def _hidden(w):
    """처음엔 숨겨 두고 나중에 set_visible 로 보일 것 — 속까지 미리 보이게 해 둔다
    (no_show_all 인 위젯은 창의 show_all 이 속을 건너뛰므로)"""
    w.show_all()
    w.hide()
    w.set_no_show_all(True)


class CalcWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLE)
        self.app = app
        st = app.state
        self.mode = st.get("mode") if st.get("mode") in MODE_NAMES else "standard"
        self.engine = eng.Engine(self.mode == "scientific")
        if st.get("angle") in eng.ANGLES:
            self.engine.angle = st["angle"]
        self.history = []                 # [(식, Decimal)]
        self.memory = []                  # [Decimal] — 0 번이 맨 위 (MR · M+ · M- 가 쓰는 것)
        self.second = False
        self.trig_second = False
        self.wide = None
        self._fit_w = 0
        self._font_px = None
        self._maximized = False
        self.buttons = {}                 # 동작 → 단추 (지금 자판의 것 — 키를 누르면 반짝)
        self.set_icon_name("accessories-calculator")
        self.get_style_context().add_class("calc-window")
        self.set_size_request(320, 460)
        w, h = self._saved_size(self.mode)
        self.set_default_size(w, h)
        if st.get("maximized"):
            self.maximize()

        self.overlay = Gtk.Overlay()
        self.add(self.overlay)
        main = Gtk.Box()
        self.overlay.add(main)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.column.get_style_context().add_class("c-column")
        main.pack_start(self.column, True, True, 0)
        self.side = SidePanel(self)
        _hidden(self.side)
        main.pack_start(self.side, False, False, 0)

        self._build_top()
        self._build_display()
        self.sci_bar = self._build_sci_bar()
        self.column.pack_start(self.sci_bar, False, False, 0)
        self.column.pack_start(self._build_memory_row(), False, False, 0)
        self.pads = Gtk.Stack()
        self.pads.set_homogeneous(False)
        self.pad_std, self.btn_std = self._build_pad(STD_ROWS, 4)
        self.pad_sci, self.btn_sci = self._build_pad(SCI_ROWS, 5)
        self.pads.add_named(self.pad_std, "standard")
        self.pads.add_named(self.pad_sci, "scientific")
        self.column.pack_start(self.pads, True, True, 0)

        # 덮는 칸들 — 흐린 막(눌러서 닫기) · 올라오는 기록/메모리 · 왼쪽 탐색
        self.scrim = Gtk.EventBox()
        self.scrim.get_style_context().add_class("c-scrim")
        self.scrim.set_no_show_all(True)
        self.scrim.connect("button-press-event", lambda *_: (self.close_overlays(), True)[1])
        self.overlay.add_overlay(self.scrim)
        self.fly = Gtk.Revealer()
        self.fly.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.fly.set_transition_duration(160)
        self.fly.set_valign(Gtk.Align.FILL)
        self.fly.set_no_show_all(True)
        self.fly_stack = Gtk.Stack()
        self.fly_stack.get_style_context().add_class("c-flyout")
        self.fly_history = HistoryList(self)
        self.fly_memory = MemoryList(self)
        self.fly_stack.add_named(self.fly_history, "history")
        self.fly_stack.add_named(self.fly_memory, "memory")
        self.fly_stack.show_all()
        self.fly.add(self.fly_stack)
        self.fly.connect("notify::child-revealed", self._on_revealed)
        self.overlay.add_overlay(self.fly)
        self.nav_rev = Gtk.Revealer()
        self.nav_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.nav_rev.set_transition_duration(160)
        self.nav_rev.set_halign(Gtk.Align.START)
        self.nav_rev.set_no_show_all(True)
        self.nav = NavPane(self)
        self.nav.show_all()
        self.nav_rev.add(self.nav)
        self.nav_rev.connect("notify::child-revealed", self._on_revealed)
        self.overlay.add_overlay(self.nav_rev)

        self.connect("key-press-event", self._on_key)
        self.connect("size-allocate", self._on_alloc)
        self.connect("delete-event", self._on_delete)
        self.connect("window-state-event", self._on_wstate)
        self.show_all()
        self._apply_mode()
        self.refresh()
        self.refresh_lists()

    # ── 만들기 ──
    def _build_top(self):
        top = Gtk.Box(spacing=4)
        top.get_style_context().add_class("c-top")
        top.pack_start(flat_button(icon(["open-menu-symbolic", "view-more-symbolic"], 16), "탐색 열기",
                                   self.toggle_nav), False, False, 0)
        self.title = Gtk.Label(xalign=0)
        self.title.get_style_context().add_class("c-title")
        top.pack_start(self.title, False, False, 6)
        self.hist_btn = flat_button(icon(["document-open-recent-symbolic", "appointment-soon-symbolic",
                                          "x-office-calendar-symbolic"], 16), "기록 (Ctrl+H)",
                                    lambda: self.toggle_flyout("history"))
        _hidden(self.hist_btn)
        top.pack_end(self.hist_btn, False, False, 0)
        self.column.pack_start(top, False, False, 0)

    def _build_display(self):
        ev = Gtk.EventBox()                       # 오른쪽 단추 — 복사 · 붙여넣기
        ev.connect("button-press-event", self._on_display_press)
        box = self.display = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("c-display")
        ev.add(box)
        self.expr_label = Gtk.Label(xalign=1)
        self.expr_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.expr_label.get_style_context().add_class("c-expr")
        self.expr_label.set_size_request(-1, 22)
        box.pack_start(self.expr_label, False, False, 0)
        self.result_label = Gtk.Label(xalign=1, yalign=1)
        self.result_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.result_label.get_style_context().add_class("c-result")
        self.result_label.set_size_request(-1, 64)
        box.pack_start(self.result_label, False, False, 0)
        self.column.pack_start(ev, False, False, 0)

    def _build_sci_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        bar.set_no_show_all(True)
        r1 = Gtk.Box(spacing=2)
        self.angle_btn = flat_button("DEG", "각도 단위 바꾸기 — 도 · 라디안 · 그레이드", lambda: self.press("angle"),
                                     css="c-mode-btn")
        r1.pack_start(self.angle_btn, False, False, 0)
        self.fe_btn = Gtk.ToggleButton(label="F-E")
        self.fe_btn.set_can_focus(False)
        self.fe_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.fe_btn.get_style_context().add_class("c-mode-btn")
        self.fe_btn.set_tooltip_text("늘 지수 표기로 보기 (V)")
        self._fe_sig = self.fe_btn.connect("toggled", lambda *_: self.press("fe"))
        r1.pack_start(self.fe_btn, False, False, 0)
        bar.pack_start(r1, False, False, 0)
        r2 = Gtk.Box(spacing=2)
        r2.pack_start(self._popover_button("삼각법", self._trig_popover()), False, False, 0)
        r2.pack_start(self._popover_button("함수", self._func_popover()), False, False, 0)
        bar.pack_start(r2, False, False, 0)
        for w in (r1, r2):
            w.show_all()
        return bar

    def _popover_button(self, label, pop):
        b = Gtk.MenuButton()
        b.set_can_focus(False)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("c-mode-btn")
        h = Gtk.Box(spacing=6)
        h.pack_start(Gtk.Label(label=label), False, False, 0)
        h.pack_start(icon(["pan-down-symbolic"], 12), False, False, 0)
        b.add(h)
        b.set_popover(pop)
        return b

    def _key_button(self, markup, css, act, tip, width=None):
        b = Gtk.Button()
        b.set_can_focus(False)
        lbl = Gtk.Label()
        lbl.set_markup(markup)
        b.add(lbl)
        b.lbl = lbl
        b.act = act
        for c in ["c-key", "c-" + css]:
            b.get_style_context().add_class(c)
        if tip:
            b.set_tooltip_text(tip)
        b.connect("clicked", lambda btn: self.press(btn.act))
        if width:
            b.set_size_request(width, -1)
        return b

    def _trig_popover(self):
        pop = Gtk.Popover()
        g = Gtk.Grid(column_spacing=2, row_spacing=2)
        g.set_border_width(6)
        self.trig_2nd = Gtk.ToggleButton()
        self.trig_2nd.set_can_focus(False)
        l2 = Gtk.Label()
        l2.set_markup("2<sup>nd</sup>")
        self.trig_2nd.add(l2)
        self.trig_2nd.get_style_context().add_class("c-key")
        self.trig_2nd.get_style_context().add_class("c-fn")
        self.trig_2nd.set_size_request(64, 40)
        self.trig_2nd.connect("toggled", lambda t: self._set_trig_second(t.get_active()))
        g.attach(self.trig_2nd, 0, 0, 1, 1)
        self.trig_btns = []
        for i, name in enumerate(("sin", "cos", "tan")):
            b = self._key_button(name, "fn", "un:" + name, None, 64)
            b.base = name
            b.set_size_request(64, 40)
            b.connect("clicked", lambda *_: pop.popdown())
            g.attach(b, i + 1, 0, 1, 1)
            self.trig_btns.append(b)
        g.show_all()
        pop.add(g)
        return pop

    def _func_popover(self):
        pop = Gtk.Popover()
        g = Gtk.Grid(column_spacing=2, row_spacing=2)
        g.set_border_width(6)
        for i, (markup, act, tip) in enumerate((("|x|", "un:abs", "절댓값"), ("⌊x⌋", "un:floor", "내림"),
                                                ("⌈x⌉", "un:ceil", "올림"), ("rand", "rand", "0과 1 사이의 난수"))):
            b = self._key_button(markup, "fn", act, tip)
            b.set_size_request(64, 40)
            b.connect("clicked", lambda *_: pop.popdown())
            g.attach(b, i, 0, 1, 1)
        g.show_all()
        pop.add(g)
        return pop

    def _set_trig_second(self, on):
        self.trig_second = on
        for b in self.trig_btns:
            b.act = "un:a" + b.base if on else "un:" + b.base
            b.lbl.set_markup(f"{b.base}<sup>-1</sup>" if on else b.base)

    def _build_memory_row(self):
        row = Gtk.Box(homogeneous=True)
        row.get_style_context().add_class("c-memrow")
        self.mem_btns = {}
        for label, op, tip in (("MC", "mc", "메모리 모두 지우기 (Ctrl+L)"), ("MR", "mr", "메모리 불러오기 (Ctrl+R)"),
                               ("M+", "m+", "메모리에 더하기 (Ctrl+P)"), ("M-", "m-", "메모리에서 빼기 (Ctrl+Q)"),
                               ("MS", "ms", "메모리에 저장 (Ctrl+M)"), ("M▾", "list", "메모리 보기")):
            b = flat_button(label, tip, (lambda op=op: self.memory_op(op)) if op != "list"
                            else (lambda: self.toggle_flyout("memory")), css="c-mem")
            row.pack_start(b, True, True, 0)
            self.mem_btns[op] = b
        return row

    def _build_pad(self, rows, cols):
        g = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, row_spacing=2, column_spacing=2)
        g.get_style_context().add_class("c-pad")
        btns = {}
        for r, ids in enumerate(rows):
            for c, kid in enumerate(ids):
                markup, css, act, tip = KEYS[kid]
                b = self._key_button(markup, css, act, tip)
                b.kid = kid
                b.set_hexpand(True)
                b.set_vexpand(True)
                g.attach(b, c, r, 1, 1)
                btns[kid] = b
        return g, btns

    # ── 모드 ──
    def _saved_size(self, mode):
        sizes = self.app.state.get("sizes") if isinstance(self.app.state.get("sizes"), dict) else {}
        s = sizes.get(mode) or DEFAULT_SIZES[mode]
        try:
            return max(320, int(s[0])), max(460, int(s[1]))
        except (TypeError, ValueError, IndexError):
            return DEFAULT_SIZES[mode]

    def _remember_size(self):
        if self._maximized:
            return
        sizes = self.app.state.setdefault("sizes", {})
        if not isinstance(sizes, dict):
            sizes = self.app.state["sizes"] = {}
        sizes[self.mode] = list(self.get_size())

    def set_mode(self, mode):
        self.close_overlays()
        if mode not in MODE_NAMES or mode == self.mode:
            return
        self._remember_size()
        self.mode = mode
        self.engine.scientific = mode == "scientific"
        self.engine.reset()
        self.app.state["mode"] = mode
        self.app.save_state()
        self._apply_mode()
        if not self._maximized:
            self.resize(*self._saved_size(mode))
        self.refresh()

    def _apply_mode(self):
        sci = self.mode == "scientific"
        self.title.set_text(MODE_NAMES[self.mode])
        self.pads.set_visible_child_name(self.mode)
        self.sci_bar.set_visible(sci)
        self.buttons = {}
        for b in (self.btn_sci if sci else self.btn_std).values():
            self.buttons.setdefault(b.act, b)
        self.nav.sync(self.mode)
        self._set_second(False)
        self._font_px = None
        self._fit_w = 0
        self._queue_fit()

    def _set_second(self, on):
        self.second = on
        b2 = self.btn_sci.get("2nd")
        if b2 is not None:
            ctx = b2.get_style_context()
            if on:
                ctx.add_class("on")
            else:
                ctx.remove_class("on")
        for kid, alt in SECOND.items():
            b = self.btn_sci.get(kid)
            if b is None:
                continue
            markup, act, tip = alt if on else (KEYS[kid][0], KEYS[kid][2], KEYS[kid][3])
            b.lbl.set_markup(markup)
            b.act = act
            b.set_tooltip_text(tip)
        if self.mode == "scientific":
            self.buttons = {}
            for b in self.btn_sci.values():
                self.buttons.setdefault(b.act, b)

    # ── 누르기 ──
    def press(self, act):
        e = self.engine
        kind, _sep, arg = act.partition(":")
        record = None
        if kind == "d":
            e.digit(arg)
        elif kind == "point":
            e.point()
        elif kind == "op":
            e.binary(arg)
        elif kind == "un":
            e.apply_unary(arg)
        elif kind == "neg":
            e.negate()
        elif kind == "pct":
            e.percent()
        elif kind == "ce":
            e.clear_entry()
        elif kind == "c":
            e.reset()
        elif kind == "bs":
            e.backspace()
        elif kind == "eq":
            record = e.equals()
        elif kind == "lp":
            e.open_paren()
        elif kind == "rp":
            e.close_paren()
        elif kind == "exp":
            e.exp_entry()
        elif kind == "const":
            e.constant(arg)
        elif kind == "rand":
            e.random()
        elif kind == "2nd":
            self._set_second(not self.second)
            return
        elif kind == "angle":
            e.cycle_angle()
            self.app.state["angle"] = e.angle
            self.app.save_state()
        elif kind == "fe":
            e.fe = not e.fe
        # 2nd 는 한 번 쓰면 풀린다 (윈도우처럼) — 숫자·지우기는 제외
        if self.second and kind in ("un", "op") and act not in ("op:+", "op:-", "op:*", "op:/"):
            self._set_second(False)
        if record:
            self.history.append(record)
            del self.history[:-HISTORY_MAX]
            self.refresh_lists()
        self.refresh()

    def refresh(self):
        e = self.engine
        self.expr_label.set_text(e.expression_text())
        self.expr_label.set_tooltip_text(e.expression_text() or None)
        self.result_label.set_text(e.display_text())
        self._fit_w = 0
        self._fit_result()
        err = bool(e.error)
        for pad in (self.btn_std, self.btn_sci):
            for kid, b in pad.items():
                b.set_sensitive(not err or kid in AFTER_ERROR)
        for b in self.trig_btns:
            b.set_sensitive(not err)
        has = bool(self.memory)
        for op, b in self.mem_btns.items():
            b.set_sensitive(not err and (has or op in ("m+", "m-", "ms")))
        lp = self.btn_sci.get("lp")
        if lp is not None:
            n = e.open_parens
            lp.lbl.set_markup(f"(<sub>{n}</sub>" if n else "(")
        self.angle_btn.set_label(eng.ANGLE_NAMES[e.angle])
        self.fe_btn.handler_block(self._fe_sig)
        self.fe_btn.set_active(e.fe)
        self.fe_btn.handler_unblock(self._fe_sig)

    # ── 결과 글자 크기 — 긴 숫자는 줄여서 한 줄에 ──
    def _queue_fit(self):
        GLib.idle_add(lambda: (self._fit_result(), False)[1])

    def _fit_result(self):
        avail = self.display.get_allocated_width() - 28
        if avail <= 20:
            return
        text = self.result_label.get_text()
        base = 46 if self.mode == "standard" else 40
        if self.engine.error:
            base = 26
        layout = self.result_label.create_pango_layout(text)
        desc = self.result_label.get_pango_context().get_font_description().copy()
        desc.set_absolute_size(base * Pango.SCALE)
        layout.set_font_description(desc)
        w, _h = layout.get_pixel_size()
        px = base if w <= avail else max(14, int(base * avail / max(1, w)))
        if px != self._font_px:
            self._font_px = px
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_size_new_absolute(px * Pango.SCALE))
            self.result_label.set_attributes(attrs)

    def _on_alloc(self, _w, alloc):
        w = alloc.width
        if w != self._fit_w:
            self._fit_w = w
            self._queue_fit()
        wide = w >= WIDE
        if wide != self.wide:
            self.wide = wide
            GLib.idle_add(self._apply_wide, wide)

    def _apply_wide(self, wide):
        self.side.set_visible(wide)
        if wide:
            self.close_overlays()
        self.hist_btn.set_visible(not wide)
        return False

    # ── 기록·메모리 ──
    def refresh_lists(self):
        for lst in (self.side.history, self.side.memory, self.fly_history, self.fly_memory):
            lst.refresh()
        self.refresh()

    def restore_history(self, index):
        if 0 <= index < len(self.history):
            expr, val = self.history[index]
            self.engine.restore(expr, val)
            self.close_overlays()
            self.refresh()

    def clear_history(self):
        self.history.clear()
        self.refresh_lists()

    def memory_op(self, op):
        e = self.engine
        v = e.current_value()
        if op == "mc":
            self.memory.clear()
        elif op == "mr":
            if self.memory:
                e.set_value(self.memory[0])
        elif v is None:
            return
        elif op == "ms":
            self.memory.insert(0, v)
            e.entry = None                         # 저장한 뒤 숫자를 치면 새 숫자 (윈도우처럼)
        elif op in ("m+", "m-"):
            if not self.memory:
                self.memory.insert(0, Decimal(0))
            self._mem_add(0, v if op == "m+" else -v)
            e.entry = None
        self.refresh_lists()

    def memory_item(self, op, i):
        if not 0 <= i < len(self.memory):
            return
        v = self.engine.current_value()
        if op == "mc":
            del self.memory[i]
        elif op == "mr":
            self.engine.set_value(self.memory[i])
            self.close_overlays()
        elif v is not None:
            self._mem_add(i, v if op == "m+" else -v)
        self.refresh_lists()

    def _mem_add(self, i, v):
        try:
            self.memory[i] = eng.binary("+", self.memory[i], v)
        except eng.CalcError:
            pass

    # ── 덮는 칸 ──
    def toggle_flyout(self, which):
        if self.wide:
            self.side.show(which)
            return
        if self.fly.get_visible() and self.fly.get_reveal_child() and self.fly_stack.get_visible_child_name() == which:
            self.close_overlays()
            return
        self.close_overlays(keep_scrim=True)
        self.fly_stack.set_visible_child_name(which)
        # 메모리 줄 아래(자판 자리)를 덮는다
        y = self.pads.translate_coordinates(self.overlay, 0, 0)
        top = y[1] if y else 200
        if which == "memory" and self.mem_btns:
            yy = self.mem_btns["mc"].translate_coordinates(self.overlay, 0, 0)
            top = min(top, yy[1] + self.mem_btns["mc"].get_allocated_height()) if yy else top
        self.fly.set_margin_top(max(0, top))
        self.scrim.show()
        self.fly.show()
        self.fly.set_reveal_child(True)

    def toggle_nav(self):
        if self.nav_rev.get_visible() and self.nav_rev.get_reveal_child():
            self.close_overlays()
            return
        self.close_overlays(keep_scrim=True)
        self.scrim.show()
        self.nav_rev.show()
        self.nav_rev.set_reveal_child(True)

    def close_overlays(self, keep_scrim=False):
        for r in (self.fly, self.nav_rev):
            if r.get_reveal_child():
                r.set_reveal_child(False)
        if not keep_scrim:
            self.scrim.hide()

    def _on_revealed(self, rev, _p):
        # 다 닫힌 칸은 숨긴다 — Overlay 의 칸은 보이지 않아도 제 자리의 누르기를 가로채므로
        if not rev.get_child_revealed() and not rev.get_reveal_child():
            rev.hide()

    def overlays_open(self):
        return self.fly.get_reveal_child() or self.nav_rev.get_reveal_child()

    # ── 복사·붙여넣기 ──
    def copy(self):
        t = self.engine.copy_text()
        if t is not None:
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text(t, -1)
            cb.store()

    def paste(self):
        def got(_cb, text):
            x = eng.parse_number(text)
            if x is None:
                return
            try:
                x = eng.check_range(x)
            except eng.CalcError:
                return
            self.engine.set_value(x)
            self.refresh()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(got)

    def _on_display_press(self, _w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        m = Gtk.Menu()
        for label, cb in (("복사", self.copy), ("붙여넣기", self.paste)):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _i, cb=cb: cb())
            m.append(it)
        m.show_all()
        m.attach_to_widget(self.display, None)
        m.popup_at_pointer(ev)
        return True

    # ── 키보드 ──
    def _flash(self, act):
        b = self.buttons.get(act)
        if b is None or not b.get_mapped():
            return
        b.set_state_flags(Gtk.StateFlags.ACTIVE, False)
        GLib.timeout_add(110, lambda: (b.unset_state_flags(Gtk.StateFlags.ACTIVE), False)[1])

    def _on_key(self, _w, ev):
        st = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = bool(st & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(st & Gdk.ModifierType.MOD1_MASK)
        shift = bool(st & Gdk.ModifierType.SHIFT_MASK)
        k = ev.keyval
        if alt and not ctrl:
            if k in (Gdk.KEY_1, Gdk.KEY_KP_1):
                self.set_mode("standard")
                return True
            if k in (Gdk.KEY_2, Gdk.KEY_KP_2):
                self.set_mode("scientific")
                return True
            return False
        if ctrl:
            name = Gdk.keyval_name(Gdk.keyval_to_lower(k)) or ""
            if name == "d" and shift:
                self.clear_history()
            elif name == "c":
                self.copy()
            elif name == "v":
                self.paste()
            elif name == "h":
                self.toggle_flyout("history")
            elif name in ("m", "p", "q", "r", "l"):
                self.memory_op({"m": "ms", "p": "m+", "q": "m-", "r": "mr", "l": "mc"}[name])
            else:
                return False
            return True
        if k == Gdk.KEY_Escape and self.overlays_open():
            self.close_overlays()
            return True
        if isinstance(self.get_focus(), Gtk.Entry):
            return False
        act = KEYMAP_ANY.get(k) or (KEYMAP_SCI if self.mode == "scientific" else KEYMAP_STD).get(k)
        if act is None:
            return False
        if self.overlays_open() and act != "c":
            self.close_overlays()
        self._flash(act)
        self.press(act)
        return True

    # ── 닫기 ──
    def _on_wstate(self, _w, ev):
        self._maximized = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def _on_delete(self, *_):
        self._remember_size()
        self.app.state["maximized"] = self._maximized
        self.app.state["mode"] = self.mode
        self.app.state["angle"] = self.engine.angle
        self.app.save_state()
        return False
