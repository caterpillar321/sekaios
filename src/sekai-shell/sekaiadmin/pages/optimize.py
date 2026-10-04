"""컴퓨터 관리 › 드라이브 최적화 (윈도우의 "드라이브 최적화"처럼).

위: 드라이브마다 종류(SSD/HDD)·마지막 TRIM·데이터 검사(btrfs). 아래: "분석"한 결과 — 큰 파일 중 조각이 많은 것.
일은 /usr/libexec/sekai/sekai-optimize 가 한다 (상태 보기는 권한 없이, 분석·최적화·검사는 pkexec).
"자주 고쳐 쓰는 큰 파일"은 이름이 아니라 실제 조각 수로 찾는다 — btrfs 면 그 폴더의 CoW 를 끄거나(앞으로 만드는 파일)
그 파일을 CoW 없이 다시 쓰자고 권한다 (가상 머신 디스크·데이터베이스 등).
"""
import datetime
import json
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..common import (DetailGrid, NoticeBar, SortHeaders, cmp_num, fmt_bytes, gicon, mk_view,  # noqa: E402
                      scrolled, text_column)

HELPER = "/usr/libexec/sekai/sekai-optimize"
FS_NAMES = {"btrfs": "Btrfs", "ext4": "ext4", "xfs": "XFS", "ext3": "ext3", "ext2": "ext2", "f2fs": "F2FS"}

# 드라이브 목록 열
D_ICON, D_NAME, D_KIND, D_FS, D_TRIM, D_CHECK, D_STATE, D_KEY = range(8)
# 분석 결과 열
F_PATH, F_SIZE, F_FRAGS, F_HINT, F_SIZEN, F_FRAGSN = range(6)


def _when(s):
    if not s:
        return "없음"
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S", "%a %b %d %H:%M:%S %Y"):
        try:
            t = datetime.datetime.strptime(s.strip(), fmt)
            return f"{t.month}월 {t.day}일 {t:%H:%M}"
        except ValueError:
            continue
    return s


class OptimizePage:
    searchable = False

    def __init__(self, win):
        self.win = win
        self.busy = False
        self.drives = {}
        self.result = None

        self.store = Gtk.ListStore(Gio.Icon, str, str, str, str, str, str, str)
        self.view = mk_view(self.store)
        for title, col, wdt, icon in (("드라이브", D_NAME, 200, D_ICON), ("종류", D_KIND, 90, None),
                                      ("파일 시스템", D_FS, 90, None), ("마지막 최적화(TRIM)", D_TRIM, 150, None),
                                      ("데이터 검사", D_CHECK, 170, None), ("상태", D_STATE, 200, None)):
            self.view.append_column(text_column(title, col, wdt, icon=icon, expand=(col == D_STATE)))
        self.view.get_selection().connect("changed", lambda *_: self._sel_changed())

        self.fstore = Gtk.ListStore(str, str, str, str, float, float)
        self.fsorted = Gtk.TreeModelSort(model=self.fstore)
        self.fview = mk_view(self.fsorted)
        sorter = SortHeaders(self.fsorted)
        for title, col, wdt, sid, xa in (("파일", F_PATH, 380, F_PATH, 0.0), ("크기", F_SIZE, 90, F_SIZEN, 1.0),
                                         ("조각", F_FRAGS, 80, F_FRAGSN, 1.0), ("권장", F_HINT, 260, F_HINT, 0.0)):
            c = text_column(title, col, wdt, xalign=xa, expand=(col == F_PATH))
            self.fview.append_column(c)
            sorter.add(c, sid, numeric=sid in (F_SIZEN, F_FRAGSN))
        for sid in (F_SIZEN, F_FRAGSN):
            self.fsorted.set_sort_func(sid, lambda m, a, b, s=sid: cmp_num(m[a][s], m[b][s]))
        self.fview.get_selection().connect("changed", lambda *_: self._update_actions())

        self.notice = NoticeBar()
        self.grid = DetailGrid()
        self.flabel = Gtk.Label(xalign=0)
        self.flabel.get_style_context().add_class("adm-key")

        # 드라이브는 몇 개뿐 — 목록은 낮게 고정하고, 남는 자리는 분석 결과(파일 목록)가 가져간다
        dsc = scrolled(self.view)
        dsc.set_size_request(-1, 150)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(self.notice, False, False, 0)
        box.pack_start(dsc, False, False, 0)
        box.pack_start(self.grid, False, False, 0)
        box.pack_start(self.flabel, False, False, 4)
        box.pack_start(scrolled(self.fview), True, True, 0)
        self.widget = box

        self.actions = Gtk.Box(spacing=6)
        self.b_an = self._abtn("분석", self.do_analyze)
        self.b_opt = self._abtn("최적화", self.do_optimize)
        self.b_scrub = self._abtn("데이터 검사", self.do_scrub)
        self.b_nocow = self._abtn("이 폴더 쓰기 최적화…", self.do_nocow)
        self.b_rw = self._abtn("파일 다시 쓰기…", self.do_rewrite)
        self.actions.show_all()
        self._show_result()
        self._update_actions()

    def _abtn(self, label, cb):
        b = Gtk.Button(label=label)
        b.connect("clicked", lambda *_: cb())
        self.actions.pack_start(b, False, False, 0)
        return b

    # ── 보이기 ──
    def on_show(self, **kw):
        self.refresh()

    def on_hide(self):
        pass

    def refresh(self):
        self.win.run_async([HELPER, "status"], self._got_status)

    def _got_status(self, ok, out, err):
        if not ok:
            self.notice.show_notice(f"드라이브 상태를 읽지 못했습니다: {(err or '').strip()}", kind="error")
            return
        try:
            data = json.loads(out)
        except ValueError:
            return
        sel = self._sel_key()
        self.store.clear()
        self.drives = {}
        for d in data:
            key = d.get("uuid") or d["device"]
            self.drives[key] = d
            name = (d.get("label") or ("SekaiOS" if d["mount"] == "/" else os.path.basename(d["mount"]) or d["mount"]))
            name = f"{name} ({d['mount']})"
            kind = "SSD" if d.get("ssd") else "하드 디스크"
            trim = (_when(d.get("trim_last")) if d.get("ssd") else "필요 없음")
            check, state = "—", "정상"
            if d["fstype"] == "btrfs":
                live = d.get("scrub") or {}
                saved = d.get("scrub_saved") or {}
                if live.get("running") or live.get("status") == "running":
                    check, state = "검사 중…", "데이터 검사 중"
                elif live.get("started") or saved.get("at"):
                    check = _when(live.get("started") or saved.get("at"))
                    errs = live.get("errors", saved.get("errors", 0)) or 0
                    if errs:
                        state = f"데이터 오류 {errs}개 — 디스크 확인 필요"
                else:
                    check = "아직 안 함"
            if self.result and self.result.get("device") == d["device"] and self.result["files"]:
                state = (state if state != "정상" else "") + (" · " if state != "정상" else "") + \
                    f"조각난 큰 파일 {len(self.result['files'])}개"
            self.store.append([gicon(["drive-harddisk-solidstate", "drive-harddisk"] if d.get("ssd")
                                     else ["drive-harddisk"]), name, kind, FS_NAMES.get(d["fstype"], d["fstype"]),
                               trim, check, state, key])
        it = self.store.get_iter_first()
        while it is not None:
            if self.store[it][D_KEY] == sel or sel is None:
                self.view.get_selection().select_iter(it)
                break
            it = self.store.iter_next(it)
        self._sel_changed()

    def _sel_key(self):
        m, it = self.view.get_selection().get_selected()
        return m[it][D_KEY] if it is not None else None

    def _drive(self):
        return self.drives.get(self._sel_key())

    def _sel_changed(self):
        d = self._drive()
        if not d:
            self.grid.set_rows([])
        else:
            auto = "켜짐 (매주)" if d.get("trim_auto") else "꺼짐"
            rows = [("장치", d["device"]), ("연결 위치", ", ".join(d["mounts"])),
                    ("크기", f"{fmt_bytes(d['size'])} (사용 가능 {fmt_bytes(d['free'])})"),
                    ("자동 최적화", auto if d.get("ssd") else "하드 디스크는 분석 후 필요할 때")]
            if d["fstype"] == "btrfs":
                rows.append(("자동 데이터 검사", "켜짐 (매달)" if d.get("scrub_auto") else "꺼짐"))
            self.grid.set_rows(rows)
        self._update_actions()

    def _update_actions(self):
        d = self._drive()
        idle = not self.busy
        self.b_an.set_sensitive(bool(d) and idle)
        self.b_opt.set_sensitive(bool(d) and idle)
        self.b_scrub.set_sensitive(bool(d) and idle and d["fstype"] == "btrfs")
        f = self._sel_file()
        btr = bool(self.result) and self.result.get("fstype") == "btrfs"
        self.b_nocow.set_sensitive(idle and f is not None and btr)
        self.b_rw.set_sensitive(idle and f is not None and btr and not f.get("nocow"))

    # ── 분석 결과 ──
    def _show_result(self):
        self.fstore.clear()
        r = self.result
        if not r:
            self.flabel.set_text("분석 결과 — 드라이브를 고르고 ‘분석’을 누르면 조각이 많은 큰 파일을 찾습니다")
            return
        n = len(r["files"])
        text = (f"분석 결과 — 64MB 이상 파일 {r['scanned']}개 중 조각이 많은 파일 {n}개" if n else
                f"분석 결과 — 64MB 이상 파일 {r['scanned']}개 모두 괜찮습니다")
        if r.get("system_skipped"):
            text += f" (시스템 영역 {r['system_skipped']}개는 복원 지점과 나눠 써 그대로 둡니다)"
        self.flabel.set_text(text)
        for f in r["files"]:
            if f.get("nocow"):
                hint = "쓰기 최적화됨 (최적화로 조각 모음)"
            elif r["fstype"] == "btrfs":
                hint = ("자주 고쳐 쓰는 큰 파일 — 쓰기 최적화 권장" if not f.get("snapshotted")
                        else "시스템 영역 — 복원 지점과 나눠 써 그대로 둠")
            else:
                hint = "조각 모음 권장 (최적화)"
            self.fstore.append([f["path"], fmt_bytes(f["size"]), f"{f['frags']:,}", hint,
                                float(f["size"]), float(f["frags"])])

    def _sel_file(self):
        m, it = self.fview.get_selection().get_selected()
        if it is None or not self.result:
            return None
        path = m[it][F_PATH]
        return next((f for f in self.result["files"] if f["path"] == path), None)

    # ── 동작 ──
    def _run(self, args, done, what):
        self.busy = True
        self.win.busy_changed()
        self._update_actions()
        self.notice.show_notice(what, kind="info")

        def fin(ok, out, err):
            self.busy = False
            self.win.busy_changed()
            self.notice.hide_notice()
            if not ok:
                lines = (err or "").strip().splitlines()
                msg = next((ln[6:] for ln in lines if ln.startswith("ERROR ")), lines[-1] if lines else "")
                if "dismissed" not in msg.lower() and "not authorized" not in msg.lower() and msg:
                    self.notice.show_notice(f"하지 못했습니다: {msg}", kind="error")
            else:
                done(out)
            self._update_actions()
        self.win.run_async(["pkexec", HELPER] + args, fin)

    def do_analyze(self):
        d = self._drive()
        if not d:
            return

        def done(out):
            try:
                self.result = json.loads(out)
            except ValueError:
                return
            self._show_result()
            self.refresh()
        self._run(["analyze", d["device"]], done, "드라이브를 분석하는 중… (큰 파일의 조각을 재고 있습니다)")

    def do_optimize(self):
        d = self._drive()
        if not d:
            return

        def done(out):
            try:
                r = json.loads(out)
            except ValueError:
                r = {}
            n = sum(1 for x in r.get("done", []) if x[0] == "defrag")
            if any(x[0] == "trim" for x in r.get("done", [])):
                self.win.toast("TRIM 을 마쳤습니다 (SSD 가 지운 공간을 정리했습니다)")
            elif n or r.get("skipped_snapshotted"):
                skip = r.get("skipped_snapshotted", 0)
                self.win.toast(f"파일 {n}개의 조각을 모았습니다" + (f" · 시스템 영역 {skip}개는 그대로 두었습니다" if skip else ""))
            else:
                self.win.toast("조각 모음이 필요한 파일이 없습니다")
            self.refresh()
        self._run(["optimize", d["device"]], done, "최적화하는 중…")

    def do_scrub(self):
        d = self._drive()
        if not d:
            return
        self._run(["scrub", d["device"]], lambda _o: (self.win.toast("데이터 검사를 시작했습니다 — 끝나면 상태에 나옵니다"),
                                                      GLib.timeout_add_seconds(5, lambda: self.refresh() and False)),
                  "데이터 검사를 시작하는 중…")

    def do_nocow(self):
        f = self._sel_file()
        if not f:
            return
        folder = os.path.dirname(f["path"])
        self.win.confirm(
            "이 폴더를 쓰기 최적화할까요?",
            f"{folder}\n\n앞으로 이 폴더에 만드는 파일은 고쳐 쓸 때 조각나지 않습니다 (가상 머신 디스크·데이터베이스에 알맞음). "
            "대신 그 파일들은 압축과 데이터 검사(체크섬)를 하지 않습니다. 이미 있는 파일은 ‘파일 다시 쓰기’로 바꿀 수 있습니다.",
            "쓰기 최적화", lambda: self._run(["nocow", folder], lambda _o: self.win.toast("쓰기 최적화를 켰습니다"),
                                          "폴더 설정을 바꾸는 중…"))

    def do_rewrite(self):
        f = self._sel_file()
        if not f:
            return

        def done(_o):
            f["nocow"] = True
            self.win.toast("파일을 다시 써서 조각을 없앴습니다")
            self.do_analyze()
        self.win.confirm(
            "이 파일을 다시 쓸까요?",
            f"{f['path']} ({fmt_bytes(f['size'])})\n\n조각나지 않는 새 파일로 복사한 뒤 바꿔 끼웁니다. 파일 크기만큼 여유 공간이 "
            "잠깐 필요하고, 이 파일을 쓰는 프로그램(가상 머신 등)은 먼저 꺼야 합니다.",
            "다시 쓰기", lambda: self._run(["rewrite", f["path"]], done, "파일을 다시 쓰는 중… (크기에 따라 몇 분 걸립니다)"))


PAGE = {
    "id": "optimize",
    "title": "드라이브 최적화",
    "group": "storage",
    "order": 20,
    "icon": ["drive-harddisk-symbolic", "media-flash-symbolic"],
    "build": OptimizePage,
}
