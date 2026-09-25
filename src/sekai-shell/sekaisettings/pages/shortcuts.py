"""단축키 — SekaiOS 의 모든 단축키를 묶음별로 보여 주고 바꾼다.

목록·기본값·설명은 hyprland.conf 의 bind 줄 한 곳에서 온다 (sekaishell/keybinds.py).
바꾼 것만 settings.json 의 "keybinds" 에 저장하고 store.set_keybinds 가 조각·세션에 반영한다.

새 키를 받는 동안에는 이미 걸린 단축키가 키를 가로채지 않게 한다:
  Hyprland — 단축키가 없는 submap(sekai-capture, hyprland.conf)으로 바꿔 둔다.
             창이 초점을 잃거나 닫히면 곧바로 되돌린다 (Esc 는 Hyprland 쪽에서도 되돌린다)
  X11      — 키보드를 붙잡는다 (sxhkd·xfwm4 의 키 잡기보다 앞선다)
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sekaishell import keybinds as kb
from ..util import hyprctl
from ..widgets import Page, button, row

CAPTURE_SUBMAP = "sekai-capture"
_MOD_KEYS = {
    Gdk.KEY_Shift_L: "SHIFT", Gdk.KEY_Shift_R: "SHIFT",
    Gdk.KEY_Control_L: "CTRL", Gdk.KEY_Control_R: "CTRL",
    Gdk.KEY_Alt_L: "ALT", Gdk.KEY_Alt_R: "ALT", Gdk.KEY_Meta_L: "ALT", Gdk.KEY_Meta_R: "ALT",
    Gdk.KEY_Super_L: "SUPER", Gdk.KEY_Super_R: "SUPER", Gdk.KEY_Hyper_L: "SUPER", Gdk.KEY_Hyper_R: "SUPER",
}
_IGNORE_KEYS = {Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock, Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_ISO_Level5_Shift}


def _mods(state):
    m = set()
    if state & Gdk.ModifierType.CONTROL_MASK:
        m.add("CTRL")
    if state & Gdk.ModifierType.MOD1_MASK:
        m.add("ALT")
    if state & Gdk.ModifierType.SHIFT_MASK:
        m.add("SHIFT")
    if state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK):
        m.add("SUPER")
    return m


def _parent(w):
    top = w.get_toplevel()
    return top if isinstance(top, Gtk.Window) and top.is_toplevel() else None


def _confirm(parent, text, sub, ok_label):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                          buttons=Gtk.ButtonsType.NONE, text=text)
    d.format_secondary_text(sub)
    d.add_buttons("취소", Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
    d.set_default_response(Gtk.ResponseType.OK)
    r = d.run()
    d.destroy()
    return r == Gtk.ResponseType.OK


class CaptureDialog(Gtk.Dialog):
    """"새 단축키를 누르세요" — Esc 취소, Backspace 끄기.
    run_capture() → ("set", 조합) | ("off", "") | ("default", "") | None"""

    def __init__(self, parent, what, current, check, can_default=False):
        super().__init__(title="단축키 바꾸기", transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.check = check                     # 조합 → 오류 글 (받을 수 있으면 None)
        self.result = None
        self._held = set()                     # 누르고 있는 수식 키
        self._other = False                    # 수식 키 말고 다른 키를 눌렀나 (Win 혼자 떼기를 가리려고)
        self._pending = None                   # (조합, 키 코드) — 그 키를 뗄 때 정한다
        self._captured = False                 # Hyprland submap / X11 키보드 붙잡기 중인가
        self._grab_seat = None
        if can_default:
            self.add_button("기본값으로", 1)
        self.add_button("취소", Gtk.ResponseType.CANCEL)
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<b>{GLib.markup_escape_text(what)}</b> 에 쓸 새 단축키를 누르세요")
        t.set_line_wrap(True)
        box.add(t)
        self.big = Gtk.Label(label=kb.pretty(current) if current else "없음")
        self.big.get_style_context().add_class("kb-capture")
        box.add(self.big)
        self.err = Gtk.Label(xalign=0)
        self.err.get_style_context().add_class("kb-capture-error")
        self.err.set_line_wrap(True)
        self.err.set_no_show_all(True)
        box.add(self.err)
        hint = Gtk.Label(label="Esc 취소 · Backspace 단축키 끄기", xalign=0)
        hint.get_style_context().add_class("row-sub")
        box.add(hint)
        # 버튼이 키보드 초점을 가져가면 Enter·Space 가 단추를 누른다 — 키는 모두 여기서 받는다
        self.connect("key-press-event", self._press)
        self.connect("key-release-event", self._release)
        self.connect("map-event", lambda *_: self._begin() or False)
        self.connect("focus-in-event", lambda *_: self._begin() or False)
        self.connect("focus-out-event", lambda *_: self._end(focus=True) or False)
        self.connect("unmap", lambda *_: self._end())

    # ── 다른 단축키가 가로채지 않게 ──
    def _begin(self):
        if self._captured:
            return
        self._captured = True
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            hyprctl("dispatch", "submap", CAPTURE_SUBMAP)
        elif kb.x11_session():
            gw = self.get_window()
            seat = Gdk.Display.get_default().get_default_seat()
            if gw is not None and seat is not None:
                st = seat.grab(gw, Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)
                if st == Gdk.GrabStatus.SUCCESS:
                    self._grab_seat = seat

    def _end(self, focus=False):
        """focus: 초점을 잃어서 — Hyprland 에선 다른 창을 쓰는 동안 단축키가 죽지 않게 되돌린다.
        X11 의 붙잡기는 그 자신이 초점 바뀜을 일으키므로 창이 닫힐 때만 놓는다"""
        if not self._captured or (focus and self._grab_seat is not None):
            return
        self._captured = False
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            hyprctl("dispatch", "submap", "reset")
        if self._grab_seat is not None:
            self._grab_seat.ungrab()
            self._grab_seat = None

    # ── 키 ──
    def _show_held(self):
        if self._held:
            self.big.set_text(" + ".join(kb.pretty(kb.combo(self._held, "x")).split(" + ")[:-1]) + " + …")

    def _press(self, _w, ev):
        kv = ev.keyval
        if kv in _IGNORE_KEYS:
            return True
        if kv in _MOD_KEYS:
            if not self._held:
                self._other = False
            self._held = _mods(ev.state) | {_MOD_KEYS[kv]}
            self._show_held()
            return True
        self._other = True
        mods = _mods(ev.state)
        if not mods and kv == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True
        if not mods and kv == Gdk.KEY_BackSpace:
            self.result = ("off", "")
            self.response(Gtk.ResponseType.OK)
            return True
        # 수식 키를 뺀 키 이름 — Shift+1 이 "exclam" 이 아니라 "1" 이 되게 (Hyprland 도 이렇게 맞춘다)
        keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        ok, base, *_ = keymap.translate_keyboard_state(ev.hardware_keycode, 0, ev.group)
        name = Gdk.keyval_name(base if ok and base else kv)
        if not name:
            return True
        c = kb.combo(mods, name)
        # 정하는 것은 그 키를 뗄 때 — 누를 때 닫으면 Hyprland 가 원래 단축키로 돌아온 뒤 Win 을 떼게 되어
        #   "Win 만 눌렀다 떼기"(시작 메뉴)로 잘못 읽을 수 있다. 떼는 동안 Hyprland 는 그 bindr 를 가린다
        self._pending = (c, ev.hardware_keycode) if self._check(c) else None
        return True

    def _release(self, _w, ev):
        kv = ev.keyval
        if kv in _MOD_KEYS:
            alone = self._held == {"SUPER"} and not self._other
            self._held.discard(_MOD_KEYS[kv])
            if alone and kv in (Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                c = kb.combo({"SUPER"}, Gdk.keyval_name(kv))       # Win 키만 눌렀다 뗐다
                if self._check(c):
                    self._done(c)
            elif self._held:
                self._show_held()
        elif self._pending and ev.hardware_keycode == self._pending[1]:
            self._done(self._pending[0])
        return True

    def _check(self, c):
        self.big.set_text(kb.pretty(c))
        err = self.check(c)
        self.err.set_text(err or "")
        self.err.set_visible(bool(err))
        return not err

    def _done(self, c):
        self.result = ("set", c)
        self.response(Gtk.ResponseType.OK)

    def run_capture(self):
        self.show_all()
        r = self.run()
        self._end()
        self.destroy()
        if r == 1:
            return ("default", "")
        return self.result if r == Gtk.ResponseType.OK else None


class ShortcutsPage:
    def __init__(self, store):
        self.store = store
        self.x11 = kb.x11_session()
        self.x11_keys = kb.x11_combos() if self.x11 else set()
        self.p = Page("단축키", "SekaiOS 전체에서 쓰는 키입니다. 오른쪽의 키를 눌러 바꾸세요 — "
                                "새 키를 누르면 바로 적용됩니다.")
        if self.x11:
            self.p.add_widget(_notice("기본 화면 모드입니다. 창 배치·가상 데스크톱 등 이 모드에 없는 기능의 단축키는 "
                                      "여기서 바꿔도 보통 화면 모드에서만 동작합니다."))

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("단축키 찾기 (이름이나 키)")
        self.search.get_style_context().add_class("kb-search")
        self.search.connect("search-changed", lambda *_: self._filter())
        self.p.add_widget(self.search)

        # 사용자 지정
        self.custom_title, self.custom_list = self._section("사용자 지정 단축키")
        self.sections = []                     # (제목, 리스트박스, [행]) — 행의 kb_text 로 찾는다
        self.rows = {}                         # 항목 id → (행, 키 단추, 기본값 단추)
        self._build_defaults()

        s = self.p.section("되돌리기")
        row(s, "모든 단축키를 기본값으로", "바꾼 키를 모두 처음대로 되돌립니다",
            icon=["edit-undo", "view-refresh"], control=button("되돌리기", self._reset_all))
        self.refresh()

    @property
    def widget(self):
        return self.p

    # ── 만들기 ──
    def _section(self, title):
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.get_style_context().add_class("section-title")
        self.p.box.pack_start(lbl, False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        self.p.box.pack_start(lb, False, False, 0)
        return lbl, lb

    def _build_defaults(self):
        groups = {}
        for e in kb.entries():
            if e.group not in groups:
                groups[e.group] = self._section(e.group) + ([],)
                self.sections.append(groups[e.group])
            _t, lb, items = groups[e.group]
            chip = Gtk.Button()
            chip.get_style_context().add_class("kb-chip")
            chip.connect("clicked", lambda _b, ent=e: self._change(ent))
            reset = button("기본값", lambda ent=e: self._to_default(ent), cls="kb-reset")
            reset.set_no_show_all(True)
            reset.set_tooltip_text(f"기본값 {kb.pretty(e.id)} 로 되돌리기")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.pack_start(reset, False, False, 0)
            box.pack_start(chip, False, False, 0)
            r = row(lb, e.desc, "·", control=box)
            r.sub_label.set_no_show_all(True)
            if e.lock:
                chip.set_sensitive(False)
                chip.set_tooltip_text(e.lock)
            self.rows[e.id] = (r, chip, reset)
            items.append(r)
        if not self.sections:
            self.p.add_widget(_notice("기본 단축키 목록(hyprland.conf)을 찾지 못했습니다."))

    # ── 지금 값으로 다시 그리기 ──
    def _sec(self):
        return kb.clean_section(self.store.get("keybinds"))

    def refresh(self):
        sec = self._sec()
        eff = kb.bindings(sec)
        ch = sec["changed"]
        for e, c in eff:
            if e.custom is not None or e.id not in self.rows:
                continue
            r, chip, reset = self.rows[e.id]
            chip.set_label(kb.pretty(c) if c else "없음")
            ctx = chip.get_style_context()
            (ctx.add_class if not c else ctx.remove_class)("kb-off")
            notes = []
            if e.lock:
                notes.append(e.lock)
            elif e.id in ch or not c:
                notes.append(f"기본값: {kb.pretty(e.id)}" + ("" if e.id in ch else
                                                             " — 다른 단축키가 이 키를 쓰고 있어 꺼졌습니다"))
            if self.x11 and kb.norm(e.id) not in self.x11_keys:
                notes.append("기본 화면 모드에서는 동작하지 않습니다")
            r.sub_label.set_text(" · ".join(notes))
            r.kb_text = f"{e.desc} {e.group} {kb.pretty(e.id)} {kb.pretty(c)}".lower()   # 기본 키·지금 키로 찾기
            r.sub_label.set_visible(bool(notes))
            reset.set_visible(not e.lock and (e.id in ch or not c))
        self._fill_custom(sec, eff)
        self._filter()

    def _fill_custom(self, sec, eff):
        for ch in self.custom_list.get_children():
            self.custom_list.remove(ch)
        self.custom_items = []
        keys = {e.custom: c for e, c in eff if e.custom is not None}
        for i, c in enumerate(sec["custom"]):
            chip = Gtk.Button(label=kb.pretty(keys.get(i, "")) if keys.get(i) else "없음")
            chip.get_style_context().add_class("kb-chip")
            if not keys.get(i):
                chip.get_style_context().add_class("kb-off")
            chip.connect("clicked", lambda _b, n=i: self._change_custom_key(n))
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.pack_start(button("편집", lambda n=i: self._edit_custom(n)), False, False, 0)
            box.pack_start(button("삭제", lambda n=i: self._delete_custom(n)), False, False, 0)
            box.pack_start(chip, False, False, 0)
            r = row(self.custom_list, c["name"], c["command"], icon=["utilities-terminal", "system-run"],
                    control=box)
            r.kb_text = f"{c['name']} {c['command']} {kb.pretty(c['key'])}".lower()
            self.custom_items.append(r)
        r = row(self.custom_list, "사용자 지정 단축키 추가", "원하는 키로 명령이나 앱을 실행합니다",
                icon=["list-add", "document-new"], control=button("추가…", self._add_custom))
        r.kb_text = "사용자 지정 단축키 추가 custom add"
        self.custom_items.append(r)
        self.custom_list.show_all()

    def _filter(self):
        q = self.search.get_text().strip().lower()
        for title, lb, items in [(self.custom_title, self.custom_list, self.custom_items)] + self.sections:
            any_on = False
            for r in items:
                on = not q or q in getattr(r, "kb_text", "")
                r.set_visible(on)
                any_on = any_on or on
            title.set_visible(any_on)
            lb.set_visible(any_on)

    # ── 바꾸기 ──
    def _owner(self, c, sec, skip_id=None, skip_custom=None):
        """조합 c 를 지금 쓰는 항목"""
        n = kb.norm(c)
        for e, cur in kb.bindings(sec):
            if cur and kb.norm(cur) == n and e.id != skip_id and \
                    not (skip_custom is not None and e.custom == skip_custom):
                return e
        return None

    def _checker(self, sec, skip_id=None, skip_custom=None):
        def check(c):
            mods, key = kb.split(c)
            if kb.norm(c) in kb.RESERVED:
                return kb.RESERVED[kb.norm(c)]
            if not kb.is_win_alone(c) and not (mods - {"SHIFT"}) and not kb.plain_ok(key):
                return "글자·숫자 키는 Win · Ctrl · Alt 중 하나와 함께 누르세요 (혼자 쓰면 글을 칠 수 없게 됩니다)"
            o = self._owner(c, sec, skip_id, skip_custom)
            if o is not None and o.lock:
                return f"'{o.desc}' 의 고정된 키입니다 — 다른 키를 누르세요"
            return None
        return check

    def _settle(self, c, sec, skip_id=None, skip_custom=None):
        """c 를 쓰려고 할 때 — 겹치면 묻고, 앱의 단축키를 가로챌 키면 한 번 더 묻는다.
        쓰기로 하면 겹치는 것을 끈 sec 를, 그만두면 None"""
        mods, key = kb.split(c)
        parent = _parent(self.p)
        o = self._owner(c, sec, skip_id, skip_custom)
        if o is not None and not _confirm(
                parent, f"{kb.pretty(c)} 는 이미 '{o.desc}' 에 쓰고 있습니다",
                f"바꾸면 '{o.desc}' 의 단축키는 꺼집니다. 나중에 그 항목에서 다른 키를 줄 수 있습니다.", "바꾸기"):
            return None
        if "SUPER" not in mods and "ALT" not in mods and "CTRL" in mods and len(key) == 1 and not _confirm(
                parent, f"{kb.pretty(c)} 는 앱들이 흔히 쓰는 키입니다",
                "단축키로 쓰면 모든 앱에서 이 키(복사·붙여넣기 등)가 먹히지 않습니다. 그래도 쓸까요?", "쓰기"):
            return None
        if o is not None:
            if o.custom is not None:
                sec["custom"][o.custom]["key"] = ""
            else:
                sec["changed"][o.id] = ""
        return sec

    def _apply(self, sec):
        self.store.set_keybinds(sec["changed"], sec["custom"])
        self.refresh()

    def _change(self, e):
        if e.lock:
            return
        sec = self._sec()
        cur = {x.id: c for x, c in kb.bindings(sec) if x.custom is None}.get(e.id, "")
        res = CaptureDialog(_parent(self.p), e.desc, cur, self._checker(sec, skip_id=e.id),
                            can_default=e.id in sec["changed"] or not cur).run_capture()
        if res is None:
            return
        kind, c = res
        if kind == "default":
            return self._to_default(e)
        if kind == "off":
            sec["changed"][e.id] = ""
        else:
            sec = self._settle(c, sec, skip_id=e.id)
            if sec is None:
                return
            if kb.norm(c) == kb.norm(e.id):
                sec["changed"].pop(e.id, None)
            else:
                sec["changed"][e.id] = c
        self._apply(sec)

    def _to_default(self, e):
        sec = self._sec()
        sec["changed"].pop(e.id, None)
        # 기본 키를 다른 것이 가져가 있었으면 — 그것을 끌지 묻는다
        sec = self._settle(e.id, sec, skip_id=e.id)
        if sec is not None:
            self._apply(sec)

    def _reset_all(self):
        sec = self._sec()
        if not sec["changed"] and not any(c["key"] for c in sec["custom"]):
            return
        if not _confirm(_parent(self.p), "모든 단축키를 기본값으로 되돌릴까요?",
                        "바꾼 키가 모두 처음대로 돌아갑니다. 사용자 지정 단축키는 남기되, "
                        "기본 단축키와 겹치는 키는 끕니다.", "되돌리기"):
            return
        base = {kb.norm(e.id) for e in kb.entries()}
        for c in sec["custom"]:
            if c["key"] and kb.norm(c["key"]) in base:
                c["key"] = ""
        self.store.set_keybinds({}, sec["custom"])
        self.refresh()

    # ── 사용자 지정 ──
    def _change_custom_key(self, i):
        sec = self._sec()
        if i >= len(sec["custom"]):
            return
        c = sec["custom"][i]
        res = CaptureDialog(_parent(self.p), c["name"], c["key"],
                            self._checker(sec, skip_custom=i)).run_capture()
        if res is None or res[0] == "default":
            return
        if res[0] == "off":
            c["key"] = ""
        else:
            sec = self._settle(res[1], sec, skip_custom=i)
            if sec is None:
                return
            sec["custom"][i]["key"] = res[1]
        self._apply(sec)

    def _delete_custom(self, i):
        sec = self._sec()
        if i >= len(sec["custom"]):
            return
        if not _confirm(_parent(self.p), f"'{sec['custom'][i]['name']}' 단축키를 삭제할까요?",
                        "이 단축키로 실행하던 명령은 더 이상 키로 부를 수 없습니다.", "삭제"):
            return
        del sec["custom"][i]
        self._apply(sec)

    def _add_custom(self):
        self._edit_custom(None)

    def _edit_custom(self, i):
        sec = self._sec()
        cur = sec["custom"][i] if i is not None and i < len(sec["custom"]) else \
            {"name": "", "command": "", "key": ""}
        d = Gtk.Dialog(title="사용자 지정 단축키" if i is None else "단축키 편집",
                       transient_for=_parent(self.p), modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "저장" if i is not None else "추가", Gtk.ResponseType.OK)
        ok = d.get_widget_for_response(Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        g = Gtk.Grid(column_spacing=12, row_spacing=8)
        name, cmd = Gtk.Entry(), Gtk.Entry()
        name.set_text(cur["name"])
        name.set_placeholder_text("예: 웹 브라우저")
        name.set_max_length(60)
        cmd.set_text(cur["command"])
        cmd.set_placeholder_text("예: chromium --incognito")
        cmd.set_width_chars(34)
        state = {"key": cur["key"]}
        chip = Gtk.Button(label=kb.pretty(cur["key"]) if cur["key"] else "키 누르기…")
        chip.get_style_context().add_class("kb-chip")
        for n, (t, w) in enumerate((("이름", name), ("실행할 명령", cmd), ("키", chip))):
            g.attach(Gtk.Label(label=t, xalign=0), 0, n, 1, 1)
            w.set_halign(Gtk.Align.START if w is chip else Gtk.Align.FILL)
            g.attach(w, 1, n, 1, 1)
        box.add(g)
        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("row-sub")
        err.set_line_wrap(True)
        box.add(err)

        def check(*_):
            msg = ""
            if not kb.clean_name(name.get_text()):
                msg = "이름을 적어 주세요"
            elif not cmd.get_text().strip():
                msg = "실행할 명령을 적어 주세요"
            elif not state["key"]:
                msg = "키를 눌러 단축키를 정하세요"
            err.set_text(msg)
            ok.set_sensitive(not msg)

        def pick(_b):
            res = CaptureDialog(d, kb.clean_name(name.get_text()) or "새 단축키", state["key"],
                                self._checker(sec, skip_custom=i)).run_capture()
            if res is None or res[0] == "default":
                return
            state["key"] = res[1] if res[0] == "set" else ""
            chip.set_label(kb.pretty(state["key"]) if state["key"] else "키 누르기…")
            check()
        chip.connect("clicked", pick)
        name.connect("changed", check)
        cmd.connect("changed", check)
        check()
        d.show_all()
        r = d.run()
        entry = {"name": kb.clean_name(name.get_text()), "command": " ".join(cmd.get_text().split("\n")).strip(),
                 "key": state["key"]}
        d.destroy()
        if r != Gtk.ResponseType.OK:
            return
        sec = self._settle(entry["key"], sec, skip_custom=i)
        if sec is None:
            return
        if i is None:
            sec["custom"].append(entry)
        else:
            sec["custom"][i] = entry
        self._apply(sec)


def _notice(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def build(store):
    return ShortcutsPage(store).widget


PAGES = [{"id": "shortcuts", "title": "단축키",
          "icon": ["preferences-desktop-keyboard-shortcuts", "input-keyboard",
                   "preferences-desktop-keyboard"],
          "build": build, "sections": ("keybinds",)}]
