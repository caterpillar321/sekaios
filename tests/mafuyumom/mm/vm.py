"""시험용 VM — 골든 이미지 위에 실행마다 새 덧씌우기 디스크를 만들어 띄우고, 끝나면 버린다.

  골든 이미지: 검증된 상태의 SekaiOS VM 디스크 사본 (mafuyumom golden --from <svm 폴더>)
  실행:        golden/disk.qcow2 를 바탕으로 run/disk.qcow2 (덧씌우기) — 시험이 무엇을 바꿔도 골든은 그대로
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import time

from . import config, remote
from .qmp import QMP

RUN_DISK = os.path.join(config.RUN, "disk.qcow2")
RUN_VARS = os.path.join(config.RUN, "OVMF_VARS.fd")
PIDF = os.path.join(config.RUN, "qemu.pid")
QMP_SOCK = os.path.join(config.RUN, "qmp.sock")
SERIAL = os.path.join(config.RUN, "serial.log")


def log(*a):
    print("  ·", *a, flush=True)


# ── 골든 이미지 ──────────────────────────────────────────
def golden_info():
    try:
        with open(os.path.join(config.GOLDEN, "info.json")) as f:
            return json.load(f)
    except OSError:
        return None


def make_golden(src_dir):
    """src_dir(svm.sh 의 VM 폴더 — disk.qcow2 · OVMF_VARS.fd)을 골든으로 복사한다. 원본 VM 은 꺼져 있어야 한다"""
    pidf = os.path.join(src_dir, "qemu.pid")
    if os.path.exists(pidf):
        try:
            os.kill(int(open(pidf).read()), 0)
            raise SystemExit(f"원본 VM 이 켜져 있습니다 — 끄고 다시 하세요 ({src_dir})")
        except (ProcessLookupError, ValueError):
            pass
    os.makedirs(config.GOLDEN, exist_ok=True)
    log("골든 디스크를 만드는 중 (압축 복사)…")
    tmp = os.path.join(config.GOLDEN, "disk.qcow2.tmp")
    subprocess.run(["qemu-img", "convert", "-O", "qcow2", os.path.join(src_dir, "disk.qcow2"), tmp], check=True)
    os.replace(tmp, os.path.join(config.GOLDEN, "disk.qcow2"))
    shutil.copy(os.path.join(src_dir, "OVMF_VARS.fd"), os.path.join(config.GOLDEN, "OVMF_VARS.fd"))
    with open(os.path.join(config.GOLDEN, "info.json"), "w") as f:
        json.dump({"from": src_dir, "made": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    log("골든 이미지 준비됨:", config.GOLDEN)


# ── 실행 VM ──────────────────────────────────────────────
def running():
    try:
        os.kill(int(open(PIDF).read()), 0)
        return True
    except (OSError, ValueError):
        return False


def fresh():
    """골든 위에 새 덧씌우기 디스크"""
    if running():
        stop(force=True)
    if not os.path.exists(os.path.join(config.GOLDEN, "disk.qcow2")):
        raise SystemExit("골든 이미지가 없습니다 — 먼저: mafuyumom golden --from ~/.cache/sekai-svm-btrfs")
    os.makedirs(config.RUN, exist_ok=True)
    for f in glob.glob(os.path.join(config.RUN, "*")):
        os.remove(f)
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", os.path.join(config.GOLDEN, "disk.qcow2"),
                    "-F", "qcow2", RUN_DISK], check=True)
    shutil.copy(os.path.join(config.GOLDEN, "OVMF_VARS.fd"), RUN_VARS)


def start():
    subprocess.run([
        "qemu-system-x86_64", "-name", "mafuyumom", "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "8192",
        "-machine", "q35",
        "-drive", f"if=pflash,format=raw,readonly=on,file={config.OVMF_CODE}",
        "-drive", f"if=pflash,format=raw,file={RUN_VARS}",
        "-drive", f"file={RUN_DISK},if=none,id=hd0,format=qcow2", "-device", "virtio-blk-pci,drive=hd0,bootindex=1",
        "-device", f"virtio-vga,xres={config.SCREEN[0]},yres={config.SCREEN[1]}",
        "-device", "qemu-xhci", "-device", "usb-tablet", "-device", "usb-kbd",
        "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{config.SSH_PORT}-:22", "-device", "virtio-net-pci,netdev=n0",
        "-audiodev", "none,id=snd0", "-device", "ich9-intel-hda", "-device", "hda-output,audiodev=snd0",
        "-display", "none", "-vnc", f"127.0.0.1:{config.VNC}",
        "-qmp", f"unix:{QMP_SOCK},server=on,wait=off", "-serial", f"file:{SERIAL}",
        "-daemonize", "-pidfile", PIDF], check=True)


def stop(force=False):
    if not running():
        return
    pid = int(open(PIDF).read())
    if not force:
        try:
            q = QMP(QMP_SOCK).connect(5)
            q.cmd("system_powerdown")
            q.close()
            for _ in range(40):
                if not running():
                    return
                time.sleep(1)
        except Exception:
            pass
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not running():
            return
        time.sleep(0.5)


def qmp():
    return QMP(QMP_SOCK).connect()


def wait_ssh(timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        if remote.alive():
            return True
        time.sleep(3)
    return False


def wait_greeter(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if remote.run("pgrep -f sekai-greete[r] >/dev/null", session=False, timeout=10).ok:
            return True
        time.sleep(2)
    return False


def session_up():
    return remote.run('pgrep -f "/usr/bin/sekai-pane[l]" >/dev/null', session=False, timeout=10).ok


def close_all_windows(timeout=10):
    """열린 창을 모두 닫는다 — --reuse 로 이어 돌릴 때 지난 판의 창이 입력을 가로채지 않게"""
    remote.run("hyprctl -j clients | python3 -c 'import json,sys,os,signal\n"
               "for c in json.load(sys.stdin):\n"
               "    try: os.kill(c[\"pid\"], signal.SIGTERM)\n"
               "    except Exception: pass'", timeout=20)
    end = time.time() + timeout
    while time.time() < end:
        if remote.run("hyprctl clients").out.strip() == "no open windows":
            return True
        time.sleep(0.5)
    return False


def login(q, timeout=120):
    """로그인 화면에서 암호를 쳐 들어간다 — 작업 표시줄이 뜰 때까지"""
    if session_up():
        return True
    if not wait_greeter():
        return False
    time.sleep(3)
    q.type(config.PASSWORD)
    q.key("ret")
    end = time.time() + timeout
    while time.time() < end:
        if session_up():
            time.sleep(6)                    # 작업 표시줄·바탕화면이 자리 잡게
            return True
        time.sleep(2)
    return False


def reboot_and_login(q):
    """다시 부팅하고 로그인 — QMP 연결은 그대로 쓴다 (QEMU 는 한 번에 한 연결만 받아, 새로 열면 멈춘다)"""
    remote.root("systemctl reboot", timeout=10)
    time.sleep(15)
    if not wait_ssh():
        return False
    return login(q)


def boot_id():
    r = remote.run("cat /proc/sys/kernel/random/boot_id", session=False, timeout=10)
    return r.out.strip() if r.ok else None


def wait_rebooted(q, old_boot, timeout=600):
    """시험이 화면에서 다시 시작을 눌렀을 때 — 새로 부팅될 때까지(boot_id 가 바뀔 때까지) 기다렸다가 로그인"""
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(5)
        b = boot_id()
        if b and b != old_boot:
            return login(q)
    return False


def versions():
    r = remote.run("dpkg-query -W -f='${Package} ${Version}\\n' sekai-de sekai-shell sekaios-base worldlink 2>/dev/null",
                   session=False)
    return dict(l.split(" ", 1) for l in r.out.splitlines() if " " in l)


# ── 최신 패키지 ──
def newest(pattern):
    """packages/ 에서 그 이름의 가장 새 판"""
    files = glob.glob(os.path.join(config.REPO, "packages", pattern))
    if not files:
        return None
    def ver(p):
        return os.path.basename(p).split("_")[1]
    best = files[0]
    for f in files[1:]:
        if subprocess.run(["dpkg", "--compare-versions", ver(f), "gt", ver(best)]).returncode == 0:
            best = f
    return best


def install_latest(q):
    """packages/ 의 최신 SekaiOS·WorldLink 패키지를 깔고 다시 부팅해 로그인 (mafuyumom · shinei 가 함께 쓴다)"""
    pats = ["sekai-de_*_all.deb", "sekai-shell_*_all.deb", "sekaios-base_*_all.deb", "worldlink_*_amd64.deb",
            "sekaicomp_*_all.deb", "hyprland_*_all.deb", "hyprbars_*_amd64.deb", "hyprexpo_*_amd64.deb"]
    debs = [d for d in (newest(p) for p in pats) if d]
    print("  · 최신 패키지:", ", ".join(os.path.basename(d) for d in debs), flush=True)
    remote.run("mkdir -p /tmp/mm-debs; rm -f /tmp/mm-debs/*", session=False)
    for d in debs:
        remote.push(d, "/tmp/mm-debs/")
    r = remote.root("DEBIAN_FRONTEND=noninteractive apt-get install -y -q --allow-downgrades /tmp/mm-debs/*.deb "
                    "> /tmp/mm-debs/log 2>&1; echo rc=$?", timeout=900)
    if "rc=0" not in r.out:
        print(remote.root("tail -20 /tmp/mm-debs/log").out)
        raise SystemExit("패키지 설치 실패")
    print("  · 설치됨 — 다시 부팅", flush=True)
    if not reboot_and_login(q):
        raise SystemExit("다시 부팅한 뒤 로그인하지 못했습니다")
