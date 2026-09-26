"""사진(sekai-photos) — 앱. 명령줄·색(모드·강조색)·작업 스레드.

실행: sekai-photos [--new-window] [파일 또는 폴더 …]
    파일 하나      그 그림을 열고 같은 폴더의 그림을 ←/→ 로 넘긴다
    폴더          그 폴더의 첫 그림
    여러 파일      고른 것들만 넘긴다 (탐색기에서 여러 개를 골라 열 때)
    이미 떠 있으면 새 창을 만들지 않고 마지막으로 쓴 창에서 연다 (윈도우 사진 앱처럼).
    --new-window 를 붙이면 새 창에서 (탐색기에서 Shift 를 누르고 열 때 쓰라고).

색은 설정 앱·작업 관리자와 같다 — settings.css 앞에 강조색·모드를 붙이고 이 앱의 모양(PHOTOS_CSS)을 더한다.
설정 앱에서 모드·강조색을 바꾸면 곧바로 따라간다. 창 크기·최대화·정보 칸은 ~/.local/state/sekai/photos.json.
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import dbg, theme  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402

from .imageio import Pool  # noqa: E402
from .window import PhotosWindow  # noqa: E402

APP_ID = "org.sekaios.Photos"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]

# settings.css 위에 얹는 이 앱의 모양. 그림 칸 바탕(ph_canvas)은 다크 모드면 거의 검정, 라이트면 옅은 회색
PHOTOS_CSS = """
.ph-window { background: @ph_canvas; color: @fg; }
.ph-toolbar {
    background: @winbg;
    border-bottom: 1px solid @line;
    padding: 6px 10px;
}
.ph-toolbar.ph-floating { background: alpha(@winbg, 0.94); }
.ph-toolbar button {
    background: transparent;
    border-color: transparent;
    padding: 6px 9px;
}
.ph-toolbar button:hover { background: @hover; }
.ph-toolbar button:active, .ph-toolbar button:checked { background: @pressed; }
.ph-toolbar button:disabled { opacity: 0.4; background: transparent; }
.ph-toolbar image { color: @fg; }
button.ph-name { padding: 6px 10px; }
button.ph-name label { font-weight: 600; color: @fg; }
label.ph-pos { color: @text2; font-size: 13px; font-feature-settings: "tnum"; }
button.ph-zoom label { font-size: 13px; font-feature-settings: "tnum"; }
separator.ph-sep { background: @line; min-width: 1px; margin: 8px 4px; }
button.ph-nav {
    background: alpha(@surface, 0.86);
    border: 1px solid @line;
    border-radius: 8px;
    padding: 16px 8px;
    margin: 0 14px;
    box-shadow: 0 2px 8px alpha(#000000, 0.25);
}
button.ph-nav:hover { background: @card; }
button.ph-nav image { color: @fg; }
.ph-info { background: @winbg; border-left: 1px solid @line; }
.ph-info-head { padding: 14px 10px 8px 20px; }
label.ph-info-title { font-size: 16px; font-weight: 700; color: @fg; }
button.ph-info-close { background: transparent; border-color: transparent; padding: 6px; }
button.ph-info-close:hover { background: @hover; }
.ph-info-body { padding: 6px 20px 24px 20px; }
label.ph-info-key { font-size: 12px; color: @text2; }
label.ph-info-val { color: @fg; }
button.ph-link { background: transparent; border-color: transparent; padding: 0; }
button.ph-link label { color: mix(@accent, @fg, 0.25); }
button.ph-link:hover label { text-decoration-line: underline; }
.ph-toast {
    background: @card;
    border: 1px solid alpha(@accent, 0.45);
    border-radius: 8px;
    padding: 8px 14px;
    margin-bottom: 28px;
    color: @fg;
}
label.ph-msg-title { font-size: 18px; font-weight: 600; color: @fg; }
label.ph-msg-sub { color: @text2; }
image.ph-msg-icon { color: @text3; opacity: 0.6; }
label.ph-pop-title { font-weight: 600; }
label.ph-err { color: mix(#e0453a, @fg, 0.25); font-size: 12px; }
popover.ph-rename, popover.ph-rename > * { background: @card; color: @fg; }
"""


def _load_css(a):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    prelude += "@define-color ph_canvas %s;\n" % (
        "mix(@bg, #000000, 0.35)" if a.get("mode") != "light" else "mix(@bg, #ffffff, 0.2)")
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + PHOTOS_CSS).encode())
    except GLib.Error as e:
        print("[sekai-photos] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return None
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


def parse_args(args):
    """(파일 인자들, 새 창인지) — 파일 인자는 부른 쪽의 작업 폴더 기준으로 do_command_line 이 GFile 로 바꾼다"""
    files, new, only_files = [], False, False
    for a in args:
        if not only_files and a == "--":
            only_files = True
        elif not only_files and a in ("--new-window", "-n"):
            new = True
        elif not only_files and a.startswith("-") and a != "-":
            dbg("모르는 선택 사항", a)
        else:
            files.append(a)
    return files, new


class PhotosApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.pools = None
        self._css = None
        self._cfg_mon = None
        self._cfg_src = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("사진")
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self._css = _load_css(a)
        # 읽기 2 · 고품질 그리기 1 · 파일 쓰기 1 (파일을 바꾸는 일은 한 줄로 차례대로)
        self.pools = (Pool(2, "load"), Pool(1, "render"), Pool(1, "io"))
        self._watch_settings()

    def do_command_line(self, cl):
        names, new = parse_args(cl.get_arguments()[1:])
        paths, bad = [], []
        for n in names:
            p = cl.create_file_for_arg(n).get_path()
            (paths if p else bad).append(p or n)
        win = None if new else self.get_active_window()
        if win is not None and (getattr(win, "_closing", False) or not win.get_visible()):
            # 닫았지만 회전 저장을 마치려고 숨겨 둔 창 — 여기에 열면 저장이 끝나는 순간 창째 사라졌다
            win = next((w for w in self.get_windows()
                        if w.get_visible() and not getattr(w, "_closing", False)), None)
        fresh = win is None
        if fresh:
            win = PhotosWindow(self, self.pools)
            win.show_all()
            win.canvas.grab_focus()           # 키(←/→·Space)가 처음부터 그림 넘기기로 (도구 모음 단추가 아니라)
        if paths:
            win.open_paths(paths)
        elif fresh:
            win.show_empty()
        if bad:
            win.toast("이 위치의 파일은 열 수 없습니다: " + ", ".join(bad))
        win.present()

        # 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 종료한다 (설정 앱·작업 관리자와 같은 방법)
        shot = os.environ.get("SEKAI_SHOT")
        if shot and fresh:
            def grab():
                gw = win.get_window()
                if gw is not None:
                    pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
                    if pb:
                        pb.savev(shot, "png", [], [])
                        print("shot:", shot, gw.get_width(), "x", gw.get_height())
                win.close()
                return False
            GLib.timeout_add(int(os.environ.get("SEKAI_SHOT_DELAY", "2500")), grab)
        return 0

    # ── 설정(색) 따라가기 ──
    def _watch_settings(self):
        """설정 앱이 settings.json 을 바꿔치기(원자적 저장)하므로 폴더를 본다"""
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
        self._css = _load_css(a)
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        for w in self.get_windows():
            if isinstance(w, PhotosWindow):
                w.theme_changed()
                w.queue_draw()
        return False


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-photos [--new-window] [파일 또는 폴더 …]")
        print("  파일 하나를 열면 같은 폴더의 사진을 ←/→ 로 넘겨 봅니다.")
        print("  이미 열려 있으면 그 창에서 엽니다. --new-window 는 새 창에서.")
        return 0
    # 창 클래스가 .desktop 의 StartupWMClass(org.sekaios.Photos)와 같게 — 안 하면 Wayland 에서 'sekai-photos'
    GLib.set_prgname(APP_ID)
    return PhotosApp().run([sys.argv[0]] + list(argv))
