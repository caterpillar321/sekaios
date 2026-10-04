"""VM 안에서 명령 — SSH (사용자 세션 환경으로, 또는 sudo 로 root)."""
import shlex
import subprocess

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


def _ssh_base(port=None):
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
    p = subprocess.run(["scp", "-q", "-P", str(config.SSH_PORT), "-i", config.KEY, "-o", "IdentitiesOnly=yes",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        local, f"{config.USER}@127.0.0.1:{remote}"], capture_output=True, text=True, timeout=300)
    return p.returncode == 0


def pull(remote, local):
    p = subprocess.run(["scp", "-q", "-P", str(config.SSH_PORT), "-i", config.KEY, "-o", "IdentitiesOnly=yes",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        f"{config.USER}@127.0.0.1:{remote}", local], capture_output=True, text=True, timeout=300)
    return p.returncode == 0


def alive(timeout=3):
    try:
        p = subprocess.run(_ssh_base() + ["true"], capture_output=True, timeout=timeout + 5)
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        return False
