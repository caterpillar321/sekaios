"""업데이트 — 데비안과 SekaiOS 저장소의 새 패키지를 확인하고 설치한다.

관리자 권한이 필요한 일은 /usr/libexec/sekai/sekai-update 가 한다 (pkexec).
이 페이지는 그 출력(PROGRESS/PKG/COUNT/REBOOT/ERROR 줄)을 읽어 보여 줄 뿐이다.
"""
import os
import re
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..widgets import Page, button, info, row

HELPER = "/usr/libexec/sekai/sekai-update"
SOURCES = "/etc/apt/sources.list.d/sekaios.sources"
SHOW_MAX = 40


def _pending():
    """관리자 권한 없이 — 받아 둔 목록 기준으로 설치될 업데이트"""
    try:
        res = subprocess.run(["apt-get", "-s", "-q", "full-upgrade"], capture_output=True,
                             text=True, timeout=60, env=dict(os.environ, LC_ALL="C.UTF-8"))
    except Exception:
        return []
    pk = []
    for line in res.stdout.splitlines():
        m = re.match(r"Inst (\S+) (?:\[(\S+)\] )?\((\S+)", line)
        if m:
            pk.append((m.group(1), m.group(2) or "-", m.group(3)))
    return pk


def _last_check():
    for p in ("/var/lib/apt/periodic/update-success-stamp", "/var/lib/apt/lists"):
        try:
            return os.path.getmtime(p)
        except OSError:
            continue
    return None


def _ago(ts):
    if not ts:
        return "확인한 적 없음"
    d = time.time() - ts
    if d < 90:
        return "방금"
    if d < 3600:
        return f"{int(d // 60)}분 전"
    if d < 86400:
        return f"{int(d // 3600)}시간 전"
    t = time.localtime(ts)
    return f"{t.tm_mon}월 {t.tm_mday}일 {t.tm_hour:02d}:{t.tm_min:02d}"


def _sekai_repo():
    """(켜짐?, 주소)"""
    try:
        with open(SOURCES, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False, None
    uri = re.search(r"^URIs:\s*(\S+)", text, re.M)
    en = re.search(r"^Enabled:\s*(\S+)", text, re.M)
    on = not en or en.group(1).lower() not in ("no", "false", "0")
    return on, uri.group(1) if uri else None


def _version():
    try:
        return subprocess.run(["dpkg-query", "-W", "-f=${Version}", "sekai-desktop"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


class UpdatePage:
    def __init__(self, store):
        self.busy = False
        self.pkgs = []
        self.p = Page("업데이트", "보안 수정과 새 기능을 받습니다. 설치는 직접 누를 때만 합니다.")

        # ── 상태 카드 ──
        s = self.p.section()
        self.status_row = row(s, "확인하는 중…", " ",
                              icon=["system-software-update", "software-update-available"],
                              control=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8))
        box = self.status_row.control
        self.check_btn = button("업데이트 확인", self.check)
        self.apply_btn = button("지금 설치", self.apply, cls="accent-btn")
        self.reboot_btn = button("다시 시작", lambda: subprocess.Popen(["systemctl", "reboot"]),
                                 cls="accent-btn")
        for b in (self.check_btn, self.apply_btn, self.reboot_btn):
            box.pack_start(b, False, False, 0)
        self.apply_btn.set_no_show_all(True)
        self.reboot_btn.set_no_show_all(True)

        self.progress = Gtk.ProgressBar()
        self.progress.get_style_context().add_class("update-progress")
        self.progress.set_show_text(False)
        self.progress.set_no_show_all(True)
        self.p.add_widget(self.progress)
        self.progress_text = Gtk.Label(xalign=0)
        self.progress_text.get_style_context().add_class("row-sub")
        self.progress_text.set_ellipsize(Pango.EllipsizeMode.END)
        self.progress_text.set_no_show_all(True)
        self.p.add_widget(self.progress_text)

        # ── 목록 ──
        self.list_title = Gtk.Label(label="설치할 업데이트", xalign=0)
        self.list_title.get_style_context().add_class("section-title")
        self.list_title.set_no_show_all(True)
        self.p.add_widget(self.list_title)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.get_style_context().add_class("section")
        self.list.set_no_show_all(True)
        self.p.add_widget(self.list)

        # ── 정보 ──
        s = self.p.section("정보")
        self.ver_label = info(_version() or "알 수 없음")
        row(s, "SekaiOS 버전", None, control=self.ver_label)
        on, uri = _sekai_repo()
        row(s, "SekaiOS 업데이트 저장소",
            "SekaiOS 서명 키로 서명된 패키지만 받습니다" if on else
            "꺼져 있습니다. 데비안 업데이트만 받습니다.",
            control=info(uri if on else "꺼짐"))
        row(s, "자동으로 확인", "하루 한 번 목록을 받아 두고, 업데이트가 있으면 알림으로 알려 줍니다",
            control=info("켜짐"))

        self._set_status("확인하는 중…", "받아 둔 목록에서 찾고 있습니다")
        threading.Thread(target=self._load_cached, daemon=True).start()

    @property
    def widget(self):
        return self.p

    # ── 표시 ──
    def _set_status(self, title, sub=None):
        self.status_row.title_label.set_text(title)
        if hasattr(self.status_row, "sub_label"):
            self.status_row.sub_label.set_text(sub or "")

    def _show_list(self, pkgs):
        for r in self.list.get_children():
            self.list.remove(r)
        for name, old, new in pkgs[:SHOW_MAX]:
            r = Gtk.ListBoxRow()
            r.get_style_context().add_class("row")
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            n = Gtk.Label(label=name, xalign=0)
            n.get_style_context().add_class("row-title")
            h.pack_start(n, True, True, 0)
            v = Gtk.Label(label=f"{old}  →  {new}" if old != "-" else f"새로 설치  {new}")
            v.get_style_context().add_class("row-value")
            v.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            v.set_max_width_chars(72)
            h.pack_end(v, False, False, 0)
            r.add(h)
            self.list.add(r)
        if len(pkgs) > SHOW_MAX:
            r = Gtk.ListBoxRow()
            r.get_style_context().add_class("row")
            r.add(Gtk.Label(label=f"… 그리고 {len(pkgs) - SHOW_MAX}개 더", xalign=0))
            self.list.add(r)
        has = bool(pkgs)
        for w in (self.list_title, self.list):
            w.set_visible(has)
        if has:
            # no_show_all 이 켜진 위젯은 show_all 이 안쪽으로 내려가지 않는다 → 행을 직접
            for r in self.list.get_children():
                r.show_all()
        self.apply_btn.set_visible(has and not self.busy)

    def _summary(self, pkgs):
        last = _ago(_last_check())
        if pkgs:
            self._set_status(f"업데이트 {len(pkgs)}개를 설치할 수 있습니다", f"마지막 확인: {last}")
        else:
            self._set_status("최신 상태입니다", f"마지막 확인: {last}")

    def _load_cached(self):
        pk = _pending()
        GLib.idle_add(self._cached_done, pk)

    def _cached_done(self, pk):
        if self.busy:
            return False
        self.pkgs = pk
        self._summary(pk)
        self._show_list(pk)
        return False

    # ── 도우미 실행 ──
    def _run(self, action, title):
        if self.busy:
            return
        if not os.path.exists(HELPER):
            self._set_status("업데이트 도우미가 없습니다", HELPER)
            return
        self.busy = True
        for b in (self.check_btn, self.apply_btn):
            b.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.show()
        self.progress_text.set_text("관리자 인증을 기다리는 중…")
        self.progress_text.show()
        self._set_status(title, "창을 닫아도 작업은 계속됩니다")
        self._got = {"pkgs": [], "count": None, "reboot": False, "error": None}
        threading.Thread(target=self._worker, args=(action,), daemon=True).start()

    def _worker(self, action):
        try:
            p = subprocess.Popen(["pkexec", HELPER, action], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            GLib.idle_add(self._finish, action, f"실행하지 못했습니다: {e}")
            return
        for line in p.stdout:
            GLib.idle_add(self._line, line.rstrip("\n"))
        rc = p.wait()
        err = None
        if rc in (126, 127):
            err = "인증이 취소되었습니다"
        GLib.idle_add(self._finish, action, err)

    def _line(self, line):
        kind, _, rest = line.partition(" ")
        if kind == "PROGRESS":
            pct, _, text = rest.partition(" ")
            try:
                self.progress.set_fraction(max(0, min(100, int(pct))) / 100)
            except ValueError:
                pass
            self.progress_text.set_text(text)
        elif kind == "PKG":
            parts = rest.split(" ")
            if len(parts) == 3:
                self._got["pkgs"].append(tuple(parts))
        elif kind == "COUNT":
            try:
                self._got["count"] = int(rest)
            except ValueError:
                pass
        elif kind == "REBOOT":
            self._got["reboot"] = True
        elif kind == "ERROR":
            self._got["error"] = rest
        return False

    def _finish(self, action, err):
        self.busy = False
        for b in (self.check_btn, self.apply_btn):
            b.set_sensitive(True)
        self.progress.hide()
        self.progress_text.hide()
        err = err or self._got["error"]
        if action == "check":
            self.pkgs = self._got["pkgs"]
            self._summary(self.pkgs)
            self._show_list(self.pkgs)
        else:
            self.ver_label.set_text(_version() or "알 수 없음")
            left = self._got["count"]
            self.pkgs = [] if not left else _pending()
            self._show_list(self.pkgs)
            if not err:
                self._set_status("업데이트를 설치했습니다",
                                 "다시 시작해야 일부 업데이트가 적용됩니다" if self._got["reboot"]
                                 else "지금부터 새 버전이 쓰입니다 (열려 있던 앱은 다시 열면 적용)")
            if self._got["reboot"]:
                self.reboot_btn.show()
        if err:
            self._set_status("업데이트하지 못했습니다", err)
        return False

    def check(self):
        self._run("check", "업데이트를 확인하는 중…")

    def apply(self):
        self._run("apply", "업데이트를 설치하는 중…")


def build(store):
    return UpdatePage(store).widget


PAGES = [{"id": "update", "title": "업데이트",
          "icon": ["system-software-update", "software-update-available",
                   "update-manager", "system-software-install"],
          "build": build}]
