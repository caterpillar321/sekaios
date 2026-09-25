"""사진 — 그림 칸. 창 맞춤·확대·이동·회전, 움직이는 그림(GIF·WebP) 재생.

좌표는 모두 장치 픽셀 (HiDPI 에서도 100% = 그림 한 화소가 화면 한 화소 — 윈도우 사진 앱처럼).
  zoom      그림 한 화소 → 화면 화소 수
  ox, oy    돌려 보인 그림의 왼쪽 위가 칸의 어디에 있는지
  rot       보기 회전 (시계 방향 90° 단위)

빠르게 그리기: 원본을 그때그때 줄이지 않는다.
  base   창 맞춤 크기로 미리 그려 둔 그림 전체 (읽을 때 작업 스레드가 만든다 — 넘겨 보기가 곧바로)
  hq     지금 배율로 보이는 부분(+여유)만 고품질로 그린 것 (작업 스레드, 가장 최근 요청만)
배율을 바꾸는 동안에는 가진 것(base·옛 hq)을 cairo 로 늘여 그리고, 멈추면(40ms) 새 hq 가 와서 선명해진다.
그림은 돌리지 않은 채로 두고 그릴 때 cairo 변환으로 돌린다 (회전이 곧바로).
"""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import imageio  # noqa: E402
from .imageio import Cancelled, fit_zoom, scaled_dims  # noqa: E402

MAX_ZOOM = 32.0                 # 3200%
# Ctrl+± · 확대/축소 단추가 밟는 배율
LADDER = [0.02, 0.03, 0.05, 0.08, 0.1, 0.125, 0.167, 0.2, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0,
          3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 24.0, 32.0]
WHOLE_MAX = 12_000_000          # 배율을 바꾼 그림 전체가 이보다 작으면 통째로 그려 둔다 (이동할 때 다시 그리지 않게)
MARGIN = 384                    # 부분만 그릴 때 보이는 곳 둘레에 더 그려 둘 여유 (장치 픽셀)
HQ_DELAY = 40                   # 배율·위치가 멈추고 이만큼 뒤에 고품질로 다시 그린다 (ms)


def _rot_matrix(r, UW, UH):
    """돌리지 않은 그림 좌표 (UW×UH) → 시계 방향 r×90° 돌린 그림 좌표"""
    if r == 1:
        return cairo.Matrix(0, 1, -1, 0, UH, 0)
    if r == 2:
        return cairo.Matrix(-1, 0, 0, -1, UW, UH)
    if r == 3:
        return cairo.Matrix(0, -1, 1, 0, 0, UW)
    return cairo.Matrix()


def _unrotate_rect(r, UW, UH, x0, y0, x1, y1):
    """돌린 그림 위의 사각형 → 돌리지 않은 그림 위의 사각형 (x0, y0, x1, y1)"""
    if r == 1:
        return y0, UH - x1, y1, UH - x0
    if r == 2:
        return UW - x1, UH - y1, UW - x0, UH - y0
    if r == 3:
        return UW - y1, x0, UW - y0, x1
    return x0, y0, x1, y1


class Canvas(Gtk.DrawingArea):
    """window 가 넣는 콜백 (없어도 됨):
        on_zoom_changed()          배율이 바뀌었다 (도구 모음의 % 글자)
        on_need_full(entry)        줄여 읽은 큰 그림을 맞춤보다 크게 본다 — 원본 해상도를 읽어 달라
        on_activity()              마우스가 움직였다 (넘기기 화살표·전체 화면 도구 모음)
        on_press(event) -> bool    눌렀다 (True 면 여기서 끝 — 슬라이드 쇼를 끝낼 때)
        on_context(event)          오른쪽 단추
        on_nav(delta)              마우스 뒤로·앞으로 단추"""

    def __init__(self, render_pool):
        super().__init__()
        self.pool = render_pool
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK |
                        Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK |
                        Gdk.EventMask.TOUCHPAD_GESTURE_MASK)
        self.entry = None
        self.rot = 0
        self.zoom = 1.0
        self.fit = True
        self.ox = self.oy = 0.0
        self.bg = (0.07, 0.07, 0.08)
        self.hide_cursor = False
        self._vp = (1, 1)                   # 작업 스레드가 읽는 칸 크기 (장치 픽셀)
        self._hq = None                     # (entry id, ver, zoom, UW, UH, (x, y, w, h), 그림)
        self._hq_src = 0
        self._hq_job = None
        self._base = None                   # (entry id, zoom, (x, y, w, h), 그림)
        self._anim_iter = None
        self._anim_src = 0
        self._drag = None
        self._cursor_name = None
        self._full_asked = None
        self._pinch = None
        for name in ("on_zoom_changed", "on_need_full", "on_activity", "on_press", "on_context", "on_nav"):
            setattr(self, name, None)

        self.connect("size-allocate", self._on_alloc)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("scroll-event", self._on_scroll)
        self.connect("leave-notify-event", lambda *_: self._set_cursor(None) if not self._drag else None)
        self.connect("unrealize", lambda *_: self._stop_anim())
        # 터치패드 벌리기·오므리기
        self._zoom_gesture = Gtk.GestureZoom.new(self)
        self._zoom_gesture.connect("begin", self._pinch_begin)
        self._zoom_gesture.connect("scale-changed", self._pinch_scale)
        self._zoom_gesture.connect("end", lambda *_: setattr(self, "_pinch", None))

    # ── 크기 ────────────────────────────────────────────────
    def viewport(self):
        """칸 크기 (장치 픽셀) — 작업 스레드에서도 부른다 (튜플 읽기뿐)"""
        return self._vp

    def _sf(self):
        return max(1, self.get_scale_factor())

    def _dims(self):
        """돌린 그림의 원본 크기 (RW, RH)"""
        e = self.entry
        if e is None or not e.W:
            return 1, 1
        return (e.H, e.W) if self.rot % 2 else (e.W, e.H)

    def fit_zoom(self):
        RW, RH = self._dims()
        vw, vh = self._vp
        return fit_zoom(RW, RH, vw, vh, bool(self.entry and self.entry.vector))

    def _scaled(self, z=None):
        """지금 배율에서 돌리지 않은 그림 크기 (UW, UH) 와 돌린 크기 (DW, DH) — 정수"""
        e = self.entry
        UW, UH = scaled_dims(e.W, e.H, self.zoom if z is None else z)
        return (UW, UH), ((UH, UW) if self.rot % 2 else (UW, UH))

    def _on_alloc(self, _w, alloc):
        sf = self._sf()
        old = self._vp
        self._vp = (max(1, alloc.width * sf), max(1, alloc.height * sf))
        if old == self._vp or self.entry is None:
            return
        if self.fit:
            self._apply_fit()
        else:
            # 가운데를 그대로 두고 칸만 커지거나 작아진다
            self.ox += (self._vp[0] - old[0]) / 2
            self.oy += (self._vp[1] - old[1]) / 2
            self._clamp()
        self._changed(zoom=False)

    # ── 그림 바꾸기 ─────────────────────────────────────────
    def set_entry(self, entry, rot=0):
        same = self.entry is entry
        self._stop_anim()
        self.entry = entry
        self.rot = rot % 4
        self._full_asked = None
        if not same:
            self._hq = None
            self._base = None
        if entry is not None and entry.ok:
            if entry.base is not None:
                z, rect, surf = entry.base
                self._base = (entry.id, z, rect, surf)
            if entry.anim is not None:
                self._start_anim()
        self.fit = True
        self._apply_fit()
        self._changed()

    def entry_updated(self):
        """같은 그림의 src 가 바뀌었다 (원본 해상도가 왔다) — 새로 그린다"""
        self._hq = None
        self._request_hq()
        self.queue_draw()

    def set_rotation(self, rot):
        self.rot = rot % 4
        self.fit = True                     # 돌리면 창에 맞춰 다시 (윈도우 사진 앱처럼)
        self._apply_fit()
        self._changed()

    def set_background(self, rgb):
        self.bg = rgb
        self.queue_draw()

    # ── 배율 ────────────────────────────────────────────────
    def _apply_fit(self):
        if self.entry is None or not self.entry.ok:
            return
        self.zoom = self.fit_zoom()
        self._center()

    def _center(self):
        _u, (DW, DH) = self._scaled()
        vw, vh = self._vp
        self.ox = (vw - DW) / 2
        self.oy = (vh - DH) / 2

    def _clamp(self):
        if self.entry is None or not self.entry.ok:
            return
        _u, (DW, DH) = self._scaled()
        vw, vh = self._vp
        self.ox = (vw - DW) / 2 if DW <= vw else min(0, max(vw - DW, self.ox))
        self.oy = (vh - DH) / 2 if DH <= vh else min(0, max(vh - DH, self.oy))

    def pannable(self):
        if self.entry is None or not self.entry.ok:
            return False
        _u, (DW, DH) = self._scaled()
        return DW > self._vp[0] + 0.5 or DH > self._vp[1] + 0.5

    def set_zoom(self, z, anchor=None, allow_below_fit=False):
        """anchor = (x, y) 장치 픽셀 — 그 점 아래의 그림 자리가 그대로 남게 (마우스 휠은 커서 둘레로)"""
        e = self.entry
        if e is None or not e.ok:
            return
        fz = self.fit_zoom()
        lo = min(fz, self.zoom) if not allow_below_fit else 0.01
        z = max(lo, min(MAX_ZOOM, z))
        if not allow_below_fit and z <= fz * 1.0005:
            self.zoom_fit()
            return
        if anchor is None:
            anchor = (self._vp[0] / 2, self._vp[1] / 2)
        ax, ay = anchor
        # 커서 아래의 그림 자리 (배율 없는 좌표)
        px = (ax - self.ox) / self.zoom
        py = (ay - self.oy) / self.zoom
        self.zoom = z
        self.fit = False
        self.ox = ax - px * z
        self.oy = ay - py * z
        self._clamp()
        self._changed()

    def zoom_fit(self):
        self.fit = True
        self._apply_fit()
        self._changed()

    def zoom_actual(self, anchor=None):
        self.set_zoom(1.0, anchor, allow_below_fit=True)

    def zoom_step(self, up, anchor=None):
        z = self.zoom
        fz = self.fit_zoom()
        if up:
            nxt = next((s for s in LADDER if s > z * 1.001), MAX_ZOOM)
            if z < fz * 0.999 < nxt:       # 맞춤보다 작게 보던 중이면 맞춤에 먼저 선다
                self.zoom_fit()
                return
            self.set_zoom(nxt, anchor, allow_below_fit=True)
        else:
            nxt = next((s for s in reversed(LADDER) if s < z * 0.999), LADDER[0])
            if nxt < fz * 0.999 < z * 0.999:
                self.zoom_fit()
                return
            self.set_zoom(nxt, anchor, allow_below_fit=z < fz * 0.999)

    def toggle_zoom(self, anchor=None):
        """두 번 누르기 — 맞춤 ↔ 100% (작은 그림은 맞춤이 100% 라 200%)"""
        if self.fit:
            target = 1.0 if self.zoom < 0.999 else self.zoom * 2
            self.set_zoom(target, anchor, allow_below_fit=True)
        else:
            self.zoom_fit()

    # ── 바뀜 알리기 ─────────────────────────────────────────
    def _changed(self, zoom=True):
        self._request_hq()
        self._check_full()
        self._update_cursor_state()
        self.queue_draw()
        if zoom and self.on_zoom_changed:
            self.on_zoom_changed()

    def _check_full(self):
        e = self.entry
        if e is None or not e.reduced or e.src is not e.small or self._full_asked == e.id:
            return
        if self.zoom > e.k * 1.02 and self.on_need_full:
            self._full_asked = e.id
            self.on_need_full(e)

    # ── 고품질 부분 그리기 ──────────────────────────────────
    def _needed(self):
        """지금 보이는 곳을 덮으려면 필요한 (배율, UW, UH, 돌리지 않은 사각형) — 그릴 게 없으면 None"""
        e = self.entry
        if e is None or not e.ok or e.anim is not None:
            return None
        (UW, UH), (DW, DH) = self._scaled()
        vw, vh = self._vp
        ox, oy = round(self.ox), round(self.oy)
        x0, y0 = max(0, -ox), max(0, -oy)
        x1, y1 = min(DW, vw - ox), min(DH, vh - oy)
        if x1 <= x0 or y1 <= y0:
            return None
        return UW, UH, _unrotate_rect(self.rot, UW, UH, x0, y0, x1, y1)

    @staticmethod
    def _covers(rect, need):
        x, y, w, h = rect
        a0, b0, a1, b1 = need
        return x <= a0 and y <= b0 and x + w >= a1 and y + h >= b1

    def _exact(self):
        """지금 배율과 똑같이 그려 둔 것 중 보이는 곳을 다 덮는 것 — (zoom, rect, 그림) 또는 None"""
        e = self.entry
        n = self._needed()
        if n is None:
            return None
        UW, UH, need = n
        z = self.zoom
        b = self._base
        if b is not None and b[0] == e.id and abs(b[1] - z) < 1e-9 and self._covers(b[2], need):
            return b[1:]
        h = self._hq
        if h is not None and h[0] == e.id and h[1] == e.ver and abs(h[2] - z) < 1e-9 and self._covers(h[5], need):
            return h[2], h[5], h[6]
        return None

    def _request_hq(self):
        if self._hq_src:
            GLib.source_remove(self._hq_src)
            self._hq_src = 0
        if self._needed() is not None and self._exact() is None:
            self._hq_src = GLib.timeout_add(HQ_DELAY, self._render_hq)

    def _render_hq(self):
        self._hq_src = 0
        n = self._needed()
        if n is None or self._exact() is not None:
            return False
        UW, UH, (a0, b0, a1, b1) = n
        e = self.entry
        if UW * UH <= WHOLE_MAX:
            rect = (0, 0, UW, UH)
        else:
            x0, y0 = max(0, a0 - MARGIN), max(0, b0 - MARGIN)
            x1, y1 = min(UW, a1 + MARGIN), min(UH, b1 + MARGIN)
            rect = (int(x0), int(y0), int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0)))
        if self._hq_job is not None:
            self._hq_job.cancelled = True     # 아직 시작 안 했으면 건너뛴다 (가장 최근 것만)
        src, eid, ver, z = e.src, e.id, e.ver, self.zoom

        def work(job):
            if job.cancelled:
                raise Cancelled()
            return imageio.render_region(src, UW, UH, rect)

        def done(surf, exc):
            if self._hq_job is job:
                self._hq_job = None
            if exc is not None:
                if not isinstance(exc, (Cancelled, MemoryError)):
                    print("[sekai-photos] 그리기 실패:", exc, flush=True)
                return
            cur = self.entry
            if cur is None or cur.id != eid or cur.ver != ver:
                return
            self._hq = (eid, ver, z, UW, UH, rect, surf)
            self.queue_draw()
            if abs(self.zoom - z) > 1e-9 or self._exact() is None:
                self._request_hq()             # 그리는 동안 배율·위치가 또 바뀌었다
        job = self._hq_job = self.pool.submit(work, done, prio=0, tag="hq")
        return False

    # ── 그리기 ──────────────────────────────────────────────
    def do_draw(self, cr):
        sf = self._sf()
        cr.set_source_rgb(*self.bg)
        cr.paint()
        e = self.entry
        if e is None or not e.ok:
            return False
        cr.save()
        cr.scale(1 / sf, 1 / sf)
        (UW, UH), (DW, DH) = self._scaled()
        ox, oy = round(self.ox), round(self.oy)
        cr.rectangle(ox, oy, DW, DH)
        cr.clip()
        if self._anim_iter is not None:
            self._draw_frame(cr, ox, oy, UW, UH)
        else:
            ex = self._exact()
            if ex is not None:
                self._paint(cr, ex[2], ex[0], ex[1], ox, oy, UW, UH, pad=False)
            else:
                b = self._base
                if b is not None and b[0] == e.id:
                    self._paint(cr, b[3], b[1], b[2], ox, oy, UW, UH, pad=True)
                elif e.src is not None and self._hq is None:
                    self._paint_pixbuf(cr, e.src, ox, oy, UW, UH)
                h = self._hq
                if h is not None and h[0] == e.id and h[1] == e.ver:
                    self._paint(cr, h[6], h[2], h[5], ox, oy, UW, UH, pad=False)
        cr.restore()
        return False

    def _paint(self, cr, surf, z_src, rect, ox, oy, UW, UH, pad):
        """zoom z_src 로 그려 둔 그림(돌리지 않은 좌표의 rect 자리)을 지금 배율·회전으로"""
        s = self.zoom / z_src
        cr.save()
        cr.translate(ox, oy)
        cr.transform(_rot_matrix(self.rot, UW, UH))
        cr.scale(s, s)
        x, y, w, h = rect
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.set_source_surface(surf, x, y)
        pat = cr.get_source()
        exact = abs(s - 1) < 1e-9
        pat.set_filter(cairo.FILTER_NEAREST if exact else cairo.FILTER_BILINEAR)
        if pad:
            pat.set_extend(cairo.EXTEND_PAD)
        cr.paint()
        cr.restore()

    def _paint_pixbuf(self, cr, pb, ox, oy, UW, UH):
        """그려 둔 게 아직 하나도 없을 때 (창 크기를 모를 때 읽은 그림) — 원본을 바로 늘려 그린다"""
        cr.save()
        cr.translate(ox, oy)
        cr.transform(_rot_matrix(self.rot, UW, UH))
        cr.scale(UW / pb.get_width(), UH / pb.get_height())
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint()
        cr.restore()

    def _draw_frame(self, cr, ox, oy, UW, UH):
        pb = self._anim_iter.get_pixbuf()
        if pb is None:
            return
        s = UW / pb.get_width()
        cr.translate(ox, oy)
        cr.transform(_rot_matrix(self.rot, UW, UH))
        cr.scale(s, UH / pb.get_height())
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        pat = cr.get_source()
        pat.set_filter(cairo.FILTER_GOOD if s < 1 else cairo.FILTER_BILINEAR if s < 4 else cairo.FILTER_NEAREST)
        pat.set_extend(cairo.EXTEND_PAD)
        cr.paint()

    # ── 움직이는 그림 ───────────────────────────────────────
    def _start_anim(self):
        try:
            self._anim_iter = self.entry.anim.get_iter(None)
        except Exception as ex:
            print("[sekai-photos] 움직이는 그림을 재생하지 못함:", ex, flush=True)
            self._anim_iter = None
            return
        self._schedule_frame()

    def _schedule_frame(self):
        d = self._anim_iter.get_delay_time()
        if d >= 0:
            self._anim_src = GLib.timeout_add(max(20, d), self._anim_tick)

    def _anim_tick(self):
        self._anim_src = 0
        it = self._anim_iter
        if it is None:
            return False
        if it.advance(None):
            self.queue_draw()
        self._schedule_frame()
        return False

    def _stop_anim(self):
        if self._anim_src:
            GLib.source_remove(self._anim_src)
            self._anim_src = 0
        self._anim_iter = None

    # ── 마우스 ──────────────────────────────────────────────
    def _set_cursor(self, name):
        if self.hide_cursor:
            name = "none"
        if name == self._cursor_name:
            return
        self._cursor_name = name
        win = self.get_window()
        if win is None:
            return
        cur = None
        if name:
            disp = self.get_display()
            cur = Gdk.Cursor.new_from_name(disp, name)
            if cur is None and name == "none":
                cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.BLANK_CURSOR)
        win.set_cursor(cur)

    def _update_cursor_state(self):
        if self._drag is not None:
            self._set_cursor("grabbing")
        elif self.pannable():
            self._set_cursor("grab")
        else:
            self._set_cursor(None)

    def set_hide_cursor(self, on):
        self.hide_cursor = on
        self._cursor_name = "?"
        self._update_cursor_state()

    def _dev(self, ev):
        sf = self._sf()
        return ev.x * sf, ev.y * sf

    def _on_press(self, _w, ev):
        self.grab_focus()
        if self.on_press and self.on_press(ev):
            return True
        if ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1:
            self._drag = None
            self.toggle_zoom(self._dev(ev))
            return True
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button == 1:
            if self.pannable():
                self._drag = (ev.x, ev.y, self.ox, self.oy)
                self._set_cursor("grabbing")
            return True
        if ev.button == 3:
            if self.on_context:
                self.on_context(ev)
            return True
        if ev.button in (8, 9) and self.on_nav:
            self.on_nav(-1 if ev.button == 8 else 1)
            return True
        return False

    def _on_release(self, _w, ev):
        if ev.button == 1 and self._drag is not None:
            self._drag = None
            self._update_cursor_state()
        return False

    def _on_motion(self, _w, ev):
        if self._drag is not None:
            sx, sy, ox, oy = self._drag
            sf = self._sf()
            self.ox = ox + (ev.x - sx) * sf
            self.oy = oy + (ev.y - sy) * sf
            self._clamp()
            self._request_hq()
            self.queue_draw()
            return True
        self._update_cursor_state()
        if self.on_activity:
            self.on_activity()
        return False

    def _on_scroll(self, _w, ev):
        """휠 = 커서 둘레로 확대/축소 (윈도우 11 사진 앱의 기본값). Ctrl+휠도 같다. 가로 휠은 옆으로 이동"""
        if self.entry is None or not self.entry.ok:
            return False
        dx = dy = 0.0
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok, dx, dy = ev.get_scroll_deltas()
        elif ev.direction == Gdk.ScrollDirection.UP:
            dy = -1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            dy = 1
        elif ev.direction == Gdk.ScrollDirection.LEFT:
            dx = -1
        elif ev.direction == Gdk.ScrollDirection.RIGHT:
            dx = 1
        if abs(dx) > abs(dy) and not (ev.state & Gdk.ModifierType.CONTROL_MASK):
            if self.pannable():
                self.ox -= dx * 60 * self._sf()
                self._clamp()
                self._request_hq()
                self.queue_draw()
            return True
        if dy == 0:
            return True
        dy = max(-3.0, min(3.0, dy))
        self.set_zoom(self.zoom * (1.2 ** (-dy)), self._dev(ev))
        return True

    def _pinch_begin(self, g, _seq):
        self._pinch = self.zoom

    def _pinch_scale(self, g, scale):
        if self._pinch is None:
            return
        ok, x, y = g.get_bounding_box_center()
        sf = self._sf()
        self.set_zoom(self._pinch * scale, (x * sf, y * sf) if ok else None)

    # ── 바깥에서 쓰는 것 ────────────────────────────────────
    def pan(self, dx, dy):
        """확대해 볼 때 키보드로 옮기기 (dx, dy 는 논리 픽셀 — 그림이 움직이는 쪽)"""
        sf = self._sf()
        self.ox += dx * sf
        self.oy += dy * sf
        self._clamp()
        self._changed(zoom=False)

    def percent(self):
        return round(self.zoom * 100)
