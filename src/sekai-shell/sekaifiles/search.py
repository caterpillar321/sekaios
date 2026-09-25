"""파일 탐색기 — 지금 폴더 아래를 이름으로 찾기 (작업 스레드).

대소문자를 가리지 않는 부분 일치. 숨긴 항목(점 파일 · 폴더의 .hidden 목록)은 '숨긴 항목 표시'일 때만.
찾는 대로 0.1초마다 모아서 메인 스레드로 보낸다 (결과가 흘러들어오듯 보이게).
로컬 폴더는 os.scandir (빠르다), 그 밖(휴지통 · 네트워크)은 GIO 로 읽는다.
심볼릭 링크 폴더는 따라 들어가지 않는다 (같은 곳을 끝없이 도는 것을 막는다).
"""
import os
import threading
import time

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .common import ATTRS, Entry, display_dir, local_path  # noqa: E402

MAX_RESULTS = 20000
SKIP_ROOTS = {"/proc", "/sys", "/dev", "/run", "/tmp/.X11-unix"}


def _hidden_list(dirpath):
    """폴더의 .hidden 파일 — 한 줄에 이름 하나 (GIO·Nautilus 와 같은 규칙)"""
    try:
        with open(os.path.join(dirpath, ".hidden"), encoding="utf-8", errors="replace") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except OSError:
        return set()


class Searcher:
    """on_batch([Entry]) · on_done(찾은 수, 너무 많아 멈췄는지) — 둘 다 메인 스레드에서"""

    def __init__(self, base_uri, query, show_hidden, on_batch, on_done):
        self.base_uri = base_uri
        self.q = query.casefold()
        self.show_hidden = show_hidden
        self.on_batch = on_batch
        self.on_done = on_done
        self._stop = threading.Event()
        self.found = 0
        self._buf = []
        self._last = 0.0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="sekai-files-search").start()

    def cancel(self):
        self._stop.set()

    @property
    def cancelled(self):
        return self._stop.is_set()

    def _emit(self, force=False):
        now = time.monotonic()
        if not self._buf or (not force and now - self._last < 0.1):
            return
        batch, self._buf = self._buf, []
        self._last = now

        def go():
            if not self._stop.is_set():
                self.on_batch(batch)
            return False
        GLib.idle_add(go)

    def _add(self, e):
        self._buf.append(e)
        self.found += 1
        self._emit()

    def _run(self):
        truncated = False
        try:
            p = local_path(self.base_uri)
            if p:
                truncated = self._walk_local(p)
            else:
                truncated = self._walk_gio(Gio.File.new_for_uri(self.base_uri))
        except Exception as ex:                  # 찾기가 죽어도 창은 살아야 한다
            print("[sekai-files] 검색 오류:", ex, flush=True)
        self._emit(force=True)

        def fin():
            if not self._stop.is_set():
                self.on_done(self.found, truncated)
            return False
        GLib.idle_add(fin)

    def _walk_local(self, base):
        stack = [base]
        q = self.q
        while stack:
            if self._stop.is_set():
                return False
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                ents = list(it)
            hidden = _hidden_list(d) if not self.show_hidden and any(x.name == ".hidden" for x in ents) else ()
            loc = display_dir(d)
            subdirs = []
            for de in ents:
                name = de.name
                if not self.show_hidden and (name.startswith(".") or name in hidden or name.endswith("~")):
                    continue
                try:
                    is_dir = de.is_dir()
                    is_link_dir = is_dir and de.is_symlink()
                except OSError:
                    is_dir = is_link_dir = False
                if is_dir and not is_link_dir:
                    full = os.path.join(d, name)
                    if full not in SKIP_ROOTS:
                        subdirs.append(full)
                if q in name.casefold():
                    try:
                        st = de.stat()
                        size, mtime, mode = st.st_size, st.st_mtime, st.st_mode
                    except OSError:
                        size, mtime, mode = 0, 0, 0
                    self._add(Entry.from_path(os.path.join(d, name), name, is_dir, size, mtime, mode, loc))
                    if self.found >= MAX_RESULTS:
                        return True
            # 이름 순으로 들어가도록 거꾸로 쌓는다 (결과가 대략 가나다순으로 흘러든다)
            subdirs.sort(reverse=True)
            stack.extend(subdirs)
        return False

    def _walk_gio(self, base):
        stack = [base]
        q = self.q
        while stack:
            if self._stop.is_set():
                return False
            d = stack.pop()
            try:
                en = d.enumerate_children(ATTRS, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
            except GLib.Error:
                continue
            try:
                parent_label = GLib.uri_unescape_string(d.get_uri(), None) or d.get_uri()
                for info in en:
                    if self._stop.is_set():
                        return False
                    if (info.get_is_hidden() or info.get_is_backup()) and not self.show_hidden:
                        continue
                    child = d.get_child(info.get_name())
                    if info.get_file_type() == Gio.FileType.DIRECTORY:
                        stack.append(child)
                    if q in (info.get_display_name() or "").casefold():
                        e = Entry.from_info(d, info, gfile=child)
                        e.loc = e.orig or parent_label
                        self._add(e)
                        if self.found >= MAX_RESULTS:
                            return True
            except GLib.Error:
                continue
            finally:
                try:
                    en.close(None)
                except GLib.Error:
                    pass
        return False
