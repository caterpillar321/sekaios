"""Nenerobo — 색 구성표 · 화면 CSS (GTK 없음).

"SekaiOS"(자동) 구성표는 셸 설정(~/.config/sekai/settings.json 의 appearance)의 다크·라이트 모드를 따른다 —
다크는 예전 kitty 설정(/etc/xdg/sekai/kitty/kitty.conf)과 같은 색이라 쓰던 사람이 그대로 알아본다.
나머지는 윈도우 터미널·다른 터미널에 흔한 구성표 (색 값은 각 구성표가 널리 알린 그대로).
"""
import re

from sekaishell import config, theme

DEFAULT_ACCENT = "#39c5bb"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# id → 구성표. "sel_bg" 가 None 이면 VTE 기본(글자·바탕 뒤집기)으로 고른 글을 보인다
SCHEMES = {
    "sekai-dark": {
        "name": "SekaiOS 다크", "fg": "#e4e4e8", "bg": "#1b1b1f", "cursor": "#39c5bb", "cursor_fg": "#1b1b1f",
        "sel_bg": "#39c5bb", "sel_fg": "#0f1f1e",
        "palette": ["#2a2a30", "#f26d78", "#7fd88f", "#f0c66e", "#6fb3f2", "#c29df2", "#39c5bb", "#c8c8d0",
                    "#5a5a64", "#ff8a94", "#9be8a8", "#f7d88c", "#8fc6ff", "#d6b8ff", "#5fd6ce", "#ffffff"],
    },
    "sekai-light": {
        "name": "SekaiOS 라이트", "fg": "#1f1f24", "bg": "#fbfbfc", "cursor": "#12807a", "cursor_fg": "#fbfbfc",
        "sel_bg": "#9fe3dd", "sel_fg": "#0f1f1e",
        "palette": ["#1f1f24", "#c8333f", "#2e8a3e", "#9a6400", "#1f6fc2", "#8a46c9", "#12807a", "#b8b8c0",
                    "#5a5a64", "#e0505c", "#3aa04c", "#b77c00", "#3a86de", "#a466e0", "#1aa198", "#e8e8ee"],
    },
    "campbell": {
        "name": "Campbell (윈도우 터미널 기본)", "fg": "#cccccc", "bg": "#0c0c0c", "cursor": "#ffffff",
        "cursor_fg": "#0c0c0c", "sel_bg": "#ffffff", "sel_fg": "#0c0c0c",
        "palette": ["#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#0037da", "#881798", "#3a96dd", "#cccccc",
                    "#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff", "#b4009e", "#61d6d6", "#f2f2f2"],
    },
    "one-half-dark": {
        "name": "One Half Dark", "fg": "#dcdfe4", "bg": "#282c34", "cursor": "#dcdfe4", "cursor_fg": "#282c34",
        "sel_bg": "#474e5d", "sel_fg": "#dcdfe4",
        "palette": ["#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#dcdfe4",
                    "#5a6374", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#dcdfe4"],
    },
    "one-half-light": {
        "name": "One Half Light", "fg": "#383a42", "bg": "#fafafa", "cursor": "#4f525d", "cursor_fg": "#fafafa",
        "sel_bg": "#bfceff", "sel_fg": "#383a42",
        "palette": ["#383a42", "#e45649", "#50a14f", "#c18301", "#0184bc", "#a626a4", "#0997b3", "#fafafa",
                    "#4f525d", "#df6c75", "#98c379", "#e4c07a", "#61afef", "#c577dd", "#56b5c1", "#ffffff"],
    },
    "solarized-dark": {
        "name": "Solarized Dark", "fg": "#839496", "bg": "#002b36", "cursor": "#93a1a1", "cursor_fg": "#002b36",
        "sel_bg": "#073642", "sel_fg": "#93a1a1",
        "palette": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
    "solarized-light": {
        "name": "Solarized Light", "fg": "#657b83", "bg": "#fdf6e3", "cursor": "#586e75", "cursor_fg": "#fdf6e3",
        "sel_bg": "#eee8d5", "sel_fg": "#586e75",
        "palette": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
    "tango-dark": {
        "name": "Tango Dark", "fg": "#d3d7cf", "bg": "#000000", "cursor": "#d3d7cf", "cursor_fg": "#000000",
        "sel_bg": None, "sel_fg": None,
        "palette": ["#000000", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                    "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"],
    },
}
AUTO = "auto"                       # SekaiOS 모드를 따른다 (sekai-dark · sekai-light)


def appearance():
    """셸 설정의 모드·강조색·제목줄 — {"mode", "accent", "bg", "surface", "fg", "titlebar_bg", "titlebar_height"}"""
    a = config.settings("appearance")
    a = a if isinstance(a, dict) else {}
    mode = theme.mode_of(a)
    out = {"mode": mode, "accent": DEFAULT_ACCENT}
    out.update(theme.PALETTES[mode])
    for k in ("accent", "bg", "surface", "fg", "titlebar_bg"):
        v = a.get(k)
        if isinstance(v, str) and _HEX.match(v):
            out[k] = v
    try:
        out["titlebar_height"] = max(28, min(56, int(a.get("titlebar_height", 34))))
    except (TypeError, ValueError):
        out["titlebar_height"] = 34
    return out


def scheme_id(sid, ap):
    """"auto"·모르는 이름 → 실제 구성표 id"""
    if sid in SCHEMES:
        return sid
    return "sekai-dark" if ap["mode"] == "dark" else "sekai-light"


def scheme(sid, ap):
    return SCHEMES[scheme_id(sid, ap)]


def _rgba(hex_, alpha):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def css(ap):
    """창 전체의 CSS — 제목줄 안의 탭 (고른 탭은 그 탭 구성표의 바탕색으로 이어져 보인다), 검색 막대, 설정 창"""
    fg, tb = ap["fg"], ap["titlebar_bg"]
    out = [f"""
.nr-titlebar {{ background: {tb}; min-height: {ap["titlebar_height"] + 6}px; }}
.nr-title {{ font-weight: 600; margin-left: 14px; }}
.nr-winbtn {{ min-width: 46px; padding: 0; border-radius: 0; background: transparent; border: none; box-shadow: none; }}
.nr-winbtn:hover {{ background: {_rgba(fg, 0.10)}; }}
.nr-winbtn.nr-x:hover {{ background: #c42b1c; }}
.nr-tabs {{ padding-left: 6px; }}
.nr-tab {{ margin: 6px 0 0 2px; padding: 0 4px 0 12px; border-radius: 8px 8px 0 0; color: {_rgba(fg, 0.72)};
           min-height: 34px; }}
.nr-tab:hover {{ background: {_rgba(fg, 0.07)}; }}
.nr-tab.active {{ color: {fg}; }}
.nr-tab.active .nr-mark {{ background: {ap["accent"]}; }}
.nr-tab.admin .nr-mark {{ background: #e5484d; }}
.nr-mark {{ min-width: 3px; min-height: 14px; border-radius: 2px; margin-right: 8px; background: transparent; }}
.nr-tab button.nr-close {{ min-width: 22px; min-height: 22px; padding: 0; margin-left: 4px; border-radius: 6px; }}
.nr-tab button.nr-close image {{ -gtk-icon-size: 12px; }}
.nr-titlebar button.nr-flat {{ margin-top: 6px; min-width: 30px; min-height: 30px; padding: 0; border-radius: 8px; }}
vte-terminal {{ padding: 6px 12px; }}
popover.menu modelbutton accelerator {{ margin-left: 28px; }}
.nr-search {{ background: {ap["surface"]}; color: {fg}; border-radius: 10px; padding: 6px; margin: 10px 18px;
              border: 1px solid {_rgba(fg, 0.12)}; box-shadow: 0 4px 14px rgba(0,0,0,0.35); }}
.nr-search entry {{ min-width: 220px; }}
.nr-settings {{ background: {ap["bg"]}; }}
.nr-side {{ background: {tb}; padding: 8px; }}
.nr-side row {{ border-radius: 8px; padding: 9px 12px; margin: 1px 0; }}
.nr-side row:selected {{ background: {_rgba(ap["accent"], 0.18)}; color: {fg}; }}
.nr-page {{ padding: 22px 28px; }}
.nr-h1 {{ font-size: 15pt; font-weight: 700; margin-bottom: 14px; }}
.nr-card {{ background: {ap["surface"]}; border-radius: 10px; border: 1px solid {_rgba(fg, 0.08)}; }}
.nr-card > row, .nr-row {{ padding: 12px 16px; }}
.nr-card > row:not(:last-child) {{ border-bottom: 1px solid {_rgba(fg, 0.07)}; }}
.nr-sub {{ color: {_rgba(fg, 0.62)}; font-size: 9pt; }}
.nr-err {{ color: #e5484d; font-weight: 600; }}
.nr-sec {{ font-weight: 600; margin: 18px 0 8px 2px; }}
.nr-swatch {{ min-width: 18px; min-height: 18px; border-radius: 4px; }}
.nr-preview {{ border-radius: 8px; padding: 10px 14px; font-family: monospace; }}
"""]
    for sid, sc in SCHEMES.items():
        out.append(f".nr-tab.active.sc-{sid} {{ background: {sc['bg']}; color: {sc['fg']}; }}\n"
                   f".nr-body.sc-{sid} {{ background: {sc['bg']}; }}\n")
    return "".join(out)
