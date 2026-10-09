"""SekaiOS 앱의 공통 뼈대 — 파일 탐색기·메모장·계산기·사진·작업 관리자·컴퓨터 관리·Windows 앱·스토어·앱 설치 관리자.

앱마다 똑같이 만들던 것 (카나데가 찾은 중복 — 8곳 넘게 거의 같은 코드였다):
  appearance()  설정 앱의 색·모드 (모드의 기본 묶음 위에, 올바른 #rrggbb 만)
  AppTheme      settings.css + 색 + 앱의 CSS 를 화면에 — 설정 앱에서 색·모드를 바꾸면 곧바로 따라간다
  JsonState     ~/.local/state/sekai/<앱>.json — 창 크기·정렬 같은 것 (프로세스마다 임시 파일 → 바꿔치기)
  ToastMixin    창 아래 잠깐 뜨는 한 줄 (self.toast_label · self._toast_src)
  shot_when_asked  개발용 SEKAI_SHOT=/경로.png — 창을 찍고 끝낸다
"""
import json
import os
import re
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config, dbg, theme  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_CSS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]
CFG_DIR = os.path.expanduser("~/.config/sekai")
DEFAULT_ACCENT = "#39c5bb"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def appearance():
    """settings.json 의 색 — 설정 앱 저장소와 같은 규칙(모드의 기본 묶음 위에, 올바른 #rrggbb 만)"""
    a = config.settings("appearance")
    a = a if isinstance(a, dict) else {}
    mode = theme.mode_of(a)
    out = {"mode": mode, "accent": DEFAULT_ACCENT}
    out.update(theme.PALETTES[mode])
    for k in ("accent", "bg", "surface", "fg"):
        v = a.get(k)
        if isinstance(v, str) and _HEX.match(v):
            out[k] = v
    return out


def _settings_css():
    for p in SETTINGS_CSS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    return ""


class AppTheme:
    """앱 하나의 모양.
    name     오류 줄 앞에 붙일 이름 (sekai-files)
    css      앱이 settings.css 위에 얹을 CSS — 글, 또는 a(appearance) 를 받아 글을 돌려주는 함수
    prelude  a 를 받아 앞에 둘 @define-color 를 더 돌려주는 함수 (모드마다 다른 바탕 등) — 없어도 된다"""

    def __init__(self, name, css="", prelude=None):
        self.name, self.css, self.prelude = name, css, prelude
        self.prov = None
        self.extra = ""                     # 페이지가 나중에 더하는 모양 (컴퓨터 관리)
        self._mon = None
        self._src = 0

    def _text(self, a, extra):
        pre = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
        if self.prelude:
            pre += self.prelude(a)
        css = self.css(a) if callable(self.css) else self.css
        return pre + _settings_css() + css + extra

    def load(self, a=None):
        """화면에 얹는다 (전에 얹은 것은 뗀다) → 쓴 appearance"""
        a = a or appearance()
        screen = Gdk.Screen.get_default()
        if self.prov is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, self.prov)
            self.prov = None
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(self._text(a, self.extra).encode())
        except GLib.Error as e:
            print(f"[{self.name}] CSS 오류:", e.message, file=sys.stderr, flush=True)
            if not self.extra:
                return a
            try:                            # 페이지가 더한 모양이 틀렸다 — 그것만 빼고
                prov.load_from_data(self._text(a, "").encode())
            except GLib.Error:
                return a
        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.prov = prov
        theme.apply_contrast_css()          # 대비 테마면 테두리·초점을 앱 CSS 위에
        return a

    def reload(self):
        a = self.load()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        return a

    def follow(self, on_change=None):
        """설정 앱에서 색·모드를 바꾸면 다시 얹고 on_change(a) — 설정 앱이 settings.json 을 바꿔치기(원자적 저장)하므로
        파일이 아니라 폴더를 본다"""
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            self._mon = Gio.File.new_for_path(CFG_DIR).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except (GLib.Error, OSError) as e:
            dbg("설정 폴더를 볼 수 없습니다:", e)
            return

        def fire():
            self._src = 0
            a = self.reload()
            if on_change:
                on_change(a)
            return False

        def changed(_m, f, other, _ev):
            if "settings.json" not in {x.get_basename() for x in (f, other) if x is not None}:
                return
            if self._src:
                GLib.source_remove(self._src)
            self._src = GLib.timeout_add(200, fire)
        self._mon.connect("changed", changed)


class JsonState:
    """작은 JSON 상태 파일 — 읽기 실패는 빈 사전, 쓰기는 프로세스마다 임시 파일 → 바꿔치기"""

    def __init__(self, path, what="상태"):
        self.path, self.what = path, what

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, d):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except (OSError, TypeError, ValueError) as e:
            dbg(f"{self.what} 저장 실패", e)


class ToastMixin:
    """창 아래 잠깐 뜨는 한 줄 — self.toast_label(Label) 과 self._toast_src = 0 을 만들어 둔 창에 섞는다"""
    TOAST_SECS = 5

    def toast(self, text, secs=None):
        self.toast_label.set_text(text)
        self.toast_label.show()
        if self._toast_src:
            GLib.source_remove(self._toast_src)

        def hide():
            self._toast_src = 0
            self.toast_label.hide()
            return False
        self._toast_src = GLib.timeout_add_seconds(secs or self.TOAST_SECS, hide)


def shot_when_asked(win, done, delay_ms=2500):
    """개발용: SEKAI_SHOT=/경로.png 면 SEKAI_SHOT_DELAY(기본 delay_ms) 뒤 창을 찍고 done() — 문서·기준 그림용"""
    shot = os.environ.get("SEKAI_SHOT")
    if not shot:
        return False

    def grab():
        gw = win.get_window()
        if gw is not None:
            pb = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
            if pb:
                pb.savev(shot, "png", [], [])
                print("shot:", shot, gw.get_width(), "x", gw.get_height())
        done()
        return False
    GLib.timeout_add(int(os.environ.get("SEKAI_SHOT_DELAY", str(delay_ms))), grab)
    return True
