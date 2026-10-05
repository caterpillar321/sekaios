"""Windows 프로그램 열기 — .exe · .msi 를 더블클릭하면 뜨는 창 (sekai-wine open <파일>).

  설치 프로그램 → 이 앱만의 환경(C: 드라이브)을 새로 만들어 설치하고, 생긴 바로 가기를 시작 메뉴에 올린다
  그냥 실행    → 설치 없이 공용 환경("quick")에서 한 번 돌린다

환경을 만드는 데(처음 한 번) 30초~1분. 설치 중 취소하면 그 환경을 통째로 지운다.
"""
import os
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import theme  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402
from . import core  # noqa: E402

APP_ID = "org.sekaios.WineOpen"
APP_NAME = "Windows 프로그램"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]

CSS = """
.wo-window { background: @winbg; color: @fg; }
.wo-body { padding: 26px 30px 18px 30px; }
label.wo-name { font-size: 16.5pt; font-weight: 700; color: @fg; }
label.wo-sub { color: @text2; }
label.wo-state { font-weight: 600; color: @fg; }
.wo-note {
    background: alpha(@accent, 0.08);
    border: 1px solid alpha(@accent, 0.30);
    border-radius: 8px;
    padding: 10px 14px;
}
.wo-bad {
    background: alpha(#e81123, 0.10);
    border: 1px solid alpha(#e81123, 0.40);
    border-radius: 8px;
    padding: 10px 14px;
}
.wo-foot { padding: 14px 30px 20px 30px; border-top: 1px solid @line; background: @surface; }
label.wo-progress-text { color: @text2; font-size: 9.75pt; }
"""


def _load_css(a):
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    body = ""
    for p in CSS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + CSS).encode())
    except GLib.Error as e:
        print("[sekai-wine] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    theme.apply_contrast_css()


def _label(text="", cls=None, wrap=True):
    lb = Gtk.Label(label=text, xalign=0)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_max_width_chars(60)
    if cls:
        lb.get_style_context().add_class(cls)
    return lb


def _icon(icon, px):
    """아이콘 이름 또는 절대 경로 (Windows 앱 아이콘은 exe 에서 꺼낸 png 의 경로)"""
    if icon.startswith("/"):
        try:
            from gi.repository import GdkPixbuf
            return Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(icon, px, px))
        except GLib.Error:
            icon = "application-x-ms-dos-executable"
    img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    img.set_pixel_size(px)
    return img


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


class OpenWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path):
        super().__init__(application=app, title=APP_NAME)
        self.path = path
        self.env = None
        self.busy = False
        self.cancelled = False
        self.set_default_size(600, -1)
        self.set_resizable(False)
        self.set_icon_name("application-x-ms-dos-executable")
        self.get_style_context().add_class("wo-window")
        self.connect("delete-event", self._on_delete)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.get_style_context().add_class("wo-body")
        outer.pack_start(body, True, True, 0)

        head = Gtk.Box(spacing=18)
        icon = Gtk.Image.new_from_icon_name("application-x-ms-dos-executable", Gtk.IconSize.DIALOG)
        icon.set_pixel_size(64)
        icon.set_valign(Gtk.Align.START)
        head.pack_start(icon, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        col.pack_start(_label(os.path.basename(path), "wo-name"), False, False, 0)
        try:
            sz = _human(os.path.getsize(path))
        except OSError:
            sz = ""
        kind = "Windows 설치 패키지 (.msi)" if path.lower().endswith(".msi") else "Windows 프로그램"
        col.pack_start(_label(" · ".join(x for x in (kind, sz) if x), "wo-sub"), False, False, 0)
        head.pack_start(col, True, True, 0)
        body.pack_start(head, False, False, 0)

        # ── 고르기 ──
        self.choose = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.r_install = Gtk.RadioButton.new_with_label(None, "설치 — 이 앱만의 환경을 만들어 설치하고 시작 메뉴에 올립니다")
        self.r_run = Gtk.RadioButton.new_with_label_from_widget(self.r_install, "그냥 실행 — 설치하지 않고 이번만 실행합니다")
        (self.r_install if core.looks_like_installer(path) else self.r_run).set_active(True)
        self.choose.pack_start(self.r_install, False, False, 0)
        self.choose.pack_start(self.r_run, False, False, 0)
        nrow = Gtk.Box(spacing=10)
        nrow.pack_start(_label("앱 이름", wrap=False), False, False, 0)
        self.name = Gtk.Entry()
        self.name.set_text(core.guess_name(path))
        self.name.set_hexpand(True)
        self.name.get_accessible().set_name("앱 이름")
        nrow.pack_start(self.name, True, True, 0)
        self.choose.pack_start(nrow, False, False, 0)
        self.r_install.connect("toggled", lambda b: nrow.set_sensitive(b.get_active()))
        nrow.set_sensitive(self.r_install.get_active())
        self.game = Gtk.CheckButton(label="게임 — Proton 으로 실행 (곧 지원)")
        self.game.set_sensitive(False)
        self.choose.pack_start(self.game, False, False, 0)
        eng = core.default_engine("app")
        self.eng_note = _label(f"엔진: {eng['label']}" if eng else "", "wo-sub")
        self.choose.pack_start(self.eng_note, False, False, 0)
        body.pack_start(self.choose, False, False, 0)

        self.note = Gtk.Box(spacing=10)
        self.note.get_style_context().add_class("wo-note")
        self.note_img = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.LARGE_TOOLBAR)
        self.note_img.set_valign(Gtk.Align.START)
        self.note.pack_start(self.note_img, False, False, 0)
        self.note_text = _label("Windows 프로그램은 SekaiOS 의 앱과 달리 Wine 으로 돌아갑니다. 잘 안 되는 프로그램도 있습니다.")
        self.note.pack_start(self.note_text, True, True, 0)
        body.pack_start(self.note, False, False, 0)

        # ── 진행 ──
        self.prog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.state = _label("", "wo-state")
        self.bar = Gtk.ProgressBar()
        self.sub = _label("", "wo-progress-text")
        for w in (self.state, self.bar, self.sub):
            self.prog.pack_start(w, False, False, 0)
        self.prog.set_no_show_all(True)
        body.pack_start(self.prog, False, False, 0)

        # ── 결과 (설치한 앱들) ──
        self.result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.result.set_no_show_all(True)
        body.pack_start(self.result, False, False, 0)

        foot = Gtk.Box(spacing=8)
        foot.get_style_context().add_class("wo-foot")
        outer.pack_start(foot, False, False, 0)
        self.b_cancel = Gtk.Button(label="취소")
        self.b_cancel.connect("clicked", lambda *_: self._cancel())
        self.b_go = Gtk.Button(label="설치" if self.r_install.get_active() else "실행")
        self.b_go.get_style_context().add_class("suggested-action")
        self.b_go.connect("clicked", lambda *_: self._go())
        self.r_install.connect("toggled", lambda b: self.b_go.set_label("설치" if b.get_active() else "실행"))
        foot.pack_end(self.b_go, False, False, 0)
        foot.pack_end(self.b_cancel, False, False, 0)
        if not eng:
            self._bad("Wine 엔진이 설치돼 있지 않습니다 — sekai-wine-11.0 패키지가 필요합니다.")
            self.b_go.set_sensitive(False)
        self.show_all()

    # ── 상태 ──
    def _bad(self, text):
        ctx = self.note.get_style_context()
        ctx.remove_class("wo-note")
        ctx.add_class("wo-bad")
        self.note_img.set_from_icon_name("dialog-error", Gtk.IconSize.LARGE_TOOLBAR)
        self.note_text.set_text(text)
        self.note.show_all()

    def _progress(self, state, sub=""):
        self.prog.show()
        for w in (self.state, self.bar, self.sub):
            w.show()
        self.state.set_text(state)
        self.sub.set_text(sub)
        if not getattr(self, "_pulse", None):
            self._pulse = GLib.timeout_add(120, lambda: (self.bar.pulse(), self.busy)[1])

    def _ui(self, fn, *a):
        GLib.idle_add(lambda: (fn(*a), False)[1])

    # ── 실행 ──
    def _go(self):
        if self.b_go.get_label() == "닫기":
            self.destroy()
            return
        self.busy = True
        self.choose.set_sensitive(False)
        self.b_go.set_sensitive(False)
        self.note.hide()
        if self.r_install.get_active():
            name = self.name.get_text().strip() or core.guess_name(self.path)
            threading.Thread(target=self._install, args=(name,), daemon=True).start()
        else:
            threading.Thread(target=self._run_once, daemon=True).start()

    def _install(self, name):
        try:
            self.env = env = core.create(name, "app")
            self._ui(self._progress, "환경을 만드는 중…", "이 앱만의 C: 드라이브 — 처음 30초~1분쯤 걸립니다")
            core.init(env)
            if self.cancelled:
                return
            core.snapshot(env, "설치 전")
            self._ui(self._progress, "설치 프로그램을 실행했습니다", "설치 창의 안내를 따라 하세요 — 끝나면 여기로 돌아옵니다")
            rc = core.spawn(env, self.path, install=True).wait()
            core.wait_all(env)
            if self.cancelled:
                return
            self._ui(self._progress, "바로 가기를 정리하는 중…")
            added = core.collect(env)
            self._ui(self._done, env, added, rc)
        except Exception as e:
            self._ui(self._failed, str(e))

    def _run_once(self):
        try:
            env = core.load(core.QUICK)
            if not env or not env.get("initialized"):
                if env:
                    core.remove(env)
                env = core.create("바로 실행", "app", eid=core.QUICK)
                self._ui(self._progress, "처음 한 번 환경을 만드는 중…", "30초~1분쯤 걸립니다")
                core.init(env)
            core.spawn(env, self.path)
            self._ui(self.destroy)
        except Exception as e:
            self._ui(self._failed, str(e))

    def _done(self, env, added, rc=0):
        self.busy = False
        self.prog.hide()
        self.choose.hide()                       # 끝난 뒤엔 결과만
        for c in self.result.get_children():
            self.result.remove(c)
        if added:
            self.result.pack_start(_label(f"{env['name']} 을(를) 설치했습니다 — 시작 메뉴에 올렸습니다:", "wo-state"),
                                   False, False, 0)
            for a in added:
                row = Gtk.Box(spacing=10)
                img = _icon(a["icon"], 24)
                row.pack_start(img, False, False, 0)
                row.pack_start(_label(a["name"]), True, True, 0)
                b = Gtk.Button(label="실행")
                b.get_accessible().set_name(f"{a['name']} 실행")
                b.connect("clicked", lambda _b, app=a: core.spawn(env, app["lnk"]))
                row.pack_start(b, False, False, 0)
                self.result.pack_start(row, False, False, 0)
        elif rc not in (0, None):
            self.result.pack_start(_label(f"설치 프로그램이 오류로 끝났습니다 (종료 코드 {rc}).", "wo-state"), False, False, 0)
            self.result.pack_start(_label("설치가 안 됐을 수 있습니다. 설정 › Windows 앱 에서 이 환경을 지울 수 있습니다.",
                                          "wo-sub"), False, False, 0)
        else:
            self.result.pack_start(_label("설치는 끝났지만 시작 메뉴 바로 가기를 찾지 못했습니다.", "wo-state"), False, False, 0)
            self.result.pack_start(_label("설정 › Windows 앱 에서 이 환경의 C: 드라이브를 열어 실행 파일을 찾을 수 있습니다.",
                                          "wo-sub"), False, False, 0)
            b = Gtk.Button(label="C: 드라이브 열기")
            b.connect("clicked", lambda *_: self._open_c(env))
            self.result.pack_start(b, False, False, 0)
        self.result.show()                       # no_show_all 이라 show_all 은 자기 자신을 건너뛴다
        for c in self.result.get_children():
            c.show_all()
        self.b_cancel.hide()
        self.b_go.set_label("닫기")
        self.b_go.set_sensitive(True)

    def _open_c(self, env):
        import subprocess
        subprocess.Popen(["sekai-files", os.path.join(core.prefix(env), "drive_c")], start_new_session=True)

    def _failed(self, msg):
        self.busy = False
        self.prog.hide()
        self._bad(f"실패했습니다 — {msg}")
        if self.env and not self.env.get("apps"):
            core.remove(self.env)                    # 반쯤 만든 환경은 남기지 않는다
            self.env = None
        self.b_cancel.hide()
        self.b_go.set_label("닫기")
        self.b_go.set_sensitive(True)

    def _cancel(self):
        if not self.busy:
            self.destroy()
            return
        self.cancelled = True
        env = self.env
        if env:
            threading.Thread(target=lambda: (core.remove(env), GLib.idle_add(self.destroy)), daemon=True).start()
        else:
            self.destroy()

    def _on_delete(self, *_):
        if self.busy:
            self._cancel()
            return True
        return False


class WineOpen(Gtk.Application):
    """하나만 뜬다 — 이미 떠 있으면 파일이 그 프로세스로 넘어간다 (HANDLES_OPEN). 파일마다 창 하나"""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        _load_css(appearance())

    def do_open(self, files, _n, _hint):
        for f in files:
            if f.get_path():
                OpenWindow(self, f.get_path()).present()

    def do_activate(self):
        pass


def main(paths):
    GLib.set_prgname(APP_ID)              # 창 class = org.sekaios.WineOpen (작업 표시줄 아이콘·시험이 찾는 이름)
    return WineOpen().run([sys.argv[0]] + [os.path.abspath(p) for p in paths])
