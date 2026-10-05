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


def test(name, suite, timeout=180, slow=False, quick=False):
    """slow: 재부팅·긴 무작위 — full 에서만. quick: 영역마다 대표 하나씩 — mafuyumom quick (중간 점검, ~1분 반)"""
    def deco(fn):
        TESTS.append({"name": name, "suite": suite, "fn": fn, "timeout": timeout, "slow": slow, "quick": quick,
                      "id": f"{suite}/{fn.__name__}"})
        return fn
    return deco


class T:
    """시험에 넘기는 도구 상자"""

    def __init__(self, q, outdir, tid, ui=None):
        self.q = q
        self.ui = ui or UI(q)
        self.outdir = outdir
        self.tid = tid
        self.shots = []
        self.notes = []
        self.findings = []        # 시험은 통과해도 남길 발견 (예: 이름 없는 단추)
        self.cleanups = []        # 시험이 실패해도 끝에 돌리는 정리 (다음 시험에 창이 남지 않게)

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
        """SekaiOS 앱은 python3 로 돌아 이름(comm)이 python3 — 명령줄의 /usr/bin/이름 으로도 찾는다"""
        self.sh(f"pkill -x {proc}; pkill -f '[/]usr/bin/{proc}( |$)'; true")

    def after(self, fn):
        """끝에 꼭 할 정리 — 문자열이면 VM 안 셸 명령"""
        self.cleanups.append(fn)

    def auth(self, timeout=20):
        """관리자 인증 창(사용자 계정 컨트롤)이 뜨면 암호를 넣고 예 — 창이 안 뜨면(인증을 기억 중) False.
        창은 미끄러져 들어온다 — 움직이는 중에 치면 글자가 빠져 "암호가 올바르지 않습니다"가 된다"""
        from . import config
        f = self.ui.wait(app="polkit-agent", role="password text", timeout=timeout)
        if not f:
            return False
        for _ in range(10):                               # 자리가 멈출 때까지
            time.sleep(0.3)
            g = self.ui.find(app="polkit-agent", role="password text")
            if not g:
                break
            if (g["cx"], g["cy"]) == (f["cx"], f["cy"]):
                break
            f = g
        self.shot("인증창")
        for attempt in range(2):
            self.click(f["cx"], f["cy"])
            time.sleep(0.3)
            self.key("ctrl-a")
            self.key("backspace")
            self.type(config.PASSWORD)
            want = len(config.PASSWORD)
            n = self.wait(lambda: len(self.ui.text(app="polkit-agent", role="password text") or "") == want and want, 3) \
                or len(self.ui.text(app="polkit-agent", role="password text") or "")
            if n != want:
                self.note(f"인증 창에 친 글자 {n}/{len(config.PASSWORD)} — 다시")
                continue
            self.key("ret")
            if self.wait(lambda: not self.ui.find(app="polkit-agent", role="password text"), 10):
                return True
            self.note("인증 창이 닫히지 않았다 (암호 거절?) — 다시")
            f = self.ui.find(app="polkit-agent", role="password text") or f
        self.fail("관리자 인증을 통과하지 못했다")

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
    ui = UI(q)                    # 에이전트 하나를 끝까지 (요청마다 띄우면 앱에 pidfd 가 쌓인다 — mm_agent.serve)
    try:
        return _run(selected, outdir, q, ui, results)
    finally:
        ui.stop()


def _run(selected, outdir, q, ui, results):
    for tc in selected:
        print(f"▶ {tc['id']} — {tc['name']}", flush=True)
        t = T(q, outdir, tc["id"], ui)
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
            if type(e).__name__ == "RealOnly":          # VM 에만 있는 동작 (실기 모드) — 실패가 아니라 건너뜀
                status, msg = "skip", str(e)
            else:
                status, msg = "error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"
        dt = time.time() - t0
        if status in ("fail", "error"):
            t.shot("끝")                                # 정리하기 전 화면
        for fn in reversed(t.cleanups):
            try:
                t.sh(fn) if isinstance(fn, str) else fn()
            except Exception as e:
                t.notes.append(f"(정리 실패: {e})")
        after = checks.probe(since - 1)
        probs = checks.problems(before, after)
        tb = checks.tracebacks(since - 1) if after.get("tb", 0) > before.get("tb", 0) else ""
        if probs and status not in ("fail", "error"):
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
