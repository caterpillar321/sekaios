"""파일 탐색기 — 폴더 하나의 목록 (읽기 · 바뀜 따라가기 · 정렬 · 숨긴 항목).

GtkListStore 의 행 순서는 여기서 직접 정한다 (정렬 함수를 GTK 에 맡기면 파이썬 비교 함수가
행마다 불려 10,000개 폴더에서 느리다):
    self.asc   — 보이는 항목의 정렬 열쇠를 오름차순으로 (common.sort_key — 폴더 먼저, 끝은 URI)
    화면 순서  — 오름차순이면 그대로, 내림차순이면 폴더 묶음·파일 묶음을 각각 뒤집는다
                 (윈도우 탐색기처럼 폴더는 내림차순에서도 위에)
    넣기·빼기는 bisect 로 자리를 찾아 store 의 그 자리에 (GtkListStore 는 GSequence 라 O(log n))
    정렬을 바꾸면 store.reorder 한 번 (선택이 그대로 남는다)

읽기: enumerate_children_async 로 한 번에 256개씩. 처음 200ms 동안 모은 것은 정렬해 새 store 에
한꺼번에 채우고(보기에 붙이기 전), 그 뒤로 오는 것은 한 줄씩 제자리에 끼운다 — 로컬 폴더는 대개
200ms 안에 다 읽혀 한 번에 뜨고, 느린 네트워크 폴더는 읽는 대로 보인다.
바뀜: Gio.FileMonitor — 알림을 200ms 모았다가 바뀐 파일만 작업 스레드에서 다시 읽는다.

부르는 쪽(창)에 알리는 것 — on_event(이름, 값):
    "store"   새 store 로 바뀜 (보기에 다시 붙인다)
    "added"   [uri] 줄이 생김 (고르기를 기다리던 것이 있으면)
    "changed" 항목 수·크기가 바뀜 (상태 표시줄)
    "loaded"  다 읽음
    "error"   읽지 못함 (한국어 이유)
    "gone"    폴더 자체가 지워지거나 옮겨짐
"""
import bisect
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .common import ATTRS, Entry, dbg, fmt_date, fmt_size, icons, sort_key, type_desc  # noqa: E402

# 모델의 열
C_URI, C_NAME, C_SHORT, C_PIX_S, C_PIX_M, C_PIX_L, C_DATE, C_TYPE, C_SIZE, C_EXTRA, C_EXTRA2, C_SENS = range(12)
COL_TYPES = (str, str, str, GdkPixbuf.Pixbuf, GdkPixbuf.Pixbuf, GdkPixbuf.Pixbuf,
             str, str, str, str, str, bool)
ALL_COLS = list(range(len(COL_TYPES)))

# 아이콘 크기 — 윈도우의 자세히(16) · 보통 아이콘(48) · 큰 아이콘(96)
PX_S, PX_M, PX_L = 16, 48, 96

BATCH = 256
FIRST_FLUSH_MS = 200


def load_error_text(err):
    """폴더를 읽지 못한 이유 (윈도우 탐색기의 말투)"""
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED):
        return "이 폴더에 액세스할 수 있는 권한이 없습니다."
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
        return "위치를 찾을 수 없습니다. 폴더가 옮겨졌거나 지워졌을 수 있습니다."
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_DIRECTORY):
        return "폴더가 아닙니다."
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED):
        return "연결되지 않은 위치입니다."
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
        return "이 위치는 열 수 없습니다."
    return f"이 위치를 열 수 없습니다.\n{err.message}"


class FolderModel:
    """kind: "folder"(보통 폴더) · "trash"(휴지통 — 원래 위치·삭제한 날짜) · "search"(검색 결과 — 위치)"""

    def __init__(self, kind, sort, desc, show_hidden, fitter, on_event):
        self.kind = kind
        self.sort = sort
        self.desc = desc
        self.show_hidden = show_hidden
        self.fitter = fitter                    # 아이콘 보기의 두 줄 이름 (폭이 0 이면 자르지 않음)
        self.on_event = on_event
        self.dir = None
        self.entries = {}                       # uri → Entry (숨긴 것도)
        self.dimmed = set()                     # 잘라내기 한 항목 (흐리게)
        self.asc = []
        self.ndirs = 0
        self.store = Gtk.ListStore(*COL_TYPES)
        self.loading = False
        self.error = None
        self.gen = 0
        self._cancel = None
        self._pending = []
        self._first = True                      # 아직 store 를 한 번도 채우지 않음
        self._flush_src = 0
        self._mon = None
        self._changed = {}
        self._change_src = 0
        self._change_busy = False
        self.started = 0.0

    # ── 읽기 ──
    def load(self, gfile):
        self.stop()
        self.gen += 1
        gen = self.gen
        self.dir = gfile
        self.entries = {}
        self.asc = []
        self.ndirs = 0
        self._pending = []
        self._first = True
        self.error = None
        self.loading = True
        self.started = time.monotonic()
        self._replace_store([])
        self._cancel = Gio.Cancellable()
        gfile.enumerate_children_async(ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT,
                                       self._cancel, self._enum_ready, gen)
        self._flush_src = GLib.timeout_add(FIRST_FLUSH_MS, self._flush_tick, gen)
        self._start_monitor()

    def begin_search(self):
        """검색 결과 모델 — 읽지 않고 add_entries 로 받는다"""
        self.stop()
        self.gen += 1
        self.entries = {}
        self.asc = []
        self.ndirs = 0
        self._pending = []
        self._first = True
        self.loading = True
        self.error = None
        self._replace_store([])

    def stop(self):
        """읽기·감시를 멈춘다 (다른 곳으로 갈 때 · 창을 닫을 때)"""
        self.gen += 1
        if self._cancel is not None:
            self._cancel.cancel()
            self._cancel = None
        for attr in ("_flush_src", "_change_src"):
            src = getattr(self, attr)
            if src:
                GLib.source_remove(src)
                setattr(self, attr, 0)
        if self._mon is not None:
            self._mon.cancel()
            self._mon = None
        self._changed = {}
        self.loading = False

    def _enum_ready(self, src, res, gen):
        if gen != self.gen:
            return
        try:
            en = src.enumerate_children_finish(res)
        except GLib.Error as e:
            if gen == self.gen:
                self._fail(e)
            return
        en.next_files_async(BATCH, GLib.PRIORITY_DEFAULT, self._cancel, self._got, gen)

    def _got(self, en, res, gen):
        if gen != self.gen:
            try:
                en.close_async(GLib.PRIORITY_LOW, None, None, None)
            except Exception:
                pass
            return
        try:
            infos = en.next_files_finish(res)
        except GLib.Error as e:
            if gen == self.gen:
                self._flush()
                self._fail(e)
            return
        if not infos:
            en.close_async(GLib.PRIORITY_LOW, None, None, None)
            self._finish(gen)
            return
        d = self.dir
        for info in infos:
            try:
                self._pending.append(Entry.from_info(d, info))
            except Exception as ex:             # 이상한 항목 하나 때문에 폴더 전체가 안 보이면 안 된다
                dbg("항목을 읽지 못함", ex)
        en.next_files_async(BATCH, GLib.PRIORITY_DEFAULT, self._cancel, self._got, gen)

    def _flush_tick(self, gen):
        if gen != self.gen:
            return False
        self._flush()
        return True                             # 읽는 동안 200ms 마다

    def _finish(self, gen):
        if self._flush_src:
            GLib.source_remove(self._flush_src)
            self._flush_src = 0
        self._flush()
        self.loading = False
        self.on_event("loaded", None)
        if self._changed:
            self._schedule_changes()

    def _fail(self, err):
        if self._flush_src:
            GLib.source_remove(self._flush_src)
            self._flush_src = 0
        self.loading = False
        self.error = load_error_text(err)
        self.on_event("error", self.error)

    def _flush(self):
        """모아 둔 항목을 목록에 — 처음이면 정렬해 새 store 로, 아니면 한 줄씩 제자리에"""
        if not self._pending:
            if self._first and not self.loading:
                self._first = False
            return
        batch, self._pending = self._pending, []
        self.add_entries(batch)

    def add_entries(self, batch):
        fresh = {}
        for e in batch:
            if e.uri in fresh:                  # 한 묶음에 같은 것이 두 번 — 나중 것으로
                fresh[e.uri] = self.entries[e.uri] = e
                continue
            old = self.entries.get(e.uri)
            if old is not None:                 # 읽는 동안 감시가 먼저 넣은 것
                self._update(old, e)
                continue
            self.entries[e.uri] = e
            fresh[e.uri] = e
        fresh = list(fresh.values())
        vis = [e for e in fresh if self._visible(e)]
        if self._first and not self.asc:
            # 처음 — 정렬해 붙이기 전의 새 store 에 한꺼번에
            self._first = False
            for e in vis:
                e.key = sort_key(e, self.sort)
            vis.sort(key=lambda e: e.key)
            self.asc = [e.key for e in vis]
            self.ndirs = sum(1 for e in vis if e.is_dir)
            self._replace_store(self._display_entries())
        else:
            self._first = False
            for e in vis:
                self._insert(e)
        for e in fresh:
            if not self._visible(e):
                e.key = sort_key(e, self.sort)
        if fresh:
            self.on_event("added", [e.uri for e in vis])
            self.on_event("changed", None)

    # ── 화면 순서 ↔ 오름차순 ──
    def _disp(self, i, n=None, nd=None):
        n = len(self.asc) if n is None else n
        nd = self.ndirs if nd is None else nd
        if not self.desc:
            return i
        if i < nd:
            return nd - 1 - i
        return nd + (n - 1 - i)

    def _asc_index(self, pos):
        if not self.desc:
            return pos
        n, nd = len(self.asc), self.ndirs
        if pos < nd:
            return nd - 1 - pos
        return n - 1 - (pos - nd)

    def _display_entries(self):
        out = [None] * len(self.asc)
        for i, k in enumerate(self.asc):
            out[self._disp(i)] = self.entries[k[-1]]
        return out

    def display_uris(self):
        out = [None] * len(self.asc)
        for i, k in enumerate(self.asc):
            out[self._disp(i)] = k[-1]
        return out

    def entry_at(self, pos):
        """화면 자리(TreePath 의 번호) → Entry"""
        if pos is None or pos < 0 or pos >= len(self.asc):
            return None
        return self.entries.get(self.asc[self._asc_index(pos)][-1])

    def pos_of(self, uri):
        e = self.entries.get(uri)
        if e is None or e.key is None or not self._visible(e):
            return None
        i = bisect.bisect_left(self.asc, e.key)
        if i < len(self.asc) and self.asc[i] == e.key:
            return self._disp(i)
        return None

    def iter_of(self, uri):
        pos = self.pos_of(uri)
        if pos is None:
            return None
        return self.store.iter_nth_child(None, pos)

    def count(self):
        return len(self.asc)

    # ── 행 ──
    def _row(self, e):
        ic = icons()
        g = e.gicon
        th = e.thumb or {}
        extra = extra2 = ""
        if self.kind == "trash":
            extra, extra2 = e.orig, fmt_date(e.dtime)
        elif self.kind == "search":
            extra = e.loc
        short = self.fitter.cached(e.name)
        if short is None:
            e.short, e.short_w = e.name, None   # 보이게 되면 잰다 (fit_range)
        else:
            e.short, e.short_w = short, self.fitter.key
        return [e.uri, e.name, e.short,
                ic.get(g, PX_S), th.get(PX_M) or ic.get(g, PX_M), th.get(PX_L) or ic.get(g, PX_L),
                fmt_date(e.mtime), type_desc(e.ctype, e.is_dir, e.name), "" if e.is_dir else fmt_size(e.size),
                extra, extra2, e.uri not in self.dimmed]

    def _replace_store(self, ents):
        st = Gtk.ListStore(*COL_TYPES)
        for e in ents:
            st.insert_with_valuesv(-1, ALL_COLS, self._row(e))
        self.store = st
        self.on_event("store", st)

    def _insert(self, e):
        e.key = sort_key(e, self.sort)
        i = bisect.bisect_left(self.asc, e.key)
        self.asc.insert(i, e.key)
        if e.is_dir:
            self.ndirs += 1
        self.store.insert_with_valuesv(self._disp(i), ALL_COLS, self._row(e))

    def _remove_row(self, e):
        if e.key is None:
            return False
        i = bisect.bisect_left(self.asc, e.key)
        if i >= len(self.asc) or self.asc[i] != e.key:
            return False
        pos = self._disp(i)
        del self.asc[i]
        if e.is_dir:
            self.ndirs -= 1
        it = self.store.iter_nth_child(None, pos)
        if it is not None:
            self.store.remove(it)
        return True

    def _visible(self, e):
        return self.show_hidden or not e.hidden

    def _update(self, old, e):
        """같은 URI 의 새 정보 — 자리가 같으면 글자만, 다르면 빼고 다시 넣는다"""
        if old.mtime == e.mtime and old.size == e.size:
            e.thumb, e.thumb_req = old.thumb, old.thumb_req
        self.entries[e.uri] = e
        if (old.name, old.mtime, old.size, old.ctype, old.is_dir, old.hidden, old.orig, old.dtime) == \
                (e.name, e.mtime, e.size, e.ctype, e.is_dir, e.hidden, e.orig, e.dtime) and old.key is not None:
            # 보이는 것이 그대로 — 행을 건드리지 않는다 (이름을 고치는 중인 칸이 닫히지 않게)
            e.key = old.key
            e.thumb, e.thumb_req = old.thumb, old.thumb_req
            e.short, e.short_w = old.short, old.short_w
            return False
        was = self._visible(old) and old.key is not None and self._has_key(old.key)
        now = self._visible(e)
        e.key = sort_key(e, self.sort)
        if was and now and e.key == old.key and e.is_dir == old.is_dir:
            it = self.iter_of(e.uri)
            if it is not None:
                self.store.set(it, ALL_COLS, self._row(e))
            return False
        if was:
            self._remove_row(old)
        if now:
            self._insert(e)
        return True

    def _has_key(self, k):
        i = bisect.bisect_left(self.asc, k)
        return i < len(self.asc) and self.asc[i] == k

    def upsert(self, e):
        old = self.entries.get(e.uri)
        if old is None:
            self.add_entries([e])
            return
        self._update(old, e)
        self.on_event("changed", None)

    def remove(self, uri):
        e = self.entries.pop(uri, None)
        if e is None:
            return
        if self._visible(e):
            self._remove_row(e)
        self.on_event("changed", None)

    def refresh_row(self, uri):
        it = self.iter_of(uri)
        e = self.entries.get(uri)
        if it is not None and e is not None:
            self.store.set(it, ALL_COLS, self._row(e))

    # ── 정렬 · 숨긴 항목 · 아이콘 ──
    def set_sort(self, field, desc):
        if field == self.sort and desc == self.desc:
            return
        old = self.display_uris()
        self.sort, self.desc = field, desc
        for e in self.entries.values():
            e.key = sort_key(e, field)
        vis = [e for e in self.entries.values() if self._visible(e)]
        self.asc = sorted(e.key for e in vis)
        self.ndirs = sum(1 for e in vis if e.is_dir)
        new = self.display_uris()
        if len(old) == len(new) and len(new) > 1:
            where = {u: i for i, u in enumerate(old)}
            try:
                self.store.reorder([where[u] for u in new])
                return
            except (KeyError, TypeError) as ex:
                dbg("다시 정렬 실패 — 새로 채움", ex)
        self._replace_store(self._display_entries())

    def set_show_hidden(self, on):
        if on == self.show_hidden:
            return
        hidden = [e for e in self.entries.values() if e.hidden]
        if on:
            self.show_hidden = True
            if len(hidden) > 2000:
                self._rebuild()
            else:
                for e in hidden:
                    self._insert(e)
        else:
            if len(hidden) > 2000:
                self.show_hidden = False
                self._rebuild()
            else:
                for e in hidden:
                    self._remove_row(e)
                self.show_hidden = False
        self.on_event("changed", None)

    def _rebuild(self):
        vis = [e for e in self.entries.values() if self._visible(e)]
        for e in vis:
            e.key = sort_key(e, self.sort)
        self.asc = sorted(e.key for e in vis)
        self.ndirs = sum(1 for e in vis if e.is_dir)
        self._replace_store(self._display_entries())

    def fit_range(self, a, b):
        """보이는 항목의 이름을 지금 폭에 맞춰 두 줄로 (보이지 않는 것은 보일 때)"""
        w = self.fitter.key
        n = len(self.asc)
        for pos in range(max(0, a), min(n, b + 1)):
            e = self.entry_at(pos)
            if e is None or e.short_w == w:
                continue
            s = self.fitter.fit(e.name)
            e.short_w = w
            if s != e.short:
                e.short = s
                it = self.store.iter_nth_child(None, pos)
                if it is not None:
                    self.store.set_value(it, C_SHORT, s)

    def refill_icons(self):
        """아이콘 테마가 바뀜 (다크 ↔ 라이트)"""
        ic = icons()
        it = self.store.get_iter_first()
        pos = 0
        while it is not None:
            e = self.entry_at(pos)
            if e is not None:
                th = e.thumb or {}
                self.store.set(it, [C_PIX_S, C_PIX_M, C_PIX_L],
                               [ic.get(e.gicon, PX_S), th.get(PX_M) or ic.get(e.gicon, PX_M),
                                th.get(PX_L) or ic.get(e.gicon, PX_L)])
            it = self.store.iter_next(it)
            pos += 1

    def set_thumb(self, uri, pixbuf_m, pixbuf_l):
        e = self.entries.get(uri)
        if e is None:
            return
        e.thumb = {PX_M: pixbuf_m, PX_L: pixbuf_l}
        it = self.iter_of(uri)
        if it is not None:
            self.store.set(it, [C_PIX_M, C_PIX_L], [pixbuf_m, pixbuf_l])

    def set_dimmed(self, uris):
        """잘라내기 한 항목은 흐리게 (윈도우처럼) — 다시 읽어도 그대로 남게 집합으로 둔다"""
        uris = set(uris)
        changed = (self.dimmed - uris) | (uris - self.dimmed)
        self.dimmed = uris
        for u in changed:
            it = self.iter_of(u)
            if it is not None:
                self.store.set_value(it, C_SENS, u not in uris)

    # ── 바뀜 따라가기 ──
    def _start_monitor(self):
        if self.dir is None:
            return
        try:
            self._mon = self.dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as e:
            dbg("폴더를 감시할 수 없습니다", self.dir.get_uri(), e.message)
            self._mon = None
            return
        self._mon.connect("changed", self._on_monitor)

    def _on_monitor(self, _m, f, other, ev):
        E = Gio.FileMonitorEvent
        if f is not None and self.dir is not None and f.equal(self.dir):
            if ev in (E.DELETED, E.MOVED_OUT, E.UNMOUNTED):
                GLib.idle_add(lambda: (self.on_event("gone", None), False)[1])
            return
        if ev == E.CHANGES_DONE_HINT:
            return                              # CHANGED 에서 이미 모은다
        for g in (f, other):
            if g is not None:
                p = g.get_parent()
                if p is not None and self.dir is not None and p.equal(self.dir):
                    self._changed[g.get_uri()] = g
        if self._changed:
            self._schedule_changes()

    def _schedule_changes(self):
        if self._change_src or self._change_busy or self.loading:
            return                              # 읽는 중이면 다 읽은 뒤에 (_finish 가 다시 부른다)
        self._change_src = GLib.timeout_add(200, self._apply_changes)

    def _apply_changes(self):
        self._change_src = 0
        if not self._changed:
            return False
        files = list(self._changed.values())
        self._changed = {}
        self._change_busy = True
        gen = self.gen

        def work():
            out = []
            for g in files:
                try:
                    info = g.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
                    out.append((g.get_uri(), Entry.from_info(None, info, gfile=g)))
                except GLib.Error:
                    out.append((g.get_uri(), None))
                except Exception as ex:
                    dbg("바뀐 항목을 읽지 못함", ex)
            GLib.idle_add(done, out)

        def done(out):
            self._change_busy = False
            if gen != self.gen:
                return False
            added = []
            for uri, e in out:
                if e is None:
                    self.remove(uri)
                elif uri in self.entries:
                    self._update(self.entries[uri], e)
                else:
                    added.append(e)
            if added:
                self.add_entries(added)
            self.on_event("changed", None)
            if self._changed:
                self._schedule_changes()
            return False
        threading.Thread(target=work, daemon=True, name="sekai-files-mon").start()
        return False
