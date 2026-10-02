"""컴퓨터 관리 — 디스크 관리 (윈도우의 "디스크 관리"처럼).

위: 볼륨 목록 (볼륨 · 위치 · 파일 시스템 · 상태 · 용량 · 사용 가능 공간 · % 사용 가능).
아래: 디스크 지도 — 디스크마다 한 줄, 파티션을 크기에 맞춘 칸으로 (할당되지 않은 공간은 검은 띠).
둘 중 어디를 눌러도 같은 것이 골라진다. 동작은 제목줄 단추 · 오른쪽 클릭 메뉴:
  열기 · 연결/연결 해제 · 꺼내기 · 새 볼륨(빈 공간) · 포맷 · 볼륨 삭제 · 이름 바꾸기 · 오류 검사 · 디스크 초기화 ·
  볼륨 확장·축소 · 연결 위치 변경(윈도우의 "드라이브 문자 및 경로 변경" — fstab) · 속성(디스크 건강 상태 포함) ·
  디스크 이미지 연결·분리(윈도우의 "VHD 연결" — .img · .iso 를 디스크처럼).
모두 udisks2 를 D-Bus 로 부른다 (자료는 diskinfo.py). 관리자 권한이 필요한 것은 udisks 가 polkit 으로 묻는다
(ALLOW_INTERACTIVE_AUTHORIZATION → SekaiOS 사용자 계정 컨트롤 창).
SekaiOS 가 쓰는 볼륨(/ · 부팅 · 스왑)과 켜져 있는 설치 USB 는 포맷·삭제·초기화를 막는다 (diskinfo 의 protect).
지우는 일은 모두 먼저 묻는다 — 기본 단추는 취소.
크기 조절은 udisks 가 아는 파일 시스템만 (Manager.CanResize — ext4 · NTFS. exFAT · FAT32 는 윈도우처럼 못 한다).
다른 프로그램이 부를 때: sekai-admin --page=disks --format=/dev/sdb1 (탐색기의 드라이브 "포맷…") · --select=/dev/sdb1
"""
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .. import diskinfo as D  # noqa: E402
from ..common import (NoticeBar, SortHeaders, cmp_num, cmp_text, dbus_error_name, dbus_error_text, gicon,  # noqa: E402
                      key_is_menu, lookup_color, menu_item, mk_view, popup, scrolled, text_column)

UD_PATH = "/org/freedesktop/UDisks2"
OM = "org.freedesktop.DBus.ObjectManager"
SETTLE_MS = 400               # 장치를 꽂으면 신호가 여러 개 온다 — 잠잠해진 뒤 한 번만 다시 읽는다
LONG = 60 * 60 * 1000         # 포맷·검사는 오래 걸릴 수 있다 (큰 디스크의 NTFS 검사 등)
ROW_H = 78                    # 지도에서 디스크 한 줄의 높이
LEFT_W = 168                  # 지도 왼쪽 디스크 설명 칸
MIN_SEG = 96                  # 칸 하나의 최소 폭
C_KEY, C_ICON, C_NAME, C_LOC, C_FS, C_STATE, C_CAP, C_FREE, C_PCT, C_CAPN, C_FREEN, C_PCTN = range(12)

CSS = """
.dm-map { background: @winbg; }
"""


def _esc(s):
    return GLib.markup_escape_text(s or "")


def _key(v):
    return v.path or f"free:{v.disk.path}:{v.offset}"


class DiskMap(Gtk.DrawingArea):
    """디스크 지도 — 그리기와 누름 판정만. 무엇이 골라졌는지는 page.sel"""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.boxes = []               # [(x, y, w, h, 열쇠)] — 누름 판정
        self.set_has_tooltip(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.set_can_focus(True)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("query-tooltip", self._tip)

    def relayout(self):
        n = len(self.page.disks)
        self.set_size_request(-1, max(ROW_H, n * (ROW_H + 10) + 10))
        self.queue_draw()

    def _layout(self, d, width):
        """칸들의 폭 — 모두 최소 폭을 받고, 남는 폭은 크기에 비례해서"""
        segs = d.segments
        if not segs:
            return []
        avail = max(len(segs) * MIN_SEG, width)
        extra = avail - len(segs) * MIN_SEG
        total = sum(max(v.size, 1) for v in segs)
        ws = [MIN_SEG + extra * max(v.size, 1) / total for v in segs]
        return ws

    def _draw(self, w, cr):
        alloc = w.get_allocation()
        fg = lookup_color(w, "fg", (0.9, 0.9, 0.9, 1))
        text2 = lookup_color(w, "text2", (0.7, 0.7, 0.7, 1))
        card = lookup_color(w, "card", (0.2, 0.2, 0.22, 1))
        line = lookup_color(w, "line", (1, 1, 1, 0.08))
        accent = lookup_color(w, "accent", (0.22, 0.77, 0.73, 1))
        self.boxes = []
        y = 10
        x0 = 10
        width = alloc.width - LEFT_W - 30
        for d in self.page.disks:
            # 왼쪽 설명 칸
            sel_disk = self.page.sel == f"disk:{d.path}"
            cr.set_source_rgba(*(accent[:3] + (0.18,)) if sel_disk else card)
            cr.rectangle(x0, y, LEFT_W, ROW_H)
            cr.fill()
            self.boxes.append((x0, y, LEFT_W, ROW_H, f"disk:{d.path}"))
            self._text(cr, f"<b>{_esc(d.title)}</b>\n{_esc(d.label)}\n"
                           f"{_esc(D.fmt_size(d.size))}\n{_esc(d.state)}",
                       x0 + 10, y + 6, LEFT_W - 16, fg, 9)
            # 칸들
            x = x0 + LEFT_W + 6
            for v, sw in zip(d.segments, self._layout(d, width)):
                key = _key(v)
                sel = self.page.sel == key
                cr.set_source_rgba(*card)
                cr.rectangle(x, y, sw - 4, ROW_H)
                cr.fill()
                # 위쪽 띠 — 볼륨은 강조색, 할당되지 않은 공간은 검정, 시스템 볼륨은 진한 강조색
                if v.free:
                    cr.set_source_rgba(0.05, 0.05, 0.06, 1)
                elif v.protect:
                    cr.set_source_rgba(*(c * 0.75 for c in accent[:3]), 1)
                else:
                    cr.set_source_rgba(*accent)
                cr.rectangle(x, y, sw - 4, 8)
                cr.fill()
                if sel:
                    # 골라진 칸 — 빗금 대신 강조색 테두리 (윈도우의 빗금 표시)
                    cr.set_source_rgba(*accent)
                    cr.set_line_width(2)
                    cr.rectangle(x + 1, y + 1, sw - 6, ROW_H - 2)
                    cr.stroke()
                else:
                    cr.set_source_rgba(*line)
                    cr.set_line_width(1)
                    cr.rectangle(x + 0.5, y + 0.5, sw - 5, ROW_H - 1)
                    cr.stroke()
                if v.free:
                    body = f"{_esc(D.fmt_size(v.size))}\n할당되지 않음"
                else:
                    loc = f" ({_esc(v.mount)})" if v.mount and v.mount not in ("/",) else ""
                    body = (f"<b>{_esc(v.name)}</b>{loc}\n{_esc(D.fmt_size(v.size))} {_esc(v.fs_name)}\n"
                            f"<span foreground='{_hex(text2)}'>{_esc(v.status)}</span>")
                self._text(cr, body, x + 8, y + 14, sw - 18, fg, 9)
                self.boxes.append((x, y, sw - 4, ROW_H, key))
                x += sw
            y += ROW_H + 10
        return False

    def _text(self, cr, markup, x, y, w, rgba, pt):
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(f"Pretendard {pt}")
        lay.set_font_description(fd)
        lay.set_width(max(1, int(w)) * Pango.SCALE)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        lay.set_markup(markup, -1)
        cr.set_source_rgba(*rgba)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)

    def _hit(self, x, y):
        for bx, by, bw, bh, key in self.boxes:
            if bx <= x < bx + bw and by <= y < by + bh:
                return key
        return None

    def _press(self, _w, ev):
        self.grab_focus()
        key = self._hit(ev.x, ev.y)
        if key is None:
            return False
        self.page.select(key, from_map=True)
        if ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1:
            self.page.default_action()
        elif ev.button == 3:
            self.page.context_menu(self, ev)
        return True

    def _tip(self, _w, x, y, _kb, tip):
        key = self._hit(x, y)
        item = self.page.item(key) if key else None
        if item is None:
            return False
        if isinstance(item, D.Disk):
            tip.set_text(f"{item.title} — {item.label}\n{item.dev} · {D.fmt_size(item.size)} · "
                         f"{'GPT' if item.table == 'gpt' else 'MBR' if item.table == 'dos' else '파티션 표 없음'}")
        elif item.free and item.disk.table is None:
            tip.set_text(f"초기화되지 않은 디스크 {D.fmt_size(item.size)} — 두 번 눌러 초기화합니다")
        elif item.free:
            tip.set_text(f"할당되지 않은 공간 {D.fmt_size(item.size)} — 두 번 눌러 새 볼륨을 만듭니다")
        else:
            lines = [f"{item.name} ({item.dev})", f"{D.fmt_size(item.size)} {item.fs_name}", item.status]
            if item.mount:
                lines.append(f"위치: {item.mount}")
            if item.protect:
                lines.append(item.protect)
            tip.set_text("\n".join(lines))
        return True


def _hex(rgba):
    return "#%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in rgba[:3])


class DisksPage:
    searchable = False

    def __init__(self, win):
        self.win = win
        self.busy = False
        self.disks = []
        self.items = {}               # 열쇠 → Volume · Disk
        self.sel = None
        self.bus = None
        self._subs = []
        self._settle = 0
        self._loading = False
        self._again = False
        self.caps = {}                # 파일 시스템 → 크기 조절 방식 (D.RESIZE_*)
        self._pending = None          # 목록을 읽은 뒤 할 일 (--format · --select)

        self.store = Gtk.ListStore(str, Gio.Icon, str, str, str, str, str, str, str, float, float, float)
        self.sorted = Gtk.TreeModelSort(model=self.store)
        self.view = mk_view(self.sorted)
        cols = [("볼륨", C_NAME, 190, 0.0, C_ICON), ("위치", C_LOC, 150, 0.0, None),
                ("파일 시스템", C_FS, 100, 0.0, None), ("상태", C_STATE, 220, 0.0, None),
                ("용량", C_CAP, 90, 1.0, None), ("사용 가능 공간", C_FREE, 110, 1.0, None),
                ("% 사용 가능", C_PCT, 90, 1.0, None)]
        self.sorter = SortHeaders(self.sorted)
        for title, col, wdt, xa, icon in cols:
            c = text_column(title, col, wdt, xalign=xa, icon=icon, expand=(col == C_STATE))
            self.view.append_column(c)
            sid = {C_CAP: C_CAPN, C_FREE: C_FREEN, C_PCT: C_PCTN}.get(col, col)
            self.sorter.add(c, sid, numeric=sid in (C_CAPN, C_FREEN, C_PCTN))
        for sid in (C_NAME, C_LOC, C_FS, C_STATE):
            self.sorted.set_sort_func(sid, lambda m, a, b, s=sid: cmp_text(m[a][s], m[b][s]))
        for sid in (C_CAPN, C_FREEN, C_PCTN):
            self.sorted.set_sort_func(sid, lambda m, a, b, s=sid: cmp_num(m[a][s], m[b][s]))
        self.view.get_selection().connect("changed", self._on_list_sel)
        self.view.connect("row-activated", lambda *_: self.default_action())
        self.view.connect("button-press-event", self._list_press)
        self.view.connect("key-press-event", self._list_key)

        self.map = DiskMap(self)
        self.map.get_style_context().add_class("dm-map")
        self.notice = NoticeBar()

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.pack1(scrolled(self.view), True, False)
        msc = scrolled(self.map, frame=True)
        msc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        paned.pack2(msc, True, False)
        paned.set_position(240)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(self.notice, False, False, 0)
        box.pack_start(paned, True, True, 0)
        self.widget = box

        # 제목줄 단추
        self.actions = Gtk.Box(spacing=6)
        self.b_open = self._abtn("열기", self.do_open)
        self.b_new = self._abtn("새 볼륨…", self.do_new)
        self.b_fmt = self._abtn("포맷…", self.do_format)
        self.b_del = self._abtn("삭제…", self.do_delete)
        self.b_ext = self._abtn("볼륨 확장…", self.do_extend)
        more = Gtk.MenuButton()
        more.add(Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        more.set_tooltip_text("다른 동작")
        more.connect("button-press-event", lambda w, ev: (self.context_menu(w, ev), True)[1])
        self.actions.pack_start(more, False, False, 0)
        self.actions.show_all()
        self._update_actions()

    def _abtn(self, label, cb):
        b = Gtk.Button(label=label)
        b.connect("clicked", lambda *_: cb())
        self.actions.pack_start(b, False, False, 0)
        return b

    # ── 보이기·감시 ──
    def on_show(self, **kw):
        if kw.get("format") or kw.get("select"):
            self._pending = kw
        if self.bus is None:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._got_bus)
        else:
            self.refresh()

    def on_hide(self):
        pass                          # 감시는 가볍다 (신호만) — 돌아왔을 때 바로 맞게 남겨 둔다

    def _got_bus(self, _src, res):
        try:
            self.bus = Gio.bus_get_finish(res)
        except GLib.Error as e:
            self.notice.show_notice(f"디스크 서비스에 연결하지 못했습니다: {e.message}", kind="error")
            return
        for sig in ("InterfacesAdded", "InterfacesRemoved"):
            self._subs.append(self.bus.signal_subscribe(D.UD, OM, sig, UD_PATH, None, Gio.DBusSignalFlags.NONE,
                                                        self._on_signal))
        self._subs.append(self.bus.signal_subscribe(D.UD, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                                    None, None, Gio.DBusSignalFlags.NONE, self._on_signal))
        for fs in ("ext4", "ext3", "ext2", "ntfs", "btrfs", "xfs", "f2fs"):
            self.bus.call(D.UD, UD_PATH + "/Manager", D.UD + ".Manager", "CanResize", GLib.Variant("(s)", (fs,)),
                          GLib.VariantType("((bts))"), Gio.DBusCallFlags.NONE, 5000, None, self._got_cap, fs)
        self.refresh()

    def _got_cap(self, bus, res, fs):
        try:
            ok, flags, _util = bus.call_finish(res).unpack()[0]
        except GLib.Error:
            return                        # 이 파일 시스템은 크기를 못 바꾼다
        if ok:
            self.caps[fs] = flags
            self._update_actions()

    def _on_signal(self, *_a):
        if self._settle:
            GLib.source_remove(self._settle)
        self._settle = GLib.timeout_add(SETTLE_MS, self._settled)

    def _settled(self):
        self._settle = 0
        self.refresh()
        return False

    def refresh(self):
        if self.bus is None:
            return
        if self._loading:
            self._again = True
            return
        self._loading = True
        self.bus.call(D.UD, UD_PATH, OM, "GetManagedObjects", None, GLib.VariantType("(a{oa{sa{sv}}})"),
                      Gio.DBusCallFlags.NONE, 15000, None, self._got_objects)

    def _got_objects(self, bus, res):
        self._loading = False
        try:
            objs = bus.call_finish(res).unpack()[0]
        except GLib.Error as e:
            self.notice.show_notice(f"디스크 목록을 읽지 못했습니다: {dbus_error_text(e)}", kind="error")
            return
        # statvfs(사용 가능 공간)는 연결된 볼륨마다 — 느린 네트워크 드라이브가 없으니 메인 스레드에서도 짧다
        self.disks = D.build(objs)
        self._render()
        if self._again:
            self._again = False
            self.refresh()

    # ── 그리기 ──
    def _render(self):
        self.items = {}
        for d in self.disks:
            self.items[f"disk:{d.path}"] = d
            for v in d.segments:
                self.items[_key(v)] = v
        keep = self.sel
        self.store.clear()
        for d in self.disks:
            for v in d.segments:
                if v.free or v.container:
                    continue
                cap = v.size
                free = v.avail
                pct = (free / cap * 100) if (free is not None and cap) else -1
                icon = ["drive-removable-media-symbolic", "drive-removable-media"] if d.removable else \
                    ["drive-harddisk-system-symbolic", "drive-harddisk-symbolic"] if v.protect else \
                    ["drive-harddisk-symbolic", "drive-harddisk"]
                self.store.append([_key(v), gicon(icon), v.name, v.mount or "", v.fs_name, v.status,
                                   D.fmt_size(cap), D.fmt_size(free) if free is not None else "",
                                   f"{pct:.0f} %" if pct >= 0 else "", float(cap),
                                   float(free if free is not None else -1), float(pct)])
        self.map.relayout()
        if keep in self.items:
            self.select(keep)
        else:
            self.sel = None
            self._update_actions()
        if not self.disks:
            self.notice.show_notice("디스크를 찾지 못했습니다.", kind="warn")
        elif not self.busy:
            self.notice.hide_notice()
        if getattr(self, "_automount", None):
            GLib.idle_add(lambda: (self._automount_now(), False)[1])
        if self._pending:
            kw, self._pending = self._pending, None
            dev = os.path.realpath(kw.get("format") or kw.get("select"))
            key = next((k for k, it in self.items.items()
                        if isinstance(it, D.Volume) and not it.free and it.dev and os.path.realpath(it.dev) == dev), None)
            if key is None:
                self.win.toast(f"{dev} 볼륨을 찾지 못했습니다")
            else:
                self.select(key, from_map=True)
                if kw.get("format"):
                    if self._can()["format"]:
                        self.do_format()
                    else:
                        self.win.notice("포맷할 수 없습니다", self.selected().protect or "이 볼륨은 포맷할 수 없습니다.")

    # ── 고르기 ──
    def item(self, key):
        return self.items.get(key)

    def selected(self):
        return self.items.get(self.sel)

    def select(self, key, from_map=False):
        self.sel = key
        self.map.queue_draw()
        # 목록도 같은 볼륨으로 (디스크·빈 공간이면 목록 선택을 푼다)
        sel = self.view.get_selection()
        found = None
        for row in self.sorted:
            if row[C_KEY] == key:
                found = row.iter
        self._syncing = True
        if found is not None:
            sel.select_iter(found)
            if from_map:
                self.view.scroll_to_cell(self.sorted.get_path(found), None, False, 0, 0)
        else:
            sel.unselect_all()
        self._syncing = False
        self._update_actions()

    def _on_list_sel(self, sel):
        if getattr(self, "_syncing", False):
            return
        m, it = sel.get_selected()
        if it is None:
            return
        self.sel = m[it][C_KEY]
        self.map.queue_draw()
        self._update_actions()

    def _list_press(self, view, ev):
        if ev.button != 3:
            return False
        res = view.get_path_at_pos(int(ev.x), int(ev.y))
        if res:
            view.get_selection().select_path(res[0])
        self.context_menu(view, ev)
        return True

    def _list_key(self, view, ev):
        if key_is_menu(ev):
            self.context_menu(view, None)
            return True
        if ev.keyval == Gdk.KEY_Delete:
            self.do_delete()
            return True
        return False

    # ── 무엇을 할 수 있나 ──
    def _can(self):
        it = self.selected()
        c = {"open": False, "mount": False, "unmount": False, "new": False, "format": False, "delete": False,
             "label": False, "check": False, "init": False, "eject": False, "extend": False, "shrink": False,
             "place": False, "props": False, "detach": False, "attach": not self.busy and self.bus is not None}
        if it is None or self.busy:
            return c
        c["props"] = not getattr(it, "free", False)
        if isinstance(it, D.Disk):
            c["init"] = not D.disk_protect(it)
            c["eject"] = it.detachable and not it.system and not it.live
            c["detach"] = bool(it.image)
            return c
        d = it.disk
        c["eject"] = d.detachable and not d.system and not d.live
        c["detach"] = bool(d.image)
        if it.free:
            c["new"] = d.table is not None and not d.ro and not d.live
            return c
        c["open"] = it.has_fs or it.encrypted
        c["mount"] = it.has_fs and not it.mounts
        c["unmount"] = it.has_fs and bool(it.mounts) and not it.protect
        c["format"] = not it.protect and not d.ro and not it.container
        c["delete"] = not it.protect and not d.ro and d.table is not None
        c["label"] = it.has_fs and it.fs in D.LABEL_MAX and not d.ro and not it.protect
        c["check"] = it.has_fs and it.fs in ("ext4", "ext3", "ext2", "ntfs", "vfat", "exfat") and not it.protect
        c["extend"] = not self._resize_why(it, grow=True)
        c["shrink"] = not self._resize_why(it, grow=False)
        c["place"] = it.has_fs and bool(it.uuid) and not it.protect and not d.image and not it.encrypted
        return c

    def _resize_why(self, v, grow):
        """볼륨 확장·축소를 못 하는 이유 (할 수 있으면 빈 글자)"""
        d = v.disk
        if v.free or v.container or not v.has_fs or d.ro or d.live:
            return "이 볼륨은 크기를 바꿀 수 없습니다"
        if d.table is None or v.logical:
            return "파티션이 아닌 볼륨(또는 확장 파티션 안의 논리 볼륨)은 크기를 바꿀 수 없습니다"
        if v.encrypted:
            return "암호화된 볼륨은 크기를 바꿀 수 없습니다"
        flags = self.caps.get(v.fs)
        if flags is None:
            return f"{v.fs_name or '이'} 볼륨은 크기를 바꿀 수 없습니다 (윈도우에서도 NTFS · ext4 같은 것만 됩니다)"
        if grow:
            if v.next_free is None:
                return "바로 뒤에 할당되지 않은 공간이 없습니다"
            if not flags & (D.RESIZE_OFFLINE_GROW | D.RESIZE_ONLINE_GROW):
                return "이 파일 시스템은 늘릴 수 없습니다"
            if v.mounts and not flags & D.RESIZE_ONLINE_GROW and v.protect:
                return v.protect
            return ""
        if v.protect:
            return v.protect
        if not flags & (D.RESIZE_OFFLINE_SHRINK | D.RESIZE_ONLINE_SHRINK):
            return f"{v.fs_name} 볼륨은 줄일 수 없습니다"
        return ""

    def _update_actions(self):
        c = self._can()
        self.b_open.set_sensitive(c["open"])
        self.b_new.set_sensitive(c["new"])
        self.b_fmt.set_sensitive(c["format"])
        self.b_del.set_sensitive(c["delete"])
        it = self.selected()
        reason = getattr(it, "protect", "") if it is not None and not isinstance(it, D.Disk) else ""
        for b in (self.b_fmt, self.b_del):
            b.set_tooltip_text(reason or None)
        self.b_ext.set_sensitive(c["extend"])
        self.b_ext.set_tooltip_text(None if c["extend"] or not isinstance(it, D.Volume) or it.free
                                    else self._resize_why(it, grow=True))

    def default_action(self):
        it = self.selected()
        if it is None or self.busy:
            return
        if isinstance(it, D.Disk):
            if self._can()["init"] and it.table is None and it.whole is None:
                self.do_init()
            return
        if it.free:
            if self._can()["new"]:
                self.do_new()
            elif it.disk.table is None:
                self.select(f"disk:{it.disk.path}")
                if self._can()["init"]:
                    self.do_init()
            return
        if self._can()["open"]:
            self.do_open()

    def context_menu(self, widget, ev):
        """오른쪽 클릭 메뉴 — 꺼진 항목에 이유 말풍선을 달지 않는다 (말풍선이 아래 항목을 덮어 누름을 막았다).
        이유는 제목줄 단추의 말풍선에"""
        it = self.selected()
        c = self._can()
        m = Gtk.Menu()
        if isinstance(it, D.Disk):
            menu_item(m, "디스크 초기화…", self.do_init, c["init"])
            if it.image:
                menu_item(m, "디스크 이미지 분리", self.do_detach, c["detach"])
            else:
                menu_item(m, "꺼내기", self.do_eject, c["eject"])
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "속성", self.do_props, c["props"])
        elif it is not None and it.free:
            menu_item(m, "새 볼륨…", self.do_new, c["new"])
            if it.disk.table is None:
                menu_item(m, "디스크 초기화…", lambda: (self.select(f"disk:{it.disk.path}"), self.do_init()),
                          not D.disk_protect(it.disk))
        elif it is not None:
            menu_item(m, "열기", self.do_open, c["open"])
            if it.mounts:
                menu_item(m, "연결 해제", self.do_unmount, c["unmount"])
            else:
                menu_item(m, "연결", self.do_mount, c["mount"])
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "볼륨 확장…", self.do_extend, c["extend"])
            menu_item(m, "볼륨 축소…", self.do_shrink, c["shrink"])
            menu_item(m, "연결 위치 변경…", self.do_place, c["place"])
            menu_item(m, "이름 바꾸기…", self.do_label, c["label"])
            menu_item(m, "오류 검사…", self.do_check, c["check"])
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "포맷…", self.do_format, c["format"])
            menu_item(m, "볼륨 삭제…", self.do_delete, c["delete"])
            if c["detach"]:
                m.append(Gtk.SeparatorMenuItem())
                menu_item(m, "디스크 이미지 분리", self.do_detach, True)
            elif c["eject"]:
                m.append(Gtk.SeparatorMenuItem())
                menu_item(m, "꺼내기", self.do_eject, True)
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "속성", self.do_props, c["props"])
        m.append(Gtk.SeparatorMenuItem())
        menu_item(m, "디스크 이미지 연결…", self.do_attach, c["attach"])
        menu_item(m, "새로 고침", self.refresh, not self.busy)
        view = widget if isinstance(widget, Gtk.TreeView) else self.view
        if isinstance(widget, Gtk.TreeView) or ev is None:
            popup(m, view, ev)
        else:
            m.show_all()
            m.attach_to_widget(widget, None)
            m.popup_at_pointer(ev)

    # ── udisks 부르기 ──
    def _call(self, path, iface, method, args, rtype, done, what):
        """비동기로 부르고 done(결과 또는 None) — 실패하면 알린다. 그동안 다른 동작을 막는다"""
        self._set_busy(True, what)

        def fin(bus, res):
            self._set_busy(False)
            try:
                out = bus.call_finish(res)
            except GLib.Error as e:
                self._fail(what, e)
                done(None)
                return
            done(out.unpack() if out is not None else ())
        self.bus.call(D.UD, path, iface, method, args, GLib.VariantType(rtype) if rtype else None,
                      Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, LONG, None, fin)

    def _chain(self, steps, done, what):
        """[(경로, 인터페이스, 메서드, 인자, 답 형식)] 을 차례로 — 하나라도 실패하면 멈춘다"""
        def step(i, _res=None):
            if i >= len(steps):
                done(True)
                return
            p, iface, meth, args, rt = steps[i]
            self._call(p, iface, meth, args, rt, lambda r: step(i + 1) if r is not None else done(False), what)
        step(0)

    def _set_busy(self, on, what=""):
        self.busy = on
        self.win.busy_changed()
        if on:
            self.notice.show_notice(f"{what} 중…", kind="info")
        else:
            self.notice.hide_notice()
        self._update_actions()

    def _fail(self, what, e):
        name = dbus_error_name(e) or ""
        import sys
        print(f"[sekai-admin] 디스크 {what} 실패: {name}: {e.message}", file=sys.stderr, flush=True)
        if name.endswith("NotAuthorizedDismissed") or name.endswith("NotAuthorizedCanObtain"):
            self.win.toast("관리자 인증이 취소되어 바꾸지 않았습니다")
            return
        text = dbus_error_text(e)
        low = text.lower()
        if "target is busy" in low or name.endswith("DeviceBusy") or ("unmount" in low and "busy" in low):
            text = "다른 프로그램이 이 볼륨의 파일을 쓰고 있습니다 — 파일 탐색기·터미널 등을 닫고 다시 해 주세요"
        elif name.endswith("NotSupported"):
            text = f"이 볼륨에서는 할 수 없는 일입니다 ({text})"
        self.win.notice(f"{what}에 실패했습니다", text)

    # ── 동작 ──
    def _fs_path(self, v):
        """파일 시스템이 있는 블록 (잠금 푼 암호화 볼륨이면 풀린 쪽)"""
        return v.unlocked or v.path

    def do_open(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or v.free:
            return
        if v.encrypted and not v.unlocked:
            # 암호는 파일 탐색기가 묻는다 (내 PC 에서 그 드라이브를 열면)
            self._launch(["sekai-files", "computer:///"])
            self.win.toast("파일 탐색기의 내 PC 에서 이 드라이브를 열면 암호를 묻습니다")
            return
        if v.mount:
            self._launch(["sekai-files", v.mount])
            return
        self._mount(v, lambda where: where and self._launch(["sekai-files", where]))

    def _launch(self, argv):
        try:
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.win.toast(f"열지 못했습니다: {e.message}")

    def _mount(self, v, then=None):
        self._call(self._fs_path(v), D.I_FS, "Mount", GLib.Variant("(a{sv})", ({},)), "(s)",
                   lambda r: (self.refresh(), then and then(r[0] if r else None)), "연결")

    def do_mount(self):
        v = self.selected()
        if isinstance(v, D.Volume) and not v.free:
            self._mount(v)

    def do_unmount(self, then=None):
        v = self.selected()
        if not isinstance(v, D.Volume) or v.free or v.protect:
            return
        self._call(self._fs_path(v), D.I_FS, "Unmount", GLib.Variant("(a{sv})", ({},)), None,
                   lambda r: (self.refresh(), then and r is not None and then()), "연결 해제")

    def _release_steps(self, v):
        """볼륨을 지우거나 포맷하기 전에 — 연결 해제·잠그기"""
        steps = []
        fsp = self._fs_path(v)
        if v.mounts:
            steps.append((fsp, D.I_FS, "Unmount", GLib.Variant("(a{sv})", ({},)), None))
        if v.encrypted and v.unlocked:
            steps.append((v.path, D.I_CRYPT, "Lock", GLib.Variant("(a{sv})", ({},)), None))
        return steps

    def do_eject(self):
        it = self.selected()
        d = it if isinstance(it, D.Disk) else getattr(it, "disk", None)
        if d is None or not d.drive:
            return
        steps = []
        for v in d.segments:
            if not v.free:
                steps += self._release_steps(v)
        meth = "Eject" if d.ejectable else "PowerOff"
        steps.append((d.drive, D.I_DRIVE, meth, GLib.Variant("(a{sv})", ({},)), None))
        name = d.label

        def done(ok):
            self.refresh()
            if ok:
                self.win.toast(f"{name} 을(를) 안전하게 뺄 수 있습니다")
        self._chain(steps, done, "꺼내기")

    # ── 이름·포맷·새 볼륨 창 ──
    def _dialog(self, title, ok_label, destructive=False):
        dlg = Gtk.Dialog(title=title, transient_for=self.win, modal=True)
        dlg.add_button("취소", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        ok.get_style_context().add_class("destructive-action" if destructive else "accent-btn")
        dlg.set_default_response(Gtk.ResponseType.CANCEL if destructive else Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)
        dlg.set_default_size(540, -1)
        return dlg, box, ok

    def _fs_picker(self, box, default):
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        combo = Gtk.ComboBoxText()
        for fs, name, _desc in D.FS_CHOICES:
            combo.append(fs, name)
        combo.set_active_id(default if default in [f for f, _n, _d in D.FS_CHOICES] else "ntfs")
        desc = Gtk.Label(xalign=0)
        desc.set_line_wrap(True)
        desc.set_max_width_chars(48)
        desc.get_style_context().add_class("dim-label")

        def changed(*_):
            fs = combo.get_active_id()
            desc.set_text(next((d for f, _n, d in D.FS_CHOICES if f == fs), ""))
        combo.connect("changed", changed)
        changed()
        grid.attach(Gtk.Label(label="파일 시스템", xalign=0), 0, 0, 1, 1)
        grid.attach(combo, 1, 0, 1, 1)
        grid.attach(desc, 1, 1, 1, 1)
        box.add(grid)
        return combo, grid

    def _label_entry(self, grid, row, text, combo=None):
        e = Gtk.Entry()
        e.set_text(text or "")
        e.set_activates_default(True)
        e.set_hexpand(True)

        def limit(*_):
            fs = combo.get_active_id() if combo is not None else None
            e.set_max_length(D.LABEL_MAX.get(fs, 32))
        if combo is not None:
            combo.connect("changed", limit)
        limit()
        grid.attach(Gtk.Label(label="볼륨 이름", xalign=0), 0, row, 1, 1)
        grid.attach(e, 1, row, 1, 1)
        return e

    def _fmt_options(self, fs, label):
        opts = {"label": GLib.Variant("s", label or ""), "update-partition-type": GLib.Variant("b", True)}
        if fs in ("ext4", "ext3"):
            opts["take-ownership"] = GLib.Variant("b", True)   # 새 디스크의 맨 위 폴더를 이 사용자 것으로
        if fs == "vfat":
            opts["label"] = GLib.Variant("s", (label or "").upper())
        return opts

    def do_format(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or v.free or not self._can()["format"]:
            return
        # 윈도우처럼 지금 파일 시스템 그대로 — 없거나 고를 수 없는 것이면 이동식은 exFAT, 아니면 NTFS
        default = v.fs if v.fs in ("ntfs", "exfat", "ext4", "vfat") else ("exfat" if v.disk.removable else "ntfs")
        dlg, box, _ok = self._dialog(f"{v.name} 포맷", "포맷", destructive=True)
        warn = Gtk.Label(xalign=0)
        warn.set_markup(f"<b>{_esc(v.name)}</b> ({_esc(D.fmt_size(v.size))}, {_esc(v.dev)})의 "
                        "<b>모든 파일이 지워집니다.</b>")
        warn.set_line_wrap(True)
        box.add(warn)
        combo, grid = self._fs_picker(box, default)
        entry = self._label_entry(grid, 2, v.label, combo)
        dlg.show_all()

        def resp(_d, r):
            fs, label = combo.get_active_id(), entry.get_text().strip()
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            steps = self._release_steps(v)
            steps.append((v.path, D.I_BLOCK, "Format",
                          GLib.Variant("(sa{sv})", (fs, self._fmt_options(fs, label))), None))
            self._chain(steps, lambda ok: (self.refresh(), ok and self.win.toast(f"{label or v.name} 을(를) 포맷했습니다")),
                        "포맷")
        dlg.connect("response", resp)

    def do_new(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not v.free or not self._can()["new"]:
            return
        d = v.disk
        dlg, box, _ok = self._dialog("새 볼륨 만들기", "만들기")
        info = Gtk.Label(xalign=0)
        info.set_markup(f"{_esc(d.title)} ({_esc(d.label)})의 할당되지 않은 공간 "
                        f"<b>{_esc(D.fmt_size(v.size))}</b>에 새 볼륨을 만듭니다.")
        info.set_line_wrap(True)
        box.add(info)
        combo, grid = self._fs_picker(box, "exfat" if d.removable else "ntfs")
        entry = self._label_entry(grid, 2, "", combo)
        max_mb = v.size // D.MIB
        adj = Gtk.Adjustment(value=max_mb, lower=min(16, max_mb), upper=max_mb, step_increment=1024,
                             page_increment=10240)
        spin = Gtk.SpinButton(adjustment=adj, digits=0)
        spin.set_numeric(True)
        unit = Gtk.Box(spacing=6)
        unit.pack_start(spin, True, True, 0)
        unit.pack_start(Gtk.Label(label=f"MB  (최대 {max_mb:,} MB)"), False, False, 0)
        grid.attach(Gtk.Label(label="크기", xalign=0), 0, 3, 1, 1)
        grid.attach(unit, 1, 3, 1, 1)
        dlg.show_all()

        def resp(_d, r):
            fs, label, mb = combo.get_active_id(), entry.get_text().strip(), int(spin.get_value())
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            size = v.size if mb >= max_mb else mb * D.MIB
            ptype = (D.GPT_TYPE if d.table == "gpt" else D.MBR_TYPE).get(fs, "")
            args = GLib.Variant("(ttssa{sv}sa{sv})", (v.offset, size, ptype, "", {}, fs,
                                                       self._fmt_options(fs, label)))
            def made(res):
                if res:
                    self.sel = res[0]                   # 다시 읽은 뒤 만든 볼륨이 골라져 있게
                    self.win.toast("새 볼륨을 만들었습니다")
                self.refresh()
            self._call(d.path, D.I_TABLE, "CreatePartitionAndFormat", args, "(o)", made, "새 볼륨 만들기")
        dlg.connect("response", resp)

    def do_delete(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or v.free or not self._can()["delete"]:
            return
        used = f" — 지금 {D.fmt_size(v.used)} 를 쓰고 있습니다" if v.used else ""

        def go():
            steps = self._release_steps(v)
            steps.append((v.path, D.I_PART, "Delete", GLib.Variant("(a{sv})", ({},)), None))
            self._chain(steps, lambda ok: (self.refresh(), ok and self.win.toast(f"{v.name} 볼륨을 삭제했습니다")),
                        "볼륨 삭제")
        self.win.confirm(f"{v.name} 볼륨을 삭제할까요?",
                         f"{v.name} ({D.fmt_size(v.size)}, {v.dev})의 모든 파일이 지워지고, 그 자리는 "
                         f"할당되지 않은 공간이 됩니다{used}. 되돌릴 수 없습니다.", "삭제", go)

    def do_label(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not self._can()["label"]:
            return
        dlg, box, _ok = self._dialog("볼륨 이름 바꾸기", "바꾸기")
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        box.add(grid)
        entry = self._label_entry(grid, 0, v.label)
        entry.set_max_length(D.LABEL_MAX.get(v.fs, 32))
        dlg.show_all()

        def resp(_d, r):
            label = entry.get_text().strip()
            dlg.destroy()
            if r != Gtk.ResponseType.OK or label == v.label:
                return
            if v.fs == "vfat":
                label = label.upper()
            fsp = self._fs_path(v)
            empty = GLib.Variant("(a{sv})", ({},))
            # exFAT·NTFS·FAT 의 이름 바꾸는 도구는 연결된 볼륨을 열지 못한다 — 잠시 연결을 풀고 바꾼 뒤 다시 연결
            offline = bool(v.mounts) and v.fs not in ("ext4", "ext3", "ext2")
            steps = [(fsp, D.I_FS, "Unmount", empty, None)] if offline else []
            steps.append((fsp, D.I_FS, "SetLabel", GLib.Variant("(sa{sv})", (label, {})), None))
            if offline:
                steps.append((fsp, D.I_FS, "Mount", empty, "(s)"))
            self._chain(steps, lambda ok: (self.refresh(), ok and self.win.toast(f"볼륨 이름을 {label} (으)로 바꿨습니다")),
                        "이름 바꾸기")
        dlg.connect("response", resp)

    def do_check(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not self._can()["check"]:
            return
        was = v.mount
        fsp = self._fs_path(v)

        def remount():
            if was:
                self._call(fsp, D.I_FS, "Mount", GLib.Variant("(a{sv})", ({},)), "(s)", lambda r: self.refresh(), "연결")
            else:
                self.refresh()

        def checked(r):
            if r is None:
                remount()
                return
            if r[0]:
                self.win.notice("오류 검사", f"{v.name} 에서 오류를 찾지 못했습니다.")
                remount()
                return

            def repair():
                self._call(fsp, D.I_FS, "Repair", GLib.Variant("(a{sv})", ({},)), "(b)",
                           lambda rr: (self.win.notice("오류 검사", "오류를 고쳤습니다." if rr and rr[0] else
                                                       "일부 오류를 고치지 못했습니다 — 데이터를 다른 곳에 백업해 두세요."),
                                       remount()), "복구")
            self.win.confirm("오류를 찾았습니다", f"{v.name} 의 파일 시스템에 오류가 있습니다. 지금 고칠까요?",
                             "고치기", repair)

        def unmounted(ok):
            if not ok:
                remount()
                return
            self._call(fsp, D.I_FS, "Check", GLib.Variant("(a{sv})", ({},)), "(b)", checked, "오류 검사")

        def start():
            steps = []
            if v.mounts:
                steps.append((fsp, D.I_FS, "Unmount", GLib.Variant("(a{sv})", ({},)), None))
            self._chain(steps, unmounted, "연결 해제")
        if v.mounts:
            self.win.confirm(f"{v.name} 오류 검사", "검사하는 동안 이 볼륨의 연결을 잠시 해제합니다. "
                             "이 볼륨의 파일을 연 프로그램은 먼저 닫아 주세요.", "검사", start)
        else:
            start()

    def do_init(self):
        d = self.selected()
        if not isinstance(d, D.Disk) or D.disk_protect(d):
            return
        dlg, box, _ok = self._dialog(f"{d.title} 초기화", "초기화", destructive=bool(d.table or d.whole))
        txt = Gtk.Label(xalign=0)
        txt.set_line_wrap(True)
        txt.set_max_width_chars(52)
        if d.table or d.whole:
            txt.set_markup(f"<b>{_esc(d.title)}</b> ({_esc(d.label)}, {_esc(D.fmt_size(d.size))})의 "
                           "<b>모든 볼륨과 파일이 지워집니다.</b>")
        else:
            txt.set_markup(f"<b>{_esc(d.title)}</b> ({_esc(d.label)}, {_esc(D.fmt_size(d.size))})를 "
                           "쓰려면 먼저 초기화해야 합니다. 초기화한 뒤 새 볼륨을 만드세요.")
        box.add(txt)
        gpt = Gtk.RadioButton.new_with_label(None, "GPT (GUID 파티션 테이블) — 권장")
        mbr = Gtk.RadioButton.new_with_label_from_widget(gpt, "MBR (마스터 부트 레코드) — 아주 오래된 PC·기기와 같이 쓸 때")
        box.add(gpt)
        box.add(mbr)
        dlg.show_all()

        def resp(_d, r):
            kind = "gpt" if gpt.get_active() else "dos"
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            self._call(d.path, D.I_BLOCK, "Format", GLib.Variant("(sa{sv})", (kind, {})), None,
                       lambda res: (self.refresh(), res is not None and self.win.toast(f"{d.title} 을(를) 초기화했습니다")),
                       "디스크 초기화")
        dlg.connect("response", resp)


    # ── 볼륨 확장 · 축소 ──
    def _mb_row(self, grid, row, title, lo, hi, value):
        adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=1024, page_increment=10240)
        spin = Gtk.SpinButton(adjustment=adj, digits=0)
        spin.set_numeric(True)
        spin.set_activates_default(True)
        unit = Gtk.Box(spacing=6)
        unit.pack_start(spin, True, True, 0)
        unit.pack_start(Gtk.Label(label=f"MB  (최대 {hi:,} MB)"), False, False, 0)
        grid.attach(Gtk.Label(label=title, xalign=0), 0, row, 1, 1)
        grid.attach(unit, 1, row, 1, 1)
        return spin

    def _kv(self, grid, row, key, val):
        k = Gtk.Label(label=key, xalign=0)
        k.get_style_context().add_class("dim-label")
        v = Gtk.Label(label=val, xalign=0)
        v.set_selectable(True)
        v.set_line_wrap(True)
        v.set_max_width_chars(44)
        grid.attach(k, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        return v

    def _resize_steps(self, v, size, grow):
        """크기를 바꾸는 차례 — 늘릴 땐 파티션 먼저, 줄일 땐 파일 시스템 먼저. 연결된 채로 못 하면 잠시 연결을 푼다"""
        flags = self.caps.get(v.fs, 0)
        online = bool(v.mounts) and bool(flags & (D.RESIZE_ONLINE_GROW if grow else D.RESIZE_ONLINE_SHRINK))
        fsp = self._fs_path(v)
        empty = GLib.Variant("(a{sv})", ({},))
        steps = []
        offline = not online
        if offline and v.mounts:
            steps.append((fsp, D.I_FS, "Unmount", empty, None))
        if offline and v.fs.startswith("ext"):
            # resize2fs 는 연결을 푼 ext 를 먼저 검사(e2fsck -f)해야 바꾼다
            steps.append((fsp, D.I_FS, "Repair", empty, "(b)"))
        part = (v.path, D.I_PART, "Resize", GLib.Variant("(ta{sv})", (size, {})), None)
        fs = (fsp, D.I_FS, "Resize", GLib.Variant("(ta{sv})", (0 if grow else size, {})), None)
        steps += [part, fs] if grow else [fs, part]
        if v.fs == "ntfs":
            # ntfsresize 는 다음 윈도우 부팅 때 검사하라고 '더러움' 표시를 남긴다 — 리눅스의 ntfs3 은 그런 볼륨을
            #   연결하지 않는다. 검사는 ntfsresize 가 이미 했다 → 표시만 지운다 (udisks 의 NTFS 복구 = ntfsfix -d)
            steps.append((fsp, D.I_FS, "Repair", empty, "(b)"))
        if offline and v.mounts:
            steps.append((fsp, D.I_FS, "Mount", empty, "(s)"))
        return steps, offline and bool(v.mounts)

    def _resized(self, v, size, grow):
        """크기 조절이 끝났을 때 — 실패했는데 연결을 풀어 둔 채면 다시 연결한다"""
        def done(ok):
            if ok:
                self.win.toast(f"{v.name} 을(를) {D.fmt_size(size)} 로 {'늘렸' if grow else '줄였'}습니다")
                self.refresh()
            elif v.mounts:
                self._call(self._fs_path(v), D.I_FS, "Mount", GLib.Variant("(a{sv})", ({},)), "(s)",
                           lambda _r: self.refresh(), "다시 연결")
            else:
                self.refresh()
        return done

    def do_extend(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not self._can()["extend"]:
            return
        free = v.next_free
        max_mb = free.size // D.MIB
        dlg, box, _ok = self._dialog(f"{v.name} 확장", "확장")
        txt = Gtk.Label(xalign=0)
        txt.set_line_wrap(True)
        txt.set_max_width_chars(56)
        txt.set_markup(f"<b>{_esc(v.name)}</b> 바로 뒤의 할당되지 않은 공간 <b>{_esc(D.fmt_size(free.size))}</b> 중 "
                       "이 볼륨에 붙일 크기를 고르세요. 파일은 그대로 남습니다.")
        box.add(txt)
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.add(grid)
        self._kv(grid, 0, "지금 크기", f"{D.fmt_size(v.size)}  ({v.size // D.MIB:,} MB)")
        spin = self._mb_row(grid, 1, "늘릴 크기", 1, max_mb, max_mb)
        after = self._kv(grid, 2, "늘린 뒤 크기", "")
        spin.connect("value-changed", lambda *_: after.set_text(
            D.fmt_size(v.size + int(spin.get_value()) * D.MIB)))
        spin.emit("value-changed")
        _steps, unmounts = self._resize_steps(v, 0, True)
        if unmounts:
            note = Gtk.Label(xalign=0, label="늘리는 동안 이 볼륨의 연결을 잠시 해제합니다. "
                                             "이 볼륨의 파일을 연 프로그램은 먼저 닫아 주세요.")
            note.set_line_wrap(True)
            note.set_max_width_chars(56)
            note.get_style_context().add_class("dim-label")
            box.add(note)
        dlg.show_all()

        def resp(_d, r):
            mb = int(spin.get_value())
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            size = v.size + (free.size if mb >= max_mb else mb * D.MIB)
            steps, _u = self._resize_steps(v, size, True)
            self._chain(steps, self._resized(v, size, True), "볼륨 확장")
        dlg.connect("response", resp)

    def _measure(self, v, then):
        """쓴 공간을 지금 잰다 (목록을 읽은 뒤에 파일을 썼을 수 있다) — 연결돼 있지 않으면 잠시 연결해 재고 다시 푼다.
        then(쓴 바이트 또는 None)"""
        fsp = self._fs_path(v)

        def used_at(where):
            try:
                st = os.statvfs(where)
                return max(0, (st.f_blocks - st.f_bfree) * st.f_frsize)
            except OSError:
                return None
        if v.mount:
            then(used_at(v.mount))
            return

        def mounted(r):
            if not r:
                then(None)
                return
            used = used_at(r[0])
            self._call(fsp, D.I_FS, "Unmount", GLib.Variant("(a{sv})", ({},)), None, lambda _r: then(used),
                       "사용 공간 확인")
        self._call(fsp, D.I_FS, "Mount", GLib.Variant("(a{sv})", ({},)), "(s)", mounted, "사용 공간 확인")

    def do_shrink(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not self._can()["shrink"]:
            return

        def measured(used):
            if used is None:
                self.win.notice("볼륨 축소", "이 볼륨이 쓰는 공간을 알아내지 못했습니다 (연결이 되지 않았습니다).")
                return
            v.used = used
            self._shrink_dialog(v, D.shrink_min(v))
        self._measure(v, measured)

    def _shrink_dialog(self, v, min_size):
        can_mb = max(0, (v.size - min_size) // D.MIB)
        if can_mb < 16:
            self.win.notice("볼륨 축소", f"{v.name} 은(는) 거의 가득 차 있어 줄일 수 있는 공간이 없습니다.")
            return
        dlg, box, _ok = self._dialog(f"{v.name} 축소", "축소")
        txt = Gtk.Label(xalign=0)
        txt.set_line_wrap(True)
        txt.set_max_width_chars(56)
        txt.set_markup(f"<b>{_esc(v.name)}</b> 의 끝에서 떼어 낼 크기를 고르세요. 떼어 낸 자리는 할당되지 않은 공간이 되어 "
                       "새 볼륨을 만들 수 있습니다. 파일은 그대로 남습니다.")
        box.add(txt)
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.add(grid)
        self._kv(grid, 0, "지금 크기", f"{D.fmt_size(v.size)}  ({v.size // D.MIB:,} MB)")
        self._kv(grid, 1, "줄일 수 있는 공간", f"{D.fmt_size(can_mb * D.MIB)}  ({can_mb:,} MB)")
        spin = self._mb_row(grid, 2, "줄일 크기", 1, can_mb, can_mb)
        after = self._kv(grid, 3, "줄인 뒤 크기", "")
        spin.connect("value-changed", lambda *_: after.set_text(
            D.fmt_size(v.size - int(spin.get_value()) * D.MIB)))
        spin.emit("value-changed")
        if v.mounts:
            note = Gtk.Label(xalign=0, label="줄이는 동안 이 볼륨의 연결을 잠시 해제합니다. "
                                             "이 볼륨의 파일을 연 프로그램은 먼저 닫아 주세요.")
            note.set_line_wrap(True)
            note.set_max_width_chars(56)
            note.get_style_context().add_class("dim-label")
            box.add(note)
        dlg.show_all()

        def resp(_d, r):
            mb = int(spin.get_value())
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            size = (v.size - mb * D.MIB) // D.MIB * D.MIB
            steps, _u = self._resize_steps(v, size, False)
            self._chain(steps, self._resized(v, size, False), "볼륨 축소")
        dlg.connect("response", resp)

    # ── 연결 위치 (윈도우의 드라이브 문자 및 경로 변경) ──
    @staticmethod
    def _fstab_variant(item):
        typ, d = item
        out = {}
        for k, val in d.items():
            if k in ("freq", "passno"):
                out[k] = GLib.Variant("i", int(val))
            else:
                b = val if isinstance(val, (bytes, bytearray)) else bytes(x for x in val if isinstance(x, int))
                out[k] = GLib.Variant("ay", bytes(b).rstrip(b"\0") + b"\0")
        return (typ, out)

    def do_place(self):
        v = self.selected()
        if not isinstance(v, D.Volume) or not self._can()["place"]:
            return
        cur = v.fstab
        dlg, box, ok = self._dialog(f"{v.name} 연결 위치 변경", "확인")
        auto = Gtk.RadioButton.new_with_label(None, "자동 — 연결할 때마다 SekaiOS 가 정합니다 (/media/사용자/볼륨 이름)")
        fix = Gtk.RadioButton.new_with_label_from_widget(auto, "항상 이 폴더에 연결:")
        box.add(auto)
        box.add(fix)
        row = Gtk.Box(spacing=8, margin_start=24)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_activates_default(True)
        base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (v.label or f"disk{v.disk.index}-{v.number}"))
        entry.set_text(cur["dir"] if cur else f"/mnt/{base}")
        browse = Gtk.Button(label="찾아보기…")
        row.pack_start(entry, True, True, 0)
        row.pack_start(browse, False, False, 0)
        box.add(row)
        boot = Gtk.CheckButton(label="SekaiOS 를 시작할 때 자동으로 연결", margin_start=24)
        boot.set_active(cur["boot"] if cur else not v.disk.detachable)
        box.add(boot)
        err = Gtk.Label(xalign=0, margin_start=24)
        err.set_line_wrap(True)
        err.set_max_width_chars(56)
        err.get_style_context().add_class("error")
        box.add(err)
        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(56)
        hint.get_style_context().add_class("dim-label")
        hint.set_text("폴더가 없으면 새로 만듭니다. 바꾸는 데 관리자 권한이 필요합니다.")
        box.add(hint)
        (fix if cur else auto).set_active(True)

        def check(*_):
            on = fix.get_active()
            for w in (entry, browse, boot):
                w.set_sensitive(on)
            why = ""
            if on:
                path = os.path.normpath(entry.get_text().strip())
                if not (cur and path == os.path.normpath(cur["dir"])) and path not in v.mounts:
                    why = D.fixed_dir_problem(path, os.path.expanduser("~"))
            err.set_text(why)
            err.set_visible(bool(why))
            ok.set_sensitive(not why)
        for w, sig in ((auto, "toggled"), (entry, "changed")):
            w.connect(sig, check)

        def pick(*_):
            fc = Gtk.FileChooserNative.new("연결할 폴더 고르기", dlg, Gtk.FileChooserAction.SELECT_FOLDER, "고르기", "취소")
            fc.set_create_folders(True)
            if fc.run() == Gtk.ResponseType.ACCEPT and fc.get_filename():
                entry.set_text(fc.get_filename())
            fc.destroy()
        browse.connect("clicked", pick)
        dlg.show_all()
        check()

        def resp(_d, r):
            want_fix, path, at_boot = fix.get_active(), os.path.normpath(entry.get_text().strip()), boot.get_active()
            dlg.destroy()
            if r != Gtk.ResponseType.OK:
                return
            fsp = self._fs_path(v)
            empty = GLib.Variant("(a{sv})", ({},))
            was = bool(v.mounts)
            steps = []
            if not want_fix:
                if not cur:
                    return
                if was:
                    steps.append((fsp, D.I_FS, "Unmount", empty, None))
                steps.append((fsp, D.I_BLOCK, "RemoveConfigurationItem",
                              GLib.Variant("((sa{sv})a{sv})", (self._fstab_variant(cur["item"]), {})), None))
                if was:
                    steps.append((fsp, D.I_FS, "Mount", empty, "(s)"))
                done_text = "연결 위치를 자동으로 되돌렸습니다"
            else:
                # users — 이 사용자가 연결·해제할 때 다시 묻지 않는다 (udisks 가 이 사용자로 mount 를 부른다).
                #   users 는 noexec 를 함께 켜므로 exec 로 되돌린다. 폴더는 udisks 가 항목을 넣을 때 만든다
                opts = ["nofail", "x-gvfs-show", "users", "exec"]
                if not at_boot:
                    opts.append("noauto")
                if v.fs in ("ntfs", "exfat", "vfat"):          # 권한이 없는 파일 시스템 — 이 사용자의 것으로 연결
                    opts += [f"uid={os.getuid()}", f"gid={os.getgid()}"]
                item = ("fstab", {"fsname": f"UUID={v.uuid}".encode(), "dir": path.encode(), "type": b"auto",
                                  "opts": ",".join(opts).encode(), "freq": 0, "passno": 0})
                new = self._fstab_variant(item)
                if was:
                    steps.append((fsp, D.I_FS, "Unmount", empty, None))
                if cur:
                    steps.append((fsp, D.I_BLOCK, "UpdateConfigurationItem",
                                  GLib.Variant("((sa{sv})(sa{sv})a{sv})",
                                               (self._fstab_variant(cur["item"]), new, {})), None))
                else:
                    steps.append((fsp, D.I_BLOCK, "AddConfigurationItem",
                                  GLib.Variant("((sa{sv})a{sv})", (new, {})), None))
                if was or at_boot:
                    steps.append((fsp, D.I_FS, "Mount", empty, "(s)"))
                done_text = f"{v.name} 을(를) 이제 {path} 에 연결합니다"
            self._chain(steps, lambda okk: (self.refresh(), okk and self.win.toast(done_text)), "연결 위치 변경")
        dlg.connect("response", resp)

    # ── 속성 ──
    def do_props(self):
        it = self.selected()
        if it is None or getattr(it, "free", False):
            return
        disk = it if isinstance(it, D.Disk) else it.disk
        title = f"{disk.title} 속성" if isinstance(it, D.Disk) else f"{it.name} 속성"
        dlg = Gtk.Dialog(title=title, transient_for=self.win, modal=True)
        dlg.add_button("닫기", Gtk.ResponseType.CLOSE)
        dlg.set_default_response(Gtk.ResponseType.CLOSE)
        dlg.set_resizable(False)                      # 내용에 맞는 높이 (아래에 빈 자리가 남지 않게)
        box = dlg.get_content_area()
        box.set_border_width(16)
        grid = Gtk.Grid(column_spacing=18, row_spacing=7)
        box.add(grid)
        rows = []
        if isinstance(it, D.Volume):
            v = it
            rows += [("볼륨", v.name), ("파일 시스템", v.fs_name or "-"), ("상태", v.status),
                     ("용량", f"{D.fmt_size(v.size)}  ({v.size:,} 바이트)")]
            if v.used is not None:
                rows += [("사용 중", D.fmt_size(v.used)), ("사용 가능", D.fmt_size(v.avail))]
            rows.append(("연결 위치", v.mount or "연결되지 않음"))
            if v.fstab:
                rows.append(("고정 연결 위치", v.fstab["dir"] + ("  (시작할 때 연결)" if v.fstab["boot"] else "")))
            where = f"{disk.title}" + (f" · 파티션 {v.number}" if v.number else "")
            rows += [("위치", where), ("장치", v.dev), ("UUID", v.uuid or "-")]
            pt = D.PTYPE_NAMES.get(v.ptype.lower()) or v.ptype
            if pt:
                rows.append(("파티션 종류", pt))
        else:
            rows += [("디스크", f"{disk.title} — {disk.label}"), ("종류", disk.kind)]
            if disk.image:
                rows.append(("이미지 파일", disk.image))
            if disk.bus:
                rows.append(("연결 방식", {"usb": "USB", "ata": "SATA", "nvme": "NVMe", "sdio": "SD",
                                           "scsi": "SCSI"}.get(disk.bus, disk.bus.upper())))
            if disk.serial:
                rows.append(("일련 번호", disk.serial))
            rows += [("용량", f"{D.fmt_size(disk.size)}  ({disk.size:,} 바이트)"),
                     ("파티션 형식", {"gpt": "GPT (GUID 파티션 테이블)", "dos": "MBR (마스터 부트 레코드)"}.get(
                         disk.table, "없음 (초기화되지 않음)" if disk.whole is None else "없음 (디스크 전체가 볼륨)")),
                     ("장치", disk.dev), ("디스크 상태", disk.health or "알 수 없음 (이 디스크는 상태를 알려 주지 않습니다)")]
            if disk.temp is not None:
                rows.append(("온도", f"{disk.temp} °C"))
            if disk.power_on_hours is not None:
                rows.append(("사용 시간", f"{disk.power_on_hours:,} 시간"))
        for i, (k, val) in enumerate(rows):
            lab = self._kv(grid, i, k, val)
            if k == "디스크 상태" and disk.health_bad:
                lab.get_style_context().add_class("error")
        dlg.connect("response", lambda d, _r: d.destroy())
        dlg.show_all()

    # ── 디스크 이미지 (윈도우의 VHD 연결 · 분리) ──
    def do_attach(self):
        if self.bus is None or self.busy:
            return
        fc = Gtk.FileChooserDialog(title="디스크 이미지 연결", transient_for=self.win, modal=True,
                                   action=Gtk.FileChooserAction.OPEN)
        fc.add_button("취소", Gtk.ResponseType.CANCEL)
        fc.add_button("연결", Gtk.ResponseType.ACCEPT)
        fc.set_default_response(Gtk.ResponseType.ACCEPT)
        flt = Gtk.FileFilter()
        flt.set_name("디스크 이미지 (.img · .iso · .raw · .vhd)")
        for pat in ("*.img", "*.iso", "*.raw", "*.vhd", "*.IMG", "*.ISO", "*.RAW", "*.VHD"):
            flt.add_pattern(pat)
        fc.add_filter(flt)
        allf = Gtk.FileFilter()
        allf.set_name("모든 파일")
        allf.add_pattern("*")
        fc.add_filter(allf)
        ro = Gtk.CheckButton(label="읽기 전용으로 연결")
        fc.set_extra_widget(ro)
        fc.connect("selection-changed", lambda *_: ro.set_active(
            (fc.get_filename() or "").lower().endswith(".iso") or not os.access(fc.get_filename() or "/", os.W_OK)))

        def resp(_d, r):
            path, readonly = fc.get_filename(), ro.get_active()
            fc.destroy()
            if r == Gtk.ResponseType.ACCEPT and path:
                self._loop_setup(path, readonly)
        fc.connect("response", resp)
        fc.show_all()

    def _loop_setup(self, path, readonly):
        try:
            fd = os.open(path, os.O_RDONLY if readonly else os.O_RDWR)
        except OSError as e:
            self.win.notice("디스크 이미지를 열지 못했습니다", f"{path}: {e.strerror}")
            return
        fdl = Gio.UnixFDList.new()
        try:
            idx = fdl.append(fd)
        finally:
            os.close(fd)
        self._set_busy(True, "디스크 이미지 연결")

        def fin(bus, res):
            self._set_busy(False)
            try:
                out, _fds = bus.call_with_unix_fd_list_finish(res)
            except GLib.Error as e:
                self._fail("디스크 이미지 연결", e)
                return
            obj = out.unpack()[0]
            self.sel = f"disk:{obj}"
            self._automount = obj                   # 윈도우처럼 볼륨을 바로 쓸 수 있게 연결해 둔다
            self.win.toast(f"{os.path.basename(path)} 을(를) 디스크로 연결했습니다")
            self.refresh()
        self.bus.call_with_unix_fd_list(D.UD, UD_PATH + "/Manager", D.UD + ".Manager", "LoopSetup",
                                        GLib.Variant("(ha{sv})", (idx, {"read-only": GLib.Variant("b", readonly)})),
                                        GLib.VariantType("(o)"), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                                        60000, fdl, None, fin)

    def _automount_now(self):
        """막 연결한 이미지의 볼륨을 연결 — 파티션은 조금 뒤에 나타날 수 있어 몇 번 다시 본다"""
        obj = getattr(self, "_automount", None)
        if not obj or self.busy:
            return
        self._automount_tries = getattr(self, "_automount_tries", 0) + 1
        d = self.items.get(f"disk:{obj}")
        steps = [(self._fs_path(v), D.I_FS, "Mount", GLib.Variant("(a{sv})", ({},)), "(s)")
                 for v in (d.segments if d else []) if not v.free and v.has_fs and not v.mounts]
        if not steps and self._automount_tries < 6:
            GLib.timeout_add(500, lambda: (self.refresh(), False)[1])
            return
        self._automount, self._automount_tries = None, 0
        if steps:
            self._chain(steps, lambda _ok: self.refresh(), "연결")

    def do_detach(self):
        it = self.selected()
        d = it if isinstance(it, D.Disk) else getattr(it, "disk", None)
        if d is None or not d.image:
            return
        steps = []
        for v in d.segments:
            if not v.free:
                steps += self._release_steps(v)
        steps.append((d.path, D.I_LOOP, "Delete", GLib.Variant("(a{sv})", ({},)), None))
        name = os.path.basename(d.image)
        self._chain(steps, lambda ok: (self.refresh(), ok and self.win.toast(f"{name} 을(를) 분리했습니다")),
                    "디스크 이미지 분리")


def build(win):
    return DisksPage(win)


PAGE = {"id": "disks", "title": "디스크 관리", "group": "storage", "order": 10,
        "icon": ["drive-harddisk-symbolic", "drive-harddisk", "gnome-disks"],
        "build": build, "css": CSS}
