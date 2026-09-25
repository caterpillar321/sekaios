"""메모장 — 탭 하나 (글 칸 · 파일 경로 · 인코딩 · 줄 끝 · 확대 비율 · 바깥 변경 감시).

파일 읽기는 작업 스레드(textcodec.read_file), 저장은 Gio 비동기(replace_contents) — 화면이 멈추지 않게.
묻는 창(큰 파일·바이너리·저장 여부)은 창(window.py)이 띄운다. 여기는 글과 파일만.
"""
import itertools
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, GtkSource, Pango  # noqa: E402

from . import textcodec  # noqa: E402
from .common import (UNTITLED, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP, display_name, font_css,  # noqa: E402
                     icon)

_ids = itertools.count(1)
URI_TARGET = 7001      # 끌어 놓기의 파일 목록 (text/uri-list)


def _stamp_of(info):
    """Gio 파일 정보 → (mtime 마이크로초, 크기) — textcodec.read_file 의 것과 같은 단위"""
    sec = info.get_attribute_uint64(Gio.FILE_ATTRIBUTE_TIME_MODIFIED)
    usec = info.get_attribute_uint32(Gio.FILE_ATTRIBUTE_TIME_MODIFIED_USEC)
    return (sec * 1000000 + usec, info.get_size())


class TabLabel(Gtk.Box):
    """탭 머리 — 제목 + 닫기(저장 안 했으면 ●, 올리면 ×)"""

    def __init__(self, on_close):
        super().__init__(spacing=6)
        self.get_style_context().add_class("np-tab")
        self.label = Gtk.Label(label=UNTITLED, xalign=0)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_width_chars(4)          # 탭이 많으면 이만큼까지 줄어든다 (제목은 … 로)
        self.label.set_max_width_chars(22)
        self.pack_start(self.label, True, True, 0)
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_can_focus(False)
        btn.set_tooltip_text("탭 닫기 (Ctrl+W)")
        btn.get_style_context().add_class("np-tabclose")
        ov = Gtk.Overlay()
        x = icon(["window-close-symbolic"], 12)
        x.get_style_context().add_class("np-x")
        ov.add(x)
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("np-dot")
        dot.set_halign(Gtk.Align.CENTER)
        dot.set_valign(Gtk.Align.CENTER)
        ov.add_overlay(dot)
        ov.set_overlay_pass_through(dot, True)
        btn.add(ov)
        btn.connect("clicked", lambda *_: on_close())
        self.pack_start(btn, False, False, 0)
        self.show_all()

    def natural_width(self):
        """제목을 다 보일 때의 폭 (닫기 단추 포함, 탭 여백 빼고)"""
        return self.label.get_preferred_width()[1] + self.get_spacing() + 22

    def set_state(self, title, modified, tooltip):
        self.label.set_text(title)
        self.set_tooltip_text(tooltip)
        ctx = self.get_style_context()
        if modified:
            ctx.add_class("modified")
        else:
            ctx.remove_class("modified")


class NoticeBar(Gtk.Box):
    """글 위의 한 줄 안내 + 단추 (다른 프로그램이 파일을 바꿨을 때)"""

    def __init__(self):
        super().__init__(spacing=10)
        self.get_style_context().add_class("np-notice")
        self.pack_start(icon(["dialog-information-symbolic"], 16), False, False, 0)
        self.label = Gtk.Label(xalign=0)
        self.label.set_line_wrap(True)
        self.pack_start(self.label, True, True, 0)
        self.btns = Gtk.Box(spacing=6)
        self.pack_end(self.btns, False, False, 0)
        self.set_no_show_all(True)

    def show_notice(self, text, buttons):
        self.label.set_text(text)
        for b in self.btns.get_children():
            b.destroy()
        for label, cb in buttons:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            self.btns.pack_start(b, False, False, 0)
        for w in self.get_children():
            w.show_all()
        self.show()

    def hide_notice(self):
        self.hide()


class Document:
    """탭 하나. 창이 notebook(탭 머리 — 빈 page) 과 stack(글 칸 — widget) 에 나눠 넣는다"""

    def __init__(self, win):
        self.win = win
        self.id = next(_ids)
        self.path = None
        self.encoding = textcodec.DEFAULT_ENCODING
        self.eol = textcodec.DEFAULT_EOL
        self.zoom = 100
        self.disk = None            # (mtime, 크기) — 마지막으로 읽거나 저장한 때의 파일
        self.loading = False
        self.saving = False
        self.version = 0            # 글이 바뀔 때마다 +1 (저장하는 동안 친 글을 '저장됨'으로 잘못 표시하지 않게)
        self.search = None
        self._monitor = None
        self._mon_src = 0
        self._stale = False         # 바뀜 알림을 이미 띄웠다 (같은 알림을 거듭 띄우지 않게)
        self._closed = False

        self.buffer = GtkSource.Buffer()
        self.buffer.set_style_scheme(None)          # 색은 settings.css(모드·강조색)를 따른다
        self.buffer.set_highlight_syntax(False)
        self.buffer.set_highlight_matching_brackets(False)
        self.buffer.set_max_undo_levels(-1)
        self.view = GtkSource.View.new_with_buffer(self.buffer)
        v = self.view
        v.set_show_line_numbers(False)
        v.set_highlight_current_line(False)
        v.set_show_right_margin(False)
        v.set_auto_indent(False)
        v.set_insert_spaces_instead_of_tabs(False)
        v.set_tab_width(8)
        v.set_left_margin(10)
        v.set_right_margin(10)
        v.set_top_margin(6)
        v.set_bottom_margin(6)
        v.get_style_context().add_class("np-text")
        self._font_prov = Gtk.CssProvider()
        # settings.css 의 `* { font-family: Pretendard }` 보다 위에 (그 칸에만)
        v.get_style_context().add_provider(self._font_prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self._font = None

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.add(v)
        self.notice = NoticeBar()
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.get_style_context().add_class("np-body")
        self.widget.pack_start(self.notice, False, False, 0)
        self.widget.pack_start(self.scroller, True, True, 0)

        self.page = Gtk.Box()                        # 탭 머리 아래의 빈 칸 (글은 창의 stack 에)
        self.page.set_size_request(-1, 0)
        self.tab = TabLabel(lambda: win.close_doc(self))

        self.buffer.connect("modified-changed", lambda *_: self._changed_state())
        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("mark-set", self._on_mark)
        for prop in ("can-undo", "can-redo", "has-selection"):
            self.buffer.connect("notify::" + prop, lambda *_: win.doc_actions_changed(self))
        v.connect("scroll-event", self._on_scroll)
        v.connect("drag-drop", self._on_drag_drop)
        v.connect("drag-data-received", self._on_drag_data)
        v.connect("populate-popup", self._on_popup)
        # X11 식 "고르면 곧 PRIMARY 복사"를 끈다 — 켜 두면 찾기 칸이 글을 고르는 순간(PRIMARY 를 가져간다)
        #   GtkTextBuffer 가 선택을 풀어 찾은 글·고른 글이 사라진다. 윈도우엔 PRIMARY 가 없다
        #   (다른 앱의 PRIMARY 를 가운데 단추로 붙여 넣는 것은 그대로 된다).
        #   글 칸이 realize 때 PRIMARY 를 붙이고 unrealize 때 떼므로 그 짝을 맞춰 준다
        primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        v.connect_after("realize", lambda *_: self.buffer.remove_selection_clipboard(primary))
        v.connect("unrealize", lambda *_: self.buffer.add_selection_clipboard(primary))
        tl = v.drag_dest_get_target_list()
        if tl is not None:
            tl.add_uri_targets(URI_TARGET)
        self._sync_tab()

    # ── 이름·상태 ──
    @property
    def title(self):
        return display_name(self.path)

    def is_modified(self):
        return self.buffer.get_modified()

    def is_blank(self):
        """새로 연 빈 탭 (파일을 열면 이 탭을 대신 쓴다 — 윈도우 메모장처럼)"""
        return (self.path is None and not self.loading and not self.is_modified()
                and self.buffer.get_char_count() == 0)

    def _changed_state(self):
        self._sync_tab()
        self.win.doc_state_changed(self)

    def _sync_tab(self):
        self.tab.set_state(self.title, self.is_modified(), self.path or UNTITLED)

    def _on_changed(self, _b):
        self.version += 1
        self.win.doc_cursor_moved(self)

    def _on_mark(self, _b, _it, mark):
        if mark.get_name() in ("insert", "selection_bound"):
            self.win.doc_cursor_moved(self)

    def get_text(self):
        b = self.buffer
        return b.get_text(b.get_start_iter(), b.get_end_iter(), True)

    # ── 모양 ──
    def apply_prefs(self, font, wrap):
        self._font = font
        self._apply_font()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)

    def _apply_font(self):
        try:
            self._font_prov.load_from_data(font_css(self._font, self.zoom).encode())
        except GLib.Error as e:
            print("[sekai-notepad] 글꼴 CSS 오류:", e.message, flush=True)

    def set_zoom(self, zoom):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, int(zoom)))
        if zoom != self.zoom:
            self.zoom = zoom
            self._apply_font()
            self.win.doc_cursor_moved(self)

    def _on_scroll(self, _v, ev):
        """Ctrl+휠 — 확대/축소"""
        if not ev.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        step = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            step = 1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            step = -1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = ev.get_scroll_deltas()
            if ok and abs(dy) > 0.01:
                step = -1 if dy > 0 else 1
        if step:
            self.set_zoom(self.zoom + step * ZOOM_STEP)
        return True

    def _on_popup(self, _v, menu):
        """오른쪽 단추 메뉴 — GTK 기본 메뉴의 영어·중복 항목 대신 윈도우 메모장과 같은 항목"""
        if not isinstance(menu, Gtk.Menu):
            return
        for c in menu.get_children():
            menu.remove(c)
        has_sel = self.buffer.get_has_selection()
        items = [("실행 취소", "undo", self.buffer.can_undo()), None,
                 ("잘라내기", "cut", has_sel), ("복사", "copy", has_sel), ("붙여넣기", "paste", True),
                 ("삭제", "delete", has_sel), None, ("모두 선택", "select-all", True)]
        for it in items:
            if it is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            label, act, ok = it
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(ok)
            mi.connect("activate", lambda _m, a=act: self.win.run_action(a))
            menu.append(mi)
        menu.show_all()

    # ── 끌어 놓기 — 파일이면 새 탭으로 연다 (글은 GTK 가 그대로 넣는다) ──
    def _on_drag_drop(self, view, ctx, _x, _y, time):
        if "text/uri-list" in [t.name() for t in ctx.list_targets()]:
            view.drag_get_data(ctx, Gdk.Atom.intern("text/uri-list", False), time)
            return True
        return False

    def _on_drag_data(self, view, ctx, _x, _y, data, info, time):
        if info != URI_TARGET and data.get_target().name() != "text/uri-list":
            return
        GObject.signal_stop_emission_by_name(view, "drag-data-received")
        paths = [p for p in (Gio.File.new_for_uri(u).get_path() for u in data.get_uris()) if p]
        Gtk.drag_finish(ctx, bool(paths), False, time)
        if paths:
            GLib.idle_add(lambda: (self.win.open_paths(paths), False)[1])

    # ── 읽기 ──
    def load(self, path, forced, allow_big, allow_binary, done):
        """작업 스레드에서 읽고, 메인 스레드에서 done(상태, 정보). 상태는 textcodec.read_file 의 것"""
        self.loading = True
        self.path = path
        self._sync_tab()
        self.view.set_sensitive(False)

        def work():
            try:
                res = textcodec.read_file(path, forced, allow_big, allow_binary)
            except Exception as e:                   # 예상 못 한 오류도 창에 알린다
                res = ("error", e)
            GLib.idle_add(finish, res)

        def finish(res):
            if self._closed:
                return False
            self.loading = False
            self.view.set_sensitive(True)
            status, info = res
            if status == "ok":
                text, enc, eol, stamp = info
                self._set_loaded(text, enc, eol, stamp)
            elif status == "missing":                # 없는 파일 — 그 이름의 새 문서 (저장하면 만들어진다)
                self._set_loaded("", textcodec.DEFAULT_ENCODING, textcodec.DEFAULT_EOL, None)
            done(status, info)
            return False
        threading.Thread(target=work, daemon=True, name="sekai-notepad-read").start()

    def _set_loaded(self, text, enc, eol, stamp):
        b = self.buffer
        b.begin_not_undoable_action()
        b.set_text(text)
        b.end_not_undoable_action()
        b.place_cursor(b.get_start_iter())
        b.set_modified(False)
        self.encoding, self.eol, self.disk = enc, eol, stamp
        self._stale = False
        self.notice.hide_notice()
        self.view.scroll_to_mark(b.get_insert(), 0.0, False, 0, 0)
        self._sync_tab()
        self.watch()
        self.win.doc_state_changed(self)
        self.win.doc_cursor_moved(self)

    def reload(self, done=None):
        """다른 프로그램이 바꾼 파일을 다시 — 커서 줄은 그대로"""
        line = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()
        path = self.path

        def after(status, info):
            if status == "ok":
                b = self.buffer
                it = b.get_iter_at_line(min(line, b.get_line_count() - 1))
                b.place_cursor(it)
                GLib.idle_add(lambda: (self.view.scroll_to_mark(b.get_insert(), 0.2, False, 0, 0), False)[1])
            if done:
                done(status, info)
        self.load(path, None, True, True, after)

    # ── 저장 ──
    def write(self, path, enc, data, done):
        """바이트를 path 에 (Gio 가 임시 파일에 쓰고 바꿔치기 — 권한·심볼릭 링크는 보존). done(ok, 오류)"""
        self.saving = True
        version = self.version
        f = Gio.File.new_for_path(path)

        def finished(fobj, res):
            try:
                fobj.replace_contents_finish(res)
            except GLib.Error as e:
                self.saving = False
                done(False, e)
                return
            if self._closed:
                done(True, None)
                return
            old = self.path
            self.path, self.encoding = path, enc
            if self.version == version:
                self.buffer.set_modified(False)
            self._stale = False
            self.notice.hide_notice()
            self._sync_tab()
            if old != path:
                self.watch()
            self.win.doc_state_changed(self)
            self.win.doc_cursor_moved(self)

            def stamped(st):
                self.disk = st
                self.saving = False           # 시각을 받은 뒤에야 — 그 사이의 감시 알림이 '바뀜'으로 보지 않게
            self.query_stamp(stamped)
            done(True, None)
        f.replace_contents_bytes_async(GLib.Bytes.new(data), None, False, Gio.FileCreateFlags.NONE, None, finished)

    # ── 바깥 변경 감시 ──
    def query_stamp(self, cb):
        """파일의 지금 (mtime, 크기) — 없거나 못 읽으면 None. 비동기"""
        if not self.path:
            cb(None)
            return
        f = Gio.File.new_for_path(self.path)

        def got(fobj, res):
            try:
                info = fobj.query_info_finish(res)
            except GLib.Error:
                cb(None)
                return
            cb(_stamp_of(info))
        f.query_info_async(f"{Gio.FILE_ATTRIBUTE_TIME_MODIFIED},{Gio.FILE_ATTRIBUTE_TIME_MODIFIED_USEC},"
                           f"{Gio.FILE_ATTRIBUTE_STANDARD_SIZE}", Gio.FileQueryInfoFlags.NONE,
                           GLib.PRIORITY_DEFAULT, None, got)

    def watch(self):
        self._unwatch()
        if not self.path:
            return
        try:
            self._monitor = Gio.File.new_for_path(self.path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self._monitor = None                     # 감시가 안 되는 곳 — 창에 초점이 올 때 확인한다
            return
        self._monitor.connect("changed", self._on_monitor)

    def _unwatch(self):
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._mon_src:
            GLib.source_remove(self._mon_src)
            self._mon_src = 0

    def _on_monitor(self, *_a):
        if self._mon_src:
            GLib.source_remove(self._mon_src)
        self._mon_src = GLib.timeout_add(400, self._monitor_settled)

    def _monitor_settled(self):
        self._mon_src = 0
        self.check_disk()
        return False

    def check_disk(self):
        """파일이 우리가 알던 것과 다르면 알림 막대 (창에 초점이 올 때·탭을 바꿀 때·감시 알림)"""
        if not self.path or self.loading or self.saving or self._stale or self._closed:
            return
        known = self.disk

        def got(st):
            if self.saving or self.loading or self._stale or self._closed or self.disk != known:
                return
            if st is None:
                if known is None:
                    return                           # 처음부터 없던 파일 (아직 저장하지 않은 새 이름)
                self._stale = True
                self.notice.show_notice(
                    "다른 프로그램에서 파일이 삭제되었거나 이동되었습니다. 저장하면 다시 만들어집니다.",
                    [("닫기", self.notice.hide_notice)])
                self.buffer.set_modified(True)
                return
            if st == known:
                return
            self._stale = True

            def do_reload():
                self.notice.hide_notice()
                self.reload()

            def keep():
                self.notice.hide_notice()
                self.disk = st                       # 이번 변경은 알았다 — 다음 변경부터 다시 알린다
                self._stale = False
            self.notice.show_notice("다른 프로그램에서 파일이 변경되었습니다. 다시 로드할까요?",
                                    [("다시 로드", do_reload), ("무시", keep)])
        self.query_stamp(got)

    # ── 찾기 ──
    def search_context(self, settings, style):
        """창의 찾기 설정을 함께 쓰는 검색 (처음 찾을 때 만든다)"""
        if self.search is None:
            # 강조 표시를 끈 채 만들어야 "search-match 모양이 없다" 경고가 나지 않는다 (색 묶음을 쓰지 않으므로)
            self.search = GtkSource.SearchContext(buffer=self.buffer, settings=settings, highlight=False)
            self.search.set_match_style(style)
        return self.search

    def close(self):
        self._closed = True
        self._unwatch()
        if self.search is not None:
            self.search.set_highlight(False)
