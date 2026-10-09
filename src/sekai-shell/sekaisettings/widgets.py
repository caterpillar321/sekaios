"""SekaiOS 설정 — 재사용 위젯.

윈도우 10/11 설정처럼 "한 줄 = 한 항목" 형태의 행(row)을 만든다.
   [아이콘]  제목                              [컨트롤]
             설명
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gdk  # noqa: E402

from sekaishell import ui as _ui  # noqa: E402

combo = _ui.combo          # 공용으로 옮겼다 — 빠른 설정의 Wi-Fi 창도 쓴다
icon_image = _ui.icon_image


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


class RowBox(Gtk.Container):
    """설정 한 줄의 [아이콘·글] ··· [조작]. 한 줄에 들어가지 않으면 조작을 글 아래로 내린다 (윈도우 11 설정처럼
    — 창이 좁을 때). 그래서 줄의 최소 폭은 둘 중 넓은 것만큼이다 (예전엔 둘을 더한 폭이 창의 최소 폭을 잡았다).
    자식: lead(아이콘과 글) 하나, control 하나 (없어도 된다)"""

    SPACING = 12

    def __init__(self, lead, control=None, indent=0):
        # (Gtk.Box 를 이어받아 배치만 바꾸면 Box 의 CSS 부품에 크기가 배정되지 않아 경고가 났다 — 직접 담는다)
        super().__init__()
        self.set_has_window(False)
        self._kids = []
        self.lead, self.control, self.indent = lead, control, indent
        self.add(lead)
        if control is not None:
            self.add(control)

    def do_add(self, w):
        self._kids.append(w)
        w.set_parent(self)
        self.queue_resize()

    def do_remove(self, w):
        if w in getattr(self, "_kids", ()):
            self._kids.remove(w)
            w.unparent()
            if w is self.control:
                self.control = None
            self.queue_resize()

    def do_forall(self, _include_internals, callback, *data):
        # (GObject 가 만들어지는 도중 — __init__ 이 _kids 를 두기 전 — 에도 불린다)
        for w in list(getattr(self, "_kids", ())):
            callback(w, *data)

    def do_child_type(self):
        return Gtk.Widget.__gtype__

    def _ctl(self):
        c = self.control
        return c if c is not None and c.get_visible() else None

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self):
        lm, ln = self.lead.get_preferred_width()
        c = self._ctl()
        if c is None:
            return lm, ln
        cm, cn = c.get_preferred_width()
        return max(lm, self.indent + cm), ln + self.SPACING + cn

    def _one_line(self, width):
        c = self._ctl()
        if c is None:
            return True
        return width >= self.lead.get_preferred_width()[0] + self.SPACING + c.get_preferred_width()[0]

    def _split(self, width):
        """한 줄일 때 (글 폭, 조작 폭) — 조작은 되도록 자연 폭, 글은 나머지"""
        c = self._ctl()
        cm, cn = c.get_preferred_width()
        lm = self.lead.get_preferred_width()[0]
        cw = max(cm, min(cn, width - self.SPACING - lm))
        return max(1, width - self.SPACING - cw), cw

    def do_get_preferred_height_for_width(self, width):
        c = self._ctl()
        if c is None:
            return self.lead.get_preferred_height_for_width(width)
        if self._one_line(width):
            lw, cw = self._split(width)
            lm, ln = self.lead.get_preferred_height_for_width(lw)
            cm, cn = c.get_preferred_height_for_width(cw)
            return max(lm, cm), max(ln, cn)
        lm, ln = self.lead.get_preferred_height_for_width(width)
        cm, cn = c.get_preferred_height_for_width(max(1, width - self.indent))
        return lm + self.SPACING + cm, ln + self.SPACING + cn

    def do_get_preferred_height(self):
        return self.do_get_preferred_height_for_width(self.do_get_preferred_width()[1])

    def do_get_preferred_width_for_height(self, _height):
        return self.do_get_preferred_width()

    def do_size_allocate(self, a):
        self.set_allocation(a)
        c = self._ctl()
        if c is None:
            self.lead.size_allocate(a)
            return
        if self._one_line(a.width):
            lw, cw = self._split(a.width)
            ch = min(a.height, c.get_preferred_height_for_width(cw)[1])
            la = Gdk.Rectangle()
            la.x, la.y, la.width, la.height = a.x, a.y, lw, a.height
            ca = Gdk.Rectangle()
            ca.x, ca.y, ca.width, ca.height = a.x + a.width - cw, a.y + (a.height - ch) // 2, cw, ch
        else:
            lh = self.lead.get_preferred_height_for_width(a.width)[1]
            cw = min(a.width - self.indent, c.get_preferred_width()[1])
            ch = c.get_preferred_height_for_width(cw)[1]
            la = Gdk.Rectangle()
            la.x, la.y, la.width, la.height = a.x, a.y, a.width, lh
            ca = Gdk.Rectangle()
            ca.x, ca.y, ca.width, ca.height = a.x + self.indent, a.y + lh + self.SPACING, cw, ch
        self.lead.size_allocate(la)
        c.size_allocate(ca)


def _name_controls(w, title):
    """줄 제목을 그 줄의 스위치·콤보·숫자 칸·글자 없는 단추의 접근성 이름으로 — 화면 읽기가 "켬, 스위치"가 아니라
    "투명 효과, 스위치"로 읽게 (글자 있는 단추는 제 글자 그대로)"""
    from sekaishell import accessible
    if isinstance(w, (Gtk.Switch, Gtk.SpinButton, Gtk.Scale, Gtk.ComboBox, Gtk.Entry)) or (
            isinstance(w, Gtk.Button) and not isinstance(w, Gtk.ComboBox) and not (w.get_label() or "").strip()):
        accessible.name(w, title)
    if isinstance(w, Gtk.Container):
        w.forall(lambda c: _name_controls(c, title))


def row(listbox, title, subtitle=None, icon=None, control=None, activatable=False):
    """항목 한 줄을 만들어 리스트박스에 붙이고, 그 Row 를 돌려준다."""
    r = Gtk.ListBoxRow()
    r.set_activatable(activatable)
    r.get_style_context().add_class("row")

    lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    if icon:
        img = icon_image(icon, 20)
        img.set_valign(Gtk.Align.CENTER)
        lead.pack_start(img, False, False, 0)

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
    lead.pack_start(v, True, True, 0)

    if control is not None:
        control.set_valign(Gtk.Align.CENTER)
        r.control = control
        _name_controls(control, title)
    # 글 아래로 내릴 때는 아이콘 너비만큼 들여 글과 줄을 맞춘다
    h = RowBox(lead, control, indent=(20 + 12) if icon else 0)
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


# ── 페이지들이 저마다 만들던 작은 도우미 (네트워크·블루투스·프린터·소리·단축키 …) — 카나데가 찾은 중복 ──
def notice(text=""):
    """페이지 안의 안내 한 줄 (줄바꿈)"""
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def subsection(parent, title=None, hidden=False):
    """parent 상자 안에 섹션(제목 + 리스트박스) — Page.section 과 같은 모양을 페이지 밖 상자에"""
    if title:
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.get_style_context().add_class("section-title")
        parent.pack_start(lbl, False, False, 0)
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    lb.get_style_context().add_class("section")
    lb.set_no_show_all(hidden)
    parent.pack_start(lb, False, False, 0)
    return lb


def clear(container):
    """안의 위젯을 모두 떼어 없앤다"""
    for w in container.get_children():
        container.remove(w)
        w.destroy()


def reveal(w, on):
    """보이기·숨기기 — 처음 보일 때 안쪽도 함께 (no_show_all 로 만든 줄)"""
    if on and not w.get_visible():
        w.show()
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                c.show_all()
    elif not on and w.get_visible():
        w.hide()


def busy_box(text):
    """돌아가는 표시 + 글 — 오래 걸리는 작업 동안"""
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    sp = Gtk.Spinner()
    sp.start()
    b.pack_start(sp, False, False, 0)
    b.pack_start(info(text), False, False, 0)
    return b


def parent_window(w):
    """위젯이 든 창 (대화 상자의 부모로) — 아직 창에 안 붙었으면 None"""
    top = w.get_toplevel() if w is not None else None
    return top if isinstance(top, Gtk.Window) and top.is_toplevel() else None


def confirm(parent, text, sub, ok_label, on_ok):
    """[취소][ok_label] 경고 확인 — 기다리지 않는다, 확인을 누르면 on_ok()"""
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                          buttons=Gtk.ButtonsType.NONE, text=text)
    d.format_secondary_text(sub)
    d.add_buttons("취소", Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
    d.set_default_response(Gtk.ResponseType.CANCEL)

    def responded(dlg, resp):
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            on_ok()
    d.connect("response", responded)
    d.show_all()


class StatusLine:
    """페이지 아래 상태 한 줄 — self.msg(Label)·self.dead·self.say_src 를 쓰는 클래스에 섞는다.
    잘 된 소식은 8초 뒤 사라지고, 오류는 남는다"""

    def say(self, text, error=False):
        if self.dead:
            return
        if self.say_src:
            GLib.source_remove(self.say_src)
            self.say_src = 0
        self.msg.set_text(text or "")
        self.msg.set_visible(bool(text))
        if text and not error:

            def hide():
                self.say_src = 0
                self.msg.hide()
                return False
            self.say_src = GLib.timeout_add_seconds(8, hide)
