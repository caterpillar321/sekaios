"""Nenerobo — 탭 하나 (VTE 터미널 + 찾기 막대).

셸(또는 -e 로 받은 명령)을 VTE 에서 돌린다. 끝나면 탭을 닫는다 — 명령이 실패로 끝났으면(-e) 그 출력을 읽을 수 있게
"아무 키나 누르면 닫습니다" 를 띄우고 기다린다. 링크는 Ctrl+클릭으로 연다 (OSC 8 링크와 글 속의 주소 모두).
지금 무엇이 도는지는 pty 의 포그라운드 프로세스 그룹으로 안다 (셸이 아니면 닫기 전에 묻는다).
"""
import os
import signal

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402

from . import style  # noqa: E402
from .config import profile_argv, user_shell  # noqa: E402

PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400
URL_RE = (r"(?:https?|ftp|file)://[^\s<>\"'`]+[^\s<>\"'`.,;:!?)\]}]"
          r"|www\.[a-zA-Z0-9-]+\.[^\s<>\"'`]+[^\s<>\"'`.,;:!?)\]}]")
# 셸에 물려주지 않는 환경 변수 — 다른 터미널 안에서 이 터미널을 켰을 때 그 터미널의 것이 남지 않게
DROP_ENV = ("TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "VTE_VERSION", "KITTY_WINDOW_ID",
            "KITTY_PID", "KITTY_PUBLIC_KEY", "KITTY_INSTALLATION_DIR", "WINDOWID", "GIO_LAUNCHED_DESKTOP_FILE",
            "GIO_LAUNCHED_DESKTOP_FILE_PID", "DESKTOP_STARTUP_ID", "XDG_ACTIVATION_TOKEN")
ZOOM_MIN, ZOOM_MAX = 0.5, 3.0
WORD_CHARS = "-,./?%&#:_=+@~"          # 두 번 눌러 고를 때 단어에 넣는 기호 (경로·주소가 한 번에)


def rgba(hex_):
    c = Gdk.RGBA()
    c.parse(hex_)
    return c


CURSOR = {"ibeam": Vte.CursorShape.IBEAM, "block": Vte.CursorShape.BLOCK, "underline": Vte.CursorShape.UNDERLINE}


class TermTab(Gtk.Overlay):
    def __init__(self, win, argv=None, cwd=None, env=None, title=None, hold=False, profile=None):
        super().__init__()
        self.win = win
        self.profile = profile or {"id": "shell", "kind": "shell"}
        if argv is None:
            argv = profile_argv(self.profile)
        if not cwd and self.profile.get("cwd"):
            cwd = os.path.expanduser(self.profile["cwd"])
        self.admin = self.profile.get("kind") == "admin"
        self.scheme_override = self.profile.get("scheme") or None    # 프로필마다 다른 색 (없으면 전체 설정)
        self.argv = list(argv) if argv else [user_shell()]
        self.cmd_mode = bool(argv)          # 셸이 아닌 명령 (-e · 프로필의 명령 · SSH · 관리자)
        self.hold = hold
        self.fixed_title = title            # -T · 탭 이름 바꾸기 (없으면 프로그램이 알린 제목)
        self.pid = -1
        self.exited = False
        self.wait_close = False             # 끝난 뒤 키를 기다리는 중
        self.env = dict(env or os.environ)
        self.start_cwd = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")

        t = self.term = Vte.Terminal()
        t.set_hexpand(True)
        t.set_vexpand(True)
        self.apply_config()
        t.set_audible_bell(False)
        t.set_word_char_exceptions(WORD_CHARS)
        t.set_scroll_on_keystroke(True)
        t.set_scroll_on_output(False)
        t.set_allow_hyperlink(True)
        t.set_mouse_autohide(True)
        try:
            tag = t.match_add_regex(Vte.Regex.new_for_match(URL_RE, -1, PCRE2_MULTILINE), 0)
            t.match_set_cursor_name(tag, "pointer")
        except GLib.Error:
            pass
        self.set_child(t)

        t.connect("child-exited", self._exited)
        t.connect("notify::window-title", lambda *_: self.win.tab_changed(self))
        t.connect("bell", lambda *_: self.win.tab_bell(self))
        t.connect("selection-changed", self._selection_changed)

        # Ctrl+클릭 = 링크 열기, 오른쪽 클릭 = 메뉴 (VTE 보다 먼저 받는다)
        click = Gtk.GestureClick(button=0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        t.add_controller(click)
        # Ctrl+휠 = 글자 크기
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._scroll)
        t.add_controller(scroll)

        self._build_search()
        self._spawn()

    # ── 모양 (설정이 바뀌면 다시 부른다) ──
    @property
    def scheme_id(self):
        return style.scheme_id(self.scheme_override or self.win.cfg["scheme"], self.win.ap)

    def apply_config(self):
        cfg, t = self.win.cfg, self.term
        sc = style.SCHEMES[self.scheme_id]
        t.set_font(Pango.FontDescription.from_string(cfg.font()))
        t.set_colors(rgba(sc["fg"]), rgba(sc["bg"]), [rgba(c) for c in sc["palette"]])
        t.set_color_cursor(rgba(sc["cursor"]))
        t.set_color_cursor_foreground(rgba(sc["cursor_fg"]))
        t.set_color_highlight(rgba(sc["sel_bg"]) if sc.get("sel_bg") else None)
        t.set_color_highlight_foreground(rgba(sc["sel_fg"]) if sc.get("sel_fg") else None)
        t.set_bold_is_bright(bool(cfg["bold_bright"]))
        t.set_cursor_shape(CURSOR.get(cfg["cursor_shape"], Vte.CursorShape.IBEAM))
        t.set_cursor_blink_mode(Vte.CursorBlinkMode.ON if cfg["cursor_blink"] else Vte.CursorBlinkMode.OFF)
        try:
            t.set_scrollback_lines(max(0, min(1000000, int(cfg["scrollback"]))))
        except (TypeError, ValueError):
            t.set_scrollback_lines(10000)

    def _selection_changed(self, *_):
        if self.win.cfg["copy_on_select"] and self.term.get_has_selection():
            self.term.copy_clipboard_format(Vte.Format.TEXT)

    # ── 실행 ──
    def _spawn(self):
        env = {k: v for k, v in self.env.items() if k not in DROP_ENV}
        env.update(TERM="xterm-256color", COLORTERM="truecolor", TERM_PROGRAM="Nenerobo")
        envv = [f"{k}={v}" for k, v in env.items()]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, self.start_cwd, self.argv, envv, GLib.SpawnFlags.SEARCH_PATH,
                              None, None, -1, None, self._spawned, None)

    def _spawned(self, _term, pid, error, *_):
        if error is not None:
            self.exited = True
            self.wait_close = True
            self.term.feed(f"\x1b[31m{' '.join(self.argv)} 을(를) 실행하지 못했습니다: {error.message}\x1b[0m\r\n"
                           "\x1b[2m아무 키나 누르면 닫습니다\x1b[0m".encode())
            return
        self.pid = pid

    def _exited(self, _term, status):
        self.exited = True
        try:
            code = os.waitstatus_to_exitcode(status)
        except ValueError:
            code = status
        if self.hold or (self.cmd_mode and code != 0):
            self.wait_close = True
            why = f"중단됨 (신호 {-code})" if code < 0 else f"코드 {code}"
            self.term.feed(f"\r\n\x1b[2m[프로세스가 끝났습니다 ({why}) — 아무 키나 누르면 닫습니다]\x1b[0m"
                           .encode())
            self.win.tab_changed(self)
            return
        self.win.close_tab(self, ask=False)

    def hangup(self):
        """탭을 닫을 때 — 셸과 그 아래 프로그램에 끊김(SIGHUP)을 알린다"""
        if self.pid > 0 and not self.exited:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                pass

    # ── 지금 상태 ──
    def foreground(self):
        """셸 말고 앞에서 도는 프로그램의 이름 (없으면 None)"""
        if self.pid <= 0 or self.exited:
            return None
        pty = self.term.get_pty()
        if pty is None:
            return None
        try:
            pg = os.tcgetpgrp(pty.get_fd())
        except OSError:
            return None
        if pg <= 0 or pg == self.pid:
            return None
        try:
            with open(f"/proc/{pg}/comm", encoding="utf-8") as f:
                return f.read().strip() or "프로그램"
        except OSError:
            return "프로그램"

    def cwd(self):
        """지금 폴더 — 셸의 /proc/<pid>/cwd (셸 설정 없이도 안다)"""
        if self.pid > 0 and not self.exited:
            try:
                return os.readlink(f"/proc/{self.pid}/cwd")
            except OSError:
                pass
        return self.start_cwd

    def title(self):
        if self.fixed_title:
            return self.fixed_title
        t = self.term.get_property("window-title")
        if t:
            return t
        if self.profile.get("name") and self.profile.get("kind") != "shell":
            return self.profile["name"]
        return os.path.basename(self.argv[0])

    # ── 글자 크기 ──
    def zoom(self, step):
        t = self.term
        s = 1.0 if step == 0 else t.get_font_scale() * (1.1 if step > 0 else 1 / 1.1)
        t.set_font_scale(max(ZOOM_MIN, min(ZOOM_MAX, s)))

    def _scroll(self, ctrl, _dx, dy):
        if ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.zoom(-1 if dy > 0 else 1)
            return True
        return False

    # ── 복사 · 붙여넣기 ──
    def copy(self):
        if self.term.get_has_selection():
            self.term.copy_clipboard_format(Vte.Format.TEXT)
            return True
        return False

    def paste(self):
        self.term.paste_clipboard()

    # ── 링크 · 오른쪽 클릭 ──
    def link_at(self, x, y):
        u = self.term.check_hyperlink_at(x, y)
        if u:
            return u
        m, _tag = self.term.check_match_at(x, y)
        if m and m.startswith("www."):
            m = "https://" + m
        return m

    def _pressed(self, g, n, x, y):
        btn = g.get_current_button()
        state = g.get_current_event_state()
        if btn == 1 and n == 1 and state & Gdk.ModifierType.CONTROL_MASK:
            url = self.link_at(x, y)
            if url:
                g.set_state(Gtk.EventSequenceState.CLAIMED)
                self.win.open_uri(url)
        elif btn == 3 and not state & Gdk.ModifierType.SHIFT_MASK:
            g.set_state(Gtk.EventSequenceState.CLAIMED)
            self.win.context_menu(self, x, y, self.link_at(x, y))

    # ── 찾기 (Ctrl+Shift+F) ──
    def _build_search(self):
        bar = self.sbar = Gtk.Box(spacing=4)
        bar.add_css_class("nr-search")
        bar.set_halign(Gtk.Align.END)
        bar.set_valign(Gtk.Align.START)
        e = self.sentry = Gtk.SearchEntry(placeholder_text="찾기")
        e.connect("search-changed", self._search_changed)
        e.connect("activate", lambda *_: self.find(up=True))
        e.connect("stop-search", lambda *_: self.hide_search())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._search_key)
        e.add_controller(keys)
        bar.append(e)
        for icon, tip, up in (("go-up-symbolic", "이전 (Enter)", True), ("go-down-symbolic", "다음 (Shift+Enter)", False)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, u=up: self.find(up=u))
            bar.append(b)
        case = self.scase = Gtk.ToggleButton(label="Aa", tooltip_text="대·소문자 구분")
        case.add_css_class("flat")
        case.connect("toggled", self._search_changed)
        bar.append(case)
        x = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="닫기 (Esc)")
        x.add_css_class("flat")
        x.connect("clicked", lambda *_: self.hide_search())
        bar.append(x)
        bar.set_visible(False)
        self.add_overlay(bar)

    def show_search(self):
        self.sbar.set_visible(True)
        if self.term.get_has_selection():
            sel = self.term.get_text_selected(Vte.Format.TEXT) if hasattr(self.term, "get_text_selected") else None
            if sel and "\n" not in sel and len(sel) < 200:
                self.sentry.set_text(sel)
        self.sentry.grab_focus()
        self.sentry.select_region(0, -1)

    def hide_search(self):
        self.sbar.set_visible(False)
        self.term.search_set_regex(None, 0)
        self.term.grab_focus()

    def _search_changed(self, *_):
        text = self.sentry.get_text()
        if not text:
            self.term.search_set_regex(None, 0)
            self.sentry.remove_css_class("error")
            return
        flags = PCRE2_MULTILINE | (0 if self.scase.get_active() else PCRE2_CASELESS)
        try:
            rx = Vte.Regex.new_for_search(GLib.Regex.escape_string(text, -1), -1, flags)
        except GLib.Error:
            return
        self.term.search_set_regex(rx, 0)
        self.term.search_set_wrap_around(True)
        self.find(up=True)

    def find(self, up=True):
        if not self.sentry.get_text():
            return
        ok = self.term.search_find_previous() if up else self.term.search_find_next()
        (self.sentry.remove_css_class if ok else self.sentry.add_css_class)("error")

    def _search_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.find(up=False)
            return True
        return False


def open_uri(parent, uri):
    try:
        Gtk.UriLauncher.new(uri).launch(parent, None, None, None)
    except (AttributeError, GLib.Error):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            pass
