"""SekaiOS 셸 공용 모듈 (패널 · 트레이 · 알림)."""
import os
import sys

__version__ = "0.1.0"

DEBUG = os.environ.get("SEKAI_DEBUG") == "1"


def _stderr_to_journal():
    """오류 출력이 /dev/null 이면 저널로 — Hyprland 가 exec 로 띄운 앱(시작 메뉴·단축키)의 출력은 /dev/null 로 버려져,
    파이썬 오류(Traceback)가 어디에도 남지 않았다. journalctl --user -t <프로그램> · 이벤트 뷰어에서 보인다"""
    global _journal_pipe
    try:
        if os.readlink("/proc/self/fd/2") != "/dev/null":
            return
    except OSError:
        return
    import shutil
    import subprocess
    if not shutil.which("systemd-cat"):
        return
    tag = os.path.basename(sys.argv[0] or "") or "sekai"
    try:
        _journal_pipe = subprocess.Popen(["systemd-cat", "-t", tag], stdin=subprocess.PIPE, close_fds=True)
        os.dup2(_journal_pipe.stdin.fileno(), 2)
    except OSError:
        pass


_journal_pipe = None
_stderr_to_journal()


def dbg(*a):
    if DEBUG:
        print("[sekai]", *a, file=sys.stderr, flush=True)
