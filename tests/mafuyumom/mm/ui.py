"""화면의 위젯을 이름·역할로 찾아 진짜 마우스로 누른다 (VM 안의 mm_agent.py → AT-SPI)."""
import json
import os
import select
import subprocess
import time

from . import remote

AGENT_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "mm_agent.py")
AGENT = "/tmp/mm_agent.py"


class UI:
    def __init__(self, q):
        self.q = q
        self.pushed = False
        self.proc = None          # VM 안에서 계속 도는 에이전트 (python3 mm_agent.py --serve)

    def _ensure(self):
        if not self.pushed:
            remote.push(AGENT_LOCAL, AGENT)
            self.pushed = True

    def _start(self):
        self._ensure()
        self.proc = subprocess.Popen(remote._ssh_base() + [f"{remote.SESSION_ENV}; exec python3 {AGENT} --serve"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        if self._readline(30) is None:                   # {"ready": true}
            self.stop()
            self.pushed = False                          # 다시 부팅해 /tmp 가 비었을 수 있다
            return False
        return True

    def _readline(self, timeout):
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            return None
        line = self.proc.stdout.readline()
        return line or None

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait(5)
            except Exception:
                pass
        self.proc = None

    def call(self, req, timeout=30):
        for _ in range(2):                               # 끊겼으면(다시 부팅 등) 한 번 새로 띄워
            if (self.proc is None or self.proc.poll() is not None) and not self._start():
                continue
            try:
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.stop()
                continue
            line = self._readline(timeout)
            if line is None:                             # 멈췄다 — 버리고 다음 요청에서 새로
                self.stop()
                return None
            try:
                out = json.loads(line)
            except ValueError:
                return None
            if isinstance(out, dict) and "error" in out and len(out) == 1:
                return None
            return out
        return None

    def find(self, app=None, role=None, name=None, name_re=None, all=False, showing=True, frame=None, not_frame=None):
        req = {"op": "find", "all": all, "showing": showing}
        if frame:
            req["frame"] = frame
        if not_frame:
            req["not_frame"] = not_frame
        if app:
            req["app"] = app
        if role:
            req["role"] = role
        if name is not None:
            req["name"] = name
        if name_re:
            req["name_re"] = name_re
        r = self.call(req) or []
        return r if all else (r[0] if r else None)

    def wait(self, timeout=10, sensitive=False, **kw):
        """보일 때까지 — sensitive=True 면 눌 수 있게(꺼져 있지 않게) 될 때까지"""
        end = time.time() + timeout
        while True:
            n = self.find(**kw)
            if n and n.get("cx") is not None and (not sensitive or "sensitive" in n.get("states", [])):
                return n
            if time.time() > end:
                return None
            time.sleep(0.5)

    def click(self, timeout=10, button="left", n=1, **kw):
        """찾아서 그 가운데를 누른다 — 못 찾으면 None (눌 수 있게 될 때까지 기다린다)"""
        if kw.get("role") in ("button", "push button", "toggle button", "check box", "radio button", "menu item"):
            kw.setdefault("sensitive", True)        # 단추는 꺼져 있으면(확인 중 등) 켜질 때까지
        w = self.wait(timeout=timeout, **kw)
        if not w:
            return None
        self.q.click(w["cx"], w["cy"], btn=button, n=n)
        return w

    def text(self, **kw):
        r = self.call(dict(op="text", **kw)) or {}
        return r.get("text")

    def focus(self):
        return self.call({"op": "focus"})

    def tree(self, app, depth=8):
        return self.call({"op": "tree", "app": app, "depth": depth}) or []

    def unnamed(self, app):
        """이름 없는 단추·스위치 — 화면 읽기(내레이터)가 "단추"라고만 읽는 것"""
        out = []
        for role in ("push button", "button", "toggle button", "check box", "radio button", "menu item"):
            for n in self.find(app=app, role=role, all=True) or []:
                if not n["name"].strip():
                    out.append(n)
        return out
