"""메모장 — 찾기·바꾸기 막대 (윈도우 11 메모장처럼 글 위에 붙는다).

창마다 하나, 지금 탭의 글에 쓴다. 찾기 설정(찾을 글·대/소문자·단어 단위·줄 바꿈)은 창의 모든 탭이 함께 쓴다
— 탭을 바꿔도 F3 이 같은 글을 찾는다. 찾는 일은 GtkSourceView 의 SearchContext 가 (큰 글은 나눠서) 한다.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import Gdk, GLib, Gtk, GtkSource  # noqa: E402

from .common import icon, icon_button  # noqa: E402


class FindBar(Gtk.Revealer):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.set_transition_duration(120)
        self.settings = GtkSource.SearchSettings()
        self.settings.set_wrap_around(True)
        self.doc = None
        self._ctx = None
        self._ctx_sig = 0
        self._style = None
        self._opened = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("np-find")
        self.add(box)

        row = Gtk.Box(spacing=4)
        box.pack_start(row, False, False, 0)
        self.toggle = Gtk.ToggleButton()
        self.toggle.set_can_focus(False)
        self.toggle.set_relief(Gtk.ReliefStyle.NONE)
        self.toggle.get_style_context().add_class("np-flat")
        self.toggle.set_tooltip_text("바꾸기 표시")
        self._arrow = icon(["pan-end-symbolic"], 14)
        self.toggle.add(self._arrow)
        self.toggle.connect("toggled", self._on_toggle)
        row.pack_start(self.toggle, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("찾기")
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", lambda *_: self.find(backward=False))
        self.entry.connect("key-press-event", self._on_key)
        row.pack_start(self.entry, False, False, 0)
        self.count = Gtk.Label(xalign=0)
        self.count.get_style_context().add_class("np-count")
        row.pack_start(self.count, False, False, 4)
        row.pack_start(icon_button(["go-down-symbolic"], "다음 찾기 (F3)", lambda: self.find(False)), False, False, 0)
        row.pack_start(icon_button(["go-up-symbolic"], "이전 찾기 (Shift+F3)", lambda: self.find(True)), False, False, 0)

        # 옵션 — 윈도우 11 메모장의 찾기 옵션 단추
        opt = Gtk.MenuButton()
        opt.set_relief(Gtk.ReliefStyle.NONE)
        opt.set_can_focus(False)
        opt.get_style_context().add_class("np-flat")
        opt.set_tooltip_text("찾기 옵션")
        opt.add(icon(["preferences-other-symbolic", "emblem-system-symbolic", "view-more-symbolic"], 16))
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pbox.set_border_width(10)
        self.chk_case = Gtk.CheckButton(label="대/소문자 구분")
        self.chk_word = Gtk.CheckButton(label="단어 단위")
        self.chk_wrap = Gtk.CheckButton(label="줄 바꿈")
        self.chk_wrap.set_tooltip_text("끝에 닿으면 처음부터 다시 찾습니다")
        self.chk_wrap.set_active(True)
        self.chk_case.connect("toggled", lambda c: self._set_opt(self.settings.set_case_sensitive, c))
        self.chk_word.connect("toggled", lambda c: self._set_opt(self.settings.set_at_word_boundaries, c))
        self.chk_wrap.connect("toggled", lambda c: self._set_opt(self.settings.set_wrap_around, c))
        for c in (self.chk_case, self.chk_word, self.chk_wrap):
            pbox.pack_start(c, False, False, 0)
        pbox.show_all()
        pop.add(pbox)
        opt.set_popover(pop)
        row.pack_start(opt, False, False, 0)
        row.pack_end(icon_button(["window-close-symbolic"], "닫기 (Esc)", self.close), False, False, 0)

        # 바꾸기 줄
        self.rep_rev = Gtk.Revealer()
        self.rep_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.rep_rev.set_transition_duration(100)
        rrow = Gtk.Box(spacing=4)
        pad = Gtk.Box()
        pad.set_size_request(30, -1)                 # 찾기 칸과 줄을 맞춘다 (펼침 단추 폭)
        rrow.pack_start(pad, False, False, 0)
        self.rentry = Gtk.Entry()
        self.rentry.set_placeholder_text("바꾸기")
        self.rentry.connect("activate", lambda *_: self.replace_one())
        self.rentry.connect("key-press-event", self._on_key)
        rrow.pack_start(self.rentry, False, False, 0)
        for label, cb in (("바꾸기", self.replace_one), ("모두 바꾸기", self.replace_all)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("np-text-btn")
            b.connect("clicked", lambda _b, cb=cb: cb())
            rrow.pack_start(b, False, False, 2)
        self.rep_rev.add(rrow)
        box.pack_start(self.rep_rev, False, False, 0)

    # ── 열고 닫기 ──
    @property
    def is_open(self):
        return self._opened

    def open(self, replace=False):
        doc = self.win.current()
        if doc is None:
            return
        # 한 줄짜리 선택이 있으면 그것을 찾는다 (윈도우처럼)
        b = doc.buffer
        sel = b.get_selection_bounds()
        if sel:
            text = b.get_text(sel[0], sel[1], True)
            if text and "\n" not in text and len(text) < 200 and text != self.entry.get_text():
                self.entry.set_text(text)
        self._opened = True
        self.set_reveal_child(True)
        self.toggle.set_active(replace)
        if self._ctx is not None:
            self._ctx.set_highlight(True)
        self.entry.grab_focus()
        self._update_count()

    def close(self):
        if not self._opened:
            return
        self._opened = False
        self.set_reveal_child(False)
        if self._ctx is not None:
            self._ctx.set_highlight(False)
        doc = self.win.current()
        if doc is not None:
            doc.view.grab_focus()

    def _on_toggle(self, t):
        on = t.get_active()
        self.rep_rev.set_reveal_child(on)
        self._arrow.set_from_icon_name("pan-down-symbolic" if on else "pan-end-symbolic", Gtk.IconSize.BUTTON)
        t.set_tooltip_text("바꾸기 숨기기" if on else "바꾸기 표시")
        if on and self._opened:
            GLib.idle_add(lambda: (self.rentry.grab_focus(), False)[1] if self.entry.get_text() else False)

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and ev.state & Gdk.ModifierType.SHIFT_MASK:
            self.find(backward=True)
            return True
        return False

    def _set_opt(self, setter, check):
        setter(check.get_active())
        self._update_count()

    # ── 탭 ──
    def attach(self, doc):
        """지금 탭이 바뀌었다 — 그 글의 검색에 붙는다"""
        if self._ctx is not None:
            self._ctx.set_highlight(False)
            if self._ctx_sig:
                self._ctx.disconnect(self._ctx_sig)
        self._ctx, self._ctx_sig = None, 0
        self.doc = doc
        if doc is None:
            return
        self._ctx = doc.search_context(self.settings, self.match_style())
        self._ctx.set_highlight(self._opened)
        self._ctx_sig = self._ctx.connect("notify::occurrences-count", lambda *_: self._update_count())
        self._update_count()

    def match_style(self):
        if self._style is None:
            self._style = self.win.app.match_style()
        return self._style

    def restyle(self, style):
        """강조색이 바뀌었다"""
        self._style = style
        for d in self.win.docs():
            if d.search is not None:
                d.search.set_match_style(style)

    # ── 찾기 ──
    def _on_changed(self, e):
        text = e.get_text()
        self.settings.set_search_text(text or None)
        if not text:
            self._mark_nomatch(False)
            self._update_count()
            return
        doc = self.win.current()
        if doc is None:
            return
        # 치는 대로 찾기 — 지금 선택의 시작부터 (글자를 더 칠수록 같은 곳에서 늘어난다)
        b = doc.buffer
        sel = b.get_selection_bounds()
        start = sel[0] if sel else b.get_iter_at_mark(b.get_insert())
        self._go(doc, start, backward=False)

    def find(self, backward=False):
        """다음/이전 찾기 (F3 · Shift+F3 · Enter). 막대가 닫혀 있어도 마지막 찾은 글로"""
        doc = self.win.current()
        if doc is None:
            return
        if not self.settings.get_search_text():
            self.open()
            return
        b = doc.buffer
        sel = b.get_selection_bounds()
        if backward:
            it = sel[0] if sel else b.get_iter_at_mark(b.get_insert())
        else:
            it = sel[1] if sel else b.get_iter_at_mark(b.get_insert())
        self._go(doc, it, backward)

    def _go(self, doc, it, backward):
        ctx = doc.search_context(self.settings, self.match_style())
        found, ms, me, _wrapped = (ctx.backward if backward else ctx.forward)(it)
        if found:
            doc.buffer.select_range(ms, me)
            doc.view.scroll_to_mark(doc.buffer.get_insert(), 0.15, False, 0, 0)
        self._mark_nomatch(not found)
        self._update_count(found_none=not found)
        return found

    def _mark_nomatch(self, on):
        ctx = self.entry.get_style_context()
        if on:
            ctx.add_class("np-nomatch")
        else:
            ctx.remove_class("np-nomatch")

    def _update_count(self, found_none=False):
        ctx = self._ctx
        text = self.settings.get_search_text()
        if ctx is None or not text:
            self.count.set_text("")
            return
        n = ctx.get_occurrences_count()
        if n < 0:
            self.count.set_text("…")                 # 아직 세는 중 (큰 글)
            return
        if n == 0:
            self.count.set_text("결과 없음")
            self._mark_nomatch(True)
            return
        pos = 0
        sel = self.doc.buffer.get_selection_bounds() if self.doc else None
        if sel:
            pos = max(0, ctx.get_occurrence_position(sel[0], sel[1]))
        self.count.set_text(f"{pos}/{n}")
        if not found_none:
            self._mark_nomatch(False)

    def cursor_moved(self):
        if self._opened:
            self._update_count()

    # ── 바꾸기 ──
    def replace_one(self):
        doc = self.win.current()
        if doc is None or not self.settings.get_search_text():
            return
        ctx = doc.search_context(self.settings, self.match_style())
        b = doc.buffer
        sel = b.get_selection_bounds()
        if sel and ctx.get_occurrence_position(sel[0], sel[1]) > 0:
            try:
                ctx.replace(sel[0], sel[1], self.rentry.get_text(), -1)
            except GLib.Error as e:
                print("[sekai-notepad] 바꾸기 실패:", e.message, flush=True)
        self.find(backward=False)

    def replace_all(self):
        doc = self.win.current()
        if doc is None or not self.settings.get_search_text():
            return
        ctx = doc.search_context(self.settings, self.match_style())
        try:
            n = ctx.replace_all(self.rentry.get_text(), -1)
        except GLib.Error as e:
            print("[sekai-notepad] 모두 바꾸기 실패:", e.message, flush=True)
            return
        self.count.set_text(f"{n}개 바꿈" if n else "결과 없음")
