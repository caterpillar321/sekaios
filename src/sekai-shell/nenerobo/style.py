"""Nenerobo — 색 구성표 · 글꼴 · 화면 CSS (GTK 없음).

색은 SekaiOS 셸 설정(~/.config/sekai/settings.json 의 appearance)의 다크·라이트 모드를 따른다.
다크 구성표는 예전 kitty 설정(/etc/xdg/sekai/kitty/kitty.conf)과 같은 색 — 쓰던 사람이 그대로 알아보게.
"""
import re

from sekaishell import config, theme

# 한글은 고정폭 칸에 맞는 나눔고딕코딩으로 — 목록 앞에서부터 글자가 있는 글꼴을 쓴다
FONT = "JetBrains Mono, NanumGothicCoding 11"
SCROLLBACK = 10000
WORD_CHARS = "-,./?%&#:_=+@~"          # 두 번 눌러 고를 때 단어에 넣는 기호 (경로·주소가 한 번에)
DEFAULT_ACCENT = "#39c5bb"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

SCHEMES = {
    "dark": {
        "fg": "#e4e4e8", "bg": "#1b1b1f", "cursor": "#39c5bb", "cursor_fg": "#1b1b1f",
        "sel_bg": "#39c5bb", "sel_fg": "#0f1f1e",
        "palette": ["#2a2a30", "#f26d78", "#7fd88f", "#f0c66e", "#6fb3f2", "#c29df2", "#39c5bb", "#c8c8d0",
                    "#5a5a64", "#ff8a94", "#9be8a8", "#f7d88c", "#8fc6ff", "#d6b8ff", "#5fd6ce", "#ffffff"],
    },
    "light": {
        "fg": "#1f1f24", "bg": "#fbfbfc", "cursor": "#12807a", "cursor_fg": "#fbfbfc",
        "sel_bg": "#9fe3dd", "sel_fg": "#0f1f1e",
        "palette": ["#1f1f24", "#c8333f", "#2e8a3e", "#9a6400", "#1f6fc2", "#8a46c9", "#12807a", "#b8b8c0",
                    "#5a5a64", "#e0505c", "#3aa04c", "#b77c00", "#3a86de", "#a466e0", "#1aa198", "#e8e8ee"],
    },
}


def appearance():
    """셸 설정의 모드·강조색 — {"mode", "accent", "bg", "surface", "fg", "titlebar_bg"}"""
    a = config.settings("appearance")
    a = a if isinstance(a, dict) else {}
    mode = theme.mode_of(a)
    out = {"mode": mode, "accent": DEFAULT_ACCENT}
    out.update(theme.PALETTES[mode])
    acc = a.get("accent")
    if isinstance(acc, str) and _HEX.match(acc):
        out["accent"] = acc
    for k in ("bg", "surface", "fg", "titlebar_bg"):
        v = a.get(k)
        if isinstance(v, str) and _HEX.match(v):
            out[k] = v
    try:
        out["titlebar_height"] = max(28, min(56, int(a.get("titlebar_height", 34))))
    except (TypeError, ValueError):
        out["titlebar_height"] = 34
    return out


def _rgba(hex_, alpha):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def css(ap, sc):
    """창 전체의 CSS — 제목줄 안의 탭(윈도우 터미널처럼 고른 탭은 터미널 바탕색), 검색 막대"""
    fg, tb = ap["fg"], ap["titlebar_bg"]
    return f"""
.nr-titlebar {{ background: {tb}; min-height: {ap["titlebar_height"] + 6}px; }}
.nr-winbtn {{ min-width: 46px; padding: 0; border-radius: 0; background: transparent; border: none; box-shadow: none; }}
.nr-winbtn:hover {{ background: {_rgba(fg, 0.10)}; }}
.nr-winbtn.nr-x:hover {{ background: #c42b1c; }}
.nr-tabs {{ padding-left: 6px; }}
.nr-tab {{ margin: 6px 0 0 2px; padding: 0 4px 0 12px; border-radius: 8px 8px 0 0; color: {_rgba(fg, 0.72)};
           min-height: 34px; }}
.nr-tab:hover {{ background: {_rgba(fg, 0.07)}; }}
.nr-tab.active {{ background: {sc["bg"]}; color: {sc["fg"]}; }}
.nr-tab.active .nr-mark {{ background: {ap["accent"]}; }}
.nr-mark {{ min-width: 3px; min-height: 14px; border-radius: 2px; margin-right: 8px; background: transparent; }}
.nr-tab button.nr-close {{ min-width: 22px; min-height: 22px; padding: 0; margin-left: 4px; border-radius: 6px; }}
.nr-tab button.nr-close image {{ -gtk-icon-size: 12px; }}
.nr-titlebar button.nr-flat {{ margin-top: 6px; min-width: 30px; min-height: 30px; padding: 0; border-radius: 8px; }}
.nr-body {{ background: {sc["bg"]}; }}
vte-terminal {{ padding: 6px 12px; }}
.nr-search {{ background: {ap["surface"]}; color: {fg}; border-radius: 10px; padding: 6px; margin: 10px 18px;
              border: 1px solid {_rgba(fg, 0.12)}; box-shadow: 0 4px 14px rgba(0,0,0,0.35); }}
.nr-search entry {{ min-width: 220px; }}
.nr-search .nr-count {{ margin: 0 6px; color: {_rgba(fg, 0.6)}; }}
"""
