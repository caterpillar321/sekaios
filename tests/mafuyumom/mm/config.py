"""MafuyuMom 설정 — 경로·포트·계정. 환경변수로 덮어쓸 수 있다 (MM_*)."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # tests/mafuyumom
REPO = os.path.dirname(os.path.dirname(HERE))                                # SekaiOS 저장소

ROOT = os.environ.get("MM_ROOT", os.path.expanduser("~/.cache/mafuyumom"))
GOLDEN = os.path.join(ROOT, "golden")        # 골든 이미지 (disk.qcow2 · OVMF_VARS.fd · info.json)
RUN = os.path.join(ROOT, "run")              # 실행마다 새로 만드는 덧씌우기 디스크·QMP·직렬 기록
REPORTS = os.path.join(ROOT, "reports")      # 실행마다 reports/<시각>/ (report.html · results.json · shots/)

SSH_PORT = int(os.environ.get("MM_SSH_PORT", "2340"))
PROBE_PORT = SSH_PORT + 1            # 호스트 → VM 8765 (방화벽 시험: 밖에서 들어오는 연결이 막히나)
VNC = int(os.environ.get("MM_VNC", "9"))     # 127.0.0.1:5909 — 서버에서 직접 볼 때
KEY = os.environ.get("MM_KEY", os.path.expanduser("~/.ssh/sekaios-dev"))
USER = os.environ.get("MM_USER", "miku")
PASSWORD = os.environ.get("MM_PASSWORD", "miku1234")

SCREEN = (1920, 1080)

# 실기 모드 — VM 대신 실제 PC 를 시험한다 (그 PC 는 real/setup-target.sh 로 한 번 준비)
#   MM_REAL=miku@192.168.0.56  MM_REAL_JUMP=homedesktop (집 공유기 안이면 거쳐 갈 곳)
REAL = os.environ.get("MM_REAL", "")
REAL_JUMP = os.environ.get("MM_REAL_JUMP", "")
REAL_UINPUT = os.path.join(HERE, "real", "mm-uinput.py")
REAL_SCREEN_SET = bool(os.environ.get("MM_REAL_SCREEN"))      # 없으면 연결할 때 시험대에서 알아낸다 (realio.logical_screen)
if REAL and REAL_SCREEN_SET:
    SCREEN = tuple(int(x) for x in os.environ["MM_REAL_SCREEN"].split("x"))
OVMF_CODE = "/usr/share/OVMF/OVMF_CODE_4M.fd"
