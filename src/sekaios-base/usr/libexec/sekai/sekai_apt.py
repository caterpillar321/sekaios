"""root 도우미(sekai-update · sekai-gpu)가 함께 쓰는 apt 진행 읽기 — 설정 창에 줄 단위로 알린다 (PROGRESS 퍼센트 글).

같은 폴더(/usr/libexec/sekai, root 소유)에서 불러온다. 예전에는 도우미마다 같은 코드를 따로 들고 있었다 (카나데).
sekai-apps(sekai-de 패키지)도 같은 방식이지만 패키지가 달라 따로 둔다 — 고칠 때는 그쪽도 함께."""
import os
import subprocess
import sys
import threading


def out(*a):
    # 설정 창을 닫으면 읽는 쪽이 사라진다 — 그래도 설치는 끝까지 가야 한다 (dpkg 가 반쯤 멈추면 안 됨)
    try:
        print(*a, flush=True)
    except (BrokenPipeError, OSError):
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass


def run_status(cmd, lo, hi, env, verb="설치하는 중", on_line=None):
    """apt 의 기계용 진행 정보(Status-Fd)를 읽어 PROGRESS lo~hi 로 바꾼다 → (종료 코드, 출력 줄들).
    on_line(줄) — 출력 한 줄마다 (DKMS 빌드처럼 apt 진행률이 멈춰 보이는 동안 알릴 것이 있을 때)"""
    r, w = os.pipe()
    p = subprocess.Popen(cmd + ["-o", f"APT::Status-Fd={w}"], env=env, pass_fds=(w,),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    os.close(w)
    log = []

    def drain():
        for line in p.stdout:
            log.append(line.rstrip())
            if on_line:
                on_line(line)
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    with os.fdopen(r, "r", errors="replace") as f:
        for line in f:
            # dlstatus:1:33.3:받는 중…  /  pmstatus:pkg:57.1:Unpacking pkg
            parts = line.rstrip("\n").split(":", 3)
            if len(parts) == 4 and parts[0] in ("dlstatus", "pmstatus"):
                try:
                    pct = float(parts[2])
                except ValueError:
                    continue
                what = "내려받는 중" if parts[0] == "dlstatus" else verb
                name = parts[1] if parts[0] == "pmstatus" else ""
                out("PROGRESS", int(lo + (hi - lo) * pct / 100), f"{what} {name}".strip())
    p.wait()
    t.join(2)
    return p.returncode, log
