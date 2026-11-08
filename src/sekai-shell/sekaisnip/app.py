"""SekaiOS 캡처 도구 — 앱 (창 하나 · 방식·지연 기억 · 색 따라가기).

실행: sekai-snip [--new | --record]
    --new     창을 띄우자마자 새 캡처 (이미 떠 있으면 그 창에서)
    --record  영역 녹화를 시작한다 — 녹화 중이면 멈춘다 (Win+Shift+R)

찍는 일은 sekai-screenshot 이 한다 (--quiet: 알림 없이 저장 경로만 돌려준다). 단축키 캡처와 같은 곳(사진/스크린샷)에
저장되고 클립보드에도 들어간다. 이 앱은 방식(사각형 영역·창·전체 화면)과 지연(없음·3·5·10초)을 고르고, 찍은 그림을
보여 주고 복사·다른 이름으로 저장·폴더 열기·사진 앱에서 열기를 한다.
녹화(recorder.py)는 wf-recorder 로 동영상/화면 녹화 폴더에 MP4 — 범위는 캡처와 같고, 소리는 시스템 소리·마이크.
방식·지연·캡처/녹화·소리는 ~/.local/state/sekai/snip.json.
"""
import os
import shutil
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import theme  # noqa: E402
from sekaishell.appkit import AppTheme, JsonState, ToastMixin, appearance, shot_when_asked  # noqa: E402

from .recorder import Recorder  # noqa: E402

APP_ID = "org.sekaios.Snip"
STATE = os.path.expanduser("~/.local/state/sekai/snip.json")
MODES = [("region", "사각형 영역"), ("window", "창"), ("full", "전체 화면")]
DELAYS = [(0, "지연 없음"), (3, "3초 지연"), (5, "5초 지연"), (10, "10초 지연")]
HIDE_MS = 350                     # 창이 화면에서 걷히는(닫힘 효과) 시간 — 이만큼은 늘 기다린 뒤 찍는다
MAX_W, MAX_H = 960, 640           # 미리 보기 최대 크기 (창이 화면보다 커지지 않게)

SNIP_CSS = """
.snip-window { background: @surface; color: @fg; }
.snip-bar { padding: 8px 10px; border-bottom: 1px solid @line; }
button.snip-new { background-image: none; background-color: @accent; border: none; border-radius: 6px;
                  padding: 6px 14px; box-shadow: none; }
button.snip-new label, button.snip-new image { color: @c_on_accent; font-weight: 600; }
button.snip-new:hover { background-color: mix(@accent, @surface, 0.12); }
button.snip-act { background-image: none; background: transparent; border: none; border-radius: 6px;
                  padding: 6px 8px; box-shadow: none; }
button.snip-act:hover { background: alpha(@fg, 0.08); }
button.snip-act:disabled image { opacity: 0.35; }
.snip-view { background: mix(@surface, #000000, 0.18); }
label.snip-hint { color: @text2; padding: 28px 36px; }
label.snip-info { color: @text2; font-size: 9pt; padding: 4px 12px; }
.toast-line { background: alpha(@fg, 0.9); color: @surface; border-radius: 6px; padding: 6px 12px; margin: 8px; }
.snip-kind { background: alpha(@fg, 0.06); border-radius: 7px; padding: 2px; }
.snip-kind button { background-image: none; background: transparent; border: none; border-radius: 5px;
                    padding: 4px 10px; box-shadow: none; min-height: 0; }
.snip-kind button:checked { background: @surface; box-shadow: 0 1px 2px alpha(#000000, 0.25); }
.snip-kind button label { color: @fg; }
image.snip-video { color: @text2; padding: 26px 0 6px 0; }
/* 녹화 — 카운트다운 · 녹화 중 막대 (레이어 창) */
window.rec-countdown { background: alpha(#101012, 0.78); border-radius: 18px; }
window.rec-countdown box { padding: 18px 40px; }
label.rec-count-num { font-size: 46pt; font-weight: 700; color: #ffffff; }
label.rec-count-sub { color: alpha(#ffffff, 0.8); font-size: 10pt; }
window.rec-bar { background: alpha(@surface, 0.96); border: 1px solid @line; border-radius: 10px; }
window.rec-bar > box { padding: 4px 4px 4px 14px; }
label.rec-dot { color: #e5484d; }
label.rec-time { color: @fg; font-weight: 600; min-width: 42px; }
button.rec-stop { background-image: none; background: alpha(#e5484d, 0.16); border: none; border-radius: 7px;
                  padding: 4px 12px; box-shadow: none; }
button.rec-stop:hover { background: alpha(#e5484d, 0.28); }
button.rec-stop label, button.rec-stop image { color: @fg; font-weight: 600; }
"""


def _on_accent(hexcolor):
    h = hexcolor.lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#000000"
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return "#000000" if 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] > 0.28 else "#ffffff"


_THEME = AppTheme("sekai-snip", SNIP_CSS, prelude=lambda a: f"@define-color c_on_accent {_on_accent(a['accent'])};\n")
_STATE = JsonState(STATE, "캡처 도구 상태")


def _icon_button(icon, tip, cb, cls="snip-act"):
    b = Gtk.Button()
    b.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
    b.set_tooltip_text(tip)
    b.get_style_context().add_class(cls)
    b.connect("clicked", lambda *_: cb())
    return b


class SnipWindow(ToastMixin, Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="캡처 도구")
        self.app = app
        self.file = None
        self.is_video = False
        self.busy = False
        self.rec = None
        self.set_icon_name("applets-screenshooter")
        self.get_style_context().add_class("snip-window")
        self.set_default_size(560, 1)
        self.set_resizable(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        bar = Gtk.Box(spacing=6)
        bar.get_style_context().add_class("snip-bar")
        outer.pack_start(bar, False, False, 0)

        st = app.state
        # 캡처 | 녹화 (윈도우 캡처 도구처럼)
        kind = Gtk.Box()
        kind.get_style_context().add_class("snip-kind")
        self.k_shot = Gtk.RadioButton.new_with_label(None, "캡처")
        self.k_rec = Gtk.RadioButton.new_with_label_from_widget(self.k_shot, "녹화")
        for b in (self.k_shot, self.k_rec):
            b.set_mode(False)                     # 동그라미 없이 단추 모양
            kind.pack_start(b, False, False, 0)
        self.k_rec.set_active(st.get("kind") == "record")
        self.k_shot.set_tooltip_text("화면 캡처 (그림)")
        self.k_rec.set_tooltip_text("화면 녹화 (동영상) — Win+Shift+R")
        bar.pack_start(kind, False, False, 0)

        new = Gtk.Button()
        nb = Gtk.Box(spacing=6)
        self.new_icon = Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        self.new_label = Gtk.Label(label="새 캡처")
        nb.pack_start(self.new_icon, False, False, 0)
        nb.pack_start(self.new_label, False, False, 0)
        new.add(nb)
        new.get_style_context().add_class("snip-new")
        new.connect("clicked", lambda *_: self.new_capture())
        self.new_btn = new
        bar.pack_start(new, False, False, 0)

        self.mode = Gtk.ComboBoxText()
        for k, label in MODES:
            self.mode.append(k, label)
        if not self.mode.set_active_id(st.get("mode", "region")):
            self.mode.set_active(0)
        self.mode.set_tooltip_text("캡처 방식")
        self.delay = Gtk.ComboBoxText()
        for k, label in DELAYS:
            self.delay.append(str(k), label)
        if not self.delay.set_active_id(str(st.get("delay", 0))):
            self.delay.set_active(0)
        self.delay.set_tooltip_text("찍기 전에 기다릴 시간 — 메뉴처럼 열어 둬야 하는 것을 찍을 때")
        for c in (self.mode, self.delay):
            c.connect("changed", lambda *_: self._remember())
            bar.pack_start(c, False, False, 0)
        self.a_sys = Gtk.CheckButton(label="시스템 소리")
        self.a_mic = Gtk.CheckButton(label="마이크")
        self.a_sys.set_active(st.get("rec_system", True))
        self.a_mic.set_active(st.get("rec_mic", False))
        self.a_sys.set_tooltip_text("컴퓨터에서 나는 소리를 같이 녹음합니다")
        self.a_mic.set_tooltip_text("마이크 소리를 같이 녹음합니다")
        for c in (self.a_sys, self.a_mic):
            c.connect("toggled", lambda *_: self._remember())
            c.set_no_show_all(True)
            bar.pack_start(c, False, False, 0)
        for b in (self.k_shot, self.k_rec):
            b.connect("toggled", lambda *_: self._kind_changed())

        self.acts = []
        for icon, tip, cb in (("folder-open-symbolic", "폴더 열기", self.open_folder),
                              ("image-x-generic-symbolic", "사진 앱에서 열기", self.open_photos),
                              ("document-save-as-symbolic", "다른 이름으로 저장 (Ctrl+S)", self.save_as),
                              ("edit-copy-symbolic", "복사 (Ctrl+C)", self.copy)):
            b = _icon_button(icon, tip, cb)
            b.set_sensitive(False)
            bar.pack_end(b, False, False, 0)
            self.acts.append(b)
        self.open_btn = self.acts[1]

        overlay = Gtk.Overlay()
        outer.pack_start(overlay, True, True, 0)
        self.view = Gtk.ScrolledWindow()
        self.view.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.view.get_style_context().add_class("snip-view")
        overlay.add(self.view)
        self.hint = Gtk.Label(justify=Gtk.Justification.CENTER)
        self.hint.get_style_context().add_class("snip-hint")
        self.view.add(self.hint)
        self.view.set_min_content_height(110)          # 안내 글이 보이게 (그림을 찍으면 그림 크기로)
        self.image = Gtk.Image()
        self.video = Gtk.Image.new_from_icon_name("video-x-generic", Gtk.IconSize.DIALOG)
        self.video.set_pixel_size(96)
        self.video.get_style_context().add_class("snip-video")
        self.info = Gtk.Label(xalign=0)
        self.info.get_style_context().add_class("snip-info")
        self.info.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        outer.pack_start(self.info, False, False, 0)

        self.toast_label = Gtk.Label()
        self.toast_label.get_style_context().add_class("toast-line")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.END)
        self.toast_label.set_no_show_all(True)
        overlay.add_overlay(self.toast_label)
        self._toast_src = 0

        keys = Gtk.AccelGroup()
        self.add_accel_group(keys)
        for key, cb in (("<Primary>n", self.new_capture), ("<Primary>c", self.copy), ("<Primary>s", self.save_as)):
            k, m = Gtk.accelerator_parse(key)
            keys.connect(k, m, 0, lambda *_a, f=cb: (f(), True)[1])
        self._kind_changed(save=False)

    # ── 찍기 ──────────────────────────────────────────────
    def _remember(self):
        self.app.state.update(mode=self.mode.get_active_id() or "region",
                              delay=int(self.delay.get_active_id() or 0),
                              kind="record" if self.k_rec.get_active() else "capture",
                              rec_system=self.a_sys.get_active(), rec_mic=self.a_mic.get_active())
        _STATE.save(self.app.state)

    def _sync_kind(self):
        """캡처/녹화에 따라 보일 것 — show_all 뒤에도 다시 (안 그러면 숨긴 것이 도로 보였다)"""
        rec = self.k_rec.get_active()
        self.delay.set_visible(not rec)
        self.a_sys.set_visible(rec)
        self.a_mic.set_visible(rec)

    def _kind_changed(self, save=True):
        rec = self.k_rec.get_active()
        self.new_icon.set_from_icon_name("media-record-symbolic" if rec else "list-add-symbolic", Gtk.IconSize.BUTTON)
        self.new_label.set_text("새 녹화" if rec else "새 캡처")
        self.new_btn.set_tooltip_text("새 녹화 — 끝내려면 [중지] 또는 Win+Shift+R" if rec else "새 캡처 (Ctrl+N)")
        self._sync_kind()
        self.mode.set_tooltip_text("녹화 범위" if rec else "캡처 방식")
        self.hint.set_text("[새 녹화] 를 눌러 화면을 녹화하세요. 3초 뒤 시작합니다.\n"
                           "단축키: Win+Shift+R 영역 녹화 시작·중지 · 동영상/화면 녹화 폴더에 저장"
                           if rec else
                           "[새 캡처] 를 눌러 화면을 찍으세요.\n"
                           "단축키: Win+Shift+S 영역 · Print 전체 화면 · Alt+Print 지금 창")
        if save:
            self._remember()

    def new_capture(self):
        if self.k_rec.get_active():
            self.new_recording()
            return
        if self.busy:
            return
        self.busy = True
        mode = self.mode.get_active_id() or "region"
        wait = HIDE_MS + int(self.delay.get_active_id() or 0) * 1000
        self.hide()                       # 이 창이 찍히지 않게 (창 방식이면 그 아래 지금 창이 찍힌다)
        GLib.timeout_add(wait, self._run, mode)

    def _run(self, mode):
        try:
            p = Gio.Subprocess.new(["sekai-screenshot", mode, "--quiet"],
                                   Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            self._done(None, f"캡처를 시작하지 못했습니다: {e.message}")
            return False
        p.communicate_utf8_async(None, None, self._finished)
        return False

    def _finished(self, p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = ""
        path = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        self._done(path if path and os.path.isfile(path) else None, None)

    def _done(self, path, err):
        self.busy = False
        self.present()
        if err:
            self.toast(err)
            return
        if path is None:
            return                        # 영역 고르기를 Esc 로 취소했다 — 전 그림 그대로
        self.show_file(path)
        self.toast("클립보드에 복사했고 스크린샷 폴더에 저장했습니다")

    # ── 녹화 ──────────────────────────────────────────────
    def new_recording(self, mode=None):
        if self.rec is not None and self.rec.active:
            self.rec.stop()
            return
        if self.busy:
            return
        if not os.environ.get("WAYLAND_DISPLAY"):
            # 기본 화면 모드(X11, 그래픽 드라이버 없음) — wf-recorder 는 웨일랜드 합성기에서만 된다
            if self.app.hidden_start:
                self.app.hidden_start = False
                self.app.release()
            self.present()
            self.toast("기본 화면 모드에서는 화면 녹화를 쓸 수 없습니다")
            return
        self.busy = True
        self.rec = Recorder(self._rec_state)
        mode = mode or self.mode.get_active_id() or "region"
        self.hide()
        GLib.timeout_add(HIDE_MS, lambda: (self.rec.begin(mode, self.a_sys.get_active(), self.a_mic.get_active()),
                                           False)[1])

    def stop_recording(self):
        if self.rec is not None and self.rec.active:
            self.rec.stop()
            return True
        return False

    def _rec_state(self, kind, value):
        if kind == "started":
            return
        self.busy = False
        if kind == "cancelled":
            if self.app.hidden_start:
                self.app.quit()               # 단축키로 시작했다가 Esc — 창 없이 끝낸다
            else:
                self.present()
            return
        if self.app.hidden_start:
            self.app.hidden_start = False
            self.app.release()                # 이제 창이 있으니 창을 닫으면 끝나게
        self.present()
        if kind == "error":
            self.toast(value)
            return
        self.show_video(value, self.rec.duration if self.rec else 0)
        self.toast("동영상/화면 녹화 폴더에 저장했습니다")

    def show_video(self, path, secs):
        self.file, self.is_video = path, True
        child = self.view.get_child()
        if child is not None:
            self.view.remove(child)
        self.view.add(self.video)
        self.view.set_min_content_width(360)
        self.view.set_min_content_height(140)
        mb = os.path.getsize(path) / 1e6 if os.path.exists(path) else 0
        self.info.set_text(f"{os.path.basename(path)} · {int(secs) // 60}:{int(secs) % 60:02d} · {mb:.1f} MB")
        for b in self.acts:
            b.set_sensitive(True)
        self.open_btn.set_tooltip_text("재생")
        self.open_btn.get_child().set_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)
        self.show_all()
        self._sync_kind()
        self.resize(1, 1)

    def show_file(self, path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error as e:
            self.toast(f"그림을 열지 못했습니다: {e.message}")
            return
        self.file, self.is_video = path, False
        self.open_btn.set_tooltip_text("사진 앱에서 열기")
        self.open_btn.get_child().set_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.BUTTON)
        w, h = pb.get_width(), pb.get_height()
        scale = min(1.0, MAX_W / w, MAX_H / h)
        shown = pb if scale >= 1.0 else pb.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)),
                                                         GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(shown)
        child = self.view.get_child()
        if child is not None and child is not self.image:
            self.view.remove(child)
        if self.image.get_parent() is None:
            self.view.add(self.image)
        self.view.set_min_content_width(min(MAX_W, shown.get_width()))
        self.view.set_min_content_height(min(MAX_H, shown.get_height()))
        self.info.set_text(f"{os.path.basename(path)} · {w}×{h}")
        for b in self.acts:
            b.set_sensitive(True)
        self.show_all()
        self._sync_kind()
        self.resize(1, 1)                 # 그림 크기에 맞춰 줄이기도

    # ── 찍은 뒤 ────────────────────────────────────────────
    def copy(self):
        if not self.file:
            return
        # 동영상은 파일로 (탐색기·메신저에 붙여 넣기) — 그림은 그림으로
        mime = "text/uri-list" if self.is_video else "image/png"
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            cmd = ["wl-copy", "--type", mime]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard", "-t", mime, "-i"]
        else:
            self.toast("클립보드 도구가 없습니다")
            return
        try:
            if self.is_video:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                p.stdin.write((Gio.File.new_for_path(self.file).get_uri() + "\r\n").encode())
                p.stdin.close()
                self.toast("동영상 파일을 복사했습니다")
                return
            with open(self.file, "rb") as f:
                # 붙여 넣을 때까지 뒤에 남아 클립보드를 지킨다 — 이 앱을 닫아도 남게 떼어 낸다
                subprocess.Popen(cmd, stdin=f, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            self.toast("클립보드에 복사했습니다")
        except OSError as e:
            self.toast(f"복사하지 못했습니다: {e}")

    def save_as(self):
        if not self.file:
            return
        d = Gtk.FileChooserNative.new("다른 이름으로 저장", self, Gtk.FileChooserAction.SAVE, "저장", "취소")
        d.set_do_overwrite_confirmation(True)
        d.set_current_folder(os.path.dirname(self.file))
        d.set_current_name(os.path.basename(self.file))
        kinds = (("MP4 동영상", ("*.mp4",)),) if self.is_video else (("PNG 그림", ("*.png",)), ("JPEG 그림", ("*.jpg", "*.jpeg")))
        for name, pats in kinds:
            flt = Gtk.FileFilter()
            flt.set_name(name)
            for pat in pats:
                flt.add_pattern(pat)
            d.add_filter(flt)
        if d.run() != Gtk.ResponseType.ACCEPT:
            return
        dest = d.get_filename()
        try:
            if self.is_video:
                if not dest.lower().endswith(".mp4"):
                    dest += ".mp4"
                shutil.copyfile(self.file, dest)
            elif dest.lower().endswith((".jpg", ".jpeg")):
                GdkPixbuf.Pixbuf.new_from_file(self.file).savev(dest, "jpeg", ["quality"], ["92"])
            else:
                if not dest.lower().endswith(".png"):
                    dest += ".png"
                shutil.copyfile(self.file, dest)
            self.toast(f"저장했습니다: {os.path.basename(dest)}")
        except (OSError, GLib.Error) as e:
            self.toast(f"저장하지 못했습니다: {getattr(e, 'message', e)}")

    def _spawn(self, cmd):
        try:
            subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False

    def open_folder(self):
        if self.file and not self._spawn(["sekai-files", "--select", self.file]):
            self._spawn(["xdg-open", os.path.dirname(self.file)])

    def open_photos(self):
        if not self.file:
            return
        if self.is_video:
            # 동영상 앱이 없으면(기본 설치엔 없다) 크로미움이 재생한다
            try:
                app = subprocess.run(["xdg-mime", "query", "default", "video/mp4"], capture_output=True,
                                     text=True, timeout=3).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                app = ""
            if app or not shutil.which("chromium"):
                self._spawn(["xdg-open", self.file])
            else:
                self._spawn(["chromium", Gio.File.new_for_path(self.file).get_uri()])
            return
        if not self._spawn(["sekai-photos", self.file]):
            self._spawn(["xdg-open", self.file])


class SnipApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.state = {}
        self.win = None
        self.hidden_start = False         # 창을 띄우지 않고 단축키로 녹화를 시작했다

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("캡처 도구")
        self.state = _STATE.load()
        a = appearance()
        theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
        _THEME.load(a)
        _THEME.follow()

    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        for a in args:
            if a not in ("--new", "--record"):
                print(f"[sekai-snip] 모르는 옵션: {a}", file=sys.stderr)
        if "--record" in args:
            # Win+Shift+R — 녹화 중이면 멈추고, 아니면 창 없이 영역 녹화를 시작한다 (끝나면 창에 결과)
            if self.win is not None and self.win.stop_recording():
                return 0
            if self.win is None:
                self.hidden_start = True
                self.win = SnipWindow(self)
                self.win.connect("destroy", lambda *_: setattr(self, "win", None))
                self.hold()                   # 창이 없는 동안에도 끝나지 않게
                GLib.idle_add(lambda: (self.win.new_recording("region"), False)[1])
                return 0
            self.win.new_recording("region")
            return 0
        if self.win is None:
            self.win = SnipWindow(self)
            self.win.connect("destroy", lambda *_: setattr(self, "win", None))
            self.win.show_all()
            shot_when_asked(self.win, self.quit)
        if "--new" in args:
            self.win.new_capture()
        else:
            self.win.present()
        return 0


def main(argv=None):
    GLib.set_prgname(APP_ID)          # 웨일랜드 app_id — 작업 표시줄이 시작 메뉴 항목(StartupWMClass)과 짝짓는다
    return SnipApp().run([sys.argv[0]] + list(argv if argv is not None else sys.argv[1:]))
