"""보안 창 공용 조각 — 관리자 권한 확인(사용자 계정 컨트롤, lib/polkit-agent)과
네트워크 암호 창(lib/nm-agent)이 함께 쓴다.

lxpolkit·nm-applet 의 창은 다른 데스크톱의 것이라 모양·말투가 우리와 달랐다 (영어 문구,
"신원:" 콤보, 최소화·최대화 단추, 작업 표시줄 항목). 여기서 윈도우 11 처럼 만든다.
  · 색은 설정 앱과 같은 settings.css (+ settings.json 의 강조색·모드). 창을 열 때마다 다시 읽는다 —
    잠깐 떴다 닫히는 창이라 설정 파일을 따로 감시하지 않아도 늘 지금 모드·강조색으로 뜬다.
  · Hyprland: 카드는 OVERLAY 층 layer-shell 표면 (모서리를 고정하지 않아 가운데, 모니터를 정하지 않아
    컴포지터가 사용자가 쓰던 모니터에 둔다). 사용자 계정 컨트롤은 모든 모니터를 어둡게 덮는다 (Dim).
    이름표(namespace)로 hyprland.conf 의 layerrule(나타나는 효과·흐림)을 건다.
  · 기본 화면 모드(X11, layer-shell 없음): 테두리 없는 항상 위 창, 작업 표시줄에 보이지 않는다.
메인 스레드를 막는 일은 하지 않는다 (파일은 작은 것만 읽는다).
"""
import math
import os
import pwd
import re
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import theme  # noqa: E402
from .layer import WAYLAND, GtkLayerShell  # noqa: E402
from .taskmgr_common import appearance  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATHS = [os.path.join(HERE, "..", "settings.css"), "/usr/share/sekai-shell/settings.css"]

# settings.css(색 이름 @accent @fg @card @winbg @line @text2 … 과 기본 컨트롤 모양) 위에 얹는 보안 창의 모양
AUTH_CSS = """
@define-color auth_err  mix(#e0453a, @fg, 0.25);
@define-color auth_warn mix(#d69a00, @fg, 0.20);
window.auth-win { background: transparent; }
/* 사용자 계정 컨트롤 뒤의 어두운 막 */
window.auth-dim { background: alpha(#000000, 0.50); }

/* 카드 — 둘레에 그림자 자리(margin)를 남긴다. 투명한 창 안에 둥근 카드만 보인다 */
.auth-card {
    background: @winbg;
    border: 1px solid alpha(@fg, 0.14);
    border-radius: 8px;
    margin: 28px;
    box-shadow: 0 10px 34px alpha(#000000, 0.50), 0 0 0 1px alpha(#000000, 0.25);
    transition: border-color 90ms ease;
}
/* 바깥(어두운 막)을 누르면 카드가 깜박인다 — 윈도우처럼 "여기서 먼저 답하세요" */
.auth-card.auth-attn { border-color: @accent; }

/* 맨 위 강조색 띠 ("사용자 계정 컨트롤") — GTK3 는 자식을 둥근 모서리로 자르지 않으므로 띠에도 모서리를 준다 */
.auth-head {
    background: @accent;
    border-radius: 7px 7px 0 0;
    padding: 9px 20px;
}
.auth-head label { color: @on_accent; font-size: 12px; font-weight: 600; }
.auth-head image { color: @on_accent; }

.auth-body { padding: 18px 24px 14px 24px; }
label.auth-question { font-size: 20px; font-weight: 600; color: @fg; }
label.auth-title    { font-size: 17px; font-weight: 600; color: @fg; }
label.auth-appname  { font-size: 15px; font-weight: 600; color: @fg; }
label.auth-sub      { font-size: 12px; color: @text2; }
label.auth-text     { font-size: 13px; color: @fg; }
label.auth-key      { font-size: 12px; color: @text2; }
label.auth-val      { font-size: 12px; color: @fg; }
label.auth-unknown  { color: @auth_warn; }
label.auth-msg      { font-size: 12px; color: @auth_err; }
label.auth-msg.info { color: @text2; }
label.auth-msg.warn { color: @auth_warn; }

/* "자세한 내용 표시" — 윈도우처럼 글자 단추 */
button.auth-link {
    background: transparent;
    border-color: transparent;
    padding: 2px 4px;
    margin-left: -4px;
}
button.auth-link label { color: mix(@accent, @fg, 0.25); font-size: 12px; }
button.auth-link:hover { background: @hover; }
.auth-details {
    background: @card;
    border: 1px solid @line;
    border-radius: 6px;
    padding: 10px 12px;
}

/* 계정 칸 (사진·이름) */
list.auth-accounts, list.auth-accounts row { background: transparent; }
list.auth-accounts row {
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 8px;
    margin-bottom: 2px;
}
list.auth-accounts row:hover { background: @hover; }
list.auth-accounts row:selected {
    background: alpha(@accent, 0.14);
    border-color: alpha(@accent, 0.55);
}
list.auth-accounts row:selected label { color: @fg; }
list.auth-accounts.single row, list.auth-accounts.single row:hover, list.auth-accounts.single row:selected {
    background: transparent;
    border-color: transparent;
}

/* 암호 칸 — 카드(@winbg) 위에서 보이게 한 단계 밝게, 초점이면 아래 선이 강조색 */
.auth-card entry {
    background: @card;
    padding: 7px 10px;
    min-height: 20px;
}
.auth-card entry:focus { background: @winbg; border-bottom-color: @accent; }
.auth-card entry image { color: @text2; }
.auth-card entry image:hover { color: @fg; }

/* 아래 단추 줄 — 윈도우 사용자 계정 컨트롤처럼 한 단계 다른 면 위에 같은 너비 단추 둘 */
.auth-foot {
    background: @card;
    border-top: 1px solid @line;
    border-radius: 0 0 7px 7px;
    padding: 16px 24px;
}
.auth-foot button { padding: 7px 16px; min-height: 18px; }
.auth-foot button.accent-btn:disabled { background: alpha(@accent, 0.45); border-color: transparent; }
.auth-foot button.accent-btn:disabled label { color: alpha(@on_accent, 0.7); }
"""

_prov = None


def load_style(extra=""):
    """모드·강조색을 새로 읽어 모양을 바꿔 끼운다 (창을 열 때마다). 읽은 appearance 를 돌려준다"""
    global _prov
    a = appearance()
    prelude = "".join(f"@define-color {k} {a[k]};\n" for k in ("accent", "bg", "surface", "fg"))
    body = ""
    for p in CSS_PATHS:
        try:
            with open(p, encoding="utf-8") as f:
                body = f.read()
            break
        except OSError:
            continue
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((prelude + body + AUTH_CSS + extra).encode())
    except GLib.Error as e:
        print("[sekai-auth] CSS 오류:", e.message, file=sys.stderr, flush=True)
        prov = None
    scr = Gdk.Screen.get_default()
    if prov is not None:
        if _prov is not None:
            Gtk.StyleContext.remove_provider_for_screen(scr, _prov)
        Gtk.StyleContext.add_provider_for_screen(scr, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        _prov = prov
    theme.apply_gtk_settings(Gtk.Settings.get_default(), a["mode"])
    return a


def use_layer():
    """layer-shell 로 띄울 수 있나 (Hyprland 등). 아니면 보통 창 (기본 화면 모드 X11)"""
    return WAYLAND and GtkLayerShell.is_supported()


_WJ = "\u2060"                                        # WORD JOINER — 보이지 않고, 그 자리에서 줄을 바꾸지 않는다
_BREAKABLE = re.compile(r"(?<=[\uac00-\ud7a3])(?=[\uac00-\ud7a3?!.,)])")


def keep_words(text):
    """한글 낱말 가운데서 줄이 바뀌지 않게 ("허용하/시겠어요?" → 띄어쓰기에서만 바뀐다 — 윈도우처럼).
    Pango 는 한글 음절 사이 어디서나 줄을 바꾼다. 음절 사이에 WORD JOINER 를 넣어 막는다"""
    return _BREAKABLE.sub(_WJ, text or "")


def label(text="", cls=None, wrap=True, xalign=0.0, selectable=False):
    if wrap and not selectable:
        text = keep_words(text)                           # 고를 수 있는 글자는 복사할 때 섞이지 않게 그대로
    lb = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # 자연 너비를 한 글자로 — 긴 글이 카드를 넓히지 않고 카드 너비에 맞춰 줄을 바꾼다
        lb.set_max_width_chars(1)
        lb.set_hexpand(True)
    if selectable:
        lb.set_selectable(True)
        lb.set_can_focus(False)            # Tab 이 글자로 가지 않게 (끌어서 고르기는 된다)
    for c in ([cls] if isinstance(cls, str) else (cls or [])):
        lb.get_style_context().add_class(c)
    return lb


def icon(names, size):
    """후보 중 테마에 있는 첫 아이콘 (이름이나 파일 경로)"""
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if not n:
            continue
        if os.path.isabs(n) and os.path.isfile(n):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(n, size, size)
                return Gtk.Image.new_from_pixbuf(pb)
            except GLib.Error:
                continue
        if th.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n, Gtk.IconSize.DIALOG)
            img.set_pixel_size(size)
            return img
    img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    return img


def color(widget, name, fallback=(0.22, 0.77, 0.73, 1.0)):
    ok, c = widget.get_style_context().lookup_color(name)
    return (c.red, c.green, c.blue, c.alpha) if ok else fallback


# ── 계정 ─────────────────────────────────────────────────────
def account(uid):
    """uid → {"uid", "name"(로그인 이름), "display"(보일 이름 — GECOS 첫 칸), "face"(사진 경로 또는 None)}"""
    try:
        p = pwd.getpwuid(int(uid))
    except (KeyError, ValueError, TypeError):
        return {"uid": uid, "name": str(uid), "display": str(uid), "face": None}
    gecos = (p.pw_gecos or "").split(",")[0].strip()
    display = gecos or p.pw_name
    if p.pw_uid == 0 and display == "root":
        display = "관리자 (root)"
    face = None
    # AccountsService 사진은 누구나 읽을 수 있다. 남의 ~/.face 는 보통 못 읽는다 (읽을 수 있을 때만)
    for cand in (f"/var/lib/AccountsService/icons/{p.pw_name}",
                 os.path.join(p.pw_dir or "/nonexistent", ".face"),
                 os.path.join(p.pw_dir or "/nonexistent", ".face.icon")):
        try:
            if os.path.isfile(cand) and os.access(cand, os.R_OK) and os.path.getsize(cand) < 8 * 1024 * 1024:
                face = cand
                break
        except OSError:
            continue
    return {"uid": p.pw_uid, "name": p.pw_name, "display": display, "face": face}


class Avatar(Gtk.DrawingArea):
    """계정 사진 (둥글게 자른다). 사진이 없으면 강조색 원에 이름 첫 글자 — 윈도우의 머리글자 사진처럼"""

    def __init__(self, acct, size=40):
        super().__init__()
        self.size = size
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.text = (acct.get("display") or acct.get("name") or "?")[:1].upper()
        self.pix = None
        if acct.get("face"):
            try:
                # 두 배로 읽어 두면 배율 2 화면에서도 선명하다
                self.pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(acct["face"], size * 2, size * 2, True)
            except GLib.Error:
                self.pix = None
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        s = self.size
        a = w.get_allocation()
        x0, y0 = (a.width - s) / 2, (a.height - s) / 2
        cr.arc(x0 + s / 2, y0 + s / 2, s / 2, 0, 2 * math.pi)
        if self.pix is not None:
            cr.save()
            cr.clip()
            pw, ph = self.pix.get_width(), self.pix.get_height()
            k = s / max(1, min(pw, ph))
            cr.translate(x0 + (s - pw * k) / 2, y0 + (s - ph * k) / 2)
            cr.scale(k, k)
            Gdk.cairo_set_source_pixbuf(cr, self.pix, 0, 0)
            cr.paint()
            cr.restore()
            return False
        r, g, b, _a = color(w, "accent")
        cr.set_source_rgb(r, g, b)
        cr.fill()
        layout = w.create_pango_layout(self.text)
        fd = Pango.FontDescription.from_string("Pretendard, NanumGothic, sans-serif Bold")
        fd.set_absolute_size(s * 0.42 * Pango.SCALE)
        layout.set_font_description(fd)
        tw, th = layout.get_pixel_size()
        r, g, b, _a = color(w, "on_accent", (0.05, 0.12, 0.12, 1.0))
        cr.set_source_rgb(r, g, b)
        cr.move_to(x0 + (s - tw) / 2, y0 + (s - th) / 2)
        PangoCairo.show_layout(cr, layout)
        return False


def account_tile(acct, sub=None, size=40):
    """사진 + 이름 + 작은 글자 한 줄 (계정 목록의 한 칸)"""
    h = Gtk.Box(spacing=12)
    h.pack_start(Avatar(acct, size), False, False, 0)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    v.set_valign(Gtk.Align.CENTER)
    v.pack_start(label(acct["display"], "auth-appname", wrap=False), False, False, 0)
    if sub:
        v.pack_start(label(sub, "auth-sub", wrap=False), False, False, 0)
    h.pack_start(v, True, True, 0)
    return h


# ── 암호 칸 ──────────────────────────────────────────────────
class PasswordEntry(Gtk.Entry):
    """가려진 암호 칸 + 보기 단추 (윈도우처럼 누르고 있는 동안만 보이고, 글자가 있을 때만 나타난다.
    Alt+F8 은 보이기·가리기를 바꾼다). Enter 는 창의 기본 단추를 누른다."""

    REVEAL = "view-reveal-symbolic"

    def __init__(self, placeholder="암호", secret=True):
        super().__init__()
        self.secret = secret
        self.set_visibility(not secret)
        self.set_invisible_char("●")
        self.set_placeholder_text(placeholder)
        self.set_activates_default(True)
        self.set_hexpand(True)
        # Caps Lock 은 칸 아래 글(CapsWarning)로 알린다 — GTK 가 칸 안에 따로 그리는 표시는 끈다
        self.set_property("caps-lock-warning", False)
        if secret:
            # 암호 입력 — 입력기(한글 모드)가 끼어들지 않고 맞춤법 검사도 하지 않는다
            self.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            self.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
            self.connect("changed", self._sync_icon)
            self.connect("icon-press", lambda *_: self.set_visibility(True))
            self.connect("icon-release", lambda *_: self.set_visibility(False))
            self.connect("key-press-event", self._key)
            self.connect("focus-out-event", lambda *_: self.set_visibility(False))
        acc = self.get_accessible()
        if acc is not None:
            acc.set_name(placeholder)

    def _sync_icon(self, *_):
        want = self.REVEAL if self.get_text() and Gtk.IconTheme.get_default().has_icon(self.REVEAL) else None
        if self.get_icon_name(Gtk.EntryIconPosition.SECONDARY) != want:
            self.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, want)
            if want:
                self.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "누르고 있으면 암호가 보입니다")
        if not self.get_text():
            self.set_visibility(False)

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_F8 and ev.state & Gdk.ModifierType.MOD1_MASK:
            self.set_visibility(not self.get_visibility())
            return True
        return False

    def clear(self):
        self.set_text("")
        self.set_visibility(not self.secret)


class BusyField(Gtk.Overlay):
    """칸 + 그 안 오른쪽에 뜨는 도는 표시 (확인하는 동안). 옆에 두면 칸이 좁아졌다 넓어지며 흔들린다"""

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.add(entry)
        self.spinner = Gtk.Spinner()
        self.spinner.set_halign(Gtk.Align.END)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_margin_end(12)
        self.spinner.set_no_show_all(True)
        self.add_overlay(self.spinner)
        self.set_overlay_pass_through(self.spinner, True)

    def set_busy(self, on):
        e = self.entry
        e.set_sensitive(not on)
        if on:
            # 보기 단추와 겹치지 않게 잠깐 뺀다 (글자가 바뀌면 PasswordEntry 가 다시 붙인다)
            e.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()
            if isinstance(e, PasswordEntry):
                e._sync_icon()


class CapsWarning(Gtk.Label):
    """Caps Lock 이 켜져 있으면 보이는 한 줄 (지켜보는 칸에 초점이 있을 때만)"""

    def __init__(self, *entries):
        super().__init__(label="Caps Lock이 켜져 있습니다", xalign=0)
        self.get_style_context().add_class("auth-msg")
        self.get_style_context().add_class("warn")
        self.set_no_show_all(True)
        self.entries = entries
        self._km = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        self._hid = self._km.connect("state-changed", lambda *_: self.update())
        for e in entries:
            e.connect("focus-in-event", lambda *_: (self.update(), False)[1])
            e.connect("focus-out-event", lambda *_: (self.update(), False)[1])
        self.connect("destroy", lambda *_: self._km.disconnect(self._hid))

    def update(self):
        on = self._km.get_caps_lock_state() and any(e.has_focus() for e in self.entries)
        self.set_visible(bool(on))


class Message(Gtk.Label):
    """오류(빨강)·경고(노랑)·안내(회색) 한 줄. 비면 숨는다"""

    def __init__(self):
        super().__init__(xalign=0)
        self.set_line_wrap(True)
        self.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.set_max_width_chars(1)
        self.set_hexpand(True)
        self.get_style_context().add_class("auth-msg")
        self.set_no_show_all(True)

    def show_text(self, text, kind="error"):
        ctx = self.get_style_context()
        for k in ("info", "warn"):
            ctx.remove_class(k)
        if kind in ("info", "warn"):
            ctx.add_class(kind)
        self.set_text(keep_words(text))
        self.set_visible(bool(text))


# ── 창 ───────────────────────────────────────────────────────
class Dim(Gtk.Window):
    """한 모니터를 덮는 반투명 검은 막 (사용자 계정 컨트롤 뒤). 입력을 받아 뒤의 앱을 못 누르게 하고,
    누르면 on_click (카드 깜박임)."""

    def __init__(self, monitor, on_click=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_decorated(False)
        self.get_style_context().add_class("auth-dim")
        self.monitor = monitor
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "sekai-uac-dim")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for e in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                  GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, e, True)
        GtkLayerShell.set_exclusive_zone(self, -1)          # 작업 표시줄 자리까지 덮는다
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        if monitor is not None:
            GtkLayerShell.set_monitor(self, monitor)
        area = Gtk.EventBox()
        area.set_visible_window(False)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        if on_click:
            area.connect("button-press-event", lambda *_: (on_click(), True)[1])
        self.add(area)


class DimSet:
    """모든 모니터의 막 — 떠 있는 동안 모니터를 꽂거나 빼도 따라간다"""

    def __init__(self, on_click=None):
        self.on_click = on_click
        self.dims = []
        self.disp = Gdk.Display.get_default()
        for i in range(self.disp.get_n_monitors()):
            self._add(self.disp.get_monitor(i))
        self._hids = [self.disp.connect("monitor-added", lambda _d, m: self._add(m)),
                      self.disp.connect("monitor-removed", lambda _d, m: self._drop(m))]

    def _add(self, m):
        d = Dim(m, self.on_click)
        d.show_all()
        self.dims.append(d)

    def _drop(self, m):
        for d in [d for d in self.dims if d.monitor == m]:
            self.dims.remove(d)
            d.destroy()

    def destroy(self):
        for h in self._hids:
            self.disp.disconnect(h)
        self._hids = []
        for d in self.dims:
            d.destroy()
        self.dims = []


class CardWindow(Gtk.Window):
    """가운데 뜨는 카드 창. self.card 에 내용을 넣는다 (head · body · foot 는 부르는 쪽이 만든다).

    namespace  Hyprland 이름표 (layerrule)
    exclusive  True: 떠 있는 동안 키보드를 이 창만 받는다 (사용자 계정 컨트롤)
               False: 다른 창을 눌러 초점을 옮길 수 있다 (네트워크 암호 — 암호 관리자에서 복사해 올 수 있게)
    기본 화면 모드에선 exclusive 면 창이 뜬 뒤 키보드를 잡는다 (다른 창으로 새지 않게)"""

    def __init__(self, namespace, title, exclusive=True, width=456):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title(title)                               # 화면 읽기 프로그램이 읽는 창 이름
        self.exclusive = exclusive
        vis = Gdk.Screen.get_default().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_decorated(False)
        self.set_resizable(False)
        self.get_style_context().add_class("auth-win")
        self.layer = use_layer()
        if self.layer:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, namespace)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE if exclusive
                                            else GtkLayerShell.KeyboardMode.ON_DEMAND)
        else:
            # 기본 화면 모드: 제목줄·최소화·최대화 없이 항상 위, 작업 표시줄·창 전환에 나오지 않게
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
            self.stick()
            self.connect("map-event", self._x11_mapped)
        self._grab_seat = None
        self._grab_src = 0
        self._attn_src = 0
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.card.get_style_context().add_class("auth-card")
        self.card.set_size_request(width, -1)
        self.add(self.card)
        self.connect("destroy", lambda *_: self._cleanup())

    # ── 기본 화면 모드: 초점 가져오기 (+ 키보드 잡기) ──
    def _x11_mapped(self, *_):
        gw = self.get_window()
        if gw is None:
            return False
        try:
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            t = GdkX11.x11_get_server_time(gw)
        except (ValueError, ImportError, TypeError, AttributeError):
            t = Gtk.get_current_event_time()
        self.present_with_time(t)
        if self.exclusive:
            self._grab_tries = 0
            self._grab_src = GLib.timeout_add(100, self._try_grab)
        return False

    def _try_grab(self):
        """다른 창이 키보드를 잡고 있으면 잠깐 뒤에 다시 (창이 막 뜬 순간엔 실패할 때가 있다)"""
        gw = self.get_window()
        if gw is None or not self.get_visible():
            self._grab_src = 0
            return False
        seat = Gdk.Display.get_default().get_default_seat()
        st = seat.grab(gw, Gdk.SeatCapabilities.KEYBOARD, True, None, None, None, None)
        if st == Gdk.GrabStatus.SUCCESS:
            self._grab_seat = seat
            self._grab_src = 0
            return False
        self._grab_tries += 1
        if self._grab_tries > 20:
            self._grab_src = 0
            return False
        return True

    def _cleanup(self):
        for attr in ("_grab_src", "_attn_src"):
            src = getattr(self, attr)
            if src:
                GLib.source_remove(src)
                setattr(self, attr, 0)
        if self._grab_seat is not None:
            self._grab_seat.ungrab()
            self._grab_seat = None

    def attention(self):
        """카드 테두리를 세 번 깜박인다"""
        if self._attn_src:
            return
        ctx = self.card.get_style_context()
        n = [0]

        def step():
            n[0] += 1
            (ctx.add_class if n[0] % 2 else ctx.remove_class)("auth-attn")
            if n[0] >= 6:
                ctx.remove_class("auth-attn")
                self._attn_src = 0
                return False
            return True
        self._attn_src = GLib.timeout_add(110, step)
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.beep()


def head_strip(text, icon_names=("security-high-symbolic", "dialog-password-symbolic")):
    """맨 위 강조색 띠"""
    h = Gtk.Box(spacing=8)
    h.get_style_context().add_class("auth-head")
    th = Gtk.IconTheme.get_default()
    name = next((n for n in icon_names if th.has_icon(n)), None)
    if name:
        img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU)
        img.set_pixel_size(14)
        h.pack_start(img, False, False, 0)
    h.pack_start(label(text, wrap=False), False, False, 0)
    return h


def body_box(spacing=12):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    b.get_style_context().add_class("auth-body")
    return b


def foot_buttons(ok_text, cancel_text):
    """(아래 줄, 확인 단추, 취소 단추) — 같은 너비 둘, 확인은 강조색·기본 단추(Enter)"""
    foot = Gtk.Box(spacing=8, homogeneous=True)
    foot.get_style_context().add_class("auth-foot")
    ok = Gtk.Button(label=ok_text)
    ok.get_style_context().add_class("accent-btn")
    ok.set_can_default(True)
    cancel = Gtk.Button(label=cancel_text)
    foot.pack_start(ok, True, True, 0)
    foot.pack_start(cancel, True, True, 0)
    return foot, ok, cancel


def details_grid(rows, key_width=96):
    """이름 : 값 두 칸 (고를 수 있는 글자). rows = [(이름, 값)] — 빈 값은 건너뛴다. 긴 값은 줄인다"""
    g = Gtk.Grid(column_spacing=12, row_spacing=4)
    i = 0
    for k, v in rows:
        if v in (None, ""):
            continue
        kl = label(k, "auth-key", wrap=False)
        kl.set_yalign(0)
        kl.set_size_request(key_width, -1)
        v = str(v)
        if len(v) > 400:                                  # 아주 긴 명령줄이 카드를 화면 밖으로 밀지 않게
            v = v[:400] + "…"
        vl = label(v, "auth-val", selectable=True)
        g.attach(kl, 0, i, 1, 1)
        g.attach(vl, 1, i, 1, 1)
        i += 1
    return g


HANGUL = re.compile(r"[가-힣]")


def is_korean(text):
    return bool(text and HANGUL.search(text))
