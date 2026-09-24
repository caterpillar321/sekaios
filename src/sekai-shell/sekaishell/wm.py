"""창 관리자 호환 층 — Hyprland IPC 를 쓰는 셸 코드가 X11(기본 화면 모드)에서도 돌게.

sekai-panel 의 Hypr 클래스와 같은 모양(query / dispatch / subscribe)을 libwnck 로 흉내 낸다.
돌려주는 값도 hyprctl -j 의 JSON 과 같은 모양의 사전이다.
  · 주소(address)   = X 창 번호를 "0x…" 로
  · 최소화된 창      = workspace {"id": -99, "name": "special:min"} (Hyprland 쪽 약속과 같게)
  · 클래스(class)   = WM_CLASS 의 instance 이름 (소문자, 예: thunar, org.xfce.mousepad)
"""
import os
import signal
import subprocess

import gi
gi.require_version("Wnck", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Wnck, Gdk, GLib  # noqa: E402

MIN_WS = {"id": -99, "name": "special:min"}


class X11Hypr:
    def __init__(self):
        Wnck.set_client_type(Wnck.ClientType.PAGER)   # 활성화 요청을 사용자 동작으로 취급
        self.scr = Wnck.Screen.get_default()
        self.scr.force_update()
        self.history = []                               # 최근에 활성화된 창 순서 (xid)
        self.scr.connect("active-window-changed", self._on_active)
        self._on_active(self.scr, None)

    # ── 도움 ──
    def _on_active(self, scr, _prev):
        w = scr.get_active_window()
        if w is not None:
            xid = w.get_xid()
            if xid in self.history:
                self.history.remove(xid)
            self.history.insert(0, xid)

    def _windows(self):
        self.scr.force_update()
        out = []
        for w in self.scr.get_windows() or []:
            if w.get_window_type() not in (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG):
                continue
            if w.is_skip_tasklist():
                continue
            out.append(w)
        return out

    def _find(self, addr):
        try:
            xid = int(str(addr).replace("address:", ""), 16)
        except ValueError:
            return None
        for w in self._windows():
            if w.get_xid() == xid:
                return w
        return None

    def _client(self, w):
        x, y, cw, ch = w.get_geometry()
        ws = w.get_workspace()
        wsd = MIN_WS if w.is_minimized() else {
            "id": (ws.get_number() + 1) if ws else 1,
            "name": str((ws.get_number() + 1) if ws else 1)}
        cls = (w.get_class_instance_name() or w.get_class_group_name() or "").strip()
        xid = w.get_xid()
        hist = self.history.index(xid) if xid in self.history else 999
        disp = Gdk.Display.get_default()
        mon = 0
        for i in range(disp.get_n_monitors()):
            g = disp.get_monitor(i).get_geometry()
            if g.x <= x + cw // 2 < g.x + g.width and g.y <= y + ch // 2 < g.y + g.height:
                mon = i
        return {"address": hex(xid), "class": cls.lower() if cls else "", "initialClass": cls,
                "title": w.get_name() or "", "workspace": wsd, "focusHistoryID": hist,
                "mapped": True, "hidden": False, "floating": True,
                "fullscreen": 1 if w.is_maximized() else 0, "at": [x, y], "size": [cw, ch],
                "monitor": mon, "pid": w.get_pid()}

    # ── hyprctl 흉내 ──
    def query(self, what):
        what = what.strip()
        if what == "clients":
            return [self._client(w) for w in self._windows()]
        if what == "activewindow":
            w = self.scr.get_active_window()
            return self._client(w) if w is not None and w in self._windows() else {}
        if what == "activeworkspace":
            ws = self.scr.get_active_workspace()
            return {"id": (ws.get_number() + 1) if ws else 1}
        if what == "workspaces":
            res = []
            for ws in self.scr.get_workspaces() or []:
                n = ws.get_number() + 1
                cnt = sum(1 for w in self._windows() if w.get_workspace() == ws and not w.is_minimized())
                res.append({"id": n, "name": str(n), "windows": cnt})
            return res
        if what == "monitors":
            disp = Gdk.Display.get_default()
            res = []
            for i in range(disp.get_n_monitors()):
                m = disp.get_monitor(i)
                g, wa = m.get_geometry(), m.get_workarea()
                res.append({"id": i, "name": m.get_model() or f"X-{i}", "x": g.x, "y": g.y,
                            "width": g.width, "height": g.height, "scale": 1.0, "transform": 0,
                            "focused": i == 0,
                            "reserved": [wa.x - g.x, wa.y - g.y, (g.x + g.width) - (wa.x + wa.width),
                                         (g.y + g.height) - (wa.y + wa.height)]})
            return res
        if what.startswith("getoption"):
            return {"int": 0, "custom": "0"}              # 틈·제목줄 높이 — 창 관리자가 알아서
        return []

    def dispatch(self, cmd):
        cmd = cmd.strip()
        verb, _, arg = cmd.partition(" ")
        t = Gtk_time()
        try:
            if verb == "focuswindow":
                w = self._find(arg)
                if w:
                    if w.is_minimized():
                        w.unminimize(t)
                    w.activate(t)
            elif verb in ("movetoworkspace", "movetoworkspacesilent"):
                target, _, addr = arg.partition(",")
                w = self._find(addr)
                if not w:
                    return
                if target.startswith("special:"):
                    w.minimize()                          # Hyprland 쪽의 "최소화" 약속
                else:
                    if w.is_minimized():
                        w.unminimize(t)
                    if verb == "movetoworkspace":
                        w.activate(t)
            elif verb == "closewindow":
                w = self._find(arg)
                if w:
                    w.close(t)
            elif verb == "alterzorder":
                w = self._find(arg.partition(",")[2])
                if w:
                    w.activate(t)
            elif verb == "fullscreen":
                w = self.scr.get_active_window()
                if w:
                    (w.unmaximize if w.is_maximized() else w.maximize)()
            elif verb == "workspace":
                try:
                    n = int(arg) - 1
                    ws = self.scr.get_workspace(n)
                    if ws:
                        ws.activate(t)
                except ValueError:
                    pass
            elif verb in ("resizewindowpixel", "movewindowpixel"):
                parts = arg.replace("exact", "").strip()
                vals, _, addr = parts.partition(",")
                a, b = [int(float(v)) for v in vals.split()[:2]]
                w = self._find(addr)
                if not w:
                    return
                x, y, cw, ch = w.get_geometry()
                if w.is_maximized():
                    w.unmaximize()
                G = Wnck.WindowMoveResizeMask
                if verb == "resizewindowpixel":
                    w.set_geometry(Wnck.WindowGravity.STATIC, G.WIDTH | G.HEIGHT, x, y, a, b)
                else:
                    w.set_geometry(Wnck.WindowGravity.STATIC, G.X | G.Y, a, b, cw, ch)
            elif verb == "exec":
                subprocess.Popen(["sh", "-c", arg], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif verb == "exit":
                # 기본 화면 모드 세션의 우두머리 프로세스를 끝내면 X 가 닫히고 로그인 화면으로
                pid = os.environ.get("SEKAI_SESSION_PID")
                if pid:
                    os.kill(int(pid), signal.SIGTERM)
            # setfloating 등 X11 에선 뜻이 없는 명령은 조용히 넘긴다
        except Exception:
            pass

    def subscribe(self, on_event):
        """wnck 신호를 Hyprland 이벤트 이름으로 바꿔 전달한다"""
        def ev(name, arg=""):
            GLib.idle_add(lambda: (on_event(name, arg), False)[1])

        def win_hooks(w):
            w.connect("name-changed", lambda *_: ev("windowtitle", hex(w.get_xid())[2:]))
            w.connect("state-changed", lambda *_: ev("movewindow", hex(w.get_xid())[2:]))
            w.connect("workspace-changed", lambda *_: ev("movewindow", hex(w.get_xid())[2:]))

        for w in self.scr.get_windows() or []:
            win_hooks(w)

        def on_opened(_s, w):
            win_hooks(w)
            if not _shell_window(w):
                ev("openwindow", hex(w.get_xid())[2:] + ",1,,")

        self.scr.connect("window-opened", on_opened)
        self.scr.connect("window-closed", lambda _s, w: None if _shell_window(w)
                         else ev("closewindow", hex(w.get_xid())[2:]))

        def on_active(scr, _prev):
            w = scr.get_active_window()
            if w is None or not _shell_window(w):
                ev("activewindowv2", "")
        self.scr.connect("active-window-changed", on_active)
        self.scr.connect("active-workspace-changed", lambda *_: ev("workspace", ""))


def _shell_window(w):
    """셸 자신의 창(메뉴·팝업·알림 — 작업 표시줄에 안 나오는 창)인가.
    Hyprland 에선 이런 것이 layer 표면이라 창 이벤트가 나지 않는다. X11 에서도 똑같이
    이벤트를 감춰야 한다 — 안 그러면 메뉴가 뜨자마자 '다른 창이 열렸다'며 닫힌다."""
    return w.is_skip_tasklist() or w.get_window_type() not in (Wnck.WindowType.NORMAL,
                                                                Wnck.WindowType.DIALOG)


def Gtk_time():
    """X 서버 시각 — 창 관리자는 시각 없는(0) 활성화 요청을 '초점 빼앗기'로 보고 무시할 수 있다"""
    try:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11
        return GdkX11.x11_get_server_time(Gdk.get_default_root_window())
    except Exception:
        return 0
