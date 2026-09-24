"""클립보드 기록 (Win+V) — cliphist 가 모아 둔 기록을 보여 준다.

항목을 고르면 그 내용을 클립보드에 넣고, 원래 창에 붙여넣기 키를 보낸다
(윈도우처럼 바로 붙는다). 터미널이면 Ctrl+Shift+V 를 보낸다.
"""
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, GtkLayerShell  # noqa: E402

from . import dbg
from .popup import PanelPopup

E = GtkLayerShell.Edge
LIMIT = 60
TERMINALS = ("foot", "footclient", "kitty", "alacritty", "org.wezfurlong.wezterm",
             "xfce4-terminal", "gnome-terminal-server", "org.gnome.console", "konsole",
             "xterm", "urxvt", "terminator", "tilix")


def _list():
    try:
        out = subprocess.run(["cliphist", "list"], capture_output=True, timeout=3).stdout
    except Exception as e:
        dbg("cliphist list 실패", e)
        return []
    lines = out.decode("utf-8", "replace").splitlines()
    return [ln for ln in lines if "\t" in ln][:LIMIT]


def _decode(line):
    try:
        return subprocess.run(["cliphist", "decode"], input=(line + "\n").encode(),
                              capture_output=True, timeout=3).stdout
    except Exception:
        return b""


def _is_image(preview):
    return preview.startswith("[[ binary data") and any(
        t in preview for t in (" png ", " jpeg ", " jpg ", " gif ", " webp ", " bmp "))


class ClipboardPopup(PanelPopup):
    def __init__(self, hypr):
        super().__init__(anchor_edge=E.RIGHT, margin=12, dim=False,
                         keyboard=GtkLayerShell.KeyboardMode.EXCLUSIVE)
        self.hypr = hypr
        self.get_style_context().add_class("clip-popup")
        self.target = None            # 붙여넣을 창 (열 때의 활성 창)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        t = Gtk.Label(label="클립보드", xalign=0)
        t.get_style_context().add_class("pop-title")
        head.pack_start(t, True, True, 0)
        self.clear_btn = Gtk.Button(label="모두 지우기")
        self.clear_btn.get_style_context().add_class("pop-btn")
        self.clear_btn.connect("clicked", lambda *_: self.clear_all())
        head.pack_end(self.clear_btn, False, False, 0)
        self.root.pack_start(head, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("clip-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.connect("row-activated", lambda _l, row: self.choose(row))
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(360, 440)
        sw.add(self.listbox)
        self.root.pack_start(sw, True, True, 0)

        self.empty = Gtk.Label(label="복사한 내용이 여기에 쌓입니다.\nCtrl+C 로 복사해 보세요.")
        self.empty.set_justify(Gtk.Justification.CENTER)
        self.empty.get_style_context().add_class("noti-empty")
        self.root.pack_start(self.empty, False, False, 0)
        self.empty.set_no_show_all(True)

    def set_panel_height(self, h):
        GtkLayerShell.set_margin(self, E.BOTTOM, h + 12)

    def on_open(self):
        aw = self.hypr.query("activewindow") or {}
        self.target = aw if isinstance(aw, dict) and aw.get("address") else None
        self.fill()

    def fill(self):
        for r in self.listbox.get_children():
            self.listbox.remove(r)
        lines = _list()
        for line in lines:
            _id, _, preview = line.partition("\t")
            row = Gtk.ListBoxRow()
            row.line = line
            row.get_style_context().add_class("clip-row")
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if _is_image(preview):
                img = Gtk.Image.new_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG)
                img.set_halign(Gtk.Align.START)
                h.pack_start(img, True, True, 0)
                self._thumb_async(line, img)
            else:
                lbl = Gtk.Label(label=preview.strip()[:400], xalign=0)
                lbl.set_line_wrap(True)
                lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_lines(3)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_max_width_chars(36)
                lbl.get_style_context().add_class("clip-text")
                h.pack_start(lbl, True, True, 0)
            rm = Gtk.Button()
            rm.get_style_context().add_class("noti-close")
            rm.set_tooltip_text("기록에서 지우기")
            im = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            im.set_pixel_size(12)
            rm.add(im)
            rm.set_valign(Gtk.Align.START)
            rm.connect("clicked", lambda _b, r=row: self.delete(r))
            h.pack_end(rm, False, False, 0)
            row.add(h)
            self.listbox.add(row)
        self.listbox.show_all()
        has = bool(lines)
        self.empty.set_visible(not has)
        self.clear_btn.set_sensitive(has)
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)

    def open(self):
        super().open()
        # 창이 뜬 뒤에 첫 항목에 초점 — 먼저 주면 present() 가 초점을 되돌린다
        GLib.timeout_add(60, self._focus_first)

    def _focus_first(self):
        row = self.listbox.get_selected_row() or self.listbox.get_row_at_index(0)
        if row:
            row.grab_focus()
        return False

    def _thumb_async(self, line, img):
        def work():
            data = _decode(line)
            GLib.idle_add(self._set_thumb, img, data)
        threading.Thread(target=work, daemon=True).start()

    def _set_thumb(self, img, data):
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pb = loader.get_pixbuf()
            w, h = pb.get_width(), pb.get_height()
            s = min(300 / w, 120 / h, 1.0)
            img.set_from_pixbuf(pb.scale_simple(max(1, int(w * s)), max(1, int(h * s)),
                                                GdkPixbuf.InterpType.BILINEAR))
        except Exception as e:
            dbg("클립보드 그림 미리보기 실패", e)
        return False

    # ── 동작 ──
    def choose(self, row):
        data = _decode(row.line)
        target = self.target
        self.close()
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            p.stdin.write(data)
            p.stdin.close()
        except Exception as e:
            dbg("wl-copy 실패", e)
            return
        if target and shutil.which("wtype"):
            cls = (target.get("class") or "").lower()
            keys = (["-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"]
                    if cls in TERMINALS else ["-M", "ctrl", "-k", "v", "-m", "ctrl"])
            # 팝업이 닫히고 원래 창이 키보드를 돌려받은 뒤에 보낸다
            GLib.timeout_add(180, lambda: (subprocess.Popen(["wtype"] + keys), False)[1])

    def delete(self, row):
        try:
            subprocess.run(["cliphist", "delete"], input=(row.line + "\n").encode(), timeout=3)
        except Exception as e:
            dbg("cliphist delete 실패", e)
        self.listbox.remove(row)
        if not self.listbox.get_children():
            self.empty.show()
            self.clear_btn.set_sensitive(False)

    def clear_all(self):
        try:
            subprocess.run(["cliphist", "wipe"], timeout=3)
        except Exception as e:
            dbg("cliphist wipe 실패", e)
        # 지금 클립보드도 비운다 (윈도우의 "모두 지우기"와 같게)
        subprocess.Popen(["wl-copy", "--clear"])
        self.fill()

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Delete:
            row = self.listbox.get_selected_row()
            if row:
                idx = row.get_index()
                self.delete(row)
                nxt = self.listbox.get_row_at_index(min(idx, len(self.listbox.get_children()) - 1))
                if nxt:
                    self.listbox.select_row(nxt)
                    nxt.grab_focus()
            return True
        return super()._key(_w, ev)
