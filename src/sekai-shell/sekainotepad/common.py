"""메모장 — 여러 모듈이 함께 쓰는 것 (모양·상태 파일·아이콘·글꼴·묻는 창·시각 글).

색은 컴퓨터 관리·작업 관리자와 같다 — settings.css 앞에 강조색·모드를 붙이고 이 앱의 모양(NOTEPAD_CSS)을 더한다.
"""
import json
import os
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import dbg  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402,F401

APP_ID = "org.sekaios.Notepad"
APP_NAME = "메모장"
APP_ICONS = ["accessories-text-editor", "text-editor", "text-x-generic"]
UNTITLED = "제목 없음"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
STATE = os.path.expanduser("~/.local/state/sekai/notepad.json")
FALLBACK_MONO = "JetBrains Mono 10"      # SekaiOS 기본 고정폭 글꼴 (gsettings 가 없을 때)
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 10, 500, 10
BIG_FILE = 20 * 1024 * 1024
RECENT_MAX = 10

# 모드마다 다른 면 색 — 윈도우 11 메모장처럼 탭 줄은 제목줄 쪽으로 어둡게(라이트는 회색),
#   고른 탭·메뉴 줄·상태 표시줄은 한 면, 글 쓰는 곳은 그보다 조금 더 밝게(라이트는 흰색)
_MODE_COLORS = {
    "dark": """
@define-color np_strip mix(@bg, @surface, 0.45);
@define-color np_bar   mix(@surface, @fg, 0.06);
@define-color np_text  mix(@surface, @fg, 0.035);
""",
    "light": """
@define-color np_strip @bg;
@define-color np_bar   mix(@surface, #ffffff, 0.55);
@define-color np_text  mix(@surface, #ffffff, 0.9);
""",
}

NOTEPAD_CSS = """
.np-window { background: @np_text; color: @fg; }

/* ── 탭 줄 ── */
.np-strip { background: @np_strip; padding: 6px 6px 0 6px; }
notebook.np-tabs, notebook.np-tabs > header, notebook.np-tabs > header > tabs,
notebook.np-tabs > stack {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
}
notebook.np-tabs > header { padding: 0; }
notebook.np-tabs > stack { min-height: 0; }
notebook.np-tabs > header > tabs > tab {
    background: transparent;
    border: none;
    border-radius: 8px 8px 0 0;
    box-shadow: none;
    outline: none;
    padding: 7px 6px 7px 14px;
    margin: 0 1px;
    min-height: 0;
    min-width: 0;
}
notebook.np-tabs > header > tabs > tab:hover { background: alpha(@fg, 0.06); }
notebook.np-tabs > header > tabs > tab:checked { background: @np_bar; }
notebook.np-tabs > header > tabs > tab label { color: @text2; font-size: 13px; }
notebook.np-tabs > header > tabs > tab:hover label,
notebook.np-tabs > header > tabs > tab:checked label { color: @fg; }
notebook.np-tabs > header > arrow { color: @text2; min-width: 22px; min-height: 22px; }
notebook.np-tabs > header > arrow:hover { background: alpha(@fg, 0.08); border-radius: 6px; }
button.np-tabclose {
    padding: 0;
    min-width: 22px;
    min-height: 22px;
    border: none;
    border-radius: 4px;
    background: transparent;
    box-shadow: none;
}
button.np-tabclose:hover { background: alpha(@fg, 0.10); }
button.np-tabclose image { color: @text2; }
label.np-dot { font-size: 9px; color: @fg; }
/* 저장하지 않은 탭: 닫기 단추 자리에 ● — 탭에 마우스를 올리면 다시 × (윈도우 11 메모장처럼) */
.np-tab .np-dot { opacity: 0; }
.np-tab.modified .np-dot { opacity: 1; }
.np-tab.modified .np-x { opacity: 0; }
tab:hover .np-tab.modified .np-dot { opacity: 0; }
tab:hover .np-tab.modified .np-x { opacity: 1; }
button.np-newtab {
    padding: 4px;
    margin: 2px 0 4px 4px;
    border: none;
    border-radius: 6px;
    background: transparent;
    box-shadow: none;
}
button.np-newtab:hover { background: alpha(@fg, 0.08); }
button.np-newtab image { color: @fg; }

/* ── 메뉴 줄 ── */
.np-menurow { background: @np_bar; padding: 2px 6px; border-bottom: 1px solid @line; }
menubar.np-menubar { background: transparent; border: none; box-shadow: none; padding: 0; }
menubar.np-menubar > menuitem {
    padding: 5px 12px;
    margin: 2px 1px;
    border-radius: 6px;
    color: @fg;
    box-shadow: none;
}
menubar.np-menubar > menuitem:hover { background: @hover; box-shadow: none; }
menubar.np-menubar > menuitem label { color: @fg; font-size: 13px; }
menu { padding: 4px; border-radius: 8px; }
menu menuitem { padding: 6px 12px; border-radius: 4px; min-width: 190px; }
menu menuitem label { font-size: 13px; }
menu menuitem accelerator { color: @text3; font-size: 12px; }
menu menuitem:disabled label { color: @text3; }
menu separator { background: @line; margin: 4px 0; min-height: 1px; }
button.np-flat {
    background: transparent;
    border: none;
    border-radius: 6px;
    box-shadow: none;
    padding: 5px 8px;
}
button.np-flat:hover { background: @hover; }
button.np-flat:checked { background: alpha(@accent, 0.22); }
button.np-flat:disabled image { color: @text3; }

/* ── 글 쓰는 곳 ── */
textview.np-text, textview.np-text text { background-color: @np_text; color: @fg; caret-color: @fg; }
textview.np-text text selection,
textview.np-text text selection:focus { background-color: alpha(@accent, 0.38); color: @fg; }
.np-body scrollbar { background: transparent; }

/* ── 찾기·바꾸기 ── */
.np-find {
    background: @np_bar;
    border-bottom: 1px solid @line;
    padding: 6px 10px;
}
.np-find entry { min-width: 260px; padding: 4px 8px; }
.np-find entry.np-nomatch { border-bottom-color: mix(#e0453a, @fg, 0.2); }
.np-find button.np-text-btn { padding: 4px 12px; font-size: 13px; }
label.np-count { color: @text2; font-size: 12px; min-width: 64px; }
.np-find checkbutton label { font-size: 13px; }

/* ── 알림 막대 (다른 프로그램이 파일을 바꿨을 때) ── */
.np-notice {
    background: alpha(@accent, 0.12);
    border-bottom: 1px solid alpha(@accent, 0.35);
    padding: 6px 12px;
}
.np-notice label { color: @fg; font-size: 13px; }
.np-notice button { padding: 3px 12px; font-size: 13px; }

/* ── 상태 표시줄 ── */
.np-status { background: @np_bar; border-top: 1px solid @line; padding: 0 8px; min-height: 26px; }
.np-status label { color: @text2; font-size: 12px; padding: 4px 10px; }
.np-status separator { background: @line; min-width: 1px; margin: 6px 0; }

label.np-error { color: mix(#e0453a, @fg, 0.25); font-size: 13px; }   /* 다크·라이트 모두 읽히게 */

/* ── 설정 페이지 ── */
.np-settings { background: @np_text; }
label.np-preview { padding: 10px 12px; border-radius: 6px; background: @np_bar; color: @fg; }
"""


def load_css(a):
    """settings.css + 강조색·모드 + 이 앱의 모양 → 화면 전체에 (앱의 모든 창)"""
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + _MODE_COLORS.get(a["mode"], _MODE_COLORS["dark"])
                             + NOTEPAD_CSS).encode())
    except GLib.Error as e:
        print("[sekai-notepad] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return None
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


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
        dbg("메모장 상태 저장 실패", e)


def icon(names, size=16):
    """후보 중 테마에 있는 첫 아이콘 (기호 아이콘 — 없으면 빈 그림 대신 Adwaita 의 것)"""
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if n and th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.BUTTON)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name(names[-1] if isinstance(names, list) else names, Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def icon_button(names, tooltip, cb, size=16, css="np-flat"):
    b = Gtk.Button()
    b.add(icon(names, size))
    b.set_tooltip_text(tooltip)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.set_can_focus(False)
    b.get_style_context().add_class(css)
    if cb:
        b.connect("clicked", lambda *_: cb())
    return b


# ── 글꼴 ─────────────────────────────────────────────────────
_iface = None


def interface_settings():
    """org.gnome.desktop.interface (없는 시스템이면 None) — 고정폭 글꼴을 여기서 읽고 바뀌면 따라간다"""
    global _iface
    if _iface is None:
        src = Gio.SettingsSchemaSource.get_default()
        if src is not None and src.lookup("org.gnome.desktop.interface", True) is not None:
            _iface = Gio.Settings.new("org.gnome.desktop.interface")
        else:
            _iface = False
    return _iface or None


def system_mono_font():
    s = interface_settings()
    name = s.get_string("monospace-font-name") if s is not None else ""
    return name or FALLBACK_MONO


def font_css(font, zoom):
    """글꼴 이름("JetBrains Mono 10") + 확대 비율 → 글 칸의 CSS. settings.css 의 `* { font-family }` 보다
    높은 우선순위로 그 칸에만 붙인다"""
    desc = Pango.FontDescription.from_string(font or FALLBACK_MONO)
    fam = (desc.get_family() or "Monospace").replace("\\", "\\\\").replace('"', '\\"')
    size = desc.get_size() / Pango.SCALE if desc.get_size() > 0 else 10
    unit = "px" if desc.get_size_is_absolute() else "pt"
    size = max(1.0, size * zoom / 100.0)
    style = {Pango.Style.ITALIC: "italic", Pango.Style.OBLIQUE: "oblique"}.get(desc.get_style(), "normal")
    weight = int(desc.get_weight()) if desc.get_set_fields() & Pango.FontMask.WEIGHT else 400
    rule = (f'font-family: "{fam}", monospace; font-size: {size:.2f}{unit}; '
            f"font-style: {style}; font-weight: {weight};")
    return f"textview.np-text, textview.np-text text {{ {rule} }}"


def font_label(font):
    """설정에 보일 글꼴 이름 — "JetBrains Mono 10" → "JetBrains Mono, 10pt\""""
    desc = Pango.FontDescription.from_string(font or FALLBACK_MONO)
    size = desc.get_size() / Pango.SCALE if desc.get_size() > 0 else 10
    extra = []
    if desc.get_set_fields() & Pango.FontMask.WEIGHT and desc.get_weight() >= Pango.Weight.SEMIBOLD:
        extra.append("굵게")
    if desc.get_style() != Pango.Style.NORMAL:
        extra.append("기울임꼴")
    s = f"{desc.get_family() or 'Monospace'}, {size:g}pt"
    return s + (" · " + " ".join(extra) if extra else "")


# ── 글 ───────────────────────────────────────────────────────
def time_date_text(t=None):
    """F5 로 넣는 시각 — 윈도우 한국어 메모장과 같은 "오후 11:07 2026-09-25\""""
    t = time.localtime(t)
    h = t.tm_hour % 12 or 12
    return f"{'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d} {t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}"


def display_name(path):
    return os.path.basename(path) if path else UNTITLED


def menu_label(text):
    """Gio 메뉴 글은 '_' 를 단축 글자로 읽는다 — 파일 이름의 밑줄이 사라지지 않게"""
    return text.replace("_", "__")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def io_error_text(err):
    """저장·열기 실패 → 사람이 읽을 이유"""
    if isinstance(err, GLib.Error):
        codes = {
            Gio.IOErrorEnum.PERMISSION_DENIED: "이 위치에 쓸 권한이 없습니다.",
            Gio.IOErrorEnum.READ_ONLY: "읽기 전용 위치입니다.",
            Gio.IOErrorEnum.NOT_FOUND: "폴더를 찾을 수 없습니다.",
            Gio.IOErrorEnum.NO_SPACE: "디스크 공간이 부족합니다.",
            Gio.IOErrorEnum.IS_DIRECTORY: "같은 이름의 폴더가 있습니다.",
            Gio.IOErrorEnum.FILENAME_TOO_LONG: "파일 이름이 너무 깁니다.",
            Gio.IOErrorEnum.INVALID_FILENAME: "파일 이름에 쓸 수 없는 문자가 있습니다.",
        }
        for code, text in codes.items():
            if err.matches(Gio.io_error_quark(), code):
                return text
        return err.message
    if isinstance(err, PermissionError):
        return "파일을 읽을 권한이 없습니다."
    if isinstance(err, IsADirectoryError):
        return "폴더는 열 수 없습니다."
    if isinstance(err, OSError):
        return err.strerror or str(err)
    return str(err)


def is_permission_error(err):
    return isinstance(err, GLib.Error) and (err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED) or
                                            err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.READ_ONLY))


# ── 묻는 창 ──────────────────────────────────────────────────
def ask(parent, text, secondary, buttons, on_response, default=None, kind=Gtk.MessageType.QUESTION):
    """buttons = [(글, 응답 id, 모양)] — 모양: None · "suggested" · "destructive".
    창이 닫히면 on_response(응답 id). Esc·창 닫기는 마지막 단추(취소)로 본다."""
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind,
                          buttons=Gtk.ButtonsType.NONE, text=text)
    d.set_title(APP_NAME)
    if secondary:
        d.format_secondary_text(secondary)
    for label, rid, style in buttons:
        b = d.add_button(label, rid)
        if style == "suggested":
            b.get_style_context().add_class("accent-btn")
        elif style == "destructive":
            b.get_style_context().add_class("destructive-action")
    if default is not None:
        d.set_default_response(default)
    cancel = buttons[-1][1]

    def resp(dlg, r):
        dlg.destroy()
        if r in (Gtk.ResponseType.DELETE_EVENT, Gtk.ResponseType.NONE):
            r = cancel
        on_response(r)
    d.connect("response", resp)
    d.show_all()
    return d


def notice(parent, text, secondary=None):
    return ask(parent, text, secondary, [("확인", Gtk.ResponseType.OK, "suggested")], lambda _r: None,
               default=Gtk.ResponseType.OK, kind=Gtk.MessageType.WARNING)
