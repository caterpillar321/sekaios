"""작업 관리자 — 성능 탭.

왼쪽: 장치마다 작은 그래프와 지금 값 (CPU · 메모리 · 디스크 · 네트워크 · GPU).
오른쪽: 고른 장치의 큰 그래프와 세부 값.
기록은 탭이 안 보여도 늘 쌓고(가볍다), 그리기는 보일 때만. 그래프는 시각 기준으로 최근 60초 —
업데이트 속도를 바꿔도 가로축이 같고, 일시 중지면 멈춘 자리에 머문다.
"""
import collections
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import taskmgr_data as D  # noqa: E402
from .taskmgr_common import lookup_color  # noqa: E402

SPAN = 60.0


class Series:
    def __init__(self):
        self.pts = collections.deque(maxlen=400)      # 0.5초 × 120 + 여유

    def add(self, t, v):
        if v is not None:
            self.pts.append((t, float(v)))

    def last(self):
        return self.pts[-1][1] if self.pts else None

    def peak(self, now):
        return max((v for t, v in self.pts if t >= now - SPAN), default=0.0)


def bits_pair(a, b):
    """네트워크 한 줄 — 보내기·받기를 같은 단위로 (윈도우처럼 '보내기: 0 받기: 8.0 Kbps')"""
    m = max(a or 0, b or 0)
    div, unit = 1e3, "Kbps"
    for d, u in ((1e9, "Gbps"), (1e6, "Mbps")):
        if m >= d:
            div, unit = d, u
            break

    def f(v):
        v = (v or 0) / div
        return "0" if v < 0.05 else f"{v:.1f}"
    return f(a), f(b), unit


def nice_max(v, floor):
    """자동 눈금 — 1·2·5 × 10ⁿ 로 올림"""
    v = max(v, floor)
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * e * 1.0000001:
            return m * e
    return 10 * e


def draw_graph(cr, widget, x, y, w, h, series, maxv, now, gap, grid=True, lw=1.4):
    """series = [(Series, "fill" | "dash")]. 간격(gap)보다 오래 끊긴 곳은 선을 잇지 않는다 (일시 중지·탭 전환)"""
    ar, ag, ab, _ = lookup_color(widget, "accent", (0.22, 0.77, 0.73, 1))
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.set_source_rgba(*lookup_color(widget, "card", (0.15, 0.15, 0.17, 1)))
    cr.fill_preserve()
    cr.clip()
    cr.set_line_width(1)
    if grid:                                            # 윈도우처럼 10 × 10 칸
        cr.set_source_rgba(ar, ag, ab, 0.13)
        for i in range(1, 10):
            gx = int(x + w * i / 10) + 0.5
            cr.move_to(gx, y)
            cr.line_to(gx, y + h)
            gy = int(y + h * i / 10) + 0.5
            cr.move_to(x, gy)
            cr.line_to(x + w, gy)
        cr.stroke()
    maxv = maxv or 1.0
    for s, style in series:
        segs, cur, prev = [], [], None
        for t, v in s.pts:
            if t < now - SPAN - gap:
                continue
            if prev is not None and t - prev > gap:
                segs.append(cur)
                cur = []
            cur.append((x + w - (now - t) / SPAN * w, y + h - max(0.0, min(v, maxv)) / maxv * h))
            prev = t
        segs.append(cur)
        for seg in segs:
            if len(seg) < 2:
                continue
            if style == "fill":
                cr.move_to(seg[0][0], y + h)
                for px, py in seg:
                    cr.line_to(px, py)
                cr.line_to(seg[-1][0], y + h)
                cr.close_path()
                cr.set_source_rgba(ar, ag, ab, 0.18)
                cr.fill()
            cr.move_to(*seg[0])
            for px, py in seg[1:]:
                cr.line_to(px, py)
            cr.set_source_rgba(ar, ag, ab, 1.0)
            cr.set_line_width(lw)
            cr.set_dash([4.0, 3.0] if style == "dash" else [])
            cr.stroke()
    cr.set_dash([])
    cr.restore()
    cr.set_line_width(1)
    cr.set_source_rgba(ar, ag, ab, 0.75)
    cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
    cr.stroke()


class Graph(Gtk.DrawingArea):
    def __init__(self, page, series, maxv=100.0, grid=True, height=160, lw=1.4):
        super().__init__()
        self.page, self.series, self.maxv, self.grid, self.lw = page, series, maxv, grid, lw
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        a = self.get_allocation()
        draw_graph(cr, self, 0, 0, a.width, a.height, self.series, self.maxv, self.page.now,
                   self.page.gap, self.grid, self.lw)
        return False


class CoreGrid(Gtk.DrawingArea):
    """논리 프로세서마다 작은 그래프 — 한 그림판에 격자로 (위젯 수십 개를 만들지 않게)"""

    def __init__(self, page, series):
        super().__init__()
        self.page, self.series = page, series
        self.set_size_request(-1, 160)
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        a = self.get_allocation()
        n = max(1, len(self.series))
        cols = max(1, math.ceil(math.sqrt(n * a.width / max(1, a.height))))
        rows = math.ceil(n / cols)
        cols = math.ceil(n / rows)
        gap = 4
        cw = (a.width - gap * (cols - 1)) / cols
        ch = (a.height - gap * (rows - 1)) / rows
        for i, s in enumerate(self.series):
            r, c = divmod(i, cols)
            draw_graph(cr, self, int(c * (cw + gap)), int(r * (ch + gap)), int(cw), int(ch), [(s, "fill")],
                       100.0, self.page.now, self.page.gap, grid=False, lw=1.1)
        return False


class MemBar(Gtk.DrawingArea):
    """메모리 구성 — 사용 중 | 캐시 | 비어 있음"""

    def __init__(self):
        super().__init__()
        self.parts = (0, 0, 1)
        self.set_size_request(-1, 36)
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        a = self.get_allocation()
        ar, ag, ab, _ = lookup_color(self, "accent", (0.22, 0.77, 0.73, 1))
        cr.set_source_rgba(*lookup_color(self, "card", (0.15, 0.15, 0.17, 1)))
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        used, cache, total = self.parts
        x = 0.0
        for val, alpha in ((used, 0.55), (cache, 0.22)):
            ww = a.width * val / max(1, total)
            cr.set_source_rgba(ar, ag, ab, alpha)
            cr.rectangle(x, 0, ww, a.height)
            cr.fill()
            x += ww
            cr.set_source_rgba(ar, ag, ab, 0.75)
            cr.move_to(int(x) + 0.5, 0)
            cr.line_to(int(x) + 0.5, a.height)
            cr.stroke()
        cr.set_source_rgba(ar, ag, ab, 0.75)
        cr.rectangle(0.5, 0.5, a.width - 1, a.height - 1)
        cr.stroke()
        return False


# ── 오른쪽 세부 화면의 조각 ──────────────────────────────────
def _label(text, cls, xalign=0.0, ellipsize=False):
    lbl = Gtk.Label(label=text, xalign=xalign)
    lbl.get_style_context().add_class(cls)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    return lbl


def _set(lbl, text):
    if lbl.get_text() != text:
        lbl.set_text(text)


class Detail:
    """세부 화면 틀 — 제목 줄, 그래프(설명·눈금), 값 표"""

    def __init__(self, title, right=""):
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.get_style_context().add_class("perf-detail")
        head = Gtk.Box(spacing=12)
        self.title = _label(title, "perf-head")
        self.right = _label(right, "perf-model", 1.0, True)
        head.pack_start(self.title, False, False, 0)
        head.pack_end(self.right, True, True, 0)
        self.box.pack_start(head, False, False, 0)
        self.big, self.kv = {}, {}

    def caption(self, left, right="", extra=None):
        row = Gtk.Box(spacing=8)
        row.get_style_context().add_class("perf-caprow")
        row.pack_start(_label(left, "perf-cap"), False, False, 0)
        r = _label(right, "perf-cap", 1.0)
        row.pack_end(r, False, False, 0)
        if extra is not None:
            row.pack_end(extra, False, False, 8)
        self.box.pack_start(row, False, False, 0)
        return r

    def graph(self, widget, expand=True):
        self.box.pack_start(widget, expand, expand, 0)
        foot = Gtk.Box()
        foot.get_style_context().add_class("perf-foot")
        foot.pack_start(_label("60초", "perf-cap"), False, False, 0)
        foot.pack_end(_label("0", "perf-cap"), False, False, 0)
        self.box.pack_start(foot, False, False, 0)
        return foot

    def stats(self, big, kv):
        """big = [(열쇠, 설명)] (두 칸씩), kv = [(열쇠, 설명)]"""
        area = Gtk.Box(spacing=48)
        area.get_style_context().add_class("perf-stats")
        g = Gtk.Grid(column_spacing=32, row_spacing=12)
        for i, (key, cap) in enumerate(big):
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            v.pack_start(_label(cap, "perf-cap"), False, False, 0)
            val = _label("—", "perf-big-val")
            v.pack_start(val, False, False, 0)
            g.attach(v, i % 2, i // 2, 1, 1)
            self.big[key] = val
        area.pack_start(g, False, False, 0)
        t = Gtk.Grid(column_spacing=18, row_spacing=4)
        for i, (key, cap) in enumerate(kv):
            t.attach(_label(cap, "perf-kv-key"), 0, i, 1, 1)
            val = _label("—", "perf-kv-val", 0.0, True)
            val.set_max_width_chars(40)
            val.set_selectable(True)
            val.set_can_focus(False)
            t.attach(val, 1, i, 1, 1)
            self.kv[key] = val
        area.pack_start(t, False, False, 0)
        self.box.pack_start(area, False, False, 0)
        self.stats_area = area                    # 좁으면 세부 표를 큰 숫자 아래로 (PerfPage._set_narrow)

    def set(self, key, text):
        lbl = self.big.get(key) or self.kv.get(key)
        if lbl is not None:
            _set(lbl, text)


class CpuView:
    def __init__(self, page, info):
        self.page = page
        d = self.d = Detail("CPU", info.get("model", ""))
        self.toggle = Gtk.ToggleButton(label="논리 프로세서별")
        self.toggle.get_style_context().add_class("perf-toggle")
        self.toggle.set_tooltip_text("그래프를 전체 사용률 / 논리 프로세서마다로 바꿉니다")
        d.caption("% 사용률", "100%", self.toggle)
        self.stack = Gtk.Stack()
        self.total = Graph(page, [(page.series("cpu"), "fill")], 100.0)
        self.cores = CoreGrid(page, [page.series(f"cpu/{i}") for i in range(info["logical"])])
        self.stack.add_named(self.total, "total")
        self.stack.add_named(self.cores, "cores")
        d.graph(self.stack)
        kv = [("base", info.get("base_label", "기본 속도")), ("sockets", "소켓"), ("cores", "코어"),
              ("logical", "논리 프로세서"), ("virt", "가상 머신" if info.get("vm") else "가상화"),
              ("l1", "L1 캐시"), ("l2", "L2 캐시"), ("l3", "L3 캐시")]
        d.stats([("use", "사용률"), ("speed", "속도"), ("procs", "프로세스"), ("threads", "스레드"),
                 ("uptime", "작동 시간")], kv)
        d.set("base", f"{info['base']:.2f}GHz" if info.get("base") else "—")
        d.set("sockets", str(info["sockets"]))
        d.set("cores", str(info["cores"]))
        d.set("logical", str(info["logical"]))
        d.set("virt", "예" if info.get("vm") else (info.get("virt") or "—"))
        for k in ("l1", "l2", "l3"):
            d.set(k, D.fmt_kb(info[k]) if info.get(k) else "—")
        self.toggle.connect("toggled", self._on_toggle)
        self.widget = d.box
        # 그래프를 오른쪽 클릭해도 바꿀 수 있게 (윈도우의 '그래프 변경')
        for g in (self.total, self.cores):
            g.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            g.connect("button-press-event", self._on_press)
        self.widget.show_all()
        self.toggle.set_active(page.win.state.get("cpu_view") == "cores")
        self._on_toggle(self.toggle)

    def _on_toggle(self, b):
        self.stack.set_visible_child_name("cores" if b.get_active() else "total")
        self.page.win.state["cpu_view"] = "cores" if b.get_active() else "total"

    def _on_press(self, _w, ev):
        if ev.button == 3:
            m = Gtk.Menu()
            for label, on in (("전체 사용률", False), ("논리 프로세서", True)):
                it = Gtk.CheckMenuItem(label=label)
                it.set_draw_as_radio(True)
                it.set_active(self.toggle.get_active() == on)
                it.connect("activate", lambda _i, on=on: self.toggle.set_active(on))
                m.append(it)
            m.show_all()
            m.attach_to_widget(self.widget, None)
            m.popup_at_pointer(ev)
            return True
        return False

    def update(self, snap):
        d = self.d
        info = snap["cpuinfo"]
        freq = snap.get("freq") or ((info.get("mhz") or 0) / 1000) or None
        d.set("use", f"{snap['cpu']:.0f}%")
        d.set("speed", f"{freq:.2f}GHz" if freq else "—")
        d.set("procs", f"{snap.get('nproc', 0):,}")
        d.set("threads", f"{snap.get('nthreads', 0):,}")
        d.set("uptime", D.fmt_uptime(snap.get("uptime")))
        (self.cores if self.toggle.get_active() else self.total).queue_draw()


class MemView:
    def __init__(self, page, mem):
        self.page = page
        d = self.d = Detail("메모리", D.fmt_gb(mem["total"]))
        self.cap_total = d.caption("메모리 사용량", D.fmt_gb(mem["total"]))
        self.graph = Graph(page, [(page.series("mem"), "fill")], float(mem["total"] or 1))
        d.graph(self.graph)
        d.caption("메모리 구성")
        self.bar = MemBar()
        d.box.pack_start(self.bar, False, False, 0)
        d.stats([("used", "사용 중"), ("avail", "사용 가능"), ("commit", "커밋됨"), ("cached", "캐시됨"),
                 ("swap", "스왑")],
                [("total", "전체"), ("shmem", "공유 메모리"), ("swap_total", "스왑 크기")])
        self.widget = d.box
        self.widget.show_all()

    def update(self, snap):
        m, d = snap["mem"], self.d
        self.graph.maxv = float(m["total"] or 1)
        d.set("used", D.fmt_gb(m["used"]))
        d.set("avail", D.fmt_gb(m["avail"]))
        d.set("commit", f"{D.fmt_gb(m['commit'])[:-2]}/{D.fmt_gb(m['commit_limit'])}")
        d.set("cached", D.fmt_gb(m["cached"]))
        d.set("swap", f"{D.fmt_gb(m['swap_used'])[:-2]}/{D.fmt_gb(m['swap_total'])}" if m["swap_total"] else "없음")
        d.set("total", D.fmt_gb(m["total"]))
        d.set("shmem", D.fmt_gb(m["shmem"]))
        d.set("swap_total", D.fmt_gb(m["swap_total"]) if m["swap_total"] else "없음")
        self.bar.parts = (m["used"], max(0, m["avail"] - m["free"]), m["total"])
        self.bar.set_tooltip_text(f"사용 중 {D.fmt_gb(m['used'])} · 캐시 {D.fmt_gb(max(0, m['avail'] - m['free']))}"
                                  f" · 비어 있음 {D.fmt_gb(m['free'])}")
        self.graph.queue_draw()
        self.bar.queue_draw()


def disk_title(dev):
    mounts = [m for m in dev["mounts"] if not m.startswith(("/snap/", "/run/", "/var/lib/"))][:2]
    return f"디스크 {dev['index']}" + (f" ({', '.join(mounts)})" if mounts else "")


class DiskView:
    def __init__(self, page, dev):
        self.page, self.id = page, dev["id"]
        d = self.d = Detail(disk_title(dev), dev["model"] or dev["dev"])
        d.caption("활성 시간", "100%")
        self.busy = Graph(page, [(page.series(f"disk/{self.id}/busy"), "fill")], 100.0, height=120)
        d.graph(self.busy)
        self.scale = d.caption("디스크 전송 속도  (실선 읽기 · 점선 쓰기)", "")
        self.rate = Graph(page, [(page.series(f"disk/{self.id}/rd"), "fill"),
                                 (page.series(f"disk/{self.id}/wr"), "dash")], 1.0, height=120)
        d.graph(self.rate)
        d.stats([("busy", "활성 시간"), ("resp", "평균 응답 시간"), ("rd", "읽기 속도"), ("wr", "쓰기 속도")],
                [("size", "용량"), ("system", "시스템 디스크"), ("swap", "스왑"), ("kind", "종류"),
                 ("dev", "장치"), ("mounts", "마운트 위치")])
        self.widget = d.box
        self.widget.show_all()

    def update(self, dev):
        d, now = self.d, self.page.now
        _set(d.title, disk_title(dev))
        peak = max(self.page.series(f"disk/{self.id}/rd").peak(now), self.page.series(f"disk/{self.id}/wr").peak(now))
        self.rate.maxv = nice_max(peak * 1.1, 1024 * 1024)
        _set(self.scale, D.fmt_rate(self.rate.maxv))
        d.set("busy", f"{dev['busy']:.0f}%")
        d.set("resp", f"{dev['resp']:.1f}ms")
        d.set("rd", D.fmt_rate(dev["rd"]))
        d.set("wr", D.fmt_rate(dev["wr"]))
        d.set("size", D.fmt_size(dev["size"]))
        d.set("system", "예" if dev["system"] else "아니요")
        d.set("swap", "예" if dev["swap"] else "아니요")
        d.set("kind", dev["kind"])
        d.set("dev", dev["dev"])
        d.set("mounts", ", ".join(dev["mounts"][:6]) or "없음")
        self.busy.queue_draw()
        self.rate.queue_draw()


class NetView:
    def __init__(self, page, dev):
        self.page, self.id = page, dev["id"]
        d = self.d = Detail(dev["kind"], dev["id"])
        self.scale = d.caption("처리량  (실선 받기 · 점선 보내기)", "")
        self.graph = Graph(page, [(page.series(f"net/{self.id}/rx"), "fill"),
                                  (page.series(f"net/{self.id}/tx"), "dash")], 1.0)
        d.graph(self.graph)
        d.stats([("tx", "보내기"), ("rx", "받기")],
                [("name", "어댑터 이름"), ("driver", "드라이버"), ("kind", "연결 형식"), ("ipv4", "IPv4 주소"),
                 ("ipv6", "IPv6 주소"), ("speed", "링크 속도"), ("state", "상태")])
        self.widget = d.box
        self.widget.show_all()

    def update(self, dev):
        d, now = self.d, self.page.now
        peak = max(self.page.series(f"net/{self.id}/rx").peak(now), self.page.series(f"net/{self.id}/tx").peak(now))
        self.graph.maxv = nice_max(peak * 1.1, 100_000)
        _set(self.scale, D.fmt_bits(self.graph.maxv))
        d.set("tx", D.fmt_bits(dev["tx"]))
        d.set("rx", D.fmt_bits(dev["rx"]))
        d.set("name", dev["id"])
        d.set("driver", dev["driver"] or "—")
        d.set("kind", dev["kind"])
        d.set("ipv4", dev["ipv4"] or "—")
        d.set("ipv6", dev["ipv6"] or "—")
        d.set("speed", (f"{dev['speed'] / 1000:g} Gbps" if dev["speed"] >= 1000 else f"{dev['speed']} Mbps")
              if dev["speed"] else "—")
        d.set("state", "연결됨" if dev["up"] else "연결 안 됨")
        self.graph.queue_draw()


class GpuView:
    def __init__(self, page, dev):
        self.page, self.id = page, dev["id"]
        d = self.d = Detail(f"GPU {dev['index']}", dev["name"])
        d.caption("사용률", "100%")
        self.busy = Graph(page, [(page.series(f"gpu/{self.id}/busy"), "fill")], 100.0)
        d.graph(self.busy)
        self.mem_cap = d.caption("전용 GPU 메모리 사용량", "")
        self.vram = Graph(page, [(page.series(f"gpu/{self.id}/vram"), "fill")], 1.0, height=100)
        self.vram_foot = d.graph(self.vram, expand=False)
        self.note = _label("", "perf-note", 0.0)
        self.note.set_line_wrap(True)
        d.box.pack_start(self.note, False, False, 0)
        d.stats([("busy", "사용률"), ("vram", "전용 GPU 메모리"), ("temp", "GPU 온도")],
                [("name", "이름"), ("driver", "드라이버"), ("slot", "PCI 위치")])
        self.widget = d.box
        self.widget.show_all()

    def update(self, dev):
        d = self.d
        _set(d.right, dev["name"])
        sleep = dev.get("sleep")
        d.set("busy", "절전 중" if sleep else (f"{dev['busy']:.0f}%" if dev.get("busy") is not None else "—"))
        tot = dev.get("vram_total")
        if tot:
            self.vram.maxv = float(tot)
            _set(self.mem_cap, D.fmt_gb(tot))
            d.set("vram", f"{D.fmt_gb(dev.get('vram_used') or 0)[:-2]}/{D.fmt_gb(tot)}")
        else:
            d.set("vram", "—")
        for w in (self.vram, self.vram_foot, self.mem_cap.get_parent()):
            w.set_visible(bool(tot))
        d.set("temp", f"{dev['temp']:.0f} °C" if dev.get("temp") is not None else "—")
        d.set("name", dev["name"])
        d.set("driver", dev.get("driver") or "—")
        d.set("slot", dev["id"])
        if sleep:
            _set(self.note, "GPU 가 절전 상태라 값을 읽지 않습니다 (읽으면 GPU 가 깨어납니다).")
        elif dev.get("busy") is None:
            _set(self.note, "이 GPU 드라이버는 사용률을 알려 주지 않습니다.")
        else:
            _set(self.note, "")
        self.note.set_visible(bool(self.note.get_text()))
        self.busy.queue_draw()
        self.vram.queue_draw()


# ── 성능 탭 ──────────────────────────────────────────────────
class PerfPage:
    id = "performance"
    title = "성능"
    searchable = False
    wants_procs = False

    def __init__(self, win):
        self.win = win
        self.now = 0.0
        self.gap = 5.0
        self.hist = collections.defaultdict(Series)
        self.devs = []                           # [(열쇠, 종류, 자료)]
        self.rows = {}                           # 열쇠 → 왼쪽 목록 행
        self.views = {}                          # 열쇠 → 세부 화면
        self.current = "cpu"

        root = Gtk.Box(spacing=0)
        left = Gtk.ScrolledWindow()
        left.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left.set_size_request(270, -1)
        left.get_style_context().add_class("perf-side")
        self.list = Gtk.ListBox()
        self.list.get_style_context().add_class("perf-list")
        self.list.connect("row-selected", self._on_row)
        left.add(self.list)
        root.pack_start(left, False, False, 0)
        self.left = left
        rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # 좁을 때 — 왼쪽 목록 대신 고르는 칸 (윈도우 작업 관리자를 좁혔을 때처럼 상세가 넓게)
        self.picker = Gtk.ComboBoxText()
        self.picker.get_style_context().add_class("perf-picker")
        self.picker.set_halign(Gtk.Align.START)
        self.picker.set_margin_start(12)
        self.picker.set_margin_top(4)
        self.picker.set_no_show_all(True)
        self._picking = False
        self.picker.connect("changed", self._on_pick)
        rbox.pack_start(self.picker, False, False, 0)
        right = Gtk.ScrolledWindow()
        # 가로로도 밀릴 수 있게 — 상세 화면이 창의 최소 폭을 잡지 않는다
        right.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.stack = Gtk.Stack()
        self.stack.get_style_context().add_class("perf-main")
        right.add(self.stack)
        rbox.pack_start(right, True, True, 0)
        root.pack_start(rbox, True, True, 0)
        root.connect("size-allocate", self._on_alloc)
        self._narrow_src = 0
        self.widget = root

        self.actions = Gtk.Box(spacing=8)
        self.actions.pack_start(win.new_task_button(), False, False, 0)

    def series(self, key):
        return self.hist[key]

    def set_interval(self, iv):
        self.gap = max(5.0, 3 * (iv or 1.0))

    # ── 기록 (탭이 안 보여도) ──
    def record(self, snap):
        t = self.now = snap["t"]
        h = self.hist
        h["cpu"].add(t, snap["cpu"])
        for i, v in enumerate(snap["percpu"]):
            h[f"cpu/{i}"].add(t, v)
        h["mem"].add(t, snap["mem"]["used"])
        for dv in snap["disks"]:
            h[f"disk/{dv['id']}/busy"].add(t, dv["busy"])
            h[f"disk/{dv['id']}/rd"].add(t, dv["rd"])
            h[f"disk/{dv['id']}/wr"].add(t, dv["wr"])
        for n in snap["nets"]:
            h[f"net/{n['id']}/rx"].add(t, n["rx"])
            h[f"net/{n['id']}/tx"].add(t, n["tx"])
        for g in snap["gpus"]:
            if not g.get("sleep"):
                h[f"gpu/{g['id']}/busy"].add(t, g.get("busy"))
                h[f"gpu/{g['id']}/vram"].add(t, g.get("vram_used"))

    # ── 화면 ──
    def refresh(self, snap, _clients=None):
        devs = [("cpu", "cpu", snap), ("mem", "mem", snap["mem"])]
        devs += [(f"disk:{d['id']}", "disk", d) for d in snap["disks"]]
        devs += [(f"net:{n['id']}", "net", n) for n in snap["nets"]]
        devs += [(f"gpu:{g['id']}", "gpu", dict(g, index=i)) for i, g in enumerate(snap["gpus"])]
        changed = [k for k, _t, _d in devs] != [k for k, _t, _d in self.devs]
        self.devs = devs
        if changed:
            self._rebuild_list(devs)
        mem = snap["mem"]
        for key, kind, dv in devs:
            row = self.rows[key]
            if kind == "cpu":
                freq = snap.get("freq") or ((snap["cpuinfo"].get("mhz") or 0) / 1000) or None
                _set(row.value, f"{snap['cpu']:.0f}%" + (f"  {freq:.2f}GHz" if freq else ""))
            elif kind == "mem":
                row.spark.maxv = float(mem["total"] or 1)
                _set(row.value, f"{D.fmt_gb(mem['used'])[:-2]}/{D.fmt_gb(mem['total'])} "
                                f"({100 * mem['used'] / (mem['total'] or 1):.0f}%)")
            elif kind == "disk":
                _set(row.title, disk_title(dv))
                _set(row.value, f"{dv['busy']:.0f}%")
            elif kind == "net":
                peak = max(self.hist[f"net/{dv['id']}/rx"].peak(self.now), self.hist[f"net/{dv['id']}/tx"].peak(self.now))
                row.spark.maxv = nice_max(peak * 1.1, 100_000)
                tx, rx, unit = bits_pair(dv["tx"], dv["rx"])
                _set(row.value, f"보내기: {tx} 받기: {rx} {unit}" if dv["up"] else "연결 안 됨")
            elif kind == "gpu":
                _set(row.sub, dv["name"])
                if dv.get("sleep"):
                    txt = "절전 중"
                elif dv.get("busy") is not None:
                    txt = f"{dv['busy']:.0f}%" + (f" ({dv['temp']:.0f} °C)" if dv.get("temp") is not None else "")
                else:
                    txt = ""
                _set(row.value, txt)
            row.spark.queue_draw()
        self._show(self.current, snap)

    def _rebuild_list(self, devs):
        keep = self.current
        for r in self.list.get_children():
            self.list.remove(r)
        self.rows = {}
        for key, kind, dv in devs:
            if kind == "cpu":
                title, sub, ser, maxv = "CPU", "", [(self.hist["cpu"], "fill")], 100.0
            elif kind == "mem":
                title, sub, ser, maxv = "메모리", "", [(self.hist["mem"], "fill")], float(dv["total"] or 1)
            elif kind == "disk":
                title, sub = disk_title(dv), dv["kind"]
                ser, maxv = [(self.hist[f"disk/{dv['id']}/busy"], "fill")], 100.0
            elif kind == "net":
                title, sub = dv["kind"], dv["id"]
                ser, maxv = [(self.hist[f"net/{dv['id']}/rx"], "fill"), (self.hist[f"net/{dv['id']}/tx"], "dash")], 1.0
            else:
                title, sub = f"GPU {dv['index']}", dv["name"]
                ser, maxv = [(self.hist[f"gpu/{dv['id']}/busy"], "fill")], 100.0
            r = Gtk.ListBoxRow()
            r.key = key
            h = Gtk.Box(spacing=12)
            r.spark = Graph(self, ser, maxv, grid=False, height=46, lw=1.1)
            r.spark.set_size_request(72, 46)
            r.spark.set_valign(Gtk.Align.CENTER)
            h.pack_start(r.spark, False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            v.set_valign(Gtk.Align.CENTER)
            r.title = _label(title, "perf-title", 0.0, True)
            r.sub = _label(sub, "perf-sub", 0.0, True)
            r.value = _label("", "perf-sub", 0.0, True)
            v.pack_start(r.title, False, False, 0)
            if sub:
                v.pack_start(r.sub, False, False, 0)
            v.pack_start(r.value, False, False, 0)
            h.pack_start(v, True, True, 0)
            r.add(h)
            r.show_all()
            self.list.add(r)
            self.rows[key] = r
        # 없어진 장치(뽑은 USB 디스크)의 세부 화면은 버린다
        for key in [k for k in self.views if k not in self.rows]:
            self.stack.remove(self.views.pop(key).widget)
        if keep not in self.rows:
            keep = "cpu"
        self.current = keep
        self._picking = True
        self.picker.remove_all()
        for r in self.list.get_children():
            t = r.title.get_text()
            s = r.sub.get_text() if r.sub.get_parent() is not None else ""
            self.picker.append(r.key, f"{t} ({s})" if s and s != t else t)
        self.picker.set_active_id(keep)
        self._picking = False
        self.list.select_row(self.rows[keep])

    def _on_alloc(self, _w, a):
        """좁으면 왼쪽 목록을 숨기고 위에 고르는 칸 (넓어지면 되돌린다 — 경계에서 깜박이지 않게 틈을 둔다)"""
        want = None
        if self.left.get_visible() and a.width < 620:
            want = True
        elif not self.left.get_visible() and a.width >= 680:
            want = False
        if want is not None and not self._narrow_src:
            def go():
                self._narrow_src = 0
                self._set_narrow(want)
                return False
            self._narrow_src = GLib.idle_add(go)

    def _set_narrow(self, narrow):
        self.narrow = narrow
        self.left.set_visible(not narrow)
        self.picker.set_visible(narrow)
        ctx = self.widget.get_style_context()
        (ctx.add_class if narrow else ctx.remove_class)("perf-narrow")
        for v in self.views.values():
            self._orient(v)

    def _orient(self, view):
        area = getattr(getattr(view, "d", None), "stats_area", None)
        if area is not None:
            narrow = getattr(self, "narrow", False)
            area.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
            area.set_spacing(16 if narrow else 48)

    def _on_pick(self, cb):
        key = cb.get_active_id()
        if self._picking or not key or key not in self.rows:
            return
        if self.list.get_selected_row() is not self.rows[key]:
            self.list.select_row(self.rows[key])

    def _on_row(self, _lb, r):
        if r is None:
            return
        self.current = r.key
        if self.picker.get_active_id() != r.key:
            self._picking = True
            self.picker.set_active_id(r.key)
            self._picking = False
        if self.win.snap is not None and self.devs:
            self._show(r.key, self.win.snap)

    def _show(self, key, snap):
        dv = next((d for k, _t, d in self.devs if k == key), None)
        kind = next((t for k, t, _d in self.devs if k == key), None)
        if kind is None:
            return
        view = self.views.get(key)
        if view is None:
            view = {"cpu": lambda: CpuView(self, snap["cpuinfo"]), "mem": lambda: MemView(self, snap["mem"]),
                    "disk": lambda: DiskView(self, dv), "net": lambda: NetView(self, dv),
                    "gpu": lambda: GpuView(self, dv)}[kind]()
            self.views[key] = view
            self._orient(view)
            self.stack.add_named(view.widget, key)
        view.update(snap if kind in ("cpu", "mem") else dv)
        if self.stack.get_visible_child_name() != key:
            self.stack.set_visible_child_name(key)
