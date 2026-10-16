"""라이브러리 › 업데이트 — apt 로 깐 일반 앱과 Flathub 앱의 새 판 (시스템 업데이트는 설정 › 업데이트, 윈도우처럼).

무엇이 '앱'인지는 sekaishell/aptapps.py — 스토어로 깔았든 터미널에서 apt install 로 깔았든 같다.
apt 앱은 sekai-apps upgrade(저장소 입구 sekai-apps-repo), Flathub 은 libflatpak 업데이트.
한 번에 하나씩 — 설치·제거 대기열(Jobs)과도 번갈아 (apt 는 동시에 못 돈다, Jobs.hold).
"""
import threading

from gi.repository import GLib

from sekaishell import appmgr, aptapps

from .widgets import feed_progress

RUNTIMES = "fp-runtimes"


class Updates:
    def __init__(self, cat, jobs):
        self.cat, self.jobs = cat, jobs
        # [{"key", "kind"(apt·flatpak), "name", "old", "new", "app"(스토어 목록의 App 또는 None),
        #   "pkg"(apt) · "refs"(flatpak), "desktop"(apt 앱의 .desktop 또는 None), "more"(함께 올라가는 짝 수)}]
        self.items = []
        self.scanned = False
        self.scanning = False
        self.note = None                # 다 세지 못했을 때 알릴 말
        self.queue = []                 # 기다리는 항목 key
        self.current = None             # (key, {"pct", "text", "error"})
        self.errors = {}                # key → 마지막 오류
        self._watch = []

    def watch(self, cb):
        self._watch.append(cb)

    def _notify(self):
        for cb in list(self._watch):
            cb()

    # ── 세기 ──
    def scan(self):
        if self.scanning or self.busy():
            return
        self.scanning = True
        self._notify()
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        items, note = [], None
        sysd, apps, _pins = aptapps.split()
        if sysd is None:
            note = "apt 로 깐 앱의 업데이트를 세지 못했습니다 — 다른 설치가 끝난 뒤 다시 확인해 주세요"
        for pkg, ch in sorted(apps.items()):
            own = next((c for c in ch if c[0] == pkg), ch[0])
            a = self.cat.by_pkg.get(pkg)
            a = a if a is not None and not a.is_flatpak else None
            name, desktop = aptapps.app_entry(pkg)
            items.append({"key": "apt:" + pkg, "kind": "apt", "pkg": pkg, "app": a, "desktop": desktop,
                          "name": a.name if a else name, "old": own[1], "new": own[2], "more": len(ch) - 1})
        fps = appmgr.flatpak_updates() if self.cat.flatpak else []
        if fps is None:
            note = note or "Flathub 에 연결하지 못했습니다 — 인터넷 연결을 확인해 주세요"
            fps = []
        for f in fps:
            if not f["app"]:
                continue
            a = self.cat.by_pkg.get(f["id"])
            a = a if a is not None and a.is_flatpak else None
            items.append({"key": "fp:" + f["id"], "kind": "flatpak", "refs": [f["ref"]], "app": a, "desktop": None,
                          "id": f["id"], "name": a.name if a else f["name"], "old": f["old"],
                          "new": (a.version() if a else None) or "", "more": 0})
        rts = [f["ref"] for f in fps if not f["app"]]
        if rts:
            items.append({"key": RUNTIMES, "kind": "flatpak", "refs": rts, "app": None, "desktop": None,
                          "name": "Flathub 앱이 함께 쓰는 구성 요소", "old": "", "new": f"런타임 {len(rts)}개",
                          "more": 0})
        GLib.idle_add(self._scanned, items, note)

    def _scanned(self, items, note):
        self.items, self.note = items, note
        self.scanning, self.scanned = False, True
        self._notify()
        return False

    # ── 올리기 ──
    def busy(self):
        return self.current is not None or bool(self.queue)

    def state(self, key):
        if self.current and self.current[0] == key:
            return "updating"
        return "queued" if key in self.queue else None

    def progress(self, key):
        return self.current[1] if self.current and self.current[0] == key else None

    def add(self, key):
        if self.state(key) is None and any(it["key"] == key for it in self.items):
            self.errors.pop(key, None)
            self.queue.append(key)
            self._notify()
            self._next()

    def add_all(self):
        for it in self.items:
            if self.state(it["key"]) is None:
                self.errors.pop(it["key"], None)
                self.queue.append(it["key"])
        self._notify()
        self._next()

    def _next(self):
        if self.current is not None or not self.queue:
            return
        if self.jobs.busy():
            # 설치·제거가 도는 중 — 끝나기를 기다린다
            GLib.timeout_add(1000, lambda: self._next() and False)
            return
        key = self.queue.pop(0)
        it = next((x for x in self.items if x["key"] == key), None)
        if it is None:
            self._next()
            return
        prog = {"pct": 0, "text": "준비하는 중…", "error": None}
        self.current = (key, prog)
        self.jobs.hold = True
        self._notify()

        def line(kind, rest):
            if feed_progress(prog, kind, rest):
                self._notify()
            return False

        def done(rc):
            cancelled = rc in appmgr.CANCELLED and not prog["error"]
            err = ("인증이 취소되었습니다" if cancelled else
                   prog["error"] or (f"비정상 종료했습니다 (코드 {rc})" if rc != 0 else None))
            if err:
                self.errors[key] = err
                if cancelled:
                    self.queue.clear()          # 인증을 닫았다 — 나머지도 묻지 않는다
            else:
                self.items = [x for x in self.items if x["key"] != key]
            self.current = None
            self.jobs.hold = False
            if not self.queue:
                self.cat.refresh_installed()
                self.jobs.kick()
                if not err:
                    self.scan()                 # 한 앱을 올리며 다른 것도 바뀌었을 수 있다 — 다시 센다
            self._notify()
            self._next()
            return False

        if it["kind"] == "apt":
            appmgr.run_helper(["upgrade", it["pkg"]], line, done, repo=True)
        else:
            appmgr.run_flatpak("update", it["refs"], line, done)
