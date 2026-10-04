"""시험 등록·실행 — @test 로 등록하고, 실행기는 시험마다 t(도구 상자)를 넘긴다.

  @test("시작 메뉴로 계산기 열기", suite="session")
  def _(t):
      t.key("meta_l")
      t.ui.click(app="sekai-panel", role="text")          # 검색 칸
      t.type("calc"); t.key("ret")
      w = t.window("org.sekaios.Calculator")              # 창이 뜰 때까지
      t.expect(w, "계산기 창이 떴다")

  실패: t.expect 가 거짓이거나 t.fail(…) — 예외는 "오류". 불변식(checks)이 깨지면 "경고"를 붙인다.
"""
import json
import os
import time
import traceback

from . import checks, remote, vm
from .ui import UI

TESTS = []


class Fail(Exception):
    pass


class Skip(Exception):
    pass


def test(name, suite, timeout=180, slow=False):
    def deco(fn):
        TESTS.append({"name": name, "suite": suite, "fn": fn, "timeout": timeout, "slow": slow,
                      "id": f"{suite}/{fn.__name__}"})
        return fn
    return deco


class T:
    """시험에 넘기는 도구 상자"""

    def __init__(self, q, outdir, tid):
        self.q = q
        self.ui = UI(q)
        self.outdir = outdir
        self.tid = tid
        self.shots = []
        self.notes = []
        self.findings = []        # 시험은 통과해도 남길 발견 (예: 이름 없는 단추)

    # ── 입력 ──
    def key(self, *combos, **kw):
        self.q.key(*combos, **kw)

    def type(self, text):
        self.q.type(text)

    def click(self, x, y, **kw):
        self.q.click(x, y, **kw)

    def drag(self, *a, **kw):
        self.q.drag(*a, **kw)

    # ── VM 안 ──
    def sh(self, cmd, timeout=60):
        return remote.run(cmd, timeout=timeout)

    def root(self, cmd, timeout=300):
        return remote.root(cmd, timeout=timeout)

    def hypr(self, what):
        r = remote.run(f"hyprctl -j {what}", timeout=20)
        try:
            return json.loads(r.out)
        except ValueError:
            return None

    def clients(self, cls=None):
        cs = [c for c in (self.hypr("clients") or []) if c.get("mapped", True)]
        return [c for c in cs if cls is None or c.get("class") == cls]

    def window(self, cls, timeout=15, title=None):
        """그 클래스의 창이 뜰 때까지 (창 정보, 없으면 None)"""
        end = time.time() + timeout
        while time.time() < end:
            for c in self.clients(cls):
                if title is None or title in (c.get("title") or ""):
                    return c
            time.sleep(0.5)
        return None

    def gone(self, cls, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if not self.clients(cls):
                return True
            time.sleep(0.5)
        return False

    def launch(self, cmd, cls, timeout=20):
        self.sh(f"hyprctl dispatch exec {cmd!r} >/dev/null")
        return self.window(cls, timeout)

    def close(self, cls):
        for c in self.clients(cls):
            self.sh(f"hyprctl dispatch closewindow address:{c['address']} >/dev/null")
        return self.gone(cls)

    def kill(self, proc):
        self.sh(f"pkill -x {proc}; true")

    # ── 판정 ──
    def wait(self, cond, timeout=10, every=0.5):
        end = time.time() + timeout
        while True:
            v = cond()
            if v:
                return v
            if time.time() > end:
                return v
            time.sleep(every)

    def expect(self, cond, what):
        if not cond:
            raise Fail(what)
        self.notes.append("✓ " + what)

    def fail(self, what):
        raise Fail(what)

    def skip(self, why):
        raise Skip(why)

    def note(self, text):
        self.notes.append(text)

    def finding(self, text):
        self.findings.append(text)

    def shot(self, name):
        os.makedirs(os.path.join(self.outdir, "shots"), exist_ok=True)
        fn = f"{self.tid.replace('/', '_')}_{len(self.shots):02d}_{name}.png"
        try:
            self.q.shot(os.path.join(self.outdir, "shots", fn))
            self.shots.append(fn)
        except Exception as e:
            self.notes.append(f"(스크린숏 실패: {e})")
        return fn


def run(selected, outdir, q):
    results = []
    for tc in selected:
        print(f"▶ {tc['id']} — {tc['name']}", flush=True)
        t = T(q, outdir, tc["id"])
        since = time.time()
        before = checks.probe(since - 1)
        status, msg = "pass", ""
        t0 = time.time()
        try:
            tc["fn"](t)
        except Fail as e:
            status, msg = "fail", str(e)
        except Skip as e:
            status, msg = "skip", str(e)
        except Exception as e:
            status, msg = "error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"
        dt = time.time() - t0
        after = checks.probe(since - 1)
        probs = checks.problems(before, after)
        tb = checks.tracebacks(since - 1) if after.get("tb", 0) > before.get("tb", 0) else ""
        if status in ("fail", "error") or probs:
            t.shot("끝")
        if probs and status == "pass":
            status = "warn"
        # 다음 시험이 깨끗하게 시작하도록 — 열린 메뉴·창 닫기
        q.key("esc")
        icon = {"pass": "✓", "warn": "△", "fail": "✗", "error": "‼", "skip": "−"}[status]
        print(f"  {icon} {status} ({dt:.1f}s){' — ' + msg.splitlines()[0] if msg else ''}"
              f"{' · ' + ', '.join(probs) if probs else ''}", flush=True)
        results.append({"id": tc["id"], "name": tc["name"], "suite": tc["suite"], "status": status, "msg": msg,
                        "secs": round(dt, 1), "problems": probs, "traceback": tb, "notes": t.notes,
                        "findings": t.findings, "shots": t.shots})
        # 세션이 무너졌으면 다시 로그인해 다음 시험이 이어지게
        if not vm.session_up():
            print("  ! 세션이 없다 — 다시 로그인", flush=True)
            vm.login(q)
    return results
