"""읽기 전용으로 붙은 내장 드라이브 안내 — 윈도우 빠른 시작·최대 절전 중이던 NTFS (sekai-drives).

sekai-drives.service 가 부팅 때 /run/sekai/drives.json 을 쓴다. 읽기 전용이 된 드라이브가 있으면 로그인한 뒤 한 번 알린다
(같은 부팅에서 같은 드라이브로는 다시 알리지 않는다 — 로그아웃했다 들어와도).
"""
import json
import os
import subprocess
import threading

from gi.repository import GLib

from . import config, dbg

STATE = "/run/sekai/drives.json"
FIRST_DELAY = 10

WHY = {
    "hibernated": ("윈도우의 빠른 시작(또는 최대 절전) 때문에 윈도우가 이 드라이브를 아직 쓰는 중입니다. "
                   "지금 쓰면 윈도우가 다시 켜질 때 파일이 사라지거나 드라이브가 손상될 수 있어 읽기만 됩니다.\n"
                   "윈도우에서 '다시 시작'으로 끄거나, 제어판 › 전원 옵션에서 '빠른 시작 켜기'를 끄면 다음부터 쓸 수 있습니다."),
    "unclean": ("윈도우가 이 드라이브를 깨끗하게 닫지 않고 꺼졌습니다. 손상을 막으려고 읽기만 됩니다.\n"
                "윈도우로 한 번 켰다가 '종료'로 끄면 다음부터 쓸 수 있습니다."),
}


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


class DriveHint:
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
                drives = [d for d in json.load(f) if d.get("readonly")]
        except (OSError, ValueError, AttributeError):
            return
        if not drives:
            return
        key = _boot_id() + ":" + ",".join(sorted(d.get("uuid", "") for d in drives))
        if config.state("drives_ro_seen", "") == key:
            return
        config.set_state("drives_ro_seen", key)
        names = ", ".join(f"{d.get('label') or '로컬 디스크'} ({d.get('letter')}:)" for d in drives)
        dbg(f"[drives] 읽기 전용: {names}")
        reason = drives[0].get("reason")
        body = WHY.get(reason, "윈도우가 이 드라이브를 사용 중인 상태로 남겨 두어 읽기만 됩니다.")
        try:
            act = subprocess.run(["notify-send", "-a", "파일 탐색기", "-i", "drive-harddisk", "-u", "critical",
                                  "--action=default=열기", f"{names} 을(를) 읽기 전용으로 연결했습니다", body],
                                 capture_output=True, text=True).stdout.strip()
        except Exception:
            return
        if act == "default":
            subprocess.Popen(["sekai-files", drives[0].get("path") or "computer:///"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
