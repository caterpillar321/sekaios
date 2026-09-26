"""파일 탐색기 — 왼쪽 탐색 창 (윈도우 11 처럼).

    홈
    ─ 즐겨찾기: 바탕 화면 · 다운로드 · 문서 · 사진 · 음악 · 동영상
    ─ 내 PC
        로컬 디스크 (/) · USB 같은 드라이브 (연결 안 된 것도 — 누르면 연결) · 꺼내기 단추
    ─ 휴지통

행에 파일을 놓으면 그리로 복사·이동 (휴지통이면 삭제). 드라이브 목록은 Gio.VolumeMonitor 를 따라 바로 바뀐다.
창(host)이 주는 것: navigate(uri) · open_drive(drive) · eject_drive(drive) · nav_context_menu(row, event)
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .common import COMPUTER, HOME, TRASH, favorites, icons, image, list_drives, norm_uri, uri_of_path  # noqa: E402
from .views import DropTarget  # noqa: E402


class NavRow(Gtk.ListBoxRow):
    def __init__(self, kind, label, icon_names=None, gicon=None, uri=None, drive=None, indent=0,
                 eject_cb=None):
        super().__init__()
        self.kind = kind                        # "loc" · "drive" · "sep"
        self.uri = uri
        self.drive = drive
        self.label_text = label
        if kind == "sep":
            self.set_selectable(False)
            self.set_activatable(False)
            self.get_style_context().add_class("fx-nav-sep")
            self.add(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            return
        self.get_style_context().add_class("fx-nav-row")
        box = Gtk.Box(spacing=10)
        box.set_margin_start(indent)
        if gicon is not None:
            img = Gtk.Image.new_from_pixbuf(icons().get(gicon, 18))
        else:
            img = image(icon_names or ["folder"], 18)
        box.pack_start(img, False, False, 0)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(lbl, True, True, 0)
        if eject_cb is not None:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("fx-eject")
            b.add(image(["media-eject-symbolic", "media-eject"], 14))
            b.set_tooltip_text("꺼내기")
            b.set_focus_on_click(False)
            b.connect("clicked", lambda *_: eject_cb(drive))
            box.pack_end(b, False, False, 0)
        self.add(box)
        self.set_tooltip_text(label)


class NavPane(Gtk.Box):
    def __init__(self, host, vm):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.host = host
        self.vm = vm
        self.get_style_context().add_class("fx-nav")
        self.list = Gtk.ListBox()
        self.list.get_style_context().add_class("fx-nav-list")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.list.connect("row-activated", self._activated)
        self.list.connect("button-press-event", self._press)
        self.list.connect("popup-menu", self._menu_key)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.list)
        self.pack_start(sc, True, True, 0)
        self._current = None
        self._hl = None
        self._rebuild_src = 0
        self.drop = DropTarget(self.list, host, self._resolve_drop, self._unhighlight)
        self._sigs = [vm.connect(sig, lambda *_: self.rebuild_later())
                      for sig in ("mount-added", "mount-removed", "mount-changed", "volume-added", "volume-removed",
                                  "volume-changed", "drive-connected", "drive-disconnected", "drive-changed")]
        self.rebuild()

    def shutdown(self):
        """창이 닫힐 때 — 드라이브 알림을 끊는다"""
        for sid in self._sigs:
            try:
                self.vm.disconnect(sid)
            except Exception:
                pass
        self._sigs = []
        if self._rebuild_src:
            GLib.source_remove(self._rebuild_src)
            self._rebuild_src = 0

    def rebuild_later(self):
        if self._rebuild_src:
            GLib.source_remove(self._rebuild_src)
        self._rebuild_src = GLib.timeout_add(120, self._rebuild_now)

    def _rebuild_now(self):
        self._rebuild_src = 0
        self.rebuild()
        return False

    def _sep(self):
        return NavRow("sep", "")

    def rebuild(self):
        for r in self.list.get_children():
            r.destroy()
        L = self.list
        L.add(NavRow("loc", "홈", ["user-home", "go-home"], uri=HOME))
        L.add(self._sep())
        for p, label, icons_ in favorites():
            L.add(NavRow("loc", label, icons_, uri=uri_of_path(p)))
        L.add(self._sep())
        L.add(NavRow("loc", "내 PC", ["computer", "system"], uri=COMPUTER))
        for d in list_drives(self.vm):
            eject = self.host.eject_drive if (d.removable and (d.can_eject or d.can_unmount)) else None
            L.add(NavRow("drive", d.name, gicon=d.gicon, uri=d.root_uri, drive=d, indent=18, eject_cb=eject))
        L.add(self._sep())
        L.add(NavRow("loc", "휴지통", ["user-trash", "user-trash-full"], uri=TRASH))
        L.show_all()
        self.select_uri(self._current)

    def select_uri(self, uri):
        """지금 위치와 같은 행을 고른 것으로 (없으면 아무것도)"""
        self._current = uri
        n = norm_uri(uri) if uri else None
        hit = None
        for r in self.list.get_children():
            if r.kind != "sep" and r.uri and n and norm_uri(r.uri) == n:
                hit = r
                break
        if hit is None:
            self.list.unselect_all()
        elif self.list.get_selected_row() is not hit:
            self.list.select_row(hit)

    def _activated(self, _l, row):
        if row.kind == "drive":
            self.host.open_drive(row.drive)
        elif row.uri:
            self.host.navigate(row.uri)
        # 고름 표시는 실제로 간 곳을 따른다 (못 가면 원래 행으로)
        GLib.idle_add(lambda: (self.select_uri(self._current), False)[1])

    def _press(self, _l, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button in (8, 9):
            self.host.view_nav_button(ev.button)
            return True
        if ev.button == 3:
            row = self.list.get_row_at_y(int(ev.y))
            if row is not None and row.kind != "sep":
                self.host.nav_context_menu(row, ev)
            return True
        if ev.button == 2:
            row = self.list.get_row_at_y(int(ev.y))
            if row is not None and row.kind == "loc" and row.uri:
                self.host.open_new_tab(row.uri)         # 가운데 단추 — 새 탭에서 (윈도우 11 탐색기처럼)
                return True
        return False

    def _menu_key(self, *_):
        row = self.list.get_selected_row()
        if row is not None and row.kind != "sep":
            self.host.nav_context_menu(row, None)
            return True
        return False

    # 놓기
    def _resolve_drop(self, x, y):
        row = self.list.get_row_at_y(int(y))
        if row is None or row.kind == "sep" or not row.uri or row.uri in (HOME, COMPUTER):
            return None, None
        return row.uri, (lambda r=row: self._highlight(r))

    def _highlight(self, row):
        if self._hl is not row:
            self._unhighlight()
            self.list.drag_highlight_row(row)
            self._hl = row

    def _unhighlight(self):
        if self._hl is not None:
            self.list.drag_unhighlight_row()
            self._hl = None
