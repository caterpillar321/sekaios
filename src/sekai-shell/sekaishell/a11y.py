"""접근성 — 작업 표시줄(sekai-panel)이 맡는 몫 (설정은 설정 › 접근성, 저장은 sekaishell.store 의 a11y 섹션).

  돋보기        Win + = / Win + - / Win + Esc — 합성기의 커서 주변 확대(cursor:zoom_factor), OSD 로 배율
  색 필터       Win + Ctrl + C — 켜고 끈다 (화면 셰이더, 종류는 설정에서)
  화상 키보드   Win + Ctrl + O — wvkbd (wlroots 계열 합성기용 레이어 셸 키보드). 키는 가상 키보드로 들어가
                 ibus-hangul 을 거치므로 한글 입력기가 켜져 있으면 한글로 써진다
  고정 키       걸린 수식 키를 화면 오른쪽 아래에 보인다 (IPC sekaisticky). Shift 다섯 번 → 켤지 묻기
  필터 키       오른쪽 Shift 8초 → 켤지 묻기 (IPC sekaia11y)
  내레이터      Win + Ctrl + Enter — Orca (화면 읽기). 키는 합성기 → /usr/lib/sekai/a11yd → Orca (a11y KeyboardMonitor)
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
NARRATOR_BIN = "orca"
MAX_ZOOM = 10.0
FILTER_NAMES = {"grayscale": "회색조", "inverted": "반전", "grayscale-inverted": "회색조 반전",
                "deuteranopia": "적록 (녹색약)", "protanopia": "적록 (적색약)", "tritanopia": "청황 (청색약)"}
# HL_MODIFIER 비트 → 이름 (윈도우의 고정 키 표시처럼)
MODS = ((1 << 0, "Shift"), (1 << 2, "Ctrl"), (1 << 3, "Alt"), (1 << 6, "Win"))


def _store():
    from .store import Store         # 설정 앱과 같은 저장 — 조각 파일·즉시 반영까지
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


class FocusTracker:
    """돋보기가 켜져 있는 동안 키보드 포커스·글자 커서를 따라간다 (윈도우 돋보기의 "포커스 따라가기").
    AT-SPI 의 포커스·커서 이벤트로 자리를 알아 합성기에 hyprctl dispatch sekaizoomfocus x y — 마우스를 움직이면
    합성기가 다시 마우스를 따른다 (SEKAI_ZOOM_FOCUS). 웨이랜드에선 앱이 아는 좌표가 창 안 좌표뿐이라 지금 창의 자리를 더한다"""

    EVENTS = ("object:state-changed:focused", "object:text-caret-moved")

    def __init__(self, hypr):
        self.hypr = hypr
        self.listener = None
        self._last = None

    def start(self):
        if self.listener is not None:
            return
        try:
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
        except (ValueError, ImportError) as e:
            dbg("포커스 따라가기: Atspi 없음", e)
            return
        self.Atspi = Atspi
        self.listener = Atspi.EventListener.new(self._on_event)
        for ev in self.EVENTS:
            try:
                self.listener.register(ev)
            except GLib.Error as e:
                dbg("포커스 따라가기 등록 실패", ev, e)

    def stop(self):
        if self.listener is None:
            return
        for ev in self.EVENTS:
            try:
                self.listener.deregister(ev)
            except GLib.Error:
                pass
        self.listener = None
        self._last = None

    def _on_event(self, ev):
        try:
            Atspi = self.Atspi
            acc = ev.source
            if ev.type.startswith("object:state-changed:focused"):
                if not ev.detail1:
                    return
                r = acc.get_component_iface().get_extents(Atspi.CoordType.WINDOW)
                x, y = r.x + r.width / 2, r.y + r.height / 2
                if r.width <= 0 or r.height <= 0:
                    return
            else:
                t = acc.get_text_iface()
                if t is None:
                    return
                r = t.get_character_extents(max(0, ev.detail1), Atspi.CoordType.WINDOW)
                if r.width <= 0 and r.height <= 0:
                    return
                x, y = r.x, r.y + r.height / 2
            win = self.hypr.query("activewindow") or {}
            if not win or win.get("pid") != acc.get_process_id():
                return                  # 작업 표시줄·메뉴 같은 레이어, 다른 창 — 자리를 모른다
            ax, ay = win.get("at", [0, 0])
            pt = (round(ax + x), round(ay + y))
            if pt != self._last:
                self._last = pt
                self.hypr.dispatch(f"sekaizoomfocus {pt[0]} {pt[1]}")
        except Exception as e:          # 앱이 사라지는 중 등 — 따라가기만 건너뛴다
            dbg("포커스 따라가기", e)


class A11y:
    def __init__(self, hypr, osd, bottom):
        self.hypr = hypr
        self.osd = osd
        self.osk = None
        self.narrator = None
        self.focus = FocusTracker(hypr)
        self.badge = StickyBadge(bottom)
        self._asking = False
        # 로그인할 때 화상 키보드 (설정 › 접근성의 "로그인할 때 화상 키보드 띄우기")
        if (config.settings("a11y") or {}).get("osk"):
            GLib.timeout_add_seconds(3, lambda: self.osk_ctl("on") and False)
        # 로그인할 때 내레이터 (설정 › 접근성의 "로그인할 때 내레이터 켜기")
        if (config.settings("a11y") or {}).get("narrator"):
            GLib.timeout_add_seconds(3, lambda: self.narrator_ctl("on") and False)

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
        (self.focus.start if z > 1.0 else self.focus.stop)()
        if z <= 1.0:
            self.hypr.dispatch("sekaizoomfocus off")
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

    # ── 내레이터 (Orca) ──
    def _narrator_running(self):
        if self.narrator is not None and self.narrator.poll() is None:
            return True
        # 설정 앱·터미널에서 따로 띄운 Orca 도 켜진 것으로 본다
        return subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", NARRATOR_BIN],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def narrator_ctl(self, action="toggle"):
        on = {"on": True, "off": False}.get(action, not self._narrator_running())
        if on and not self._narrator_running():
            if not shutil.which(NARRATOR_BIN):
                self.osd.show_text("audio-speakers-symbolic", "내레이터(Orca)가 설치되지 않았습니다")
                return
            # 화면 읽기가 켜졌다고 알린다 — 크로미움·일렉트론 앱이 접근성 정보를 내놓는다
            subprocess.run(["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.narrator = subprocess.Popen([NARRATOR_BIN, "--replace"], stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                dbg("내레이터 실행 실패", e)
                self.narrator = None
                return
            self.osd.show_text("audio-speakers-symbolic", "내레이터 켬 — Win + Ctrl + Enter 로 끕니다")
        elif not on and self._narrator_running():
            p, self.narrator = self.narrator, None
            if p is not None and p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
                threading.Thread(target=p.wait, daemon=True).start()
            else:
                subprocess.run(["pkill", "-u", str(os.getuid()), "-x", NARRATOR_BIN],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "false"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.osd.show_text("audio-speakers-symbolic", "내레이터 끔")

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
