"""컴퓨터 관리 — 디스크 관리 (윈도우의 "디스크 관리"처럼).

위: 볼륨 목록 (볼륨 · 위치 · 파일 시스템 · 상태 · 용량 · 사용 가능 공간 · % 사용 가능).
아래: 디스크 지도 — 디스크마다 한 줄, 파티션을 크기에 맞춘 칸으로 (할당되지 않은 공간은 검은 띠).
둘 중 어디를 눌러도 같은 것이 골라진다. 동작은 제목줄 단추 · 오른쪽 클릭 메뉴:
  열기 · 연결/연결 해제 · 꺼내기 · 새 볼륨(빈 공간) · 포맷 · 볼륨 삭제 · 이름 바꾸기 · 오류 검사 · 디스크 초기화.
모두 udisks2 를 D-Bus 로 부른다 (자료는 diskinfo.py). 관리자 권한이 필요한 것은 udisks 가 polkit 으로 묻는다
(ALLOW_INTERACTIVE_AUTHORIZATION → SekaiOS 사용자 계정 컨트롤 창).
SekaiOS 가 쓰는 볼륨(/ · 부팅 · 스왑)과 켜져 있는 설치 USB 는 포맷·삭제·초기화를 막는다 (diskinfo 의 protect).
지우는 일은 모두 먼저 묻는다 — 기본 단추는 취소.
나중에: 볼륨 확장·축소, 고정 연결 위치(윈도우의 드라이브 문자).
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
            self._text(cr, f"<b>{_esc(d.title)}</b>\n{_esc(d.model or d.kind)}\n"
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
            tip.set_text(f"{item.title} — {item.model or item.kind}\n{item.dev} · {D.fmt_size(item.size)} · "
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
    def on_show(self, **_kw):
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
        self.refresh()

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
        else:
            self.notice.hide_notice()

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
             "label": False, "check": False, "init": False, "eject": False}
        if it is None or self.busy:
            return c
        if isinstance(it, D.Disk):
            c["init"] = not D.disk_protect(it)
            c["eject"] = it.detachable and not it.system and not it.live
            return c
        d = it.disk
        c["eject"] = d.detachable and not d.system and not d.live
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
        return c

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
            menu_item(m, "꺼내기", self.do_eject, c["eject"])
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
            menu_item(m, "이름 바꾸기…", self.do_label, c["label"])
            menu_item(m, "오류 검사…", self.do_check, c["check"])
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "포맷…", self.do_format, c["format"])
            menu_item(m, "볼륨 삭제…", self.do_delete, c["delete"])
            if c["eject"]:
                m.append(Gtk.SeparatorMenuItem())
                menu_item(m, "꺼내기", self.do_eject, True)
        m.append(Gtk.SeparatorMenuItem())
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
        name = d.model or d.kind

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
        default = "exfat" if v.disk.removable else (v.fs if v.fs in ("ntfs", "exfat", "ext4", "vfat") else "ntfs")
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
        info.set_markup(f"{_esc(d.title)} ({_esc(d.model or d.kind)})의 할당되지 않은 공간 "
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
            txt.set_markup(f"<b>{_esc(d.title)}</b> ({_esc(d.model or d.kind)}, {_esc(D.fmt_size(d.size))})의 "
                           "<b>모든 볼륨과 파일이 지워집니다.</b>")
        else:
            txt.set_markup(f"<b>{_esc(d.title)}</b> ({_esc(d.model or d.kind)}, {_esc(D.fmt_size(d.size))})를 "
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


def build(win):
    return DisksPage(win)


PAGE = {"id": "disks", "title": "디스크 관리", "group": "storage", "order": 10,
        "icon": ["drive-harddisk-symbolic", "drive-harddisk", "gnome-disks"],
        "build": build, "css": CSS}
