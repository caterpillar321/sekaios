"""컴퓨터 관리 — 서비스 (systemd).

윈도우 services.msc 처럼 서비스를 보여 주고 시작·중지·다시 시작하고 시작 유형을 바꾼다.
모두 systemd 의 D-Bus(org.freedesktop.systemd1)를 비동기로 부른다.
  시스템 서비스  시스템 버스.   사용자 서비스  $XDG_RUNTIME_DIR/systemd/private (systemctl --user 와 같은 길),
                 없으면 세션 버스.
  목록  ListUnitsByPatterns(올라와 있는 유닛 — 상태) + ListUnitFilesByPatterns(설치된 파일 — 시작 유형).
        올라와 있지 않은 서비스의 이름(Description=)은 유닛 파일을 작업 스레드에서 읽는다 (읽은 것은 기억).
        템플릿(foo@.service)·별칭은 빼고, 템플릿에서 만든 인스턴스(getty@tty1)는 보인다.
  동작  StartUnit·StopUnit·RestartUnit 을 이 앱이 직접 부른다 (ALLOW_INTERACTIVE_AUTHORIZATION → polkit 창).
        polkit 의 auth_admin_keep 은 부른 프로세스 단위라, 한 번 인증하면 몇 분 동안 다시 묻지 않는다
        (systemctl 을 매번 띄우면 매번 묻는다). 결과는 JobRemoved 신호(done·failed…) — Subscribe 해야 온다.
  시작 유형  자동 = enable · 수동 = disable · 사용 안 함 = mask. 바꾼 뒤 Reload (systemctl 처럼).
        static(설치 정보 없음 — 다른 유닛이 필요할 때 시작)은 자동으로 바꿀 수 없다.
세션·로그인에 꼭 필요한 서비스(CRITICAL)를 중지·다시 시작·수동·사용 안 함으로 바꿀 때는 먼저 묻는다.
보일 때만 몇 초마다 새로 고친다.

다른 페이지에서: win.show_page("services", unit="ssh.service", scope="system"|"user") — 그 서비스를 골라 둔다.
"""
import fnmatch
import os
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..common import (ME_UID, DetailGrid, NoticeBar, SortHeaders, copy_text, dbus_error_name,  # noqa: E402
                      dbus_error_text, fmt_bytes, fmt_duration, fmt_time, gicon, key_is_menu, menu_item,
                      mk_view, open_location, popup, scrolled, text_column)

SD = "org.freedesktop.systemd1"
SD_PATH = "/org/freedesktop/systemd1"
SD_MGR = "org.freedesktop.systemd1.Manager"
PROPS = "org.freedesktop.DBus.Properties"
UNIT_IFACE = "org.freedesktop.systemd1.Unit"
SVC_IFACE = "org.freedesktop.systemd1.Service"
U64_MAX = 2 ** 64 - 1
REFRESH_SECS = 3
FILES_EVERY = 10          # 유닛 파일(시작 유형)은 열 번에 한 번 — 바꾸면 곧바로 다시 읽는다
AUTH_TIMEOUT = 300000     # 인증 창을 기다리는 동안 (ms)

# 멈추면 세션·로그인이 무너지는 것 — (유닛 이름 무늬, 무엇에 필요한지)
SYSTEM_CRITICAL = (
    ("greetd.service", "로그인 화면"), ("display-manager.service", "로그인 화면"),
    ("dbus.service", "프로그램 사이의 통신(바탕화면 전체)"), ("dbus-broker.service", "프로그램 사이의 통신(바탕화면 전체)"),
    ("systemd-logind.service", "로그인·세션 관리"), ("systemd-user-sessions.service", "로그인"),
    ("user@*.service", "로그인한 사용자의 세션"), ("getty@*.service", "콘솔 로그인"),
    ("polkit.service", "관리자 인증(이 창에서 다시 켜는 것도)"), ("systemd-udevd.service", "장치 인식"),
    ("systemd-journald.service", "시스템 기록"), ("NetworkManager.service", "네트워크 연결"),
    ("wpa_supplicant.service", "Wi-Fi 연결"), ("accounts-daemon.service", "사용자 계정 정보"),
    ("sekai-*.service", "SekaiOS 구성 요소"), ("ssh.service", "원격 접속(SSH)"),
)
USER_CRITICAL = (
    ("dbus.service", "프로그램 사이의 통신(바탕화면 전체)"), ("dbus-broker.service", "프로그램 사이의 통신(바탕화면 전체)"),
    ("pipewire*.service", "소리·화면 공유"), ("wireplumber.service", "소리"),
    ("xdg-desktop-portal*.service", "파일 열기 창·화면 공유"), ("xdg-document-portal.service", "파일 열기 창"),
    ("xdg-permission-store.service", "앱 권한"), ("at-spi-dbus-bus.service", "접근성"),
    ("gnome-keyring-daemon.service", "암호 보관함"), ("sekai-*.service", "SekaiOS 구성 요소"),
)

START_KO = {"enabled": "자동", "enabled-runtime": "자동", "linked": "수동", "linked-runtime": "수동",
            "alias": "수동", "static": "수동", "indirect": "수동", "disabled": "수동",
            "masked": "사용 안 함", "masked-runtime": "사용 안 함",
            "generated": "생성됨", "transient": "임시", "bad": "오류"}
START_NOTE = {"static": "다른 서비스가 필요할 때 시작", "indirect": "다른 유닛을 통해 켜짐",
              "enabled-runtime": "이번 부팅에만 자동", "masked-runtime": "이번 부팅에만 사용 안 함",
              "generated": "자동으로 만들어진 유닛", "transient": "잠깐 만들어진 유닛", "bad": "유닛 파일 오류"}
TRIGGER_NOTE = {"socket": "요청이 오면 시작", "timer": "예약된 때 시작", "path": "파일이 바뀌면 시작"}
RESULT_KO = {"exit-code": "오류 코드로 끝남", "signal": "신호를 받고 끝남", "core-dump": "비정상 종료(코어 덤프)",
             "timeout": "시간 초과", "watchdog": "응답 없음(watchdog)", "start-limit-hit": "너무 자주 다시 시작해 멈춤",
             "resources": "자원 부족", "protocol": "프로토콜 오류", "oom-kill": "메모리 부족으로 강제 종료",
             "exec-condition": "실행 조건이 맞지 않음"}
VERB = {"StartUnit": ("시작", "시작했습니다"), "StopUnit": ("중지", "중지했습니다"),
        "RestartUnit": ("다시 시작", "다시 시작했습니다")}
KIND_KO = {"auto": "자동", "manual": "수동", "disabled": "사용 안 함"}
# systemd 가 돌려주는 D-Bus 오류 → 사람의 말
SD_ERRORS = {
    "org.freedesktop.systemd1.UnitMasked": "사용 안 함으로 설정된 서비스입니다. 시작 유형을 먼저 바꾸세요.",
    "org.freedesktop.systemd1.OnlyByDependency": "이 서비스는 직접 시작·중지할 수 없습니다 (다른 서비스가 필요할 때만).",
    "org.freedesktop.systemd1.NoSuchUnit": "없는 서비스입니다.",
    "org.freedesktop.systemd1.LoadFailed": "서비스 설정을 읽지 못했습니다.",
    "org.freedesktop.systemd1.JobTypeNotApplicable": "이 서비스에는 할 수 없는 동작입니다.",
    "org.freedesktop.systemd1.TransactionIsDestructive": "진행 중인 다른 작업과 겹쳐 할 수 없습니다.",
    "org.freedesktop.systemd1.UnitLinked": "유닛 파일이 다른 곳에 연결되어 있어 바꿀 수 없습니다.",
}


def sd_error_text(err):
    if isinstance(err, str):
        return err
    return SD_ERRORS.get(dbus_error_name(err)) or dbus_error_text(err)


def status_of(active, sub):
    """(보일 글, 정렬 순서) — 실패가 맨 앞"""
    if active == "failed":
        return "실패", 0
    if active == "activating":
        return ("다시 시작 대기" if sub == "auto-restart" else "시작 중"), 1
    if active == "active":
        return ("완료됨", 3) if sub == "exited" else ("실행 중", 2)
    if active == "reloading":
        return "다시 읽는 중", 2
    if active == "deactivating":
        return "중지 중", 4
    if active == "maintenance":
        return "유지 보수 중", 4
    if active == "refreshing":
        return "새로 고치는 중", 2
    return "중지됨", 5


def template_of(name):
    """getty@tty1.service → getty@.service"""
    at = name.find("@")
    if at < 0:
        return None
    dot = name.rfind(".")
    return name[:at + 1] + name[dot:]


def critical_reason(name, user):
    for pat, why in USER_CRITICAL if user else SYSTEM_CRITICAL:
        if fnmatch.fnmatchcase(name, pat):
            return why
    return None


def read_description(path, name, user):
    """유닛 파일의 [Unit] Description= (작업 스레드). 가려진(/dev/null) 것은 원래 파일에서"""
    cands = [path] if path else []
    base = ("/usr/lib/systemd/user", "/lib/systemd/user") if user else \
        ("/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system")
    cands += [os.path.join(d, name) for d in base]
    for p in cands:
        try:
            if os.path.realpath(p) == "/dev/null":
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                sect = None
                for line in f:
                    s = line.strip()
                    if s.startswith("["):
                        sect = s
                    elif sect == "[Unit]" and s.startswith("Description="):
                        return s.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


# ── systemd D-Bus ────────────────────────────────────────────
class Manager:
    """한 systemd(시스템 또는 내 사용자 세션) — 모든 호출은 비동기, 결과는 메인 스레드에서"""

    def __init__(self, user):
        self.user = user
        self.conn = None
        self.bus_name = SD                     # 사용자 private 소켓(1:1 연결)이면 None
        self.error = None
        self._waiting = []
        self._connecting = False
        self._subscribed = False
        self._jobs = {}                        # 작업 경로 → 끝나면 부를 것
        self._finished = {}                    # 답보다 신호가 먼저 온 작업의 결과
        self.on_change = None                  # 작업이 끝나거나 유닛 파일이 바뀌면

    def ready(self, cb):
        """연결되면 cb(True), 못 하면 cb(False) — self.error 에 이유"""
        if self.conn is not None and not self.conn.is_closed():
            cb(True)
            return
        self.conn = None
        self._waiting.append(cb)
        if self._connecting:
            return
        self._connecting = True
        self._subscribed = False
        if self.user:
            rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{ME_UID}"
            sock = os.path.join(rt, "systemd", "private")
            if os.path.exists(sock):
                addr = "unix:path=" + Gio.dbus_address_escape_value(sock)
                Gio.DBusConnection.new_for_address(addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT,
                                                   None, None, self._got_private)
                return
            Gio.bus_get(Gio.BusType.SESSION, None, self._got_bus)
        else:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._got_bus)

    def _got_private(self, _src, res):
        try:
            conn = Gio.DBusConnection.new_for_address_finish(res)
        except GLib.Error:
            Gio.bus_get(Gio.BusType.SESSION, None, self._got_bus)     # 소켓이 안 되면 세션 버스로
            return
        self._connected(conn, None)

    def _got_bus(self, _src, res):
        try:
            conn = Gio.bus_get_finish(res)
        except GLib.Error as e:
            self._connected(None, e)
            return
        self._connected(conn, SD)

    def _connected(self, conn, bus_name):
        self._connecting = False
        self.conn, self.bus_name = conn, bus_name
        self.error = None
        if conn is None:
            self.error = "서비스 관리자에 연결할 수 없습니다"
        else:
            conn.set_exit_on_close(False)
            for sig, fn in (("JobRemoved", self._on_job_removed), ("UnitFilesChanged", self._on_files),
                            ("Reloading", self._on_reloading)):
                conn.signal_subscribe(bus_name, SD_MGR, sig, SD_PATH, None, Gio.DBusSignalFlags.NONE, fn)
        waiting, self._waiting = self._waiting, []
        for cb in waiting:
            cb(conn is not None)

    def call(self, method, args, rtype, done, interactive=False, path=SD_PATH, iface=SD_MGR, timeout=25000):
        """done(풀어낸 결과 튜플 또는 None, 오류(GLib.Error·글) 또는 None)"""
        def go(ok):
            if not ok:
                done(None, self.error)
                return
            flags = Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION if interactive else Gio.DBusCallFlags.NONE
            self.conn.call(self.bus_name, path, iface, method, args,
                           GLib.VariantType.new(rtype) if rtype else None, flags,
                           AUTH_TIMEOUT if interactive else timeout, None, fin)

        def fin(conn, res):
            try:
                v = conn.call_finish(res)
            except GLib.Error as e:
                done(None, e)
                return
            done(v.unpack() if v is not None else (), None)
        self.ready(go)

    def job(self, method, unit, done):
        """StartUnit·StopUnit·RestartUnit — 작업이 끝나면 done(결과: done·failed·…, 오류)"""
        def start(*_):
            self.call(method, GLib.Variant("(ss)", (unit, "replace")), "(o)", got, interactive=True)

        def got(res, err):
            if err is not None:
                done(None, err)
                return
            path = res[0]
            r = self._finished.pop(path, None)
            if r is not None:
                done(r, None)
            else:
                self._jobs[path] = lambda result: done(result, None)
        if self._subscribed:
            start()
        else:                                     # 구독해야 JobRemoved 가 온다 (이미 구독이면 오류 — 상관없다)
            self._subscribed = True
            self.call("Subscribe", None, None, start)

    def _on_job_removed(self, _c, _s, _p, _i, _n, params, *_):
        try:
            _id, job, _unit, result = params.unpack()
        except (TypeError, ValueError):
            return
        cb = self._jobs.pop(job, None)
        if cb is not None:
            cb(result)
        else:
            if len(self._finished) > 256:
                self._finished.clear()
            self._finished[job] = result
        if self.on_change:
            self.on_change(False)

    def _on_files(self, *_):
        if self.on_change:
            self.on_change(True)

    def _on_reloading(self, _c, _s, _p, _i, _n, params, *_):
        if not params.unpack()[0] and self.on_change:
            self.on_change(True)

    def file_ops(self, steps, done):
        """유닛 파일 바꾸기 [(메서드, 인자 Variant, 답 형식)] 를 차례로 → Reload. done(마지막 결과들, 오류)"""
        results = []

        def step(i):
            if i == len(steps):
                self.call("Reload", None, None, lambda _r, e: done(results, e), interactive=True)
                return
            method, args, rtype = steps[i]

            def fin(res, err):
                if err is not None:
                    done(results, err)
                    return
                results.append((method, res))
                step(i + 1)
            self.call(method, args, rtype, fin, interactive=True)
        step(0)

    def props(self, unit, opath, done):
        """Unit·Service 속성을 합친 dict (없으면 {})"""
        def with_path(path):
            res, left = {}, [2]

            def one(iface):
                def fin(r, _e):
                    if r:
                        res.update(r[0])
                    left[0] -= 1
                    if left[0] == 0:
                        done(res)
                self.call("GetAll", GLib.Variant("(s)", (iface,)), "(a{sv})", fin, path=path, iface=PROPS)
            one(UNIT_IFACE)
            one(SVC_IFACE)
        if opath and opath != "/":
            with_path(opath)
        else:                                     # 올라와 있지 않다 — 불러온다 (systemctl status 와 같다)
            self.call("LoadUnit", GLib.Variant("(s)", (unit,)), "(o)",
                      lambda r, _e: with_path(r[0]) if r else done({}))


# ── 화면 ─────────────────────────────────────────────────────
(S_NAME, S_TITLE, S_STATUS, S_SICON, S_SRANK, S_START, S_NOTE, S_WEIGHT, S_SEARCH) = range(9)
S_TYPES = (str, str, str, GObject.Object, int, str, str, int, str)


class ServicesPage:
    searchable = True
    search_hint = "서비스 이름·설명으로 검색"

    def __init__(self, win):
        self.win = win
        self.st = win.page_state("services")
        self.busy = False
        self.visible = False
        self.user = self.st.get("scope") == "user"
        self.mgrs = {False: Manager(False), True: Manager(True)}
        for u, m in self.mgrs.items():
            m.on_change = lambda files, u=u: self._changed(u, files)
        self.units = {}                          # 이름 → dict
        self.index = {}                          # 이름 → 저장소 행
        self._files = None                       # 유닛 파일 [(경로, 상태)] (지금 보는 범위)
        self._descs = {}                         # (범위, 경로) → Description
        self._asked = set()
        self._tick_n = 0
        self._timer = 0
        self._listing = False
        self._again = False
        self._gen = 0
        self._dgen = 0
        self._jobs = 0
        self._pending_select = None
        self.query = ""
        self.failed_only = False
        self._err_icon = gicon(["dialog-error", "dialog-error-symbolic"])
        self._syncing = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.notice = NoticeBar()
        outer.pack_start(self.notice, False, False, 0)
        outer.pack_start(self._build_toolbar(), False, False, 0)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(self.paned, True, True, 0)
        self.paned.pack1(self._build_list(), True, False)
        self.paned.pack2(self._build_details(), False, False)
        pos = self.st.get("paned")
        self._paned_pos = int(pos) if isinstance(pos, int) and pos > 120 else None
        self._placed = False
        self.paned.connect("size-allocate", self._first_alloc)
        self.paned.connect("notify::position", lambda p, _s: self.st.__setitem__("paned", p.get_position()))
        self.widget = outer
        self._build_actions()
        self._sync_buttons()

    # ── 만들기 ──
    def _build_toolbar(self):
        row = Gtk.Box(spacing=8)
        row.get_style_context().add_class("adm-filters")
        seg = Gtk.Box(spacing=0)
        seg.get_style_context().add_class("linked")
        seg.get_style_context().add_class("adm-seg")
        self.b_sys = Gtk.ToggleButton(label="시스템 서비스")
        self.b_usr = Gtk.ToggleButton(label="사용자 서비스")
        self.b_sys.set_tooltip_text("컴퓨터 전체의 서비스 — 바꾸려면 관리자 인증이 필요합니다")
        self.b_usr.set_tooltip_text("내 로그인 세션에서 도는 서비스 (systemctl --user)")
        for b, u in ((self.b_sys, False), (self.b_usr, True)):
            b.set_active(u == self.user)
            b.connect("toggled", self._on_seg, u)
            seg.pack_start(b, False, False, 0)
        row.pack_start(seg, False, False, 0)
        self.count = Gtk.Label(xalign=0)
        self.count.get_style_context().add_class("adm-sum")
        row.pack_end(self.count, False, False, 0)
        self.fail_btn = Gtk.ToggleButton()
        self.fail_btn.get_style_context().add_class("adm-chip")
        self.fail_btn.get_style_context().add_class("err")
        self.fail_btn.set_tooltip_text("실패한 서비스만 보기")
        self.fail_btn.set_no_show_all(True)
        self.fail_btn.connect("toggled", lambda b: self._set_failed_only(b.get_active()))
        row.pack_end(self.fail_btn, False, False, 0)
        return row

    def _build_list(self):
        self.store = Gtk.ListStore(*S_TYPES)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.sort = Gtk.TreeModelSort(model=self.filter)
        v = self.view = mk_view(self.sort)
        cols = [text_column("이름", S_TITLE, width=250, expand=True, weight=S_WEIGHT),
                text_column("상태", S_STATUS, width=110, weight=S_WEIGHT, icon=S_SICON),
                text_column("시작 유형", S_START, width=90),
                text_column("유닛", S_NAME, width=200),
                text_column("참고", S_NOTE, width=180)]
        for c in cols:
            v.append_column(c)
        # 상태 칸의 아이콘 자리 — 대부분 비어 있어 목록이 첫 줄로 잰 폭이 0 이 되면 실패 아이콘이 잘린다
        cols[1].get_cells()[0].set_fixed_size(24, -1)
        self.sorter = SortHeaders(self.sort, self.win.sort_saver("services"))
        for c, sid in zip(cols, (S_TITLE, S_SRANK, S_START, S_NAME, S_NOTE)):
            self.sorter.add(c, sid)
        sid, order = self.win.saved_sort("services", S_TITLE, Gtk.SortType.ASCENDING)
        self.sorter.set(sid, order)
        v.get_selection().connect("changed", lambda *_: self._on_select())
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.pack_start(scrolled(v), True, True, 0)
        self.empty = Gtk.Label(xalign=0)
        self.empty.get_style_context().add_class("adm-empty")
        self.empty.set_no_show_all(True)
        box.pack_start(self.empty, False, False, 0)
        return box

    def _build_details(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("adm-details")
        box.set_size_request(-1, 150)
        head = Gtk.Box(spacing=8)
        self.d_title = Gtk.Label(xalign=0)
        self.d_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.d_title.get_style_context().add_class("adm-dtitle")
        head.pack_start(self.d_title, True, True, 0)
        self.d_file = Gtk.Button(label="파일 위치 열기")
        self.d_file.connect("clicked", lambda *_: self._open_file())
        head.pack_end(self.d_file, False, False, 0)
        self.d_logs = Gtk.Button(label="로그 보기")
        self.d_logs.set_tooltip_text("이벤트 뷰어에서 이 서비스의 기록만 봅니다")
        self.d_logs.connect("clicked", lambda *_: self.show_logs())
        head.pack_end(self.d_logs, False, False, 0)
        box.pack_start(head, False, False, 0)
        self.d_grid = DetailGrid(key_width=120)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.d_grid)
        box.pack_start(sc, True, True, 0)
        self._props = {}
        return box

    def _build_actions(self):
        a = self.actions = Gtk.Box(spacing=8)
        self.b_start = Gtk.Button(label="시작")
        self.b_stop = Gtk.Button(label="중지")
        self.b_restart = Gtk.Button(label="다시 시작")
        for b, m in ((self.b_start, "StartUnit"), (self.b_stop, "StopUnit"), (self.b_restart, "RestartUnit")):
            b.connect("clicked", lambda _b, m=m: self.do_job(m))
            a.pack_start(b, False, False, 0)
        self.b_type = Gtk.MenuButton()
        bx = Gtk.Box(spacing=6)
        bx.pack_start(Gtk.Label(label="시작 유형"), False, False, 0)
        bx.pack_start(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        self.b_type.add(bx)
        self.type_menu = Gtk.Menu()
        self.type_items = {}
        for kind in ("auto", "manual", "disabled"):
            it = Gtk.CheckMenuItem(label=KIND_KO[kind])
            it.set_draw_as_radio(True)
            it.connect("activate", lambda _i, k=kind: None if self._syncing else self.set_start_type(k))
            self.type_menu.append(it)
            self.type_items[kind] = it
        self.type_menu.show_all()
        self.b_type.set_popup(self.type_menu)
        a.pack_start(self.b_type, False, False, 0)
        b = Gtk.Button(label="새로 고침")
        b.set_tooltip_text("F5")
        b.connect("clicked", lambda *_: self.refresh())
        a.pack_start(b, False, False, 0)

    def _first_alloc(self, p, alloc):
        if self._placed or alloc.height < 200:
            return
        self._placed = True
        pos = self._paned_pos or int(alloc.height * 0.66)
        GLib.idle_add(lambda: (p.set_position(min(pos, alloc.height - 150)), False)[1])

    # ── 페이지 규칙 ──
    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q != self.query:
            self.query = q
            self.filter.refilter()
            self._update_count()

    def on_show(self, unit=None, scope=None, **_kw):
        self.visible = True
        if unit and scope is None:
            scope = "system"
        if scope in ("system", "user") and (scope == "user") != self.user:
            self._set_scope(scope == "user", load=False)
        if unit:
            self._pending_select = str(unit)
            if self.query:
                self.win.set_search("")           # 찾던 글자 때문에 그 서비스가 가려지지 않게
        self._load(files=True)
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(REFRESH_SECS, self._tick)

    def on_hide(self):
        self.visible = False
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def refresh(self):
        self._load(files=True)

    # ── 범위 · 거르기 ──
    def _on_seg(self, btn, user):
        if self._syncing:
            return
        if btn.get_active():
            self._set_scope(user)
        elif user == self.user:                   # 지금 범위를 다시 눌렀다 — 눌린 채로 둔다
            self._syncing = True
            btn.set_active(True)
            self._syncing = False

    def _set_scope(self, user, load=True):
        self._syncing = True
        self.b_sys.set_active(not user)
        self.b_usr.set_active(user)
        self._syncing = False
        if user == self.user and self.units:
            return
        self.user = user
        self.st["scope"] = "user" if user else "system"
        self._gen += 1
        self._listing = False
        self._files = None
        self.units, self.index = {}, {}
        self.store.clear()
        self.notice.hide_notice()
        self._show_details()
        if load:
            self._load(files=True)

    def _set_failed_only(self, on):
        self.failed_only = on
        self.filter.refilter()
        self._update_count()

    def _visible(self, model, it, _d):
        if self.failed_only and model.get_value(it, S_SRANK) != 0:
            return False
        return not self.query or self.query in (model.get_value(it, S_SEARCH) or "")

    # ── 읽기 ──
    def mgr(self):
        return self.mgrs[self.user]

    def _tick(self):
        if not self.visible:
            self._timer = 0
            return False
        self._tick_n += 1
        self._load(files=self._tick_n % FILES_EVERY == 0)
        return True

    def _changed(self, user, files):
        """작업이 끝났거나 유닛 파일이 바뀌었다 (systemd 신호)"""
        if user == self.user and self.visible:
            self._load(files=files)

    def _load(self, files=False):
        if self._listing:
            self._again = self._again or files
            return
        self._listing = True
        self._again = False
        gen = self._gen
        user = self.user
        got = {"units": None, "files": None if (files or self._files is None) else self._files}

        def done_units(res, err):
            if gen != self._gen:
                return
            if err is not None:
                self._failed(err)
                return
            got["units"] = res[0]
            merge()

        def done_files(res, err):
            if gen != self._gen:
                return
            got["files"] = res[0] if err is None else []
            merge()

        def merge():
            if got["units"] is None or got["files"] is None:
                return
            self._listing = False
            self._files = got["files"]
            self._merge(user, got["units"], got["files"])
            if self._again:
                self._load(files=True)

        m = self.mgr()
        m.call("ListUnitsByPatterns", GLib.Variant("(asas)", ([], ["*.service", "*.socket", "*.timer", "*.path"])),
               "(a(ssssssouso))", done_units)
        if got["files"] is None:
            m.call("ListUnitFilesByPatterns", GLib.Variant("(asas)", ([], ["*.service"])), "(a(ss))", done_files)

    def _failed(self, err):
        self._listing = False
        what = "사용자 서비스" if self.user else "시스템 서비스"
        self.notice.show_notice(f"{what} 목록을 읽지 못했습니다: {sd_error_text(err)}",
                                [("다시 시도", self.refresh)], kind="error")
        if not self.units:
            self._update_count()

    def _merge(self, user, loaded, files):
        self.notice.hide_notice()
        fstate = {}
        for path, state in files:
            fstate[os.path.basename(path)] = (state, path)
        # 소켓·타이머·경로 유닛이 기다리는 서비스 — "요청이 오면 시작" 같은 참고
        triggers = {}
        for name, _d, load, active, _sub, *_rest in loaded:
            kind = name.rsplit(".", 1)[-1]
            if kind in TRIGGER_NOTE and load == "loaded" and active == "active":
                triggers.setdefault(name.rsplit(".", 1)[0] + ".service", kind)
        units = {}
        for name, desc, load, active, sub, _follow, opath, _jid, jtype, _jpath in loaded:
            if not name.endswith(".service"):
                continue
            if load == "not-found" and active == "inactive":
                continue                          # 다른 유닛이 이름만 부르는 것 — 설치되지 않았다
            st, fpath = fstate.get(name) or fstate.get(template_of(name) or "") or (None, None)
            if load == "masked":
                st = st or "masked"
            units[name] = {"name": name, "desc": desc, "load": load, "active": active, "sub": sub,
                           "file": st, "path": fpath, "opath": opath, "job": jtype}
        missing = []
        for fname, (st, path) in fstate.items():
            if fname in units or st == "alias" or fname.endswith("@.service"):
                continue
            desc = self._descs.get((user, path))
            if desc is None:
                missing.append((path, fname))
            units[fname] = {"name": fname, "desc": desc or "", "load": "", "active": "inactive", "sub": "dead",
                            "file": st, "path": path, "opath": None, "job": ""}
        for name, u in units.items():
            note = START_NOTE.get(u["file"] or "", "")
            trig = triggers.get(name)
            if trig and u["active"] != "active":
                note = TRIGGER_NOTE[trig]
            if u["load"] in ("error", "bad-setting"):
                note = "설정 오류 — 불러오지 못함"
            if u["job"]:
                note = {"start": "시작 대기 중", "stop": "중지 대기 중", "restart": "다시 시작 대기 중"}.get(u["job"], note)
            u["note"] = note
        self.units = units
        self._apply_rows()
        self._ask_descs(user, missing)
        if self._pending_select and self._pending_select in self.index:
            self.select(self._pending_select)
            self._pending_select = None
        elif self._pending_select:
            self._pending_select = None
            self.win.toast("그 서비스는 이 목록에 없습니다")
        self._sync_buttons()
        if self.visible:
            self._refresh_details()

    def _row_values(self, u):
        status, rank = status_of(u["active"], u["sub"])
        title = u["desc"] or u["name"]
        start = START_KO.get(u["file"] or "", "—")
        failed = rank == 0
        return {S_NAME: u["name"], S_TITLE: title, S_STATUS: status, S_SICON: self._err_icon if failed else None,
                S_SRANK: rank, S_START: start, S_NOTE: u["note"],
                S_WEIGHT: Pango.Weight.BOLD if failed else Pango.Weight.NORMAL,
                S_SEARCH: f"{title}\n{u['name']}\n{u['note']}".casefold()}

    def _apply_rows(self):
        """바뀐 칸만 고친다 — 선택·스크롤이 그대로 남게"""
        for name in [n for n in self.index if n not in self.units]:
            self.store.remove(self.index.pop(name))
        for name, u in self.units.items():
            vals = self._row_values(u)
            it = self.index.get(name)
            if it is None:
                self.index[name] = self.store.append([vals[i] for i in range(len(S_TYPES))])
                continue
            cols, new = [], []
            for c, v in vals.items():
                if self.store.get_value(it, c) != v:
                    cols.append(c)
                    new.append(v)
            if cols:
                self.store.set(it, cols, new)
        self._update_count()

    def _ask_descs(self, user, missing):
        missing = [(p, n) for p, n in missing if (user, p) not in self._asked]
        if not missing:
            return
        self._asked.update((user, p) for p, _n in missing)

        def work():
            return {p: read_description(p, n, user) for p, n in missing}

        def done(res, exc):
            if exc is not None or not res:
                return
            for p, d in res.items():
                self._descs[(user, p)] = d
            if user != self.user:
                return
            changed = False
            for u in self.units.values():
                if not u["desc"] and u["path"] in res and res[u["path"]]:
                    u["desc"] = res[u["path"]]
                    changed = True
            if changed:
                self._apply_rows()
                self._show_details()
        self.win.run_thread(work, done)

    def _update_count(self):
        n = len(self.units)
        failed = sum(1 for u in self.units.values() if u["active"] == "failed")
        shown = self.sort.iter_n_children(None)
        s = f"서비스 {n:,}개"
        if shown != n:
            s = f"{shown:,}개 표시 (전체 {n:,}개)"
        self.count.set_text(s)
        self.fail_btn.set_label(f"실패 {failed}개")
        self.fail_btn.set_visible(failed > 0 or self.failed_only)
        if not failed and self.failed_only:
            self.fail_btn.set_active(False)
        if n and not shown:
            self.empty.set_text("조건에 맞는 서비스가 없습니다.")
        elif not n and not self._listing:
            self.empty.set_text("서비스가 없습니다.")
        self.empty.set_visible(shown == 0 and (n > 0 or not self._listing))

    # ── 고르기 ──
    def selected(self):
        model, it = self.view.get_selection().get_selected()
        if it is None:
            return None
        return self.units.get(model.get_value(it, S_NAME))

    def select(self, name):
        for row in self.sort:
            if row[S_NAME] == name:
                path = row.path
                self.view.get_selection().select_path(path)
                # 방금 채운 목록은 아직 크기가 정해지지 않았다 — 배치가 끝난 뒤 스크롤
                GLib.idle_add(lambda: (self.view.scroll_to_cell(path, None, True, 0.3, 0), False)[1])
                self.view.grab_focus()
                return True
        return False

    def _on_select(self):
        self._sync_buttons()
        self._props = {}
        self._show_details()
        self._refresh_details()

    def _sync_buttons(self):
        u = self.selected()
        masked = bool(u) and (u["load"] == "masked" or (u["file"] or "").startswith("masked"))
        active = u["active"] if u else ""
        self.b_start.set_sensitive(bool(u) and not masked and active in ("inactive", "failed", "deactivating"))
        self.b_stop.set_sensitive(bool(u) and active in ("active", "activating", "reloading", "refreshing"))
        self.b_restart.set_sensitive(bool(u) and not masked)
        self.b_start.set_tooltip_text("사용 안 함으로 설정되어 있어 시작할 수 없습니다 — 시작 유형을 먼저 바꾸세요"
                                      if masked else None)
        st = (u["file"] or "") if u else ""
        can_type = bool(u) and st in ("enabled", "enabled-runtime", "disabled", "masked", "masked-runtime",
                                      "static", "indirect", "linked", "linked-runtime")
        self.b_type.set_sensitive(can_type)
        self.b_type.set_tooltip_text(None if can_type or not u else
                                     "이 서비스는 시작 유형을 바꿀 수 없습니다 (자동으로 만들어졌거나 임시 유닛)")
        cur = {"자동": "auto", "수동": "manual", "사용 안 함": "disabled"}.get(START_KO.get(st, ""))
        self._syncing = True
        for kind, it in self.type_items.items():
            it.set_active(kind == cur)
        self.type_items["auto"].set_sensitive(st not in ("static", "indirect"))
        self.type_items["auto"].set_tooltip_text("설치 정보([Install])가 없어 자동으로 켤 수 없습니다"
                                                 if st in ("static", "indirect") else None)
        self._syncing = False

    # ── 세부 ──
    def _refresh_details(self):
        u = self.selected()
        if u is None:
            return
        self._dgen += 1
        gen = self._dgen
        name = u["name"]

        def done(props):
            if gen != self._dgen or not self.selected() or self.selected()["name"] != name:
                return
            self._props = props or {}
            self._show_details()
        self.mgr().props(name, u["opath"], done)

    def _show_details(self):
        u = self.selected()
        self.d_logs.set_sensitive(u is not None)
        if u is None:
            self.d_title.set_text("서비스를 고르면 여기에 자세한 내용이 나옵니다.")
            self.d_grid.set_rows([])
            self.d_file.set_sensitive(False)
            return
        p = self._props
        self.d_title.set_text(u["desc"] or u["name"])
        self.d_grid.set_rows(self._detail_rows(u, p))
        path = p.get("FragmentPath") or u["path"]
        self.d_file.set_sensitive(bool(path) and os.path.exists(path) and os.path.realpath(path) != "/dev/null")

    def _detail_rows(self, u, p):
        status, _r = status_of(p.get("ActiveState", u["active"]), p.get("SubState", u["sub"]))
        res = p.get("Result")
        if res and res != "success" and p.get("ActiveState") == "failed":
            status += f" — {RESULT_KO.get(res, res)}"
        fst = p.get("UnitFileState") or u["file"] or ""
        start = START_KO.get(fst, fst or "—")
        preset = p.get("UnitFilePreset")
        if preset in ("enabled", "disabled"):
            start += f" (기본값: {'자동' if preset == 'enabled' else '수동'})"
        if u["note"]:
            start += f" · {u['note']}"
        cond = None
        if p.get("ConditionResult") is False and p.get("ConditionTimestamp"):
            cond = "조건이 맞지 않아 시작하지 않았습니다"
        exe = None
        es = p.get("ExecStart")
        if es:
            try:
                exe = " ".join(es[0][1]) if es[0][1] else es[0][0]     # systemctl show 처럼 (따옴표 없이)
            except (IndexError, TypeError):
                exe = None
        pid = p.get("MainPID") or None
        since = None
        t = p.get("ActiveEnterTimestamp") or 0
        if t and p.get("ActiveState") in ("active", "reloading"):
            since = f"{fmt_time(t / 1e6)} ({fmt_duration(time.time() - t / 1e6)} 전)"
        mem = p.get("MemoryCurrent")
        mem = fmt_bytes(mem) if isinstance(mem, int) and mem != U64_MAX and pid else None
        cpu = p.get("CPUUsageNSec")
        cpu = f"{cpu / 1e9:.1f}초" if isinstance(cpu, int) and cpu != U64_MAX and cpu > 0 else None
        tasks = p.get("TasksCurrent")
        tasks = tasks if isinstance(tasks, int) and tasks != U64_MAX and pid else None

        def names(key, limit=6):
            v = [x for x in (p.get(key) or []) if isinstance(x, str)]
            if not v:
                return None
            return ", ".join(v[:limit]) + (f" 외 {len(v) - limit}개" if len(v) > limit else "")
        by = ", ".join(x for x in (names("WantedBy"), names("RequiredBy")) if x) or None
        return [("이름", u["desc"] or u["name"]),
                ("유닛", u["name"]),
                ("상태", status),
                ("시작 유형", start),
                ("시작 조건", cond),
                ("시작하게 하는 것", names("TriggeredBy")),
                ("실행 명령", exe),
                ("실행 계정", p.get("User") or (None if self.user or not p else "root")),
                ("기본 PID", pid),
                ("시작 시각", since),
                ("메모리", mem),
                ("CPU 시간", cpu),
                ("작업 수", tasks),
                ("다시 시작 횟수", p.get("NRestarts") or None),
                ("유닛 파일", p.get("FragmentPath") or u["path"]),
                ("덧붙인 설정", ", ".join(p.get("DropInPaths") or []) or None),
                ("필요로 함", names("Requires")),
                ("함께 켜는 것", names("Wants")),
                ("이 서비스를 켜는 것", by),
                ("문서", names("Documentation", 3))]

    def show_logs(self):
        u = self.selected()
        if u is not None:
            self.win.show_page("events", unit=u["name"], scope="user" if self.user else "system")

    def _open_file(self):
        u = self.selected()
        if u is not None:
            open_location(self._props.get("FragmentPath") or u["path"])

    # ── 동작 ──
    def _set_busy(self, delta):
        self._jobs = max(0, self._jobs + delta)
        self.busy = self._jobs > 0
        self.win.busy_changed()

    def _guard(self, u, what, go):
        """세션에 꼭 필요한 서비스면 먼저 묻는다"""
        why = critical_reason(u["name"], self.user)
        if not why:
            go()
            return
        title = u["desc"] or u["name"]
        self.win.confirm(f"'{title}'을(를) {what}할까요?",
                         f"이 서비스는 {why}에 필요합니다. {what}하면 화면이 멈추거나 로그아웃되거나 연결이 끊길 수 "
                         "있고, 저장하지 않은 작업을 잃을 수 있습니다.\n그래도 계속할까요?", what, go)

    def do_job(self, method):
        u = self.selected()
        if u is None:
            return
        verb, did = VERB[method]
        name, title, user = u["name"], u["desc"] or u["name"], self.user

        def go():
            self._set_busy(1)
            self.win.toast(f"'{title}' {verb} 중…", 30)

            def done(result, err):
                self._set_busy(-1)
                self._load()
                if err is not None:
                    self._report(f"'{title}'을(를) {verb}하지 못했습니다", sd_error_text(err), name, user)
                elif result == "done":
                    self.win.toast(f"'{title}'을(를) {did}")
                elif result in ("failed", "dependency", "timeout"):
                    why = {"failed": "서비스가 오류로 끝났습니다.",
                           "dependency": "먼저 켜져야 하는 다른 서비스가 시작되지 않았습니다.",
                           "timeout": "정해진 시간 안에 끝나지 않았습니다."}[result]
                    self._report(f"'{title}'을(를) {verb}하지 못했습니다", why + " 로그에서 이유를 확인할 수 있습니다.",
                                 name, user)
                elif result == "canceled":
                    self.win.toast(f"'{title}' {verb} 작업이 다른 작업에 밀려 취소되었습니다")
                else:
                    self.win.toast(f"'{title}': {verb} — {result}")
            self.mgrs[user].job(method, name, done)
        if method in ("StopUnit", "RestartUnit"):
            self._guard(u, verb, go)
        else:
            go()

    def set_start_type(self, kind):
        u = self.selected()
        if u is None:
            return
        st = u["file"] or ""
        name, title, user = u["name"], u["desc"] or u["name"], self.user
        runtime = st.endswith("-runtime")
        V = GLib.Variant
        steps = []
        GLib.idle_add(lambda: (self._sync_buttons(), False)[1])     # 메뉴의 점은 실제 상태로 (취소해도 맞게)
        masked = st.startswith("masked") or u["load"] == "masked"
        enabled = st.startswith("enabled") or st.startswith("linked")
        if kind == "auto":
            if st in ("static", "indirect"):
                self.win.notice("자동으로 바꿀 수 없습니다",
                                "이 서비스에는 설치 정보([Install])가 없어 다른 서비스가 필요할 때만 시작됩니다.")
                return
            if masked:
                steps.append(("UnmaskUnitFiles", V("(asb)", ([name], runtime)), "(a(sss))"))
            steps.append(("EnableUnitFiles", V("(asbb)", ([name], False, False)), "(ba(sss))"))
        elif kind == "manual":
            if masked:
                steps.append(("UnmaskUnitFiles", V("(asb)", ([name], runtime)), "(a(sss))"))
            if enabled:
                steps.append(("DisableUnitFiles", V("(asb)", ([name], runtime)), "(a(sss))"))
        elif kind == "disabled":
            if masked:
                return
            if enabled:
                steps.append(("DisableUnitFiles", V("(asb)", ([name], runtime)), "(a(sss))"))
            steps.append(("MaskUnitFiles", V("(asbb)", ([name], False, False)), "(a(sss))"))
        if not steps:
            self._sync_buttons()
            return

        def go():
            self._set_busy(1)

            def done(results, err):
                self._set_busy(-1)
                self._load(files=True)
                if err is not None:
                    self._report(f"'{title}'의 시작 유형을 바꾸지 못했습니다", sd_error_text(err), name, user,
                                 logs=False)
                    return
                for method, res in results:
                    if method == "EnableUnitFiles" and res and not res[0] and not res[1]:
                        self.win.notice("자동으로 바꿀 수 없습니다",
                                        "이 서비스에는 설치 정보([Install])가 없어 자동으로 켤 수 없습니다.")
                        return
                msg = f"'{title}'의 시작 유형을 '{KIND_KO[kind]}'(으)로 바꿨습니다"
                if kind == "disabled" and u["active"] in ("active", "activating"):
                    msg += " — 지금 실행 중인 것은 [중지]를 눌러야 멈춥니다"
                self.win.toast(msg, 7)
            self.mgrs[user].file_ops(steps, done)
        if kind in ("manual", "disabled"):
            self._guard(u, "사용 안 함으로 설정" if kind == "disabled" else "수동으로 설정", go)
        else:
            go()

    def _report(self, title, text, unit, user, logs=True):
        """실패 알림 — 로그를 볼 수 있으면 [로그 보기]"""
        d = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.NONE, text=title)
        d.format_secondary_text(text)
        if logs:
            d.add_button("로그 보기", 1)
        d.add_button("닫기", Gtk.ResponseType.CLOSE)
        d.set_default_response(Gtk.ResponseType.CLOSE)

        def resp(dlg, r):
            dlg.destroy()
            if r == 1:
                self.win.show_page("events", unit=unit, scope="user" if user else "system")
        d.connect("response", resp)
        d.show_all()

    # ── 오른쪽 단추 메뉴 · 키 ──
    def _on_key(self, _v, ev):
        if key_is_menu(ev):
            self._menu(ev)
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK and ev.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            u = self.selected()
            if u is not None:
                copy_text(u["name"])
                self.win.toast("유닛 이름을 복사했습니다", 2)
            return True
        return False

    def _on_press(self, v, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        hit = v.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return True
        v.get_selection().select_path(hit[0])
        self._menu(ev)
        return True

    def _menu(self, ev):
        u = self.selected()
        if u is None:
            return
        m = Gtk.Menu()
        menu_item(m, "시작", lambda: self.do_job("StartUnit"), sensitive=self.b_start.get_sensitive())
        menu_item(m, "중지", lambda: self.do_job("StopUnit"), sensitive=self.b_stop.get_sensitive())
        menu_item(m, "다시 시작", lambda: self.do_job("RestartUnit"), sensitive=self.b_restart.get_sensitive())
        m.append(Gtk.SeparatorMenuItem())
        for kind in ("auto", "manual", "disabled"):
            it = self.type_items[kind]
            menu_item(m, f"시작 유형: {KIND_KO[kind]}", lambda k=kind: self.set_start_type(k),
                      sensitive=self.b_type.get_sensitive() and it.get_sensitive() and not it.get_active())
        m.append(Gtk.SeparatorMenuItem())
        menu_item(m, "로그 보기", self.show_logs, sensitive="events" in self.win.specs)
        menu_item(m, "파일 위치 열기", self._open_file, sensitive=self.d_file.get_sensitive())
        menu_item(m, "유닛 이름 복사", lambda: copy_text(u["name"]))
        popup(m, self.view, ev)


PAGE = {
    "id": "services",
    "title": "서비스",
    "group": "services",
    "order": 10,
    "icon": ["system-run-symbolic", "emblem-system-symbolic", "preferences-system-services"],
    "build": ServicesPage,
}
