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
