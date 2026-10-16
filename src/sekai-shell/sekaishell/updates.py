"""업데이트 알림 — 받아 둔 패키지 목록으로 설치할 업데이트가 있는지 가끔 본다.

목록을 새로 받는 일은 apt-daily.timer 가 한다 (/etc/apt/apt.conf.d/20sekai-periodic).
여기서는 관리자 권한 없이 시뮬레이션만 해서 개수를 센다. 시스템(SekaiOS·데비안)과 앱(apt 로 깐 일반 앱 +
Flathub)을 따로 알린다 — 시스템은 설정 › 업데이트, 앱은 스토어 › 라이브러리가 연다 (aptapps.py).
같은 개수로는 두 번 알리지 않는다.
"""
import os
import subprocess
import threading

from gi.repository import GLib

from . import dbg
from . import config

FIRST_DELAY = 5 * 60          # 로그인하고 5분 뒤 처음 확인 (로그인 직후는 바쁘다)
INTERVAL = 6 * 3600


def count_pending():
    """→ (시스템 업데이트 수, 앱 업데이트 수) · 셀 수 없으면 None"""
    from . import aptapps
    sysd, apps, _pins = aptapps.split()
    if sysd is None:
        dbg("업데이트 확인 실패", apps)
        return None
    return len(sysd[0]), len(apps) + count_flatpak()


def count_flatpak():
    """Flathub 앱(시스템 설치) 업데이트 수 — 관리자 권한 없이 원격 목록과 견준다. flatpak 이 없거나 실패하면 0"""
    import shutil
    if not shutil.which("flatpak"):
        return 0
    try:
        res = subprocess.run(["flatpak", "remote-ls", "--system", "--updates", "--columns=application"],
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, LC_ALL="C.UTF-8"))
    except Exception as e:
        dbg("Flathub 업데이트 확인 실패", e)
        return 0
    return sum(1 for ln in res.stdout.splitlines() if ln.strip() and not ln.startswith("Application"))


class UpdateNotifier:
    def __init__(self):
        GLib.timeout_add_seconds(FIRST_DELAY, self._tick)

    def _tick(self):
        threading.Thread(target=self._check, daemon=True).start()
        GLib.timeout_add_seconds(INTERVAL, self._tick)
        return False

    def _check(self):
        r = count_pending()
        if r is None:
            return
        n_sys, n_app = r
        dbg(f"[updates] 시스템 {n_sys}개 · 앱 {n_app}개")
        self._notify("updates_notified", n_sys, f"업데이트 {n_sys}개를 설치할 수 있습니다",
                     "보안 수정과 새 기능이 포함되어 있을 수 있습니다.",
                     ["sekai-settings", "--page=update"])
        self._notify("app_updates_notified", n_app, f"앱 업데이트 {n_app}개가 있습니다",
                     "스토어 › 라이브러리에서 설치할 수 있습니다.", ["sekai-store", "--library"])

    def _notify(self, key, n, title, body, cmd):
        last = config.state(key, 0)
        if n == 0:
            if last:
                config.set_state(key, 0)
            return
        if n == last:
            return
        config.set_state(key, n)

        def ask():
            try:
                act = subprocess.run(
                    ["notify-send", "-a", "업데이트", "-i", "system-software-update",
                     "--action=default=열기", "--action=open=업데이트 보기", title, body],
                    capture_output=True, text=True).stdout.strip()
            except Exception:
                return
            if act in ("open", "default"):
                subprocess.Popen(cmd, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # notify-send 는 누를 때까지 기다린다 — 두 알림이 서로를 막지 않게 따로
        threading.Thread(target=ask, daemon=True).start()
