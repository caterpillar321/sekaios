"""SekaiOS 설정 — 공용 유틸리티.

명령 실행·Hyprland IPC·색 변환은 sekaishell.sysutil 로 옮겼다 (설정 저장소가 공용으로 가면서 — 셸도 쓴다).
설정 페이지들이 쓰던 이름 그대로 여기서 다시 내보낸다."""
import os

from sekaishell.sysutil import (failure_reason, hex_to_rgba, human_bytes, hyprctl,
                                keyword, run, run_async, spawn)

__all__ = ["failure_reason", "hex_to_rgba", "human_bytes", "hyprctl", "keyword", "run", "run_async",
           "spawn", "LOCK_NOW", "dbg"]

DEBUG = os.environ.get("SEKAI_DEBUG") == "1"

# 지금 잠그기 — sekai-lock 을 직접 띄우지 않는다. lock-now 는 sekai-lock 이 못 잠그면 swaylock 으로 대신 잠그고,
#   잠금 화면이 풀리지 않은 채 죽으면 다시 띄운다 (Super+L·패널과 같은 길)
LOCK_NOW = "/usr/libexec/sekai/lock-now"


def dbg(*a):
    if DEBUG:
        print("[sekai-settings]", *a, flush=True)
