"""작업 관리자 — 프로세스 탭 · 세부 정보 탭 · 작업 끝내기.

목록은 매번 새로 만들지 않는다: 행마다 열쇠(앱·창·pid:시작시각)를 두고, 바뀐 칸만 고친다.
그래야 선택·펼침·스크롤이 그대로 남는다. 모델은 저장소 → 거르기(검색) → 정렬 순서로 쌓는다.
"""
import os
import signal

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import taskmgr_data as D  # noqa: E402
from .taskmgr_common import (FALLBACK_ICON, SortHeaders, accent_rgb, cmp_num, cmp_text,  # noqa: E402
                             confirm, conv_down, heat, key_is_delete, key_is_menu, menu_item,
                             notice, open_location, popup, stat_header, text_column)

NEED_ADMIN = "관리자 권한이 필요합니다"
STATE_FULL = {"R": "실행 중", "S": "대기 중", "D": "디스크 대기", "T": "일시 중단됨", "t": "추적 중",
              "Z": "좀비", "I": "유휴", "X": "종료됨", "P": "멈춤"}
# 프로세스 탭은 윈도우처럼 눈여겨볼 상태만 (대기·실행은 비워 둔다 — 대부분이 대기라 소음이 된다)
STATE_SHORT = {"T": "일시 중단됨", "t": "일시 중단됨", "Z": "좀비"}


def can_end(p):
    return p is not None and (D.ME == 0 or p.uid == D.ME)


# ── 작업 끝내기 ──────────────────────────────────────────────
def terminate(targets, after=None):
    """SIGTERM → 3초 뒤에도 살아 있으면 SIGKILL. 기다리는 동안 화면을 막지 않는다 (GLib 타이머).
    targets = [(pid, 시작 시각)] — 그새 pid 가 다른 프로세스에 재사용됐으면 건드리지 않는다."""
    sent, denied = [], 0
    for pid, start in targets:
        if not D.proc_alive(pid, start):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            denied += 1
            continue
        sent.append((pid, start))
        try:
            os.kill(pid, signal.SIGCONT)        # 일시 중단(T)된 프로세스는 계속될 때까지 TERM 을 처리하지 않는다
        except OSError:
            pass
    if sent:
        def kill_rest():
            for pid, start in sent:
                if D.proc_alive(pid, start):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            if after:
                after()
            return False
        GLib.timeout_add(3000, kill_rest)
    return len(sent), denied


def end_processes(win, pids, tree=False, label=None):
    """pids(와 tree 면 그 자손)를 끝낸다. 세션 핵심이 섞여 있으면 먼저 묻는다."""
    procs = (win.snap or {}).get("procs") or {}
    pids = [p for p in pids if p in procs]
    if not pids:
        return
    targets = D.descendants(procs, pids) if tree else set(pids)
    if os.getpid() not in pids:                  # 이 창을 띄운 터미널 등을 끝낼 때 작업 관리자는 남긴다
        targets.discard(os.getpid())
    targets = [p for p in targets if not procs[p].kthread]
    own = [p for p in targets if can_end(procs[p])]
    if not own:
        notice(win, NEED_ADMIN, "다른 계정(시스템)의 프로세스는 관리자 권한으로만 끝낼 수 있습니다.")
        return
    first = procs[pids[0]]
    label = label or first.name

    def go():
        refresh = lambda: win.collector.wake()                         # noqa: E731
        _n, denied = terminate([(p, procs[p].start) for p in own], after=refresh)
        GLib.timeout_add(300, lambda: (refresh(), False)[1])
        if denied or len(own) < len(targets):
            win.toast("일부 프로세스는 다른 계정의 것이라 끝내지 못했습니다")

    core = D.core_pids(procs) & set(own)
    if core:
        names = sorted({procs[p].name for p in core})
        shown = ", ".join(names[:4]) + (" 등" if len(names) > 4 else "")
        confirm(win, "세션에 꼭 필요한 프로세스입니다",
                f"{shown}을(를) 끝내면 작업 표시줄·바탕화면이 사라지거나 로그아웃되어 "
                "저장하지 않은 작업을 잃을 수 있습니다.\n그래도 끝낼까요?", "작업 끝내기", go)
    elif tree and len(own) > 1:
        confirm(win, f"'{label}'의 프로세스 트리를 끝낼까요?",
                f"이 프로세스와 여기서 시작된 프로세스 {len(own) - 1}개가 함께 끝납니다. "
                "저장하지 않은 작업은 사라집니다.", "프로세스 트리 끝내기", go)
    else:
        go()


# ── 프로세스 탭 ──────────────────────────────────────────────
(K_KEY, K_ICON, K_NAME, K_STATUS, K_CPU, K_CPU_T, K_CPU_BG, K_MEM, K_MEM_T, K_MEM_BG,
 K_DISK, K_DISK_T, K_DISK_BG, K_KIND, K_WEIGHT, K_TIP, K_SEARCH, K_ORDER) = range(18)
P_TYPES = (str, GObject.Object, str, str, float, str, str, float, str, str,
           float, str, str, int, int, str, str, int)
HEADER, APP, WINDOW, PROC = range(4)
S_NAME, S_STATUS, S_CPU, S_MEM, S_DISK = range(5)
P_SORTS = {S_NAME: (K_NAME, False), S_STATUS: (K_STATUS, False), S_CPU: (K_CPU, True),
           S_MEM: (K_MEM, True), S_DISK: (K_DISK, True)}
GROUPS = (("h:apps", "앱", 0), ("h:bg", "백그라운드 프로세스", 1), ("h:sys", "시스템 프로세스", 2))


class _Row:
    __slots__ = ("it", "parent", "children", "vals")

    def __init__(self, it, parent, vals):
        self.it, self.parent, self.children, self.vals = it, parent, set(), vals


def _mk_view(model):
    v = Gtk.TreeView(model=model)
    v.set_enable_search(False)                  # 위쪽 검색 칸을 쓴다 (글자를 치면 뜨는 GTK 검색 창 대신)
    v.set_fixed_height_mode(True)
    v.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
    v.get_style_context().add_class("tm-list")
    return v


def _scrolled(view):
    sc = Gtk.ScrolledWindow()
    sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sc.add(view)
    sc.get_style_context().add_class("tm-listbox")
    return sc


class ProcessesPage:
    id = "processes"
    title = "프로세스"
    searchable = True
    wants_procs = True

    def __init__(self, win):
        self.win = win
        self.index = {}                         # 열쇠 → _Row
        self.targets = {}                       # 열쇠 → 끝낼 대상 등
        self.query = ""
        self._vis = None                        # 검색 중일 때 보일 열쇠들
        self._open = {"h:apps": True, "h:bg": True, "h:sys": False}   # 묶음 펼침 (사용자가 바꾼 것 기억)
        self._hdr_has = {}                      # 묶음 머리마다 지난번에 자식이 있었나
        self._auto = False                      # 프로그램이 펼치는 중 — 사용자 선택으로 기억하지 않는다
        self._acc = "57,197,187"

        self.store = Gtk.TreeStore(*P_TYPES)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.sort = Gtk.TreeModelSort(model=self.filter)
        for sid, spec in P_SORTS.items():
            self.sort.set_sort_func(sid, self._cmp, spec)
        self.view = v = _mk_view(self.sort)

        name = text_column("이름", K_NAME, width=320, expand=True, weight=K_WEIGHT, icon=K_ICON)
        status = text_column("상태", K_STATUS, width=110)
        self.cols = {}
        cpu = text_column("", K_CPU_T, width=86, xalign=1.0, bg=K_CPU_BG)
        mem = text_column("", K_MEM_T, width=110, xalign=1.0, bg=K_MEM_BG)
        disk = text_column("", K_DISK_T, width=100, xalign=1.0, bg=K_DISK_BG)
        for c, cap, key in ((cpu, "CPU", "cpu"), (mem, "메모리", "mem"), (disk, "디스크", "disk")):
            w, lbl = stat_header(cap)
            c.set_widget(w)
            self.cols[key] = lbl
        for c in (name, status, cpu, mem, disk):
            v.append_column(c)
        v.set_expander_column(name)
        v.set_tooltip_column(K_TIP)
        self.sorter = SortHeaders(self.sort, win.remember_sort)
        for c, sid, num in ((name, S_NAME, False), (status, S_STATUS, False), (cpu, S_CPU, True),
                            (mem, S_MEM, True), (disk, S_DISK, True)):
            self.sorter.add(c, sid, num)
        sid, order = win.saved_sort(self.id, S_NAME, Gtk.SortType.ASCENDING)
        self.sorter.set(sid, order)

        v.get_selection().connect("changed", lambda *_: self._sync_buttons())
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        v.connect("row-activated", self._on_activate)
        v.connect("row-expanded", lambda _v, it, _p: self._remember(it, True))
        v.connect("row-collapsed", lambda _v, it, _p: self._remember(it, False))
        self.widget = _scrolled(v)

        self.actions = Gtk.Box(spacing=8)
        self.actions.pack_start(win.new_task_button(), False, False, 0)
        self.end_btn = Gtk.Button(label="작업 끝내기")
        self.end_btn.get_style_context().add_class("tm-end")
        self.end_btn.connect("clicked", lambda *_: self.end_selected())
        self.actions.pack_start(self.end_btn, False, False, 0)
        self._sync_buttons()

    # ── 정렬·거르기 ──
    def _cmp(self, model, a, b, spec):
        col, numeric = spec
        ka, oa, va, na = model.get(a, K_KIND, K_ORDER, col, K_NAME)
        kb, ob, vb, nb = model.get(b, K_KIND, K_ORDER, col, K_NAME)
        desc = self.sorter.order == Gtk.SortType.DESCENDING
        if ka == HEADER or kb == HEADER:        # 묶음(앱·백그라운드·시스템)은 늘 같은 순서
            r = cmp_num(oa, ob)
            return -r if desc else r
        r = cmp_num(va, vb) if numeric else cmp_text(va, vb)
        if r == 0:                              # 같으면 이름순 (내림차순이어도 가나다순)
            r = cmp_text(na, nb)
            if desc:
                r = -r
        return r

    def _visible(self, model, it, _data):
        key = model.get_value(it, K_KEY)
        if key is None:
            return False
        if self._vis is not None:
            return key in self._vis
        if model.get_value(it, K_KIND) == HEADER:
            return model.iter_has_child(it)      # 빈 묶음은 숨긴다
        return True

    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q == self.query:
            return
        was = bool(self.query)
        self.query = q
        self._compute_vis()
        self.filter.refilter()
        if q:
            self._auto = True
            self.view.expand_all()
            self._auto = False
        elif was:
            self._auto = True
            self.view.collapse_all()
            self._auto = False
        self._ensure_open()

    def _compute_vis(self):
        """검색어가 든 행 + 그 조상(묶음이 보여야 하므로) + 그 자손(앱을 찾으면 창·프로세스도)"""
        if not self.query:
            self._vis = None
            return
        vis = set()
        for k, r in self.index.items():
            if self.query not in r.vals[K_SEARCH]:
                continue
            cur = k
            while cur and cur not in vis:
                vis.add(cur)
                cur = self.index[cur].parent
            stack = list(r.children)
            while stack:
                c = stack.pop()
                if c not in vis:
                    vis.add(c)
                    stack.extend(self.index[c].children)
        self._vis = vis

    def _remember(self, sort_it, is_open):
        if self._auto or self.query:
            return
        key = self.sort.get_value(sort_it, K_KEY)
        if key in self._open:
            self._open[key] = is_open

    def _ensure_open(self):
        """묶음이 (다시) 보이게 되면 사용자가 둔 대로 펼친다 — 거르기로 숨었다 나오면 GTK 는 접어 버린다"""
        if self.query:
            return
        self._auto = True
        for key, want in self._open.items():
            r = self.index.get(key)
            it = conv_down(self.sort, self.filter, r.it) if r else None
            if it is None:
                continue
            path = self.sort.get_path(it)
            if want and not self.view.row_expanded(path):
                self.view.expand_row(path, False)
            elif not want and self.view.row_expanded(path):
                self.view.collapse_row(path)
        self._auto = False

    # ── 새로 고침 ──
    def refresh(self, snap, clients):
        procs = snap.get("procs")
        if procs is None:
            return
        mem = snap.get("mem") or {}
        self.cols["cpu"].set_text(f"{snap.get('cpu', 0):.0f}%")
        tot = mem.get("total") or 1
        self.cols["mem"].set_text(f"{100 * mem.get('used', 0) / tot:.0f}%")
        busy = max([d["busy"] for d in snap.get("disks") or []] or [0])
        self.cols["disk"].set_text(f"{busy:.0f}%")
        self._acc = accent_rgb(self.view)          # 칸마다 스타일을 묻지 않게 한 번만
        rows, self.targets = self._build(procs, clients, tot)
        self._apply(rows)
        self._recheck_headers()
        if self.query:
            old = self._vis
            self._compute_vis()
            self.filter.refilter()
            if self._vis != old:
                self._auto = True
                self.view.expand_all()
                self._auto = False
        self._ensure_open()
        self._sync_buttons()

    def _recheck_headers(self):
        """묶음 머리는 자식이 있어야 보이는데(_visible), 필터는 자식이 생기거나 없어져도 부모를 다시 보지 않는다 —
        비었다/찼다가 바뀐 머리는 알려 준다 (첫 스냅숏에 창 목록이 없으면 "앱" 묶음이 영영 숨었다)"""
        it = self.store.get_iter_first()
        while it is not None:
            if self.store.get_value(it, K_KIND) == HEADER:
                key, has = self.store.get_value(it, K_KEY), self.store.iter_has_child(it)
                if self._hdr_has.get(key) != has:
                    self._hdr_has[key] = has
                    self.store.row_changed(self.store.get_path(it), it)
            it = self.store.iter_next(it)

    def _vals(self, key, icon, name, status, cpu, mem, disk, kind, tip, search, total, order=0):
        w = self._acc
        num = kind in (APP, PROC)
        return [key, icon, name, status,
                round(cpu, 1) if num else -1.0, D.fmt_pct(cpu) if num else "",
                heat(w, cpu / 40) if num else None,
                round(mem / D.MIB, 1) if num else -1.0, D.fmt_mb(mem) if num else "",
                heat(w, mem / total / 0.15) if num else None,
                round(disk / D.MIB, 1) if num and disk is not None else -1.0,
                D.fmt_rate_mb(disk) if num else "",
                heat(w, (disk or 0) / (30 * D.MIB)) if num else None,
                kind, Pango.Weight.BOLD if kind == HEADER else Pango.Weight.NORMAL,
                GLib.markup_escape_text(tip or ""), (search or "").casefold(), order]

    def _proc_vals(self, p, total, app_icon=None):
        icon = self.win.resolver.icon_for_exe(p.name)
        if icon is FALLBACK_ICON and app_icon is not None:
            icon = app_icon
        return self._vals("p:" + p.key(), icon, p.name, STATE_SHORT.get(p.state, ""), p.cpu, p.mem, p.disk,
                          PROC, p.cmdline(), f"{p.name} {p.pid}", total)

    def _build(self, procs, clients, total):
        """앱(창이 있는 프로세스와 그 자손) · 백그라운드(내 계정의 나머지) · 시스템(다른 계정)"""
        live = {pid: p for pid, p in procs.items() if not p.kthread}
        core = D.core_pids(live)
        ch = D.children_map(live)
        wins = {}
        for c in clients or ():
            # 세션 핵심(Xwayland 등)이 창 주인으로 잡히면 앱으로 묶지 않는다 — 끝내면 모든 X 앱이 죽는다
            if c["pid"] in live and c["pid"] not in core:
                wins.setdefault(c["pid"], []).append(c)
        owner = {}

        def own(pid):
            """가장 가까운 '창 가진' 조상 (자기 포함)"""
            chain, cur, res, n = [], pid, None, 0
            while cur in live and n < 512:
                if cur in owner:
                    res = owner[cur]
                    break
                chain.append(cur)
                if cur in wins:
                    res = cur
                    break
                cur, n = live[cur].ppid, n + 1
            for x in chain:
                owner[x] = res
            return res

        apps, root_app = {}, {}
        for wpid in sorted(wins):
            p = live[wpid]
            aid, name, icon = self.win.resolver.for_window(wins[wpid][0]["class"], p.name)
            a = apps.get(aid)
            if a is None:
                a = apps[aid] = {"id": aid, "name": name, "icon": icon, "pids": [], "wins": {}}
            a["wins"][wpid] = wins[wpid]
            root_app[wpid] = a
        bg, sysp = [], []
        for pid, p in live.items():
            o = own(pid)
            if o is not None:
                root_app[o]["pids"].append(pid)
            elif p.uid == D.ME:
                bg.append(p)
            else:
                sysp.append(p)

        rows, targets = [], {}
        counts = {"h:apps": len(apps), "h:bg": len(bg), "h:sys": len(sysp)}
        hdr = {}
        for key, label, order in GROUPS:
            hdr[key] = self._vals(key, None, f"{label} ({counts[key]})", "", 0, 0, 0, HEADER, "", label,
                                  total, order)
        rows.append(("h:apps", None, hdr["h:apps"]))
        for a in apps.values():
            akey = "a:" + a["id"]
            plist = [live[x] for x in a["pids"]]
            group = set(a["pids"])
            dk = [p.disk for p in plist if p.disk is not None]
            allw = [c for wl in a["wins"].values() for c in wl]
            n = len(plist) if len(plist) > 1 else len(allw)
            name = f"{a['name']} ({n})" if n > 1 else a["name"]
            status = "일시 중단됨" if plist and all(p.state in "Tt" for p in plist) else ""
            main = min(a["wins"])
            rows.append((akey, "h:apps", self._vals(
                akey, a["icon"], name, status, sum(p.cpu for p in plist), sum(p.mem for p in plist),
                sum(dk) if dk else None, APP, live[main].cmdline(), a["name"], total)))
            targets[akey] = {"pids": sorted(group), "tree": False, "name": a["name"], "pid": main,
                             "win": allw[0], "ok": any(can_end(p) for p in plist)}
            if len(plist) == 1:                 # 프로세스가 하나면 창 제목만 (윈도우처럼)
                for c in allw:
                    wkey = "w:" + c["address"]
                    rows.append((wkey, akey, self._win_vals(wkey, a["icon"], c, total)))
                    targets[wkey] = dict(targets[akey], win=c)
                continue
            for p in sorted(plist, key=lambda q: q.pid):
                pkey = "p:" + p.key()
                rows.append((pkey, akey, self._proc_vals(p, total, a["icon"])))
                targets[pkey] = {"pids": [p.pid], "tree": False, "name": p.name, "pid": p.pid,
                                 "win": None, "ok": can_end(p)}
                for c in a["wins"].get(p.pid, ()):
                    wkey = "w:" + c["address"]
                    rows.append((wkey, pkey, self._win_vals(wkey, a["icon"], c, total)))
                    sub = sorted(D.descendants(live, [p.pid], ch) & group)
                    targets[wkey] = {"pids": sub, "tree": False, "name": p.name, "pid": p.pid,
                                     "win": c, "ok": can_end(p)}
        for key, plist in (("h:bg", bg), ("h:sys", sysp)):
            rows.append((key, None, hdr[key]))
            for p in plist:
                pkey = "p:" + p.key()
                rows.append((pkey, key, self._proc_vals(p, total)))
                targets[pkey] = {"pids": [p.pid], "tree": False, "name": p.name, "pid": p.pid,
                                 "win": None, "ok": can_end(p)}
        return rows, targets

    def _win_vals(self, key, icon, c, total):
        title = c["title"] or "(제목 없음)"
        return self._vals(key, icon, title, "", 0, 0, 0, WINDOW, title, title, total)

    def _apply(self, rows):
        """바람직한 행 목록과 지금 저장소를 맞춘다 — 바뀐 칸만 고치고, 없어진 행만 지운다"""
        st = self.store
        seen = set()
        for key, parent, vals in rows:
            seen.add(key)
            r = self.index.get(key)
            if r is not None and r.parent != parent:      # 다른 묶음으로 옮겨 갔다
                self._drop(key)
                r = None
            if r is None:
                pr = self.index.get(parent) if parent else None
                it = st.append(pr.it if pr else None, vals)
                self.index[key] = _Row(it, parent, vals)
                if pr:
                    pr.children.add(key)
            elif r.vals != vals:
                st.set(r.it, {i: v for i, (v, o) in enumerate(zip(vals, r.vals)) if v != o})
                r.vals = vals
        for key in [k for k in self.index if k not in seen]:
            if key in self.index:
                self._drop(key)

    def _drop(self, key):
        r = self.index.pop(key)
        stack = list(r.children)                # 자식 행은 저장소에서 함께 사라진다 — 색인에서도
        while stack:
            c = self.index.pop(stack.pop(), None)
            if c:
                stack.extend(c.children)
        if r.parent in self.index:
            self.index[r.parent].children.discard(key)
        self.store.remove(r.it)

    # ── 선택·동작 ──
    def _selected(self):
        model, it = self.view.get_selection().get_selected()
        if it is None:
            return None, None
        key = model.get_value(it, K_KEY)
        return key, self.targets.get(key)

    def _sync_buttons(self):
        _key, t = self._selected()
        self.end_btn.set_sensitive(bool(t and t["ok"]))
        self.end_btn.set_tooltip_text(NEED_ADMIN if t and not t["ok"] else None)

    def end_selected(self):
        _key, t = self._selected()
        if t and t["ok"]:
            end_processes(self.win, t["pids"], tree=t["tree"], label=t["name"])

    def _on_key(self, _v, ev):
        if key_is_delete(ev):
            self.end_selected()
            return True
        if key_is_menu(ev):
            self._menu(ev)
            return True
        return False

    def _on_press(self, v, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        hit = v.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return True
        v.get_selection().select_path(hit[0])
        v.set_cursor(hit[0], None, False)
        self._menu(ev)
        return True

    def _menu(self, ev):
        key, t = self._selected()
        if not t:
            return
        m = Gtk.Menu()
        if t.get("win"):
            menu_item(m, "전환", lambda: self.win.switch_to(t["win"]))
        menu_item(m, "작업 끝내기", self.end_selected, sensitive=t["ok"],
                  tooltip=None if t["ok"] else NEED_ADMIN)
        m.append(Gtk.SeparatorMenuItem())
        procs = (self.win.snap or {}).get("procs") or {}
        p = procs.get(t["pid"])
        path = D.exe_path(p) if p else None
        menu_item(m, "파일 위치 열기", lambda: open_location(path), sensitive=bool(path))
        menu_item(m, "세부 정보로 이동", lambda: self.win.goto_details(t["pid"]))
        popup(m, self.view, ev)

    def _on_activate(self, v, path, _col):
        key = self.sort.get_value(self.sort.get_iter(path), K_KEY)
        t = self.targets.get(key)
        if key.startswith("w:") and t and t.get("win"):
            self.win.switch_to(t["win"])
        elif v.row_expanded(path):
            v.collapse_row(path)
        else:
            v.expand_row(path, False)


# ── 세부 정보 탭 ─────────────────────────────────────────────
(E_KEY, E_ICON, E_NAME, E_PID, E_STATUS, E_USER, E_CPU, E_CPU_T, E_MEM, E_MEM_T,
 E_CMD, E_UID, E_SEARCH, E_TIP) = range(14)
E_TYPES = (str, GObject.Object, str, int, str, str, float, str, float, str, str, int, str, str)
E_NUMERIC = {E_PID, E_CPU, E_MEM}


class DetailsPage:
    id = "details"
    title = "세부 정보"
    searchable = True
    wants_procs = True

    def __init__(self, win):
        self.win = win
        self.index = {}                         # 열쇠 → [iter, 값]
        self.by_pid = {}
        self.query = ""
        self.store = Gtk.ListStore(*E_TYPES)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(lambda m, it, _d: not self.query or self.query in (m.get_value(it, E_SEARCH) or ""))
        # 정렬은 GTK 의 기본 열 비교(C)로 — 수백 행을 다시 정렬할 때 파이썬 비교 함수가 가장 비싸다
        self.sort = Gtk.TreeModelSort(model=self.filter)
        self.view = v = _mk_view(self.sort)
        cols = [text_column("이름", E_NAME, width=220, icon=E_ICON),
                text_column("PID", E_PID, width=76, xalign=1.0),
                text_column("상태", E_STATUS, width=96),
                text_column("사용자 이름", E_USER, width=110),
                text_column("CPU", E_CPU_T, width=70, xalign=1.0),
                text_column("메모리", E_MEM_T, width=100, xalign=1.0),
                text_column("명령줄", E_CMD, width=360, expand=True)]
        for c in cols:
            v.append_column(c)
        v.set_tooltip_column(E_TIP)                # 툴팁은 마크업으로 읽힌다 — 이스케이프한 열
        self.sorter = SortHeaders(self.sort, self.win.remember_sort)
        for c, sid in zip(cols, (E_NAME, E_PID, E_STATUS, E_USER, E_CPU, E_MEM, E_CMD)):
            self.sorter.add(c, sid, sid in E_NUMERIC)
        sid, order = win.saved_sort(self.id, E_NAME, Gtk.SortType.ASCENDING)
        self.sorter.set(sid, order)
        v.get_selection().connect("changed", lambda *_: self._sync_buttons())
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        self.widget = _scrolled(v)

        self.actions = Gtk.Box(spacing=8)
        self.actions.pack_start(win.new_task_button(), False, False, 0)
        self.end_btn = Gtk.Button(label="작업 끝내기")
        self.end_btn.get_style_context().add_class("tm-end")
        self.end_btn.connect("clicked", lambda *_: self.end_selected(False))
        self.actions.pack_start(self.end_btn, False, False, 0)
        self._sync_buttons()

    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q != self.query:
            self.query = q
            self.filter.refilter()

    def refresh(self, snap, _clients=None):
        procs = snap.get("procs")
        if procs is None:
            return
        res = self.win.resolver
        seen = set()
        for p in procs.values():
            if p.kthread:
                continue
            key = p.key()
            seen.add(key)
            cmd = p.cmdline()
            vals = [key, res.icon_for_exe(p.name), p.name, p.pid, STATE_FULL.get(p.state, p.state), p.user,
                    round(p.cpu, 1), D.fmt_pct(p.cpu), round(p.mem / D.MIB, 1), D.fmt_mb(p.mem), cmd, p.uid,
                    f"{p.name} {p.pid} {p.user} {cmd}".casefold(), GLib.markup_escape_text(cmd)]
            ent = self.index.get(key)
            if ent is None:
                self.index[key] = [self.store.append(vals), vals]
                self.by_pid[p.pid] = key
            elif ent[1] != vals:
                self.store.set(ent[0], {i: v for i, (v, o) in enumerate(zip(vals, ent[1])) if v != o})
                ent[1] = vals
        for key in [k for k in self.index if k not in seen]:
            it, vals = self.index.pop(key)
            if self.by_pid.get(vals[E_PID]) == key:
                del self.by_pid[vals[E_PID]]
            self.store.remove(it)
        self._sync_buttons()

    def select_pid(self, pid):
        key = self.by_pid.get(pid)
        if key is None:
            return False
        it = conv_down(self.sort, self.filter, self.index[key][0])
        if it is None:                          # 검색으로 숨었다 — 검색을 비운다
            self.win.clear_search()
            it = conv_down(self.sort, self.filter, self.index[key][0])
        if it is None:
            return False
        path = self.sort.get_path(it)
        self.view.get_selection().select_path(path)
        self.view.set_cursor(path, None, False)
        self.view.scroll_to_cell(path, None, True, 0.4, 0.0)
        self.view.grab_focus()
        return True

    def _selected(self):
        model, it = self.view.get_selection().get_selected()
        if it is None:
            return None
        pid = model.get_value(it, E_PID)
        return ((self.win.snap or {}).get("procs") or {}).get(pid)

    def _sync_buttons(self):
        p = self._selected()
        self.end_btn.set_sensitive(can_end(p))
        self.end_btn.set_tooltip_text(NEED_ADMIN if p is not None and not can_end(p) else None)

    def end_selected(self, tree):
        p = self._selected()
        if can_end(p):
            end_processes(self.win, [p.pid], tree=tree)

    def _on_key(self, _v, ev):
        if key_is_delete(ev):
            self.end_selected(bool(ev.state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if key_is_menu(ev):
            self._menu(ev)
            return True
        return False

    def _on_press(self, v, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        hit = v.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return True
        v.get_selection().select_path(hit[0])
        v.set_cursor(hit[0], None, False)
        self._menu(ev)
        return True

    def _menu(self, ev):
        p = self._selected()
        if p is None:
            return
        ok = can_end(p)
        m = Gtk.Menu()
        tip = None if ok else NEED_ADMIN
        menu_item(m, "작업 끝내기", lambda: self.end_selected(False), sensitive=ok, tooltip=tip)
        menu_item(m, "프로세스 트리 끝내기", lambda: self.end_selected(True), sensitive=ok, tooltip=tip)
        m.append(Gtk.SeparatorMenuItem())
        path = D.exe_path(p)
        menu_item(m, "파일 위치 열기", lambda: open_location(path), sensitive=bool(path))
        menu_item(m, "명령줄 복사", lambda: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(p.cmdline(), -1))
        popup(m, self.view, ev)
