"""디스플레이 배치 — 윈도우 설정처럼 모니터 사각형을 끌어 실제 책상 위 배치와 같게 놓는다.

놓으면 가장 가까운 다른 모니터의 가장자리에 붙고(모서리가 가까우면 줄을 맞춘다),
왼쪽 위가 (0, 0) 이 되게 전체를 옮긴다 (Hyprland 는 음수 좌표를 권하지 않는다).
좌표는 Hyprland 와 같은 논리 픽셀 — 배율·회전을 반영한 크기.
"""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402

from sekaishell.layer import GtkLayerShell
from sekaishell.monitors import gdk_for, hypr_monitors

H = 240            # 그림판 높이
PAD = 24           # 그림판 가장자리 여백


def logical(m):
    """Hyprland 모니터 항목 → 논리 크기 (배율·회전 반영)"""
    s = m.get("scale") or 1.0
    w, h = m.get("width", 0) / s, m.get("height", 0) / s
    if int(m.get("transform", 0)) % 2:
        w, h = h, w
    return int(round(w)), int(round(h))


def _overlap(a, b):
    """두 사각형의 안쪽이 겹치는가 (가장자리만 맞닿는 건 괜찮다)"""
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and \
        a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def _clamp(v, lo, hi):
    return max(lo, min(v, hi))


def snap(m, others):
    """m 을 가장 가까운 다른 모니터의 가장자리에 붙인 자리 (x, y). 붙일 곳이 없으면 None."""
    best, best_d = None, None
    for o in others:
        cands = []
        t = 0.06 * max(o["h"], m["h"])                 # 줄 맞추기로 끌어당기는 거리
        for x in (o["x"] - m["w"], o["x"] + o["w"]):   # 왼쪽·오른쪽에 붙이기
            y = _clamp(m["y"], o["y"] - m["h"] + 1, o["y"] + o["h"] - 1)
            for edge in (o["y"], o["y"] + o["h"] - m["h"]):
                if abs(y - edge) < t:
                    y = edge
                    break
            cands.append((x, y))
        t = 0.06 * max(o["w"], m["w"])
        for y in (o["y"] - m["h"], o["y"] + o["h"]):   # 위·아래에 붙이기
            x = _clamp(m["x"], o["x"] - m["w"] + 1, o["x"] + o["w"] - 1)
            for edge in (o["x"], o["x"] + o["w"] - m["w"]):
                if abs(x - edge) < t:
                    x = edge
                    break
            cands.append((x, y))
        for x, y in cands:
            probe = dict(m, x=x, y=y)
            if any(_overlap(probe, p) for p in others):
                continue
            d = math.hypot(x - m["x"], y - m["y"])
            if best_d is None or d < best_d:
                best, best_d = (int(x), int(y)), d
    return best


def normalize(mons):
    """왼쪽 위가 (0, 0) 이 되게"""
    if not mons:
        return
    mx, my = min(m["x"] for m in mons), min(m["y"] for m in mons)
    for m in mons:
        m["x"] -= mx
        m["y"] -= my


def _connected(mons):
    """모든 모니터가 가장자리로 이어져 있는가 (떨어진 섬이 있으면 커서가 못 건너간다)"""
    if len(mons) < 2:
        return True

    def touch(a, b):
        h = (a["x"] + a["w"] == b["x"] or b["x"] + b["w"] == a["x"]) and \
            a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
        v = (a["y"] + a["h"] == b["y"] or b["y"] + b["h"] == a["y"]) and \
            a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
        return h or v
    seen, todo = {0}, [0]
    while todo:
        i = todo.pop()
        for j in range(len(mons)):
            if j not in seen and touch(mons[i], mons[j]):
                seen.add(j)
                todo.append(j)
    return len(seen) == len(mons)


class ArrangeView(Gtk.Box):
    """on_apply({이름: (x, y)}) 는 적용을 눌렀을 때. accent 는 강조색 (#rrggbb)."""

    def __init__(self, hmons, primary, accent, on_apply):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.on_apply = on_apply
        self.primary = primary
        self.accent = Gdk.RGBA()
        if not self.accent.parse(accent or "#39c5bb"):
            self.accent.parse("#39c5bb")
        self.mons = []
        for i, m in enumerate(hmons, 1):
            w, h = logical(m)
            self.mons.append({"name": m.get("name"), "num": i, "x": int(m.get("x", 0)),
                              "y": int(m.get("y", 0)), "w": w, "h": h, "hm": m})
        self.orig = {m["name"]: (m["x"], m["y"]) for m in self.mons}
        self.drag = None          # (모니터, 누른 곳 논리 좌표와의 차, 보기 변환)
        self.selected = None
        self.view = None

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(-1, H)
        self.area.get_style_context().add_class("arrange-area")
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("button-release-event", self._release)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("arrange-frame")
        frame.add(self.area)
        self.pack_start(frame, False, False, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.hint = Gtk.Label(label="모니터를 끌어서 실제 책상 위 배치와 같게 놓으세요.", xalign=0)
        self.hint.set_line_wrap(True)
        self.hint.get_style_context().add_class("dim-label")
        bar.pack_start(self.hint, True, True, 0)
        ident = Gtk.Button(label="식별")
        ident.set_tooltip_text("각 화면에 번호를 잠깐 띄웁니다")
        ident.connect("clicked", lambda *_: self.identify())
        bar.pack_end(ident, False, False, 0)
        self.apply_btn = Gtk.Button(label="적용")
        self.apply_btn.get_style_context().add_class("suggested-action")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", lambda *_: self._apply())
        bar.pack_end(self.apply_btn, False, False, 0)
        self.pack_start(bar, False, False, 0)

    # ── 보기 변환 (논리 좌표 ↔ 그림판) ──
    def _make_view(self):
        W, Hh = self.area.get_allocated_width(), self.area.get_allocated_height()
        x0 = min(m["x"] for m in self.mons)
        y0 = min(m["y"] for m in self.mons)
        x1 = max(m["x"] + m["w"] for m in self.mons)
        y1 = max(m["y"] + m["h"] for m in self.mons)
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        s = min((W - 2 * PAD) / bw, (Hh - 2 * PAD) / bh)
        ox = (W - bw * s) / 2 - x0 * s
        oy = (Hh - bh * s) / 2 - y0 * s
        return s, ox, oy

    def _hit(self, px, py):
        s, ox, oy = self.view or self._make_view()
        for m in reversed(self.mons):
            x, y = ox + m["x"] * s, oy + m["y"] * s
            if x <= px <= x + m["w"] * s and y <= py <= y + m["h"] * s:
                return m
        return None

    # ── 그리기 ──
    def _draw(self, w, cr):
        if not self.mons:
            return False
        view = self.view or self._make_view()
        s, ox, oy = view
        fg = w.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        a = self.accent
        order = sorted(self.mons, key=lambda m: m is (self.drag or (None,))[0])   # 끄는 것을 맨 위에
        for m in order:
            x, y = ox + m["x"] * s + 2, oy + m["y"] * s + 2
            ww, hh = m["w"] * s - 4, m["h"] * s - 4
            sel = m is self.selected or (self.drag and m is self.drag[0])
            r = 6
            cr.new_sub_path()
            cr.arc(x + ww - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + ww - r, y + hh - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + hh - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()
            if sel:
                cr.set_source_rgba(a.red, a.green, a.blue, 0.35)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            cr.fill_preserve()
            if sel:
                cr.set_source_rgba(a.red, a.green, a.blue, 1.0)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.set_line_width(2 if sel else 1)
            cr.stroke()

            # 번호 (크게) · 이름
            lay = PangoCairo.create_layout(cr)
            lay.set_font_description(Pango.FontDescription.from_string(
                f"Sans Bold {max(12, min(30, int(hh / 3)))}px"))
            lay.set_text(str(m["num"]), -1)
            tw, th = lay.get_pixel_size()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.95)
            cr.move_to(x + (ww - tw) / 2, y + (hh - th) / 2 - 6)
            PangoCairo.show_layout(cr, lay)
            lay2 = PangoCairo.create_layout(cr)
            lay2.set_font_description(Pango.FontDescription.from_string("Sans 9"))
            sub = m["name"] + ("  ·  주" if m["name"] == self.primary else "")
            lay2.set_text(sub, -1)
            lay2.set_width(int(max(10, ww - 8)) * Pango.SCALE)
            lay2.set_ellipsize(Pango.EllipsizeMode.END)
            lay2.set_alignment(Pango.Alignment.CENTER)
            _tw2, th2 = lay2.get_pixel_size()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            cr.move_to(x + 4, y + (hh + th) / 2 - 4)
            PangoCairo.show_layout(cr, lay2)
        return False

    # ── 끌기 ──
    def _press(self, _w, ev):
        if ev.button != 1:
            return False
        m = self._hit(ev.x, ev.y)
        self.selected = m
        if m is not None and len(self.mons) > 1:
            self.view = self._make_view()          # 끄는 동안 보기를 고정 (그림판이 흔들리지 않게)
            s, ox, oy = self.view
            self.drag = (m, (ev.x - ox) / s - m["x"], (ev.y - oy) / s - m["y"])
        self.area.queue_draw()
        return True

    def _motion(self, _w, ev):
        if not self.drag:
            return False
        m, dx, dy = self.drag
        s, ox, oy = self.view
        m["x"] = int((ev.x - ox) / s - dx)
        m["y"] = int((ev.y - oy) / s - dy)
        self.area.queue_draw()
        return True

    def _release(self, _w, ev):
        if not self.drag or ev.button != 1:
            return False
        m = self.drag[0]
        self.drag = None
        others = [o for o in self.mons if o is not m]
        pos = snap(m, others)
        if pos is None:                            # 붙일 곳이 없으면 원래 자리로
            m["x"], m["y"] = self._last.get(m["name"], self.orig[m["name"]])
        else:
            m["x"], m["y"] = pos
        normalize(self.mons)
        self._last = {o["name"]: (o["x"], o["y"]) for o in self.mons}
        self.view = None
        self._changed()
        self.area.queue_draw()
        return True

    _last = {}

    def _changed(self):
        now = {m["name"]: (m["x"], m["y"]) for m in self.mons}
        ok = _connected(self.mons)
        self.apply_btn.set_sensitive(now != self.orig and ok)
        if not ok:
            self.hint.set_text("모든 모니터가 가장자리로 이어져 있어야 합니다.")
        else:
            self.hint.set_text("모니터를 끌어서 실제 책상 위 배치와 같게 놓으세요."
                               + ("  적용을 눌러야 바뀝니다." if now != self.orig else ""))

    def _apply(self):
        self.on_apply({m["name"]: (m["x"], m["y"]) for m in self.mons})
        self.orig = {m["name"]: (m["x"], m["y"]) for m in self.mons}
        self._changed()

    def set_positions(self, pos):
        """되돌렸을 때 — 실제 위치로 다시"""
        for m in self.mons:
            if m["name"] in pos:
                m["x"], m["y"] = pos[m["name"]]
        self.orig = {m["name"]: (m["x"], m["y"]) for m in self.mons}
        self._last = dict(self.orig)
        self._changed()
        self.area.queue_draw()

    def set_primary(self, name):
        self.primary = name
        self.area.queue_draw()

    # ── 식별 — 각 화면에 큰 번호 ──
    def identify(self):
        wins = []
        now = {h.get("name"): h for h in hypr_monitors()}      # 지금 위치로 (배치를 바꿨을 수 있다)
        for m in self.mons:
            g = gdk_for(now.get(m["name"]))
            if g is None:
                continue
            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.get_style_context().add_class("identify-win")
            vis = Gdk.Screen.get_default().get_rgba_visual()
            if vis:
                win.set_visual(vis)
            GtkLayerShell.init_for_window(win)
            GtkLayerShell.set_namespace(win, "sekai-osd")
            GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_monitor(win, g)
            GtkLayerShell.set_anchor(win, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(win, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_margin(win, GtkLayerShell.Edge.TOP, 40)
            GtkLayerShell.set_margin(win, GtkLayerShell.Edge.LEFT, 40)
            GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.NONE)
            lbl = Gtk.Label(label=str(m["num"]))
            lbl.get_style_context().add_class("identify-num")
            win.add(lbl)
            win.show_all()
            wins.append(win)
        GLib.timeout_add(2500, lambda: [w.destroy() for w in wins] and False)
