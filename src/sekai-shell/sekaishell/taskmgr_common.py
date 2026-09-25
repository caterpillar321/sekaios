"""작업 관리자 — 여러 탭이 함께 쓰는 GTK 도움 (색·아이콘·앱 이름·목록 열·대화상자)."""
import os
import re

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import config, theme  # noqa: E402
from .appicon import app_gicon  # noqa: E402

DEFAULT_ACCENT = "#39c5bb"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FALLBACK_ICON = Gio.ThemedIcon.new_with_default_fallbacks("application-x-executable")


def appearance():
    """settings.json 의 색 — 설정 앱 저장소와 같은 규칙(모드의 기본 묶음 위에, 올바른 #rrggbb 만)"""
    a = config.settings("appearance")
    a = a if isinstance(a, dict) else {}
    mode = theme.mode_of(a)
    out = {"mode": mode, "accent": DEFAULT_ACCENT}
    out.update(theme.PALETTES[mode])
    for k in ("accent", "bg", "surface", "fg"):
        v = a.get(k)
        if isinstance(v, str) and _HEX.match(v):
            out[k] = v
    return out


def icon_image(names, size=16):
    """후보 이름 중 테마에 있는 첫 아이콘"""
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if n and th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def lookup_color(widget, name, fallback=(0.5, 0.5, 0.5, 1.0)):
    """CSS 의 @define-color 값 (accent·card·line…) — 모드·강조색이 바뀌면 CSS 를 다시 읽으므로 그대로 따라온다"""
    ok, c = widget.get_style_context().lookup_color(name)
    return (c.red, c.green, c.blue, c.alpha) if ok else fallback


def heat(rgb, frac):
    """값이 클수록 진한 칸 배경 (윈도우 작업 관리자처럼). rgb = "57,197,187" (강조색).
    단계로 끊어 값이 조금 변해도 행이 다시 그려지지 않게"""
    if frac is None or frac <= 0.001:
        return None
    a = 0.07 + 0.48 * min(1.0, frac) ** 0.55
    return f"rgba({rgb},{round(a / 0.04) * 0.04:.2f})"


def accent_rgb(widget):
    r, g, b, _ = lookup_color(widget, "accent", (0.22, 0.77, 0.73, 1))
    return f"{int(r * 255)},{int(g * 255)},{int(b * 255)}"


# ── 앱 이름·아이콘 ───────────────────────────────────────────
# 실행 파일 이름으로 앱을 찾을 때 건너뛸 것 — 해석기·감싸개는 여러 앱이 같이 쓴다
_GENERIC_EXE = {"python3", "python", "sh", "bash", "dash", "env", "flatpak", "java", "perl", "node",
                "gjs", "electron", "wine", "snap", "pkexec", "sudo", "xdg-open", "gtk-launch"}


class AppResolver:
    """창 클래스·실행 파일 이름 → (앱 id, 이름, GIcon). .desktop 을 찾는다 (결과는 기억)."""

    def __init__(self):
        self._win = {}
        self._by_exe = None
        self._by_wm = None

    def _index(self):
        if self._by_exe is not None:
            return
        self._by_exe, self._by_wm = {}, {}
        apps = [a for a in Gio.AppInfo.get_all() if isinstance(a, Gio.DesktopAppInfo)]

        def rank(a):
            # 같은 실행 파일의 항목이 여럿이면(mousepad 와 "mousepad --preferences" 설정 창) 본 앱을 먼저:
            #   메뉴에 보이는 것 → 인자 없이 실행하는 것 → id 끝이 실행 파일 이름인 것(org.xfce.mousepad)
            exe = os.path.basename(a.get_executable() or "").lower()
            args = [x for x in (a.get_commandline() or "").split()[1:] if not x.startswith("%")]
            stem = (a.get_id() or "").lower().removesuffix(".desktop").split(".")[-1]
            return (not a.should_show(), bool(args), stem != exe)
        apps.sort(key=rank)
        for a in apps:
            exe = os.path.basename(a.get_executable() or "")
            if exe and exe not in _GENERIC_EXE:
                self._by_exe.setdefault(exe, a)
            wm = a.get_startup_wm_class()
            if wm:
                self._by_wm.setdefault(wm.lower(), a)

    def reset(self):
        """앱을 설치·삭제했을 때 (Gio 의 앱 목록 변경 알림)"""
        self._win.clear()
        self._by_exe = self._by_wm = None

    def for_window(self, cls, exe_name):
        key = (cls, exe_name)
        hit = self._win.get(key)
        if hit is not None:
            return hit
        self._index()
        app = None
        low = (cls or "").lower()
        for cand in (cls, low, low.split(".")[-1]):
            if not cand:
                continue
            try:
                app = Gio.DesktopAppInfo.new(cand + ".desktop")
            except TypeError:
                app = None
            if app:
                break
        app = app or self._by_wm.get(low) or self._by_exe.get(exe_name or "")
        if app:
            hit = ("app:" + (app.get_id() or low), app.get_name() or cls, app.get_icon() or app_gicon(cls))
        else:
            hit = ("cls:" + (low or exe_name or "?"), cls or exe_name or "?", app_gicon(cls or exe_name or ""))
        self._win[key] = hit
        return hit

    def icon_for_exe(self, exe_name):
        """백그라운드 프로세스의 아이콘 — 같은 실행 파일의 .desktop 이 있으면 그 아이콘"""
        self._index()
        a = self._by_exe.get(exe_name or "")
        return (a.get_icon() if a else None) or FALLBACK_ICON


# ── 목록(TreeView) ───────────────────────────────────────────
def text_column(title, col, width=90, xalign=0.0, expand=False, bg=None, weight=None,
                ellipsize=True, icon=None):
    """열 하나. icon 이 있으면 이름 앞에 아이콘 (gicon 열). 고정 폭 — 행이 수백 개여도 매번 재지 않게"""
    c = Gtk.TreeViewColumn()
    c.set_title(title)
    if icon is not None:
        ir = Gtk.CellRendererPixbuf()
        ir.set_padding(4, 0)
        ir.props.stock_size = Gtk.IconSize.MENU
        c.pack_start(ir, False)
        c.add_attribute(ir, "gicon", icon)
    r = Gtk.CellRendererText(xalign=xalign)
    r.set_padding(8, 5)
    if ellipsize:
        r.props.ellipsize = Pango.EllipsizeMode.END
    c.pack_start(r, True)
    c.add_attribute(r, "text", col)
    if bg is not None:
        c.add_attribute(r, "cell-background", bg)
    if weight is not None:
        c.add_attribute(r, "weight", weight)
    c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
    c.set_fixed_width(width)
    c.set_min_width(48)
    c.set_resizable(True)
    c.set_expand(expand)
    c.set_alignment(xalign)
    c.renderer = r
    return c


def stat_header(caption, xalign=1.0):
    """윈도우 작업 관리자의 머리글 — 위에 전체 값(12%), 아래에 이름(CPU)"""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    val = Gtk.Label(label="", xalign=xalign)
    val.get_style_context().add_class("tm-colhead-value")
    cap = Gtk.Label(label=caption, xalign=xalign)
    cap.get_style_context().add_class("tm-colhead-name")
    box.pack_start(val, False, False, 0)
    box.pack_start(cap, False, False, 0)
    box.show_all()
    return box, val


class SortHeaders:
    """머리글을 눌러 정렬. 숫자 열은 처음 누르면 큰 값부터 (윈도우처럼)."""

    def __init__(self, sortmodel, on_change=None):
        self.model = sortmodel
        self.cols = {}
        self.on_change = on_change
        self.order = Gtk.SortType.ASCENDING

    def add(self, col, sid, numeric=False):
        self.cols[sid] = (col, numeric)
        col.set_clickable(True)
        col.connect("clicked", lambda _c: self._clicked(sid))

    def _clicked(self, sid):
        cur, order = self.model.get_sort_column_id()
        if cur == sid:
            order = Gtk.SortType.ASCENDING if order == Gtk.SortType.DESCENDING else Gtk.SortType.DESCENDING
        else:
            order = Gtk.SortType.DESCENDING if self.cols[sid][1] else Gtk.SortType.ASCENDING
        self.set(sid, order)
        if self.on_change:
            self.on_change(sid, order)

    def set(self, sid, order):
        if sid not in self.cols:
            return
        self.order = order
        self.model.set_sort_column_id(sid, order)
        for s, (c, _n) in self.cols.items():
            c.set_sort_indicator(s == sid)
            if s == sid:
                c.set_sort_order(order)


def conv_down(sortmodel, filt, store_iter):
    """저장소 행 → 화면(정렬 모델) 행. 걸러져 안 보이면 None"""
    r = filt.convert_child_iter_to_iter(store_iter)
    if isinstance(r, tuple):
        ok, r = r
        if not ok:
            return None
    if r is None:
        return None
    s = sortmodel.convert_child_iter_to_iter(r)
    if isinstance(s, tuple):
        ok, s = s
        if not ok:
            return None
    return s


def cmp_text(a, b):
    a, b = (a or "").casefold(), (b or "").casefold()
    return (a > b) - (a < b)


def cmp_num(a, b):
    return (a > b) - (a < b)


# ── 동작 ─────────────────────────────────────────────────────
def open_location(path):
    """파일 관리자에서 그 파일을 골라 보여 준다 (FileManager1.ShowItems — Thunar 가 제공).
    안 되면 담긴 폴더를 연다."""
    if not path:
        return
    uri = Gio.File.new_for_path(path).get_uri()
    folder = Gio.File.new_for_path(os.path.dirname(path) or "/").get_uri()

    def fallback():
        try:
            Gio.AppInfo.launch_default_for_uri(folder, None)
        except GLib.Error as e:
            print("[sekai-taskmgr] 폴더를 열지 못했습니다:", e.message, flush=True)

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        fallback()
        return

    def done(conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error:
            fallback()
    bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
             "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", ([uri], "")),
             None, Gio.DBusCallFlags.NONE, 4000, None, done)


def confirm(parent, title, text, ok_label, on_ok):
    """되돌릴 수 없는 일 앞에서 묻기 (기본 단추는 취소)"""
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                          buttons=Gtk.ButtonsType.NONE, text=title)
    d.format_secondary_text(text)
    d.add_button("취소", Gtk.ResponseType.CANCEL)
    b = d.add_button(ok_label, Gtk.ResponseType.ACCEPT)
    b.get_style_context().add_class("destructive-action")
    d.set_default_response(Gtk.ResponseType.CANCEL)

    def resp(dlg, r):
        dlg.destroy()
        if r == Gtk.ResponseType.ACCEPT:
            on_ok()
    d.connect("response", resp)
    d.show_all()


def notice(parent, title, text):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.INFO,
                          buttons=Gtk.ButtonsType.CLOSE, text=title)
    d.format_secondary_text(text)
    d.connect("response", lambda dlg, _r: dlg.destroy())
    d.show_all()


def menu_item(menu, label, cb, sensitive=True, tooltip=None):
    it = Gtk.MenuItem(label=label)
    it.set_sensitive(sensitive)
    if tooltip:
        it.set_tooltip_text(tooltip)
    it.connect("activate", lambda *_: cb())
    menu.append(it)
    return it


def popup(menu, view, event):
    menu.show_all()
    menu.attach_to_widget(view, None)
    if event is not None and event.type == Gdk.EventType.BUTTON_PRESS:
        menu.popup_at_pointer(event)
        return
    # 키보드(메뉴 키·Shift+F10) — 고른 행 옆에
    rect = None
    path, _col = view.get_cursor()
    if path is not None:
        rect = view.get_cell_area(path, None)
        wx, wy = view.convert_bin_window_to_widget_coords(rect.x, rect.y)
        rect.x, rect.y = wx + 24, wy
    if rect is not None:
        menu.popup_at_rect(view.get_window(), rect, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, event)
    else:
        menu.popup_at_widget(view, Gdk.Gravity.CENTER, Gdk.Gravity.NORTH_WEST, event)


def key_is_delete(ev):
    return ev.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete)


def key_is_menu(ev):
    return ev.keyval == Gdk.KEY_Menu or (ev.keyval == Gdk.KEY_F10 and ev.state & Gdk.ModifierType.SHIFT_MASK)
