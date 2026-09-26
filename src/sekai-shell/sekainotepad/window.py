"""메모장 — 창 하나 (탭 줄 · 메뉴 줄 · 찾기 막대 · 글 · 상태 표시줄 · 설정 페이지).

탭 머리는 Gtk.Notebook(빈 page — 끌어서 순서 바꾸기·넘칠 때 화살표는 GTK 가), 글 칸은 그 아래 Gtk.Stack.
이렇게 나눠야 윈도우 11 메모장처럼 탭 줄 → 메뉴 줄 → 글 순서가 된다 (Notebook 은 탭 바로 아래에 page 를 둔다).

메뉴·단축키는 Gio 동작(win.*) — 메뉴의 단축키 표시와 실제 키가 한곳(app.py 의 ACCELS)에서 온다.
묻는 창은 모두 비동기(응답 콜백)라 "닫기 → 저장할까요? → 다른 이름으로 저장" 이 줄줄이 이어진다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import textcodec  # noqa: E402
from .common import (APP_ICONS, APP_NAME, BIG_FILE, UNTITLED, ZOOM_STEP, ask, human_size,  # noqa: E402
                     icon_button, io_error_text, notice, time_date_text)
from .document import Document  # noqa: E402
from .findbar import FindBar  # noqa: E402
from .settingspage import SettingsPage  # noqa: E402

YES, NO, CANCEL = Gtk.ResponseType.YES, Gtk.ResponseType.NO, Gtk.ResponseType.CANCEL
SAVE, DISCARD = Gtk.ResponseType.ACCEPT, Gtk.ResponseType.REJECT
# 탭 폭 (px, 탭 머리 여백 포함) — 제목만큼(최대 TAB_MAX), 자리가 모자라면 고르게 줄고,
#   TAB_MIN 보다 좁아져야 하면 탭 줄을 화살표로 넘기는 방식으로
TAB_MIN, TAB_MAX, TAB_PAD = 96, 240, 22


class NotepadWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self._docs = {}                              # 탭의 빈 page → Document
        self.closing = False                         # 닫는 중 (저장 여부를 묻는 동안 다시 닫기를 눌러도 한 번만)
        self._closed = False
        self._cur = None
        self._maximized = False
        self._fit_src = 0
        self.set_icon_name(APP_ICONS[0])
        p = app.prefs
        size = p.get("size") if isinstance(p.get("size"), list) else [900, 640]
        try:
            self.set_default_size(max(420, int(size[0])), max(300, int(size[1])))
        except (TypeError, ValueError, IndexError):
            self.set_default_size(900, 640)
        # 최대화 여부는 "max" 로 — 옛 "maximized" 는 버린다: Hyprland sekai11 전에는 모든 창에 "최대화됨"이
        #   붙어 늘 true 로 저장됐고, 이제는 그 값대로 최대화해 열리므로 한 번도 최대화하지 않은 창이 최대화로 열렸다
        p.pop("maximized", None)
        if p.get("max"):
            self.maximize()
        self.set_size_request(360, 260)
        self.get_style_context().add_class("np-window")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.pack_start(self._build_strip(), False, False, 0)

        self.body = Gtk.Stack()
        self.body.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.body.set_transition_duration(90)
        root.pack_start(self.body, True, True, 0)
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_named(editor, "editor")
        self.settings_page = None

        menurow = Gtk.Box()
        menurow.get_style_context().add_class("np-menurow")
        self.menubar = Gtk.MenuBar.new_from_model(app.menu_model)
        self.menubar.get_style_context().add_class("np-menubar")
        menurow.pack_start(self.menubar, False, False, 0)
        menurow.pack_end(icon_button(["preferences-system-symbolic", "emblem-system-symbolic",
                                      "applications-system-symbolic"], "설정", self.show_settings, 16),
                         False, False, 0)
        editor.pack_start(menurow, False, False, 0)

        self.findbar = FindBar(self)
        editor.pack_start(self.findbar, False, False, 0)
        self.docstack = Gtk.Stack()
        editor.pack_start(self.docstack, True, True, 0)
        editor.pack_start(self._build_status(), False, False, 0)

        self._setup_actions()
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self.connect("window-state-event", self._on_wstate)
        self.connect("focus-in-event", self._on_focus_in)
        # 탭 줄·메뉴·상태 표시줄에 파일을 놓아도 연다 (글 칸은 Document 가 따로 받는다)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-data-received", self._on_drag_data)
        self.apply_prefs()

    # ── 만들기 ──
    def _build_strip(self):
        strip = self.strip = Gtk.Box()
        strip.get_style_context().add_class("np-strip")
        strip.connect("size-allocate", lambda *_: self._queue_fit())
        nb = self.notebook = Gtk.Notebook()
        nb.set_scrollable(False)
        nb.set_show_border(False)
        nb.set_can_focus(False)
        nb.get_style_context().add_class("np-tabs")
        nb.connect_after("switch-page", self._on_switch)      # 뒤에 — 그때 current() 가 새 탭이다
        nb.connect("button-press-event", self._on_tabs_press)
        # 탭 폭만큼만 차지해 "+" 가 마지막 탭 바로 옆에 붙는다. 탭이 많아지면 탭이 좁아지고(제목 줄임),
        #   그래도 넘치면 화살표로 넘기는 방식으로 바꾼다 (_fit_tabs — GTK3 Notebook 은 탭을 최소 폭으로만 재고,
        #   넘기기 방식을 늘 켜 두면 탭이 하나만 보인다)
        strip.pack_start(nb, False, True, 0)
        strip.pack_start(icon_button(["list-add-symbolic"], "새 탭 (Ctrl+N)", lambda: self.new_doc(), 16,
                                     css="np-newtab"), False, False, 0)
        filler = self._filler = Gtk.EventBox()
        filler.set_visible_window(False)
        filler.connect("button-press-event", self._on_strip_press)
        strip.pack_start(filler, True, True, 0)
        return strip

    def _build_status(self):
        bar = self.statusbar = Gtk.Box()
        bar.get_style_context().add_class("np-status")
        bar.set_no_show_all(True)
        self.st_pos = Gtk.Label(label="줄 1, 열 1", xalign=0)
        self.st_pos.set_width_chars(16)
        self.st_chars = Gtk.Label(xalign=0)
        bar.pack_start(self.st_pos, False, False, 0)
        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        bar.pack_start(self.st_chars, False, False, 0)
        self.st_enc = Gtk.Label(xalign=0)
        self.st_enc.set_width_chars(11)
        self.st_eol = Gtk.Label(xalign=0)
        self.st_eol.set_width_chars(14)
        self.st_zoom = Gtk.Label(xalign=0)
        self.st_zoom.set_width_chars(5)
        for w in (self.st_enc, Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), self.st_eol,
                  Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), self.st_zoom,
                  Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)):
            bar.pack_end(w, False, False, 0)
        for w in bar.get_children():
            w.show()
        return bar

    def _setup_actions(self):
        simple = {
            "new-tab": lambda: self.new_doc(),
            "new-window": lambda: self.app.new_window(),
            "open": self.open_dialog,
            "save": lambda: self.save(self.current()),
            "save-as": lambda: self.save(self.current(), save_as=True),
            "save-all": self.save_all,
            "close-tab": lambda: self.close_doc(self.current()),
            "close-window": self.close,
            "exit": self.app.quit_all,
            "undo": lambda: self._edit("undo"),
            "redo": lambda: self._edit("redo"),
            "cut": lambda: self._edit("cut"),
            "copy": lambda: self._edit("copy"),
            "paste": lambda: self._edit("paste"),
            "delete": lambda: self._edit("delete"),
            "select-all": lambda: self._edit("select-all"),
            "find": lambda: self.findbar.open(False),
            "replace": lambda: self.findbar.open(True),
            "find-next": lambda: self.findbar.find(False),
            "find-prev": lambda: self.findbar.find(True),
            "goto": self.goto_dialog,
            "time-date": self.insert_time_date,
            "font": self.show_settings,
            "zoom-in": lambda: self._zoom(+ZOOM_STEP),
            "zoom-out": lambda: self._zoom(-ZOOM_STEP),
            "zoom-reset": lambda: self._zoom(0),
            "clear-recent": self.app.clear_recent,
        }
        for name, cb in simple.items():
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        a = Gio.SimpleAction.new("open-recent", GLib.VariantType.new("s"))
        a.connect("activate", lambda _a, p: self.open_paths([p.get_string()]))
        self.add_action(a)
        for name in ("wrap", "statusbar"):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", True))
            a.connect("change-state", lambda act, v, n=name: self.app.set_pref(n, v.get_boolean()))
            self.add_action(a)

    def run_action(self, name):
        a = self.lookup_action(name)
        if a is not None and a.get_enabled():
            a.activate(None)

    # ── 설정 적용 ──
    def apply_prefs(self):
        p = self.app.prefs
        wrap = bool(p.get("wrap", True))
        font = self.app.font()
        for d in self.docs():
            d.apply_prefs(font, wrap)
        self.lookup_action("wrap").set_state(GLib.Variant("b", wrap))
        st = bool(p.get("statusbar", True))
        self.lookup_action("statusbar").set_state(GLib.Variant("b", st))
        self.statusbar.set_visible(st)
        if self.settings_page is not None:
            self.settings_page.refresh()

    def show_settings(self):
        if self.settings_page is None:
            self.settings_page = SettingsPage(self)
            self.settings_page.show_all()
            self.body.add_named(self.settings_page, "settings")
        self.settings_page.refresh()
        self.body.set_visible_child_name("settings")
        self.settings_page.grab_focus()

    def show_editor(self):
        self.body.set_visible_child_name("editor")
        d = self.current()
        if d is not None:
            d.view.grab_focus()

    # ── 탭 ──
    def docs(self):
        """탭 순서대로"""
        out = []
        for i in range(self.notebook.get_n_pages()):
            d = self._docs.get(self.notebook.get_nth_page(i))
            if d is not None:
                out.append(d)
        return out

    def current(self):
        n = self.notebook.get_current_page()
        return self._docs.get(self.notebook.get_nth_page(n)) if n >= 0 else None

    def _queue_fit(self):
        if not self._fit_src:
            self._fit_src = GLib.idle_add(self._fit_tabs)

    def _fit_tabs(self, extra=0):
        self._fit_src = 0
        avail = self.strip.get_allocated_width() - 56          # "+" 단추와 여백
        if avail <= 0:
            return False
        docs = self.docs()
        n = len(docs) + extra
        scroll = n * TAB_MIN > avail
        if scroll != self.notebook.get_scrollable():
            self.notebook.set_scrollable(scroll)
            self.strip.child_set_property(self.notebook, "expand", scroll)
            self._filler.set_visible(not scroll)          # 넘기기 방식이면 탭 줄이 끝까지 (빈 칸과 나누지 않게)
        share = TAB_MIN if scroll else max(TAB_MIN, avail // max(1, n))
        for d in docs:
            natural = min(TAB_MAX, d.tab.natural_width() + TAB_PAD)
            inner = min(natural, share) - TAB_PAD
            if d.tab.get_size_request()[0] != inner:          # 같으면 건드리지 않는다 (다시 재기 → 다시 맞추기 고리)
                d.tab.set_size_request(inner, -1)
        return False

    def new_doc(self, switch=True):
        self._fit_tabs(extra=1)          # 붙이기 전에 — 탭 줄이 창을 넓히지 않게
        doc = Document(self)
        doc.apply_prefs(self.app.font(), bool(self.app.prefs.get("wrap", True)))
        doc.widget.show_all()
        self.docstack.add_named(doc.widget, str(doc.id))
        doc.page.show()
        self._docs[doc.page] = doc
        n = self.notebook.append_page(doc.page, doc.tab)
        self.notebook.set_tab_reorderable(doc.page, True)
        if switch or self.notebook.get_n_pages() == 1:
            self.notebook.set_current_page(n)
            self.show_editor()
        return doc

    def switch_to(self, doc):
        n = self.notebook.page_num(doc.page)
        if n >= 0:
            self.notebook.set_current_page(n)
        self.show_editor()

    def _on_switch(self, _nb, page, _num):
        doc = self._docs.get(page)
        if doc is None:
            return
        self._cur = doc
        self.docstack.set_visible_child(doc.widget)
        self.findbar.attach(doc)
        self.doc_state_changed(doc)
        self.doc_cursor_moved(doc)
        self.doc_actions_changed(doc)
        doc.check_disk()
        GLib.idle_add(self._focus_doc, doc)

    def _focus_doc(self, doc):
        if doc is self.current() and self.body.get_visible_child_name() == "editor" and \
                not self.findbar.entry.has_focus() and not self.findbar.rentry.has_focus():
            doc.view.grab_focus()
        return False

    def _step_tab(self, delta):
        n = self.notebook.get_n_pages()
        if n > 1:
            self.notebook.set_current_page((self.notebook.get_current_page() + delta) % n)

    def _tab_at(self, ev):
        """눌린 자리의 탭 (탭 머리의 여백까지 — 라벨만 재면 가장자리를 누를 때 빗나간다)"""
        rx, ry = ev.x_root, ev.y_root
        for d in self.docs():
            t = d.tab
            gw = t.get_window()
            if gw is None or not t.get_mapped():
                continue
            a = t.get_allocation()
            ox, oy = gw.get_root_coords(a.x, a.y)
            if ox - 16 <= rx <= ox + a.width + 10 and oy - 10 <= ry <= oy + a.height + 10:
                return d
        return None

    def _on_tabs_press(self, _nb, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 2:        # 가운데 단추 — 탭 닫기
            d = self._tab_at(ev)
            if d is not None:
                self.close_doc(d)
                return True
        return False

    def _on_strip_press(self, _w, ev):
        """탭 줄의 빈 곳을 두 번 누르면 새 탭 (윈도우처럼)"""
        if ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1:
            self.new_doc()
            return True
        return False

    # ── 문서가 알려 오는 것 ──
    def doc_state_changed(self, doc):
        self._queue_fit()                             # 제목이 바뀌었을 수 있다
        if doc is not self.current():
            return
        self.set_title(f"{doc.title} - {APP_NAME}")
        self.st_enc.set_text(textcodec.encoding_name(doc.encoding))
        self.st_eol.set_text(textcodec.eol_name(doc.eol))

    def doc_cursor_moved(self, doc):
        if doc is not self.current():
            return
        b = doc.buffer
        it = b.get_iter_at_mark(b.get_insert())
        self.st_pos.set_text(f"줄 {it.get_line() + 1:,}, 열 {it.get_line_offset() + 1:,}")
        total = b.get_char_count()
        sel = b.get_selection_bounds()
        if sel:
            self.st_chars.set_text(f"{total:,}자 중 {sel[1].get_offset() - sel[0].get_offset():,}자")
        else:
            self.st_chars.set_text(f"{total:,}자")
        self.st_zoom.set_text(f"{doc.zoom}%")
        self.findbar.cursor_moved()

    def doc_actions_changed(self, doc):
        if doc is not self.current():
            return
        b = doc.buffer
        sel = b.get_has_selection()
        for name, on in (("undo", b.can_undo()), ("redo", b.can_redo()), ("cut", sel), ("copy", sel),
                         ("delete", True)):
            self.lookup_action(name).set_enabled(on)

    # ── 편집 ──
    def _edit(self, what):
        focus = self.get_focus()
        if isinstance(focus, Gtk.Entry):              # 찾기 칸 등 — 그 칸에서 (단축키가 글 칸으로 새지 않게)
            if what == "cut":
                focus.cut_clipboard()
            elif what == "copy":
                focus.copy_clipboard()
            elif what == "paste":
                focus.paste_clipboard()
            elif what == "select-all":
                focus.select_region(0, -1)
            elif what == "delete":
                if focus.get_selection_bounds():
                    focus.delete_selection()
                else:
                    pos = focus.get_position()
                    focus.delete_text(pos, pos + 1)
            return
        doc = self.current()
        if doc is None or self.body.get_visible_child_name() != "editor":
            return
        b, v = doc.buffer, doc.view
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if what == "undo":
            if b.can_undo():
                b.undo()
        elif what == "redo":
            if b.can_redo():
                b.redo()
        elif what == "cut":
            b.cut_clipboard(cb, True)
        elif what == "copy":
            b.copy_clipboard(cb)
        elif what == "paste":
            b.paste_clipboard(cb, None, True)
        elif what == "delete":
            if b.get_has_selection():
                b.delete_selection(True, True)
            else:
                it = b.get_iter_at_mark(b.get_insert())
                end = it.copy()
                if end.forward_cursor_position():
                    b.begin_user_action()
                    b.delete(it, end)
                    b.end_user_action()
        elif what == "select-all":
            b.select_range(b.get_start_iter(), b.get_end_iter())
        v.scroll_mark_onscreen(b.get_insert())
        v.grab_focus()

    def insert_time_date(self):
        doc = self.current()
        if doc is None:
            return
        b = doc.buffer
        b.begin_user_action()
        b.delete_selection(True, True)
        b.insert_at_cursor(time_date_text())
        b.end_user_action()
        doc.view.scroll_mark_onscreen(b.get_insert())

    def _zoom(self, delta):
        doc = self.current()
        if doc is not None:
            doc.set_zoom(100 if delta == 0 else doc.zoom + delta)

    def goto_dialog(self):
        doc = self.current()
        if doc is None:
            return
        b = doc.buffer
        d = Gtk.Dialog(title="줄 이동", transient_for=self, modal=True)
        d.add_button("취소", CANCEL)
        ok = d.add_button("이동", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("accent-btn")
        d.set_default_response(Gtk.ResponseType.OK)
        d.set_resizable(False)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        box.pack_start(Gtk.Label(label="줄 번호", xalign=0), False, False, 0)
        e = Gtk.Entry()
        e.set_input_purpose(Gtk.InputPurpose.DIGITS)
        e.set_activates_default(True)
        e.set_text(str(b.get_iter_at_mark(b.get_insert()).get_line() + 1))
        e.set_width_chars(28)
        box.pack_start(e, False, False, 0)
        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("np-error")
        err.set_no_show_all(True)
        box.pack_start(err, False, False, 0)

        def resp(dlg, r):
            if r != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            txt = e.get_text().strip().replace(",", "")
            total = b.get_line_count()
            if not txt.isdigit() or int(txt) < 1:
                err.set_text("줄 번호를 숫자로 입력하세요.")
                err.show()
                return
            if int(txt) > total:
                err.set_text("줄 번호가 전체 줄 수를 넘습니다.")
                err.show()
                return
            dlg.destroy()
            it = b.get_iter_at_line(int(txt) - 1)
            b.place_cursor(it)
            doc.view.scroll_to_mark(b.get_insert(), 0.25, False, 0, 0)
            doc.view.grab_focus()
        d.connect("response", resp)
        d.show_all()
        e.grab_focus()

    # ── 열기 ──
    def open_paths(self, paths, encoding=None):
        """파일들을 탭으로. 이미 열린 파일은 그 탭으로 가고, 지금 탭이 빈 새 탭이면 첫 파일은 그 탭에"""
        for path in paths:
            path = os.path.abspath(path)
            hit = self.app.find_open(path)
            if hit is not None:
                win, doc = hit
                win.switch_to(doc)
                if win is not self:
                    win.present()
                continue
            cur = self.current()
            if cur is not None and cur.is_blank():
                self._load(cur, path, encoding, fresh=False)
            else:
                self._load(self.new_doc(), path, encoding, fresh=True)

    def _load(self, doc, path, encoding, allow_big=False, allow_binary=False, fresh=True):
        name = os.path.basename(path)

        def done(status, info):
            if status in ("ok", "missing"):
                if status == "ok":
                    self.app.add_recent(path)
                return
            if status == "big":
                ask(self, f"{name} 파일의 크기가 {human_size(info)}입니다.",
                    f"{human_size(BIG_FILE)}보다 큰 파일은 여는 데 시간이 걸리고 메모리를 많이 쓸 수 있습니다. "
                    "그래도 여시겠습니까?",
                    [("열기", YES, "suggested"), ("취소", CANCEL, None)],
                    lambda r: self._load(doc, path, encoding, True, allow_binary, fresh) if r == YES
                    else self._abandon(doc, fresh), default=CANCEL, kind=Gtk.MessageType.WARNING)
            elif status == "binary":
                ask(self, f"{name}은(는) 텍스트 파일이 아닌 것 같습니다.",
                    "그래도 열면 알아볼 수 없는 문자로 보이고, 저장하면 파일이 손상될 수 있습니다. 여시겠습니까?",
                    [("열기", YES, None), ("취소", CANCEL, None)],
                    lambda r: self._load(doc, path, encoding, True, True, fresh) if r == YES
                    else self._abandon(doc, fresh), default=CANCEL, kind=Gtk.MessageType.WARNING)
            else:
                self._abandon(doc, fresh)
                notice(self, f"{name}을(를) 열 수 없습니다.", io_error_text(info))
        doc.load(path, encoding, allow_big, allow_binary, done)

    def _abandon(self, doc, fresh):
        """못 연 파일의 탭 — 새로 만든 탭이면 없애고, 빈 탭을 빌려 썼으면 빈 탭으로 되돌린다"""
        if doc not in self._docs.values():
            return
        if fresh and len(self._docs) > 1:
            self._remove_doc(doc)
            return
        doc._unwatch()
        doc.path = None
        doc.disk = None
        doc.buffer.begin_not_undoable_action()
        doc.buffer.set_text("")
        doc.buffer.end_not_undoable_action()
        doc.buffer.set_modified(False)
        doc._sync_tab()
        self.doc_state_changed(doc)

    def open_dialog(self):
        d = Gtk.FileChooserDialog(title="열기", transient_for=self, modal=True, action=Gtk.FileChooserAction.OPEN)
        d.add_button("취소", CANCEL)
        d.add_button("열기", Gtk.ResponseType.ACCEPT)
        d.set_default_response(Gtk.ResponseType.ACCEPT)
        d.set_select_multiple(True)
        f_txt, f_all = self._filters(d)
        d.set_filter(f_all)
        cur = self.current()
        folder = os.path.dirname(cur.path) if cur is not None and cur.path else self.app.last_dir()
        d.set_current_folder(folder)
        combo = Gtk.ComboBoxText()
        combo.append("", "자동 검색")
        for key, name, _c, _b in textcodec.ENCODINGS:
            combo.append(key, name)
        combo.set_active_id("")
        d.set_extra_widget(self._labeled("인코딩", combo))

        def resp(dlg, r):
            paths = dlg.get_filenames() if r == Gtk.ResponseType.ACCEPT else []
            enc = combo.get_active_id() or None
            dlg.destroy()
            if paths:
                self.app.set_last_dir(os.path.dirname(paths[0]))
                self.open_paths(paths, enc)
        d.connect("response", resp)
        d.show()

    @staticmethod
    def _filters(d):
        f_txt = Gtk.FileFilter()
        f_txt.set_name("텍스트 문서 (*.txt)")
        f_txt.add_pattern("*.[tT][xX][tT]")
        f_all = Gtk.FileFilter()
        f_all.set_name("모든 파일 (*.*)")
        f_all.add_pattern("*")
        d.add_filter(f_txt)
        d.add_filter(f_all)
        return f_txt, f_all

    @staticmethod
    def _labeled(text, widget):
        box = Gtk.Box(spacing=8)
        box.pack_start(Gtk.Label(label=text), False, False, 0)
        box.pack_start(widget, False, False, 0)
        box.show_all()
        return box

    # ── 저장 ──
    def save(self, doc, done=None, save_as=False):
        """done(ok) — 저장했으면 True, 취소했거나 못 했으면 False"""
        done = done or (lambda _ok: None)
        if doc is None:
            done(False)
            return
        if getattr(doc, "loading", False):
            # 아직 읽는 중 — 빈 글 칸을 그 경로에 저장하면 원본이 0바이트가 되었다
            done(False)
            return
        if save_as or doc.path is None:
            self.switch_to(doc)
            self._save_as_dialog(doc, lambda path, enc: self._write(doc, path, enc, done) if path else done(False))
        else:
            self._write(doc, doc.path, doc.encoding, done)

    def _write(self, doc, path, enc, done, lossy=False):
        name = os.path.basename(path)
        text = doc.get_text()
        if not lossy:
            bad = textcodec.unencodable(text, enc)
            if bad is not None:
                def resp(r):
                    if r == YES:
                        self._write(doc, path, "utf-8", done)
                    elif r == NO:
                        self._write(doc, path, enc, done, lossy=True)
                    else:
                        done(False)
                ask(self, f"일부 문자를 {textcodec.encoding_name(enc)}(으)로 저장할 수 없습니다.",
                    f"'{bad}' 같은 문자가 ?로 바뀝니다. UTF-8로 저장하면 모든 문자가 그대로 남습니다.",
                    [("UTF-8로 저장", YES, "suggested"), ("그대로 저장", NO, None), ("취소", CANCEL, None)],
                    resp, default=YES, kind=Gtk.MessageType.WARNING)
                return
        data = textcodec.encode(text, enc, doc.eol, errors="replace" if lossy else "strict")

        def after(ok, err):
            if ok:
                self.app.add_recent(path)
                done(True)
                return

            def resp(r):
                if r == YES:
                    self.save(doc, done, save_as=True)
                else:
                    done(False)
            ask(self, f"{name}을(를) 저장할 수 없습니다.",
                f"{io_error_text(err)}\n다른 이름으로 저장하시겠습니까?",
                [("다른 이름으로 저장", YES, "suggested"), ("취소", CANCEL, None)], resp, default=YES,
                kind=Gtk.MessageType.ERROR)
        doc.write(path, enc, data, after)

    def _save_as_dialog(self, doc, done):
        d = Gtk.FileChooserDialog(title="다른 이름으로 저장", transient_for=self, modal=True,
                                  action=Gtk.FileChooserAction.SAVE)
        d.add_button("취소", CANCEL)
        d.add_button("저장", Gtk.ResponseType.ACCEPT)
        d.set_default_response(Gtk.ResponseType.ACCEPT)
        d.set_do_overwrite_confirmation(True)
        f_txt, f_all = self._filters(d)
        if doc.path:
            d.set_filename(doc.path)
            d.set_filter(f_txt if doc.path.lower().endswith(".txt") else f_all)
        else:
            d.set_current_folder(self.app.last_dir())
            d.set_current_name(f"{UNTITLED}.txt")
            d.set_filter(f_txt)
        combo = Gtk.ComboBoxText()
        for key, name, _c, _b in textcodec.ENCODINGS:
            combo.append(key, name)
        combo.set_active_id(doc.encoding)
        d.set_extra_widget(self._labeled("인코딩", combo))

        def resp(dlg, r):
            path = dlg.get_filename() if r == Gtk.ResponseType.ACCEPT else None
            txt = dlg.get_filter() is f_txt
            enc = combo.get_active_id() or doc.encoding
            dlg.destroy()
            if not path:
                done(None, None)
                return
            # "텍스트 문서" 형식에 확장자 없이 적으면 .txt 를 붙인다 (윈도우처럼) — 그 이름이 이미 있으면 묻는다
            if txt and not os.path.splitext(os.path.basename(path))[1]:
                path += ".txt"
                if os.path.exists(path):
                    ask(self, f"{os.path.basename(path)}이(가) 이미 있습니다.", "바꾸시겠습니까?",
                        [("바꾸기", YES, "destructive"), ("취소", CANCEL, None)],
                        lambda rr: done(path, enc) if rr == YES else done(None, None), default=CANCEL,
                        kind=Gtk.MessageType.WARNING)
                    return
            self.app.set_last_dir(os.path.dirname(path))
            done(path, enc)
        d.connect("response", resp)
        d.show()

    def save_all(self, done=None):
        todo = [d for d in self.docs() if d.is_modified()]

        def step(i):
            if i >= len(todo):
                if done:
                    done(True)
                return
            self.save(todo[i], lambda ok: step(i + 1) if ok else (done and done(False)))
        step(0)

    # ── 닫기 ──
    def ask_save(self, doc, on_answer):
        """<이름>의 변경 내용을 저장하시겠습니까? → on_answer(SAVE · DISCARD · CANCEL)"""
        self.switch_to(doc)
        ask(self, f"{doc.title}의 변경 내용을 저장하시겠습니까?", None,
            [("저장", SAVE, "suggested"), ("저장 안 함", DISCARD, None), ("취소", CANCEL, None)],
            on_answer, default=SAVE)

    def close_doc(self, doc, done=None):
        done = done or (lambda _ok: None)
        if doc is None or doc not in self._docs.values():
            done(False)
            return
        if not doc.is_modified():
            self._remove_doc(doc)
            done(True)
            return

        def answer(r):
            if r == SAVE:
                self.save(doc, lambda ok: (self._remove_doc(doc), done(True)) if ok else done(False))
            elif r == DISCARD:
                self._remove_doc(doc)
                done(True)
            else:
                done(False)
        self.ask_save(doc, answer)

    def _remove_doc(self, doc):
        if doc not in self._docs.values():
            return
        last = len(self._docs) == 1
        if last and not self._closed:
            # 마지막 탭을 닫으면 창을 닫는다 (윈도우 11 메모장처럼)
            self._finish_close()
            return
        doc.close()
        n = self.notebook.page_num(doc.page)
        del self._docs[doc.page]
        if n >= 0:
            self.notebook.remove_page(n)
        self.docstack.remove(doc.widget)
        if self._cur is doc:
            self._cur = None
        self._queue_fit()

    def close_all(self, done):
        """이 창의 모든 탭 — 저장하지 않은 탭마다 묻는다. done(ok)"""
        todo = [d for d in self.docs() if d.is_modified()]

        def step(i):
            if i >= len(todo):
                done(True)
                return
            d = todo[i]

            def answer(r):
                if r == SAVE:
                    self.save(d, lambda ok: step(i + 1) if ok else done(False))
                elif r == DISCARD:
                    step(i + 1)
                else:
                    done(False)
            self.ask_save(d, answer)
        step(0)

    def _on_delete(self, *_):
        if self._closed:
            return False
        if not self.closing:
            self.closing = True

            def done(ok):
                self.closing = False
                if ok:
                    self._finish_close()
            self.close_all(done)
        return True

    def _finish_close(self):
        if self._closed:
            return
        self._closed = True
        self.app.remember_window(self)
        for d in self.docs():
            d.close()
        self.destroy()

    def _on_wstate(self, _w, ev):
        self._maximized = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def _on_focus_in(self, *_):
        d = self.current()
        if d is not None:
            d.check_disk()
        return False

    def _on_drag_data(self, _w, ctx, _x, _y, data, _info, time):
        paths = [p for p in (Gio.File.new_for_uri(u).get_path() for u in (data.get_uris() or [])) if p]
        Gtk.drag_finish(ctx, bool(paths), False, time)
        if paths:
            self.open_paths(paths)

    # ── 키 ──
    def _on_key(self, _w, ev):
        st = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        alt = Gdk.ModifierType.MOD1_MASK
        k = ev.keyval
        if st & ctrl and k in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab):
            self._step_tab(-1 if (st & shift or k == Gdk.KEY_ISO_Left_Tab) else 1)
            return True
        if st == ctrl and k in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self._step_tab(1 if k == Gdk.KEY_Page_Down else -1)
            return True
        if st == 0 and k == Gdk.KEY_Escape:
            if self.body.get_visible_child_name() == "settings":
                self.show_editor()
                return True
            if self.findbar.is_open:
                self.findbar.close()
                return True
        # Alt+F·E·V — 메뉴 열기 (메뉴 이름에 (F) 를 붙이지 않은 윈도우 11 메모장처럼 보이게 키만)
        if st == alt and self.body.get_visible_child_name() == "editor":
            idx = {Gdk.KEY_f: 0, Gdk.KEY_F: 0, Gdk.KEY_e: 1, Gdk.KEY_E: 1, Gdk.KEY_v: 2, Gdk.KEY_V: 2}.get(k)
            items = self.menubar.get_children()
            if idx is not None and idx < len(items):
                self.menubar.select_item(items[idx])
                items[idx].activate()
                return True
        return False
