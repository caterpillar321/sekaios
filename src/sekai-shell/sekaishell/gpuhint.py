"""그래픽 드라이버 안내 — NVIDIA 카드가 있는데 드라이버가 없거나 아직 안 쓰일 때 알림으로 알려 준다.

상태는 /usr/libexec/sekai/sekai-gpu status (관리자 권한 없이) 로 본다.
같은 상태로는 두 번 알리지 않는다. 라이브(설치 USB)에서는 알리지 않는다 — 설치해도 사라진다.
"""
import os
import subprocess
import threading

from gi.repository import GLib

from . import dbg
from . import config

HELPER = "/usr/libexec/sekai/sekai-gpu"
FIRST_DELAY = 20


def gpu_state():
    """'none' | 'missing' (드라이버 없음) | 'unused' (설치됐지만 안 쓰임) | 'ok'"""
    try:
        out = subprocess.run([HELPER, "status"], capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        dbg("그래픽 상태 확인 실패", e)
        return None
    nv = [ln.split(" ", 4) for ln in out.splitlines() if ln.startswith("GPU ") and " nvidia " in ln]
    installed = any(ln.startswith("NVIDIA ") and ln.strip() != "NVIDIA -" for ln in out.splitlines())
    if not nv:
        return "none"
    if any(len(p) > 3 and p[3] == "nvidia" for p in nv):
        return "ok"
    return "unused" if installed else "missing"


class GpuHint:
    def __init__(self):
        # 기본 화면 모드면 세션이 따로 알려 준다 (lib/x11/session — 그래픽 설정으로 이어짐)
        if (os.path.isdir("/run/live/medium") or not os.path.exists(HELPER)
                or os.environ.get("SEKAI_BASIC")):
            return
        GLib.timeout_add_seconds(FIRST_DELAY, self._tick)

    def _tick(self):
        threading.Thread(target=self._check, daemon=True).start()
        return False

    def _check(self):
        st = gpu_state()
        if st is None:
            return
        last = config.state("gpu_hint", "")
        dbg(f"[gpu] {st} (지난 알림 {last})")
        if st in ("none", "ok"):
            if last:
                config.set_state("gpu_hint", "")
            return
        if st == last:
            return
        config.set_state("gpu_hint", st)
        if st == "missing":
            title = "NVIDIA 그래픽 카드용 드라이버를 설치할 수 있습니다"
            body = "지금은 기본 드라이버로 표시 중이라 느리고 해상도가 제한됩니다."
        else:
            title = "NVIDIA 드라이버가 아직 쓰이지 않고 있습니다"
            body = "다시 시작하거나, 보안 부팅 키 등록을 마쳐야 합니다."
        try:
            act = subprocess.run(
                ["notify-send", "-a", "그래픽", "-i", "nvidia-settings",
                 "--action=default=열기", "--action=open=설정 열기", title, body],
                capture_output=True, text=True).stdout.strip()
        except Exception:
            return
        if act in ("open", "default"):
            subprocess.Popen(["sekai-settings", "--page=graphics"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
