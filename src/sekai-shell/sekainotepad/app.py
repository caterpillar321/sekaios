"""SekaiOS 메모장 — 앱 (창들 · 메뉴 모델 · 단축키 · 설정 · 최근 파일 · 색 따라가기).

실행: sekai-notepad [--new-window] [--encoding=<키>] [파일 …]
    이미 떠 있으면 새 프로세스를 만들지 않는다 — 파일은 가장 최근에 쓴 창의 새 탭으로
    (설정 "파일 열기: 새 창에서 열기" 나 --new-window 면 새 창). 파일 없이 부르면 새 탭(또는 새 창).
    --encoding: utf-8 · utf-8-bom · utf-16-le · utf-16-be · cp949 · latin-1 (자동 검색 대신)

설정(글꼴·자동 줄 바꿈·상태 표시줄·파일 열기)·창 크기·최근 파일은 ~/.local/state/sekai/notepad.json.
설정 앱에서 모드·강조색을 바꾸면(~/.config/sekai/settings.json) 곧바로 따라가고,
시스템 고정폭 글꼴(gsettings monospace-font-name)이 바뀌면 "기본값" 글꼴을 쓰는 탭이 따라간다.
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkSource  # noqa: E402

from sekaishell import dbg, theme  # noqa: E402

from . import textcodec  # noqa: E402
from .common import (APP_ICONS, APP_ID, APP_NAME, RECENT_MAX, appearance, interface_settings,  # noqa: E402
                     load_css, load_state, menu_label, save_state, system_mono_font)
from .window import NotepadWindow  # noqa: E402

# 동작 → 단축키 (메뉴에 보이는 것도 여기서)
ACCELS = {
    "win.new-tab": ["<Control>n"],
    "win.new-window": ["<Control><Shift>n"],
    "win.open": ["<Control>o"],
    "win.save": ["<Control>s"],
    "win.save-as": ["<Control><Shift>s"],
    "win.save-all": ["<Control><Alt>s"],
    "win.close-tab": ["<Control>w", "<Control>F4"],
    "win.close-window": ["<Control><Shift>w"],
    "win.undo": ["<Control>z"],
    "win.redo": ["<Control>y", "<Control><Shift>z"],
    "win.cut": ["<Control>x"],
    "win.copy": ["<Control>c"],
    "win.paste": ["<Control>v"],
    "win.find": ["<Control>f"],
    "win.find-next": ["F3"],
    "win.find-prev": ["<Shift>F3"],
    "win.replace": ["<Control>h"],
    "win.goto": ["<Control>g"],
    "win.select-all": ["<Control>a"],
    "win.time-date": ["F5"],
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
}


def _item(label, action, accel=None):
    it = Gio.MenuItem.new(label, action)
    if accel:
        it.set_attribute_value("accel", GLib.Variant("s", accel))     # 보이기만 (키는 글 칸이 직접)
    return it


def _section(*items):
    s = Gio.Menu()
    for it in items:
        if isinstance(it, Gio.MenuItem):
            s.append_item(it)
        else:
            s.append(*it)
    return s


class NotepadApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.prefs = {}
        self._css = None
        self._accent = None
        self._cfg_src = 0
        self.menu_model = None
        self.recent_menu = None

    # ── 시작 ──
    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name(APP_ICONS[0])
        self.prefs = load_state()
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self._css = load_css(a)
        self._accent = a["accent"]
        self._build_menu()
        for act, keys in ACCELS.items():
            self.set_accels_for_action(act, keys)
        self._watch_settings()
        iface = interface_settings()
        if iface is not None:
            iface.connect("changed::monospace-font-name", lambda *_: self._font_changed())

    def _build_menu(self):
        self.recent_menu = Gio.Menu()
        self._rebuild_recent()
        m = Gio.Menu()
        f = Gio.Menu()
        f.append_section(None, _section(("새 탭", "win.new-tab"), ("새 창", "win.new-window"),
                                        ("열기…", "win.open")))
        recent = Gio.Menu()
        recent.append_submenu("최근 파일", self.recent_menu)
        f.append_section(None, recent)
        f.append_section(None, _section(("저장", "win.save"), ("다른 이름으로 저장…", "win.save-as"),
                                        ("모두 저장", "win.save-all")))
        f.append_section(None, _section(("탭 닫기", "win.close-tab"), ("창 닫기", "win.close-window"),
                                        ("끝내기", "win.exit")))
        m.append_submenu("파일", f)

        e = Gio.Menu()
        e.append_section(None, _section(("실행 취소", "win.undo"), ("다시 실행", "win.redo")))
        e.append_section(None, _section(("잘라내기", "win.cut"), ("복사", "win.copy"), ("붙여넣기", "win.paste"),
                                        _item("삭제", "win.delete", "Delete")))
        e.append_section(None, _section(("찾기", "win.find"), ("다음 찾기", "win.find-next"),
                                        ("이전 찾기", "win.find-prev"), ("바꾸기", "win.replace"),
                                        ("이동", "win.goto")))
        e.append_section(None, _section(("모두 선택", "win.select-all"), ("시간/날짜", "win.time-date")))
        e.append_section(None, _section(("글꼴", "win.font")))
        m.append_submenu("편집", e)

        v = Gio.Menu()
        z = Gio.Menu()
        z.append_item(_item("확대", "win.zoom-in", "<Control>plus"))
        z.append_item(_item("축소", "win.zoom-out", "<Control>minus"))
        z.append_item(_item("기본 확대/축소로 복원", "win.zoom-reset", "<Control>0"))
        zs = Gio.Menu()
        zs.append_submenu("확대/축소", z)
        v.append_section(None, zs)
        v.append_section(None, _section(("상태 표시줄", "win.statusbar"), ("자동 줄 바꿈", "win.wrap")))
        m.append_submenu("보기", v)
        self.menu_model = m

    # ── 명령줄 ──
    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        new_window, enc, files, only_files = False, None, [], False
        for a in args:
            if not only_files and a == "--":
                only_files = True
            elif not only_files and a in ("--new-window", "-w"):
                new_window = True
            elif not only_files and a.startswith("--encoding="):
                enc = a.split("=", 1)[1] or None
                if enc not in {e[0] for e in textcodec.ENCODINGS}:
                    print(f"[sekai-notepad] 모르는 인코딩: {enc}", file=sys.stderr)
                    enc = None
            elif not only_files and a.startswith("-") and a != "-":
                print(f"[sekai-notepad] 모르는 옵션: {a}", file=sys.stderr)
            else:
                p = cl.create_file_for_arg(a).get_path()
                if p:
                    files.append(p)
                else:
                    print(f"[sekai-notepad] 로컬 파일이 아닙니다: {a}", file=sys.stderr)
        win = self.recent_window()
        if win is None or new_window or self.prefs.get("open_mode") == "window":
            win = self.new_window(files, enc)
        else:
            if files:
                win.open_paths(files, enc)
            else:
                win.new_doc()
            win.present()
        self._maybe_shot(win)
        return 0

    def new_window(self, files=None, enc=None):
        win = NotepadWindow(self)
        win.show_all()
        if files:
            win.open_paths(files, enc)
        if not win.docs():
            win.new_doc()
        win.present()
        return win

    def windows(self):
        return [w for w in self.get_windows() if isinstance(w, NotepadWindow) and not w._closed]

    def recent_window(self):
        """가장 최근에 쓴 창 (GtkApplication 이 초점을 받은 순서로 늘어놓는다)"""
        ws = self.windows()
        return ws[0] if ws else None

    def find_open(self, path):
        for w in self.windows():
            for d in w.docs():
                if d.path and os.path.abspath(d.path) == path:
                    return w, d
        return None

    def quit_all(self):
        """끝내기 — 창마다 저장 여부를 묻고, 하나라도 취소하면 멈춘다"""
        ws = self.windows()

        def step(i):
            if i >= len(ws):
                return
            w = ws[i]
            if w._closed:
                step(i + 1)
                return
            w.present()

            def done(ok):
                if ok:
                    w._finish_close()
                    step(i + 1)
            w.close_all(done)
        step(0)

    # ── 설정 ──
    def font(self):
        return self.prefs.get("font") or system_mono_font()

    def set_pref(self, key, value):
        if self.prefs.get(key) == value and key in self.prefs:
            return
        self.prefs[key] = value
        save_state(self.prefs)
        for w in self.windows():
            w.apply_prefs()

    def _font_changed(self):
        if not self.prefs.get("font"):
            for w in self.windows():
                w.apply_prefs()

    def remember_window(self, win):
        """창을 닫을 때 크기 — 다음 창이 같은 크기로"""
        self.prefs["maximized"] = bool(win._maximized)
        if not win._maximized:
            w, h = win.get_size()
            self.prefs["size"] = [w, h]
        save_state(self.prefs)

    def last_dir(self):
        d = self.prefs.get("last_dir")
        if isinstance(d, str) and os.path.isdir(d):
            return d
        return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or os.path.expanduser("~")

    def set_last_dir(self, d):
        if d and d != self.prefs.get("last_dir"):
            self.prefs["last_dir"] = d
            save_state(self.prefs)

    # ── 최근 파일 ──
    def add_recent(self, path):
        lst = [p for p in (self.prefs.get("recent") or []) if isinstance(p, str) and p != path]
        self.prefs["recent"] = [path] + lst[:RECENT_MAX - 1]
        save_state(self.prefs)
        self._rebuild_recent()
        try:                                          # 파일 탐색기의 "최근 항목"에도
            Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        except Exception as e:
            dbg("최근 항목 추가 실패", e)

    def clear_recent(self):
        self.prefs["recent"] = []
        save_state(self.prefs)
        self._rebuild_recent()

    def _rebuild_recent(self):
        m = self.recent_menu
        m.remove_all()
        lst = [p for p in (self.prefs.get("recent") or []) if isinstance(p, str)]
        files = Gio.Menu()
        if not lst:
            files.append("최근 파일 없음", "win.none")          # 없는 동작 — 흐리게 보인다
        home = os.path.expanduser("~")
        for p in lst:
            d = os.path.dirname(p)
            if d == home or d.startswith(home + os.sep):
                d = "~" + d[len(home):]
            it = Gio.MenuItem.new(menu_label(f"{os.path.basename(p)}   —   {d}"), None)
            it.set_action_and_target_value("win.open-recent", GLib.Variant("s", p))
            files.append_item(it)
        m.append_section(None, files)
        if lst:
            m.append_section(None, _section(("목록 지우기", "win.clear-recent")))

    # ── 색 따라가기 ──
    def match_style(self):
        """찾은 글의 바탕 — 강조색을 옅게"""
        h = (self._accent or "#39c5bb").lstrip("#")
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            r, g, b = 57, 197, 187
        return GtkSource.Style(background=f"rgba({r},{g},{b},0.30)", background_set=True)

    def _watch_settings(self):
        """설정 앱이 settings.json 을 바꿔치기(원자적 저장)하므로 폴더를 본다 — 컴퓨터 관리와 같은 방법"""
        cfg_dir = os.path.expanduser("~/.config/sekai")
        try:
            os.makedirs(cfg_dir, exist_ok=True)
            self._cfg_mon = Gio.File.new_for_path(cfg_dir).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except (GLib.Error, OSError) as e:
            dbg("설정 폴더를 볼 수 없습니다:", e)
            return

        def changed(_m, f, other, _ev):
            names = {x.get_basename() for x in (f, other) if x is not None}
            if "settings.json" not in names:
                return
            if self._cfg_src:
                GLib.source_remove(self._cfg_src)
            self._cfg_src = GLib.timeout_add(200, self._reload_theme)
        self._cfg_mon.connect("changed", changed)

    def _reload_theme(self):
        self._cfg_src = 0
        a = appearance()
        if self._css is not None:
            Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self._css)
        self._css = load_css(a)
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        if a["accent"] != self._accent:
            self._accent = a["accent"]
            style = self.match_style()
            for w in self.windows():
                w.findbar.restyle(style)
        for w in self.windows():
            w.queue_draw()
        return False

    # ── 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 끝낸다 (설정 앱·작업 관리자와 같은 방법) ──
    def _maybe_shot(self, win):
        shot = os.environ.get("SEKAI_SHOT")
        if not shot or getattr(self, "_shot_armed", False):
            return
        self._shot_armed = True

        def grab():
            # 창을 따로 그려 찍는다 — gdk_pixbuf_get_from_window 는 X11(기본 화면 모드)에서 cairo 단언으로 죽는다
            import cairo
            w, h = win.get_allocated_width(), win.get_allocated_height()
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            win.draw(cairo.Context(surf))
            surf.write_to_png(shot)
            print("shot:", shot, w, "x", h)
            for d in win.docs():
                d.buffer.set_modified(False)
            win._finish_close()
            self.quit()
            return False
        GLib.timeout_add(int(os.environ.get("SEKAI_SHOT_DELAY", "2500")), grab)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-notepad [--new-window] [--encoding=<인코딩>] [파일 …]")
        print("  인코딩:", ", ".join(e[0] for e in textcodec.ENCODINGS))
        return 0
    # X11(기본 화면 모드)에서도 창 클래스가 .desktop 의 StartupWMClass 와 같게
    GLib.set_prgname(APP_ID)
    return NotepadApp().run([sys.argv[0]] + list(argv))
