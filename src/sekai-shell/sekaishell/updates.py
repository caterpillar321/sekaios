"""업데이트 알림 — 받아 둔 패키지 목록으로 설치할 업데이트가 있는지 가끔 본다.

목록을 새로 받는 일은 apt-daily.timer 가 한다 (/etc/apt/apt.conf.d/20sekai-periodic).
여기서는 관리자 권한 없이 시뮬레이션만 해서 개수를 센다.
같은 개수로는 두 번 알리지 않는다.
"""
import os
import re
import subprocess
import threading

from gi.repository import GLib

from . import dbg
from . import config

FIRST_DELAY = 5 * 60          # 로그인하고 5분 뒤 처음 확인 (로그인 직후는 바쁘다)
INTERVAL = 6 * 3600


def count_pending():
    try:
        res = subprocess.run(["apt-get", "-s", "-q", "full-upgrade"], capture_output=True,
                             text=True, timeout=120, env=dict(os.environ, LC_ALL="C.UTF-8"))
    except Exception as e:
        dbg("업데이트 확인 실패", e)
        return None
    return sum(1 for ln in res.stdout.splitlines() if re.match(r"Inst \S+", ln))


class UpdateNotifier:
    def __init__(self):
        GLib.timeout_add_seconds(FIRST_DELAY, self._tick)

    def _tick(self):
        threading.Thread(target=self._check, daemon=True).start()
        GLib.timeout_add_seconds(INTERVAL, self._tick)
        return False

    def _check(self):
        n = count_pending()
        if n is None:
            return
        last = config.state("updates_notified", 0)
        dbg(f"[updates] {n}개 (지난 알림 {last}개)")
        if n == 0:
            if last:
                config.set_state("updates_notified", 0)
            return
        if n == last:
            return
        config.set_state("updates_notified", n)
        try:
            act = subprocess.run(
                ["notify-send", "-a", "업데이트", "-i", "system-software-update",
                 "--action=default=열기", "--action=open=업데이트 보기",
                 f"업데이트 {n}개를 설치할 수 있습니다",
                 "보안 수정과 새 기능이 포함되어 있을 수 있습니다."],
                capture_output=True, text=True).stdout.strip()
        except Exception:
            return
        if act in ("open", "default"):
            subprocess.Popen(["sekai-settings", "--page=update"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
