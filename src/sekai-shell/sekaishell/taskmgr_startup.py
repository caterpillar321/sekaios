"""작업 관리자 — 시작 앱 탭 (XDG 자동 시작).

무엇이 실행되는지는 sekaishell.autostart — 로그인할 때 실행기(/usr/lib/sekai/autostart)와 같은 규칙.
사용자 폴더(~/.config/autostart)의 파일이 시스템 폴더(/etc/xdg/autostart)의 같은 이름을 가린다 (XDG 규칙).
  사용 안 함  사용자 폴더의 같은 이름 파일에 Hidden=true. 시스템 항목이면 그 내용을 복사해 덮어쓴다.
  다시 사용   우리가 만든 덮어쓰기(X-Sekai-Override=true)면 지워서 시스템 항목으로 돌리고,
              사용자가 직접 만든 항목이면 Hidden=false.
SekaiOS 세션이 직접 띄우는 것, NoDisplay=true(시스템 구성 요소), 이 데스크톱에서는 실행하지 않거나
프로그램이 없는 항목은 목록에 보이지 않는다 (켜고 꺼도 달라지지 않는다).
"""
import os
import subprocess
import tempfile
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import autostart  # noqa: E402
from .taskmgr_common import (FALLBACK_ICON, SortHeaders, key_is_menu, menu_item,  # noqa: E402
                             notice, open_location, popup, text_column)

OVERRIDE_KEY = "X-Sekai-Override"
GROUP = autostart.GROUP


autostart_dirs = autostart.autostart_dirs
entry_enabled = autostart.entry_enabled


def entry_note(it):
    """참고 한 줄 — 로그인하고 얼마 뒤에 시작하는지 (X-GNOME-Autostart-Delay)"""
    d = autostart.delay_of(it)
    return f"로그인 {d:.0f}초 뒤" if d >= 1 else ""


def scan():
    """목록에 보일 항목 — 세션이 직접 띄우는 것·시스템 구성 요소(NoDisplay)는 뺀다"""
    items = []
    for it in autostart.scan():
        # 켜고 꺼도 달라지지 않는 것은 보이지 않는다 — 이 데스크톱에서는 실행하지 않는 항목(OnlyShowIn=GNOME 등),
        #   프로그램이 없는 항목(TryExec)
        if it["managed"] or it["nodisplay"] or not it["here"] or not it["installed"]:
            continue
        it["ours"] = autostart._true((autostart.parse_entry(it["user"]) or {}).get(OVERRIDE_KEY)) if it["user"] else False
        it["note"] = entry_note(it)
        items.append(it)
    return items


def edit_keys(text, updates):
    """[Desktop Entry] 안의 키를 바꾸거나 더한다 — 나머지 줄(주석·다른 묶음·지역화 키)은 그대로.
    없는 키는 그 묶음의 마지막 키 줄 바로 뒤에 붙인다."""
    out, inside, last, done = [], False, None, set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s == GROUP
            out.append(line)
            if inside:
                last = len(out)
            continue
        if inside and "=" in s and not s.startswith("#"):
            k = s.partition("=")[0].strip()
            if k in updates:
                if k in done:
                    continue                     # 같은 키가 두 번 — 하나만 남긴다
                line = f"{k}={updates[k]}"
                done.add(k)
            out.append(line)
            last = len(out)
            continue
        out.append(line)
    missing = [f"{k}={v}" for k, v in updates.items() if k not in done]
    if last is None:                             # 묶음이 없는 파일 — 맨 앞에 만든다
        out[0:0] = [GROUP] + missing
    else:
        out[last:last] = missing
    return "\n".join(out) + "\n"


def _write(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def set_enabled(item, on):
    """사용 / 사용 안 함 — OSError 는 부르는 쪽에서"""
    user, _ = autostart_dirs()
    upath = os.path.join(user, item["file"])
    if on:
        if item["user"] and item["ours"] and item["sys"] and item["sys_enabled"]:
            os.remove(upath)                     # 우리가 만든 덮어쓰기 — 지우면 시스템 항목 그대로
            return
        if item["user"]:
            upd = {"Hidden": "false"}
            if "X-GNOME-Autostart-enabled" in item["data"]:
                upd["X-GNOME-Autostart-enabled"] = "true"
            _write(upath, edit_keys(_read_text(upath), upd))
            return
        # 시스템 항목 자체가 꺼져 있다 — 켠 덮어쓰기를 만든다
        _write(upath, edit_keys(_read_text(item["sys"]), {"Hidden": "false", "X-GNOME-Autostart-enabled": "true",
                                                           OVERRIDE_KEY: "true"}))
    else:
        if item["user"]:
            _write(upath, edit_keys(_read_text(upath), {"Hidden": "true"}))
        else:
            _write(upath, edit_keys(_read_text(item["sys"]), {"Hidden": "true", OVERRIDE_KEY: "true"}))


def package_owners(paths):
    """dpkg 로 .desktop 을 설치한 패키지 이름 (느리다 — 작업 스레드에서)"""
    if not paths:
        return {}
    try:
        p = subprocess.run(["dpkg-query", "-S"] + list(paths), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in p.stdout.splitlines():
        if line.startswith("diversion "):
            continue
        pkg, sep, path = line.partition(": ")
        if sep:
            out[path.strip()] = pkg.split(",")[0].split(":")[0].strip()
    return out


# ── 화면 ─────────────────────────────────────────────────────
(A_FILE, A_ICON, A_NAME, A_PUB, A_STATUS, A_NOTE, A_TIP, A_SEARCH) = range(8)
A_TYPES = (str, GObject.Object, str, str, str, str, str, str)


def _gicon(name):
    if not name:
        return FALLBACK_ICON
    try:
        if os.path.isabs(name):
            return Gio.FileIcon.new(Gio.File.new_for_path(name))
        return Gio.ThemedIcon.new_with_default_fallbacks(name)
    except (TypeError, GLib.Error):
        return FALLBACK_ICON


class StartupPage:
    id = "startup"
    title = "시작 앱"
    searchable = True
    wants_procs = False

    def __init__(self, win):
        self.win = win
        self.items = {}
        self.owners = {}
        self._owners_asked = set()
        self.query = ""
        self.store = Gtk.ListStore(*A_TYPES)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(lambda m, it, _d: not self.query or self.query in (m.get_value(it, A_SEARCH) or ""))
        self.sort = Gtk.TreeModelSort(model=self.filter)
        v = self.view = Gtk.TreeView(model=self.sort)
        v.set_enable_search(False)
        v.get_style_context().add_class("tm-list")
        cols = [text_column("이름", A_NAME, width=280, expand=True, icon=A_ICON),
                text_column("게시자", A_PUB, width=180),
                text_column("상태", A_STATUS, width=110),
                text_column("참고", A_NOTE, width=260)]
        for c in cols:
            v.append_column(c)
        v.set_tooltip_column(A_TIP)
        self.sorter = SortHeaders(self.sort, win.remember_sort)
        for c, sid in zip(cols, (A_NAME, A_PUB, A_STATUS, A_NOTE)):
            self.sorter.add(c, sid)
        sid, order = win.saved_sort(self.id, A_NAME, Gtk.SortType.ASCENDING)
        self.sorter.set(sid, order)
        v.get_selection().connect("changed", lambda *_: self._sync_buttons())
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        v.connect("row-activated", lambda *_: self.toggle_selected())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.get_style_context().add_class("tm-listbox")
        sc.add(v)
        box.pack_start(sc, True, True, 0)
        self.empty = Gtk.Label(label="로그인할 때 자동으로 시작하는 앱이 없습니다.", xalign=0)
        self.empty.get_style_context().add_class("tm-empty")
        self.empty.set_no_show_all(True)
        box.pack_start(self.empty, False, False, 0)
        self.widget = box

        self.actions = Gtk.Box(spacing=8)
        self.actions.pack_start(win.new_task_button(), False, False, 0)
        self.toggle_btn = Gtk.Button(label="사용 안 함")
        self.toggle_btn.connect("clicked", lambda *_: self.toggle_selected())
        self.actions.pack_start(self.toggle_btn, False, False, 0)
        self._sync_buttons()

    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q != self.query:
            self.query = q
            self.filter.refilter()

    def refresh(self, _snap=None, _clients=None):
        pass                                     # 자동 시작 목록은 주기적으로 볼 필요가 없다 (on_show 에서)

    def on_show(self):
        self.reload()

    def reload(self):
        keep = self._selected()
        items = scan()
        self.items = {it["file"]: it for it in items}
        self.store.clear()
        for it in items:
            pub = "" if (it["user"] and not it["sys"]) else self.owners.get(it["sys"], "")
            tip = "\n".join(x for x in (it["comment"], it["exec"], it["user"] or it["sys"]) if x)
            self.store.append([it["file"], _gicon(it["icon"]), it["name"], pub,
                               "사용" if it["enabled"] else "사용 안 함", it["note"], GLib.markup_escape_text(tip),
                               f"{it['name']} {it['file']} {pub}".casefold()])
        self.empty.set_visible(not items)
        if keep:
            self._select(keep)
        self._sync_buttons()
        self._ask_owners([it["sys"] for it in items if it["sys"] and it["sys"] not in self._owners_asked])

    def _ask_owners(self, paths):
        """게시자 = 그 항목을 설치한 패키지 (dpkg) — 느려서 작업 스레드에서, 끝나면 칸만 채운다"""
        if not paths:
            return
        self._owners_asked.update(paths)

        def work():
            res = package_owners(paths)
            GLib.idle_add(self._got_owners, res)
        threading.Thread(target=work, daemon=True, name="taskmgr-dpkg").start()

    def _got_owners(self, res):
        self.owners.update(res)
        for row in self.store:
            it = self.items.get(row[A_FILE])
            if it and it["sys"] in res and not (it["user"] and not it["sys"]):
                row[A_PUB] = res[it["sys"]]
        return False

    def _selected(self):
        model, it = self.view.get_selection().get_selected()
        return model.get_value(it, A_FILE) if it is not None else None

    def _select(self, file):
        for row in self.sort:
            if row[A_FILE] == file:
                self.view.get_selection().select_path(row.path)
                return

    def _sync_buttons(self):
        it = self.items.get(self._selected() or "")
        self.toggle_btn.set_sensitive(it is not None)
        self.toggle_btn.set_label("사용" if it and not it["enabled"] else "사용 안 함")

    def toggle_selected(self):
        it = self.items.get(self._selected() or "")
        if it is None:
            return
        try:
            set_enabled(it, not it["enabled"])
        except OSError as e:
            notice(self.win, "바꾸지 못했습니다", str(e))
        self.reload()

    def _on_key(self, _v, ev):
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
        self._menu(ev)
        return True

    def _menu(self, ev):
        it = self.items.get(self._selected() or "")
        if it is None:
            return
        m = Gtk.Menu()
        menu_item(m, "사용" if not it["enabled"] else "사용 안 함", self.toggle_selected)
        path = it["user"] or it["sys"]
        menu_item(m, "파일 위치 열기", lambda: open_location(path), sensitive=bool(path))
        popup(m, self.view, ev)
