"""컴퓨터 관리 — 이벤트 뷰어 (journald).

윈도우 이벤트 뷰어처럼 기록을 보기로 나눠 보여 준다:
  시스템          system.journal — 커널·시스템 서비스 (보안 기록은 뺀다 — 보안 보기에 있다)
  응용 프로그램    내 계정(_UID)의 기록 — 로그인 세션의 앱·사용자 서비스
  보안            syslog 기능 auth·authpriv — sudo·pkexec·polkit·로그인(PAM)·ssh
  부팅            부팅마다 (--list-boots) — 이번 부팅·지난 부팅들
  모든 기록
자료는 journalctl -o json 을 작업 스레드에서 읽는다 (python3-systemd 가 없어도 되게 — journalctl 만).
  최근 것부터 한 번에 BATCH 개(-r -n). "더 보기"는 가장 오래된 것의 시각까지(--until) 또 BATCH 개 —
  --since 와 --after-cursor 는 함께 쓸 수 없어서 시각으로 잇고, 겹친 것은 커서로 거른다.
  수준·기간·원본·유닛은 journalctl 이 거른다 (불러온 것 밖의 오래전 오류도 찾게). 검색어는 불러온 것 안에서 바로.
  새 기록 따라오기(-f --after-cursor=<가장 새것>)는 페이지가 보일 때만 — 가렸다 다시 보이면 그동안 쌓인 것부터 잇는다.
권한: 일반 사용자는 자기 기록만 읽는다. 시스템 기록은 systemd-journal·adm 그룹이 있어야 한다 — 관리자 계정은
  sekai-users 가 systemd-journal 에 넣어 준다. 없으면 안내 막대와 [관리자로 보기](pkexec journalctl — 그때 한 번 읽기).

다른 페이지에서: win.show_page("events", unit="ssh.service", scope="system"|"user")  — 그 유닛의 기록만
  (그 밖에 view=system|app|security|all, level=all|warning|err, period=all|1h|today|24h|7d|boot 도 받는다)
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from ..common import (ME, ME_UID, DetailGrid, NoticeBar, SortHeaders, copy_text, first_line,  # noqa: E402
                      fmt_short_date, fmt_time, gicon, in_groups_now, is_admin, key_is_menu,
                      listed_in_group, menu_item, mk_view, popup, scrolled, text_column, user_name)

BATCH = 2000                 # 한 번에 읽는 기록 수
FOLLOW_CAP = 20000           # 따라오다 이만큼 쌓이면 멈춘다 (기록을 쏟아 내는 프로그램이 있어도 창이 느려지지 않게)
MAX_BOOTS = 30
MAX_SOURCES = 120
JOURNALCTL = shutil.which("journalctl") or "/usr/bin/journalctl"
USERS_HELPER = "/usr/libexec/sekai/sekai-users"
READ_GROUPS = ("systemd-journal", "adm", "wheel")     # 데비안은 adm·wheel 에도 기록 폴더 ACL 을 준다
# 목록·세부에 쓰는 필드만 (전체를 받으면 코어 덤프 같은 큰 필드까지 온다)
FIELDS = ("MESSAGE", "PRIORITY", "SYSLOG_IDENTIFIER", "SYSLOG_FACILITY", "SYSLOG_PID", "_SYSTEMD_UNIT",
          "_SYSTEMD_USER_UNIT", "UNIT", "USER_UNIT", "_PID", "_UID", "_COMM", "_EXE", "_CMDLINE", "_TRANSPORT",
          "_HOSTNAME", "MESSAGE_ID", "CODE_FILE", "CODE_LINE", "CODE_FUNC", "ERRNO", "COREDUMP_EXE")
AUTH_FACILITIES = ("4", "10")

VIEWS = (("system", "시스템", ["computer-symbolic", "computer"]),
         ("app", "응용 프로그램", ["view-app-grid-symbolic", "applications-other-symbolic", "applications-other"]),
         ("security", "보안", ["security-high-symbolic", "channel-secure-symbolic", "dialog-password"]),
         ("boots", "부팅", ["system-reboot-symbolic", "view-refresh-symbolic", "system-reboot"]),
         ("all", "모든 기록", ["view-list-symbolic", "text-x-generic-symbolic", "text-x-generic"]))
LEVELS = (("all", "모든 수준"), ("warning", "경고 이상"), ("err", "오류만"))
PERIODS = (("all", "전체 기간"), ("1h", "지난 1시간"), ("today", "오늘"), ("24h", "지난 24시간"),
           ("7d", "지난 7일"), ("boot", "이번 부팅"))
PRIO_KO = ("긴급", "경보", "심각", "오류", "경고", "알림", "정보", "디버그")
PRIO_EN = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
FACILITY = {0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog", 6: "lpr", 7: "news", 8: "uucp",
            9: "cron", 10: "authpriv", 11: "ftp", 12: "ntp", 13: "security", 14: "console", 15: "cron"}
TRANSPORT = {"journal": "journal (직접)", "syslog": "syslog", "stdout": "표준 출력", "kernel": "커널",
             "audit": "감사(audit)", "driver": "journald 자신"}
LEVEL_ICONS = {"err": ["dialog-error", "dialog-error-symbolic"],
               "warning": ["dialog-warning", "dialog-warning-symbolic"],
               "info": ["dialog-information", "dialog-information-symbolic"]}


def level_of(prio):
    return "err" if prio <= 3 else "warning" if prio == 4 else "info"


LEVEL_KO = {"err": "오류", "warning": "경고", "info": "정보"}
SOURCE_KIND = {"_SYSTEMD_UNIT": "유닛", "_SYSTEMD_USER_UNIT": "사용자 유닛", "_COMM": "명령"}


# ── 기록 한 줄 ───────────────────────────────────────────────
def _val(v):
    """journalctl JSON 의 값: 글자 · 바이트 배열(글자가 아닌 것) · 같은 필드 여럿(배열) · null(너무 큼)"""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, list):
        if v and all(isinstance(x, int) for x in v):
            return bytes(x & 0xFF for x in v).decode("utf-8", "replace")
        return _val(v[0]) if v else ""
    return str(v)


class Entry:
    """기록 한 줄. 목록에 쓰는 것만 풀어 두고, 세부 필드(f)는 고를 때 원래 JSON 줄에서 푼다 (수만 줄이어도 가볍게)"""
    __slots__ = ("cursor", "ts", "prio", "source", "skey", "msg", "line", "tstr", "fac", "raw", "_f", "_search")

    def __init__(self, d, raw):
        self.raw = raw
        self._f = None
        self._search = None
        self.cursor = _val(d.get("__CURSOR")) or ""
        try:
            self.ts = int(_val(d.get("__REALTIME_TIMESTAMP")) or 0)
        except ValueError:
            self.ts = 0
        try:
            self.prio = min(7, max(0, int(_val(d.get("PRIORITY")) or 6)))
        except ValueError:
            self.prio = 6
        self.fac = _val(d.get("SYSLOG_FACILITY"))
        self.source, self.skey = source_of(d)
        m = _val(d.get("MESSAGE"))
        self.msg = m if m is not None else "(메시지가 너무 커서 journalctl 이 생략했습니다)"
        self.line = first_line(self.msg)
        self.tstr = fmt_time(self.ts / 1e6)

    @property
    def f(self):
        if self._f is None:
            try:
                d = json.loads(self.raw)
            except ValueError:
                d = {}
            self._f = {k: _val(v) for k, v in d.items()} if isinstance(d, dict) else {}
        return self._f

    @property
    def search(self):
        if self._search is None:                  # 처음 검색할 때 한 번
            self._search = f"{self.source}\n{self.msg}".casefold()
        return self._search


def source_of(d):
    """(보일 이름, 거르기 매치 'FIELD=값') — syslog 이름이 있으면 그것, 없으면 유닛·명령 이름"""
    for key in ("SYSLOG_IDENTIFIER", "_SYSTEMD_UNIT", "_SYSTEMD_USER_UNIT", "_COMM"):
        v = _val(d.get(key))
        if v:
            return v, f"{key}={v}"
    return "?", None


def parse_line(line):
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or "__CURSOR" not in d:
        return None
    return Entry(d, line)


def unit_of(e):
    """(범위, 서비스 이름) — 이 기록이 어느 서비스의 것인지 (서비스 페이지로 갈 때). 서비스가 아니면 None"""
    f = e.f
    for key, scope in (("UNIT", "system"), ("USER_UNIT", "user"), ("_SYSTEMD_USER_UNIT", "user"),
                       ("_SYSTEMD_UNIT", "system")):
        u = f.get(key)
        if u and u.endswith(".service") and not (scope == "system" and u.startswith("user@")):
            return scope, u
    return None


def parse_boots(out):
    """journalctl --list-boots — JSON(systemd 25x 이상 -o json) 또는 옛 글자 표. 새것부터"""
    out = (out or "").strip()
    boots = []
    if out.startswith("["):
        try:
            arr = json.loads(out)
        except ValueError:
            arr = []
        for b in arr if isinstance(arr, list) else []:
            try:
                boots.append({"index": int(b.get("index")), "id": str(b.get("boot_id") or ""),
                              "first": int(b.get("first_entry") or 0) / 1e6,
                              "last": int(b.get("last_entry") or 0) / 1e6, "text": ""})
            except (TypeError, ValueError, AttributeError):
                continue
    else:
        for ln in out.splitlines():
            parts = ln.split(None, 2)
            if len(parts) >= 2 and re.fullmatch(r"-?\d+", parts[0]) and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
                text = parts[2] if len(parts) > 2 else ""
                # "Fri 2026-09-25 15:54:55 KST—Fri 2026-09-25 16:32:20 KST" — 이 컴퓨터의 시간대로 찍힌 것
                ts = [time.mktime(tuple(map(int, m)) + (0, 0, -1))
                      for m in re.findall(r"(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", text)]
                boots.append({"index": int(parts[0]), "id": parts[1], "first": ts[0] if ts else 0,
                              "last": ts[1] if len(ts) > 1 else 0, "text": text})
    boots = [b for b in boots if re.fullmatch(r"[0-9a-f]{32}", b["id"])]
    boots.sort(key=lambda b: b["index"], reverse=True)
    return boots[:MAX_BOOTS]


def boot_label(b):
    if b["first"]:
        when = fmt_short_date(b["first"])
    else:                                            # 옛 표 — 날짜 글자를 그대로 (앞부분만)
        when = b["text"].split("—")[0].strip()[:28] or b["id"][:8]
    return f"이번 부팅 · {when}" if b["index"] == 0 else when


def current_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip().replace("-", "")
    except OSError:
        return ""


def can_read_system():
    return ME_UID == 0 or in_groups_now(*READ_GROUPS)


# ── 화면 ─────────────────────────────────────────────────────
(C_IDX, C_ICON, C_LEVEL, C_PRIO, C_TIME, C_TS, C_SOURCE, C_MSG) = range(8)
C_TYPES = (int, GObject.Object, str, int, str, GObject.TYPE_INT64, str, str)
V_ID, V_ICON, V_LABEL, V_TIP = range(4)


class EventsPage:
    searchable = True
    search_hint = "메시지 또는 원본으로 검색 (불러온 기록 안에서)"

    def __init__(self, win):
        self.win = win
        self.st = win.page_state("events")
        self.busy = False
        self.visible = False
        self.query = ""
        self.view = self.st.get("view") if self.st.get("view") in {v[0] for v in VIEWS} else "system"
        self.level = self.st.get("level") if self.st.get("level") in dict(LEVELS) else "all"
        self.period = self.st.get("period") if self.st.get("period") in dict(PERIODS) else "all"
        self.source = None                 # 'FIELD=값' 매치
        self.unit = None                   # (범위, 이름) — 서비스 페이지에서 왔을 때
        self.admin = False                 # 관리자로 보기(pkexec) 중
        self.entries = []
        self._cursors = set()
        self.oldest = 0                    # 불러온 것 중 가장 오래된 시각(µs) — 더 보기
        self.newest_cursor = None          # 따라오기를 이을 자리
        self.more = False
        self.loaded = False
        self._gen = 0
        self._proc = None
        self._follow = None                # (프로세스, 쌓인 기록 목록, 잠금)
        self._follow_src = 0
        self._follow_stopped = ""
        self._follow_t0 = 0.0
        self._boots = []
        self._boot_now = current_boot_id()
        self._sources = {}                 # 보일 이름 → 매치 (원본 고르기 목록)
        self._icons = {k: gicon(v) for k, v in LEVEL_ICONS.items()}
        self._sum_gen = 0
        self._sel_cursor = None

        # 위: 안내 막대 · 거르기 줄
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.notice = NoticeBar()
        outer.pack_start(self.notice, False, False, 0)
        outer.pack_start(self._build_filters(), False, False, 0)

        body = Gtk.Box(spacing=12)
        outer.pack_start(body, True, True, 0)
        body.pack_start(self._build_views(), False, False, 0)

        details = self._build_details()          # 목록보다 먼저 — 목록이 처음 모델을 받을 때 세부 칸을 고친다
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        body.pack_start(self.paned, True, True, 0)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        top.pack_start(self._build_list(), True, True, 0)
        status = Gtk.Box(spacing=6)
        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("adm-status")
        status.pack_start(self.status, True, True, 0)
        self.more_btn = Gtk.Button(label=f"더 보기 (이전 {BATCH:,}개)")
        self.more_btn.get_style_context().add_class("adm-link")
        self.more_btn.set_no_show_all(True)
        self.more_btn.connect("clicked", lambda *_: self.load_more())
        status.pack_end(self.more_btn, False, False, 0)
        top.pack_start(status, False, False, 0)
        self.paned.pack1(top, True, False)
        self.paned.pack2(details, False, False)
        pos = self.st.get("paned")
        self._paned_pos = int(pos) if isinstance(pos, int) and pos > 120 else None
        self.paned.connect("size-allocate", self._first_alloc)
        self.paned.connect("notify::position", lambda p, _s: self.st.__setitem__("paned", p.get_position()))
        self.widget = outer

        self.actions = Gtk.Box(spacing=8)
        b = Gtk.Button(label="새로 고침")
        b.set_tooltip_text("F5")
        b.connect("clicked", lambda *_: self.refresh())
        self.actions.pack_start(b, False, False, 0)
        self._show_details(None)

    # ── 만들기 ──
    def _build_filters(self):
        row = Gtk.Box(spacing=8)
        row.get_style_context().add_class("adm-filters")
        self.level_cb = Gtk.ComboBoxText()
        for k, t in LEVELS:
            self.level_cb.append(k, t)
        self.level_cb.set_active_id(self.level)
        self.level_cb.connect("changed", lambda c: self._set_filter("level", c.get_active_id()))
        row.pack_start(self.level_cb, False, False, 0)
        self.period_cb = Gtk.ComboBoxText()
        for k, t in PERIODS:
            self.period_cb.append(k, t)
        self.period_cb.set_active_id(self.period)
        self.period_cb.connect("changed", lambda c: self._set_filter("period", c.get_active_id()))
        row.pack_start(self.period_cb, False, False, 0)
        self.source_cb = Gtk.ComboBoxText()
        self.source_cb.set_tooltip_text("원본 (기록을 남긴 프로그램·서비스)")
        self.source_cb.append("", "모든 원본")
        self.source_cb.set_active_id("")
        self._source_sig = self.source_cb.connect("changed", lambda c: self._set_filter("source", c.get_active_id() or None))
        row.pack_start(self.source_cb, False, False, 0)
        # 유닛 거르기 (서비스 페이지의 "로그 보기") — ✕ 로 푼다
        self.unit_btn = Gtk.Button()
        self.unit_btn.get_style_context().add_class("adm-chip")
        self.unit_btn.set_tooltip_text("이 서비스의 기록만 보는 중 — 누르면 풉니다")
        self.unit_btn.set_no_show_all(True)
        self.unit_btn.connect("clicked", lambda *_: self._set_filter("unit", None))
        row.pack_start(self.unit_btn, False, False, 0)

        # 오른쪽: 요약 (지난 24시간 오류·경고 수 — 윈도우 이벤트 뷰어의 개요처럼)
        self.sum_box = Gtk.Box(spacing=6)
        self.sum_label = Gtk.Label()
        self.sum_label.get_style_context().add_class("adm-sum")
        self.sum_box.pack_start(self.sum_label, False, False, 0)
        self.sum_err = Gtk.Button()
        self.sum_err.get_style_context().add_class("adm-chip")
        self.sum_err.get_style_context().add_class("err")
        self.sum_err.connect("clicked", lambda *_: self._from_summary("err"))
        self.sum_warn = Gtk.Button()
        self.sum_warn.get_style_context().add_class("adm-chip")
        self.sum_warn.get_style_context().add_class("warn")
        self.sum_warn.connect("clicked", lambda *_: self._from_summary("warning"))
        self.sum_box.pack_start(self.sum_err, False, False, 0)
        self.sum_box.pack_start(self.sum_warn, False, False, 0)
        self.sum_box.set_no_show_all(True)
        row.pack_end(self.sum_box, False, False, 0)
        return row

    def _build_views(self):
        self.vstore = Gtk.TreeStore(str, GObject.Object, str, str)
        self._view_iters = {}
        for vid, label, icons in VIEWS:
            it = self.vstore.append(None, [vid, gicon(icons), label, ""])
            self._view_iters[vid] = it
        v = self.vview = Gtk.TreeView(model=self.vstore)
        v.set_headers_visible(False)
        v.set_enable_search(False)
        v.set_show_expanders(True)
        v.set_level_indentation(0)
        v.get_style_context().add_class("adm-side")
        col = text_column("", V_LABEL, width=170, expand=True, icon=V_ICON)
        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        v.append_column(col)
        v.set_tooltip_column(V_TIP)
        self._vsig = v.get_selection().connect("changed", self._on_view_selected)
        sc = scrolled(v)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_size_request(190, -1)
        self._select_view_row(self.view)
        return sc

    def _build_list(self):
        self.store = Gtk.ListStore(*C_TYPES)
        self.filter = self.store.filter_new()
        self.sort = Gtk.TreeModelSort(model=self.filter)
        v = self.view_w = mk_view(self.sort)
        cols = [text_column("수준", C_LEVEL, width=84, icon=C_ICON),
                text_column("날짜 및 시간", C_TIME, width=176),
                text_column("원본", C_SOURCE, width=140),
                text_column("메시지", C_MSG, width=260, expand=True)]
        for c in cols:
            v.append_column(c)
        self.sorter = SortHeaders(self.sort, self.win.sort_saver("events"))
        for c, sid, num in zip(cols, (C_PRIO, C_TS, C_SOURCE, C_MSG), (False, True, False, False)):
            self.sorter.add(c, sid, num)
        v.get_selection().connect("changed", lambda *_: self._on_select())
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.pack_start(scrolled(v), True, True, 0)
        self.empty = Gtk.Label(xalign=0)
        self.empty.get_style_context().add_class("adm-empty")
        self.empty.set_no_show_all(True)
        box.pack_start(self.empty, False, False, 0)
        self._install_models(Gtk.ListStore(*C_TYPES))
        return box

    def _build_details(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("adm-details")
        box.set_size_request(-1, 150)
        head = Gtk.Box(spacing=8)
        self.d_icon = Gtk.Image()
        head.pack_start(self.d_icon, False, False, 0)
        self.d_title = Gtk.Label(xalign=0)
        self.d_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.d_title.get_style_context().add_class("adm-dtitle")
        head.pack_start(self.d_title, True, True, 0)
        self.d_copy = Gtk.Button(label="복사")
        self.d_copy.set_tooltip_text("메시지와 세부 정보를 클립보드로 (Ctrl+C)")
        self.d_copy.connect("clicked", lambda *_: self.copy_selected())
        head.pack_end(self.d_copy, False, False, 0)
        self.d_svc = Gtk.Button(label="서비스 보기")
        self.d_svc.set_no_show_all(True)
        self.d_svc.connect("clicked", lambda *_: self._goto_service())
        head.pack_end(self.d_svc, False, False, 0)
        box.pack_start(head, False, False, 0)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.d_msg = Gtk.Label(xalign=0, yalign=0)
        self.d_msg.set_selectable(True)
        self.d_msg.set_line_wrap(True)
        self.d_msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.d_msg.set_can_focus(False)
        inner.pack_start(self.d_msg, False, False, 0)
        self.d_grid = DetailGrid()
        inner.pack_start(self.d_grid, False, False, 0)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(inner)
        box.pack_start(sc, True, True, 0)
        return box

    def _first_alloc(self, p, alloc):
        """처음 크기가 정해질 때 — 기억한 나눔 자리, 없으면 목록 2/3"""
        if getattr(self, "_placed", False) or alloc.height < 200:
            return
        self._placed = True
        pos = self._paned_pos or int(alloc.height * 0.64)
        GLib.idle_add(lambda: (p.set_position(min(pos, alloc.height - 150)), False)[1])

    def _install_models(self, store):
        """새 저장소를 목록에 — 다 채운 뒤 한 번에 (행마다 정렬·거르기가 따라 움직이지 않게)"""
        self.store = store
        self.filter = store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.sort = Gtk.TreeModelSort(model=self.filter)
        self.sorter.model = self.sort
        sid, order = self.win.saved_sort("events", C_TS, Gtk.SortType.DESCENDING)
        self.sorter.set(sid, order)
        self.view_w.set_model(self.sort)

    # ── 페이지 규칙 ──
    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q != self.query:
            self.query = q
            self.filter.refilter()
            self._update_status()

    def on_show(self, unit=None, scope="system", view=None, level=None, period=None, **_kw):
        self.visible = True
        changed = False
        if unit:
            self.unit = ("user" if scope == "user" else "system", str(unit))
            self.view, self.level, self.period, self.source = "all", "all", "all", None
            changed = True
        if view in {v[0] for v in VIEWS if v[0] != "boots"}:
            self.view, changed = view, True
        if level in dict(LEVELS):
            self.level, changed = level, True
        if period in dict(PERIODS):
            self.period, changed = period, True
        if changed:
            self._sync_controls()
        if not self._boots:
            self._load_boots()
        if changed or not self.loaded:
            self.reload()
        else:
            self._start_follow()                  # 가려진 동안 쌓인 것부터 잇는다
            self._load_summary()

    def on_hide(self):
        self.visible = False
        self._stop_follow()

    def refresh(self):
        self._load_boots()
        self.reload()

    # ── 거르기 ──
    def _sync_controls(self):
        """상태 → 위젯 (신호가 다시 reload 를 부르지 않게 막고)"""
        self._syncing = True
        try:
            self.level_cb.set_active_id(self.level)
            self.period_cb.set_active_id(self.period)
            self._select_view_row(self.view)
            if self.source is None:
                self.source_cb.set_active_id("")
            if self.unit:
                self.unit_btn.set_label(f"서비스: {self.unit[1]}  ✕")
            self.unit_btn.set_visible(bool(self.unit))
            self.period_cb.set_sensitive(not self.view.startswith("boot"))
        finally:
            self._syncing = False

    def _set_filter(self, what, value):
        if getattr(self, "_syncing", False):
            return
        if what == "level":
            self.level = value or "all"
            self.st["level"] = self.level
        elif what == "period":
            self.period = value or "all"
            self.st["period"] = self.period
        elif what == "source":
            self.source = value
        elif what == "unit":
            self.unit = None
        self._sync_controls()
        self.reload()

    def _select_view_row(self, vid):
        store = self.vstore
        target = None
        if vid.startswith("boot:"):
            parent = self._view_iters["boots"]
            it = store.iter_children(parent)
            while it is not None:
                if store.get_value(it, V_ID) == vid:
                    target = it
                    break
                it = store.iter_next(it)
            if target is not None:
                self.vview.expand_row(store.get_path(parent), False)
        else:
            target = self._view_iters.get(vid)
        sel = self.vview.get_selection()
        sel.handler_block(self._vsig)
        if target is not None:
            sel.select_iter(target)
        else:
            sel.unselect_all()
        sel.handler_unblock(self._vsig)

    def _on_view_selected(self, sel):
        model, it = sel.get_selected()
        if it is None:
            return
        vid = model.get_value(it, V_ID)
        if vid == "boots":                        # 묶음 머리 — 펼치고 이번 부팅을 고른다
            path = model.get_path(it)
            self.vview.expand_row(path, False)
            child = model.iter_children(it)
            vid = model.get_value(child, V_ID) if child is not None else "boot:" + (self._boot_now or "0")
        if vid == self.view:
            return
        self.view = vid
        if not vid.startswith("boot:"):
            self.st["view"] = vid
        self._sync_controls()
        self.reload()

    def _from_summary(self, level):
        self.level = level
        if not self.view.startswith("boot:"):
            self.period = "24h"
        self.source = None
        self._sync_controls()
        self.reload()

    # ── journalctl 인자 ──
    def _argv(self, mode):
        """mode: load · more · follow · summary"""
        fields = "PRIORITY" if mode == "summary" else ",".join(FIELDS)
        a = [JOURNALCTL, "-q", "--no-pager", "-o", "json", f"--output-fields={fields}"]
        matches = []
        v = self.view
        if v == "system":
            a.append("--system")
        elif v == "app":
            matches.append(f"_UID={ME_UID}")
        elif v == "security":
            a.append("--facility=auth,authpriv")
        elif v.startswith("boot:"):
            a += ["-b", v[5:]]
        if self.unit:
            a += ["--user-unit" if self.unit[0] == "user" else "--unit", self.unit[1]]
        if self.source:
            matches.append(self.source)
        if mode == "summary":
            a += ["-p", "warning"]                # 요약은 수준·기간과 상관없이 (보기·서비스·원본은 따른다)
        elif self.level != "all":
            a += ["-p", self.level]
        now = time.time()
        if mode == "summary":
            if not v.startswith("boot:"):
                a.append("--since=@%d" % (now - 86400))
            a += ["-n", "50000"]
        elif mode != "follow" and not v.startswith("boot:"):
            since = {"1h": now - 3600, "24h": now - 86400, "7d": now - 7 * 86400}.get(self.period)
            if self.period == "today":
                t = time.localtime(now)
                since = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
            if since:
                a.append("--since=@%d" % since)
            elif self.period == "boot":
                a += ["-b", "0"]
        elif mode == "follow" and self.period == "boot" and not v.startswith("boot:"):
            a += ["-b", "0"]
        if mode == "load":
            a += ["-r", "-n", str(BATCH)]
        elif mode == "more":
            s, us = divmod(self.oldest, 1000000)
            a += ["-r", "-n", str(BATCH), "--until=@%d.%06d" % (s, us)]
        elif mode == "follow":
            a += ["-f"] + ([f"--after-cursor={self.newest_cursor}"] if self.newest_cursor else ["-n", "0"])
        if matches:
            a += ["--"] + matches
        if self.admin and mode in ("load", "more"):
            a = ["pkexec"] + a
        return a

    def _needs_system(self):
        return self.view != "app" and not (self.unit and self.unit[0] == "user")

    def _keep(self, e):
        """시스템 보기에서는 보안 기록(auth·authpriv)을 뺀다 — 윈도우처럼 시스템과 보안을 나눠"""
        return not (self.view == "system" and e.fac in AUTH_FACILITIES)

    # ── 읽기 ──
    def reload(self):
        self._stop_follow()
        self._fetch("load")
        self._load_summary()

    def load_more(self):
        if self.more and not self.busy:
            self._fetch("more")

    def _fetch(self, mode):
        self._gen += 1
        gen = self._gen
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()                 # 앞서 부른 것 — 결과는 버린다 (gen)
            except OSError:
                pass
        argv = self._argv(mode)
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            self._show_error(f"journalctl 을 실행하지 못했습니다: {e.strerror or e}")
            return
        self._proc = p
        self.busy = True
        self.win.busy_changed()
        self.more_btn.hide()
        if mode == "load":
            self.status.set_text("기록을 불러오는 중…")
        keep = self._keep
        seen = set(self._cursors) if mode == "more" else set()

        def work():                               # 작업 스레드 — pkexec 면 인증 창을 기다리는 동안에도
            out, err = p.communicate()
            got, raw, oldest, newest = [], 0, 0, None
            for line in out.splitlines():
                e = parse_line(line)
                if e is None:
                    continue
                raw += 1
                if newest is None:
                    newest = e.cursor
                oldest = e.ts
                if e.cursor in seen or not keep(e):
                    continue
                seen.add(e.cursor)
                got.append(e)
            return got, raw, oldest, newest, p.returncode, err.decode("utf-8", "replace")
        self.win.run_thread(work, lambda res, exc: self._fetched(gen, mode, res, exc))

    def _fetched(self, gen, mode, res, exc):
        if gen != self._gen:
            return
        self._proc = None
        self.busy = False
        self.win.busy_changed()
        if exc is not None:
            self._show_error(f"기록을 읽지 못했습니다: {exc}")
            return
        got, raw, oldest, newest, rc, err = res
        if self.admin and rc in (126, 127) and not got:
            # pkexec — 인증 창을 닫았거나 관리자 인증에 실패했다
            self.admin = False
            self.win.toast("관리자 인증이 취소되어 내 권한으로 보이는 기록만 보여 줍니다")
            self._update_notice()
            self._update_status()
            return
        # 권한이 모자라 못 읽은 것은 오류가 아니라 안내 막대가 알린다
        error = err.strip().splitlines()[-1] if rc not in (0, None) and not got and err.strip() and \
            (self.admin or not self._needs_system() or can_read_system()) else None
        if mode == "load":
            self._sel_cursor = self._selected_cursor()
            self.entries = []
            self._cursors = set()
            self.newest_cursor = None if self.admin else newest
        start = len(self.entries)
        self.entries.extend(got)
        self._cursors.update(e.cursor for e in got)
        if raw:
            self.oldest = oldest
        self.more = raw >= BATCH
        if mode == "load":
            store = Gtk.ListStore(*C_TYPES)
            self._append_rows(store, start)
            self._install_models(store)
            self.loaded = True
            self._restore_selection()
            if self.source is None:
                self._fill_sources()
        else:
            self._append_rows(self.store, start)
        self._update_notice()
        if error:
            self._show_error(error)
        self._update_status()
        if mode == "load":
            self._start_follow()

    def _append_rows(self, store, start):
        icons = self._icons
        for i in range(start, len(self.entries)):
            e = self.entries[i]
            lv = level_of(e.prio)
            store.append([i, icons[lv], LEVEL_KO[lv], e.prio, e.tstr, e.ts, e.source, e.line])

    def _visible(self, model, it, _data):
        if not self.query:
            return True
        i = model.get_value(it, C_IDX)
        return 0 <= i < len(self.entries) and self.query in self.entries[i].search

    def _fill_sources(self):
        """원본 고르기 목록 — 불러온 기록에 나온 이름들 (많이 나온 것부터 MAX_SOURCES 개, 가나다순)"""
        counts = {}
        for e in self.entries:
            if e.skey:
                counts[(e.source, e.skey)] = counts.get((e.source, e.skey), 0) + 1
        top = sorted(counts, key=lambda k: -counts[k])[:MAX_SOURCES]
        top.sort(key=lambda k: k[0].casefold())
        cb = self.source_cb
        cb.handler_block(self._source_sig)
        cb.remove_all()
        cb.append("", "모든 원본")
        seen = set()
        for name, key in top:
            if key in seen:
                continue
            seen.add(key)
            kind = SOURCE_KIND.get(key.split("=", 1)[0])
            cb.append(key, f"{name} ({kind})" if kind else name)
        cb.set_active_id("")
        cb.handler_unblock(self._source_sig)

    def select_source(self, key, name):
        """목록의 오른쪽 메뉴 "이 원본만 보기" — 고르기 목록에 없으면 더한다"""
        cb = self.source_cb
        model = cb.get_model()
        if not any(row[1] == key for row in model if len(row) > 1):
            cb.handler_block(self._source_sig)
            cb.append(key, name)
            cb.handler_unblock(self._source_sig)
        cb.set_active_id(key)                     # → changed → _set_filter

    # ── 따라오기 ──
    def _can_follow(self):
        if not self.visible or self.admin or not self.loaded:
            return False
        if self.view.startswith("boot:") and self.view[5:] != self._boot_now:
            return False                          # 지난 부팅 — 새 기록이 올 리 없다
        return len(self.entries) < FOLLOW_CAP

    def _start_follow(self):
        self._stop_follow()
        if not self._can_follow():
            self._update_status()
            return
        try:
            p = subprocess.Popen(self._argv("follow"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
        except OSError:
            return
        pending, lock, keep = [], threading.Lock(), self._keep

        def reader():
            for line in p.stdout:                 # 작업 스레드 — 한 줄씩 (쌓아 두면 메인 스레드가 가져간다)
                e = parse_line(line)
                if e is not None and keep(e):
                    with lock:
                        pending.append(e)
        threading.Thread(target=reader, daemon=True, name="sekai-admin-follow").start()
        self._follow = (p, pending, lock)
        self._follow_t0 = time.monotonic()
        self._follow_src = GLib.timeout_add(500, self._drain)
        self._follow_stopped = ""
        self._update_status()

    def _stop_follow(self):
        if self._follow_src:
            GLib.source_remove(self._follow_src)
            self._follow_src = 0
        if self._follow is not None:
            p = self._follow[0]
            self._follow = None
            try:
                p.terminate()
            except OSError:
                pass
            threading.Thread(target=p.wait, daemon=True).start()     # 거둔다 (메인 스레드는 기다리지 않게)

    def _drain(self):
        if self._follow is None:
            self._follow_src = 0
            return False
        p, pending, lock = self._follow
        with lock:
            new, pending[:] = list(pending), []
        new = [e for e in new if e.cursor not in self._cursors]
        if new:
            start = len(self.entries)
            self.entries.extend(new)
            self._cursors.update(e.cursor for e in new)
            self.newest_cursor = new[-1].cursor
            self._append_rows(self.store, start)
            self._update_status()
        if len(self.entries) >= FOLLOW_CAP:
            self._follow_src = 0
            self._stop_follow()
            self._follow_stopped = "새 기록이 너무 많아 따라오기를 멈췄습니다 — 새로 고침(F5)으로 다시 시작합니다"
            self._update_status()
            return False
        if p.poll() is not None:                  # journalctl 이 끝났다
            self._follow_src = 0
            if time.monotonic() - self._follow_t0 < 5:     # 곧바로 끝났다 — 다시 띄워도 같다
                self._stop_follow()
                self._follow_stopped = "새 기록을 따라올 수 없습니다"
                self._update_status()
            else:                                 # 한참 돌다 끝났다 (저널 회전 등) — 이어서 다시
                GLib.timeout_add_seconds(2, lambda: (self._follow is not None and self._start_follow(), False)[1])
            return False
        return True

    # ── 부팅 목록 ──
    def _load_boots(self):
        argv = [JOURNALCTL, "--list-boots", "-o", "json", "-q", "--no-pager"]

        def work():
            p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            return parse_boots(p.stdout)
        self.win.run_thread(work, self._got_boots)

    def _got_boots(self, boots, exc):
        if exc is not None or boots is None:
            return
        self._boots = boots
        store, parent = self.vstore, self._view_iters["boots"]
        sel = self.vview.get_selection()
        sel.handler_block(self._vsig)
        while store.iter_has_child(parent):
            store.remove(store.iter_children(parent))
        for b in boots:
            tip = f"부팅 ID {b['id']}"
            if b["first"] and b["last"]:
                tip += f"\n{fmt_time(b['first'])} – {fmt_time(b['last'])}"
            elif b["text"]:
                tip += "\n" + b["text"]
            store.append(parent, [f"boot:{b['id']}", None, boot_label(b), GLib.markup_escape_text(tip)])
        sel.handler_unblock(self._vsig)
        if self.view.startswith("boot:"):
            self._select_view_row(self.view)

    # ── 요약 (지난 24시간 오류·경고) ──
    def _load_summary(self):
        self._sum_gen += 1
        gen = self._sum_gen
        if self.admin or (self._needs_system() and not can_read_system()):
            self.sum_box.hide()
            return
        argv = self._argv("summary")

        def work():
            p = subprocess.run(argv, capture_output=True, timeout=120)
            err = warn = 0
            for line in p.stdout.splitlines():
                m = re.search(rb'"PRIORITY":"(\d)"', line)
                if m:
                    if int(m.group(1)) <= 3:
                        err += 1
                    else:
                        warn += 1
            return err, warn
        self.win.run_thread(work, lambda res, exc: self._got_summary(gen, res, exc))

    def _got_summary(self, gen, res, exc):
        if gen != self._sum_gen or exc is not None or res is None:
            return
        err, warn = res
        boot = self.view.startswith("boot:")
        if not err and not warn:
            self.sum_label.set_text("이 부팅에 오류·경고가 없습니다" if boot else "지난 24시간 동안 오류·경고가 없습니다")
            self.sum_err.hide()
            self.sum_warn.hide()
        else:
            self.sum_label.set_text("이 부팅:" if boot else "지난 24시간:")
            self.sum_err.set_label(f"오류 {err:,}")
            self.sum_warn.set_label(f"경고 {warn:,}")
            self.sum_err.set_visible(err > 0)
            self.sum_warn.set_visible(warn > 0)
        self.sum_label.show()
        self.sum_box.show()

    # ── 권한 안내 ──
    def _update_notice(self):
        if self.admin:
            self.notice.show_notice(
                "관리자 권한으로 한 번 읽어 온 기록입니다. 새 기록은 따라오지 않고, 거르기를 바꾸면 다시 인증합니다.",
                [("내 권한으로 돌아가기", self._leave_admin)], kind="info")
            return
        if not self._needs_system() or can_read_system():
            self.notice.hide_notice()
            return
        btns = [("관리자로 보기", self._enter_admin)]
        if listed_in_group("systemd-journal"):
            text = "시스템 기록을 보는 권한이 추가되었습니다. 다시 로그인하면 보입니다 — 지금은 내 기록만 보입니다."
        else:
            text = "시스템 기록을 보려면 관리자 권한이 필요합니다. 지금은 내 기록만 보입니다."
            if is_admin() and os.path.exists(USERS_HELPER):
                btns.append(("다음 로그인부터 바로 보기", self._grant_logs))
        self.notice.show_notice(text, btns, kind="warn")

    def _enter_admin(self):
        self.admin = True
        self._stop_follow()
        self._fetch("load")
        self.sum_box.hide()

    def _leave_admin(self):
        self.admin = False
        self.reload()

    def _grant_logs(self):
        """관리자 계정을 systemd-journal 그룹에 (sekai-users logs) — 다음 로그인부터 암호 없이"""
        def done(ok, out, err):
            if ok:
                self.win.toast("다음 로그인부터 시스템 기록이 바로 보입니다")
            else:
                msg = (out or "").strip().splitlines()[-1:] or (err or "").strip().splitlines()[-1:]
                self.win.toast("권한을 바꾸지 못했습니다" + (f" — {msg[0]}" if msg else ""))
            self._update_notice()
        self.win.run_async(["pkexec", USERS_HELPER, "logs", ME], done)

    def _show_error(self, text):
        self.notice.show_notice(text, [("다시 시도", self.reload)], kind="error")

    # ── 상태 줄 ──
    def _update_status(self):
        n = len(self.entries)
        shown = self.sort.iter_n_children(None) if self.query else n
        if not self.loaded:
            return
        if self.query:
            s = f"검색에 맞는 기록 {shown:,}개 (불러온 {n:,}개 중)"
        else:
            s = f"기록 {n:,}개"
        if self._follow is not None:
            s += " · 새 기록을 따라오는 중"
        elif self._follow_stopped:
            s += " · " + self._follow_stopped
        self.status.set_text(s)
        self.more_btn.set_visible(self.more and not self.busy)
        if n == 0:
            self.empty.set_text("조건에 맞는 기록이 없습니다.")
        elif shown == 0:
            self.empty.set_text("검색어에 맞는 기록이 없습니다. 더 보기로 이전 기록을 불러와 찾아볼 수 있습니다."
                                if self.more else "검색어에 맞는 기록이 없습니다.")
        self.empty.set_visible(shown == 0)

    # ── 고르기 · 세부 ──
    def _selected(self):
        model, it = self.view_w.get_selection().get_selected()
        if it is None:
            return None
        i = model.get_value(it, C_IDX)
        return self.entries[i] if 0 <= i < len(self.entries) else None

    def _selected_cursor(self):
        e = self._selected()
        return e.cursor if e else None

    def _restore_selection(self):
        cur = self._sel_cursor
        self._sel_cursor = None
        if not cur:
            self._show_details(None)
            return
        for row in self.sort:
            i = row[C_IDX]
            if 0 <= i < len(self.entries) and self.entries[i].cursor == cur:
                path = row.path
                self.view_w.get_selection().select_path(path)
                GLib.idle_add(lambda: (self.view_w.scroll_to_cell(path, None, False, 0, 0), False)[1])
                return
        self._show_details(None)

    def _on_select(self):
        self._show_details(self._selected())

    def _rows_for(self, e):
        f = e.f
        lv = level_of(e.prio)
        unit = f.get("_SYSTEMD_UNIT") or f.get("_SYSTEMD_USER_UNIT")
        about = f.get("UNIT") or f.get("USER_UNIT")
        code = f.get("CODE_FILE")
        if code and f.get("CODE_LINE"):
            code += f":{f.get('CODE_LINE')}"
        if code and f.get("CODE_FUNC"):
            code += f" ({f.get('CODE_FUNC')})"
        errno = f.get("ERRNO")
        if errno and errno.isdigit():
            try:
                errno = f"{errno} ({os.strerror(int(errno))})"
            except ValueError:
                pass
        fac = f.get("SYSLOG_FACILITY")
        if fac and fac.isdigit():
            fac = f"{fac} ({FACILITY.get(int(fac), '?')})"
        uid = f.get("_UID")
        ms = (e.ts // 1000) % 1000
        return [("수준", f"{LEVEL_KO[lv]} (우선순위 {e.prio} · {PRIO_KO[e.prio]} · {PRIO_EN[e.prio]})"),
                ("날짜 및 시간", f"{e.tstr}.{ms:03d}"),
                ("원본", e.source),
                ("유닛", unit),
                ("관련 유닛", about if about != unit else None),
                ("프로세스 ID", f.get("_PID") or f.get("SYSLOG_PID")),
                ("사용자", f"{user_name(uid)} ({uid})" if uid else None),
                ("실행 파일", f.get("_EXE") or f.get("COREDUMP_EXE")),
                ("명령줄", f.get("_CMDLINE")),
                ("syslog 기능", fac),
                ("전송 방식", TRANSPORT.get(f.get("_TRANSPORT"), f.get("_TRANSPORT"))),
                ("오류 번호", errno),
                ("메시지 ID", f.get("MESSAGE_ID")),
                ("소스 코드", code),
                ("부팅 ID", f.get("_BOOT_ID")),
                ("컴퓨터", f.get("_HOSTNAME"))]

    def _show_details(self, e):
        self.d_copy.set_sensitive(e is not None)
        if e is None:
            self.d_icon.clear()
            self.d_title.set_text("이벤트")
            self.d_msg.set_text("목록에서 기록을 고르면 여기에 전체 메시지와 세부 정보가 나옵니다.")
            self.d_grid.set_rows([])
            self.d_svc.hide()
            return
        lv = level_of(e.prio)
        self.d_icon.set_from_gicon(self._icons[lv], Gtk.IconSize.BUTTON)
        self.d_title.set_text(f"{LEVEL_KO[lv]} · {e.source} · {e.tstr}")
        self.d_msg.set_text(e.msg)
        self.d_grid.set_rows(self._rows_for(e))
        u = unit_of(e)
        self.d_svc.set_visible(bool(u) and "services" in self.win.specs)
        if u:
            self.d_svc.set_tooltip_text(f"서비스 페이지에서 {u[1]} 보기")

    def copy_selected(self, msg_only=False):
        e = self._selected()
        if e is None:
            return
        copy_text(e.msg if msg_only else f"{e.msg}\n\n{DetailGrid.as_text(self._rows_for(e))}\n")
        self.win.toast("클립보드에 복사했습니다", 2)

    def _goto_service(self):
        e = self._selected()
        u = unit_of(e) if e else None
        if u:
            self.win.show_page("services", unit=u[1], scope=u[0])

    # ── 오른쪽 단추 메뉴 · 키 ──
    def _on_key(self, _v, ev):
        if key_is_menu(ev):
            self._menu(ev)
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK and ev.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_selected()
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
        e = self._selected()
        if e is None:
            return
        m = Gtk.Menu()
        menu_item(m, "복사", self.copy_selected)
        menu_item(m, "메시지만 복사", lambda: self.copy_selected(msg_only=True))
        m.append(Gtk.SeparatorMenuItem())
        menu_item(m, f"'{e.source}' 원본만 보기", lambda: self.select_source(e.skey, e.source),
                  sensitive=bool(e.skey) and self.source != e.skey)
        u = unit_of(e)
        if u and "services" in self.win.specs:
            menu_item(m, f"서비스 보기 ({u[1]})", self._goto_service)
        popup(m, self.view_w, ev)


PAGE = {
    "id": "events",
    "title": "이벤트 뷰어",
    "group": "system",
    "order": 10,
    "icon": ["document-open-recent-symbolic", "view-list-bullet-symbolic", "utilities-log-viewer"],
    "build": EventsPage,
}
