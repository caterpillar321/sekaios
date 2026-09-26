"""파일 탐색기 — 홈 · 내 PC 화면.

홈: 즐겨찾기 폴더 타일 + 최근 항목 (Gtk.RecentManager — 파일만, 최근 20개, 아직 있는 것만)
내 PC: '장치 및 드라이브' 타일 — 아이콘 · 이름 · 용량 막대 · 'N GB 중 M GB 사용 가능'
       (용량은 작업 스레드에서 query_filesystem_info — 90% 넘게 차면 막대가 빨갛다)

창(host)이 주는 것: navigate(uri) · open_drive(d) · eject_drive(d) · open_entries(entries)
                    page_context_menu(page, event) · page_selection_changed()
"""
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from .common import (Entry, favorites, fmt_date, fmt_size_short, icons, image, list_drives,  # noqa: E402
                     uri_of_path)

RECENT_MAX = 20


def _section(text):
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.get_style_context().add_class("fx-section")
    return lbl


def _flowbox():
    fb = Gtk.FlowBox()
    fb.set_selection_mode(Gtk.SelectionMode.SINGLE)
    fb.set_activate_on_single_click(False)
    fb.set_homogeneous(True)
    fb.set_min_children_per_line(1)
    fb.set_max_children_per_line(12)
    fb.set_column_spacing(4)
    fb.set_row_spacing(4)
    fb.set_valign(Gtk.Align.START)
    fb.get_style_context().add_class("fx-tiles")
    return fb


class _Page:
    def _wrap(self, box):
        sc = Gtk.ScrolledWindow()
        # 가로도 넘치면 스크롤 — NEVER 면 "최근 항목" 표(열 폭 합계 ~880px)만큼 창을 더 줄일 수 없어
        #   스냅(3분할 등)이 안 됐다
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.add(box)
        sc.get_style_context().add_class("fx-page")
        sc.connect("button-press-event", self._nav_buttons)
        return sc

    def _nav_buttons(self, _w, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (8, 9):
            self.host.view_nav_button(ev.button)
            return True
        return False

    def _tile_press(self, fb, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        child = fb.get_child_at_pos(int(ev.x), int(ev.y))
        if child is None:
            return False
        self._unselect_others(fb)
        fb.select_child(child)
        child.grab_focus()
        self.host.page_context_menu(self, ev)
        return True

    def _unselect_others(self, keep):
        for w in self.selectables:
            if w is not keep:
                if isinstance(w, Gtk.FlowBox):
                    w.unselect_all()
                else:
                    w.get_selection().unselect_all()


class HomePage(_Page):
    def __init__(self, host):
        self.host = host
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("fx-page-body")
        box.pack_start(_section("즐겨찾기"), False, False, 0)
        self.fav = _flowbox()
        self.fav.connect("child-activated", self._fav_activated)
        self.fav.connect("button-press-event", self._tile_press)
        self.fav.connect("selected-children-changed", self._sel_changed)
        box.pack_start(self.fav, False, False, 0)
        box.pack_start(_section("최근 항목"), False, False, 0)
        # 최근 항목: 이름 · 사용한 날짜 · 파일 위치
        self.store = Gtk.ListStore(object, GdkPixbuf.Pixbuf, str, str, str)     # Entry, 그림, 이름, 날짜, 위치
        v = self.recent = Gtk.TreeView(model=self.store)
        v.get_style_context().add_class("fx-details")
        v.set_enable_search(False)
        v.set_activate_on_single_click(False)
        v.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        v.get_selection().connect("changed", self._sel_changed)
        v.connect("row-activated", lambda *_: self.host.open_entries(self.selected_entries()))
        v.connect("button-press-event", self._recent_press)
        for title, col, width, pix in (("이름", 2, 320, True), ("사용한 날짜", 3, 170, False),
                                       ("파일 위치", 4, 320, False)):
            c = Gtk.TreeViewColumn()
            c.set_title(title)
            if pix:
                ir = Gtk.CellRendererPixbuf()
                ir.set_padding(6, 0)
                c.pack_start(ir, False)
                c.add_attribute(ir, "pixbuf", 1)
            r = Gtk.CellRendererText()
            r.set_padding(6, 4)
            r.props.ellipsize = Pango.EllipsizeMode.END
            c.pack_start(r, True)
            c.add_attribute(r, "text", col)
            c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            c.set_fixed_width(width)
            c.set_resizable(True)
            v.append_column(c)
        filler = Gtk.TreeViewColumn()
        filler.set_expand(True)
        v.append_column(filler)
        box.pack_start(v, False, False, 0)
        self.empty = Gtk.Label(label="최근에 연 파일이 없습니다.", xalign=0)
        self.empty.get_style_context().add_class("fx-empty-inline")
        self.empty.set_no_show_all(True)
        box.pack_start(self.empty, False, False, 0)
        self.widget = self._wrap(box)
        self.selectables = [self.fav, self.recent]
        self._gen = 0
        self._recent_sig = Gtk.RecentManager.get_default().connect("changed", lambda *_: self.refresh_recent())
        self.refresh()

    def shutdown(self):
        if self._recent_sig:
            Gtk.RecentManager.get_default().disconnect(self._recent_sig)
            self._recent_sig = 0
        self._gen += 1

    def refresh(self):
        for c in self.fav.get_children():
            c.destroy()
        for p, label, icons_ in favorites():
            child = Gtk.FlowBoxChild()
            child.uri = uri_of_path(p)
            child.label = label
            child.path = p
            child.get_style_context().add_class("fx-tile")
            h = Gtk.Box(spacing=12)
            h.pack_start(image(icons_, 40), False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.set_valign(Gtk.Align.CENTER)
            n = Gtk.Label(label=label, xalign=0)
            n.get_style_context().add_class("fx-tile-name")
            s = Gtk.Label(label="고정됨", xalign=0)
            s.get_style_context().add_class("fx-tile-sub")
            v.pack_start(n, False, False, 0)
            v.pack_start(s, False, False, 0)
            h.pack_start(v, True, True, 0)
            child.add(h)
            child.set_size_request(230, -1)
            self.fav.add(child)
        self.fav.show_all()
        self.refresh_recent()

    def refresh_recent(self):
        """최근 항목 — 아직 있는지 보는 것은 작업 스레드에서 (끊긴 네트워크 경로에서 멈추지 않게)"""
        self._gen += 1
        gen = self._gen
        items = []
        try:
            for info in Gtk.RecentManager.get_default().get_items():
                uri = info.get_uri()
                if not uri.startswith("file://"):
                    continue
                items.append((uri, info.get_display_name() or "", info.get_mime_type() or "",
                              max(info.get_modified(), info.get_visited())))
        except Exception:
            items = []
        items.sort(key=lambda t: -t[3])

        def work():
            out = []
            for uri, name, mime, ts in items:
                p = Gio.File.new_for_uri(uri).get_path()
                if not p:
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if os.path.isdir(p):
                    continue
                e = Entry.from_path(p, os.path.basename(p), False, st.st_size, st.st_mtime, st.st_mode,
                                    os.path.dirname(p))
                if mime:
                    e.ctype = mime
                    e.gicon = Gio.content_type_get_icon(mime) or e.gicon
                out.append((e, ts))
                if len(out) >= RECENT_MAX:
                    break
            GLib.idle_add(done, out)

        def done(out):
            if gen != self._gen:
                return False
            self.store.clear()
            ic = icons()
            for e, ts in out:
                self.store.append([e, ic.get(e.gicon, 16), e.name, fmt_date(ts), e.loc])
            self.empty.set_visible(not out)
            self.recent.set_visible(bool(out))
            return False
        threading.Thread(target=work, daemon=True, name="sekai-files-recent").start()

    def _fav_activated(self, _fb, child):
        self.host.navigate(child.uri)

    def _recent_press(self, v, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (8, 9):
            self.host.view_nav_button(ev.button)
            return True
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        hit = v.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return True
        sel = v.get_selection()
        if not sel.path_is_selected(hit[0]):
            sel.unselect_all()
            sel.select_path(hit[0])
        self._unselect_others(v)
        v.grab_focus()
        self.host.page_context_menu(self, ev)
        return True

    def _sel_changed(self, w, *_):
        # 두 곳 중 한 곳에서만 고른다 (윈도우 홈처럼)
        src = w if isinstance(w, Gtk.FlowBox) else self.recent
        if (isinstance(w, Gtk.FlowBox) and w.get_selected_children()) or \
                (not isinstance(w, Gtk.FlowBox) and w.count_selected_rows()):
            self._unselect_others(src)
        self.host.page_selection_changed()

    # 창이 묻는 것
    def selected_tiles(self):
        return [c for c in self.fav.get_selected_children()]

    def selected_entries(self):
        m, paths = self.recent.get_selection().get_selected_rows()
        return [m[p][0] for p in paths]

    def selected_gfiles(self):
        out = [Gio.File.new_for_uri(c.uri) for c in self.selected_tiles()]
        out += [e.gfile for e in self.selected_entries()]
        return out

    def count(self):
        return len(self.fav.get_children()) + len(self.store)

    def select_all(self):
        self.fav.unselect_all()
        self.recent.get_selection().select_all()

    def unselect_all(self):
        self.fav.unselect_all()
        self.recent.get_selection().unselect_all()

    def focus(self):
        kids = self.fav.get_children()
        if kids and not self.fav.get_selected_children() and not self.recent.get_selection().count_selected_rows():
            kids[0].grab_focus()
        elif kids:
            (self.fav.get_selected_children() or kids)[0].grab_focus()


class ComputerPage(_Page):
    def __init__(self, host, vm):
        self.host = host
        self.vm = vm
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("fx-page-body")
        box.pack_start(_section("장치 및 드라이브"), False, False, 0)
        self.fb = _flowbox()
        self.fb.connect("child-activated", lambda _f, c: self.host.open_drive(c.drive))
        self.fb.connect("button-press-event", self._tile_press)
        self.fb.connect("selected-children-changed", lambda *_: self.host.page_selection_changed())
        box.pack_start(self.fb, False, False, 0)
        self.widget = self._wrap(box)
        self.selectables = [self.fb]
        self._gen = 0

    def refresh(self):
        keep = {c.drive.key for c in self.fb.get_selected_children()}
        for c in self.fb.get_children():
            c.destroy()
        self._gen += 1
        gen = self._gen
        todo = []
        for d in list_drives(self.vm):
            child = Gtk.FlowBoxChild()
            child.drive = d
            child.get_style_context().add_class("fx-tile")
            h = Gtk.Box(spacing=12)
            h.pack_start(Gtk.Image.new_from_pixbuf(icons().get(d.gicon, 48)), False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            v.set_valign(Gtk.Align.CENTER)
            n = Gtk.Label(label=d.name, xalign=0)
            n.set_ellipsize(Pango.EllipsizeMode.END)
            n.get_style_context().add_class("fx-tile-name")
            v.pack_start(n, False, False, 0)
            bar = Gtk.ProgressBar()
            bar.get_style_context().add_class("fx-cap")
            bar.set_no_show_all(True)
            v.pack_start(bar, False, False, 0)
            sub = Gtk.Label(label="" if d.root_uri else "연결되지 않음 — 두 번 눌러 연결", xalign=0)
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            sub.get_style_context().add_class("fx-tile-sub")
            v.pack_start(sub, False, False, 0)
            h.pack_start(v, True, True, 0)
            child.add(h)
            child.set_size_request(290, -1)
            child.bar, child.sub = bar, sub
            self.fb.add(child)
            if d.key in keep:
                self.fb.select_child(child)
            if d.root_uri:
                todo.append((child, d.root_uri))
        self.fb.show_all()

        def work():
            res = []
            for child, uri in todo:
                try:
                    info = Gio.File.new_for_uri(uri).query_filesystem_info(
                        "filesystem::size,filesystem::free,filesystem::used", None)
                    size = info.get_attribute_uint64("filesystem::size")
                    free = info.get_attribute_uint64("filesystem::free")
                    res.append((child, size, free))
                except GLib.Error:
                    res.append((child, 0, 0))
            GLib.idle_add(done, res)

        def done(res):
            if gen != self._gen:
                return False
            for child, size, free in res:
                if size <= 0:
                    continue
                used = max(0.0, min(1.0, (size - free) / size))
                child.bar.set_fraction(used)
                ctx = child.bar.get_style_context()
                (ctx.add_class if used > 0.9 else ctx.remove_class)("full")
                child.bar.show()
                child.sub.set_text(f"{fmt_size_short(size)} 중 {fmt_size_short(free)} 사용 가능")
            return False
        threading.Thread(target=work, daemon=True, name="sekai-files-df").start()

    def selected_drives(self):
        return [c.drive for c in self.fb.get_selected_children()]

    def selected_gfiles(self):
        return [Gio.File.new_for_uri(d.root_uri) for d in self.selected_drives() if d.root_uri]

    def count(self):
        return len(self.fb.get_children())

    def select_all(self):
        pass

    def unselect_all(self):
        self.fb.unselect_all()

    def focus(self):
        kids = self.fb.get_selected_children() or self.fb.get_children()
        if kids:
            kids[0].grab_focus()
