"""접근성 — 작업 표시줄(sekai-panel)이 맡는 몫 (설정은 설정 › 접근성, 저장은 sekaisettings.store 의 a11y 섹션).

  돋보기        Win + = / Win + - / Win + Esc — 합성기의 커서 주변 확대(cursor:zoom_factor), OSD 로 배율
  색 필터       Win + Ctrl + C — 켜고 끈다 (화면 셰이더, 종류는 설정에서)
  화상 키보드   Win + Ctrl + O — wvkbd (wlroots 계열 합성기용 레이어 셸 키보드). 키는 가상 키보드로 들어가
                 ibus-hangul 을 거치므로 한글 입력기가 켜져 있으면 한글로 써진다
  고정 키       걸린 수식 키를 화면 오른쪽 아래에 보인다 (IPC sekaisticky). Shift 다섯 번 → 켤지 묻기
  필터 키       오른쪽 Shift 8초 → 켤지 묻기 (IPC sekaia11y)
"""
import os
import shutil
import signal
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config, dbg
from .layer import GtkLayerShell
from .popup import make_translucent

E = GtkLayerShell.Edge
OSK_BIN = "wvkbd-mobintl"
MAX_ZOOM = 10.0
FILTER_NAMES = {"grayscale": "회색조", "inverted": "반전", "grayscale-inverted": "회색조 반전",
                "deuteranopia": "적록 (녹색약)", "protanopia": "적록 (적색약)", "tritanopia": "청황 (청색약)"}
# HL_MODIFIER 비트 → 이름 (윈도우의 고정 키 표시처럼)
MODS = ((1 << 0, "Shift"), (1 << 2, "Ctrl"), (1 << 3, "Alt"), (1 << 6, "Win"))


def _store():
    from sekaisettings.store import Store         # 설정 앱과 같은 저장 — 조각 파일·즉시 반영까지
    return Store()


class StickyBadge(Gtk.Window):
    """고정 키로 걸린 수식 키 — 오른쪽 아래, 누르기를 막지 않는다. 걸림은 테두리, 잠금은 채운 칸"""

    def __init__(self, bottom):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("sticky-badge-win")
        make_translucent(self)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, E.BOTTOM, True)
        GtkLayerShell.set_anchor(self, E.RIGHT, True)
        GtkLayerShell.set_margin(self, E.BOTTOM, bottom)
        GtkLayerShell.set_margin(self, E.RIGHT, 16)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.set_accept_focus(False)
        self.box = Gtk.Box(spacing=6)
        self.box.get_style_context().add_class("sticky-badge")
        self.add(self.box)
        self.connect("realize", self._no_input)

    @staticmethod
    def _no_input(w):
        gw = w.get_window()
        if gw is not None:
            try:
                import cairo
                gw.input_shape_combine_region(cairo.Region(), 0, 0)
            except Exception:
                pass

    def show_mods(self, latched, locked):
        for c in self.box.get_children():
            self.box.remove(c)
            c.destroy()
        shown = False
        for bit, name in MODS:
            if (latched | locked) & bit:
                lbl = Gtk.Label(label=name)
                lbl.get_style_context().add_class("sticky-key")
                lbl.get_style_context().add_class("locked" if locked & bit else "latched")
                self.box.pack_start(lbl, False, False, 0)
                shown = True
        if shown:
            self.show_all()
        else:
            self.hide()


class A11y:
    def __init__(self, hypr, osd, bottom):
        self.hypr = hypr
        self.osd = osd
        self.osk = None
        self.badge = StickyBadge(bottom)
        self._asking = False
        # 로그인할 때 화상 키보드 (설정 › 접근성의 "로그인할 때 화상 키보드 띄우기")
        if (config.settings("a11y") or {}).get("osk"):
            GLib.timeout_add_seconds(3, lambda: self.osk_ctl("on") and False)

    # ── 돋보기 ──
    def _zoom(self):
        try:
            v = (self.hypr.query("getoption cursor:zoom_factor") or {}).get("float")
            return float(v) if v else 1.0
        except Exception:
            return 1.0

    def magnify(self, action="in"):
        step = float((config.settings("a11y") or {}).get("magnifier_step", 1.0) or 1.0)
        z = self._zoom()
        if action == "in":
            z = min(MAX_ZOOM, z + step)
        elif action == "out":
            z = max(1.0, z - step)
        elif action == "toggle":
            z = 1.0 if z > 1.0 else 1.0 + step
        else:
            z = 1.0
        subprocess.run(["hyprctl", "keyword", "cursor:zoom_factor", f"{z:.2f}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.osd.show_text("zoom-in-symbolic" if z > 1.0 else "zoom-original-symbolic",
                           f"돋보기 {round(z * 100)}%" if z > 1.0 else "돋보기 끔")

    # ── 색 필터 ──
    def color_filter(self, _arg=""):
        def work():
            st = _store()
            on = not st.get("a11y", "color_filter")
            st.set("a11y", "color_filter", on)
            kind = st.get("a11y", "color_filter_kind")
            GLib.idle_add(self.osd.show_text, "color-select-symbolic",
                          f"색 필터 켬 — {FILTER_NAMES.get(kind, kind)}" if on else "색 필터 끔")
        threading.Thread(target=work, daemon=True).start()

    # ── 화상 키보드 ──
    def _osk_running(self):
        return self.osk is not None and self.osk.poll() is None

    def osk_ctl(self, action="toggle"):
        on = {"on": True, "off": False}.get(action, not self._osk_running())
        if on and not self._osk_running():
            if not shutil.which(OSK_BIN):
                self.osd.show_text("input-keyboard-symbolic", "화상 키보드가 설치되지 않았습니다 (wvkbd)")
                return
            a = config.settings("appearance") or {}
            bg = (a.get("surface") or "#1e1e22").lstrip("#")
            fg = (a.get("bg") or "#151517").lstrip("#")
            text = (a.get("fg") or "#f1f1f3").lstrip("#")
            acc = (a.get("accent") or "#39c5bb").lstrip("#")
            args = [OSK_BIN, "--bg", bg + "f0", "--fg", fg, "--fg-sp", fg, "--press", acc, "--press-sp", acc,
                    "--text", text, "--text-sp", text, "--fn", "Pretendard 16", "-L", "300", "-H", "320", "-R", "6",
                    # 기본은 키릴·아랍·그리스… 자판을 다 돈다 — 영문·기호·이모지만 (한글은 입력기가 바꾼다)
                    "-l", "full,special,emoji", "--landscape-layers", "landscape,landscapespecial,emoji"]
            try:
                self.osk = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            start_new_session=True)
            except OSError as e:
                dbg("화상 키보드 실행 실패", e)
                self.osk = None
        elif not on and self._osk_running():
            p, self.osk = self.osk, None
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
            threading.Thread(target=p.wait, daemon=True).start()     # 거둬 간다 (안 하면 좀비로 남는다)

    # ── 합성기 이벤트 ──
    def on_event(self, name, arg):
        if name == "sekaisticky":
            try:
                latched, locked = (int(x) for x in arg.strip().split(",")[:2])
            except ValueError:
                return
            GLib.idle_add(self.badge.show_mods, latched, locked)
        elif name == "sekaia11y":
            GLib.idle_add(self._ask, arg.strip())

    def _ask(self, what):
        """Shift 다섯 번(고정 키) · 오른쪽 Shift 8초(필터 키) — 꺼져 있으면 켤지 묻고, 켜져 있으면 끈다 (윈도우처럼)"""
        if self._asking:
            return False
        key = {"sticky": "sticky_keys", "filter": "filter_keys"}.get(what)
        if not key:
            return False
        name = "고정 키" if what == "sticky" else "필터 키"
        if (config.settings("a11y") or {}).get(key):
            threading.Thread(target=lambda: _store().set("a11y", key, False), daemon=True).start()
            self.osd.show_text("preferences-desktop-accessibility-symbolic", f"{name} 끔")
            return False
        body = ("Shift 키를 다섯 번 눌렀습니다. 고정 키를 켜면 Shift·Ctrl·Alt·Windows 키를 누른 채로 있지 않고 "
                "하나씩 차례로 눌러 단축키를 쓸 수 있습니다." if what == "sticky" else
                "오른쪽 Shift 키를 8초 동안 눌렀습니다. 필터 키를 켜면 짧게 여러 번 눌린 키나 실수로 스친 키를 무시합니다.")
        self._asking = True

        def work():
            try:
                act = subprocess.run(["notify-send", "-a", "접근성", "-i", "preferences-desktop-accessibility",
                                      "-u", "critical", "--action=yes=켜기", "--action=no=아니요",
                                      "--action=settings=접근성 설정", f"{name}를 켤까요?", body],
                                     capture_output=True, text=True, timeout=120).stdout.strip()
            except Exception:
                act = ""
            if act == "yes":
                _store().set("a11y", key, True)
                GLib.idle_add(self.osd.show_text, "preferences-desktop-accessibility-symbolic", f"{name} 켬")
            elif act == "settings":
                subprocess.Popen(["sekai-settings", "--page=a11y"], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._asking = False
        threading.Thread(target=work, daemon=True).start()
        return False
