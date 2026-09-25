"""사진 — 창 (윈도우 11 "사진" 앱의 한 장 보기처럼).

  위쪽 도구 모음  파일 이름(누르면 이름 바꾸기) · N/M · 축소 · 배율▾ · 확대 · 회전 두 개 · 삭제 · 파일 정보 · 전체 화면 · …
                  전체 화면에선 그림 위에 떠 있다가 마우스를 멈추면 숨는다
  가운데 그림 칸  canvas.py — 가장자리의 ‹ › 는 마우스를 움직일 때만 보인다
  오른쪽          파일 정보 (I)

넘겨 보기: 같은 폴더의 그림을 이름의 자연 순서로 (탐색기 기본 정렬). 앞뒤 한 장씩 미리 읽어 두고, 멀어진 것은 버린다.
여러 파일을 골라 열면(sekai-photos a.jpg b.png …) 그것들만 넘긴다.
회전: 보기만 곧바로 돌리고, 그림을 떠날 때(넘기기·닫기) 파일에 저장한다 — JPEG·PNG·BMP·TIFF 만.
파일을 바꾸는 일(회전 저장·이름 바꾸기·삭제·다른 이름으로 저장·배경 설정)은 모두 한 줄로 선 작업 스레드(io)에서
차례로 한다 — 회전을 저장하는 중에 이름을 바꾸거나 지우더라도 순서가 꼬이지 않게.
"""
import json
import os
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import dbg  # noqa: E402
from sekaishell.taskmgr_common import open_location as show_in_file_manager  # noqa: E402

from . import exif, imageio  # noqa: E402
from .canvas import Canvas  # noqa: E402
from .imageio import ROTATE_SAVE, Cancelled, error_text, ext_of  # noqa: E402

TITLE = "사진"
APP_ICONS = ["multimedia-photo-viewer", "image-viewer", "org.gnome.eog", "eog", "image-x-generic"]
STATE = os.path.expanduser("~/.local/state/sekai/photos.json")
CACHE_BYTES = 768 << 20         # 미리 읽어 둔 그림이 이보다 크면 먼 것부터 버린다
SLIDE_SECS = 3
HIDE_MS = 2200                  # 마우스를 멈추고 이만큼 뒤에 ‹ › 와 (전체 화면의) 도구 모음을 숨긴다
SPIN_MS = 300                   # 읽기가 이보다 오래 걸리면 도는 표시

FMT_NAMES = {"jpeg": "JPEG 그림", "png": "PNG 그림", "gif": "GIF 그림", "bmp": "비트맵 그림", "tiff": "TIFF 그림",
             "webp": "WebP 그림", "svg": "SVG 그림", "ico": "아이콘", "avif": "AVIF 그림", "heif": "HEIF 그림",
             "jxl": "JPEG XL 그림", "tga": "TGA 그림", "pnm": "PNM 그림", "xpm": "XPM 그림", "xbm": "XBM 그림",
             "icns": "Apple 아이콘"}
# 다른 이름으로 저장 — (이름, 확장자들, GdkPixbuf 형식)
SAVE_TYPES = [("JPEG 그림", ("jpg", "jpeg", "jpe", "jfif"), "jpeg"), ("PNG 그림", ("png",), "png"),
              ("비트맵 그림", ("bmp",), "bmp"), ("TIFF 그림", ("tif", "tiff"), "tiff"),
              ("WebP 그림", ("webp",), "webp")]
ZOOM_CHOICES = [0.25, 0.5, 0.75, 1.5, 2.0, 4.0, 8.0]


# ── 작은 도움 ────────────────────────────────────────────────
def fmt_time(ts):
    """윈도우 한국어 표기 — 2026-09-25 오후 3:21"""
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d} {'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d}"


def fmt_bytes(n):
    if n < 1024:
        return f"{n}바이트"
    n = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
    return ""


def icon(names, size=16):
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if n and th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def app_icon_name():
    th = Gtk.IconTheme.get_default()
    return next((n for n in APP_ICONS if th.has_icon(n)), APP_ICONS[-1])


def _stem_len(name):
    i = name.rfind(".")
    return i if i > 0 else len(name)


def rename_error(exc):
    if isinstance(exc, GLib.Error) and exc.domain == "g-io-error-quark":
        if exc.code == Gio.IOErrorEnum.EXISTS:
            return "같은 이름의 파일이 이미 있습니다."
        if exc.code == Gio.IOErrorEnum.INVALID_FILENAME:
            return "파일 이름에 쓸 수 없는 글자가 있습니다."
        if exc.code == Gio.IOErrorEnum.PERMISSION_DENIED:
            return "이 파일의 이름을 바꿀 권한이 없습니다."
        if exc.code == Gio.IOErrorEnum.FILENAME_TOO_LONG:
            return "파일 이름이 너무 깁니다."
    return error_text(exc)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = f"{STATE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except (OSError, TypeError, ValueError) as e:
        dbg("사진 앱 상태 저장 실패", e)


def _menu_item(menu, label, cb, accel=None):
    it = Gtk.MenuItem(label=label)
    if accel:
        key, mods = Gtk.accelerator_parse(accel)
        child = it.get_child()
        if isinstance(child, Gtk.AccelLabel) and key:
            child.set_accel(key, mods)
    it.connect("activate", lambda *_: cb())
    menu.append(it)
    return it


def _flat_button(icon_names, tip, cb):
    b = Gtk.Button()
    b.add(icon(icon_names, 16))
    b.set_tooltip_text(tip)
    b.set_focus_on_click(False)          # 누른 뒤에도 키(←/→·Space)는 그림 넘기기로
    b.connect("clicked", lambda *_: cb())
    return b


def _sep():
    s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
    s.get_style_context().add_class("ph-sep")
    return s


# ── 창 ──────────────────────────────────────────────────────
def _screen_room():
    """창이 들어갈 크기 — 첫 모니터에서 작업 표시줄(48)·제목 표시줄·여백을 뺀 것"""
    try:
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        g = mon.get_workarea()
        return max(480, g.width - 80), max(360, g.height - 48 - 40 - 60)
    except Exception:
        return 1100, 760


class PhotosWindow(Gtk.ApplicationWindow):
    def __init__(self, app, pools):
        super().__init__(application=app, title=TITLE)
        self.loader, self.render, self.io = pools
        self.set_icon_name(app_icon_name())
        self.state = load_state()
        w, h = self.state.get("size") or (1100, 760)
        try:
            w, h = int(w), int(h)
        except (TypeError, ValueError):
            w, h = 1100, 760
        # 화면보다 크면 줄인다 — 1280x800 화면에서 1100x760 창은 작업 표시줄·제목 표시줄을 빼면 넘쳐 위가 잘렸다
        mw, mh = _screen_room()
        self.set_default_size(max(480, min(w, mw)), max(360, min(h, mh)))
        if self.state.get("maximized"):
            self.maximize()
        self.set_size_request(420, 320)
        for c in ("settings-window", "ph-window"):
            self.get_style_context().add_class(c)

        self.files = []
        self.index = -1
        self.folder = None
        self.explicit = False               # 여러 파일을 골라 연 것 — 폴더를 보지 않는다
        self.listed = False                 # 폴더 목록을 다 읽었다 (N/M 을 보일 수 있다)
        self.cur_path = None
        self.entry = None
        self.rot = 0                        # 아직 저장하지 않은 보기 회전
        self.cache = {}                     # 경로 → Entry
        self.loading = {}                   # 경로 → Job
        self._saving = {}                   # 회전을 저장하는 중인 경로 → 건수
        self._own_writes = {}               # 우리가 쓴 파일 → 시각 (폴더 감시가 "바뀌었다"고 다시 읽지 않게)
        self._warned_rot = set()
        self._dir = 1
        self._list_job = None
        self._mon = None
        self._relist_src = 0
        self._spin_src = 0
        self._toast_src = 0
        self._arrow_src = 0
        self._tb_src = 0
        self._ss_src = 0
        self._slideshow = None
        self._fullscreen = False
        self._arrow_hover = False
        self._tb_hover = False
        self._io_pending = 0
        self._closing = False
        self._dead = False
        self._rename_pop = None

        root = Gtk.Overlay()
        self.add(root)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add(main)
        self.top_slot = Gtk.Box()
        main.pack_start(self.top_slot, False, False, 0)
        self.toolbar = self._build_toolbar()
        self.top_slot.pack_start(self.toolbar, True, True, 0)

        body = Gtk.Box()
        main.pack_start(body, True, True, 0)
        self.view = Gtk.Overlay()
        body.pack_start(self.view, True, True, 0)
        self.canvas = Canvas(self.render)
        self.view.add(self.canvas)
        self.canvas.on_zoom_changed = self._zoom_changed
        self.canvas.on_need_full = self._need_full
        self.canvas.on_activity = self._activity
        self.canvas.on_press = self._canvas_press
        self.canvas.on_context = self._context_menu
        self.canvas.on_nav = lambda d: self.go(self.index + d)
        self.canvas.connect("leave-notify-event", lambda *_: self._schedule_hide(350))
        self._build_overlays()

        self.info_rev = Gtk.Revealer()
        self.info_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.info_rev.set_transition_duration(150)
        self.info_rev.add(self._build_info())
        body.pack_start(self.info_rev, False, False, 0)
        self.info_rev.set_reveal_child(bool(self.state.get("info")))
        self.info_btn.set_active(bool(self.state.get("info")))

        # 전체 화면일 때 도구 모음이 떠 있을 자리
        self.fs_rev = Gtk.Revealer()
        self.fs_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.fs_rev.set_transition_duration(150)
        self.fs_rev.set_valign(Gtk.Align.START)
        root.add_overlay(self.fs_rev)

        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self.connect("window-state-event", self._on_wstate)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-data-received", self._on_drop)
        self.theme_changed()
        self._update_actions()

    # ── 만들기 ──────────────────────────────────────────────
    def _build_toolbar(self):
        bar = Gtk.Box(spacing=2)
        bar.get_style_context().add_class("ph-toolbar")
        bar.connect("enter-notify-event", lambda *_: setattr(self, "_tb_hover", True))
        bar.connect("leave-notify-event", self._toolbar_leave)

        self.name_btn = Gtk.Button()
        self.name_btn.get_style_context().add_class("ph-name")
        self.name_btn.set_focus_on_click(False)
        self.name_label = Gtk.Label(label=TITLE, xalign=0)
        self.name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.name_label.set_max_width_chars(48)
        self.name_btn.add(self.name_label)
        self.name_btn.set_tooltip_text("이름 바꾸기 (F2)")
        self.name_btn.connect("clicked", lambda *_: self.rename())
        bar.pack_start(self.name_btn, False, False, 0)
        self.pos_label = Gtk.Label()
        self.pos_label.get_style_context().add_class("ph-pos")
        bar.pack_start(self.pos_label, False, False, 0)

        self.more_btn = Gtk.MenuButton()
        self.more_btn.add(icon(["view-more-horizontal-symbolic", "view-more-symbolic", "open-menu-symbolic"]))
        self.more_btn.set_tooltip_text("더 보기")
        self.more_btn.set_focus_on_click(False)
        self.more_menu = self._build_more_menu()
        self.more_btn.set_popup(self.more_menu)
        self.fs_btn = _flat_button(["view-fullscreen-symbolic"], "전체 화면 (F11)", self.toggle_fullscreen)
        self.info_btn = Gtk.ToggleButton()
        self.info_btn.add(icon(["dialog-information-symbolic", "help-about-symbolic"]))
        self.info_btn.set_tooltip_text("파일 정보 (I)")
        self.info_btn.set_focus_on_click(False)
        self.info_btn.connect("toggled", lambda b: self.set_info(b.get_active()))
        self.del_btn = _flat_button(["user-trash-symbolic", "edit-delete-symbolic"], "삭제 (Delete)", self.delete)
        self.rotr_btn = _flat_button(["object-rotate-right-symbolic"], "시계 방향으로 회전 (Ctrl+R)",
                                     lambda: self.rotate(1))
        self.rotl_btn = _flat_button(["object-rotate-left-symbolic"], "시계 반대 방향으로 회전 (Ctrl+Shift+R)",
                                     lambda: self.rotate(-1))
        self.zin_btn = _flat_button(["zoom-in-symbolic"], "확대 (Ctrl++)", lambda: self.canvas.zoom_step(True))
        self.zout_btn = _flat_button(["zoom-out-symbolic"], "축소 (Ctrl+-)", lambda: self.canvas.zoom_step(False))
        self.zoom_btn = Gtk.MenuButton()
        self.zoom_btn.get_style_context().add_class("ph-zoom")
        self.zoom_btn.set_focus_on_click(False)
        self.zoom_btn.set_tooltip_text("확대/축소 비율")
        zb = Gtk.Box(spacing=4)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_width_chars(5)
        zb.pack_start(self.zoom_label, False, False, 0)
        zb.pack_start(icon(["pan-down-symbolic", "go-down-symbolic"], 12), False, False, 0)
        self.zoom_btn.add(zb)
        self.zoom_btn.set_popup(self._build_zoom_menu())
        for w in (self.more_btn, self.fs_btn, self.info_btn, _sep(), self.del_btn, _sep(), self.rotr_btn,
                  self.rotl_btn, _sep(), self.zin_btn, self.zoom_btn, self.zout_btn):
            bar.pack_end(w, False, False, 0)
        return bar

    def _build_zoom_menu(self):
        m = Gtk.Menu()
        _menu_item(m, "창에 맞춤", self.canvas_fit, "<Control>0")
        _menu_item(m, "실제 크기(100%)", lambda: self.canvas.zoom_actual(), "<Control>1")
        m.append(Gtk.SeparatorMenuItem())
        for z in ZOOM_CHOICES:
            _menu_item(m, f"{round(z * 100)}%", lambda z=z: self.canvas.set_zoom(z, allow_below_fit=True))
        m.show_all()
        return m

    def _build_more_menu(self):
        m = Gtk.Menu()
        self._mi_saveas = _menu_item(m, "다른 이름으로 저장…", self.save_as, "<Control>s")
        self._mi_copy = _menu_item(m, "복사", self.copy, "<Control>c")
        self._mi_loc = _menu_item(m, "파일 위치 열기", self.open_location)
        self._mi_wall = _menu_item(m, "바탕 화면 배경으로 설정", self.set_wallpaper)
        self._mi_rename = _menu_item(m, "이름 바꾸기", self.rename, "F2")
        m.append(Gtk.SeparatorMenuItem())
        _menu_item(m, "파일 정보", lambda: self.set_info(not self.info_rev.get_reveal_child()), "i")
        self._mi_slide = _menu_item(m, "슬라이드 쇼", self.slideshow, "F5")
        _menu_item(m, "전체 화면", self.toggle_fullscreen, "F11")
        m.show_all()
        return m

    def _context_menu(self, ev):
        if self.cur_path is None:
            return
        ok = self.entry is not None and self.entry.ok
        m = Gtk.Menu()
        for item in (
                ("시계 방향으로 회전", lambda: self.rotate(1), "<Control>r", ok),
                ("시계 반대 방향으로 회전", lambda: self.rotate(-1), "<Control><Shift>r", ok),
                None,
                ("복사", self.copy, "<Control>c", ok),
                ("다른 이름으로 저장…", self.save_as, "<Control>s", ok),
                ("이름 바꾸기", self.rename, "F2", True),
                ("삭제", self.delete, "Delete", True),
                None,
                ("파일 위치 열기", self.open_location, None, True),
                ("바탕 화면 배경으로 설정", self.set_wallpaper, None, ok),
                ("파일 정보", lambda: self.set_info(not self.info_rev.get_reveal_child()), "i", True)):
            if item is None:
                m.append(Gtk.SeparatorMenuItem())
                continue
            label, cb, accel, sens = item
            it = _menu_item(m, label, cb, accel)
            it.set_sensitive(sens)
        m.show_all()
        m.attach_to_widget(self.canvas, None)
        m.connect("hide", lambda menu: GLib.idle_add(menu.destroy))   # 고른 항목의 activate 는 닫힌 뒤에 온다
        m.popup_at_pointer(ev)

    def _build_overlays(self):
        # 넘기기 ‹ ›
        self.prev_rev, self.prev_btn = self._nav_button(["go-previous-symbolic"], "이전 (←)", Gtk.Align.START, -1)
        self.next_rev, self.next_btn = self._nav_button(["go-next-symbolic"], "다음 (→)", Gtk.Align.END, 1)
        # 열 수 없을 때·빈 창의 안내
        self.msg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.msg.set_halign(Gtk.Align.CENTER)
        self.msg.set_valign(Gtk.Align.CENTER)
        self.msg_icon = icon(["image-missing", "image-x-generic-symbolic"], 64)
        self.msg_icon.get_style_context().add_class("ph-msg-icon")
        self.msg.pack_start(self.msg_icon, False, False, 0)
        self.msg_title = Gtk.Label()
        self.msg_title.get_style_context().add_class("ph-msg-title")
        self.msg.pack_start(self.msg_title, False, False, 0)
        self.msg_sub = Gtk.Label()
        self.msg_sub.set_line_wrap(True)
        self.msg_sub.set_max_width_chars(60)
        self.msg_sub.set_justify(Gtk.Justification.CENTER)
        self.msg_sub.get_style_context().add_class("ph-msg-sub")
        self.msg.pack_start(self.msg_sub, False, False, 0)
        self.msg_btn = Gtk.Button(label="열기…")
        self.msg_btn.set_halign(Gtk.Align.CENTER)
        self.msg_btn.get_style_context().add_class("accent-btn")
        self.msg_btn.connect("clicked", lambda *_: self.open_dialog())
        self.msg.pack_start(self.msg_btn, False, False, 6)
        self.msg.set_no_show_all(True)
        for w in (self.msg_icon, self.msg_title, self.msg_sub):
            w.show()
        self.view.add_overlay(self.msg)
        # 읽는 중
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_no_show_all(True)
        self.view.add_overlay(self.spinner)
        self.view.set_overlay_pass_through(self.spinner, True)
        # 짧은 알림
        self.toast_label = Gtk.Label()
        self.toast_label.set_line_wrap(True)
        self.toast_label.set_max_width_chars(70)
        self.toast_label.get_style_context().add_class("ph-toast")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.END)
        self.toast_label.set_no_show_all(True)
        self.view.add_overlay(self.toast_label)
        self.view.set_overlay_pass_through(self.toast_label, True)

    def _nav_button(self, names, tip, align, delta):
        rev = Gtk.Revealer()
        rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        rev.set_transition_duration(120)
        rev.set_halign(align)
        rev.set_valign(Gtk.Align.CENTER)
        b = Gtk.Button()
        b.add(icon(names, 20))
        b.set_tooltip_text(tip)
        b.set_focus_on_click(False)
        b.get_style_context().add_class("ph-nav")
        b.connect("clicked", lambda *_: self.go(self.index + delta))
        b.connect("enter-notify-event", lambda *_: setattr(self, "_arrow_hover", True))
        b.connect("leave-notify-event", lambda *_: (setattr(self, "_arrow_hover", False),
                                                    self._schedule_hide(HIDE_MS)))
        rev.add(b)
        self.view.add_overlay(rev)
        return rev, b

    def _build_info(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("ph-info")
        box.set_size_request(320, -1)
        head = Gtk.Box(spacing=8)
        head.get_style_context().add_class("ph-info-head")
        t = Gtk.Label(label="파일 정보", xalign=0)
        t.get_style_context().add_class("ph-info-title")
        head.pack_start(t, True, True, 0)
        close = _flat_button(["window-close-symbolic"], "닫기", lambda: self.set_info(False))
        close.get_style_context().add_class("ph-info-close")
        head.pack_end(close, False, False, 0)
        box.pack_start(head, False, False, 0)
        self.info_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.info_body.get_style_context().add_class("ph-info-body")
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.info_body)
        box.pack_start(sc, True, True, 0)
        return box

    # ── 색 ──────────────────────────────────────────────────
    def theme_changed(self):
        ok, c = self.get_style_context().lookup_color("ph_canvas")
        self.canvas.set_background((c.red, c.green, c.blue) if ok else (0.07, 0.07, 0.08))

    # ── 열기 ────────────────────────────────────────────────
    def open_paths(self, paths):
        """명령줄·끌어 놓기·열기 창에서 — 파일 하나(그 폴더를 넘겨 봄) · 폴더 · 여러 파일(그것들만)"""
        paths = list(dict.fromkeys(p for p in paths if p))
        if not paths:
            return
        self._end_slideshow()
        self._leave_current()
        self._unwatch()
        self._cancel_all_loads()
        self.cache.clear()
        self.listed = False
        if len(paths) > 1:
            self.explicit, self.folder = True, None
            self.files, self.index = paths, 0
            self.listed = True
            self._show(paths[0])
            return
        path = paths[0]
        self.explicit = False
        if os.path.isdir(path):
            # 폴더 — 목록이 올 때까지 빈 칸(안내 없이 도는 표시만): "사진이 없습니다" 가 번쩍이지 않게
            self.folder = path
            self.files, self.index = [], -1
            self._show(None)
            self._hide_msg()
            self._start_spinner()
            self._list(start_first=True)
        else:
            self.folder = os.path.dirname(path) or "."
            self.files, self.index = [path], 0
            self._show(path)
            self._list(initial=True)
        self._watch()

    def show_empty(self):
        if self.cur_path is None:
            self._show(None)

    def open_dialog(self):
        d = Gtk.FileChooserDialog(title="열기", transient_for=self, action=Gtk.FileChooserAction.OPEN, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "열기", Gtk.ResponseType.ACCEPT)
        d.set_default_response(Gtk.ResponseType.ACCEPT)
        f = Gtk.FileFilter()
        f.set_name("사진")
        for e in sorted(imageio.IMAGE_EXT):
            f.add_pattern("*." + e)
            f.add_pattern("*." + e.upper())
        d.add_filter(f)
        allf = Gtk.FileFilter()
        allf.set_name("모든 파일")
        allf.add_pattern("*")
        d.add_filter(allf)
        if self.folder:
            d.set_current_folder(self.folder)

        def resp(dlg, r):
            names = dlg.get_filenames() if r == Gtk.ResponseType.ACCEPT else []
            dlg.destroy()
            if names:
                self.open_paths(names)
        d.set_select_multiple(True)
        d.connect("response", resp)
        d.show()

    def _on_drop(self, _w, _ctx, _x, _y, data, _info, _t):
        paths = []
        for uri in data.get_uris() or []:
            p = Gio.File.new_for_uri(uri).get_path()
            if p:
                paths.append(p)
        if paths:
            self.open_paths(paths)
            self.present()

    # ── 폴더 목록 ───────────────────────────────────────────
    def _list(self, start_first=False, initial=False):
        """폴더를 (다시) 읽는다 — 작업 스레드. 바뀐 파일(수정 시각·크기)은 캐시에서 버린다.
        initial: 처음 연 파일은 없어도 목록에 둔다 (다시 읽을 때는 사라진 파일을 빼야 지운 것을 안다)"""
        if self.explicit or not self.folder:
            return
        folder, cur = self.folder, self.cur_path
        stamps_of = {p: e.stamp for p, e in self.cache.items() if e.stamp}
        if self._list_job is not None:
            self._list_job.cancelled = True

        def work(job):
            keep = cur if cur and (initial or os.path.exists(cur)) else None
            lst = imageio.list_folder(folder, keep)
            stamps = {}
            for p in stamps_of:
                try:
                    st = os.stat(p)
                    stamps[p] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    stamps[p] = None
            return lst, stamps

        def done(res, exc):
            if self._list_job is job:
                self._list_job = None
            if self._dead or folder != self.folder or self.explicit:
                return
            if exc is not None:
                dbg("폴더를 읽지 못했습니다", folder, exc)
                if start_first:                # 폴더를 열었는데 읽을 수 없다 — 빈 창 안내로
                    self._show(None)
                    self.toast("폴더를 읽지 못했습니다 — " + error_text(exc))
                if not self.listed:
                    self.listed = True
                    self._update_pos()
                return
            self._apply_list(*res, start_first=start_first)
        job = self._list_job = self.loader.submit(work, done, prio=0, tag="list")

    def _apply_list(self, lst, stamps, start_first=False):
        now = time.monotonic()
        reload_cur = False
        for p, st in stamps.items():
            e = self.cache.get(p)
            if e is None or e.stamp == st or p in self._saving or now - self._own_writes.get(p, -99) < 3:
                continue
            del self.cache[p]
            if p == self.cur_path and st is not None:
                reload_cur = True
        cur = self.cur_path
        old_index = self.index
        self.listed = True
        self.files = lst
        if start_first or cur is None:
            self.index = -1
            if lst:
                self.go(0)
            else:
                self._show(None)
            return
        if cur in lst:
            self.index = lst.index(cur)
            if reload_cur:
                self.rot = 0
                self._show(cur, reload=True)
        elif lst:
            # 보던 파일이 밖에서 지워지거나 옮겨졌다 — 같은 자리의 다음 그림으로
            self.entry = None
            self.rot = 0
            self.index = -1
            self.go(min(max(0, old_index), len(lst) - 1))
            return
        else:
            self.index = -1
            self._show(None)
            return
        self._update_pos()
        self._update_arrows()
        self._prefetch()

    def _watch(self):
        self._unwatch()
        if self.explicit or not self.folder:
            return
        try:
            self._mon = Gio.File.new_for_path(self.folder).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as e:
            dbg("폴더를 지켜볼 수 없습니다", self.folder, e.message)
            return

        def changed(*_):
            if self._relist_src:
                GLib.source_remove(self._relist_src)
            self._relist_src = GLib.timeout_add(500, self._relist_now)
        self._mon.connect("changed", changed)

    def _relist_now(self):
        self._relist_src = 0
        if not self._dead:
            self._list()
        return False

    def _unwatch(self):
        if self._mon is not None:
            self._mon.cancel()
            self._mon = None
        if self._relist_src:
            GLib.source_remove(self._relist_src)
            self._relist_src = 0

    # ── 넘기기 ──────────────────────────────────────────────
    def go(self, i):
        n = len(self.files)
        if not n:
            return
        i = max(0, min(n - 1, i))
        if i == self.index and self.files[i] == self.cur_path:
            return
        self._dir = 1 if i >= self.index else -1
        self._leave_current()
        self.index = i
        self._show(self.files[i])

    def _show(self, path, reload=False):
        self.cur_path = path
        self.rot = 0
        self._close_rename()
        if path is None:
            self.entry = None
            self.canvas.set_entry(None)
            self._stop_spinner()
            self.set_title(TITLE)
            self.name_label.set_text(TITLE)
            self._show_msg("표시할 사진이 없습니다", "사진 파일을 이 창으로 끌어다 놓거나 [열기]를 누르세요.",
                           button=True)
        else:
            name = os.path.basename(path)
            self.set_title(f"{name} - {TITLE}")
            self.name_label.set_text(name)
            e = None if reload else self.cache.get(path)
            if reload:
                self.cache.pop(path, None)
                job = self.loading.pop(path, None)
                if job is not None:
                    job.cancelled = True
            if e is not None:
                self._display(e)
            else:
                self.entry = None
                self.canvas.set_entry(None)
                self._hide_msg()
                self._start_spinner()
                self._request_load(path, 0)
        self._update_pos()
        self._update_arrows()
        self._update_actions()
        self._fill_info()
        self._prefetch()

    def _display(self, e):
        self.entry = e
        self._stop_spinner()
        self.canvas.set_entry(e, e.pre_rot + self.rot)
        if e.error:
            self._show_msg("이 파일을 열 수 없습니다", e.error)
        else:
            self._hide_msg()
        self._update_actions()
        self._fill_info()
        self._zoom_changed()

    def _leave_current(self):
        """그림을 떠날 때 — 돌려 두었으면 파일에 저장하고, 원본 해상도로 읽어 둔 큰 그림은 줄인 것으로 되돌린다"""
        e = self.entry
        if e is not None:
            if self.rot % 4 and e.ok and e.fmt in ROTATE_SAVE:
                self._commit_rotation()
            if e.reduced and e.small is not None and e.src is not e.small:
                e.src = e.small
                e.k = e.small.get_width() / e.W
                e.ver += 1
        self.rot = 0

    def _request_load(self, path, prio):
        job = self.loading.get(path)
        if job is not None:
            if prio == 0 and not job.started:
                job.cancelled = True          # 미리 읽기 줄에 서 있던 것 — 지금 볼 것이니 맨 앞으로
            else:
                return
        # 회전을 저장하는 중인 파일은 저장이 끝난 뒤에 읽는다 (같은 줄 — 옛 그림을 읽지 않게)
        pool = self.io if path in self._saving else self.loader
        vp = self.canvas.viewport

        def work(job):
            return imageio.load_entry(path, vp, job)

        def done(e, exc):
            if self.loading.get(path) is job:
                del self.loading[path]
            if self._dead:
                return
            if exc is not None:
                if isinstance(exc, Cancelled):
                    return
                e = imageio.Entry(path)
                e.error = error_text(exc)
            if path != self.cur_path and not self._near(path):
                return
            self.cache[path] = e
            self._trim_cache()
            if path == self.cur_path and self.entry is None:
                self._display(e)
        # io 줄로 가는 읽기는 _io_pending 에 세지 않는다 — 넘기다 취소되면 done 이 불리지 않아 건수가 남고,
        #   닫을 때 끝나지 않는 저장을 기다리며 창이 멈췄다 (닫을 때 기다릴 것은 쓰기뿐이다)
        job = pool.submit(work, done, prio=prio, tag=path)
        self.loading[path] = job

    def _near(self, path):
        if not self.files or self.index < 0:
            return False
        lo, hi = max(0, self.index - 2), min(len(self.files), self.index + 3)
        return path in self.files[lo:hi]

    def _prefetch(self):
        if not self.files or self.index < 0:
            return
        n, i = len(self.files), self.index
        keep = set(self.files[max(0, i - 2):min(n, i + 3)])
        for p in list(self.cache):
            if p not in keep and p != self.cur_path:
                del self.cache[p]
        for p, job in list(self.loading.items()):
            if p not in keep and p != self.cur_path:
                job.cancelled = True
                del self.loading[p]
        for j in ((i + 1, i - 1) if self._dir >= 0 else (i - 1, i + 1)):
            if 0 <= j < n:
                p = self.files[j]
                if p not in self.cache and p not in self.loading:
                    self._request_load(p, 1)
        self._trim_cache()

    def _trim_cache(self):
        total = sum(e.nbytes() for e in self.cache.values())
        if total <= CACHE_BYTES:
            return
        pos = {p: k for k, p in enumerate(self.files)}
        far = sorted((p for p in self.cache if p != self.cur_path),
                     key=lambda p: -abs(pos.get(p, 1 << 30) - self.index))
        for p in far:
            if total <= CACHE_BYTES:
                break
            total -= self.cache.pop(p).nbytes()

    def _cancel_all_loads(self):
        for job in self.loading.values():
            job.cancelled = True
        self.loading.clear()

    def _need_full(self, e):
        """줄여 읽은 큰 그림을 맞춤보다 크게 본다 — 원본 해상도로 다시 읽어 끼운다"""
        path, fmt, pre = e.path, e.fmt, e.pre_rot

        def work(job):
            pb = imageio.load_full(path, fmt, job)
            # 회전을 이미 파일에 저장했다면 파일은 돌아가 있다 — 보기의 pre_rot 와 겹치지 않게 되돌린다
            return imageio.rotate(pb, -pre % 4)

        def done(pb, exc):
            if self._dead or exc is not None:
                if exc is not None and not isinstance(exc, Cancelled):
                    self.toast("원본 해상도로 읽지 못했습니다 — " + error_text(exc))
                return
            if self.entry is not e or e.pre_rot != pre:
                return
            e.src = pb
            e.k = pb.get_width() / e.W
            e.ver += 1
            self.canvas.entry_updated()
        pool = self.io if path in self._saving else self.loader
        pool.submit(work, done, prio=0, tag=path)

    # ── 화면 조각들 ─────────────────────────────────────────
    def _update_pos(self):
        n = len(self.files)
        on = self.listed and n > 0 and self.index >= 0 and self.cur_path is not None
        self.pos_label.set_text(f"{self.index + 1}/{n}" if on else "")

    def _update_actions(self):
        has = self.cur_path is not None
        ok = self.entry is not None and self.entry.ok
        for w in (self.zin_btn, self.zout_btn, self.zoom_btn, self.rotl_btn, self.rotr_btn):
            w.set_sensitive(ok)
        self.del_btn.set_sensitive(has)
        self.name_btn.set_sensitive(has)
        for it in (self._mi_saveas, self._mi_copy, self._mi_wall):
            it.set_sensitive(ok)
        for it in (self._mi_loc, self._mi_rename):
            it.set_sensitive(has)
        self._mi_slide.set_sensitive(bool(self.files))

    def _zoom_changed(self):
        ok = self.entry is not None and self.entry.ok
        self.zoom_label.set_text(f"{self.canvas.percent()}%" if ok else "—")

    def _show_msg(self, title, sub, button=False):
        self.msg_title.set_text(title)
        self.msg_sub.set_text(sub or "")
        self.msg_sub.set_visible(bool(sub))
        self.msg_btn.set_visible(button)
        self.msg_icon.set_visible(not button)
        self.msg.show()

    def _hide_msg(self):
        self.msg.hide()

    def _start_spinner(self):
        self._stop_spinner()

        def on():
            self._spin_src = 0
            self.spinner.show()
            self.spinner.start()
            return False
        self._spin_src = GLib.timeout_add(SPIN_MS, on)

    def _stop_spinner(self):
        if self._spin_src:
            GLib.source_remove(self._spin_src)
            self._spin_src = 0
        self.spinner.stop()
        self.spinner.hide()

    def toast(self, text, secs=4):
        self.toast_label.set_text(text)
        self.toast_label.show()
        if self._toast_src:
            GLib.source_remove(self._toast_src)

        def hide():
            self._toast_src = 0
            self.toast_label.hide()
            return False
        self._toast_src = GLib.timeout_add_seconds(secs, hide)

    # ── ‹ › 와 전체 화면 도구 모음 숨기기 ───────────────────
    def _activity(self):
        if self._slideshow:
            return
        self._update_arrows(show=True)
        if self._fullscreen:
            self.fs_rev.set_reveal_child(True)
        self._schedule_hide(HIDE_MS)

    def _update_arrows(self, show=None):
        n = len(self.files)
        if show is None:                       # 목록만 바뀌었다 — 보이던 것만 고친다
            show = self.prev_rev.get_reveal_child() or self.next_rev.get_reveal_child()
        vis = bool(show) and not self._slideshow and n > 1 and self.index >= 0
        self.prev_rev.set_reveal_child(vis and self.index > 0)
        self.next_rev.set_reveal_child(vis and self.index < n - 1)

    def _schedule_hide(self, ms):
        if self._arrow_src:
            GLib.source_remove(self._arrow_src)
        self._arrow_src = GLib.timeout_add(ms, self._auto_hide)
        return False

    def _auto_hide(self):
        self._arrow_src = 0
        if self._dead:
            return False
        if not self._arrow_hover:
            self.prev_rev.set_reveal_child(False)
            self.next_rev.set_reveal_child(False)
        if self._fullscreen and not self._tb_hover and not self._menu_open():
            self.fs_rev.set_reveal_child(False)
        return False

    def _menu_open(self):
        return (self.more_btn.get_active() or self.zoom_btn.get_active() or
                (self._rename_pop is not None and self._rename_pop.get_visible()))

    def _toolbar_leave(self, _w, ev):
        # 도구 모음 안의 단추로 옮겨 갈 때도 leave 가 온다 (INFERIOR) — 정말 나갔을 때만
        if ev.detail != Gdk.NotifyType.INFERIOR:
            self._tb_hover = False
            if self._fullscreen:
                self._schedule_hide(HIDE_MS)
        return False

    # ── 정보 ────────────────────────────────────────────────
    def set_info(self, on):
        on = bool(on)
        self.info_rev.set_reveal_child(on)
        if self.info_btn.get_active() != on:
            self.info_btn.set_active(on)
        self.state["info"] = on
        if on:
            self._fill_info()

    def _fill_info(self):
        if not self.info_rev.get_reveal_child():
            return
        for c in self.info_body.get_children():
            c.destroy()
        path = self.cur_path
        if path is None:
            self._info_row("", "열린 사진이 없습니다.")
            self.info_body.show_all()
            return
        e = self.entry
        self._info_row("파일 이름", os.path.basename(path))
        if e is not None:
            ex = e.exif
            if ex.get("taken"):
                self._info_row("찍은 날짜", fmt_time(ex["taken"]))
            elif e.mtime:
                self._info_row("수정한 날짜", fmt_time(e.mtime))
            if e.ok:
                RW, RH = (e.H, e.W) if (e.pre_rot + self.rot) % 2 else (e.W, e.H)
                mp = RW * RH / 1e6
                self._info_row("크기", f"{RW} × {RH}" + (f" ({mp:.1f}MP)" if mp >= 0.05 else ""))
            if e.size:
                self._info_row("파일 크기", fmt_bytes(e.size))
            if e.fmt:
                kind = FMT_NAMES.get(e.fmt, f"{e.fmt.upper()} 그림")
                self._info_row("형식", kind + (" (움직이는 그림)" if e.anim is not None else ""))
        folder = os.path.dirname(path)
        self._info_row("위치", folder, link=True)
        if e is not None and e.exif:
            ex = e.exif
            cam = exif.camera_text(ex)
            if cam:
                self._info_row("카메라", cam)
            if ex.get("lens"):
                self._info_row("렌즈", ex["lens"])
            if ex.get("fnumber"):
                self._info_row("조리개", f"f/{ex['fnumber']:.1f}".replace(".0", ""))
            if ex.get("exposure"):
                self._info_row("노출 시간", exif.exposure_text(ex["exposure"]))
            if ex.get("iso"):
                self._info_row("ISO", f"ISO-{ex['iso']}")
            if ex.get("focal"):
                self._info_row("초점 거리", f"{ex['focal']:g}mm")
        self.info_body.show_all()

    def _info_row(self, key, val, link=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if key:
            k = Gtk.Label(label=key, xalign=0)
            k.get_style_context().add_class("ph-info-key")
            box.pack_start(k, False, False, 0)
        if link:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("ph-link")
            b.set_tooltip_text("파일 위치 열기")
            b.set_focus_on_click(False)
            v = Gtk.Label(label=val, xalign=0)
            v.set_line_wrap(True)
            v.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            b.add(v)
            b.connect("clicked", lambda *_: self.open_location())
            box.pack_start(b, False, False, 0)
        else:
            v = Gtk.Label(label=val, xalign=0)
            v.set_selectable(True)
            v.set_can_focus(False)
            v.set_line_wrap(True)
            v.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            v.get_style_context().add_class("ph-info-val")
            box.pack_start(v, False, False, 0)
        self.info_body.pack_start(box, False, False, 0)

    # ── 회전 ────────────────────────────────────────────────
    def rotate(self, d):
        e = self.entry
        if e is None or not e.ok:
            return
        self.rot = (self.rot + d) % 4
        self.canvas.set_rotation(e.pre_rot + self.rot)
        if self.rot and e.fmt not in ROTATE_SAVE and e.path not in self._warned_rot:
            self._warned_rot.add(e.path)
            self.toast("회전한 상태로 저장할 수 없는 형식입니다")
        self._fill_info()

    def _commit_rotation(self):
        """지금 보기 회전을 파일에 (io 줄에서). 보기는 그대로 — pre_rot 가 넘겨받는다"""
        e = self.entry
        q = self.rot % 4
        self.rot = 0
        if not q or e is None:
            return
        path, fmt = e.path, e.fmt
        e.pre_rot = (e.pre_rot + q) % 4
        self._saving[path] = self._saving.get(path, 0) + 1

        def work(job):
            imageio.save_rotated(path, q, fmt)
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size), st.st_size, st.st_mtime

        def done(res, exc):
            left = self._saving.get(path, 1) - 1
            if left > 0:
                self._saving[path] = left
            else:
                self._saving.pop(path, None)
            self._own_writes[path] = time.monotonic()
            if self._dead:
                return
            ent = self.cache.get(path)
            if exc is not None:
                self.toast("회전한 상태로 저장하지 못했습니다 — " + error_text(exc), secs=6)
                if ent is e:
                    del self.cache[path]
                    if path == self.cur_path:
                        self._show(path, reload=True)
                return
            stamp, size, mtime = res
            e.stamp, e.size, e.mtime = stamp, size, mtime
            if ent is e and self.entry is not e:
                del self.cache[path]           # 다음에 볼 때 돌아간 파일을 새로 읽는다
                if self._near(path):
                    self._prefetch()
            elif self.entry is e:
                self._fill_info()
        self._io_submit(work, done)

    # ── 파일 작업 ───────────────────────────────────────────
    def _io_submit(self, work, done):
        self._io_pending += 1

        def fin(res, exc):
            try:
                done(res, exc)
            finally:
                self._io_finished()
        self.io.submit(work, fin, prio=5)

    def _io_finished(self):
        self._io_pending -= 1
        if self._closing and self._io_pending <= 0:
            self._closing = False
            self.destroy()

    def delete(self):
        path = self.cur_path
        if path is None:
            return
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text="이 파일을 삭제하시겠습니까?")
        d.format_secondary_text(f"{os.path.basename(path)}\n휴지통으로 옮깁니다.")
        d.add_button("아니요", Gtk.ResponseType.NO)
        d.add_button("예", Gtk.ResponseType.YES)
        d.set_default_response(Gtk.ResponseType.YES)

        def resp(dlg, r):
            dlg.destroy()
            if r == Gtk.ResponseType.YES and self.cur_path == path:
                self._do_delete(path, trash=True)
        d.connect("response", resp)
        d.show_all()

    def _do_delete(self, path, trash):
        if path == self.cur_path:
            self.rot = 0                     # 지울 그림의 회전은 저장하지 않는다

        def work(job):
            f = Gio.File.new_for_path(path)
            if trash:
                try:
                    f.trash(None)
                except GLib.Error as e:
                    if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                        return "no-trash"
                    raise
            else:
                f.delete(None)
            return "ok"

        def done(res, exc):
            if self._dead:
                return
            if exc is not None:
                self._notice("삭제하지 못했습니다", f"{os.path.basename(path)}\n{error_text(exc)}")
                return
            if res == "no-trash":
                self._confirm_permanent(path)
                return
            self._removed(path)
        self._io_submit(work, done)

    def _confirm_permanent(self, path):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.NONE, text="휴지통으로 옮길 수 없습니다")
        d.format_secondary_text(f"{os.path.basename(path)}\n이 위치에는 휴지통이 없습니다. 완전히 삭제하시겠습니까? "
                                "삭제한 파일은 되살릴 수 없습니다.")
        d.add_button("아니요", Gtk.ResponseType.NO)
        b = d.add_button("예", Gtk.ResponseType.YES)
        b.get_style_context().add_class("destructive-action")
        d.set_default_response(Gtk.ResponseType.NO)

        def resp(dlg, r):
            dlg.destroy()
            if r == Gtk.ResponseType.YES:
                self._do_delete(path, trash=False)
        d.connect("response", resp)
        d.show_all()

    def _removed(self, path):
        """지운 파일을 목록에서 빼고 다음 그림으로 (마지막이었으면 앞 그림)"""
        self.cache.pop(path, None)
        job = self.loading.pop(path, None)
        if job is not None:
            job.cancelled = True
        if path not in self.files:
            return
        i = self.files.index(path)
        self.files.pop(i)
        if path != self.cur_path:
            if i < self.index:
                self.index -= 1
            self._update_pos()
            self._update_arrows()
            return
        self.entry = None
        self.rot = 0
        self.index = -1
        if self.files:
            self.go(min(i, len(self.files) - 1))
        else:
            self._show(None)

    def rename(self):
        path = self.cur_path
        if path is None:
            return
        self._close_rename()
        name = os.path.basename(path)
        pop = Gtk.Popover.new(self.name_btn)
        pop.set_position(Gtk.PositionType.BOTTOM)
        pop.get_style_context().add_class("ph-rename")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        t = Gtk.Label(label="이름 바꾸기", xalign=0)
        t.get_style_context().add_class("ph-pop-title")
        box.pack_start(t, False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(name)
        entry.set_width_chars(36)
        box.pack_start(entry, False, False, 0)
        err = Gtk.Label(xalign=0)
        err.set_line_wrap(True)
        err.set_max_width_chars(40)
        err.get_style_context().add_class("ph-err")
        err.set_no_show_all(True)
        box.pack_start(err, False, False, 0)
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="취소")
        ok = Gtk.Button(label="저장")
        ok.get_style_context().add_class("accent-btn")
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        box.pack_start(btns, False, False, 0)
        pop.add(box)

        def fail(text):
            err.set_text(text)
            err.show()

        def save(*_):
            new = entry.get_text().strip()
            if not new:
                fail("파일 이름을 입력하세요.")
                return
            if "/" in new or new in (".", "..") or "\0" in new:
                fail("파일 이름에는 / 를 쓸 수 없습니다." if "/" in new else "쓸 수 없는 파일 이름입니다.")
                return
            if new == name:
                pop.popdown()
                return
            ok.set_sensitive(False)

            def work(job):
                return Gio.File.new_for_path(path).set_display_name(new, None).get_path()

            def done(newpath, exc):
                ok.set_sensitive(True)
                if self._dead:
                    return
                if exc is not None:
                    fail(rename_error(exc))
                    return
                pop.popdown()
                self._renamed(path, newpath)
            self._io_submit(work, done)
        entry.connect("activate", save)
        ok.connect("clicked", save)
        cancel.connect("clicked", lambda *_: pop.popdown())
        pop.connect("closed", lambda p: (self._rename_pop is p and setattr(self, "_rename_pop", None),
                                         self.canvas.grab_focus()))
        self._rename_pop = pop
        if self._fullscreen:
            self.fs_rev.set_reveal_child(True)
        box.show_all()
        pop.popup()
        entry.grab_focus()
        entry.select_region(0, _stem_len(name))

    def _close_rename(self):
        if self._rename_pop is not None:
            self._rename_pop.popdown()
            self._rename_pop = None

    def _renamed(self, old, new):
        self._own_writes[old] = self._own_writes[new] = time.monotonic()
        e = self.cache.pop(old, None)
        if e is not None:
            e.path = new
            self.cache[new] = e
        if old in self.files:
            self.files[self.files.index(old)] = new
            if not self.explicit:
                self.files.sort(key=lambda p: imageio.natural_key(os.path.basename(p)))
        if self.cur_path == old:
            self.cur_path = new
            if self.entry is not None:
                self.entry.path = new
            self.index = self.files.index(new) if new in self.files else self.index
            name = os.path.basename(new)
            self.set_title(f"{name} - {TITLE}")
            self.name_label.set_text(name)
            self._fill_info()
        self._update_pos()
        self._update_arrows()

    def save_as(self):
        e = self.entry
        if e is None or not e.ok:
            return
        writable = imageio.writable_formats()
        d = Gtk.FileChooserDialog(title="다른 이름으로 저장", transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "저장", Gtk.ResponseType.ACCEPT)
        d.set_default_response(Gtk.ResponseType.ACCEPT)
        d.set_do_overwrite_confirmation(True)
        d.set_current_folder(os.path.dirname(e.path))
        d.set_current_name(os.path.basename(e.path))
        filters = {}
        src_ext = ext_of(e.path)
        if e.fmt not in writable and src_ext:
            # GIF·SVG 처럼 GdkPixbuf 가 쓰지 못하는 형식 — 같은 형식이면 파일을 그대로 복사한다
            f = Gtk.FileFilter()
            f.set_name(f"{FMT_NAMES.get(e.fmt, e.fmt.upper() + ' 그림')} (*.{src_ext})")
            f.add_pattern("*." + src_ext)
            f.add_pattern("*." + src_ext.upper())
            d.add_filter(f)
            filters[f.get_name()] = (e.fmt, src_ext)
            d.set_filter(f)
        for label, exts, fmt in SAVE_TYPES:
            if fmt not in writable:
                continue
            f = Gtk.FileFilter()
            f.set_name(f"{label} (*.{exts[0]})")
            for x in exts:
                f.add_pattern("*." + x)
                f.add_pattern("*." + x.upper())
            d.add_filter(f)
            filters[f.get_name()] = (fmt, exts[0])
            if fmt == e.fmt:
                d.set_filter(f)
        src_path, src_fmt, anim = e.path, e.fmt, e.anim is not None

        def resp(dlg, r):
            if r != Gtk.ResponseType.ACCEPT:
                dlg.destroy()
                return
            dest = dlg.get_filename()
            flt = dlg.get_filter()
            dlg.destroy()
            if not dest:
                return
            ext = ext_of(dest)
            fmt = imageio.SAVE_EXT.get(ext)
            if not ext and flt is not None and flt.get_name() in filters:
                fmt, x = filters[flt.get_name()]
                dest += "." + x
            elif fmt is None and ext and ext == src_ext.lower():
                fmt = src_fmt
            q = self.rot % 4
            if fmt == src_fmt and fmt not in writable and q:
                self._notice("회전한 상태로 저장할 수 없는 형식입니다",
                             "PNG 나 JPEG 같은 다른 형식을 고르거나, 돌리지 않고 저장하세요.")
                return
            if fmt is None or (fmt not in writable and fmt != src_fmt):
                names = ", ".join("." + x for _l, xs, f in SAVE_TYPES if f in writable for x in xs[:1])
                self._notice("이 형식으로는 저장할 수 없습니다", f"파일 이름의 확장자를 {names} 중 하나로 하세요.")
                return
            same = os.path.realpath(dest) == os.path.realpath(src_path)

            def work(job):
                imageio.export(src_path, src_fmt, dest, fmt, q, anim)
                return True

            def done(_res, exc):
                if self._dead:
                    return
                self._own_writes[dest] = time.monotonic()
                if exc is not None:
                    self._notice("저장하지 못했습니다", f"{os.path.basename(dest)}\n{error_text(exc)}")
                    return
                if same and q and self.entry is e:
                    e.pre_rot = (e.pre_rot + q) % 4   # 지금 파일에 돌린 채로 썼다 — 떠날 때 또 저장하지 않게
                    self.rot = (self.rot - q) % 4
                self.toast("저장했습니다", secs=2)
            self._io_submit(work, done)
        d.connect("response", resp)
        d.show()

    def copy(self):
        e = self.entry
        if e is None or not e.ok:
            return
        pre, q = e.pre_rot, self.rot
        if e.reduced and e.src is e.small:
            # 줄여 읽은 큰 그림 — 원본을 읽는다 (io 줄: 저장 중인 회전이 끝난 파일을 읽으므로 보기 회전만)
            path, fmt = e.path, e.fmt

            def work(job):
                return imageio.rotate(imageio.load_full(path, fmt), q)
        else:
            src = e.src

            def work(job):
                return imageio.rotate(src, pre + q)

        def done(pb, exc):
            if self._dead:
                return
            if exc is not None or pb is None:
                self.toast("복사하지 못했습니다 — " + error_text(exc) if exc else "복사하지 못했습니다")
                return
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_image(pb)
            cb.store()
            self.toast("복사했습니다", secs=2)
        self._io_submit(work, done)

    def open_location(self):
        path = self.cur_path
        if path is None:
            return
        try:
            p = Gio.Subprocess.new(["sekai-files", "--select", path], Gio.SubprocessFlags.NONE)
            p.wait_async(None, None, None)       # 끝나면 거둔다
        except GLib.Error as e:
            dbg("sekai-files 를 띄우지 못함 — 파일 관리자 D-Bus 로", e.message)
            show_in_file_manager(path)

    def set_wallpaper(self):
        e = self.entry
        if e is None or not e.ok:
            return
        if self.rot and e.fmt in ROTATE_SAVE:
            self._commit_rotation()              # 돌려 둔 채로 배경이 되게 먼저 저장한다 (같은 줄이라 저장 뒤에 설정)
        path = os.path.abspath(e.path)

        def work(job):
            # 설정 앱과 같은 길 — settings.json 에 쓰고 sekai-wallpaper --apply (바탕화면이 다시 읽는다)
            from sekaisettings.store import Store
            Store().set("wallpaper", "path", path)
            return True

        def done(_res, exc):
            if self._dead:
                return
            if exc is not None:
                self.toast("바탕 화면 배경을 바꾸지 못했습니다 — " + error_text(exc), secs=6)
                return
            self.toast("바탕 화면 배경으로 설정했습니다")
        self._io_submit(work, done)

    def _notice(self, title, text):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.NONE, text=title)
        d.format_secondary_text(text)
        d.add_button("확인", Gtk.ResponseType.CLOSE)
        d.connect("response", lambda dlg, _r: dlg.destroy())
        d.show_all()

    # ── 전체 화면 · 슬라이드 쇼 ─────────────────────────────
    def toggle_fullscreen(self):
        if self._fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _apply_fullscreen(self):
        fs = self._fullscreen
        img = self.fs_btn.get_child()
        if fs:
            self.top_slot.remove(self.toolbar)
            self.fs_rev.add(self.toolbar)
            self.toolbar.get_style_context().add_class("ph-floating")
            self.fs_btn.set_tooltip_text("전체 화면 끝내기 (F11)")
            img.set_from_icon_name("view-restore-symbolic", Gtk.IconSize.BUTTON)
            if not self._slideshow:
                self.fs_rev.set_reveal_child(True)
                self._schedule_hide(HIDE_MS)
        else:
            self.fs_rev.set_reveal_child(False)
            self.fs_rev.remove(self.toolbar)
            self.top_slot.pack_start(self.toolbar, True, True, 0)
            self.toolbar.get_style_context().remove_class("ph-floating")
            self.fs_btn.set_tooltip_text("전체 화면 (F11)")
            img.set_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.BUTTON)
            self._tb_hover = False
            if self._slideshow:
                self._end_slideshow()
        self.toolbar.show()

    def slideshow(self):
        if self._slideshow or not self.files:
            return
        self._close_rename()
        self._slideshow = {"was_fs": self._fullscreen, "info": self.info_rev.get_reveal_child()}
        self.info_rev.set_reveal_child(False)
        self.toast_label.hide()
        self._update_arrows(show=False)
        self.canvas.set_hide_cursor(True)
        self.canvas.grab_focus()
        if self._fullscreen:
            self.fs_rev.set_reveal_child(False)
        else:
            self.fullscreen()
        # 초 단위 타이머(timeout_add_seconds)는 GLib 이 최대 1초까지 몰아 부른다 — 사진마다 간격이 들쭉날쭉했다
        self._ss_src = GLib.timeout_add(SLIDE_SECS * 1000, self._slide_next)

    def _slide_next(self):
        n = len(self.files)
        if n > 1 and self._slideshow:
            self.go((self.index + 1) % n)      # 끝까지 가면 처음부터 다시
        return self._slideshow is not None

    def _end_slideshow(self):
        ss = self._slideshow
        if ss is None:
            return
        self._slideshow = None
        if self._ss_src:
            GLib.source_remove(self._ss_src)
            self._ss_src = 0
        self.canvas.set_hide_cursor(False)
        if ss["info"]:
            self.set_info(True)
        if not ss["was_fs"] and self._fullscreen:
            self.unfullscreen()

    def _canvas_press(self, ev):
        if self._slideshow:
            self._end_slideshow()
            return True
        return False

    def canvas_fit(self):
        self.canvas.zoom_fit()

    # ── 키 ──────────────────────────────────────────────────
    def _on_key(self, _w, ev):
        kv = ev.keyval
        if self._slideshow:
            if not ev.is_modifier:
                self._end_slideshow()
                return True
            return False
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable) or (self._rename_pop is not None and self._rename_pop.get_visible()):
            return False
        st = ev.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.MOD1_MASK)
        ctrl = bool(st & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(st & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(st & Gdk.ModifierType.MOD1_MASK)
        K = Gdk
        if ctrl and not alt:
            low = Gdk.keyval_to_lower(kv)
            acts = {
                K.KEY_c: self.copy, K.KEY_s: self.save_as, K.KEY_o: self.open_dialog, K.KEY_w: self.close,
                K.KEY_r: lambda: self.rotate(-1 if shift else 1),
                K.KEY_plus: lambda: self.canvas.zoom_step(True), K.KEY_equal: lambda: self.canvas.zoom_step(True),
                K.KEY_KP_Add: lambda: self.canvas.zoom_step(True),
                K.KEY_minus: lambda: self.canvas.zoom_step(False),
                K.KEY_underscore: lambda: self.canvas.zoom_step(False),
                K.KEY_KP_Subtract: lambda: self.canvas.zoom_step(False),
                K.KEY_0: self.canvas_fit, K.KEY_KP_0: self.canvas_fit, K.KEY_KP_Insert: self.canvas_fit,
                K.KEY_1: lambda: self.canvas.zoom_actual(), K.KEY_KP_1: lambda: self.canvas.zoom_actual(),
                K.KEY_KP_End: lambda: self.canvas.zoom_actual(),
            }
            fn = acts.get(low) or acts.get(kv)
            if fn is not None:
                if self.cur_path is not None or fn in (self.open_dialog, self.close):
                    fn()
                return True
            return False
        if alt:
            if kv in (K.KEY_Return, K.KEY_KP_Enter):
                self.set_info(not self.info_rev.get_reveal_child())
                return True
            return False
        if kv in (K.KEY_Left, K.KEY_KP_Left, K.KEY_Page_Up, K.KEY_KP_Page_Up, K.KEY_BackSpace):
            self.go(self.index - 1)
        elif kv in (K.KEY_Right, K.KEY_KP_Right, K.KEY_Page_Down, K.KEY_KP_Page_Down):
            self.go(self.index + 1)
        elif kv == K.KEY_space:
            self.go(self.index + (-1 if shift else 1))
        elif kv in (K.KEY_Home, K.KEY_KP_Home):
            self.go(0)
        elif kv in (K.KEY_End, K.KEY_KP_End):
            self.go(len(self.files) - 1)
        elif kv in (K.KEY_Up, K.KEY_Down) and self.canvas.pannable():
            self.canvas.pan(0, 80 if kv == K.KEY_Up else -80)
        elif kv in (K.KEY_Delete, K.KEY_KP_Delete):
            self.delete()
        elif kv == K.KEY_F2:
            self.rename()
        elif kv == K.KEY_F5:
            self.slideshow()
        elif kv == K.KEY_F11:
            self.toggle_fullscreen()
        elif kv == K.KEY_Escape:
            if self._fullscreen:
                self.unfullscreen()
            else:
                return False
        elif kv in (K.KEY_i, K.KEY_I):
            self.set_info(not self.info_rev.get_reveal_child())
        elif kv in (K.KEY_plus, K.KEY_equal, K.KEY_KP_Add) and self.entry is not None:
            self.canvas.zoom_step(True)
        elif kv in (K.KEY_minus, K.KEY_KP_Subtract) and self.entry is not None:
            self.canvas.zoom_step(False)
        else:
            return False
        return True

    # ── 창 상태 · 닫기 ──────────────────────────────────────
    def _on_wstate(self, _w, ev):
        new = ev.new_window_state
        fs = bool(new & Gdk.WindowState.FULLSCREEN)
        if not fs:
            self.state["maximized"] = bool(new & Gdk.WindowState.MAXIMIZED)
        if fs != self._fullscreen:
            self._fullscreen = fs
            self._apply_fullscreen()
        return False

    def _on_delete(self, *_):
        self._end_slideshow()
        self._close_rename()
        self._leave_current()
        if not self.state.get("maximized") and not self._fullscreen:
            w, h = self.get_size()
            self.state["size"] = [w, h]
        save_state(self.state)
        self._unwatch()
        self._cancel_all_loads()
        if self._io_pending > 0:
            # 회전 저장 같은 파일 작업이 끝나기를 기다린다 — 창만 먼저 감춘다
            self._closing = True
            self.hide()
            return True
        self._dead = True
        return False

    def do_destroy(self):
        self._dead = True
        for attr in ("_spin_src", "_toast_src", "_arrow_src", "_tb_src", "_ss_src", "_relist_src"):
            src = getattr(self, attr, 0)
            if src:
                GLib.source_remove(src)
                setattr(self, attr, 0)
        Gtk.ApplicationWindow.do_destroy(self)
