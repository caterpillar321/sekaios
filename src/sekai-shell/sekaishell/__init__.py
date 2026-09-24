"""SekaiOS 셸 공용 모듈 (패널 · 트레이 · 알림)."""
import os
import sys

__version__ = "0.1.0"

DEBUG = os.environ.get("SEKAI_DEBUG") == "1"


def dbg(*a):
    if DEBUG:
        print("[sekai]", *a, file=sys.stderr, flush=True)
