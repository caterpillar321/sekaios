"""스토어 화면 부품 — 아이콘·그림, 앱 타일, 설치 대기열."""
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from sekaishell import appmgr  # noqa: E402

from . import catalog  # noqa: E402

GENERIC = ["application-x-executable", "applications-other"]


def label(text="", cls=None, wrap=False, lines=0, xalign=0.0, markup=False, ellipsize=False, selectable=False):
    lb = Gtk.Label(xalign=xalign)
    (lb.set_markup if markup else lb.set_text)(text)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lines:
        lb.set_lines(lines)
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    elif ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    lb.set_selectable(selectable)
    for c in (cls or "").split():
        lb.get_style_context().add_class(c)
    return lb


def _pixbuf(path, size):
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
    except GLib.Error:
        return None


def app_icon(app, size):
    """앱 아이콘 — 받아야 하는 것은 받은 뒤에 바꿔 끼운다 (그동안 일반 아이콘)"""
    img = Gtk.Image()
    img.set_pixel_size(size)
    img.set_size_request(size, size)
    th = Gtk.IconTheme.get_default()
    img.set_from_icon_name(next((n for n in GENERIC if th.has_icon(n)), GENERIC[0]), Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    ic = app.icon(size)
    if ic is None:
        return img
    kind, val = ic
    if kind == "file":
        pb = _pixbuf(val, size)
        if pb:
            img.set_from_pixbuf(pb)
    elif kind == "theme" and th.has_icon(val):
        img.set_from_icon_name(val, Gtk.IconSize.DIALOG)
        img.set_pixel_size(size)
    elif kind == "url":
        def got(path):
            if path:
                pb = _pixbuf(path, size)
                if pb:
                    img.set_from_pixbuf(pb)
            return False
        catalog.fetch(val, got)
    return img


def picture(url, width, height, cls="store-shot", on_fail=None):
    """인터넷 그림 하나 (스크린샷) — 비율을 지켜 width×height 안에"""
    frame = Gtk.EventBox()
    frame.get_style_context().add_class(cls)
    frame.set_size_request(width, height)
    img = Gtk.Image()
    sp = Gtk.Spinner()
    sp.start()
    box = Gtk.Box()
    box.set_center_widget(sp)
    frame.add(box)

    def got(path):
        pb = None
        if path:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, True)
            except GLib.Error:
                pb = None
        if pb is None:
            frame.hide()                # 못 받은 그림은 자리를 비워 두지 않는다 (깨진 그림 대신)
            frame.set_no_show_all(True)
            if on_fail:
                on_fail(frame)
            return False
        box.remove(sp)
        img.set_from_pixbuf(pb)
        box.set_center_widget(img)
        img.show()
        return False
    catalog.fetch(url, got)
    return frame


class Tile(Gtk.Button):
    """앱 타일 — 아이콘 · 이름 · 한 줄 소개 · 분류(설치됨)"""

    def __init__(self, store, app):
        super().__init__()
        self.app = app
        self.store = store
        self.get_style_context().add_class("store-tile")
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_size_request(232, 150)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        v.pack_start(app_icon(app, 48), False, False, 0)
        v.get_children()[0].set_halign(Gtk.Align.START)
        name = label(app.name, "tile-name", ellipsize=True)
        sub = label(app.summary, "tile-sub", wrap=True, lines=2)
        for lb in (name, sub):
            lb.set_max_width_chars(26)          # 긴 영어 요약 한 줄 길이로 타일이 늘어나지 않게
            lb.set_width_chars(20)
        v.pack_start(name, False, False, 0)
        v.pack_start(sub, False, False, 0)
        self.badge = label("", "tile-badge")
        v.pack_end(self.badge, False, False, 0)
        self.add(v)
        self.update()
        self.connect("clicked", lambda *_: store.show_app(app))
        store.jobs.watch(self.update)
        self.connect("destroy", lambda *_: store.jobs.unwatch(self.update))

    def update(self, *_):
        st = self.store.jobs.state_of(self.app)
        if st == "installed":
            self.badge.set_text("설치됨")
            self.badge.get_style_context().add_class("on")
        else:
            self.badge.get_style_context().remove_class("on")
            cat = self.app.category_name() + (" · Flathub" if self.app.is_flatpak else "")
            self.badge.set_text({"queued": "대기 중", "installing": "설치하는 중…", "removing": "제거하는 중…"}
                                .get(st, cat))
        return False


def grid():
    fb = Gtk.FlowBox()
    fb.set_selection_mode(Gtk.SelectionMode.NONE)
    fb.set_homogeneous(True)
    fb.set_min_children_per_line(2)
    fb.set_max_children_per_line(6)
    fb.set_row_spacing(12)
    fb.set_column_spacing(12)
    fb.set_valign(Gtk.Align.START)
    return fb


def fill(fb, store, apps, start=0, count=48):
    """apps[start:start+count] 를 타일로 붙인다 → 붙인 뒤의 위치"""
    for a in apps[start:start + count]:
        t = Tile(store, a)
        t.show_all()
        fb.add(t)
        child = t.get_parent()
        if child is not None:
            child.set_can_focus(False)          # 키보드 초점은 타일(단추)에만
    return min(len(apps), start + count)


class Jobs:
    """설치·제거 대기열 — 한 번에 하나 (apt 는 동시에 못 돈다). 화면은 watch 로 상태를 따라간다."""

    def __init__(self, catalog_):
        self.cat = catalog_
        self.queue = []                 # [(동작, 앱)]
        self.current = None             # (동작, 앱, {진행})
        self._watch = []
        self.errors = {}                # 패키지 → 마지막 오류

    def watch(self, cb):
        self._watch.append(cb)

    def unwatch(self, cb):
        if cb in self._watch:
            self._watch.remove(cb)

    def _notify(self, app=None):
        for cb in list(self._watch):
            try:
                cb(app)
            except Exception:           # 화면이 이미 사라진 타일 등
                self.unwatch(cb)

    def state_of(self, app, merged=True):
        """merged: 양쪽에 있는 앱이면 다른 출처의 상태도 (목록 타일은 어느 쪽이든 설치됐으면 '설치됨')"""
        if self.current and self.current[1] is app:
            return "installing" if self.current[0] == "install" else "removing"
        if any(a is app for _op, a in self.queue):
            return "queued"
        if self.cat.is_installed(app):
            return "installed"
        if merged and app.alt is not None:
            st = self.state_of(app.alt, merged=False)
            if st != "none":
                return st
        return "installed" if self.cat.external(app) else "none"

    def progress_of(self, app):
        if self.current and self.current[1] is app:
            return self.current[2]
        return None

    def add(self, op, app):
        if self.state_of(app) in ("queued", "installing", "removing"):
            return                      # 연타 — 이미 들어 있다
        self.errors.pop(app.pkg, None)
        self.queue.append((op, app))
        self._notify(app)
        self._next()

    def cancel(self, app):
        """아직 시작하지 않은 것만 뺀다"""
        n = len(self.queue)
        self.queue = [(o, a) for o, a in self.queue if a is not app]
        if len(self.queue) != n:
            self._notify(app)

    def busy(self):
        return self.current is not None or bool(self.queue)

    def _next(self):
        if self.current is not None or not self.queue:
            return
        op, app = self.queue.pop(0)
        prog = {"pct": 0, "text": "관리자 인증을 기다리는 중…", "apps": [], "error": None}
        self.current = (op, app, prog)
        self._notify(app)

        def line(kind, rest):
            if kind == "PROGRESS":
                pct, _, text = rest.partition(" ")
                try:
                    prog["pct"] = max(0, min(100, int(pct)))
                except ValueError:
                    pass
                prog["text"] = text
                self._notify(app)
            elif kind == "ERROR":
                prog["error"] = rest
            elif kind == "APP":
                prog["apps"].append(rest)
            return False

        def done(rc):
            if rc in appmgr.CANCELLED and not prog["error"]:
                err = "인증이 취소되었습니다"
            elif rc != 0 or prog["error"]:
                err = prog["error"] or f"도우미가 비정상 종료했습니다 (코드 {rc})"
            else:
                err = None
            if err:
                self.errors[app.pkg] = err
            self.cat.refresh_installed()
            self.current = None
            self._notify(app)
            self._next()
            return False
        if app.is_flatpak:
            appmgr.run_flatpak(op, app.ref, line, done)
        else:
            appmgr.run_helper([op, app.pkg], line, done, repo=True)


def launch(app, cat=None):
    """설치된 앱 열기 → 열었으면 True. cat 을 주면 스토어 밖에서 깐 판(업체 .deb 등)도 연다"""
    from gi.repository import Gdk, Gio
    ext = cat.external(app) if cat is not None else None
    if ext:
        paths = [ext[1]] if ext[1] else []
        if ext[2]:
            import subprocess
            res = subprocess.run(["dpkg-query", "-L", ext[2]], capture_output=True, text=True)
            paths += [p for p in res.stdout.splitlines()
                      if p.startswith("/usr/share/applications/") and p.endswith(".desktop")]
        for p in paths:
            info = Gio.DesktopAppInfo.new_from_filename(p) if os.path.exists(p) else None
            if info is None or info.get_nodisplay():
                continue
            try:
                info.launch([], Gdk.Display.get_default().get_app_launch_context())
                return True
            except GLib.Error:
                continue
    ids = app.desktop_ids()
    if app.is_flatpak and f"{app.pkg}.desktop" not in ids:
        ids.append(f"{app.pkg}.desktop")
    for i in ids:
        try:
            info = Gio.DesktopAppInfo.new(i)
        except TypeError:
            info = None
        if info is None:
            continue
        try:
            info.launch([], Gdk.Display.get_default().get_app_launch_context())
            return True
        except GLib.Error:
            continue
    if app.is_flatpak:
        # .desktop 을 못 찾았다 (세션의 XDG_DATA_DIRS 에 flatpak 자리가 없는 옛 세션 등) — 바로 실행
        try:
            Gio.Subprocess.new(["flatpak", "run", app.pkg], Gio.SubprocessFlags.NONE)
            return True
        except GLib.Error:
            return False
    # 목록의 이름과 설치된 .desktop 이름이 다를 때 — 패키지가 깐 .desktop 으로
    import subprocess
    res = subprocess.run(["dpkg-query", "-L", app.pkg], capture_output=True, text=True)
    for path in res.stdout.splitlines():
        if path.startswith("/usr/share/applications/") and path.endswith(".desktop") and os.path.exists(path):
            info = Gio.DesktopAppInfo.new_from_filename(path)
            if info is not None and not info.get_nodisplay():
                try:
                    info.launch([], Gdk.Display.get_default().get_app_launch_context())
                    return True
                except GLib.Error:
                    continue
    return False
