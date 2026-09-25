"""layer-shell 호환 층 — Wayland 에선 진짜 GtkLayerShell, X11(기본 화면 모드)에선 흉내.

셸 코드는 `from sekaishell.layer import GtkLayerShell` 로 가져다 쓰면 된다.
함수 이름·열거형이 GtkLayerShell 과 같아서 코드를 고칠 필요가 없다.

X11 에서의 흉내
  · 위치: 고정한 모서리(anchor)와 여백(margin)으로 모니터 안의 좌표를 계산해 옮긴다.
          양쪽 모서리를 다 고정하면 그 방향으로 늘린다.
  · 층:   BACKGROUND → 바탕화면 창(desktop, 항상 아래)
          BOTTOM/TOP → 작업 표시줄 같은 dock (exclusive zone 이 있으면 화면 가장자리 예약)
          OVERLAY   → 항상 위의 도구 창 (팝업·메뉴·알림·로그인 화면)
  · 키보드: EXCLUSIVE/ON_DEMAND 면 뜰 때 초점을 가져온다.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY")) and os.environ.get("XDG_SESSION_TYPE") != "x11"

if WAYLAND:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # noqa: E402,F401
else:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: E402,F401  (창의 get_xid)

    class _Enum:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _State:
        def __init__(self):
            self.anchor = {}
            self.margin = {}
            self.layer = 2
            self.kbd = 0
            self.exclusive = 0
            self.auto_exclusive = False
            self.monitor = None
            self.namespace = ""
            self.screen_hid = 0

    class _X11LayerShell:
        Edge = _Enum(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)
        Layer = _Enum(BACKGROUND=0, BOTTOM=1, TOP=2, OVERLAY=3)
        KeyboardMode = _Enum(NONE=0, EXCLUSIVE=1, ON_DEMAND=2)

        @staticmethod
        def _st(win):
            st = getattr(win, "_sekai_layer", None)
            if st is None:
                st = win._sekai_layer = _State()
            return st

        # ── GtkLayerShell 과 같은 이름의 함수들 ──
        @classmethod
        def is_supported(cls):
            return False

        @classmethod
        def init_for_window(cls, win):
            st = cls._st(win)
            win.set_decorated(False)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.stick()
            win.connect("realize", lambda w: cls._apply_hints(w))
            win.connect("size-allocate", lambda w, _a: GLib.idle_add(cls._place, w))
            win.connect("map", lambda w: (cls._place(w), cls._focus(w)))
            scr = Gdk.Screen.get_default()
            if not st.screen_hid:
                st.screen_hid = scr.connect("monitors-changed", lambda *_: cls._place(win))

                # Screen 은 프로세스 내내 산다 — 창이 없어질 때 끊지 않으면 람다가 창을 붙잡아
                #   열 때마다 새로 만들고 destroy 하는 창(다른 모니터의 ClickCatcher 등)이 풀리지 않았다
                def unhook(*_):
                    if st.screen_hid:
                        scr.disconnect(st.screen_hid)
                        st.screen_hid = 0
                win.connect("destroy", unhook)
            return st

        @classmethod
        def set_namespace(cls, win, ns):
            cls._st(win).namespace = ns
            win.set_role(ns)

        @classmethod
        def set_layer(cls, win, layer):
            cls._st(win).layer = layer

        @classmethod
        def set_anchor(cls, win, edge, on):
            cls._st(win).anchor[edge] = bool(on)
            cls._place(win)

        @classmethod
        def set_margin(cls, win, edge, px):
            cls._st(win).margin[edge] = int(px)
            cls._place(win)

        @classmethod
        def set_keyboard_mode(cls, win, mode):
            cls._st(win).kbd = mode
            win.set_accept_focus(mode != cls.KeyboardMode.NONE)
            win.set_focus_on_map(mode != cls.KeyboardMode.NONE)

        @classmethod
        def set_exclusive_zone(cls, win, px):
            cls._st(win).exclusive = int(px)
            cls._apply_strut(win)

        @classmethod
        def auto_exclusive_zone_enable(cls, win):
            cls._st(win).auto_exclusive = True
            cls._apply_strut(win)

        @classmethod
        def set_monitor(cls, win, monitor):
            cls._st(win).monitor = monitor
            cls._place(win)

        # ── 흉내의 속 ──
        @classmethod
        def _apply_hints(cls, win):
            st = cls._st(win)
            L = cls.Layer
            if st.layer == L.BACKGROUND:
                win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
                win.set_keep_below(True)
            elif st.exclusive > 0 or st.auto_exclusive:
                win.set_type_hint(Gdk.WindowTypeHint.DOCK)
                win.set_keep_above(True)
            elif st.layer == L.OVERLAY or st.layer == L.TOP:
                # 알림(토스트)·OSD 처럼 입력을 안 받는 OVERLAY 는 알림 창 — xfwm4 는 알림 창을 늘 맨 위에 둔다.
                # TOP 층(메뉴 바깥 클릭을 받는 전체 화면 창 등)은 알림 창으로 두면 안 된다:
                #   그 위로 메뉴가 못 올라와서 메뉴를 누르면 전부 "바깥 클릭"이 되어 닫힌다.
                #   → 도구 창(항상 위)으로. 같은 층끼리는 나중에 뜬 것이 위라서 메뉴가 그 위에 온다.
                hint = (Gdk.WindowTypeHint.NOTIFICATION
                        if st.kbd == cls.KeyboardMode.NONE and st.layer == L.OVERLAY
                        else Gdk.WindowTypeHint.UTILITY)
                win.set_type_hint(hint)
                win.set_keep_above(True)
            else:
                win.set_type_hint(Gdk.WindowTypeHint.DOCK)
            cls._apply_strut(win)

        @classmethod
        def _geom(cls, win):
            st = cls._st(win)
            disp = Gdk.Display.get_default()
            mon = st.monitor or disp.get_primary_monitor() or disp.get_monitor(0)
            # Hyprland 처럼: 가장자리를 예약하는 창(작업 표시줄)·바탕화면·exclusive -1(전체 덮기)
            # 이 아니면 작업 표시줄이 차지한 곳을 뺀 영역 안에 놓는다
            if st.exclusive == 0 and not st.auto_exclusive and st.layer != cls.Layer.BACKGROUND:
                return mon.get_workarea()
            return mon.get_geometry()

        @classmethod
        def _place(cls, win):
            st = cls._st(win)
            g = cls._geom(win)
            E = cls.Edge
            a, m = st.anchor, st.margin
            # 늘리지 않는 방향은 내용에 맞는 크기 (get_size 는 처음엔 GTK 기본값 200 이다)
            _mw, nw = win.get_preferred_width()
            _mh, nh = win.get_preferred_height()
            rw, rh = win.get_size_request()
            w = rw if rw > 0 else nw
            h = rh if rh > 0 else nh
            if a.get(E.LEFT) and a.get(E.RIGHT):
                w = g.width - m.get(E.LEFT, 0) - m.get(E.RIGHT, 0)
            if a.get(E.TOP) and a.get(E.BOTTOM):
                h = g.height - m.get(E.TOP, 0) - m.get(E.BOTTOM, 0)
            if a.get(E.LEFT):
                x = g.x + m.get(E.LEFT, 0)
            elif a.get(E.RIGHT):
                x = g.x + g.width - w - m.get(E.RIGHT, 0)
            else:
                x = g.x + (g.width - w) // 2
            if a.get(E.TOP):
                y = g.y + m.get(E.TOP, 0)
            elif a.get(E.BOTTOM):
                y = g.y + g.height - h - m.get(E.BOTTOM, 0)
            else:
                y = g.y + (g.height - h) // 2
            if (w, h) != tuple(win.get_size()):
                win.resize(max(1, w), max(1, h))
            win.move(x, y)
            st.size = (w, h)
            cls._apply_strut(win)
            return False

        @classmethod
        def _apply_strut(cls, win):
            """작업 표시줄이 차지한 가장자리를 창 관리자에 알린다 (최대화한 창이 가리지 않게)"""
            st = cls._st(win)
            gw = win.get_window()
            if gw is None or not (st.exclusive > 0 or st.auto_exclusive):
                return
            E = cls.Edge
            g = cls._geom(win)
            w, h = getattr(st, "size", None) or win.get_size()
            size = st.exclusive if st.exclusive > 0 else h
            # left right top bottom, left_start_y left_end_y right_start_y right_end_y
            # top_start_x top_end_x bottom_start_x bottom_end_x
            vals = [0] * 12
            if st.anchor.get(E.BOTTOM) and not st.anchor.get(E.TOP):
                scr_h = Gdk.Screen.get_default().get_height()
                vals[3] = scr_h - (g.y + g.height) + size
                vals[10], vals[11] = g.x, g.x + g.width - 1
            elif st.anchor.get(E.TOP) and not st.anchor.get(E.BOTTOM):
                vals[2] = g.y + size
                vals[8], vals[9] = g.x, g.x + g.width - 1
            _set_cardinals(gw.get_xid(), "_NET_WM_STRUT_PARTIAL", vals)
            _set_cardinals(gw.get_xid(), "_NET_WM_STRUT", vals[:4])

        @classmethod
        def _focus(cls, win):
            st = cls._st(win)
            if st.kbd in (cls.KeyboardMode.EXCLUSIVE, cls.KeyboardMode.ON_DEMAND) and \
                    st.layer != cls.Layer.BACKGROUND:
                GLib.idle_add(lambda: (win.present(), False)[1])
                if st.kbd == cls.KeyboardMode.EXCLUSIVE:
                    GLib.timeout_add(120, lambda: (cls._grab(win), False)[1])

        @classmethod
        def _grab(cls, win):
            gw = win.get_window()
            if gw is None or not win.get_visible():
                return
            gw.focus(Gtk.get_current_event_time())

    _xlib = None

    def _set_cardinals(xid, name, vals):
        """X 창 속성(CARDINAL 배열) 쓰기 — GTK3 파이썬 바인딩엔 이 기능이 없어 Xlib 을 직접 부른다"""
        global _xlib
        try:
            import ctypes
            import ctypes.util
            if _xlib is None:
                _xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
                _xlib.XOpenDisplay.restype = ctypes.c_void_p
                _xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                _xlib.XInternAtom.restype = ctypes.c_ulong
                _xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
                _xlib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                                  ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                                  ctypes.c_void_p, ctypes.c_int]
                _xlib.XFlush.argtypes = [ctypes.c_void_p]
                _xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
            dpy = _xlib.XOpenDisplay(None)
            if not dpy:
                return
            atom = _xlib.XInternAtom(dpy, name.encode(), 0)
            card = _xlib.XInternAtom(dpy, b"CARDINAL", 0)
            arr = (ctypes.c_long * len(vals))(*vals)
            _xlib.XChangeProperty(dpy, xid, atom, card, 32, 0, ctypes.cast(arr, ctypes.c_void_p), len(vals))
            _xlib.XFlush(dpy)
            _xlib.XCloseDisplay(dpy)
        except Exception:
            pass

    GtkLayerShell = _X11LayerShell
