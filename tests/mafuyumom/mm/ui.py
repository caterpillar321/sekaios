"""화면의 위젯을 이름·역할로 찾아 진짜 마우스로 누른다 (VM 안의 mm_agent.py → AT-SPI)."""
import json
import os
import shlex
import time

from . import remote

AGENT_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "mm_agent.py")
AGENT = "/tmp/mm_agent.py"


class UI:
    def __init__(self, q):
        self.q = q
        self.pushed = False

    def _ensure(self):
        if not self.pushed:
            remote.push(AGENT_LOCAL, AGENT)
            self.pushed = True

    def call(self, req, timeout=30):
        self._ensure()
        r = remote.run(f"python3 {AGENT} {shlex.quote(json.dumps(req, ensure_ascii=False))}", timeout=timeout)
        try:
            return json.loads(r.out.strip().splitlines()[-1]) if r.out.strip() else None
        except (ValueError, IndexError):
            return None

    def find(self, app=None, role=None, name=None, name_re=None, all=False, showing=True):
        req = {"op": "find", "all": all, "showing": showing}
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

    def wait(self, timeout=10, **kw):
        end = time.time() + timeout
        while True:
            n = self.find(**kw)
            if n and n.get("cx") is not None:
                return n
            if time.time() > end:
                return None
            time.sleep(0.5)

    def click(self, timeout=10, button="left", n=1, **kw):
        """찾아서 그 가운데를 누른다 — 못 찾으면 None"""
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
