"""다크 / 라이트 모드 — 색 묶음과, 일반 앱(GTK·Chromium 등)이 따라오게 하는 설정.

셸의 모든 색은 네 기본색(bg surface fg titlebar_bg)과 강조색에서 파생된다
(style.css·settings.css 의 @define-color). 모드는 이 네 값을 한꺼번에 바꾼다.
강조색은 모드와 상관없이 사용자가 고른 그대로.
"""
import configparser
import os
import subprocess

PALETTES = {
    "dark": {
        "bg": "#151517",
        "surface": "#1e1e22",      # 작업 표시줄·메뉴·팝업의 면
        "fg": "#f1f1f3",
        "titlebar_bg": "#2c2c30",  # 창 제목줄 — GTK 다크 앱 본문(#2d2d2d)에 맞춤
    },
    "light": {
        "bg": "#eceef1",
        "surface": "#f6f6f8",
        "fg": "#1c1c21",
        "titlebar_bg": "#f3f3f5",  # 창 제목줄 — GTK 라이트 앱 본문(#f6f5f4)에 맞춤
    },
}

# 모드별 GTK 테마·아이콘 테마 — 작업 표시줄(패널) 아이콘이 Papirus·Papirus-Dark 는 흰색,
#   Papirus-Light 는 어두운 색이다. 밝은 작업 표시줄엔 Papirus-Light 를 써야 트레이 아이콘이 보인다
# Sekai-Light · Sekai-Dark 는 SekaiOS 테마 (/usr/share/themes — Fluent-gtk-theme 바탕, scripts/build-theme.sh).
#   예전에는 GTK 에 들어 있는 Adwaita(GNOME 디자인)를 썼다
GTK = {
    "dark":  {"gtk": "Sekai-Dark",   "icons": "Papirus-Dark", "scheme": "prefer-dark", "prefer_dark": 1},
    "light": {"gtk": "Sekai-Light",  "icons": "Papirus-Light", "scheme": "prefer-light", "prefer_dark": 0},
}


def mode_of(appearance):
    m = (appearance or {}).get("mode", "dark")
    return m if m in PALETTES else "dark"


def icon_theme(mode):
    """쓸 수 있는 아이콘 테마 (모드에 맞는 것 → 없으면 대체)"""
    for cand in (GTK[mode]["icons"], "Papirus", "Adwaita"):
        if os.path.isdir(f"/usr/share/icons/{cand}"):
            return cand
    return "Adwaita"


def apply_gtk_settings(gtk_settings, mode):
    """이 프로그램의 GtkSettings 에 모드를 적용 (셸 프로그램·설정 앱이 스스로 부른다)"""
    if gtk_settings is None:
        return
    gtk_settings.set_property("gtk-application-prefer-dark-theme", mode == "dark")
    gtk_settings.set_property("gtk-icon-theme-name", icon_theme(mode))
    # 대화상자 제목줄은 WorldLink 가 그린다 (제목 + 닫기). GTK 는 Wayland 에서 이 값을 늘 켜서 확인 창이 제목 띠를
    #   스스로 한 겹 더 그렸다 — 제목줄이 두 겹. 끄면 대화상자 단추도 아래쪽 줄에 놓인다 (윈도우처럼)
    gtk_settings.set_property("gtk-dialogs-use-header", False)


def _write_ini(path, values):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str                         # 키 대소문자 보존
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
    if not cp.has_section("Settings"):
        cp.add_section("Settings")
    for k, v in values.items():
        cp.set("Settings", k, str(v))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        cp.write(f)
    os.replace(tmp, path)


def apply_system(mode):
    """일반 앱이 따라오게: gsettings(GTK·포털 → Chromium·GTK4/libadwaita)와 GTK 설정 파일.
    열려 있는 GTK 앱은 gsettings 변경을 바로 받고, 나머지는 다음 실행부터."""
    g = GTK[mode]
    icons = icon_theme(mode)
    home = os.path.expanduser("~")
    try:
        for key, val in (("color-scheme", g["scheme"]), ("gtk-theme", g["gtk"]), ("icon-theme", icons)):
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", key, val],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass
    # 다크는 테마 이름(Sekai-Dark)으로만 — "다크 선호"(prefer-dark)는 앱이 뜰 때 한 번만 읽는다.
    #   그걸 1 로 적어 두면 열려 있던 GTK3·GTK4 앱은 라이트로 바꿔도 "Adwaita + 다크 선호" = 다크로 남았다
    #   (테마 이름은 설정 포털로 바로 바뀐다). libadwaita·Chromium 은 color-scheme 을 본다.
    vals = {"gtk-application-prefer-dark-theme": 0, "gtk-theme-name": g["gtk"], "gtk-icon-theme-name": icons}
    for d in ("gtk-3.0", "gtk-4.0"):
        try:
            _write_ini(os.path.join(home, ".config", d, "settings.ini"), vals)
        except OSError:
            pass
    try:
        write_window_controls(mode, home)
    except OSError:
        pass


# ── 앱이 스스로 그리는 제목줄(GTK4·libadwaita)의 창 단추 — 윈도우 11 식 ──
#   GTK4 앱은 제목줄을 스스로 그려 WorldLink 막대가 빠진다(SEKAI_GEOM_CSD). 그 단추는 앱의 테마(Adwaita 의 동그라미)라
#   다른 창과 달랐다 — libadwaita 는 시스템 GTK 테마를 무시하지만 사용자 CSS(~/.config/gtk-4.0/gtk.css)는 늘 맨 위에
#   얹는다. Flatpak 앱은 sekaios-base 가 이 폴더를 읽게 해 둔다(flatpak override xdg-config/gtk-4.0:ro).
#   앱의 아이콘은 숨기고 단추 바탕에 WorldLink 막대와 같은 10px 선 그림을 놓는다 (이름 붙은 아이콘은 CSS 로 못 바꾼다)
CONTROLS_CSS = "sekai-gtk4.css"
_CONTROLS_MARK = ".sekai-gtk4"                  # @import 를 한 번 넣었다 — 사용자가 지우면 다시 넣지 않는다
# libadwaita 의 이름 붙은 색 → Sekai 테마(Sekai-Dark·Light GTK 테마와 같은 값). libadwaita 는 시스템 테마는 무시하지만
#   이 색들은 바꿔도 된다고 정해 두었다 — 1.6 이상은 CSS 변수(--이름), 그 전은 @define-color(이름) 를 읽는다
_ADW = {
    "dark": {
        "window_bg_color": "#2C2C30", "window_fg_color": "#ffffff",
        "view_bg_color": "#26262A", "view_fg_color": "#ffffff",
        "headerbar_bg_color": "#2C2C30", "headerbar_fg_color": "#ffffff", "headerbar_backdrop_color": "#2C2C30",
        "headerbar_border_color": "#ffffff", "headerbar_shade_color": "rgba(0, 0, 0, 0.36)",
        "sidebar_bg_color": "#26262A", "sidebar_fg_color": "#ffffff", "sidebar_backdrop_color": "#26262A",
        "secondary_sidebar_bg_color": "#2C2C30", "secondary_sidebar_fg_color": "#ffffff",
        "secondary_sidebar_backdrop_color": "#2C2C30",
        "card_bg_color": "rgba(255, 255, 255, 0.06)", "card_fg_color": "#ffffff",
        "dialog_bg_color": "#37373C", "dialog_fg_color": "#ffffff",
        "popover_bg_color": "#37373C", "popover_fg_color": "#ffffff",
        "thumbnail_bg_color": "#37373C", "thumbnail_fg_color": "#ffffff",
        "accent_color": "ACCENT_TEXT", "accent_bg_color": "ACCENT", "accent_fg_color": "ACCENT_FG",
        "destructive_bg_color": "#E53935", "destructive_fg_color": "#ffffff", "destructive_color": "#F28B82",
        "success_bg_color": "#2E9E5B", "success_fg_color": "#ffffff", "success_color": "#81C995",
        "warning_bg_color": "#C79A00", "warning_fg_color": "rgba(0, 0, 0, 0.87)", "warning_color": "#FDD633",
        "error_bg_color": "#E53935", "error_fg_color": "#ffffff", "error_color": "#F28B82",
    },
    "light": {
        "window_bg_color": "#F3F3F5", "window_fg_color": "rgba(0, 0, 0, 0.87)",
        "view_bg_color": "#FFFFFF", "view_fg_color": "rgba(0, 0, 0, 0.87)",
        "headerbar_bg_color": "#F3F3F5", "headerbar_fg_color": "rgba(0, 0, 0, 0.87)", "headerbar_backdrop_color": "#F3F3F5",
        "headerbar_border_color": "rgba(0, 0, 0, 0.87)", "headerbar_shade_color": "rgba(0, 0, 0, 0.07)",
        "sidebar_bg_color": "#FAFAFB", "sidebar_fg_color": "rgba(0, 0, 0, 0.87)", "sidebar_backdrop_color": "#FAFAFB",
        "secondary_sidebar_bg_color": "#F3F3F5", "secondary_sidebar_fg_color": "rgba(0, 0, 0, 0.87)",
        "secondary_sidebar_backdrop_color": "#F3F3F5",
        "card_bg_color": "#FFFFFF", "card_fg_color": "rgba(0, 0, 0, 0.87)",
        "dialog_bg_color": "#FFFFFF", "dialog_fg_color": "rgba(0, 0, 0, 0.87)",
        "popover_bg_color": "#FFFFFF", "popover_fg_color": "rgba(0, 0, 0, 0.87)",
        "thumbnail_bg_color": "#FFFFFF", "thumbnail_fg_color": "rgba(0, 0, 0, 0.87)",
        "accent_color": "ACCENT_TEXT", "accent_bg_color": "ACCENT", "accent_fg_color": "ACCENT_FG",
        "destructive_bg_color": "#D93025", "destructive_fg_color": "#ffffff", "destructive_color": "#D93025",
        "success_bg_color": "#0F9D58", "success_fg_color": "#ffffff", "success_color": "#0F9D58",
        "warning_bg_color": "#F4B400", "warning_fg_color": "rgba(0, 0, 0, 0.87)", "warning_color": "#B06F00",
        "error_bg_color": "#D93025", "error_fg_color": "#ffffff", "error_color": "#D93025",
    },
}
_SHAPES = {
    "min": '<path d="M0 5.5H10"/>',
    "max": '<rect x="0.5" y="0.5" width="9" height="9"/>',
    "restore": '<path d="M2.5 2.5V0.5H9.5V7.5H7.5"/><rect x="0.5" y="2.5" width="7" height="7"/>',
    "close": '<path d="M0 0L10 10M10 0L0 10"/>',
}
_CONTROLS = """/* SekaiOS — 앱이 스스로 그리는 제목줄(GTK4·libadwaita)의 창 단추를 윈도우 11 식으로.
   SekaiOS 가 로그인 때마다 다시 쓴다 (다크·라이트). 쓰지 않으려면 gtk.css 의 @import 줄을 지우면 된다 */
windowcontrols { border-spacing: 0; }
windowcontrols > button {
  min-width: 46px; min-height: 32px; margin: 0; padding: 0;
  border-radius: 0; box-shadow: none; border: none;
  background-color: transparent; background-repeat: no-repeat; background-position: center; background-size: 10px 10px;
}
windowcontrols > button > image { opacity: 0; background: none; box-shadow: none; }
windowcontrols > button:hover { background-color: alpha(currentColor, 0.10); }
windowcontrols > button:active { background-color: alpha(currentColor, 0.18); }
windowcontrols > button.minimize { background-image: url("sekai-min-TONE.svg"); }
windowcontrols > button.maximize { background-image: url("sekai-max-TONE.svg"); }
window.maximized windowcontrols > button.maximize,
window.fullscreen windowcontrols > button.maximize { background-image: url("sekai-restore-TONE.svg"); }
windowcontrols > button.close { background-image: url("sekai-close-TONE.svg"); }
windowcontrols > button.close:hover, windowcontrols > button.close:active {
  background-color: #c42b1c; background-image: url("sekai-close-white.svg");
}
"""


def _write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _accent():
    """설정 › 개인 설정의 강조색 (없으면 SekaiOS 청록)"""
    try:
        from . import config
        a = (config.settings("appearance") or {}).get("accent")
    except Exception:
        a = None
    return a if isinstance(a, str) and len(a) == 7 and a.startswith("#") else "#3CC8BE"


def _mix(hex_, other, t):
    a = [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _adw_colors(mode):
    acc = _accent()
    r, g, b = (int(acc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    light_acc = 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5
    sub = {"ACCENT": acc, "ACCENT_FG": "rgba(0, 0, 0, 0.87)" if light_acc else "#ffffff",
           # 글자로 쓰는 강조색 — 다크 바탕엔 조금 밝게, 라이트 바탕엔 어둡게 (읽히게)
           "ACCENT_TEXT": _mix(acc, "#ffffff", 0.2) if mode == "dark" else _mix(acc, "#000000", 0.45)}
    return {k: sub.get(v, v) for k, v in _ADW["dark" if mode == "dark" else "light"].items()}


def _adw_css(mode):
    cols = _adw_colors(mode)
    old = "".join(f"@define-color {k} {v};\n" for k, v in cols.items())
    new = "".join(f"  --{k.replace('_', '-')}: {v};\n" for k, v in cols.items())
    return ("/* SekaiOS — libadwaita 앱의 색을 Sekai 테마로 (창·헤더바·사이드바·카드·강조색). 로그인 때마다 다시 쓴다 */\n"
            + old + ":root {\n" + new + "}\n\n")


def write_window_controls(mode, home=None):
    d = os.path.join(home or os.path.expanduser("~"), ".config", "gtk-4.0")
    os.makedirs(d, exist_ok=True)
    for tone, col in (("dark", PALETTES["dark"]["fg"]), ("light", PALETTES["light"]["fg"]), ("white", "#ffffff")):
        for k, body in _SHAPES.items():
            _write_if_changed(os.path.join(d, f"sekai-{k}-{tone}.svg"),
                              f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                              f'<g fill="none" stroke="{col}" stroke-width="1">{body}</g></svg>\n')
    _write_if_changed(os.path.join(d, CONTROLS_CSS),
                      _adw_css(mode) + _CONTROLS.replace("TONE", "dark" if mode == "dark" else "light"))
    for old in ("sekai-window-controls.css", ".sekai-window-controls"):     # 이 판을 만들며 잠깐 쓴 이름
        try:
            os.remove(os.path.join(d, old))
        except OSError:
            pass
    # 사용자 gtk.css 맨 위에 @import 한 줄 (CSS 규칙상 @import 는 맨 위) — 처음 한 번만
    css, mark = os.path.join(d, "gtk.css"), os.path.join(d, _CONTROLS_MARK)
    if os.path.exists(mark):
        return
    try:
        with open(css, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    old = old.replace('@import url("sekai-window-controls.css");\n', "")   # 이 판을 만들며 잠깐 쓴 이름
    if CONTROLS_CSS not in old:
        _write_if_changed(css, f'@import url("{CONTROLS_CSS}");\n' + old)
    open(mark, "w").close()


# ── 글꼴 다듬기 (윈도우의 ClearType) ──
#   GSettings org.gnome.desktop.interface 의 font-antialiasing·font-rgba-order — GTK 앱과 크로미움이 본다.
#   기본값(sekai-desktop 의 gschema override)은 RGB 서브픽셀. 모니터의 빨강·초록·파랑 점 배열이 거꾸로(BGR)거나
#   OLED 처럼 줄지어 있지 않으면 글자에 색 번짐이 보인다 — 그땐 BGR·회색조로
SMOOTHING = {"rgb": ("rgba", "rgb"), "bgr": ("rgba", "bgr"), "gray": ("grayscale", "rgb")}


def _gsettings_get(key):
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    return out.strip().strip("'")


def font_smoothing():
    """지금 글꼴 다듬기 — "rgb" · "bgr" · "gray" """
    if _gsettings_get("font-antialiasing") != "rgba":
        return "gray"
    return "bgr" if _gsettings_get("font-rgba-order") == "bgr" else "rgb"


def set_font_smoothing(v):
    aa, order = SMOOTHING.get(v, SMOOTHING["rgb"])
    for key, val in (("font-antialiasing", aa), ("font-rgba-order", order)):
        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", key, val],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
