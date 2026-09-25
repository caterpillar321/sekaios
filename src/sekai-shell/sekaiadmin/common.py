"""컴퓨터 관리 — 페이지들이 함께 쓰는 도움 (목록·세부 칸·안내 막대·시각·권한·D-Bus 오류).

목록 열·정렬 머리글·메뉴·확인 창은 작업 관리자(sekaishell.taskmgr_common)의 것을 그대로 다시 내보낸다 —
두 앱의 목록이 같은 모양, 같은 손맛이 되게.
"""
import grp
import os
import pwd
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from sekaishell import dbg  # noqa: E402,F401
from sekaishell.taskmgr_common import (FALLBACK_ICON, SortHeaders, appearance, cmp_num,  # noqa: E402,F401
                                       cmp_text, confirm, icon_image, key_is_menu, lookup_color,
                                       menu_item, notice, open_location, popup, text_column)

ME_UID = os.getuid()
try:
    ME = pwd.getpwuid(ME_UID).pw_name
except KeyError:
    ME = str(ME_UID)


# ── 목록 ─────────────────────────────────────────────────────
def mk_view(model, fixed=True):
    """작업 관리자와 같은 목록 — 위쪽 검색 칸을 쓰므로 GTK 의 떠 있는 검색 창은 끈다"""
    v = Gtk.TreeView(model=model)
    v.set_enable_search(False)
    if fixed:
        v.set_fixed_height_mode(True)        # 행이 수천 개여도 높이를 하나하나 재지 않게 (열도 FIXED 여야 한다)
    v.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
    v.get_style_context().add_class("adm-list")
    return v


def scrolled(child, frame=True):
    sc = Gtk.ScrolledWindow()
    sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sc.add(child)
    if frame:
        sc.get_style_context().add_class("adm-listbox")
    return sc


def gicon(names):
    """후보 중 테마에 있는 첫 아이콘의 GIcon (목록 칸에 넣을 것)"""
    th = Gtk.IconTheme.get_default()
    for n in [names] if isinstance(names, str) else names:
        if n and th.has_icon(n):
            return Gio.ThemedIcon.new(n)
    return FALLBACK_ICON


# ── 세부 칸 ──────────────────────────────────────────────────
class DetailGrid(Gtk.Grid):
    """이름 : 값 두 칸짜리 표. 같은 줄 수면 글자만 바꾼다 (몇 초마다 고쳐도 번쩍이거나 선택이 풀리지 않게)"""

    def __init__(self, key_width=110):
        super().__init__(column_spacing=16, row_spacing=6)
        self.get_style_context().add_class("adm-grid")
        self._rows = []
        self._key_width = key_width

    def set_rows(self, rows):
        rows = [(k, "" if v is None else str(v)) for k, v in rows if v not in (None, "")]
        while len(self._rows) > len(rows):
            k, v = self._rows.pop()
            k.destroy()
            v.destroy()
        for i, (key, val) in enumerate(rows):
            if i >= len(self._rows):
                k = Gtk.Label(xalign=0, yalign=0)
                k.get_style_context().add_class("adm-key")
                k.set_size_request(self._key_width, -1)
                v = Gtk.Label(xalign=0, yalign=0)
                v.get_style_context().add_class("adm-val")
                v.set_selectable(True)
                v.set_line_wrap(True)
                v.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                v.set_hexpand(True)
                v.set_can_focus(False)          # 목록의 Tab 이동을 가로채지 않게 (끌어서 고르기는 된다)
                self.attach(k, 0, i, 1, 1)
                self.attach(v, 1, i, 1, 1)
                k.show()
                v.show()
                self._rows.append((k, v))
            k, v = self._rows[i]
            if k.get_text() != key:
                k.set_text(key)
            if v.get_text() != val:
                v.set_text(val)

    @staticmethod
    def as_text(rows):
        return "\n".join(f"{k}: {v}" for k, v in rows if v not in (None, ""))


# ── 안내 막대 ────────────────────────────────────────────────
class NoticeBar(Gtk.Box):
    """목록 위의 한 줄 안내 + 단추들 (권한이 모자랄 때 등). kind: info · warn · error"""

    ICONS = {"info": "dialog-information-symbolic", "warn": "dialog-warning-symbolic",
             "error": "dialog-error-symbolic"}

    def __init__(self):
        super().__init__(spacing=10)
        self.get_style_context().add_class("adm-notice")
        self.img = Gtk.Image()
        self.pack_start(self.img, False, False, 0)
        self.label = Gtk.Label(xalign=0)
        self.label.set_line_wrap(True)
        self.pack_start(self.label, True, True, 0)
        self.btns = Gtk.Box(spacing=6)
        self.pack_end(self.btns, False, False, 0)
        self.set_no_show_all(True)
        self._kind = None

    def show_notice(self, text, buttons=(), kind="info"):
        """buttons = [(글, 누르면 부를 것)]"""
        ctx = self.get_style_context()
        if self._kind:
            ctx.remove_class(self._kind)
        self._kind = kind
        ctx.add_class(kind)
        self.img.set_from_icon_name(self.ICONS.get(kind, self.ICONS["info"]), Gtk.IconSize.BUTTON)
        self.label.set_text(text)
        for b in self.btns.get_children():
            b.destroy()
        for label, cb in buttons:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            self.btns.pack_start(b, False, False, 0)
        for w in (self.img, self.label, self.btns):
            w.show_all()
        self.show()

    def hide_notice(self):
        self.hide()


# ── 글자 모양 ────────────────────────────────────────────────
def fmt_time(ts, seconds=True):
    """윈도우 한국어 표기 — 2026-09-25 오후 3:21:05 (ts 는 초)"""
    if not ts:
        return ""
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    s = f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d} {'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d}"
    return s + (f":{t.tm_sec:02d}" if seconds else "")


def fmt_short_date(ts):
    """9월 25일 오후 3:21 — 부팅 목록처럼 좁은 곳"""
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    return f"{t.tm_mon}월 {t.tm_mday}일 {'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d}"


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def fmt_duration(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs}초"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}분 {s}초"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}시간 {m}분"
    return f"{h // 24}일 {h % 24}시간"


def first_line(text, limit=400):
    s = (text or "").lstrip("\n")
    i = s.find("\n")
    s = s if i < 0 else s[:i]
    return s if len(s) <= limit else s[:limit] + "…"


def copy_text(text):
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    cb.set_text(text, -1)
    cb.store()


def user_name(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError, TypeError):
        return str(uid)


# ── 권한 ─────────────────────────────────────────────────────
def _gid(name):
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def in_groups_now(*names):
    """이 프로세스가 지금 가진 그룹 — /etc/group 에 넣었어도 다시 로그인해야 생긴다"""
    have = set(os.getgroups()) | {os.getgid()}
    return any(g is not None and g in have for g in map(_gid, names))


def listed_in_group(name, user=ME):
    """/etc/group 에 적혀 있는지 (다음 로그인부터 적용될 것까지)"""
    try:
        g = grp.getgrnam(name)
    except KeyError:
        return False
    if user in g.gr_mem:
        return True
    try:
        return pwd.getpwnam(user).pw_gid == g.gr_gid
    except KeyError:
        return False


def is_admin():
    """관리자 계정 (sudo 그룹) — 설정 앱의 사용자 페이지와 같은 기준"""
    return ME_UID == 0 or listed_in_group("sudo")


# ── D-Bus 오류 ───────────────────────────────────────────────
def dbus_error_name(err):
    try:
        return Gio.DBusError.get_remote_error(err) or ""
    except (TypeError, AttributeError):
        return ""


def dbus_error_text(err):
    """GLib.Error(원격 D-Bus 오류) → 사람이 읽을 한 줄"""
    name = dbus_error_name(err)
    msg = err.message
    try:
        Gio.DBusError.strip_remote_error(err)
        msg = err.message
    except (TypeError, AttributeError):
        pass
    low = (msg or "").lower()
    if name in ("org.freedesktop.DBus.Error.AccessDenied", "org.freedesktop.DBus.Error.AuthFailed") or \
            "access denied" in low or "not authorized" in low:
        return "관리자 인증이 취소되었거나 권한이 없어 바꾸지 않았습니다"
    if name == "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired" or "interactive authentication" in low:
        return "관리자 인증이 필요합니다 (인증 창을 띄울 수 없습니다)"
    if name == "org.freedesktop.DBus.Error.ServiceUnknown" or name == "org.freedesktop.DBus.Error.NoReply":
        return "서비스 관리자(systemd)가 응답하지 않습니다"
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        return "응답을 기다리다 시간이 지났습니다"
    return msg or "실패했습니다"


def main_idle(fn, *args):
    """작업 스레드 → 메인 스레드 (한 번만)"""
    GLib.idle_add(lambda: (fn(*args), False)[1])
