"""업데이트 — SekaiOS 와 데비안 시스템의 새 패키지를 확인하고 설치한다 (앱 업데이트는 스토어에서).

저장소 출처로 묶어 보인다 — SekaiOS · 데비안 시스템 · 다른 저장소. 묶음마다 따로 설치할 수 있다.
일반 앱(크로뮴·VS Code·Flathub 앱)은 여기서 올리지 않는다 — 윈도우처럼 스토어 › 라이브러리에서 (sekaishell/aptapps.py).
관리자 권한이 필요한 일은 /usr/libexec/sekai/sekai-update 가 한다 (pkexec).
이 페이지는 그 출력(PROGRESS/PKG/GROUP/ALSO/APPS/COUNT/REBOOT/ERROR 줄)을 읽어 보여 줄 뿐이다.
"""
import os
import re
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from sekaishell import aptapps
from ..widgets import Page, button, info, row, subsection

HELPER = "/usr/libexec/sekai/sekai-update"
SOURCES = "/etc/apt/sources.list.d/sekaios.sources"
SHOW_MAX = 40
# 묶음 → (제목, 설명)
GROUPS = [("sekai", "SekaiOS", "셸·합성기·설정 같은 SekaiOS 구성 요소"),
          ("debian", "데비안 시스템", "커널·드라이버·라이브러리"),
          ("other", "다른 저장소", "직접 추가한 저장소의 패키지 (앱이 아닌 것)")]
GROUP_TITLE = {k: t for k, t, _s in GROUPS}


def _pending():
    """관리자 권한 없이 — 받아 둔 목록 기준. → (시스템 [(이름, 지금, 새, 묶음, 보안?)], 앱 업데이트 수)
    · 셀 수 없으면 None (없음과 구별). 지워지는 것은 (이름, "-", "삭제", 묶음 없음)"""
    sysd, apps, _pins = aptapps.split(env=dict(os.environ, LC_ALL="C.UTF-8"))
    if sysd is None:                             # 다른 설치가 잠금을 쥐고 있는 등
        return None
    pk, gone = sysd
    return list(pk) + [(n, "-", "삭제", None, False) for n in gone], len(apps)


def _flatpak_count():
    from sekaishell.updates import count_flatpak
    return count_flatpak()


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
        self.pkgs = []                            # [(이름, 지금, 새, 묶음, 보안?)]
        self.p = Page("업데이트", "보안 수정과 새 기능을 받습니다. 설치는 직접 누를 때만 합니다.")

        # ── 상태 카드 ──
        s = self.p.section()
        self.status_row = row(s, "확인하는 중…", " ",
                              icon=["system-software-update", "software-update-available"],
                              control=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8))
        box = self.status_row.control
        self.check_btn = button("업데이트 확인", self.check)
        self.apply_btn = button("모두 설치", lambda: self.apply(None), cls="accent-btn")
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

        # ── 묶음별 목록 — SekaiOS · 데비안 시스템 · 다른 저장소 (+ 함께 지워지는 것) ──
        self.groups = {}
        for key, title, sub in GROUPS + [(None, "함께 지워지는 패키지", None)]:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.set_no_show_all(True)
            self.p.add_widget(holder)
            lb = subsection(holder, title)
            head = None
            if key is not None:
                btn = button("설치", lambda k=key: self.apply(k))
                head = row(lb, title, sub, control=btn)
                head.btn = btn
            self.groups[key] = (holder, lb, head, sub)

        # ── 앱 — 스토어에서 ──
        s = self.p.section("앱")
        self.apps_row = row(s, "앱 업데이트는 스토어에서",
                            "크로뮴·VS Code 같은 앱과 Flathub 앱은 스토어 › 라이브러리에서 올립니다",
                            icon=["sekai-store", "system-software-install", "org.gnome.Software"],
                            control=button("스토어 열기", lambda: subprocess.Popen(
                                ["sekai-store", "--library"], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        self.apps_n = {"apt": None, "flatpak": None}

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
        threading.Thread(target=lambda: self._set_apps(flatpak=_flatpak_count()), daemon=True).start()

    @property
    def widget(self):
        return self.p

    # ── 표시 ──
    def _set_status(self, title, sub=None):
        self.status_row.title_label.set_text(title)
        if hasattr(self.status_row, "sub_label"):
            self.status_row.sub_label.set_text(sub or "")

    def _set_apps(self, **kw):
        """앱 업데이트 수 (apt 앱 · Flathub) — 작업 스레드에서도 부른다"""
        def put():
            self.apps_n.update(kw)
            known = [v for v in self.apps_n.values() if v is not None]
            n = sum(known)
            self.apps_row.title_label.set_text(f"앱 업데이트 {n}개 — 스토어에서" if n else "앱 업데이트는 스토어에서")
            return False
        GLib.idle_add(put)

    def _pkg_row(self, lb, name, old, new, sec=False):
        r = Gtk.ListBoxRow()
        r.get_style_context().add_class("row")
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        n = Gtk.Label(label=name + ("  · 보안" if sec else ""), xalign=0)
        n.get_style_context().add_class("row-title")
        h.pack_start(n, True, True, 0)
        v = Gtk.Label(label="지워짐" if new == "삭제" else
                      f"{old}  →  {new}" if old != "-" else f"새로 설치  {new}")
        v.get_style_context().add_class("row-value")
        v.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        v.set_max_width_chars(72)
        h.pack_end(v, False, False, 0)
        r.add(h)
        lb.add(r)

    def _show_list(self, pkgs):
        for key, (holder, lb, head, sub) in self.groups.items():
            mine = [p for p in pkgs if p[3] == key]
            for r in lb.get_children():
                if r is not head:
                    lb.remove(r)
            for name, old, new, _g, sec in mine[:SHOW_MAX]:
                self._pkg_row(lb, name, old, new, sec)
            if len(mine) > SHOW_MAX:
                r = Gtk.ListBoxRow()
                r.get_style_context().add_class("row")
                r.add(Gtk.Label(label=f"… 그리고 {len(mine) - SHOW_MAX}개 더", xalign=0))
                lb.add(r)
            if head is not None:
                nsec = sum(1 for p in mine if p[4])
                head.title_label.set_text(f"업데이트 {len(mine)}개")
                head.sub_label.set_text(sub + (f" · 보안 수정 {nsec}개" if nsec else ""))
                head.btn.set_sensitive(not self.p.busy)
            holder.set_visible(bool(mine))
            if mine:
                # no_show_all 이 켜진 위젯은 show_all 이 안쪽으로 내려가지 않는다 → 직접
                for w in holder.get_children():
                    w.show()
                for r in lb.get_children():
                    r.show_all()
        self.apply_btn.set_visible(bool(pkgs) and not self.p.busy)

    def _summary(self, pkgs):
        last = _ago(_last_check())
        if pkgs:
            self._set_status(f"업데이트 {len(pkgs)}개를 설치할 수 있습니다", f"마지막 확인: {last}")
        else:
            self._set_status("최신 상태입니다", f"마지막 확인: {last}")

    def _load_cached(self):
        r = _pending()
        GLib.idle_add(self._cached_done, r)

    def _cached_done(self, r):
        if self.p.busy:
            return False
        if r is None:
            # 셀 수 없었다 (다른 설치가 잠금을 쥐었거나, 깨진 의존성, 시간 초과) — "최신"이라고 하지 않고 목록은 그대로
            self._set_status("업데이트를 확인하지 못했습니다",
                             "받아 둔 목록으로 셀 수 없었습니다. ‘업데이트 확인’으로 다시 확인해 주세요")
            self._show_list(self.pkgs)
            return False
        self.pkgs, napps = r
        self._set_apps(apt=napps)
        self._summary(self.pkgs)
        self._show_list(self.pkgs)
        return False

    # ── 도우미 실행 ──
    def _run(self, args, title):
        if self.p.busy:
            return
        if not os.path.exists(HELPER):
            self._set_status("업데이트 도우미가 없습니다", HELPER)
            return
        self.p.busy = True
        for b in (self.check_btn, self.apply_btn) + tuple(h.btn for _k, (_h, _l, h, _s) in self.groups.items() if h):
            b.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.show()
        self.progress_text.set_text("관리자 인증을 기다리는 중…")
        self.progress_text.show()
        self._set_status(title, "창을 닫아도 작업은 계속됩니다")
        self._got = {"pkgs": [], "count": None, "reboot": False, "error": None, "also": {}, "apps": None}
        threading.Thread(target=self._worker, args=(args,), daemon=True).start()

    def _worker(self, args):
        try:
            p = subprocess.Popen(["pkexec", HELPER] + args, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            GLib.idle_add(self._finish, args[0], f"실행하지 못했습니다: {e}")
            return
        for line in p.stdout:
            GLib.idle_add(self._line, line.rstrip("\n"))
        rc = p.wait()
        err = None
        if rc in (126, 127):
            err = "인증이 취소되었습니다"
        elif rc != 0:
            err = f"업데이트 도우미가 비정상 종료했습니다 (코드 {rc})"
        GLib.idle_add(self._finish, args[0], err)

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
                # 묶음은 뒤따르는 GROUP 줄이 알려 준다 (지워지는 것은 묶음 없음)
                self._got["pkgs"].append(tuple(parts) + ((None if parts[2] == "삭제" else "debian"), False))
        elif kind == "GROUP":
            parts = rest.split(" ")
            got = self._got["pkgs"]
            if len(parts) >= 2 and got and got[-1][0] == parts[0] and parts[1] in GROUP_TITLE:
                got[-1] = got[-1][:3] + (parts[1], "security" in parts[2:])
        elif kind == "ALSO":
            parts = rest.split(" ")
            if len(parts) == 2 and parts[0] in GROUP_TITLE and parts[1].isdigit():
                self._got["also"][parts[0]] = int(parts[1])
        elif kind == "APPS":
            if rest.isdigit():
                self._got["apps"] = int(rest)
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
        self.p.busy = False
        for b in (self.check_btn, self.apply_btn) + tuple(h.btn for _k, (_h, _l, h, _s) in self.groups.items() if h):
            b.set_sensitive(True)
        self.progress.hide()
        self.progress_text.hide()
        err = self._got["error"] or err          # 도우미가 알려 준 이유가 더 자세하다
        if err:
            # 실패 — 인증 취소, 다른 설치가 진행 중(dpkg 잠김), 네트워크 등. 도우미가 목록을 끝까지
            #   알려 주지 못했을 수 있으니 빈 목록으로 덮지 않는다 (예전엔 목록이 사라졌다).
            #   설치 도중 실패면 일부만 깔렸을 수 있다 — 남은 것을 받아 둔 목록 기준으로 다시 센다
            self._set_status("업데이트하지 못했습니다", err)
            self._show_list(self.pkgs)
            if action == "apply":
                self.ver_label.set_text(_version() or "알 수 없음")
                threading.Thread(target=self._reload_after_fail, args=(err,), daemon=True).start()
            if self._got["reboot"]:
                self.reboot_btn.show()
            return False
        if action == "check":
            self.pkgs = self._got["pkgs"]
            if self._got["apps"] is not None:
                self._set_apps(apt=self._got["apps"])
            self._summary(self.pkgs)
            self._show_list(self.pkgs)
            threading.Thread(target=lambda: self._set_apps(flatpak=_flatpak_count()), daemon=True).start()
        else:
            self.ver_label.set_text(_version() or "알 수 없음")
            sub = ("다시 시작해야 일부 업데이트가 적용됩니다" if self._got["reboot"]
                   else "지금부터 새 버전이 쓰입니다 (열려 있던 앱은 다시 열면 적용)")
            also = [f"{GROUP_TITLE[g]} {n}개" for g, n in self._got["also"].items()]
            if also:
                sub += " — 필요해서 " + "·".join(also) + "도 함께 올렸습니다"
            self._set_status("업데이트를 설치했습니다", sub)
            # 남은 것(다른 묶음·보류된 패키지 등)을 받아 둔 목록 기준으로 센다 — 수십 초 걸릴 수 있어 작업 스레드에서
            threading.Thread(target=self._reload_after_apply, args=(self._got["count"], sub), daemon=True).start()
            if self._got["reboot"]:
                self.reboot_btn.show()
        return False

    def _reload_after_apply(self, left, sub):
        r = _pending()
        GLib.idle_add(self._reloaded_after_apply, r, left, sub)

    def _reloaded_after_apply(self, r, left, sub):
        if self.p.busy:
            return False
        if r is None:
            # 도우미는 남은 것이 있다고 했는데 목록을 셀 수 없었다 — 없는 것처럼 보이지 않게 개수만이라도
            self.pkgs = []
            self._show_list(self.pkgs)
            if left:
                self._set_status("업데이트를 설치했습니다",
                                 f"{sub} — 남은 업데이트 {left}개는 목록을 읽지 못했습니다")
            return False
        self.pkgs, napps = r
        self._set_apps(apt=napps)
        self._show_list(self.pkgs)
        if self.pkgs:
            self._set_status("업데이트를 설치했습니다", f"{sub} — 남은 업데이트 {len(self.pkgs)}개")
        return False

    def _reload_after_fail(self, err):
        r = _pending()
        GLib.idle_add(self._reloaded_after_fail, r, err)

    def _reloaded_after_fail(self, r, err):
        if self.p.busy:
            return False
        if r is not None:                        # 다시 셀 수 있었으면 그것으로 (못 셌으면 이전 목록 유지)
            self.pkgs, napps = r
            self._set_apps(apt=napps)
            self._show_list(self.pkgs)
        self._set_status("업데이트하지 못했습니다",
                         err + (f" — 남은 업데이트 {len(self.pkgs)}개" if self.pkgs else ""))
        return False

    def check(self):
        self._run(["check"], "업데이트를 확인하는 중…")

    def apply(self, group):
        what = "업데이트를 설치하는 중…" if group is None else f"{GROUP_TITLE[group]} 업데이트를 설치하는 중…"
        self._run(["apply"] + ([group] if group else []), what)


def build(store):
    return UpdatePage(store).widget


PAGES = [{"id": "update", "title": "업데이트",
          "icon": ["system-software-update", "software-update-available",
                   "update-manager", "system-software-install"],
          "build": build}]
