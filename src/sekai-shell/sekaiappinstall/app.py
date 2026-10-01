"""SekaiOS 앱 설치 관리자 — 윈도우의 "앱 설치 관리자"처럼 .deb 를 더블클릭하면 뜨는 창.

실행: sekai-appinstall [파일.deb …]
    이미 떠 있으면 새 프로세스를 만들지 않는다 — 파일마다 창 하나 (같은 파일이면 그 창을 앞으로).

흐름: 확인(sekai-apps inspect, 보통 권한) → [설치] → 사용자 계정 컨트롤(pkexec) → 설치(sekai-apps install-deb)
      → [실행]. 파일은 도우미의 표준 입력으로 넘긴다 (경로를 넘기면 인증 뒤에 바꿔치기할 수 있다).
SekaiOS 부품을 지우게 되는 꾸러미는 설치 단추를 주지 않는다 (도우미도 다시 막는다).
"""
import os
import shutil
import sys
import tempfile
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import appmgr, dbg, theme  # noqa: E402
from sekaishell.taskmgr_common import appearance  # noqa: E402

APP_ID = "org.sekaios.AppInstaller"
APP_NAME = "앱 설치 관리자"
HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]

AI_CSS = """
.ai-window { background: @winbg; color: @fg; }
.ai-body { padding: 26px 30px 18px 30px; }
label.ai-name { font-size: 22px; font-weight: 700; color: @fg; }
label.ai-sub { color: @text2; }
label.ai-state { font-weight: 600; color: @fg; }
label.ai-summary { color: @fg; }
label.ai-desc { color: @text2; }
.ai-warn {
    background: alpha(#e0a100, 0.12);
    border: 1px solid alpha(#e0a100, 0.40);
    border-radius: 8px;
    padding: 10px 14px;
}
.ai-block {
    background: alpha(#e81123, 0.10);
    border: 1px solid alpha(#e81123, 0.40);
    border-radius: 8px;
    padding: 10px 14px;
}
.ai-ok {
    background: alpha(#10893e, 0.12);
    border: 1px solid alpha(#10893e, 0.40);
    border-radius: 8px;
    padding: 10px 14px;
}
.ai-foot { padding: 14px 30px 20px 30px; border-top: 1px solid @line; background: @surface; }
label.ai-progress-text { color: @text2; font-size: 13px; }
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
        prov.load_from_data((prelude + body + AI_CSS).encode())
    except GLib.Error as e:
        print("[sekai-appinstall] CSS 오류:", e.message, file=sys.stderr, flush=True)
        return None
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return prov


def _label(text="", cls=None, wrap=True, selectable=False):
    lb = Gtk.Label(label=text, xalign=0)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_max_width_chars(60)
    lb.set_selectable(selectable)
    if cls:
        lb.get_style_context().add_class(cls)
    return lb


def _boxed(cls, icon, text):
    box = Gtk.Box(spacing=10)
    box.get_style_context().add_class(cls)
    img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    img.set_valign(Gtk.Align.START)
    box.pack_start(img, False, False, 0)
    lb = _label(text)
    box.pack_start(lb, True, True, 0)
    box.label = lb
    return box


class InstallWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path):
        super().__init__(application=app, title=APP_NAME)
        self.path = path
        self.busy = False               # 설치 중 — 닫기를 막는다
        self.plan = None
        self.apps = []
        self.tmp = tempfile.mkdtemp(prefix="sekai-appinstall-")
        self.set_default_size(620, -1)
        self.set_resizable(False)
        self.set_icon_name("system-software-install")
        self.get_style_context().add_class("ai-window")
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", lambda *_: shutil.rmtree(self.tmp, ignore_errors=True))
        self.connect("key-press-event", self._on_key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.get_style_context().add_class("ai-body")
        outer.pack_start(body, True, True, 0)

        head = Gtk.Box(spacing=18)
        self.icon = Gtk.Image.new_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(64)
        self.icon.set_valign(Gtk.Align.START)
        head.pack_start(self.icon, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.name = _label(os.path.basename(path), "ai-name")
        self.pub = _label("", "ai-sub")
        self.meta = _label("", "ai-sub")
        for w in (self.name, self.pub, self.meta):
            col.pack_start(w, False, False, 0)
        head.pack_start(col, True, True, 0)
        body.pack_start(head, False, False, 0)

        self.state = _label("설치 파일을 확인하는 중…", "ai-state")
        body.pack_start(self.state, False, False, 0)
        self.summary = _label("", "ai-summary")
        body.pack_start(self.summary, False, False, 0)
        self.more = Gtk.Expander(label="자세한 설명")
        self.desc = _label("", "ai-desc", selectable=True)
        self.more.add(self.desc)
        body.pack_start(self.more, False, False, 0)
        self.deps = Gtk.Expander()
        self.deps_text = _label("", "ai-desc", selectable=True)
        self.deps.add(self.deps_text)
        body.pack_start(self.deps, False, False, 0)

        self.notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.pack_start(self.notes, False, False, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        foot.get_style_context().add_class("ai-foot")
        outer.pack_start(foot, False, False, 0)
        self.progress = Gtk.ProgressBar()
        self.progress.get_style_context().add_class("update-progress")
        self.progress_text = _label("", "ai-progress-text", wrap=False)
        self.progress_text.set_ellipsize(Pango.EllipsizeMode.END)
        foot.pack_start(self.progress, False, False, 0)
        foot.pack_start(self.progress_text, False, False, 0)
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        self.spinner = Gtk.Spinner()
        btns.pack_start(self.spinner, False, False, 0)
        self.close_btn = Gtk.Button(label="취소")
        self.close_btn.connect("clicked", lambda *_: self.close())
        self.run_btn = Gtk.Button(label="실행")
        self.run_btn.connect("clicked", self._on_run)
        self.main_btn = Gtk.Button(label="설치")
        self.main_btn.get_style_context().add_class("accent-btn")
        self.main_btn.connect("clicked", self._on_install)
        for b in (self.close_btn, self.run_btn, self.main_btn):
            b.set_size_request(104, -1)
            btns.pack_start(b, False, False, 0)
        foot.pack_start(btns, False, False, 0)

        self.show_all()
        for w in (self.more, self.deps, self.progress, self.progress_text, self.run_btn, self.summary):
            w.hide()
        self.main_btn.set_sensitive(False)
        self.spinner.start()
        self._inspect()

    # ── 확인 ──
    def _inspect(self):
        self.plan = appmgr.Plan()
        appmgr.run_helper(["inspect", self.path], self.plan.feed, self._inspected)
        # 앱 이름·아이콘은 따로 (큰 꾸러미는 몇 초 걸린다 — 정보가 먼저 보이게)
        def look():
            name, icon = appmgr.deb_appinfo(self.path, self.tmp)
            GLib.idle_add(self._got_appinfo, name, icon)
        threading.Thread(target=look, daemon=True).start()

    def _got_appinfo(self, name, icon):
        if name:
            self.name.set_text(name)
            self.set_title(f"{name} — {APP_NAME}")
        if icon:
            try:
                if os.path.isabs(icon):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon, 64, 64)
                    self.icon.set_from_pixbuf(pb)
                elif Gtk.IconTheme.get_default().has_icon(icon):
                    self.icon.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
                    self.icon.set_pixel_size(64)
            except GLib.Error as e:
                dbg("아이콘을 읽지 못했습니다:", e)
        return False

    def _inspected(self, rc):
        self.spinner.stop()
        p = self.plan
        if p.error or not p.done:
            self._fail(p.error or f"설치 파일을 확인하지 못했습니다 (코드 {rc})", final=True)
            return False
        info = p.info
        if self.name.get_text() == os.path.basename(self.path):
            self.name.set_text(info.get("Package", self.name.get_text()))
        pub = appmgr.publisher(info.get("Maintainer"))
        self.pub.set_text(f"게시자: {pub} (확인되지 않음)" if pub else "게시자: 알 수 없음")
        meta = [f"버전 {info.get('Version', '?')}"]
        size = appmgr.human_size(info.get("Size"))
        if size:
            meta.append(f"설치 크기 {size}")
        self.meta.set_text(" · ".join(meta))
        if info.get("Summary"):
            self.summary.set_text(info["Summary"])
            self.summary.show()
        desc = "\n".join(p.desc).strip()
        if info.get("Homepage"):
            desc = (desc + "\n\n" if desc else "") + f"홈페이지: {info['Homepage']}"
        if desc:
            self.desc.set_text(desc)
            self.more.show()
        extra = [*(f"{n} (새로 설치)" for n in p.add), *(f"{n} (업데이트)" for n in p.upg)]
        if extra:
            self.deps.set_label(f"함께 설치되는 구성 요소 {len(extra)}개")
            self.deps_text.set_text("\n".join(extra))
            self.deps.show()

        state = p.state
        ver = info.get("Version", "")
        label = "설치"
        if state == "new":
            self.state.set_text("이 앱을 설치합니다.")
        elif state == "upgrade":
            self.state.set_text(f"새 버전으로 업데이트합니다 (설치된 버전 {p.installed} → {ver}).")
            label = "업데이트"
        elif state == "same":
            self.state.set_text("같은 버전이 이미 설치되어 있습니다. 다시 설치할 수 있습니다.")
            label = "다시 설치"
        elif state == "downgrade":
            self.state.set_text(f"설치된 버전({p.installed})보다 낮은 버전입니다.")
            label = "이전 버전 설치"
            self._note("ai-warn", "dialog-warning",
                       "이전 버전으로 바꾸면 앱이 저장한 설정이 맞지 않아 제대로 동작하지 않을 수 있습니다.")
        self.main_btn.set_label(label)
        if p.dele and not p.blocks:
            self._note("ai-warn", "dialog-warning",
                       f"설치하면 다음 패키지가 지워집니다: {', '.join(p.dele[:8])}"
                       + (f" 외 {len(p.dele) - 8}개" if len(p.dele) > 8 else ""))
        if p.blocks:
            self.state.set_text("이 파일은 설치할 수 없습니다.")
            for b in p.blocks:
                self._note("ai-block", "dialog-error", b)
            self.main_btn.hide()
            self.close_btn.set_label("닫기")
            self.close_btn.grab_focus()
            return False
        self._note("ai-warn", "security-medium",
                   "인터넷에서 받은 설치 파일은 설치하는 동안 관리자 권한으로 실행됩니다. "
                   "믿을 수 있는 곳에서 받은 파일만 설치하세요.")
        self.main_btn.set_sensitive(True)
        self.main_btn.grab_focus()
        return False

    def _note(self, cls, icon, text):
        box = _boxed(cls, icon, text)
        box.show_all()
        self.notes.pack_start(box, False, False, 0)
        return box

    def _clear_notes(self):
        for c in self.notes.get_children():
            c.destroy()
        self._cancel_note = None

    # ── 설치 ──
    def _on_install(self, *_):
        if self.busy or not self.main_btn.get_sensitive():
            return                      # 연타 — 이미 시작했다
        if not os.path.isfile(self.path):
            self._fail("설치 파일을 찾을 수 없습니다 — 옮겨졌거나 지워졌을 수 있습니다", final=True)
            return
        self.busy = True
        self.main_btn.set_sensitive(False)
        self.close_btn.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.show()
        self.progress_text.set_text("관리자 인증을 기다리는 중…")
        self.progress_text.show()
        self.spinner.start()
        self._result = {"error": None, "apps": []}
        appmgr.run_helper(["install-deb", os.path.basename(self.path)], self._line, self._installed,
                          root=True, stdin_path=self.path)

    def _line(self, kind, rest):
        if kind == "PROGRESS":
            pct, _, text = rest.partition(" ")
            try:
                self.progress.set_fraction(max(0, min(100, int(pct))) / 100)
            except ValueError:
                pass
            self.progress_text.set_text(text)
        elif kind == "ERROR":
            self._result["error"] = rest
        elif kind == "APP":
            self._result["apps"].append(rest)
        return False

    def _installed(self, rc):
        self.busy = False
        self.spinner.stop()
        self.close_btn.set_sensitive(True)
        self.progress.hide()
        self.progress_text.hide()
        if rc in appmgr.CANCELLED and not self._result["error"]:
            self.progress_text.set_text("")
            self.main_btn.set_sensitive(True)
            # 보안 경고는 그대로 두고 (다시 누를 수 있으니), 취소 알림은 하나만
            if getattr(self, "_cancel_note", None) is not None:
                self._cancel_note.destroy()
            self._cancel_note = self._note("ai-warn", "dialog-information", "인증이 취소되어 설치하지 않았습니다.")
            return False
        if rc != 0 or self._result["error"]:
            self._fail(self._result["error"] or f"설치 도우미가 비정상 종료했습니다 (코드 {rc})")
            return False
        self._clear_notes()
        self.state.set_text("설치했습니다.")
        self._note("ai-ok", "emblem-ok-symbolic", "시작 메뉴에서 찾을 수 있습니다." if self._result["apps"]
                   else "이 꾸러미에는 시작 메뉴에 나오는 앱이 없습니다 (명령줄 프로그램이나 구성 요소일 수 있습니다).")
        self.deps.hide()
        self.main_btn.hide()
        self.close_btn.set_label("닫기")
        self.apps = self._result["apps"]
        if self.apps:
            self.run_btn.show()
            self.run_btn.grab_focus()
        else:
            self.close_btn.grab_focus()
        return False

    def _fail(self, text, final=False):
        self._clear_notes()
        self._note("ai-block", "dialog-error", text)
        if final:
            self.state.set_text("이 파일은 설치할 수 없습니다.")
            self.main_btn.hide()
            self.close_btn.set_label("닫기")
        else:
            self.main_btn.set_sensitive(True)
            self.main_btn.set_label("다시 시도")

    def _on_run(self, *_):
        for d in self.apps:
            try:
                info = Gio.DesktopAppInfo.new_from_filename(d)
            except TypeError:
                info = None
            if info is not None and not info.get_nodisplay():
                try:
                    info.launch([], Gdk.Display.get_default().get_app_launch_context())
                except GLib.Error as e:
                    dbg("실행하지 못했습니다:", e)
                    continue
                self.close()
                return

    # ── 닫기 ──
    def _on_delete(self, *_):
        if self.busy:
            # 설치는 창이 없어도 끝까지 가지만, 결과를 보여 줄 곳이 사라진다 — 끝날 때까지 둔다
            self.progress_text.set_text("설치가 끝날 때까지 기다려 주세요…")
            return True
        return False

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape and not self.busy:
            self.close()
            return True
        return False


class AppInstaller(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self._css = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        self._css = _load_css(a)

    def do_open(self, files, _n, _hint):
        for f in files:
            path = f.get_path()
            if not path:
                continue
            path = os.path.abspath(path)
            win = next((w for w in self.get_windows() if getattr(w, "path", None) == path), None)
            (win or InstallWindow(self, path)).present()

    def do_activate(self):
        # 파일 없이 열었다 — 설치 파일을 고르게
        d = Gtk.FileChooserNative.new("설치 파일 열기", None, Gtk.FileChooserAction.OPEN, "열기", "취소")
        flt = Gtk.FileFilter()
        flt.set_name("설치 파일 (.deb)")
        flt.add_pattern("*.deb")
        flt.add_mime_type("application/vnd.debian.binary-package")
        d.add_filter(flt)
        self.hold()
        if d.run() == Gtk.ResponseType.ACCEPT:
            self.do_open([d.get_file()], 1, "")
        self.release()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print("사용법: sekai-appinstall [파일.deb …]")
        return 0
    GLib.set_prgname(APP_ID)
    return AppInstaller().run([sys.argv[0]] + list(argv))
