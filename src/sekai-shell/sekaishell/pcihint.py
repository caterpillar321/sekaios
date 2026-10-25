"""PCI 호환 설정을 바꿨다는 안내 — 칩셋 슬롯 그래픽 카드 때문에 USB 가 죽은 걸 sekai-pci 가 감지했을 때.

sekai-pci.service 가 부팅마다 커널 기록을 보고, 다음 부팅의 pci=realloc=off 를 넣거나 빼면 /run/sekai/pci-hint.json 을 쓴다.
서비스는 로그인 화면을 기다리게 하지 않으므로 로그인한 뒤에 끝날 수 있다 — 2분 동안 몇 번 들여다본다.
같은 부팅에서는 한 번만 알린다.
"""
import json
import os
import subprocess
import threading

from gi.repository import GLib

from . import config, dbg

HINT = "/run/sekai/pci-hint.json"
TRIES = (10, 30, 60, 120)                  # 로그인 뒤 몇 초에 볼지


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


class PciHint:
    def __init__(self):
        if os.path.isdir("/run/live/medium"):
            return
        self.done = False
        for t in TRIES:
            GLib.timeout_add_seconds(t, self._tick)

    def _tick(self):
        if not self.done:
            threading.Thread(target=self._check, daemon=True).start()
        return False

    def _check(self):
        try:
            with open(HINT, encoding="utf-8") as f:
                h = json.load(f)
            kind, text = str(h.get("kind", "")), str(h.get("text", ""))[:400]
        except (OSError, ValueError, AttributeError):
            return
        if kind not in ("applied", "reverted") or not text:
            return
        self.done = True
        key = _boot_id() + ":" + kind
        if config.state("pci_hint_seen", "") == key:
            return
        config.set_state("pci_hint_seen", key)
        dbg(f"[pci] {kind}: {text}")
        title = "USB 포트를 고치려면 다시 시작하세요" if kind == "applied" else "PCI 호환 설정을 원래대로 돌렸습니다"
        try:
            act = subprocess.run(["notify-send", "-a", "설정", "-i", "video-display", "-u", "critical",
                                  "--action=default=다시 시작", title, text],
                                 capture_output=True, text=True).stdout.strip()
        except Exception:
            return
        if act == "default":
            subprocess.Popen(["systemctl", "reboot"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
