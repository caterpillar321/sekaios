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
import time

from gi.repository import GLib

from . import dbg, desktops

STATE = os.path.expanduser("~/.local/state/sekai/windows.json")


def is_max(c):
    """최대화했나 — WorldLink sekai34 부터 창의 상태(sekaiMaximized). 옛 합성기(업데이트 도중)는 fullscreen 1"""
    if "sekaiMaximized" in c:
        return bool(c.get("sekaiMaximized"))
    return c.get("fullscreen", 0) == 1


def is_fullscreen(c):
    """진짜 전체 화면(F11·동영상·게임) — 최대화와 다르다"""
    return bool(c.get("fullscreen", 0) & 2)
MIN_W, MIN_H = 320, 200
SNAP_GAP = 0               # 스냅한 창은 화면 끝과 서로에게 딱 붙인다 (윈도우 11)
# 크기를 기억하지 않을 창 — 크기가 스스로 정해지는 것들
SKIP_CLASSES = {"", "lxpolkit", "polkit-gnome-authentication-agent-1", "polkit-agent", "nm-agent", "pinentry",
                "org.freedesktop.impl.portal.desktop.gtk", "xdg-desktop-portal-gtk",
                "sekai-settings-shot"}


# 제목 표시줄(hyprbars)을 빼는 창 — hyprland.conf 의 nobar 규칙과 같게
NOBAR = re.compile(r"^(chromium|google-chrome.*)$")      # 그 밖의 앱은 WorldLink 가 알려 주는 sekaiCSD 로
#   클래스 → 처음 제목. 파일 탐색기는 본 창만 제목 표시줄을 스스로 그린다 (대화상자는 hyprbars)
NOBAR_TITLED = {"org.sekaios.Files": re.compile(r"^org\.sekaios\.Files$")}


class WindowManager:
    def __init__(self, hypr):
        self.hypr = hypr
        self.saved = {}            # 주소 → 반쪽 맞춤 전 (x, y, w, h)
        self.snapped = {}          # 주소 → 영역 (left right tl tr bl br)
        self.known = {}            # 주소 → 마지막으로 본 창 정보 (닫힐 때 크기를 알려고)
        self.mains = set()         # 앱의 첫 창(본 창)으로 열린 주소 — 크기는 이 창만 기억한다
        self._desk_shown = None    # 바탕 화면 보기로 최소화한 창들 — 다시 하면 되살린다 (show_desktop)
        self.drag_start = {}       # 주소 → 끌기 시작 때 ((x, y), (w, h))
        self.preview = None        # 끌어서 스냅 미리보기 (sekai-panel 이 넣어 준다)
        self.assist = None         # 스냅 도우미 (sekai-panel 이 넣어 준다)
        self.topbar = None         # 위쪽에서 내려오는 레이아웃 바 (sekai-panel 이 넣어 준다)
        self.winmenu = None        # 제목줄 오른쪽 클릭 창 메뉴 (sekai-panel 이 넣어 준다)
        self._poll_src = 0         # 끄는 동안 커서 위치를 읽는 타이머
        self._bar_hit = None       # 바에서 가리킨 (영역, 나머지 칸들)
        self._drag_mons = []
        self._drag_edge = None     # 지금 커서가 닿은 화면 가장자리 영역 (sekaidrag move)
        self._drag_addr = None     # 끄는 창 — 끄는 중에 닫히면 놓기 이벤트가 안 온다
        self._drag_t0 = 0          # 끌기 시작 시각 (µs)
        self._drag_pos = None      # 마지막으로 본 커서 자리와 그때부터 멈춰 있던 시각
        self._drag_armed = False   # 위쪽 띠 밖으로 나간 적이 있나 (레이아웃 바)
        self._drag_still = 0
        self._poll_one_src = 0
        self.mon_ws = {}           # 모니터 이름 → 보이던 워크스페이스 id (모니터가 빠질 때 창을 옮기려고)
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

    # ── 바탕 화면 보기 (Win+D · 세 손가락 아래로 쓸기) ──
    def show_desktop(self):
        """윈도우처럼 — 이 데스크톱의 창을 모두 최소화하고, 다시 하면 그 창들을 그대로 되살린다.
        그사이 창을 열거나 다른 창을 골랐으면 되살릴 목록은 버리고 새로 바탕 화면 보기"""
        ws = (self.hypr.query("activeworkspace") or {}).get("id")
        shown, self._desk_shown = self._desk_shown, None
        if shown and shown["ws"] == ws:
            for addr in shown["addrs"]:                   # 아래 창부터 — 맨 위였던 창이 다시 맨 위
                self.hypr.dispatch(f"sekaiminimize off,address:{addr}")
            if shown["focus"]:
                self.hypr.dispatch(f"focuswindow address:{shown['focus']}")
            return
        act = self._active() or {}
        cs = [c for c in (self.hypr.query("clients") or [])
              if c.get("mapped", True) and (c.get("workspace") or {}).get("id") == ws
              and not desktops.is_minimized(c) and int(c.get("sekaiParent", "0x0"), 16) == 0]  # 대화상자는 부모를 따라간다
        if not cs:
            return
        cs.sort(key=lambda c: -(c.get("focusHistoryID") or 0))    # 오래전에 쓴 창(아래)부터
        for c in cs:
            desktops.minimize(self.hypr, c["address"])
        self._desk_shown = {"ws": ws, "addrs": [c["address"] for c in cs], "focus": act.get("address"),
                            "t": time.monotonic()}

    def _toggle_max(self, addr):
        return self._set_max(addr, None)

    def _set_max(self, addr, on):
        """이 창의 최대화를 켜고(on=True) 끄고(False) 뒤집는다(None). WorldLink sekai34 의 sekaimaximize 는 창을 집어 준다 —
        옛 합성기는 fullscreen 1 이 "초점 창"에 걸려, 초점이 이 창에 왔을 때만 (스냅 도우미처럼 키보드를 쥔 레이어가
        떠 있으면 Hyprland 가 초점을 거부해 엉뚱한 창이 최대화됐다)"""
        mode = "toggle" if on is None else ("on" if on else "off")
        r = self.hypr.dispatch(f"sekaimaximize {mode},address:{addr}")
        if r is not None and "invalid dispatcher" not in r.lower():
            return r.strip() == "ok"
        c = self._client(addr)
        if c is None or (on is not None and is_max(c) == on):
            return c is not None
        self.hypr.dispatch(f"focuswindow address:{addr}")
        a = self._active()
        if not a or a.get("address") != addr:
            dbg(f"[win] {addr} 에 초점이 가지 않아 최대화를 바꾸지 않는다")
            return False
        self.hypr.dispatch("fullscreen 1")
        return True

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
        gap = SNAP_GAP
        return (mon["x"] + l + gap, mon["y"] + t + gap,
                w - l - r - 2 * gap, h - t - b - 2 * gap)

    def _bar(self, c):
        """hyprbars 제목 표시줄 높이. 제목줄은 창 영역 바깥 위에 그려진다.
        WorldLink sekai33 부터는 합성기가 실제 높이를 알려 준다(sekaiTop) — 아래 짐작은 옛 합성기(업데이트 도중)용"""
        top = c.get("sekaiTop")
        if isinstance(top, int):
            return max(0, top)
        cls = c.get("class") or ""
        if c.get("sekaiCSD"):
            return 0                                      # 앱이 제목줄을 스스로 그린다 (WorldLink 가 막대를 그리지 않음)
        titled = NOBAR_TITLED.get(cls)
        if NOBAR.match(cls) or (titled and titled.match(c.get("initialTitle") or "")):
            return 0
        try:
            # 설정 › 개인 설정에서 제목 표시줄을 껐으면 막대가 없다 (높이 값은 그대로 남아 있다)
            on = self.hypr.query("getoption plugin:hyprbars:enabled") or {}
            if isinstance(on, dict) and on.get("int") == 0:
                return 0
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
        g = SNAP_GAP // 2
        hw, hh = aw / 2 - g, ah / 2 - g
        rx, by = ax + aw / 2 + g, ay + ah / 2 + g
        t = (aw - 4 * g) / 3                               # 3등분 한 칸 (칸 사이 틈 두 개)
        return {"max": (ax, ay, aw, ah),
                "left": (ax, ay, hw, ah), "right": (rx, ay, hw, ah),
                "tl": (ax, ay, hw, hh), "tr": (rx, ay, hw, hh),
                "bl": (ax, by, hw, hh), "br": (rx, by, hw, hh),
                # 스냅 레이아웃의 2/3·1/3·3등분
                "l23": (ax, ay, 2 * t + 2 * g, ah), "r13": (ax + 2 * t + 4 * g, ay, t, ah),
                "l13": (ax, ay, t, ah), "r23": (ax + t + 2 * g, ay, 2 * t + 2 * g, ah),
                "c1": (ax, ay, t, ah), "c2": (ax + t + 2 * g, ay, t, ah),
                "c3": (ax + 2 * t + 4 * g, ay, t, ah)}.get(zone)

    def _client(self, addr):
        return next((x for x in (self.hypr.query("clients") or []) if x.get("address") == addr), None)

    def snap_to(self, addr, zone, c=None, mon_name=None, assist=True, rest=None):
        """창을 영역에 배치한다. 처음 스냅할 때의 크기·위치를 기억해 둔다 (풀 때 되돌리려고)."""
        c = c or self._client(addr)
        if not c:
            return
        if is_fullscreen(c):
            return                                        # 전체 화면(F11·동영상)은 건드리지 않는다
        if c.get("sekaiFixed"):
            return                                        # 크기를 바꿀 수 없는 창 — 칸에 맞출 수 없다
        maxed = is_max(c)
        if not c.get("floating"):
            self.hypr.dispatch(f"setfloating address:{addr}")
        if zone == "max":
            if not maxed:
                self._set_max(addr, True)                 # 작업 표시줄은 남긴다
            return
        if maxed:
            if not self._set_max(addr, False):
                return
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
        if assist and self.assist is not None:
            # 스냅 도우미: 남은 칸에 둘 창을 고르게 (창이 자리 잡은 뒤에)
            GLib.timeout_add(250, lambda: self.assist.start(addr, zone, rest, mon_name) and False)
        # 앱의 최소 크기가 칸보다 크면 창이 덜 줄어들고, Hyprland 가 칸 가운데에 맞추면서
        # 제목줄이 화면 밖으로 나갈 수 있다 → 실제 크기를 보고 칸 쪽 모서리에 붙여 화면 안으로
        GLib.timeout_add(200, self._fit, addr, zone, rect, bar)

    def _fit(self, addr, zone, rect, bar):
        if self.snapped.get(addr) != zone:
            return False                                  # 그사이 다른 칸으로 옮겼거나 풀었다 (Win+← 두 번 등)
        c = self._client(addr)
        if not c or c.get("fullscreen") or is_max(c):
            return False
        (cx, cy), (cw, ch) = c.get("at", [0, 0]), c.get("size", [0, 0])
        x, y, w, h = rect
        if cw <= w + 1 and ch <= h - bar + 1:
            return False                                  # 칸에 맞게 들어갔다
        ax, ay, aw, ah = self._work_area(c)
        nx = x + w - cw if zone in ("right", "tr", "br", "r13", "r23", "c3") else x  # 칸의 바깥쪽 모서리에
        ny = y + h - ch if zone in ("bl", "br") else y + bar
        nx = min(max(nx, ax), ax + aw - cw)
        # 창이 작업 영역보다 크면 아래가 삐져나가도 제목줄이 화면 안에 오는 게 먼저 (다시 잡을 수 있게)
        ny = max(min(ny, ay + ah - ch), ay + bar)
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
    # 스냅 레이아웃의 1/3·2/3 칸 — 왼쪽에 붙은 칸은 ← 로 왼쪽 반, → 로 오른쪽 반 (가운데 칸도), ↑ 최대화, ↓ 복원
    for _z in ("l23", "l13", "c1", "c2", "c3", "r13", "r23"):
        KEYS["left"][_z], KEYS["right"][_z], KEYS["up"][_z], KEYS["down"][_z] = "left", "right", "max", "restore"
    del _z

    def snap(self, direction):
        c = self._active()
        if not c or direction not in self.KEYS:
            return
        addr = c["address"]
        if is_fullscreen(c):
            return                                        # 전체 화면(F11·동영상) 중엔 스냅하지 않는다
        cur = "max" if is_max(c) else self.snapped.get(addr)
        if cur not in (None, "max") and not self._in_zone(c, cur):
            cur = None                                    # 다른 방법(Win+Shift+화살표·Super+끌기)으로 옮겨졌다
        to = self.KEYS[direction].get(cur, "restore")
        if c.get("sekaiFixed") and to != "min":
            return                                        # 크기 고정 창은 Win+↓(최소화)만
        if to == "min":
            desktops.minimize(self.hypr, addr)
        elif to == "unmax":
            self._set_max(addr, False)
        elif to == "restore":
            self._restore(addr)
        elif to != cur:
            self.snap_to(addr, to, c, assist=False)     # 키보드로 연달아 누르는 중엔 도우미를 띄우지 않는다

    def _in_zone(self, c, zone, slack=8):
        """창이 아직 그 칸 자리에 있나 (제목줄 높이를 뺀 내용 위치로 비교)"""
        rect = self.zone_rect(zone, c)
        if not rect:
            return False
        x, y, w, h = rect
        bar = self._bar(c)
        (cx, cy), (cw, ch) = c.get("at", [0, 0]), c.get("size", [0, 0])
        return abs(cx - x) <= slack and abs(cy - (y + bar)) <= slack and cw <= w + slack and ch <= h - bar + slack

    def _restore(self, addr):
        g = self.saved.pop(addr, None)
        self.snapped.pop(addr, None)
        if g:
            self._place(addr, *g)
            GLib.timeout_add(200, self._keep_visible, addr)

    def _keep_visible(self, addr):
        """제목줄이 작업 영역 밖으로 나가 있으면 안으로 들인다 (다시 잡을 수 있게)"""
        c = self._client(addr)
        if not c or c.get("fullscreen") or is_max(c):
            return False
        (x, y), (w, h) = c.get("at", [0, 0]), c.get("size", [0, 0])
        ax, ay, aw, ah = self._work_area(c)
        bar = self._bar(c)
        nx = min(max(x, ax), max(ax, ax + aw - w))
        ny = min(max(y, ay + bar), max(ay + bar, ay + ah - h))
        if abs(nx - x) > 1 or abs(ny - y) > 1:
            self.hypr.dispatch(f"movewindowpixel exact {int(nx)} {int(ny)},address:{addr}")
        return False

    # ── 끌어서 스냅 ──
    #   WorldLink sekai33 부터: 합성기가 sekaidrag>>start·move(x,y)·end(x,y)·cancel 을 한 곳에서 보내고, 영역 판단은
    #   여기서 한다 (_edge_zone). 옛 합성기(sekaisnapstart·sekaisnap·sekaisnapdrop)는 커서를 폴링하던 옛 길로 — 업데이트 뒤
    #   다시 로그인할 때까지만 쓰인다
    def _drag_start(self, addr, poll=True):
        c = self._client(addr)
        if not c:
            return False
        self._drag_addr = addr
        self._drag_edge = None
        self._drag_mons = self.hypr.query("monitors") or []         # 가장자리·레이아웃 바 판단에
        # 크기를 바꿀 수 없는 창(SEKAI_FIXED_SIZE)은 칸에 맞출 수 없다 — 스냅 미리보기·레이아웃 바 없이 옮기기만
        self._drag_fixed = bool(c.get("sekaiFixed"))
        if self._drag_fixed:
            return False
        if self.topbar is not None:
            # 새 끌기 — 멈추지 못한 옛 폴링이 남아 있어도 기준은 새로 잡는다
            self._drag_t0 = self._drag_still = GLib.get_monotonic_time()
            self._drag_pos = None
            self._drag_armed = False        # 위쪽 띠 밖으로 한 번 나가야 레이아웃 바를 띄운다 (_drag_at)
            if not poll:
                self._bar_hit = None
            elif not self._poll_src:
                self._bar_hit = None
                self._poll_src = GLib.timeout_add(33, self._drag_poll)
        if is_max(c):
            pass    # 최대화는 Hyprland 가 끌기 문턱(binds:drag_threshold)을 넘을 때 푼다 — 여기서 풀면 손이 조금만
                    #   떨려도 풀렸다. 커서가 제목줄 같은 자리에 오게 두는 것도 그쪽 (SEKAI_DRAG_RESTORE)
        elif addr not in self.snapped:
            self.drag_start[addr] = (c.get("at", [0, 0]), c.get("size", [800, 600]))
        return False

    # 위쪽 가운데로 끌면 내려오는 레이아웃 바 — 커서가 이 띠 안에 있으면 보인다
    BAND_Y, BAND_X = 150, 0.25      # 위에서 150px, 가운데에서 모니터 너비의 25% 안
    LEAVE_Y, LEAVE_X = 260, 0.32    # 이만큼 벗어나면 다시 올라간다
    # 놓기 이벤트(sekaisnapdrop)를 놓쳤을 때의 안전장치 (초) — Hyprland 에 버튼이 눌려 있는지 물을 방법이
    #   없다. 커서가 이만큼 멈춰 있거나 끌기가 이만큼 길어지면 끝난 것으로 본다 (그대로 두면 끌지 않아도
    #   위쪽 가운데에서 바와 미리보기가 뜨고 초당 30번 cursorpos 를 묻는다)
    DRAG_IDLE, DRAG_MAX = 10, 60

    def _drag_poll(self):
        cur = self.hypr.query("cursorpos") or {}
        now = GLib.get_monotonic_time()
        pos = (cur.get("x"), cur.get("y")) if isinstance(cur, dict) else None
        if pos != self._drag_pos:
            self._drag_pos, self._drag_still = pos, now
        if now - self._drag_still > self.DRAG_IDLE * 1000000 or now - self._drag_t0 > self.DRAG_MAX * 1000000:
            dbg("[win] 끌기 놓기 이벤트를 못 받았다 — 스냅 폴링을 멈춘다")
            self._poll_src = 0                 # 이 타이머는 False 를 돌려주며 스스로 끝난다
            self._drag_abort()
            return False
        if not isinstance(cur, dict) or "x" not in cur:
            return True
        self._drag_at(cur["x"], cur["y"])
        return True

    def _drag_mon(self, x, y):
        """(모니터, 논리 너비, 논리 높이) — 끌기 시작 때 받아 둔 목록에서. 커서가 화면 끝 바로 바깥(x = 너비)으로
        오기도 해서, 어느 모니터에도 안 들면 가장 가까운 모니터 (합성기의 getMonitorFromVector 와 같게)"""
        best = None
        for m in self._drag_mons:
            sc = m.get("scale", 1.0) or 1.0
            mw, mh = m["width"] / sc, m["height"] / sc
            if m.get("transform", 0) in (1, 3, 5, 7):
                mw, mh = mh, mw
            dx = max(m["x"] - x, 0, x - (m["x"] + mw - 1))
            dy = max(m["y"] - y, 0, y - (m["y"] + mh - 1))
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, m, mw, mh)
        return best[1:] if best else None

    def _edge_zone(self, x, y):
        """(영역, 모니터 이름) — 화면 가장자리·모서리 (윈도우의 끌어서 스냅). 예전엔 합성기와 제목줄 플러그인이 따로 셌다"""
        found = self._drag_mon(x, y)
        if not found:
            return "none", ""
        m, W, H = found
        x = min(max(x - m["x"], 0), W - 1)          # 화면 밖이면 그 끝으로
        y = min(max(y - m["y"], 0), H - 1)
        E = 4                                   # 가장자리로 치는 두께
        C = max(48, min(W, H) / 8)              # 모서리로 치는 길이
        L, R, T, B = x <= E, x >= W - 1 - E, y <= E, y >= H - 1 - E
        name = m.get("name", "")
        if (L and y < C) or (T and x < C):
            return "tl", name
        if (R and y < C) or (T and x > W - C):
            return "tr", name
        if (L and y > H - C) or (B and x < C):
            return "bl", name
        if (R and y > H - C) or (B and x > W - C):
            return "br", name
        if L:
            return "left", name
        if R:
            return "right", name
        if T:
            return "max", name
        return "none", name

    def _drag_move(self, x, y):
        if not self._drag_addr or getattr(self, "_drag_fixed", False):
            return False
        edge = self._edge_zone(x, y)
        if edge != self._drag_edge:
            self._drag_edge = edge
            self._drag_zone(*edge)
        if self.topbar is not None:
            self._drag_at(x, y)
        return False

    def _drag_end(self, addr, x, y):
        zone, mon = self._edge_zone(x, y) if self._drag_mons else ("none", "")
        return self._drag_drop(zone, mon, addr)

    def _drag_at(self, x, y):
        """레이아웃 바(위쪽 가운데로 끌면 내려오는 칸) — 커서 자리마다"""
        mon = None
        for m in self._drag_mons:
            sc = m.get("scale", 1.0) or 1.0
            mw, mh = m["width"] / sc, m["height"] / sc
            if m["x"] <= x < m["x"] + mw and m["y"] <= y < m["y"] + mh:
                mon = (m, mw, mh)
        if mon is None:
            return True
        m, mw, mh = mon
        dx, dy = abs(x - (m["x"] + mw / 2)), y - m["y"]
        bar = self.topbar
        if not self._drag_armed:
            # 끌기를 띠 안에서 시작했다(최대화된 창·위쪽에 붙은 창의 제목줄) — 옆으로 조금 옮기고 놓았는데
            #   바의 칸에 스냅되지 않게, 띠 밖으로 한 번 나갔다 들어와야 바를 띄운다
            if dy < self.BAND_Y and dx < mw * self.BAND_X:
                return True
            self._drag_armed = True
        if not bar.get_visible():
            if dy < self.BAND_Y and dx < mw * self.BAND_X:
                from gi.repository import Gdk
                geo = Gdk.Rectangle()
                geo.x, geo.y, geo.width, geo.height = int(m["x"]), int(m["y"]), int(mw), int(mh)
                bar.show_on(geo)
            return True
        if dy > self.LEAVE_Y or dx > mw * self.LEAVE_X:
            bar.hide()
            bar.set_hot(None)
            self._bar_hit = None
            if self.preview is not None:
                self.preview.hide_now()
            return True
        hit = bar.zone_at(x, y)
        if hit != self._bar_hit:
            self._bar_hit = hit
            bar.set_hot(hit[0] if hit else None)
            rect = self.zone_rect(hit[0], mon_name=m.get("name")) if hit else None
            if rect is None and dy <= 4:
                rect = self.zone_rect("max", mon_name=m.get("name"))   # 바 밖 맨 위 = 최대화
            if self.preview is not None:
                if rect:
                    self.preview.show_at(*rect)
                else:
                    self.preview.hide_now()
        return True

    def _stop_poll(self):
        if self._poll_src:
            GLib.source_remove(self._poll_src)
            self._poll_src = 0
        hit = self._bar_hit if (self.topbar is not None and self.topbar.get_visible()) else None
        bar_open = self.topbar is not None and self.topbar.get_visible()
        if self.topbar is not None:
            self.topbar.hide()
            self.topbar.set_hot(None)
        self._bar_hit = None
        return bar_open, hit

    def _drag_abort(self):
        """끌기가 놓기 없이 끝났다 (끄는 창이 닫힘 등) — 폴링·레이아웃 바·스냅 미리보기를 걷는다"""
        self._stop_poll()
        if self.preview is not None:
            self.preview.hide_now()
        if self._drag_addr:
            self.drag_start.pop(self._drag_addr, None)
        self._drag_addr = None

    def _drag_closed(self, addr):
        if addr == self._drag_addr:
            dbg(f"[win] 끄던 창 {addr} 이 닫혔다 — 스냅 폴링을 멈춘다")
            self._drag_abort()
        return False

    def _drag_zone(self, zone, mon_name):
        if getattr(self, "_drag_fixed", False):
            return False
        if self.topbar is not None and self.topbar.get_visible():
            return False                                 # 바가 내려와 있으면 미리보기는 바 쪽이 정한다
        rect = self.zone_rect(zone, mon_name=mon_name) if zone != "none" else None
        if rect and self.preview is not None:
            self.preview.show_at(*rect)
        elif self.preview is not None:
            self.preview.hide_now()
        return False

    def _drag_drop(self, zone, mon_name, addr):
        if getattr(self, "_drag_fixed", False):
            self._drag_fixed = False
            self._drag_abort()
            return False
        bar_open, hit = self._stop_poll()
        self._drag_addr = None
        if self.preview is not None:
            self.preview.hide_now()
        if bar_open and (hit or zone == "max"):
            # 레이아웃 바가 내려와 있었다 — 가리킨 칸으로, 칸 밖 맨 위면 최대화
            if hit:
                self.snap_to(addr, hit[0], mon_name=mon_name, rest=hit[1])
            else:
                self.snap_to(addr, "max", mon_name=mon_name)
            self.drag_start.pop(addr, None)
            return False
        # (바가 내려와 있었어도 칸 밖에 놓았으면 보통 놓기처럼 — 스냅된 창은 아래에서 원래 크기로)
        if zone != "none":
            self.snap_to(addr, zone, mon_name=mon_name)
        elif addr in self.snapped and (c0 := self._client(addr)) and self._in_zone(c0, self.snapped[addr]):
            # 제목줄을 누르다 조금 흔들렸을 뿐 — 스냅을 풀지 않고 칸에 다시 맞춘다
            self._fit_back(addr, c0)
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

    def _fit_back(self, addr, c):
        rect = self.zone_rect(self.snapped[addr], c)
        if rect:
            x, y, w, h = rect
            bar = self._bar(c)
            self.hypr.dispatch(f"movewindowpixel exact {int(x)} {int(y + bar)},address:{addr}")

    # ── 창 크기 기억 ──
    def _poll(self):
        """닫힐 때는 창 정보가 이미 없다 → 주기적으로 크기를 봐 둔다."""
        for c in self.hypr.query("clients") or []:
            if c.get("address"):
                self.known[c["address"]] = c
        self._note_monitors()
        return True

    def _note_monitors(self):
        mons = self.hypr.query("monitors") or []
        # 덮어쓰지 않고 갱신 — 빠진 모니터의 기록은 _monitor_gone 이 쓸 때까지 남긴다
        #   (빠진 뒤에 온 다른 이벤트로 여기가 먼저 불릴 수 있다)
        for m in mons:
            self.mon_ws[m.get("name")] = (m.get("activeWorkspace") or {}).get("id")
        return False

    def _monitor_gone(self, name):
        """모니터가 빠졌다 — 윈도우처럼 그 화면에 보이던 창을 남은 화면으로 옮긴다.
        Hyprland 는 워크스페이스만 옮겨서, 창이 안 보이는 워크스페이스에 화면 밖 좌표로 남는다."""
        ws = self.mon_ws.pop(name, None)
        mons = self.hypr.query("monitors") or []
        if not mons:
            return False
        target = next((m for m in mons if m.get("focused")), mons[0])
        tws = (target.get("activeWorkspace") or {}).get("id")
        for c in self.hypr.query("clients") or []:
            addr = c.get("address")
            if not addr:
                continue
            was_there = ws is not None and (c.get("workspace") or {}).get("id") == ws
            if was_there and tws is not None:
                self.hypr.dispatch(f"movetoworkspacesilent {tws},address:{addr}")
            zone = self.snapped.get(addr)
            if zone and (not was_there or c.get("fullscreen") or is_max(c)):
                continue                              # 남은 모니터의 창·최대화된 창은 그대로 둔다
            if zone:
                GLib.timeout_add(150, lambda a=addr, z=zone: self.snap_to(a, z, mon_name=target.get("name"),
                                                                           assist=False) and False)
            else:
                GLib.timeout_add(150, self._keep_visible, addr)
        GLib.timeout_add(300, self._note_monitors)
        return False

    def on_event(self, name, arg):
        if name == "activewindowv2" and self._desk_shown:
            a = "0x" + arg.strip()
            # 바탕 화면 보기 뒤 다른 창을 골랐다 (최소화하며 오가는 초점은 빼고)
            if a != "0x" and a not in self._desk_shown["addrs"] and time.monotonic() - self._desk_shown["t"] > 1:
                self._desk_shown = None
        if name == "sekaidrag":
            p = arg.strip().split(",")
            try:
                if p[0] == "start" and len(p) == 2:
                    GLib.idle_add(self._drag_start, "0x" + p[1], False)
                elif p[0] == "move" and len(p) == 3:
                    GLib.idle_add(self._drag_move, int(p[1]), int(p[2]))
                elif p[0] == "end" and len(p) == 4:
                    GLib.idle_add(self._drag_end, "0x" + p[1], int(p[2]), int(p[3]))
                elif p[0] == "cancel":
                    GLib.idle_add(self._drag_drop, "none", "", "0x0")
            except ValueError:
                pass
        elif name == "sekaisnapstart":
            GLib.idle_add(self._drag_start, "0x" + arg.strip())
        elif name == "sekaisnap":
            zone, _, mon = arg.partition(",")
            GLib.idle_add(self._drag_zone, zone, mon.strip())
        elif name == "sekaisnapdrop":
            parts = arg.strip().split(",")
            if len(parts) == 3:
                GLib.idle_add(self._drag_drop, parts[0], parts[1], "0x" + parts[2])
        elif name == "sekaiwinmenu":
            parts = arg.strip().split(",")
            if len(parts) == 3 and self.winmenu is not None:
                try:
                    x, y = int(parts[1]), int(parts[2])
                except ValueError:
                    return
                GLib.idle_add(self.winmenu.open_for, "0x" + parts[0], x, y)
        elif name == "openwindow":
            addr = "0x" + arg.split(",", 1)[0]
            self._desk_shown = None                       # 바탕 화면 보기 뒤 새 창 — 되살릴 목록은 버린다
            GLib.timeout_add(60, self._opened, addr)
        elif name == "closewindow":
            addr = "0x" + arg.strip()
            # 끄는 중에 닫히면 Hyprland 는 놓기(sekaisnapdrop)를 보내지 않는다 — 끌기 이벤트와 같은 순서로(idle)
            GLib.idle_add(self._drag_closed, addr)
            c = self.known.pop(addr, None)
            if c:
                self._closed(c)                       # 스냅 기록을 지우기 전에 (스냅된 창인지 본다)
            self.mains.discard(addr)
            self.saved.pop(addr, None)
            self.snapped.pop(addr, None)
        elif name in ("movewindow", "movewindowv2", "activewindowv2"):
            # 창 정보를 봐 둔다 — 한꺼번에 오는 이벤트는 한 번으로 모은다. 제목 이벤트는 뺐다: 크기 기억과
            #   상관없고, 제목이 계속 바뀌는 창이 있으면 이벤트마다 동기 조회를 했다
            #   (초점 창의 제목이 바뀌면 activewindowv2 도 오지만 이렇게 모이면 200ms 에 한 번이다)
            if not self._poll_one_src:
                self._poll_one_src = GLib.timeout_add(200, self._poll_one)
        if name == "monitorremoved":
            GLib.timeout_add(300, self._monitor_gone, arg.strip())
        elif name in ("workspace", "workspacev2", "focusedmon", "moveworkspace", "moveworkspacev2",
                      "monitoradded", "monitoraddedv2"):
            GLib.idle_add(self._note_monitors)

    def _poll_one(self):
        self._poll_one_src = 0
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
        if cls in SKIP_CLASSES or not c.get("floating") or c.get("fullscreen") or is_max(c):
            return
        if c["address"] not in self.mains:
            return                          # 대화상자 — 앱을 끄면 창이 한꺼번에 닫혀 대화상자가 "마지막 창"일 수 있다
        w, h = c.get("size", [0, 0])
        if c["address"] in self.snapped:
            g = self.saved.get(c["address"])
            if not g:
                return
            w, h = g[2], g[3]               # 스냅된 채 닫았다 — 칸 크기가 아니라 스냅 전 크기를
        clients = self.hypr.query("clients") or []
        if not self._first_of_class(c, clients):
            return                          # 같은 앱의 다른 창이 남아 있다 — 대화상자일 수 있다
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
        if not self._first_of_class(c, clients):
            return False
        self.mains.add(addr)
        cls = c.get("class") or ""
        want = self.sizes.get(cls)
        if not want or not c.get("floating") or c.get("fullscreen") or is_max(c):
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
