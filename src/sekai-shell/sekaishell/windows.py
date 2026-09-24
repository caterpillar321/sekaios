"""창 배치 (윈도우 11 식 스냅) 와 앱별 창 크기 기억.

SekaiOS 는 모든 창을 떠 있는(floating) 창으로 쓴다 (윈도우처럼).
그래서 반쪽·4분의 1·최대화를 Hyprland 의 좌표 명령으로 직접 한다.

영역: left right (반쪽) · tl tr bl br (4분의 1) · max (최대화)

  끌어서 스냅  제목줄을 끌어 화면 왼쪽·오른쪽 끝 → 반쪽, 네 모서리 → 4분의 1, 위쪽 끝 → 최대화.
               끄는 동안 놓을 자리가 반투명하게 보인다 (hyprbars 패치가 이벤트를 보낸다).
               스냅된 창을 끌어내 놓으면 원래 크기로.
  Win+←/→     반쪽. 반대쪽 반쪽에서는 원래 크기로, 4분의 1 에서는 옆 4분의 1 로
  Win+↑       반쪽 → 위 4분의 1, 아래 4분의 1 → 반쪽, 그 밖엔 최대화
  Win+↓       반쪽 → 아래 4분의 1, 위 4분의 1 → 반쪽, 최대화 풀기, 그 밖엔 최소화

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
        self.snapped = {}          # 주소 → 영역 (left right tl tr bl br)
        self.known = {}            # 주소 → 마지막으로 본 창 정보 (닫힐 때 크기를 알려고)
        self.drag_start = {}       # 주소 → 끌기 시작 때 ((x, y), (w, h))
        self.preview = None        # 끌어서 스냅 미리보기 (sekai-panel 이 넣어 준다)
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

    def _work_area(self, c=None, mon_name=None):
        """모니터에서 작업 표시줄 등을 뺀 영역 (논리 좌표). 창(c)이 있는 모니터나 이름으로 고른다."""
        mons = self.hypr.query("monitors") or []
        mon = None
        if mon_name:
            mon = next((m for m in mons if m.get("name") == mon_name), None)
        if mon is None and c is not None:
            mon = next((m for m in mons if m.get("id") == c.get("monitor")), None)
        mon = mon or (mons[0] if mons else None)
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

    # ── 영역 ──
    def zone_rect(self, zone, c=None, mon_name=None):
        """영역이 차지할 자리 (x, y, w, h) — 제목줄까지 포함한 바깥 크기"""
        ax, ay, aw, ah = self._work_area(c, mon_name)
        g = self._gap() // 2
        hw, hh = aw / 2 - g, ah / 2 - g
        rx, by = ax + aw / 2 + g, ay + ah / 2 + g
        return {"max": (ax, ay, aw, ah),
                "left": (ax, ay, hw, ah), "right": (rx, ay, hw, ah),
                "tl": (ax, ay, hw, hh), "tr": (rx, ay, hw, hh),
                "bl": (ax, by, hw, hh), "br": (rx, by, hw, hh)}.get(zone)

    def _client(self, addr):
        return next((x for x in (self.hypr.query("clients") or []) if x.get("address") == addr), None)

    def snap_to(self, addr, zone, c=None, mon_name=None):
        """창을 영역에 배치한다. 처음 스냅할 때의 크기·위치를 기억해 둔다 (풀 때 되돌리려고)."""
        c = c or self._client(addr)
        if not c:
            return
        maxed = c.get("fullscreen", 0) == 1
        if not c.get("floating"):
            self.hypr.dispatch(f"setfloating address:{addr}")
        if zone == "max":
            if not maxed:
                self.hypr.dispatch(f"focuswindow address:{addr}")
                self.hypr.dispatch("fullscreen 1")          # 1 = 최대화 (작업 표시줄은 남긴다)
            return
        if maxed:
            self.hypr.dispatch(f"focuswindow address:{addr}")
            self.hypr.dispatch("fullscreen 1")
            c = self._client(addr) or c
        if addr not in self.saved:
            (x, y), (w, h) = self.drag_start.get(addr) or (c.get("at", [0, 0]), c.get("size", [800, 600]))
            self.saved[addr] = (x, y, w, h)
        rect = self.zone_rect(zone, c, mon_name)
        if not rect:
            return
        x, y, w, h = rect
        bar = self._bar(c)
        self._place(addr, x, y + bar, w, h - bar)
        self.snapped[addr] = zone
        # 앱의 최소 크기가 칸보다 크면 창이 덜 줄어들고, Hyprland 가 칸 가운데에 맞추면서
        # 제목줄이 화면 밖으로 나갈 수 있다 → 실제 크기를 보고 칸 쪽 모서리에 붙여 화면 안으로
        GLib.timeout_add(200, self._fit, addr, zone, rect, bar)

    def _fit(self, addr, zone, rect, bar):
        c = self._client(addr)
        if not c:
            return False
        (cx, cy), (cw, ch) = c.get("at", [0, 0]), c.get("size", [0, 0])
        x, y, w, h = rect
        if cw <= w + 1 and ch <= h - bar + 1:
            return False                                  # 칸에 맞게 들어갔다
        ax, ay, aw, ah = self._work_area(c)
        nx = x + w - cw if zone in ("right", "tr", "br") else x      # 칸의 바깥쪽 모서리에 붙인다
        ny = y + h - ch if zone in ("bl", "br") else y + bar
        nx = min(max(nx, ax), ax + aw - cw)
        ny = min(max(ny, ay + bar), ay + ah - ch)
        if abs(nx - cx) > 1 or abs(ny - cy) > 1:
            self.hypr.dispatch(f"movewindowpixel exact {int(nx)} {int(ny)},address:{addr}")
        return False

    # ── Win+화살표 ──
    # 지금 영역 → 누른 방향 → 갈 영역 ("restore" = 원래 크기, "min" = 최소화, "unmax" = 최대화 풀기)
    KEYS = {
        "left":  {None: "left", "left": "restore", "right": "restore", "tr": "tl", "br": "bl",
                  "tl": "tl", "bl": "bl", "max": "left"},
        "right": {None: "right", "right": "restore", "left": "restore", "tl": "tr", "bl": "br",
                  "tr": "tr", "br": "br", "max": "right"},
        "up":    {None: "max", "left": "tl", "right": "tr", "bl": "left", "br": "right",
                  "tl": "max", "tr": "max", "max": "max"},
        "down":  {None: "min", "left": "bl", "right": "br", "tl": "left", "tr": "right",
                  "bl": "restore", "br": "restore", "max": "unmax"},
    }

    def snap(self, direction):
        c = self._active()
        if not c or direction not in self.KEYS:
            return
        addr = c["address"]
        cur = "max" if c.get("fullscreen", 0) == 1 else self.snapped.get(addr)
        to = self.KEYS[direction].get(cur, "restore")
        if to == "min":
            self.hypr.dispatch(f"movetoworkspacesilent special:min,address:{addr}")
        elif to == "unmax":
            self.hypr.dispatch("fullscreen 1")
        elif to == "restore":
            self._restore(addr)
        elif to != cur:
            self.snap_to(addr, to, c)

    def _restore(self, addr):
        g = self.saved.pop(addr, None)
        self.snapped.pop(addr, None)
        if g:
            self._place(addr, *g)
            GLib.timeout_add(200, self._keep_visible, addr)

    def _keep_visible(self, addr):
        """제목줄이 작업 영역 밖으로 나가 있으면 안으로 들인다 (다시 잡을 수 있게)"""
        c = self._client(addr)
        if not c or c.get("fullscreen"):
            return False
        (x, y), (w, h) = c.get("at", [0, 0]), c.get("size", [0, 0])
        ax, ay, aw, ah = self._work_area(c)
        bar = self._bar(c)
        nx = min(max(x, ax), max(ax, ax + aw - w))
        ny = min(max(y, ay + bar), max(ay + bar, ay + ah - h))
        if abs(nx - x) > 1 or abs(ny - y) > 1:
            self.hypr.dispatch(f"movewindowpixel exact {int(nx)} {int(ny)},address:{addr}")
        return False

    # ── 끌어서 스냅 (hyprbars 이벤트) ──
    def _drag_start(self, addr):
        c = self._client(addr)
        if not c:
            return False
        if c.get("fullscreen", 0) == 1:
            self.hypr.dispatch(f"focuswindow address:{addr}")
            self.hypr.dispatch("fullscreen 1")            # 최대화된 창을 끌면 먼저 최대화를 푼다
        elif addr not in self.snapped:
            self.drag_start[addr] = (c.get("at", [0, 0]), c.get("size", [800, 600]))
        return False

    def _drag_zone(self, zone, mon_name):
        rect = self.zone_rect(zone, mon_name=mon_name) if zone != "none" else None
        if rect and self.preview is not None:
            self.preview.show_at(*rect)
        elif self.preview is not None:
            self.preview.hide_now()
        return False

    def _drag_drop(self, zone, mon_name, addr):
        if self.preview is not None:
            self.preview.hide_now()
        if zone != "none":
            self.snap_to(addr, zone, mon_name=mon_name)
        elif addr in self.snapped:
            # 스냅된 창을 끌어낸 것 — 원래 크기로, 잡은 자리가 커서 밑에 그대로 오게
            c = self._client(addr)
            g = self.saved.pop(addr, None)
            self.snapped.pop(addr, None)
            if c and g:
                (x, y), (w, _h) = c.get("at", [0, 0]), c.get("size", [1, 1])
                cur = self.hypr.query("cursorpos") or {}
                cx = cur.get("x", x + w / 2) if isinstance(cur, dict) else x + w / 2
                nx = cx - (cx - x) * (g[2] / max(1, w))
                self._place(addr, nx, y, g[2], g[3])
                GLib.timeout_add(200, self._keep_visible, addr)
        self.drag_start.pop(addr, None)
        return False

    # ── 창 크기 기억 ──
    def _poll(self):
        """닫힐 때는 창 정보가 이미 없다 → 주기적으로 크기를 봐 둔다."""
        for c in self.hypr.query("clients") or []:
            if c.get("address"):
                self.known[c["address"]] = c
        return True

    def on_event(self, name, arg):
        if name == "sekaisnapstart":
            GLib.idle_add(self._drag_start, "0x" + arg.strip())
        elif name == "sekaisnap":
            zone, _, mon = arg.partition(",")
            GLib.idle_add(self._drag_zone, zone, mon.strip())
        elif name == "sekaisnapdrop":
            parts = arg.strip().split(",")
            if len(parts) == 3:
                GLib.idle_add(self._drag_drop, parts[0], parts[1], "0x" + parts[2])
        elif name == "openwindow":
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
