"""창 배치 (Win+화살표) 와 앱별 창 크기 기억.

SekaiOS 는 모든 창을 떠 있는(floating) 창으로 쓴다 (윈도우처럼).
그래서 반쪽 맞춤·최대화를 Hyprland 의 좌표 명령으로 직접 한다.

  Win+←/→   왼쪽/오른쪽 반쪽. 이미 그쪽이면 원래 크기로
  Win+↑     최대화 (반쪽 상태면 먼저 최대화)
  Win+↓     최대화 풀기 → 반쪽 풀기 → 최소화

창 크기 기억: 앱(클래스)의 첫 창이 닫힐 때 크기를 적어 두고,
다음에 그 앱의 첫 창이 열리면 그 크기로 가운데에 연다.
(두 번째 창부터는 대화상자일 가능성이 커서 건드리지 않는다.)
"""
import json
import os
import re

from gi.repository import GLib

from . import dbg

STATE = os.path.expanduser("~/.local/state/sekai/windows.json")
MIN_W, MIN_H = 320, 200
# 크기를 기억하지 않을 창 — 크기가 스스로 정해지는 것들
SKIP_CLASSES = {"", "lxpolkit", "polkit-gnome-authentication-agent-1", "pinentry",
                "org.freedesktop.impl.portal.desktop.gtk", "xdg-desktop-portal-gtk",
                "sekai-settings-shot"}


# 제목 표시줄(hyprbars)을 빼는 창 — hyprland.conf 의 nobar 규칙과 같게
NOBAR = re.compile(r"^(chromium|google-chrome.*|org\.gnome\..*)$")


class WindowManager:
    def __init__(self, hypr):
        self.hypr = hypr
        self.saved = {}            # 주소 → 반쪽 맞춤 전 (x, y, w, h)
        self.snapped = {}          # 주소 → "left" / "right"
        self.known = {}            # 주소 → 마지막으로 본 창 정보 (닫힐 때 크기를 알려고)
        self.sizes = self._load()
        GLib.timeout_add_seconds(3, self._poll)

    # ── 저장 ──
    def _load(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(v, list) and len(v) == 2}
        except Exception:
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.sizes, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATE)
        except OSError as e:
            dbg("창 크기 저장 실패", e)

    # ── 도움 ──
    def _active(self):
        a = self.hypr.query("activewindow") or {}
        return a if isinstance(a, dict) and a.get("address") else None

    def _work_area(self, c):
        """창이 있는 모니터에서 작업 표시줄 등을 뺀 영역 (논리 좌표)."""
        mons = self.hypr.query("monitors") or []
        mon = next((m for m in mons if m.get("id") == c.get("monitor")), None) or (mons[0] if mons else None)
        if not mon:
            return 0, 0, 1920, 1080
        scale = mon.get("scale", 1.0) or 1.0
        w, h = mon["width"] / scale, mon["height"] / scale
        if mon.get("transform", 0) in (1, 3, 5, 7):
            w, h = h, w
        l, t, r, b = (mon.get("reserved") or [0, 0, 0, 0])[:4]
        gap = self._gap()
        return (mon["x"] + l + gap, mon["y"] + t + gap,
                w - l - r - 2 * gap, h - t - b - 2 * gap)

    def _gap(self):
        try:
            opt = self.hypr.query("getoption general:gaps_out") or {}
            v = opt.get("custom") or opt.get("int") or 0
            if isinstance(v, str):
                v = int(v.split()[0])
            return int(v)
        except Exception:
            return 8

    def _bar(self, c):
        """hyprbars 제목 표시줄 높이. 제목줄은 창 영역 바깥 위에 그려진다."""
        if NOBAR.match(c.get("class") or ""):
            return 0
        try:
            opt = self.hypr.query("getoption plugin:hyprbars:bar_height") or {}
            return int(opt.get("int") or 0)
        except Exception:
            return 0

    def _place(self, addr, x, y, w, h):
        self.hypr.dispatch(f"resizewindowpixel exact {int(w)} {int(h)},address:{addr}")
        self.hypr.dispatch(f"movewindowpixel exact {int(x)} {int(y)},address:{addr}")

    # ── Win+화살표 ──
    def snap(self, direction):
        c = self._active()
        if not c:
            return
        addr = c["address"]
        maxed = c.get("fullscreen", 0) == 1
        if not c.get("floating"):
            self.hypr.dispatch(f"setfloating address:{addr}")

        if direction == "up":
            if not maxed:
                self.hypr.dispatch("fullscreen 1")          # 1 = 최대화 (작업 표시줄은 남긴다)
            return
        if direction == "down":
            if maxed:
                self.hypr.dispatch("fullscreen 1")          # 최대화 풀기
            elif addr in self.snapped:
                self._restore(addr)
            else:
                self.hypr.dispatch(f"movetoworkspacesilent special:min,address:{addr}")
            return
        # 왼쪽 / 오른쪽
        if maxed:
            self.hypr.dispatch("fullscreen 1")
            c = self._active() or c
        if self.snapped.get(addr) == direction:
            self._restore(addr)                              # 같은 쪽 한 번 더 = 원래대로
            return
        other = {"left": "right", "right": "left"}[direction]
        if self.snapped.get(addr) == other:
            # 반대쪽에서 넘어오면 원래 크기로 (윈도우와 같다: 오른쪽 반 → Win+← → 원래)
            self._restore(addr)
            return
        if addr not in self.saved:
            (x, y), (w, h) = c.get("at", [0, 0]), c.get("size", [800, 600])
            self.saved[addr] = (x, y, w, h)
        ax, ay, aw, ah = self._work_area(c)
        bar = self._bar(c)
        gap = self._gap() // 2
        half = aw / 2 - gap
        x = ax if direction == "left" else ax + aw / 2 + gap
        self._place(addr, x, ay + bar, half, ah - bar)
        self.snapped[addr] = direction

    def _restore(self, addr):
        g = self.saved.pop(addr, None)
        self.snapped.pop(addr, None)
        if g:
            self._place(addr, *g)

    # ── 창 크기 기억 ──
    def _poll(self):
        """닫힐 때는 창 정보가 이미 없다 → 주기적으로 크기를 봐 둔다."""
        for c in self.hypr.query("clients") or []:
            if c.get("address"):
                self.known[c["address"]] = c
        return True

    def on_event(self, name, arg):
        if name == "openwindow":
            addr = "0x" + arg.split(",", 1)[0]
            GLib.timeout_add(60, self._opened, addr)
        elif name == "closewindow":
            addr = "0x" + arg.strip()
            c = self.known.pop(addr, None)
            self.saved.pop(addr, None)
            self.snapped.pop(addr, None)
            if c:
                self._closed(c)
        elif name in ("movewindow", "movewindowv2", "windowtitle", "windowtitlev2", "activewindowv2"):
            GLib.idle_add(self._poll_one)

    def _poll_one(self):
        a = self._active()
        if a:
            self.known[a["address"]] = a
        return False

    def _first_of_class(self, c, clients):
        cls = c.get("class")
        return not any(o.get("class") == cls and o.get("address") != c.get("address")
                       for o in clients)

    def _closed(self, c):
        cls = c.get("class") or ""
        if cls in SKIP_CLASSES or not c.get("floating") or c.get("fullscreen"):
            return
        if c["address"] in self.snapped:
            return
        clients = self.hypr.query("clients") or []
        if not self._first_of_class(c, clients):
            return                          # 같은 앱의 다른 창이 남아 있다 — 대화상자일 수 있다
        w, h = c.get("size", [0, 0])
        if w >= MIN_W and h >= MIN_H:
            self.sizes[cls] = [int(w), int(h)]
            self._save()
            dbg(f"[win] {cls} 크기 기억 {w}x{h}")

    def _opened(self, addr):
        clients = self.hypr.query("clients") or []
        c = next((x for x in clients if x.get("address") == addr), None)
        if not c:
            return False
        self.known[addr] = c
        cls = c.get("class") or ""
        want = self.sizes.get(cls)
        if not want or not c.get("floating") or c.get("fullscreen"):
            return False
        if not self._first_of_class(c, clients):
            return False
        ax, ay, aw, ah = self._work_area(c)
        bar = self._bar(c)
        ay, ah = ay + bar, ah - bar
        w, h = min(want[0], aw), min(want[1], ah)
        cw, ch = c.get("size", [0, 0])
        if abs(cw - w) < 4 and abs(ch - h) < 4:
            return False
        self._place(addr, ax + (aw - w) / 2, ay + (ah - h) / 2, w, h)
        dbg(f"[win] {cls} 기억한 크기로 {w}x{h}")
        return False
