"""실기 모드 (MM_REAL) — QMP 대신 실제 PC 의 가상 입력(real/mm-uinput.py)과 화면 찍기(grim).
QMP 와 같은 함수 이름이라 시험 스위트를 그대로 쓴다. VM 에만 있는 것(cmd: 절전·전원 단추)은 RealOnly 로 알린다."""
import subprocess
import threading
import time

from . import config, remote
from .qmp import keys_for

# QEMU qcode 이름 → 리눅스 KEY_* 번호
KEY = {"esc": 1, "minus": 12, "equal": 13, "backspace": 14, "tab": 15, "ret": 28, "ctrl": 29, "semicolon": 39,
       "apostrophe": 40, "grave_accent": 41, "shift": 42, "backslash": 43, "comma": 51, "dot": 52, "slash": 53,
       "shift_r": 54, "alt": 56, "spc": 57, "caps_lock": 58, "ctrl_r": 97, "alt_r": 100, "home": 102, "up": 103,
       "pgup": 104, "left": 105, "right": 106, "end": 107, "down": 108, "pgdn": 109, "insert": 110, "delete": 111,
       "meta_l": 125, "meta_r": 126, "menu": 127, "print": 99, "sysrq": 99, "bracket_left": 26,
       "bracket_right": 27, "hangeul": 122, "hanja": 123, "plus": 78, "kp_add": 78, "kp_enter": 96}
for i, ch in enumerate("1234567890"):
    KEY[ch] = 2 + i
for row, start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for i, ch in enumerate(row):
        KEY[ch] = start + i
for i in range(1, 11):
    KEY[f"f{i}"] = 58 + i
KEY["f11"], KEY["f12"] = 87, 88
BTN = {"left": 272, "right": 273, "middle": 274}


def logical_screen():
    """시험대의 논리 화면 크기 (배율을 나눈 값, 모니터 전체를 감싸는 크기) — 가상 태블릿의 0..32767 이 여기에 맞는다.
    배율 160% 의 2560×1600 노트북이면 1600×1000. 화면 찍기도 같은 크기(grim -s 1)라 좌표가 하나로 맞는다."""
    out = remote.run("hyprctl -j monitors", timeout=20).out
    import json
    try:
        mons = [m for m in json.loads(out) if not m.get("disabled")]
    except ValueError:
        return config.SCREEN
    if not mons:
        return config.SCREEN
    def size(m):
        w, h = m["width"] / m["scale"], m["height"] / m["scale"]
        return (h, w) if m.get("transform", 0) % 2 else (w, h)
    w = max(m["x"] + size(m)[0] for m in mons)
    h = max(m["y"] + size(m)[1] for m in mons)
    return round(w), round(h)


class RealOnly(Exception):
    """VM(QEMU)에만 있는 동작 — 실기 모드에선 그 시험을 건너뛴다"""


KEEPAWAKE_KEY = 240          # KEY_UNKNOWN — 글자·단축키·고정 키 어디에도 안 걸리는 키
KEEPAWAKE_EVERY = 60         # 이만큼 입력이 없으면 한 번 (자리 비움 → 화면 끄기·잠금·절전 타이머를 되돌린다)


class RealIO:
    """keepawake=True 면 입력이 뜸할 때 KEEPAWAKE_KEY 를 눌러 시험대가 잠들지 않게 한다 —
    SSH 명령은 입력으로 치지 않아, 시험 사이·조사하는 동안 15분 뒤 절전에 들어갔다 (2026-10-06 노트북)."""
    def __init__(self, keepawake=True):
        self.p = None
        self.lock = threading.Lock()
        self.last = time.time()
        if keepawake:
            threading.Thread(target=self._keepawake, daemon=True).start()

    def _keepawake(self):
        while True:
            time.sleep(10)
            if self.p is None or self.p.poll() is not None or time.time() - self.last < KEEPAWAKE_EVERY:
                continue
            try:
                self._send(f"k {KEEPAWAKE_KEY} 1")
                self._send(f"k {KEEPAWAKE_KEY} 0")
            except Exception:
                pass

    def connect(self, timeout=20):
        if not config.REAL_SCREEN_SET:
            config.SCREEN = logical_screen()
        remote.push(config.REAL_UINPUT, "/tmp/mm-uinput.py")
        self.p = subprocess.Popen(remote._ssh_base() + ["sudo -S -p '' python3 /tmp/mm-uinput.py"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.p.stdin.write(config.PASSWORD + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline().strip()
        if line != "ready":
            raise RuntimeError(f"가상 입력을 띄우지 못했습니다 ({line!r})")
        return self

    def close(self):
        if self.p and self.p.poll() is None:
            try:
                self.p.stdin.write("q\n")
                self.p.stdin.flush()
                self.p.wait(5)
            except Exception:
                self.p.kill()

    def _send(self, line):
        with self.lock:
            if self.p is None or self.p.poll() is not None:
                self.connect()
            self.p.stdin.write(line + "\n")
            self.p.stdin.flush()
            if self.p.stdout.readline().strip() != "ok":
                raise RuntimeError(f"가상 입력이 답하지 않습니다: {line}")
            self.last = time.time()

    def cmd(self, name, **args):
        raise RealOnly(f"QMP 명령 {name} 은 VM 에서만 (실기 모드)")

    # ── 화면 ──
    def shot(self, path):
        r = subprocess.run(remote._ssh_base() + [f"{remote.SESSION_ENV}; grim -s 1 -"], capture_output=True, timeout=60)
        if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
            raise RuntimeError("화면을 찍지 못했습니다: " + r.stderr.decode(errors="replace")[-200:])
        with open(path, "wb") as f:
            f.write(r.stdout)
        return path

    # ── 키보드 ──
    def _code(self, n):
        if n not in KEY:
            raise RuntimeError(f"모르는 키 이름: {n}")
        return KEY[n]

    def key(self, *combos, hold=100, gap=0.15):
        for c in combos:
            names = [c] if c == "minus" else c.split("-")
            for n in names:
                self._send(f"k {self._code(n)} 1")
            time.sleep(hold / 1000)
            for n in reversed(names):
                self._send(f"k {self._code(n)} 0")
            time.sleep(gap)

    def keydown(self, *names):
        for n in names:
            self._send(f"k {self._code(n)} 1")

    def keyup(self, *names):
        for n in names:
            self._send(f"k {self._code(n)} 0")

    def type(self, text, gap=0.03):
        for ch in text:
            names = keys_for(ch)
            for n in names:
                self._send(f"k {self._code(n)} 1")
            time.sleep(0.02)
            for n in reversed(names):
                self._send(f"k {self._code(n)} 0")
            time.sleep(gap)
        time.sleep(0.2)

    # ── 마우스 (화면 좌표 px, QMP 와 같은 계산) ──
    def move(self, x, y):
        w, h = config.SCREEN
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        self._send(f"a {int(x * 32767 / (w - 1))} {int(y * 32767 / (h - 1))}")

    def button(self, down, btn="left"):
        if btn in ("wheel-up", "wheel-down"):
            if down:
                self._send(f"w {1 if btn == 'wheel-up' else -1}")
            return
        self._send(f"b {BTN[btn]} {1 if down else 0}")

    def click(self, x, y, btn="left", n=1):
        self.move(x, y)
        time.sleep(0.08)
        for _ in range(n):
            self.button(True, btn)
            time.sleep(0.08 if n > 1 else 0.15)
            self.button(False, btn)
            time.sleep(0.08)

    def drag(self, x1, y1, x2, y2, steps=20, hold=0.0):
        self.move(x1, y1)
        time.sleep(0.08)
        self.button(True)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
            time.sleep(0.02)
        time.sleep(hold)
        self.button(False)

    def scroll(self, x, y, clicks=1):
        self.move(x, y)
        for _ in range(abs(clicks)):
            self.button(True, "wheel-down" if clicks > 0 else "wheel-up")
            time.sleep(0.05)
