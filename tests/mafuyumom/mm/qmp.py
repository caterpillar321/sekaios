"""QMP — VM 밖에서 진짜 키보드·마우스처럼 넣고, 화면을 찍는다."""
import json
import os
import socket
import tempfile
import time

from . import config

CHAR = {" ": "spc", "\n": "ret", "\t": "tab", "-": "minus", "=": "equal", "[": "bracket_left",
        "]": "bracket_right", ";": "semicolon", "'": "apostrophe", "`": "grave_accent",
        "\\": "backslash", ",": "comma", ".": "dot", "/": "slash"}
SHIFT = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
         "(": "9", ")": "0", "_": "minus", "+": "equal", "{": "bracket_left", "}": "bracket_right",
         ":": "semicolon", '"': "apostrophe", "~": "grave_accent", "|": "backslash",
         "<": "comma", ">": "dot", "?": "slash"}


class QMPError(Exception):
    pass


def keys_for(ch):
    if ch.isascii() and ch.isalnum():
        return ["shift", ch.lower()] if ch.isupper() else [ch]
    if ch in CHAR:
        return [CHAR[ch]]
    if ch in SHIFT:
        k = SHIFT[ch]
        return ["shift", CHAR.get(k, k)]
    raise QMPError(f"입력할 수 없는 글자: {ch!r} (한글은 입력기로 — type 은 ASCII 만)")


class QMP:
    def __init__(self, sock):
        self.sock_path = sock
        self.s = None
        self.f = None

    def connect(self, timeout=20):
        end = time.time() + timeout
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX)
                self.s.connect(self.sock_path)
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.3)
        self.f = self.s.makefile("rwb")
        self._read()
        self.cmd("qmp_capabilities")
        return self

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def _read(self):
        while True:
            m = json.loads(self.f.readline())
            if "event" not in m:
                return m

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.f.write(json.dumps(msg).encode() + b"\n")
        self.f.flush()
        r = self._read()
        if "error" in r:
            raise QMPError(r["error"])
        return r.get("return")

    # ── 화면 ──
    def shot(self, path):
        """화면을 PNG 로 (전체 해상도)"""
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as t:
            tmp = t.name
        try:
            self.cmd("screendump", filename=tmp)
            with Image.open(tmp) as im:
                im.save(path)
        finally:
            os.unlink(tmp)
        return path

    # ── 키보드 ──
    def key(self, *combos, hold=100, gap=0.15):
        """key("meta_l-e", "ret") — QEMU qcode 이름, - 로 동시 누름"""
        for c in combos:
            names = [c] if c == "minus" else c.split("-")
            self.cmd("send-key", keys=[{"type": "qcode", "data": n} for n in names], **{"hold-time": hold})
            time.sleep(gap)

    def keydown(self, *names):
        self.cmd("input-send-event", events=[{"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": n}}}
                                             for n in names])

    def keyup(self, *names):
        self.cmd("input-send-event", events=[{"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": n}}}
                                             for n in names])

    def type(self, text, gap=0.03):
        for ch in text:
            self.cmd("send-key", keys=[{"type": "qcode", "data": n} for n in keys_for(ch)], **{"hold-time": 40})
            time.sleep(gap)

    # ── 마우스 (화면 좌표 px) ──
    def _abs(self, x, y):
        w, h = config.SCREEN
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        return [{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / (w - 1))}},
                {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / (h - 1))}}]

    def move(self, x, y):
        self.cmd("input-send-event", events=self._abs(x, y))

    def button(self, down, btn="left"):
        self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": btn}}])

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
        b = "wheel-down" if clicks > 0 else "wheel-up"
        for _ in range(abs(clicks)):
            self.button(True, b)
            self.button(False, b)
            time.sleep(0.05)
