"""VM 안에서 명령 — SSH (사용자 세션 환경으로, 또는 sudo 로 root)."""
import shlex
import socket
import atexit
import subprocess
import time

from . import config

# 로그인한 세션의 환경 — Hyprland 인스턴스·D-Bus·Wayland 를 찾는다
SESSION_ENV = ('U=$(id -u); export XDG_RUNTIME_DIR=/run/user/$U WAYLAND_DISPLAY=wayland-1 DISPLAY=:0 '
               'HYPRLAND_INSTANCE_SIGNATURE=$(ls -t /run/user/$U/hypr 2>/dev/null | head -1) '
               'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$U/bus')


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return f"<rc={self.rc} out={self.out[:200]!r} err={self.err[:200]!r}>"


def _ssh_target():
    """(ssh 앞부분 옵션, 사용자@주소) — VM 이면 127.0.0.1:포트, 실기 모드면 그 PC (거쳐 갈 곳 포함)"""
    if config.REAL:
        return (["-J", config.REAL_JUMP] if config.REAL_JUMP else []), config.REAL
    return ["-p", str(config.SSH_PORT)], f"{config.USER}@127.0.0.1"


def _ssh_base(port=None):
    if config.REAL:
        pre, tgt = _ssh_target()
        return ["ssh", *pre, "-i", config.KEY, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=5", tgt]
    return ["ssh", "-p", str(port or config.SSH_PORT), "-i", config.KEY, "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", f"{config.USER}@127.0.0.1"]


def run(cmd, timeout=60, session=True, input=None):
    """사용자로 — session=True 면 로그인한 세션의 환경으로"""
    full = f"{SESSION_ENV}; {cmd}" if session else cmd
    try:
        p = subprocess.run(_ssh_base() + [full], capture_output=True, text=True, errors="replace", timeout=timeout, input=input)
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        return Result(124, e.stdout or "" if isinstance(e.stdout, str) else "", f"시간 초과 {timeout}s")


def root(cmd, timeout=300):
    """root 로 (sudo — 암호는 표준 입력)"""
    full = f"sudo -S -p '' bash -c {shlex.quote(cmd)}"
    try:
        p = subprocess.run(_ssh_base() + [full], capture_output=True, text=True, errors="replace", timeout=timeout,
                           input=config.PASSWORD + "\n")
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return Result(124, "", f"시간 초과 {timeout}s")


def push(local, remote):
    pre, tgt = _ssh_target()
    pre = ["-P" if x == "-p" else x for x in pre]
    p = subprocess.run(["scp", "-q", *pre, "-i", config.KEY, "-o", "IdentitiesOnly=yes",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        local, f"{tgt}:{remote}"], capture_output=True, text=True, timeout=300)
    return p.returncode == 0


def pull(remote, local):
    pre, tgt = _ssh_target()
    pre = ["-P" if x == "-p" else x for x in pre]
    p = subprocess.run(["scp", "-q", *pre, "-i", config.KEY, "-o", "IdentitiesOnly=yes",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        f"{tgt}:{remote}", local], capture_output=True, text=True, timeout=300)
    return p.returncode == 0


_probe_fwd = None
_probe_port = None


def probe_url():
    """방화벽 시험의 '밖에서 들어오는 연결' 주소 — VM 은 QEMU 포트 연결, 실기는 다른 PC 에서 LAN 으로
    (거쳐 갈 곳이 있으면 그 PC 에서 ssh -L 로 — 시험대 입장에선 LAN 의 다른 기계가 붙는 것)"""
    global _probe_fwd, _probe_port
    if not config.REAL:
        return f"http://127.0.0.1:{config.PROBE_PORT}/"
    host = config.REAL.split("@")[-1]
    if not config.REAL_JUMP:
        return f"http://{host}:8765/"
    if _probe_fwd is None or _probe_fwd.poll() is not None:
        # 빈 포트를 따로 — PROBE_PORT 는 남아 있는 MafuyuMom VM 이 쥐고 있을 수 있다 (그럼 VM 을 찔러 놓고 통과로 본다)
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            _probe_port = so.getsockname()[1]
        _probe_fwd = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "LogLevel=ERROR",
                                       "-L", f"127.0.0.1:{_probe_port}:{host}:8765", config.REAL_JUMP],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(_probe_fwd.terminate)
        for _ in range(30):
            if _probe_fwd.poll() is not None:
                break
            with socket.socket() as so:
                if so.connect_ex(("127.0.0.1", _probe_port)) == 0:
                    break
            time.sleep(0.5)
        if _probe_fwd.poll() is not None:
            raise RuntimeError(f"{config.REAL_JUMP} 을 거친 포트 연결(→ {host}:8765)을 열지 못했습니다")
    return f"http://127.0.0.1:{_probe_port}/"


def alive(timeout=3):
    try:
        p = subprocess.run(_ssh_base() + ["true"], capture_output=True, timeout=timeout + 5)
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        return False
