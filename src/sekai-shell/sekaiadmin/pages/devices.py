"""컴퓨터 관리 — 장치 관리자 (윈도우 장치 관리자처럼).

왼쪽: 종류별 트리 (디스플레이 어댑터 · 네트워크 어댑터 …). 드라이버가 없거나 펌웨어를 못 읽은 장치에는 ⚠ 가 붙고,
그 종류는 처음부터 펼쳐 둔다(윈도우처럼). 오른쪽: 고른 장치의 상태·드라이버·장치 ID·자원과 바로 가기 단추
(커널 메시지 → 이벤트 뷰어, 그래픽 드라이버·네트워크·블루투스·프린터 설정 → 설정 앱).
아무것도 고르지 않으면 오른쪽은 요약 — 문제가 있는 장치 목록.

자료는 sekaiadmin.devinfo 가 작업 스레드에서 모은다 (/sys · udev 데이터베이스 · 커널 기록 — GTK 없음).
자동 갱신은 페이지가 보일 때만: GUdev(gir1.2-gudev-1.0)가 있으면 udev 알림(꽂기·뽑기·드라이버 붙기)으로,
없으면 몇 초마다 /sys 목록만 가볍게 견준다.
숨겨진 장치(브리지·루트 허브·가상 장치·HDMI 빈 출력 …)는 [보기 › 숨겨진 장치 표시] 에서.
장치 사용 안 함·제거(드라이버 떼기)는 일부러 없다 (잘못 누르면 키보드·디스크가 사라진다 — 나중에).

다른 곳에서: sekai-admin --page=devices --device=0000:01:00.0   (커널 이름으로 그 장치를 골라 연다)
"""
import os
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GUdev", "1.0")
    from gi.repository import GUdev  # noqa: E402
except (ValueError, ImportError):
    GUdev = None

from .. import devinfo  # noqa: E402
from ..common import (DetailGrid, NoticeBar, copy_text, gicon, key_is_menu, lookup_color,  # noqa: E402
                      menu_item, mk_view, popup, scrolled)

HERE = os.path.dirname(os.path.abspath(__file__))
POLL_SECS = 3                 # GUdev 가 없을 때 /sys 를 견주는 간격
SETTLE_MS = 700               # 장치 하나를 꽂아도 알림이 여러 개 온다 — 잠잠해진 뒤 한 번만 다시 모은다
C_KEY, C_ICON, C_MARKUP, C_STATE, C_TIP, C_CAT = range(6)
CAT_PREFIX = "cat:"

# 종류별 바로 가기 — sekai-settings --page=<id>
SETTINGS = {"graphics": "그래픽 드라이버 설정", "display": "디스플레이 설정", "network": "네트워크 설정",
            "bluetooth": "블루투스 설정", "printers": "프린터 설정", "sound": "소리 설정",
            "input": "키보드 및 마우스 설정", "power": "전원 설정"}

CSS = """
treeview.dm-tree { font-size: 13px; }
.dm-detail { padding: 2px 6px 12px 18px; }
label.dm-name { font-size: 17px; font-weight: 700; color: @fg; }
label.dm-kind { font-size: 12px; color: @text2; }
.dm-state { background: @card; border: 1px solid @line; border-radius: 8px; padding: 10px 14px; }
.dm-state.warn { background: alpha(@adm_warn, 0.12); border-color: alpha(@adm_warn, 0.45); }
.dm-state.off { background: alpha(@fg, 0.04); }
label.dm-state-title { font-weight: 600; color: @fg; }
label.dm-state-text { font-size: 12px; color: @text2; }
label.dm-sec { font-size: 13px; font-weight: 600; color: @fg; margin-top: 10px; }
button.dm-item { padding: 6px 10px; background: @card; border: 1px solid @line; border-radius: 8px; }
button.dm-item:hover { background: @hover; }
label.dm-dim { font-size: 12px; color: @text2; }
flowbox.dm-acts flowboxchild { padding: 0; }
"""


def _esc(s):
    return GLib.markup_escape_text(s or "")


def _hex(rgba):
    r, g, b, _a = rgba
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


class DevicesPage:
    searchable = True
    search_hint = "장치 이름, ID, 드라이버로 검색"

    def __init__(self, win):
        self.win = win
        st = win.page_state("devices") if hasattr(win, "page_state") else {}
        self.st = st
        self.show_hidden = bool(st.get("show_hidden"))
        self.expanded = set(x for x in (st.get("expanded") or []) if isinstance(x, str))
        self.collector = devinfo.Collector()
        self.snap = None
        self.query = ""
        self.busy = False
        self.visible = False
        self._scanning = False
        self._again = None            # 모으는 중에 또 요청이 오면 끝난 뒤 한 번 더 (수동이었는지)
        self._sig = None
        self._polling = False
        self._client = None
        self._client_sig = 0
        self._poll_src = self._settle_src = 0
        self._render_sig = None
        self._detail_sig = None
        self._building = False
        self._seen_warn = set()
        self._want_device = None
        self._icons = {}

        # ── 목록 ──
        self.store = Gtk.TreeStore(str, GObject.Object, str, str, str, bool)
        v = self.view = mk_view(self.store, fixed=False)
        v.get_style_context().add_class("dm-tree")
        v.set_headers_visible(False)
        v.set_tooltip_column(C_TIP)
        col = Gtk.TreeViewColumn()
        ir = Gtk.CellRendererPixbuf()
        ir.set_padding(4, 3)
        ir.props.stock_size = Gtk.IconSize.MENU
        col.pack_start(ir, False)
        col.add_attribute(ir, "gicon", C_ICON)
        tr = Gtk.CellRendererText()
        tr.props.ellipsize = Pango.EllipsizeMode.END
        tr.set_padding(4, 3)
        col.pack_start(tr, True)
        col.add_attribute(tr, "markup", C_MARKUP)
        sr = Gtk.CellRendererText()                         # 상태(⚠ 드라이버 없음)는 이름이 길어도 잘리지 않게 따로
        sr.set_padding(8, 3)
        col.pack_start(sr, False)
        col.add_attribute(sr, "markup", C_STATE)
        v.append_column(col)
        v.get_selection().connect("changed", lambda *_: self._on_select())
        v.connect("row-expanded", lambda _v, it, _p: self._on_expand(it, True))
        v.connect("row-collapsed", lambda _v, it, _p: self._on_expand(it, False))
        v.connect("row-activated", self._on_activate)
        v.connect("button-press-event", self._on_press)
        v.connect("key-press-event", self._on_key)
        left = scrolled(v)
        left.set_size_request(330, -1)

        # ── 세부 ──
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.detail.get_style_context().add_class("dm-detail")
        right = Gtk.ScrolledWindow()
        right.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right.add(self.detail)
        right.set_size_request(320, -1)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.pack1(left, True, False)
        self.paned.pack2(right, True, False)
        self.paned.connect("size-allocate", self._first_alloc)
        self.paned.connect("notify::position", self._on_paned)
        self._placed = False

        self.notice = NoticeBar()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.pack_start(self.notice, False, False, 0)
        box.pack_start(self.paned, True, True, 0)
        self.widget = box

        # ── 제목줄 단추 ──
        self.actions = Gtk.Box(spacing=8)
        mb = Gtk.MenuButton()
        mbox = Gtk.Box(spacing=4)
        mbox.pack_start(Gtk.Label(label="보기"), False, False, 0)
        mbox.pack_start(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU), False, False, 0)
        mb.add(mbox)
        menu = Gtk.Menu()
        self.hidden_item = Gtk.CheckMenuItem(label="숨겨진 장치 표시")
        self.hidden_item.set_active(self.show_hidden)
        self.hidden_item.connect("toggled", lambda it: self._set_hidden(it.get_active()))
        menu.append(self.hidden_item)
        menu.append(Gtk.SeparatorMenuItem())
        menu_item(menu, "모두 펼치기", lambda: self._expand_all(True))
        menu_item(menu, "모두 접기", lambda: self._expand_all(False))
        menu.show_all()
        mb.set_popup(menu)
        mb.set_tooltip_text("숨겨진 장치 표시 · 모두 펼치기")
        self.actions.pack_start(mb, False, False, 0)
        rb = Gtk.Button(label="변경 사항 검색")
        rb.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        rb.set_always_show_image(True)
        rb.set_tooltip_text("장치를 다시 찾아봅니다 (F5)")
        rb.connect("clicked", lambda *_: self.refresh())
        self.actions.pack_start(rb, False, False, 0)

        self._show_overview()

    # ── 페이지 규칙 ──
    def on_show(self, device=None, **_kw):
        if device:
            self._want_device = str(device)
        first = not self.visible
        self.visible = True
        self._start_watch()
        if first or device:
            # 가려진 동안에는 감시하지 않았다 — 보일 때마다 새로 모은다 (캐시가 있어 가볍다)
            self._scan(manual=self.snap is None)

    def on_hide(self):
        self.visible = False
        self._stop_watch()

    def refresh(self):
        self._scan(manual=True)

    def set_query(self, q):
        q = (q or "").strip().casefold()
        if q != self.query:
            self.query = q
            self._render()

    # ── 모으기 ──
    def _set_busy(self, on):
        self.busy = on
        bc = getattr(self.win, "busy_changed", None)
        if bc:
            bc()

    def _scan(self, manual=False):
        if self._scanning:
            self._again = bool(self._again) or manual
            return
        self._scanning = True
        if manual:
            self._set_busy(True)

        def work():
            return self.collector.scan(), devinfo.signature()

        def done(res, exc):
            self._scanning = False
            if manual:
                self._set_busy(False)
            if exc is not None:
                tw = getattr(self.win, "toast", None)
                if tw:
                    tw(f"장치 목록을 읽지 못했습니다: {exc}")
            elif res is not None:
                self._sig = res[1]
                self._apply(res[0], manual)
            if self._again is not None:
                again, self._again = self._again, None
                self._scan(manual=again)

        rt = getattr(self.win, "run_thread", None)
        if rt:
            rt(work, done)
            return

        def go():
            try:
                res, exc = work(), None
            except Exception as e:
                res, exc = None, e
            GLib.idle_add(lambda: (done(res, exc), False)[1])
        threading.Thread(target=go, daemon=True, name="sekai-admin-devices").start()

    def _apply(self, snap, manual):
        old = self.snap
        self.snap = snap
        # 새로 생긴 문제 장치는 그 종류를 펼쳐 보인다 (한 번 접으면 새 문제가 생기기 전까지는 그대로)
        for d in snap.problems:
            if d.key not in self._seen_warn:
                self._seen_warn.add(d.key)
                self.expanded.add(d.cat)
        self._save_expanded()
        self._render()
        self._update_notice()
        if self._want_device:
            self._select_device(self._want_device)
            self._want_device = None
        self._on_select()
        if manual and old is not None:
            vis = lambda s: {d.key for d in s.devs if not d.hidden}  # noqa: E731
            added, gone = vis(snap) - vis(old), vis(old) - vis(snap)
            msg = ("변경된 장치가 없습니다." if not added and not gone else
                   ", ".join(x for x in (f"새 장치 {len(added)}개" if added else "",
                                         f"사라진 장치 {len(gone)}개" if gone else "") if x) + "를 찾았습니다.")
            tw = getattr(self.win, "toast", None)
            if tw:
                tw(msg, 3)

    def _update_notice(self):
        if self.snap is not None and self.snap.klog == "denied":
            self.notice.show_notice("커널 기록을 읽을 권한이 없어 펌웨어를 못 읽은 장치는 찾지 못했습니다. "
                                    "관리자 계정으로 로그인하면(systemd-journal 그룹) 함께 확인합니다.", kind="info")
        else:
            self.notice.hide_notice()

    # ── 감시 (보일 때만) ──
    def _start_watch(self):
        if GUdev is not None and self._client is None:
            try:
                self._client = GUdev.Client.new(None)       # 모든 서브시스템
                self._client_sig = self._client.connect("uevent", self._on_uevent)
            except Exception as e:                          # netlink 를 못 여는 환경 — 폴링으로
                print("[sekai-admin] udev 감시를 시작하지 못했습니다:", e, flush=True)
                self._client = None
        if self._client is None and not self._poll_src:
            self._poll_src = GLib.timeout_add_seconds(POLL_SECS, self._poll)

    def _stop_watch(self):
        if self._client is not None:
            self._client.disconnect(self._client_sig)
            self._client = None                             # 놓으면 netlink 소켓이 닫힌다
        for attr in ("_poll_src", "_settle_src"):
            src = getattr(self, attr)
            if src:
                GLib.source_remove(src)
                setattr(self, attr, 0)

    def _on_uevent(self, _client, action, device):
        if not self.visible:
            return
        sub = device.get_subsystem() or ""
        # change 는 배터리 잔량·디스크 닫기처럼 자주 오는 것이 많다 — 모니터 연결·무선 끄기만 본다
        if action == "change" and sub not in ("drm", "rfkill", "net", "bluetooth"):
            return
        if self._settle_src:
            GLib.source_remove(self._settle_src)
        self._settle_src = GLib.timeout_add(SETTLE_MS, self._settled)

    def _settled(self):
        self._settle_src = 0
        if self.visible:
            self._scan()
        return False

    def _poll(self):
        if not self.visible:
            self._poll_src = 0
            return False
        if self._scanning or self._polling:
            return True
        self._polling = True

        def work():
            try:
                sig = devinfo.signature()
            except Exception:
                sig = None
            GLib.idle_add(got, sig)

        def got(sig):
            self._polling = False
            if sig is not None and self._sig is not None and sig != self._sig and self.visible:
                self._scan()
            return False
        threading.Thread(target=work, daemon=True, name="sekai-admin-devpoll").start()
        return True

    # ── 목록 그리기 ──
    def _icon(self, names, warn=False):
        key = (tuple(names or ()), warn)
        ic = self._icons.get(key)
        if ic is None:
            names = list(names or ())
            ic = gicon(names + [n + "-symbolic" for n in names] + ["application-x-executable"])
            if warn:                                        # 윈도우처럼 장치 아이콘 위에 작은 경고 표시
                ic = Gio.EmblemedIcon.new(ic, Gio.Emblem.new(Gio.ThemedIcon.new("dialog-warning")))
            self._icons[key] = ic
        return ic

    @staticmethod
    def _markup(d):
        s = _esc(d.name)
        if d.hidden:
            s = f"<span alpha='60%'>{s}</span>"             # 숨겨진 장치는 흐리게 (윈도우처럼)
        if d.suffix:
            s += f"   <span alpha='55%' size='small'>{_esc(d.suffix)}</span>"
        return s

    @staticmethod
    def _state_markup(d, warn_hex):
        if d.level == "warn":
            return f"<span foreground='{warn_hex}' weight='bold'>⚠ {_esc(d.state)}</span>"
        if d.level == "off" and d.state:
            return f"<span alpha='60%'>{_esc(d.state)}</span>"
        return ""

    @staticmethod
    def _tip(d):
        if d.level in ("warn", "off"):
            return _esc(f"{d.title} {d.detail}".strip())
        return _esc(d.kind or "")

    def _groups(self):
        q = self.query
        by = {}
        for d in self.snap.devs:
            if d.hidden and not self.show_hidden and d.level != "warn":
                continue
            if q and q not in d.search:
                continue
            by.setdefault(d.cat, []).append(d)
        out = []
        for cat, title, icons in devinfo.CATEGORIES:
            items = by.get(cat)
            if items:
                items.sort(key=lambda d: (d.order or (), d.name.casefold(), d.key))
                out.append((cat, title, icons, items))
        return out

    def _render(self):
        if self.snap is None:
            return
        warn_hex = _hex(lookup_color(self.view, "adm_warn", (0.85, 0.6, 0.0, 1.0)))
        groups = self._groups()
        rows = []
        for cat, title, icons, items in groups:
            nwarn = sum(1 for d in items if d.level == "warn")
            cm = f"{_esc(title)}  <span alpha='50%' size='small'>{len(items)}</span>"
            cs = f"<span foreground='{warn_hex}'>⚠ {nwarn}</span>" if nwarn else ""
            rows.append((cat, tuple(icons), cm, cs, [(d.key, tuple(d.icon or ()), d.level == "warn", self._markup(d),
                                                       self._state_markup(d, warn_hex), self._tip(d))
                                                      for d in items]))
        sig = (self.query, tuple((c, i, m, cs, tuple(ch)) for c, i, m, cs, ch in rows))
        if sig == self._render_sig:
            self._sync_expansion()
            return
        self._render_sig = sig
        keep = self._selected_key()
        adj = self.view.get_vadjustment()
        pos = adj.get_value() if adj else 0
        self._building = True
        try:
            self.view.set_model(None)                        # 다 채운 뒤 한 번에 (행마다 다시 그리지 않게)
            self.store.clear()
            for cat, icons, cm, cs, children in rows:
                parent = self.store.append(None, [CAT_PREFIX + cat, self._icon(icons), cm, cs,
                                                  _esc(devinfo.CAT_TITLE[cat]), True])
                for key, icon, warn, mk, sm, tip in children:
                    self.store.append(parent, [key, self._icon(icon, warn), mk, sm, tip, False])
            self.view.set_model(self.store)
            self._sync_expansion()
            if keep:
                self._select_key(keep)
        finally:
            self._building = False
        if adj is not None:
            GLib.idle_add(lambda: (adj.set_value(min(pos, max(0, adj.get_upper() - adj.get_page_size()))), False)[1])
        self._on_select()                                   # 고른 장치가 사라졌거나 검색에 걸러졌을 수 있다

    def _sync_expansion(self):
        """펼침 상태를 목록에 — 검색 중에는 모두 펼친다"""
        self._building, was = True, self._building
        try:
            it = self.store.get_iter_first()
            while it is not None:
                cat = self.store.get_value(it, C_KEY)[len(CAT_PREFIX):]
                path = self.store.get_path(it)
                want = bool(self.query) or cat in self.expanded
                if want and not self.view.row_expanded(path):
                    self.view.expand_row(path, False)
                elif not want and self.view.row_expanded(path):
                    self.view.collapse_row(path)
                it = self.store.iter_next(it)
        finally:
            self._building = was

    def _on_expand(self, it, on):
        if self._building or self.query:
            return
        cat = self.store.get_value(it, C_KEY)[len(CAT_PREFIX):]
        (self.expanded.add if on else self.expanded.discard)(cat)
        self._save_expanded()

    def _save_expanded(self):
        self.st["expanded"] = sorted(self.expanded)

    def _expand_all(self, on):
        if self.snap is None:
            return
        cats = {d.cat for d in self.snap.devs}
        self.expanded = set(cats) if on else set()
        self._save_expanded()
        self._sync_expansion()

    def _set_hidden(self, on):
        if on == self.show_hidden:
            return
        self.show_hidden = on
        self.st["show_hidden"] = on
        self._render()

    # ── 고르기 ──
    def _selected_key(self):
        model, it = self.view.get_selection().get_selected()
        return model.get_value(it, C_KEY) if it is not None else None

    def _find(self, key):
        it = self.store.get_iter_first()
        while it is not None:
            if self.store.get_value(it, C_KEY) == key:
                return it
            ch = self.store.iter_children(it)
            while ch is not None:
                if self.store.get_value(ch, C_KEY) == key:
                    return ch
                ch = self.store.iter_next(ch)
            it = self.store.iter_next(it)
        return None

    def _select_key(self, key, scroll=False):
        it = self._find(key)
        if it is None:
            return False
        path = self.store.get_path(it)
        if path.get_depth() > 1:
            self.view.expand_to_path(path)
        self.view.get_selection().select_path(path)
        if scroll:
            self.view.scroll_to_cell(path, None, True, 0.3, 0.0)
        return True

    def _select_device(self, what):
        """커널 이름(0000:01:00.0 · 1-3 · nvme0n1)이나 키로 — 숨겨진 장치여도 보이게"""
        d = next((x for x in self.snap.devs if what in (x.kname, x.key)), None)
        if d is None:
            return
        if d.hidden and not self.show_hidden:
            self.hidden_item.set_active(True)
        self.expanded.add(d.cat)
        self._sync_expansion()
        self._select_key(d.key, scroll=True)

    def _on_select(self):
        if self._building:
            return
        key = self._selected_key()
        if key is None:
            if self.snap is not None and not self.store.iter_n_children(None):
                self._show_empty()
            else:
                self._show_overview()
        elif key.startswith(CAT_PREFIX):
            self._show_category(key[len(CAT_PREFIX):])
        else:
            d = self.snap.by_key.get(key) if self.snap else None
            if d is None:
                self._show_overview()
            else:
                self._show_device(d)

    def _on_activate(self, v, path, _col):
        it = self.store.get_iter(path)
        if self.store.get_value(it, C_CAT):
            if v.row_expanded(path):
                v.collapse_row(path)
            else:
                v.expand_row(path, False)

    # ── 오른쪽 칸 ──
    def _begin(self, sig):
        """같은 내용이면 다시 만들지 않는다 (자동 갱신 때 번쩍이거나 스크롤이 튀지 않게)"""
        if sig == self._detail_sig:
            return False
        self._detail_sig = sig
        for c in self.detail.get_children():
            c.destroy()
        return True

    def _header(self, icon, title, sub):
        h = Gtk.Box(spacing=12)
        img = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DIALOG)
        img.set_pixel_size(40)
        img.set_valign(Gtk.Align.START)
        h.pack_start(img, False, False, 0)
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=title, xalign=0)
        t.set_line_wrap(True)
        t.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        t.set_selectable(True)
        t.set_can_focus(False)
        t.get_style_context().add_class("dm-name")
        tb.pack_start(t, False, False, 0)
        if sub:
            s = Gtk.Label(label=sub, xalign=0)
            s.set_line_wrap(True)
            s.get_style_context().add_class("dm-kind")
            tb.pack_start(s, False, False, 0)
        h.pack_start(tb, True, True, 0)
        self.detail.pack_start(h, False, False, 0)

    def _state_box(self, level, title, text="", hints=()):
        box = Gtk.Box(spacing=12)
        ctx = box.get_style_context()
        ctx.add_class("dm-state")
        if level in ("warn", "off"):
            ctx.add_class(level)
        icon = {"warn": "dialog-warning", "off": "system-shutdown-symbolic", "info": "dialog-information"}.get(
            level, "emblem-ok-symbolic")
        img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        img.set_valign(Gtk.Align.START)
        box.pack_start(img, False, False, 0)
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for i, s in enumerate([title, text] + list(hints)):
            if not s:
                continue
            lb = Gtk.Label(label=s, xalign=0)
            lb.set_line_wrap(True)
            lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lb.set_selectable(i > 0)
            lb.set_can_focus(False)
            lb.get_style_context().add_class("dm-state-title" if i == 0 else "dm-state-text")
            tb.pack_start(lb, False, False, 0)
        box.pack_start(tb, True, True, 0)
        self.detail.pack_start(box, False, False, 0)

    def _item_button(self, d):
        """요약·종류 칸의 장치 한 줄 — 누르면 트리에서 그 장치를 고른다"""
        b = Gtk.Button()
        b.get_style_context().add_class("dm-item")
        b.set_relief(Gtk.ReliefStyle.NONE)
        h = Gtk.Box(spacing=10)
        h.pack_start(Gtk.Image.new_from_gicon(self._icon(d.icon, d.level == "warn"), Gtk.IconSize.MENU),
                     False, False, 0)
        lb = Gtk.Label(xalign=0)
        lb.set_ellipsize(Pango.EllipsizeMode.END)
        extra = d.state if d.level in ("warn", "off") else d.suffix
        name = f"<span alpha='60%'>{_esc(d.name)}</span>" if d.hidden else _esc(d.name)
        lb.set_markup(name + (f"  <span alpha='60%' size='small'>{_esc(extra)}</span>" if extra else ""))
        h.pack_start(lb, True, True, 0)
        b.add(h)
        b.connect("clicked", lambda *_: self._goto(d))
        return b

    def _goto(self, d):
        if d.hidden and d.level != "warn" and not self.show_hidden:
            self.hidden_item.set_active(True)
        if self.query and self.query not in d.search:
            se = getattr(self.win, "search", None)
            if se is not None:
                se.set_text("")
        self.expanded.add(d.cat)
        self._save_expanded()
        self._sync_expansion()
        self._select_key(d.key, scroll=True)
        self.view.grab_focus()

    def _section(self, title, rows):
        lb = Gtk.Label(label=title, xalign=0)
        lb.get_style_context().add_class("dm-sec")
        self.detail.pack_start(lb, False, False, 0)
        g = DetailGrid(key_width=120)
        g.set_rows(rows)
        self.detail.pack_start(g, False, False, 0)

    def _dim(self, text):
        lb = Gtk.Label(label=text, xalign=0)
        lb.set_line_wrap(True)
        lb.get_style_context().add_class("dm-dim")
        self.detail.pack_start(lb, False, False, 0)

    def _show_empty(self):
        hid = sum(1 for d in self.snap.devs if d.hidden and self.query and self.query in d.search) \
            if self.snap and not self.show_hidden else 0
        if not self._begin(("empty", self.query, hid)):
            return
        self._dim("검색어와 맞는 장치가 없습니다." if self.query else "보여 줄 장치가 없습니다.")
        if hid:
            self._dim(f"숨겨진 장치 중에는 {hid}개가 맞습니다.")
            b = Gtk.Button(label="숨겨진 장치 표시")
            b.set_halign(Gtk.Align.START)
            b.connect("clicked", lambda *_: self.hidden_item.set_active(True))
            self.detail.pack_start(b, False, False, 0)
        self.detail.show_all()

    def _show_overview(self):
        snap = self.snap
        if snap is None:
            if self._begin(("loading",)):
                self._dim("장치를 찾는 중…")
                self.detail.show_all()
            return
        pc = snap.by_key.get("computer")
        sig = ("overview", pc.name if pc else "", tuple((d.key, d.state) for d in snap.problems), snap.klog,
               len(snap.devs), self.show_hidden)
        if not self._begin(sig):
            return
        shown = [d for d in snap.devs if not d.hidden]
        self._header(self._icon(pc.icon if pc else ["computer"]), pc.name if pc else "이 컴퓨터",
                     f"장치 {len(shown)}개 · 숨겨진 장치 {len(snap.devs) - len(shown)}개")
        if snap.problems:
            self._state_box("warn", f"문제가 있는 장치가 {len(snap.problems)}개 있습니다.",
                            "장치를 누르면 이유와 해결 방법을 볼 수 있습니다.")
            for d in snap.problems:
                self.detail.pack_start(self._item_button(d), False, False, 0)
        elif snap.klog == "denied":                         # 펌웨어 오류는 못 봤다 — 위 안내 막대가 이유를 말한다
            self._state_box("ok", "드라이버가 없는 장치는 없습니다.")
        else:
            self._state_box("ok", "모든 장치가 올바르게 작동하고 있습니다.")
        off = [d for d in snap.devs if d.level == "off" and not d.hidden]
        if off:
            lb = Gtk.Label(label="꺼져 있는 장치", xalign=0)
            lb.get_style_context().add_class("dm-sec")
            self.detail.pack_start(lb, False, False, 0)
            for d in off:
                self.detail.pack_start(self._item_button(d), False, False, 0)
        self._dim("장치를 꽂거나 빼면 목록이 저절로 바뀝니다.")
        self.detail.show_all()

    def _show_category(self, cat):
        items = []
        for cat_, _t, _i, ds in self._groups() if self.snap else ():
            if cat_ == cat:
                items = ds
        sig = ("cat", cat, tuple((d.key, d.name, d.state, d.suffix) for d in items))
        if not self._begin(sig):
            return
        self._header(self._icon(devinfo.CAT_ICON.get(cat)), devinfo.CAT_TITLE.get(cat, ""), f"장치 {len(items)}개")
        for d in items:
            self.detail.pack_start(self._item_button(d), False, False, 0)
        self.detail.show_all()

    def _show_device(self, d):
        sig = ("dev", d.key, d.as_text(), tuple(d.actions))
        if not self._begin(sig):
            return
        sub = devinfo.CAT_TITLE.get(d.cat, "")
        if d.vendor and d.vendor.casefold() not in d.name.casefold():
            sub += f" · {d.vendor}"
        self._header(self._icon(d.icon, d.level == "warn"), d.name, sub)
        self._state_box(d.level, d.title, d.detail, d.hints)
        acts = Gtk.FlowBox()
        acts.get_style_context().add_class("dm-acts")
        acts.set_selection_mode(Gtk.SelectionMode.NONE)
        acts.set_max_children_per_line(4)
        acts.set_column_spacing(6)
        acts.set_row_spacing(6)
        primary = "graphics" if (d.level == "warn" and d.cat == "display") else None
        for a in d.actions:
            b = Gtk.Button(label=SETTINGS[a]) if a in SETTINGS else None
            if b is None:
                continue
            if a == primary:
                b.get_style_context().add_class("suggested-action")
            b.connect("clicked", lambda _b, a=a: self._open_settings(a))
            acts.add(b)
        if d.kname:
            b = Gtk.Button(label="커널 메시지 보기")
            b.set_tooltip_text(f"이벤트 뷰어에서 '{d.kname}' 이(가) 들어간 시스템 기록을 봅니다")
            b.connect("clicked", lambda *_: self._events(d))
            acts.add(b)
        b = Gtk.Button(label="세부 정보 복사")
        b.connect("clicked", lambda *_: self._copy(d))
        acts.add(b)
        self.detail.pack_start(acts, False, False, 0)
        for title, rows in d.sections():
            self._section(title, rows)
        self.detail.show_all()

    def _on_paned(self, p, _spec):
        if self._placed:
            self.st["paned"] = p.get_position()

    def _first_alloc(self, p, alloc):
        if self._placed or alloc.width < 400:
            return
        self._placed = True
        pos = self.st.get("paned")
        pos = pos if isinstance(pos, int) and 300 < pos < alloc.width - 280 else int(alloc.width * 0.5)
        GLib.idle_add(lambda: (p.set_position(pos), False)[1])

    # ── 동작 ──
    def _open_settings(self, page):
        exe = shutil.which("sekai-settings") or os.path.normpath(os.path.join(HERE, "..", "..", "sekai-settings"))
        try:
            subprocess.Popen([exe, f"--page={page}"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            tw = getattr(self.win, "toast", None)
            if tw:
                tw(f"설정 앱을 열지 못했습니다: {e}")

    def _events(self, d):
        """이벤트 뷰어의 시스템 기록을 이 장치의 커널 이름으로 거른다"""
        sp = getattr(self.win, "show_page", None)
        if sp is None:
            return
        sp("events", view="system", query=d.kname)      # 이벤트 뷰어가 query 를 받게 되면 그것으로
        if getattr(self.win, "current", None) != "events":
            return                                      # 그 페이지가 없다 (창이 알렸다)
        se = getattr(self.win, "search", None)          # 검색 칸 글이 바뀌면 창이 그 페이지의 set_query 를 부른다
        if isinstance(se, Gtk.SearchEntry):
            se.set_text(d.kname)

    def _copy(self, d):
        copy_text(d.as_text())
        tw = getattr(self.win, "toast", None)
        if tw:
            tw("세부 정보를 클립보드에 복사했습니다", 3)

    # ── 메뉴·키 ──
    def _on_press(self, v, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        hit = v.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return True
        v.get_selection().select_path(hit[0])
        self._menu(ev)
        return True

    def _on_key(self, _v, ev):
        if key_is_menu(ev):
            self._menu(ev)
            return True
        return False

    def _menu(self, ev):
        key = self._selected_key()
        if key is None:
            return
        m = Gtk.Menu()
        if key.startswith(CAT_PREFIX):
            menu_item(m, "모두 펼치기", lambda: self._expand_all(True))
            menu_item(m, "모두 접기", lambda: self._expand_all(False))
        else:
            d = self.snap.by_key.get(key) if self.snap else None
            if d is None:
                return
            if d.kname:
                menu_item(m, "커널 메시지 보기", lambda: self._events(d))
            for a in d.actions:
                if a in SETTINGS:
                    menu_item(m, SETTINGS[a], lambda a=a: self._open_settings(a))
            m.append(Gtk.SeparatorMenuItem())
            menu_item(m, "세부 정보 복사", lambda: self._copy(d))
            menu_item(m, "이름 복사", lambda: copy_text(d.name))
        popup(m, self.view, ev)


def build(win):
    return DevicesPage(win)


PAGE = {"id": "devices", "title": "장치 관리자", "group": "system", "order": 40,
        "icon": ["computer-symbolic", "preferences-desktop-peripherals-symbolic", "drive-harddisk-symbolic",
                 "hardinfo", "computer"],
        "build": build, "css": CSS}
