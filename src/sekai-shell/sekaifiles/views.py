"""파일 탐색기 — 폴더 내용을 보이는 두 가지 보기.

  IconsView   — 큰 아이콘(96) · 보통 아이콘(48). Gtk.IconView: 끌어서 여러 개 고르기 · 방향키 이동
  DetailsView — 자세히. Gtk.TreeView (행 높이 고정 — 10,000줄이어도 가볍게): 이름 · 수정한 날짜 · 유형 · 크기
                (휴지통: 원래 위치 · 삭제한 날짜, 검색: 위치)

두 보기 모두 같은 모델(folder.FolderModel.store)을 보이고, 한 번에 하나만 모델에 붙는다.
보기는 창(host)에 알린다:
    host.view_activated()                 두 번 누름 · Enter
    host.view_selection_changed()
    host.view_context_menu(event)         오른쪽 단추 · 메뉴 키 (누른 항목은 이미 골라 둠)
    host.view_nav_button(n)               마우스의 뒤로(8) · 앞으로(9) 단추
    host.drag_uris()                      끌기를 시작할 때 보낼 URI 들 (고른 것)
    host.drag_started(uris) / host.drag_ended()
    host.drop_target(uri_or_None) …       놓기는 DropTarget 이 창의 drop_* 를 부른다
    host.header_clicked(field)            자세히 머리글 (정렬)
    host.scrolled()                       보이는 범위가 바뀜 (썸네일)
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .common import NameFitter  # noqa: E402
from .folder import (C_DATE, C_EXTRA, C_EXTRA2, C_NAME, C_PIX_L, C_PIX_M, C_PIX_S, C_SENS,  # noqa: E402
                     C_SHORT, C_SIZE, C_TYPE, PX_L, PX_M)

URI_TARGETS = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
DND_ACTIONS = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
MODS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK

# 아이콘 보기 모양 — (아이콘 크기, 항목 폭, 그림 열)
ICON_MODES = {
    "large": (PX_L, 112, C_PIX_L),
    "medium": (PX_M, 84, C_PIX_M),
}
ITEM_PAD = 4


def _range(r):
    """get_visible_range 의 돌려주는 모양이 PyGObject 판마다 다르다 — (시작, 끝) 번호로"""
    if not r:
        return None
    if len(r) == 3:
        ok, a, b = r
        if not ok:
            return None
    else:
        a, b = r
    if a is None or b is None:
        return None
    return a.get_indices()[0], b.get_indices()[0]


class DropTarget:
    """파일을 놓을 수 있는 위젯 — 어디에 놓는지(resolve)만 위젯이 정하고 나머지는 창이 한다.
    resolve(x, y) → (놓을 곳 URI 또는 None, 강조 표시 함수 또는 None)"""

    def __init__(self, widget, host, resolve, unhighlight):
        self.w = widget
        self.host = host
        self.resolve = resolve
        self.unhighlight = unhighlight
        self._dest = None
        widget.drag_dest_set(0, URI_TARGETS, DND_ACTIONS)
        widget.connect("drag-motion", self._motion)
        widget.connect("drag-leave", self._leave)
        widget.connect("drag-drop", self._drop)
        widget.connect("drag-data-received", self._received)

    def _motion(self, w, ctx, x, y, t):
        dest, hl = self.resolve(x, y)
        act = self.host.drop_action(ctx, dest) if dest else 0
        if act:
            if hl:
                hl()
            else:
                self.unhighlight()
        else:
            self.unhighlight()
        Gdk.drag_status(ctx, act, t)
        return True

    def _leave(self, *_):
        self.unhighlight()

    def _drop(self, w, ctx, x, y, t):
        self.unhighlight()
        dest, _hl = self.resolve(x, y)
        target = w.drag_dest_find_target(ctx, None)
        if not dest or target is None:
            Gtk.drag_finish(ctx, False, False, t)
            return True
        self._dest = (dest, self.host.drop_action(ctx, dest), self.host.pointer_mods())
        w.drag_get_data(ctx, target, t)
        return True

    def _received(self, _w, ctx, _x, _y, data, _info, t):
        got = self._dest
        self._dest = None
        uris = data.get_uris() if data is not None else None
        if not got or not uris:
            Gtk.drag_finish(ctx, False, False, t)
            return
        dest, act, mods = got
        external = Gtk.drag_get_source_widget(ctx) is None
        ok = self.host.drop_files(uris, dest, act, mods, external)
        # 옮기기여도 끌어 온 쪽에 지우라고(del) 하지 않는다 — 옮기기는 우리가 직접 한다
        Gtk.drag_finish(ctx, bool(ok), False, t)


def autoscroll(scroll, y):
    """끌어 놓는 중 위·아래 끝에 가까우면 조금씩 굴린다"""
    adj = scroll.get_vadjustment()
    h = scroll.get_allocated_height()
    step = 0
    if y < 32:
        step = -24
    elif y > h - 32:
        step = 24
    if step:
        adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(), adj.get_value() + step)))


class _Base:
    """두 보기가 함께 쓰는 것 — 마우스·끌기"""

    def _setup_common(self):
        v = self.view
        v.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, URI_TARGETS, DND_ACTIONS)
        v.connect("drag-begin", self._drag_begin)
        v.connect("drag-data-get", self._drag_get)
        v.connect("drag-data-delete", lambda w, _c: w.stop_emission_by_name("drag-data-delete"))
        v.connect("drag-end", self._drag_end)
        v.connect("button-press-event", self._press)
        v.connect("button-release-event", self._release)
        v.connect("popup-menu", lambda *_: (self.host.view_context_menu(None), True)[1])
        self.drop = DropTarget(v, self.host, self._resolve_drop, self._unhighlight)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(v)
        self.scroll.get_vadjustment().connect("value-changed", lambda *_: self.host.scrolled())
        v.connect("size-allocate", lambda *_: self.host.scrolled())
        self._pressed = None                   # (자리, 누를 때 고른 것) — 여러 개를 끌 수 있게
        self._dragging = False
        self._drag_payload = []

    # 끌기
    def _drag_begin(self, w, ctx):
        self._dragging = True
        if self._pressed is not None:
            pos, before = self._pressed
            if pos in before and len(before) > 1 and set(self.selected_positions()) != set(before):
                self.select_positions(before, scroll=False)   # 누를 때 풀린 고름을 되살려 모두 끈다
        self._drag_payload = self.host.drag_uris()
        self.host.drag_started(self._drag_payload)

    def _drag_get(self, _w, _ctx, data, _info, _t):
        if self._drag_payload:
            data.set_uris(self._drag_payload)

    def _drag_end(self, *_):
        self._dragging = False
        self._pressed = None
        self._drag_payload = []
        self.host.drag_ended()

    def _resolve_drop(self, x, y):
        autoscroll(self.scroll, y)
        pos = self.pos_at_widget(x, y)
        if pos is not None:
            uri = self.host.folder_uri_at(pos)
            if uri:
                return uri, (lambda p=pos: self._highlight(p))
        return self.host.current_drop_uri(), None

    # 마우스
    def _press(self, w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button in (8, 9):
            self.host.view_nav_button(ev.button)
            return True
        bw = self._bin_window()
        if bw is not None and ev.window != bw:
            return False                        # 자세히 보기의 머리글
        pos = self.pos_at_bin(ev.x, ev.y)
        if ev.button == 2 and pos is not None:
            e = self.host.model.entry_at(pos) if self.host.model is not None else None
            if e is not None and e.is_dir:
                self.host.open_new_tab(e.target or e.uri)   # 가운데 단추 — 폴더를 새 탭에서 (뒤에서)
            return True
        if ev.button == 3:
            w.grab_focus()
            if pos is None:
                self.unselect_all()
            elif pos not in self.selected_positions():
                self.select_positions([pos], cursor=pos, scroll=False)
            self.host.view_context_menu(ev)
            return True
        if ev.button == 1:
            self._pressed = None
            self._press_hook(pos)
            if pos is None:
                if not ev.state & MODS:
                    self.unselect_all()
                return False
            sel = self.selected_positions()
            if not ev.state & MODS and pos in sel and len(sel) > 1:
                self._pressed = (pos, sel)
                return self._hold_selection()
        return False

    def _release(self, w, ev):
        if ev.button != 1:
            return False
        self._release_selection()
        p, self._pressed = self._pressed, None
        if p is not None and not self._dragging:
            pos, _before = p
            # 누른 채 끌지 않았으면 윈도우처럼 누른 것 하나만 남긴다
            self.select_positions([pos], cursor=pos, scroll=False)
        return False

    # 이름 바꾸기 칸 — 두 보기가 함께
    def _begin_edit(self, editable):
        if self._rename is None or not isinstance(editable, Gtk.Entry):
            return False
        name, sel_len, _done = self._rename
        self._editable = editable
        self._esc = False
        editable.set_text(name)
        editable.get_style_context().add_class("fx-rename")
        editable.connect("key-press-event", self._edit_key)

        def sel():
            editable.select_region(0, sel_len)
            return False
        GLib.idle_add(sel)
        return True

    def _edit_key(self, _e, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self._esc = True
        return False

    def _end_edit(self, text):
        """GTK 는 창이 초점을 잃어도 '취소'로 끝낸다 — 윈도우처럼 Esc 가 아니면 친 이름을 적용한다"""
        r, self._rename = self._rename, None
        ed, self._editable = getattr(self, "_editable", None), None
        if r is None:
            return
        if text is None and ed is not None and not getattr(self, "_esc", False):
            t = ed.get_text()
            if t and t != r[0]:
                text = t
        r[2](text)

    def _hold_selection(self):
        return False

    def _press_hook(self, pos):
        pass

    def _release_selection(self):
        pass


class IconsView(_Base):
    def __init__(self, host, fitter):
        self.host = host
        v = self.view = Gtk.IconView()
        self.fitter = fitter or NameFitter(v)   # 이름을 이 보기의 글꼴로 잰다
        v.get_style_context().add_class("fx-icons")
        v.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        v.set_item_orientation(Gtk.Orientation.VERTICAL)
        v.set_activate_on_single_click(False)
        v.set_columns(-1)
        v.set_margin(10)
        v.set_row_spacing(4)
        v.set_column_spacing(4)
        v.set_item_padding(ITEM_PAD)
        v.set_spacing(4)
        v.set_can_focus(True)
        self.pix = Gtk.CellRendererPixbuf()
        self.pix.set_alignment(0.5, 1.0)
        self.txt = Gtk.CellRendererText()
        self.txt.set_alignment(0.5, 0.0)
        self.txt.props.alignment = Pango.Alignment.CENTER
        self.txt.props.wrap_mode = Pango.WrapMode.WORD_CHAR
        v.pack_start(self.pix, False)
        v.pack_start(self.txt, False)
        self.txt.connect("editing-started", self._edit_started)
        self.txt.connect("edited", self._edited)
        self.txt.connect("editing-canceled", self._edit_canceled)
        v.connect("item-activated", lambda *_: self.host.view_activated())
        v.connect("selection-changed", lambda *_: self.host.view_selection_changed())
        v.connect("style-updated", self._restyle)
        self._line_h = 0
        self.mode = None
        self._rename = None
        self._setup_common()
        self.set_mode("large")

    def set_mode(self, mode):
        if mode == self.mode:
            return False
        self.mode = mode
        px, width, col = ICON_MODES[mode]
        v = self.view
        v.clear_attributes(self.pix)
        v.add_attribute(self.pix, "pixbuf", col)
        v.add_attribute(self.pix, "sensitive", C_SENS)
        v.clear_attributes(self.txt)
        v.add_attribute(self.txt, "text", C_SHORT)
        self.pix.set_fixed_size(px, px)
        wrap = width - 2 * ITEM_PAD - 4
        self.txt.props.wrap_width = wrap
        # 글 칸 크기를 못 박는다 — 그러지 않으면 IconView 가 배치할 때마다 모든 항목의 글을 재서
        #   10,000개 폴더에서 1초 가까이 멈췄다. 이름은 이미 두 줄로 잘라 넣는다
        self._line_h = self.fitter.line_height()
        xpad, ypad = self.txt.get_padding()
        self.txt.set_fixed_size(wrap + 2 * xpad, self._line_h + 2 * ypad)
        v.set_item_width(width)
        self.fitter.set_width(wrap)
        return True

    def _restyle(self, *_):
        """글꼴이 바뀌면 (설정 · 창을 처음 띄울 때) 글 칸 높이와 두 줄 이름을 다시"""
        if self.mode is None:
            return
        h = self.fitter.line_height()
        if h == self._line_h:
            return
        self._line_h = h
        xpad, ypad = self.txt.get_padding()
        self.txt.set_fixed_size(self.txt.get_fixed_size()[0], h + 2 * ypad)
        self.fitter.style_changed()
        self.host.scrolled()

    # 모델 · 고르기
    def set_model(self, store):
        self.view.set_model(store)

    def _bin_window(self):
        return None

    def _press(self, w, ev):
        # IconView 는 빈 곳을 누르면 스스로 고름을 풀고 끌어서 고르기를 시작한다
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button in (8, 9):
            self.host.view_nav_button(ev.button)
            return True
        pos = self.pos_at_bin(ev.x, ev.y)
        if ev.button == 2 and pos is not None:
            e = self.host.model.entry_at(pos) if self.host.model is not None else None
            if e is not None and e.is_dir:
                self.host.open_new_tab(e.target or e.uri)   # 가운데 단추 — 폴더를 새 탭에서 (뒤에서)
            return True
        if ev.button == 3:
            w.grab_focus()
            if pos is None:
                self.unselect_all()
            elif pos not in self.selected_positions():
                self.select_positions([pos], cursor=pos, scroll=False)
            self.host.view_context_menu(ev)
            return True
        if ev.button == 1:
            self._pressed = None
            if pos is None:
                if not ev.state & MODS:
                    self.unselect_all()         # 많이 골랐을 때 빨리 (나머지는 IconView 가 — 끌어서 고르기)
                return False
            sel = self.selected_positions()
            if not ev.state & MODS and pos in sel and len(sel) > 1:
                self._pressed = (pos, sel)
        return False

    def selected_positions(self):
        return sorted(p.get_indices()[0] for p in self.view.get_selected_items())

    def _quiet(self, fn):
        """고른 항목이 아주 많을 때 IconView 는 항목마다 다시 그리기를 부탁해 느려진다 (영역 합치기가 n²) —
        잠깐 숨겼다가 한 번에 다시 그린다"""
        if len(self.view.get_selected_items()) > 800 and self.view.get_mapped():
            self.view.hide()
            try:
                fn()
            finally:
                self.view.show()
        else:
            fn()

    def select_positions(self, positions, cursor=None, scroll=True):
        v = self.view
        self._quiet(v.unselect_all)
        if cursor is None and positions:
            cursor = positions[0]
        if cursor is not None:
            v.set_cursor(Gtk.TreePath.new_from_indices([cursor]), None, False)
        model = v.get_model()
        if model is not None and positions and len(positions) >= model.iter_n_children(None):
            v.select_all()                     # 모두 — 한 줄씩 고르면 10,000개에서 느리다
        else:
            for pos in positions:
                v.select_path(Gtk.TreePath.new_from_indices([pos]))
        if scroll and cursor is not None:
            self.scroll_to(cursor)

    def select_all(self):
        self.view.select_all()

    def unselect_all(self):
        self._quiet(self.view.unselect_all)

    def cursor_pos(self):
        ok, path, _cell = self._cursor()
        return path.get_indices()[0] if ok and path is not None else None

    def _cursor(self):
        r = self.view.get_cursor()
        if r is None:
            return False, None, None
        if len(r) == 3:
            return r
        return True, r[0], r[1]

    def scroll_to(self, pos):
        self.view.scroll_to_path(Gtk.TreePath.new_from_indices([pos]), False, 0, 0)

    def pos_at_bin(self, x, y):
        p = self.view.get_path_at_pos(int(x), int(y))
        return p.get_indices()[0] if p is not None else None

    def pos_at_widget(self, x, y):
        bx, by = self.view.convert_widget_to_bin_window_coords(int(x), int(y))
        return self.pos_at_bin(bx, by)

    def visible_range(self):
        return _range(self.view.get_visible_range())

    def _highlight(self, pos):
        self.view.set_drag_dest_item(Gtk.TreePath.new_from_indices([pos]), Gtk.IconViewDropPosition.DROP_INTO)

    def _unhighlight(self):
        self.view.set_drag_dest_item(None, Gtk.IconViewDropPosition.DROP_INTO)

    def cell_rect(self, pos):
        """메뉴 키로 연 메뉴를 놓을 자리 (위젯 좌표)"""
        ok, rect = self.view.get_cell_rect(Gtk.TreePath.new_from_indices([pos]), None)
        if not ok:
            return None
        return rect

    # 이름 바꾸기 — 그 자리에서 (확장명 앞까지 골라 둔 채)
    def start_rename(self, pos, name, sel_len, done):
        self._rename = (name, sel_len, done)
        self.txt.props.editable = True
        path = Gtk.TreePath.new_from_indices([pos])
        self.view.scroll_to_path(path, False, 0, 0)
        self.view.grab_focus()
        self.view.set_cursor(path, self.txt, True)

    def _edit_started(self, _cell, editable, _path):
        if self._begin_edit(editable):
            editable.set_alignment(0.5)

    def _edited(self, _cell, _path, text):
        self.txt.props.editable = False
        self._end_edit(text)

    def _edit_canceled(self, _cell):
        self.txt.props.editable = False
        self._end_edit(None)

    def editing(self):
        return self._rename is not None


# 자세히 보기의 열 — (id, 머리글, 모델 열, 폭, 오른쪽 정렬, 정렬 항목)
DETAIL_COLUMNS = [
    ("name", "이름", C_NAME, 320, False, "name"),
    ("orig", "원래 위치", C_EXTRA, 240, False, "orig"),
    ("dtime", "삭제한 날짜", C_EXTRA2, 160, False, "dtime"),
    ("date", "수정한 날짜", C_DATE, 160, False, "mtime"),
    ("type", "유형", C_TYPE, 150, False, "type"),
    ("size", "크기", C_SIZE, 90, True, "size"),
    ("loc", "위치", C_EXTRA, 300, False, "loc"),
]
DETAIL_SETS = {
    "folder": ("name", "date", "type", "size"),
    "trash": ("name", "orig", "dtime", "size"),
    "search": ("name", "date", "type", "size", "loc"),
}


class DetailsView(_Base):
    def __init__(self, host, widths=None):
        self.host = host
        v = self.view = Gtk.TreeView()
        v.get_style_context().add_class("fx-details")
        v.set_enable_search(False)             # 글자를 치면 창이 그 이름으로 건너뛴다 (윈도우처럼)
        v.set_fixed_height_mode(True)
        v.set_rubber_banding(True)
        v.set_headers_clickable(True)
        v.set_activate_on_single_click(False)
        v.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        v.get_selection().connect("changed", lambda *_: self.host.view_selection_changed())
        v.connect("row-activated", lambda *_: self.host.view_activated())
        self.cols = {}
        widths = widths or {}
        for cid, title, col, width, right, field in DETAIL_COLUMNS:
            c = Gtk.TreeViewColumn()
            c.set_title(title)
            if cid == "name":
                ir = Gtk.CellRendererPixbuf()
                ir.set_padding(6, 0)
                c.pack_start(ir, False)
                c.add_attribute(ir, "pixbuf", C_PIX_S)
                c.add_attribute(ir, "sensitive", C_SENS)
                self.name_cell = r = Gtk.CellRendererText()
                r.connect("editing-started", self._edit_started)
                r.connect("edited", self._edited)
                r.connect("editing-canceled", self._edit_canceled)
            else:
                r = Gtk.CellRendererText(xalign=1.0 if right else 0.0)
            r.set_padding(6, 4)
            r.props.ellipsize = Pango.EllipsizeMode.END
            c.pack_start(r, True)
            c.add_attribute(r, "text", col)
            if cid in ("date", "type", "size", "orig", "dtime", "loc"):
                c.add_attribute(r, "sensitive", C_SENS)
            c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            try:
                w = int(widths.get(cid, width))
            except (TypeError, ValueError):
                w = width
            c.set_fixed_width(max(48, w))
            c.set_min_width(48)
            c.set_resizable(True)
            c.set_alignment(1.0 if right else 0.0)
            c.set_clickable(True)
            c.connect("clicked", lambda _c, f=field: self.host.header_clicked(f))
            c.field = field
            c.cid = cid
            v.append_column(c)
            self.cols[cid] = c
        # 오른쪽 끝을 채울 빈 열 (마지막 열이 창 폭만큼 늘어나지 않게)
        filler = Gtk.TreeViewColumn()
        filler.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        filler.set_fixed_width(24)             # 머리글 단추의 여백보다 넓게 (1 이면 GTK 가 경고한다)
        filler.set_expand(True)
        v.append_column(filler)
        self.kind = None
        self._rename = None
        self._allow_select = True
        v.get_selection().set_select_function(lambda *_a: self._allow_select)
        self._setup_common()
        self.set_kind("folder")

    def set_kind(self, kind):
        if kind == self.kind:
            return
        self.kind = kind
        show = DETAIL_SETS.get(kind, DETAIL_SETS["folder"])
        for cid, c in self.cols.items():
            c.set_visible(cid in show)
        # 보이는 순서도 그 묶음 순서대로
        prev = None
        for cid in show:
            c = self.cols[cid]
            self.view.move_column_after(c, prev)
            prev = c

    def widths(self):
        return {cid: c.get_width() for cid, c in self.cols.items() if c.get_visible() and c.get_width() > 0}

    def set_sort_indicator(self, field, desc):
        for c in self.cols.values():
            on = c.field == field
            c.set_sort_indicator(on)
            if on:
                c.set_sort_order(Gtk.SortType.DESCENDING if desc else Gtk.SortType.ASCENDING)

    def set_model(self, store):
        self.view.set_model(store)

    def _bin_window(self):
        return self.view.get_bin_window()

    def _press_hook(self, pos):
        # 줄 위에서 누르고 끌면 그 파일을 끈다 (윈도우처럼) — GTK 는 고르지 않은 줄에서 끌면 끌어서 고르기를
        #   시작하므로, 줄 위에서는 끄고 빈 곳에서만 켠다
        self.view.set_rubber_banding(pos is None)

    # 여러 줄을 끌 때 — 이미 고른 줄을 누르면 놓을 때까지 고름을 바꾸지 않는다
    def _hold_selection(self):
        self._allow_select = False
        return False

    def _release_selection(self):
        self._allow_select = True

    def _drag_end(self, *a):
        self._allow_select = True
        super()._drag_end(*a)

    def selected_positions(self):
        _m, paths = self.view.get_selection().get_selected_rows()
        return sorted(p.get_indices()[0] for p in paths)

    def select_positions(self, positions, cursor=None, scroll=True):
        self._allow_select = True
        sel = self.view.get_selection()
        sel.unselect_all()
        if cursor is None and positions:
            cursor = positions[0]
        if cursor is not None:
            self.view.set_cursor(Gtk.TreePath.new_from_indices([cursor]), None, False)
            sel.unselect_all()                 # set_cursor 가 커서 줄만 고르므로 다시
        # 이어진 줄은 한 번에 (10,000개를 한 줄씩 고르면 1초 가까이 걸린다)
        run = None
        for pos in sorted(set(positions)) + [None]:
            if run is not None and pos == run[1] + 1:
                run[1] = pos
                continue
            if run is not None:
                if run[0] == run[1]:
                    sel.select_path(Gtk.TreePath.new_from_indices([run[0]]))
                else:
                    sel.select_range(Gtk.TreePath.new_from_indices([run[0]]),
                                     Gtk.TreePath.new_from_indices([run[1]]))
            run = [pos, pos] if pos is not None else None
        if scroll and cursor is not None:
            self.scroll_to(cursor)

    def select_all(self):
        self.view.get_selection().select_all()

    def unselect_all(self):
        self.view.get_selection().unselect_all()

    def cursor_pos(self):
        path, _col = self.view.get_cursor()
        return path.get_indices()[0] if path is not None else None

    def scroll_to(self, pos):
        self.view.scroll_to_cell(Gtk.TreePath.new_from_indices([pos]), None, False, 0, 0)

    def pos_at_bin(self, x, y):
        hit = self.view.get_path_at_pos(int(x), int(y))
        if not hit:
            return None
        return hit[0].get_indices()[0]

    def pos_at_widget(self, x, y):
        bx, by = self.view.convert_widget_to_bin_window_coords(int(x), int(y))
        if by < 0:
            return None
        return self.pos_at_bin(bx, by)

    def visible_range(self):
        return _range(self.view.get_visible_range())

    def _highlight(self, pos):
        self.view.set_drag_dest_row(Gtk.TreePath.new_from_indices([pos]), Gtk.TreeViewDropPosition.INTO_OR_AFTER)

    def _unhighlight(self):
        self.view.set_drag_dest_row(None, Gtk.TreeViewDropPosition.INTO_OR_AFTER)

    def cell_rect(self, pos):
        rect = self.view.get_cell_area(Gtk.TreePath.new_from_indices([pos]), self.cols["name"])
        wx, wy = self.view.convert_bin_window_to_widget_coords(rect.x, rect.y)
        rect.x, rect.y = wx + 24, wy
        return rect

    # 이름 바꾸기
    def start_rename(self, pos, name, sel_len, done):
        self._rename = (name, sel_len, done)
        self.name_cell.props.editable = True
        path = Gtk.TreePath.new_from_indices([pos])
        self.view.grab_focus()
        self.view.set_cursor_on_cell(path, self.cols["name"], self.name_cell, True)

    def _edit_started(self, _cell, editable, _path):
        self._begin_edit(editable)

    def _edited(self, _cell, _path, text):
        self.name_cell.props.editable = False
        self._end_edit(text)

    def _edit_canceled(self, _cell):
        self.name_cell.props.editable = False
        self._end_edit(None)

    def editing(self):
        return self._rename is not None
