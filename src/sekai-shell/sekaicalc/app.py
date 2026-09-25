"""SekaiOS 계산기 — 앱 (창 하나 · 모양 · 상태 파일 · 색 따라가기).

실행: sekai-calc [--standard | --scientific]
    이미 떠 있으면 그 창을 앞으로 (모드를 말했으면 그 모드로).

모드·모드마다의 창 크기·각도 단위는 ~/.local/state/sekai/calc.json. 기록·메모리는 창을 닫으면 사라진다 (윈도우처럼).
색은 컴퓨터 관리·작업 관리자와 같다 — settings.css 앞에 강조색·모드를 붙이고 이 앱의 모양(CALC_CSS)을 더하며,
설정 앱에서 모드·강조색을 바꾸면 곧바로 따라간다.
"""
import json
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import dbg, theme  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402

from .window import CalcWindow  # noqa: E402

APP_ID = "org.sekaios.Calculator"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
STATE = os.path.expanduser("~/.local/state/sekai/calc.json")

# 모드마다 다른 단추 색 — 윈도우 11 계산기처럼 숫자 단추가 기능 단추보다 밝다 (라이트는 숫자가 흰색)
_MODE_COLORS = {
    "dark": """
@define-color c_bg      @surface;
@define-color c_fn      mix(@surface, #ffffff, 0.055);
@define-color c_num     mix(@surface, #ffffff, 0.105);
@define-color c_fn_hi   mix(@surface, #ffffff, 0.09);
@define-color c_num_hi  mix(@surface, #ffffff, 0.14);
@define-color c_down    mix(@surface, #ffffff, 0.035);
@define-color c_edge    alpha(#ffffff, 0.045);
@define-color c_edge_b  alpha(#000000, 0.18);
@define-color c_panel   mix(@surface, #ffffff, 0.04);
@define-color c_scrim   alpha(#000000, 0.30);
""",
    "light": """
@define-color c_bg      mix(@surface, @bg, 0.55);
@define-color c_fn      mix(@surface, #ffffff, 0.45);
@define-color c_num     mix(@surface, #ffffff, 0.92);
@define-color c_fn_hi   mix(@surface, @fg, 0.035);
@define-color c_num_hi  mix(@surface, #ffffff, 0.55);
@define-color c_down    mix(@surface, @fg, 0.07);
@define-color c_edge    alpha(#000000, 0.055);
@define-color c_edge_b  alpha(#000000, 0.13);
@define-color c_panel   mix(@surface, #ffffff, 0.7);
@define-color c_scrim   alpha(#000000, 0.12);
""",
}

CALC_CSS = """
.calc-window { background: @c_bg; color: @fg; }
.c-column { padding: 0 4px 4px 4px; }
.c-top { padding: 6px 2px 0 2px; }
label.c-title { font-size: 20px; font-weight: 700; color: @fg; }
button.c-flat {
    background: transparent;
    background-image: none;
    border: none;
    border-radius: 5px;
    box-shadow: none;
    padding: 7px 9px;
}
button.c-flat:hover { background: @c_fn_hi; }
button.c-flat:active { background: @c_down; }
button.c-flat image { color: @fg; }

/* 식 줄과 결과 */
.c-display { padding: 8px 12px 4px 12px; }
label.c-expr { color: @text2; font-size: 14px; }
label.c-result { color: @fg; font-weight: 600; font-size: 46px; }

/* 공학용 — DEG · F-E · 삼각법 · 함수 */
button.c-mode-btn {
    background: transparent;
    background-image: none;
    border: none;
    border-radius: 5px;
    box-shadow: none;
    padding: 5px 10px;
    min-height: 0;
}
button.c-mode-btn label { font-size: 13px; font-weight: 600; color: @fg; }
button.c-mode-btn:hover { background: @c_fn_hi; }
button.c-mode-btn:checked { background: alpha(@accent, 0.22); }
button.c-mode-btn:disabled label { color: @text3; }
popover { background: @c_panel; border: 1px solid @line; border-radius: 8px; padding: 0; }

/* 메모리 줄 */
.c-memrow { margin: 2px 0; }
button.c-mem {
    background: transparent;
    background-image: none;
    border: none;
    border-radius: 5px;
    box-shadow: none;
    padding: 6px 0;
    min-height: 0;
}
button.c-mem label { font-size: 12px; font-weight: 600; color: @fg; }
button.c-mem:hover { background: @c_fn_hi; }
button.c-mem:disabled label { color: @text3; }

/* 자판 */
.c-pad { margin-top: 2px; }
button.c-key {
    background-image: none;
    border: 1px solid @c_edge;
    border-bottom-color: @c_edge_b;
    border-radius: 5px;
    box-shadow: none;
    padding: 0;
    min-height: 30px;
    min-width: 40px;
    transition: background-color 60ms ease-out;
}
button.c-key label { color: @fg; }
button.c-fn { background-color: @c_fn; }
button.c-fn label { font-size: 15px; }
button.c-fn:hover { background-color: @c_fn_hi; }
button.c-num { background-color: @c_num; }
button.c-num label { font-size: 18px; font-weight: 600; }
button.c-num:hover { background-color: @c_num_hi; }
button.c-key:active, button.c-key:checked { background-color: @c_down; }
button.c-key:active label { color: @text2; }
button.c-key:disabled { background-color: alpha(@c_fn, 0.5); }
button.c-key:disabled label { color: @text3; }
button.c-fn.on { background-color: alpha(@accent, 0.28); }
button.c-eq { background-color: @accent; border-color: mix(@accent, #000000, 0.08); }
button.c-eq label { color: @c_on_accent; font-size: 22px; font-weight: 500; }
button.c-eq:hover { background-color: mix(@accent, @c_bg, 0.10); }
button.c-eq:active { background-color: mix(@accent, @c_bg, 0.22); }
button.c-eq:active label { color: alpha(@c_on_accent, 0.8); }
button.c-eq:disabled { background-color: alpha(@accent, 0.35); }

/* 기록 · 메모리 */
.c-side { padding: 6px 6px 4px 10px; }
.c-tabs { margin: 2px 0 8px 2px; }
button.c-tab {
    background: transparent;
    background-image: none;
    border: none;
    border-radius: 0;
    box-shadow: none;
    padding: 6px 4px;
    margin-right: 14px;
    border-bottom: 3px solid transparent;
}
button.c-tab label { font-size: 14px; font-weight: 600; color: @text2; }
button.c-tab:hover label { color: @fg; }
button.c-tab.on { border-bottom-color: @accent; }
button.c-tab.on label { color: @fg; }
list.c-list, .c-list row { background: transparent; }
.c-item { padding: 8px 10px; border-radius: 6px; }
.c-item:hover { background: @c_fn_hi; }
label.c-item-expr { color: @text2; font-size: 13px; }
label.c-item-value { color: @fg; font-size: 20px; font-weight: 600; }
label.c-empty { color: @text2; font-size: 13px; padding: 12px 12px; }
.c-mem-btns { opacity: 0; }
.c-item:hover .c-mem-btns { opacity: 1; }
button.c-mem-item {
    background: @c_fn;
    background-image: none;
    border: 1px solid @c_edge;
    border-radius: 4px;
    box-shadow: none;
    padding: 2px 8px;
    min-height: 0;
}
button.c-mem-item label { font-size: 11px; font-weight: 600; color: @fg; }
button.c-mem-item:hover { background: @c_num_hi; }
.c-listbar { padding: 2px; }

/* 덮는 칸 */
.c-scrim { background-color: @c_scrim; }
.c-flyout {
    background: @c_panel;
    border: 1px solid @line;
    border-bottom: none;
    border-radius: 8px 8px 0 0;
    padding: 8px 6px 4px 6px;
}
.c-nav {
    background: @c_panel;
    border-right: 1px solid @line;
    padding: 6px 6px;
    box-shadow: 2px 0 12px alpha(#000000, 0.25);
}
label.c-nav-cap { font-size: 13px; font-weight: 600; color: @text2; padding: 12px 10px 6px 10px; }
button.c-nav-row {
    background: transparent;
    background-image: none;
    border: none;
    border-radius: 6px;
    box-shadow: none;
    padding: 9px 10px;
    background-size: 3px 16px;
    background-position: 0% 50%;
    background-repeat: no-repeat;
}
button.c-nav-row:hover { background-color: @c_fn_hi; }
button.c-nav-row.on {
    background-color: @c_fn;
    background-image: linear-gradient(@accent, @accent);
}
button.c-nav-row label { color: @fg; font-size: 14px; }
button.c-nav-row image { color: @fg; }
"""


def _on_accent(hexcolor):
    """강조색 위의 글자색 — 밝은 강조색엔 검정, 어두운 강조색엔 흰색 (= 단추가 어떤 강조색에서도 읽히게)"""
    h = hexcolor.lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#000000"
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#000000" if lum > 0.28 else "#ffffff"


def _load_css(a):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    prelude += f"@define-color c_on_accent {_on_accent(a['accent'])};\n"
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + _MODE_COLORS.get(a["mode"], _MODE_COLORS["dark"])
                             + CALC_CSS).encode())
    except GLib.Error as e:
        print("[sekai-calc] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return None
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class CalcApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.state = {}
        self.win = None
        self._css = None
        self._cfg_src = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("계산기")
        self.state = _load_state()
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self._css = _load_css(a)
        self._watch_settings()

    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        mode = None
        for a in args:
            if a in ("--scientific", "--sci"):
                mode = "scientific"
            elif a in ("--standard", "--std"):
                mode = "standard"
            else:
                print(f"[sekai-calc] 모르는 옵션: {a}", file=sys.stderr)
        if self.win is None:
            if mode:
                self.state["mode"] = mode
            self.win = CalcWindow(self)
            self.win.connect("destroy", lambda *_: setattr(self, "win", None))
            self._maybe_shot()
        elif mode:
            self.win.set_mode(mode)
        self.win.present()
        return 0

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            tmp = f"{STATE}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATE)
        except (OSError, TypeError, ValueError) as e:
            dbg("계산기 상태 저장 실패", e)

    # ── 색 따라가기 (컴퓨터 관리와 같은 방법 — 설정 앱이 settings.json 을 바꿔치기하므로 폴더를 본다) ──
    def _watch_settings(self):
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
        if self.win is not None:
            self.win.queue_draw()
        return False

    # ── 개발용: SEKAI_SHOT=/경로.png 이면 창을 찍고 끝낸다 (설정 앱·작업 관리자와 같은 방법) ──
    def _maybe_shot(self):
        shot = os.environ.get("SEKAI_SHOT")
        if not shot:
            return
        win = self.win

        def grab():
            # 창을 따로 그려 찍는다 — gdk_pixbuf_get_from_window 는 X11(기본 화면 모드)에서 cairo 단언으로 죽는다
            import cairo
            w, h = win.get_allocated_width(), win.get_allocated_height()
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            win.draw(cairo.Context(surf))
            surf.write_to_png(shot)
            print("shot:", shot, w, "x", h)
            win.close()
            self.quit()
            return False
        GLib.timeout_add(int(os.environ.get("SEKAI_SHOT_DELAY", "2500")), grab)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-calc [--standard | --scientific]")
        return 0
    # X11(기본 화면 모드)에서도 창 클래스가 .desktop 의 StartupWMClass 와 같게
    GLib.set_prgname(APP_ID)
    return CalcApp().run([sys.argv[0]] + list(argv))
