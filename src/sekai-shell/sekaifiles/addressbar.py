"""파일 탐색기 — 주소 창 (윈도우 11 처럼).

평소: [아이콘] 내 PC › 문서 › 폴더 › — 칸을 누르면 그리로, › 를 누르면 그 안의 폴더 목록.
빈 곳을 누르거나 Ctrl+L · Alt+D: 글로 고쳐 쓰는 칸 (Enter 로 이동, ~ 은 홈 폴더, Esc 로 되돌림,
폴더 이름을 치는 중에는 아래로 후보가 뜬다).
창(host)이 주는 것: navigate(uri) · address_entered(text) · drives_menu_items() · focus_view()
"""
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .common import COMPUTER, edit_text, icons, image, local_path, natural, theme_icon  # noqa: E402

MENU_MAX = 400


def _list_subdirs(uri, show_hidden):
    """uri 안의 폴더 이름들 (작업 스레드에서) — [(보이는 이름, uri)]"""
    out = []
    p = local_path(uri)
    try:
        if p:
            with os.scandir(p) as it:
                for d in it:
                    try:
                        if not d.is_dir():
                            continue
                    except OSError:
                        continue
                    if d.name.startswith(".") and not show_hidden:
                        continue
                    out.append((GLib.filename_display_name(d.name), Gio.File.new_for_path(d.path).get_uri()))
                    if len(out) > 5000:
                        break
        else:
            f = Gio.File.new_for_uri(uri)
            en = f.enumerate_children("standard::name,standard::display-name,standard::type,standard::is-hidden",
                                      Gio.FileQueryInfoFlags.NONE, None)
            for info in en:
                if info.get_file_type() != Gio.FileType.DIRECTORY:
                    continue
                if info.get_is_hidden() and not show_hidden:
                    continue
                out.append((info.get_display_name(), f.get_child(info.get_name()).get_uri()))
                if len(out) > 5000:
                    break
    except (OSError, GLib.Error):
        return []
    out.sort(key=lambda t: natural(t[0]))
    return out


class AddressBar(Gtk.Stack):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.uri = None
        self.set_transition_type(Gtk.StackTransitionType.NONE)
        self.get_style_context().add_class("fx-address")
        self.set_hexpand(True)

        # 칸 보기
        ebox = Gtk.EventBox()
        ebox.connect("button-press-event", self._empty_press)
        row = Gtk.Box(spacing=0)
        row.get_style_context().add_class("fx-crumbs")
        self.icon = Gtk.Image()
        self.icon.get_style_context().add_class("fx-crumb-icon")
        row.pack_start(self.icon, False, False, 0)
        self.crumb_scroll = Gtk.ScrolledWindow()
        self.crumb_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.crumb_scroll.set_propagate_natural_height(True)
        self.crumb_scroll.set_propagate_natural_width(True)
        self.crumb_box = Gtk.Box(spacing=0)
        self.crumb_scroll.add(self.crumb_box)
        vp = self.crumb_scroll.get_child()
        if isinstance(vp, Gtk.Viewport):
            vp.set_shadow_type(Gtk.ShadowType.NONE)
        # 칸이 넘치면 앞쪽을 가리고 끝(지금 폴더)을 보인다
        self.crumb_scroll.get_hadjustment().connect("changed", self._keep_end)
        row.pack_start(self.crumb_scroll, True, True, 0)
        ebox.add(row)
        self.add_named(ebox, "crumbs")

        # 글 칸
        self.entry = Gtk.Entry()
        self.entry.get_style_context().add_class("fx-address-entry")
        self.entry.connect("activate", self._activate)
        self.entry.connect("key-press-event", self._entry_key)
        self.entry.connect("focus-out-event", self._focus_out)
        self.entry.connect("changed", self._entry_changed)
        self._comp_store = Gtk.ListStore(str)
        comp = Gtk.EntryCompletion()
        comp.set_model(self._comp_store)
        comp.set_text_column(0)
        comp.set_minimum_key_length(1)
        comp.set_popup_single_match(False)
        comp.set_inline_selection(True)
        self.entry.set_completion(comp)
        self._comp_dir = None
        self._comp_gen = 0
        self.add_named(self.entry, "entry")
        self.set_visible_child_name("crumbs")

    def _keep_end(self, adj):
        adj.set_value(max(adj.get_lower(), adj.get_upper() - adj.get_page_size()))

    # ── 칸 ──
    def set_location(self, uri, crumbs, icon_names):
        self.uri = uri
        # 목록과 같은 그림 (IconCache — 16px 에서도 색 있는 폴더)
        self.icon.set_from_pixbuf(icons().get(Gio.ThemedIcon.new_from_names([theme_icon(icon_names)]), 16))
        for c in self.crumb_box.get_children():
            c.destroy()
        for i, (label, target) in enumerate(crumbs):
            chev = Gtk.Button()
            chev.set_relief(Gtk.ReliefStyle.NONE)
            chev.set_focus_on_click(False)
            chev.get_style_context().add_class("fx-crumb-sep")
            chev.add(image(["go-next-symbolic", "pan-end-symbolic"], 12))
            prev = crumbs[i - 1][1] if i > 0 else None
            if prev is None:
                chev.connect("clicked", self._dropdown, None)      # 맨 앞 — 홈 · 즐겨찾기 · 내 PC · 휴지통
                chev.set_tooltip_text("다른 위치")
            elif prev == COMPUTER or local_path(prev) or "://" in prev:
                chev.connect("clicked", self._dropdown, prev)
                chev.set_tooltip_text("하위 폴더 보기")
            else:
                chev.set_sensitive(False)
            self.crumb_box.pack_start(chev, False, False, 0)
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_focus_on_click(False)
            b.get_style_context().add_class("fx-crumb")
            lbl = Gtk.Label(label=label)
            # 긴 이름만 줄인다 — 모든 칸을 줄일 수 있게 두면, 자리가 모자랄 때 앞쪽을 밀어 숨기는(_keep_end)
            #   대신 칸마다 "…"만 남았다 (윈도우는 지금 폴더 쪽을 보이고 앞쪽을 가린다)
            if len(label) > 28:
                lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                lbl.set_max_width_chars(28)
                lbl.set_width_chars(12)
            b.add(lbl)
            b.connect("clicked", lambda _b, u=target: self.host.navigate(u))
            self.crumb_box.pack_start(b, False, False, 0)
        # 마지막 칸 뒤의 › — 지금 폴더 안의 폴더들
        if crumbs:
            last = crumbs[-1][1]
            if last == COMPUTER or local_path(last) or ("://" in last and not last.startswith("sekai-")):
                chev = Gtk.Button()
                chev.set_relief(Gtk.ReliefStyle.NONE)
                chev.set_focus_on_click(False)
                chev.get_style_context().add_class("fx-crumb-sep")
                chev.add(image(["go-next-symbolic", "pan-end-symbolic"], 12))
                chev.set_tooltip_text("하위 폴더 보기")
                chev.connect("clicked", self._dropdown, last)
                self.crumb_box.pack_start(chev, False, False, 0)
        self.crumb_box.show_all()
        if self.get_visible_child_name() == "entry" and not self.entry.has_focus():
            self.cancel_edit()

    def _dropdown(self, btn, uri):
        menu = Gtk.Menu()
        menu.get_style_context().add_class("fx-menu")
        menu.attach_to_widget(btn, None)

        def fill(items):
            for c in menu.get_children():
                c.destroy()
            if not items:
                it = Gtk.MenuItem(label="(하위 폴더 없음)")
                it.set_sensitive(False)
                menu.append(it)
            for label, u, icon in items[:MENU_MAX]:
                it = Gtk.MenuItem()
                box = Gtk.Box(spacing=8)
                box.pack_start(image(icon, 16), False, False, 0)
                box.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
                it.add(box)
                it.connect("activate", lambda _i, u=u: self.host.navigate(u))
                menu.append(it)
            if len(items) > MENU_MAX:
                it = Gtk.MenuItem(label=f"… 외 {len(items) - MENU_MAX:,}개")
                it.set_sensitive(False)
                menu.append(it)
            menu.show_all()

        if uri is None or uri == COMPUTER:
            fill(self.host.top_menu_items() if uri is None else self.host.drives_menu_items())
            menu.popup_at_widget(btn, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())
            return
        loading = Gtk.MenuItem(label="읽는 중…")
        loading.set_sensitive(False)
        menu.append(loading)
        menu.show_all()
        menu.popup_at_widget(btn, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, Gtk.get_current_event())
        show_hidden = self.host.show_hidden

        def work():
            items = [(n, u, ["folder"]) for n, u in _list_subdirs(uri, show_hidden)]
            GLib.idle_add(lambda: (fill(items), False)[1])
        threading.Thread(target=work, daemon=True, name="sekai-files-crumb").start()

    # ── 고쳐 쓰기 ──
    def _empty_press(self, _w, ev):
        if ev.button == 1 and ev.type == Gdk.EventType.BUTTON_PRESS:
            self.begin_edit()
            return True
        return False

    def begin_edit(self):
        self.entry.set_text(edit_text(self.uri) if self.uri else "")
        self.set_visible_child_name("entry")
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def editing(self):
        return self.get_visible_child_name() == "entry"

    def cancel_edit(self):
        self.set_visible_child_name("crumbs")

    def _activate(self, _e):
        text = self.entry.get_text()
        self.host.address_entered(text)

    def _entry_key(self, _e, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.cancel_edit()
            self.host.focus_view()
            return True
        return False

    def _focus_out(self, *_):
        # 후보 목록을 누르는 동안에도 초점이 잠깐 빠진다 — 조금 뒤에 다시 본다
        def later():
            if not self.entry.has_focus() and self.editing():
                self.cancel_edit()
            return False
        GLib.timeout_add(150, later)
        return False

    def _entry_changed(self, _e):
        """폴더 이름 후보 — 친 글의 앞 폴더를 읽어 둔다 (작업 스레드)"""
        t = self.entry.get_text()
        if not self.editing():
            return
        path = os.path.expanduser(t) if t.startswith("~") else t
        if not path.startswith("/"):
            return
        d = path if path.endswith("/") else os.path.dirname(path)
        if d == self._comp_dir:
            return
        self._comp_dir = d
        self._comp_gen += 1
        gen = self._comp_gen
        show_hidden = os.path.basename(path).startswith(".") or self.host.show_hidden
        tilde = t.startswith("~")
        home = os.path.expanduser("~")

        def work():
            names = []
            try:
                with os.scandir(d) as it:
                    for de in it:
                        try:
                            if not de.is_dir():
                                continue
                        except OSError:
                            continue
                        if de.name.startswith(".") and not show_hidden:
                            continue
                        full = os.path.join(d, de.name)
                        if tilde and full.startswith(home):
                            full = "~" + full[len(home):]
                        names.append(full)
                        if len(names) >= 2000:
                            break
            except OSError:
                pass
            names.sort(key=natural)

            def done():
                if gen != self._comp_gen:
                    return False
                self._comp_store.clear()
                for n in names:
                    self._comp_store.append([n])
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True, name="sekai-files-comp").start()
