"""지난번 비정상 종료 안내 — 커널 패닉으로 다시 시작했으면 로그인한 뒤 알린다 (윈도우의 "예기치 않은 종료"처럼).

커널이 죽기 직전 기록은 UEFI 변수(pstore)에 남고, 다음 부팅에서 systemd-pstore 가 /var/lib/systemd/pstore 로 옮긴다.
sekai-rescue crashcheck(시스템 서비스 sekai-crashcheck)가 그것을 보고 /var/lib/sekai/last-crash.json 을 쓴다
(누구나 읽을 수 있게 — 내용은 시각과 첫 줄 요약뿐). 같은 기록으로는 두 번 알리지 않는다.
"""
import json
import os
import subprocess
import threading

from gi.repository import GLib

from . import config, dbg

STATE = "/var/lib/sekai/last-crash.json"
FIRST_DELAY = 12


class CrashHint:
    def __init__(self):
        if os.path.isdir("/run/live/medium"):
            return
        GLib.timeout_add_seconds(FIRST_DELAY, self._tick)

    def _tick(self):
        threading.Thread(target=self._check, daemon=True).start()
        return False

    def _check(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                c = json.load(f)
        except (OSError, ValueError):
            return
        cid = str(c.get("id") or "")
        if not cid or config.state("crash_seen", "") == cid:
            return
        config.set_state("crash_seen", cid)
        dbg(f"[crash] 지난번 비정상 종료 {cid}")
        title = "지난번에 시스템 오류로 다시 시작했습니다"
        body = "커널에 심각한 오류가 생겨 컴퓨터를 자동으로 다시 시작했습니다. 기록은 이벤트 뷰어에 있습니다. " \
               "자주 일어나면 설정 › 복구에서 이전 상태로 되돌려 보세요."
        try:
            act = subprocess.run(["notify-send", "-a", "시스템", "-i", "dialog-warning", "-u", "critical",
                                  "--action=default=열기", "--action=events=이벤트 보기", "--action=restore=복구 열기",
                                  title, body], capture_output=True, text=True).stdout.strip()
        except Exception:
            return
        if act in ("default", "events"):
            subprocess.Popen(["sekai-admin", "--page=events"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif act == "restore":
            subprocess.Popen(["sekai-settings", "--page=recovery"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
