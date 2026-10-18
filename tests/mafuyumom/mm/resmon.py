"""자원 감시 — 시험마다 끝나고 (정리한 뒤) 잠깐 쉬게 두고 잰다.

  쉬는데 바쁜 프로세스: 3초 동안 CPU 를 BUSY_PCT 넘게 쓰면 그 시험은 "경고" (탐색기·합성기 KDE 장식 핑퐁 같은 것)
  메모리: 프로세스(이름)별 RSS 를 시험마다 적어 두고, 한 시험 사이에 셸이 JUMP_MB 넘게 늘면 표시,
          끝나면 처음과 끝을 비교해 많이 는 것을 보고서의 "자원" 칸에 (누수 후보)
"""
import json

from . import remote

SETTLE_SECS = 1.5                          # 정리 직후의 닫힘 애니메이션 등은 빼고
IDLE_SECS = 3
BUSY_PCT = 5.0
BUSY_PCT_OF = {"worldlink": 15.0}          # VM 은 CPU 로 그려서 합성기가 쉴 때도 3% 안팎
JUMP_MB = 30
GROW_MB = 20                               # 끝 − 처음이 이만큼 넘으면 보고서에 누수 후보로
SHELL = ("sekai-panel", "sekai-desk", "sekai-idle", "worldlink", "polkit-agent", "nm-agent", "automount", "a11yd")

SAMPLE = r'''
import json, os, time
HZ = os.sysconf("SC_CLK_TCK"); me = os.getpid(); uid = os.getuid()
SKIP = {"sshd", "bash", "sh", "sleep", "python3 -", "systemd", "dbus-daemon", "(sd-pam)", "timeout"}
SKIP_PRE = ("mm_agent", "/tmp/mm")
def name(pid):
    try:
        a = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        return None
    a = [x.decode(errors="replace") for x in a if x]
    if not a:
        return None
    b = os.path.basename(a[0])
    if b.startswith("python") and len(a) > 1:
        if a[1] == "-":
            return "python3 -"
        b = os.path.basename(a[1]) if not a[1].startswith("-") else " ".join(a[1:3])
    return b
def snap():
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            if os.stat(f"/proc/{d}").st_uid != uid:
                continue
            f = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
            rss = int(f[21]) * os.sysconf("SC_PAGE_SIZE") // 1024
        except (OSError, ValueError, IndexError):
            continue
        n = name(d)
        if not n or n in SKIP or n.startswith(SKIP_PRE):
            continue
        out[d] = (n, int(f[11]) + int(f[12]), rss)
    return out
time.sleep(@SETTLE); a = snap(); t0 = time.time(); time.sleep(@SECS); b = snap(); dt = time.time() - t0
busy = {}; rss = {}
for pid, (n, tk, r) in b.items():
    rss[n] = rss.get(n, 0) + r
    if pid in a:
        busy[n] = busy.get(n, 0) + 100.0 * (tk - a[pid][1]) / HZ / dt
print(json.dumps({"busy": {k: round(v, 1) for k, v in busy.items() if v >= 1}, "rss": rss}))
'''


def measure():
    """{busy: {이름: %}, rss: {이름: kB}} — 못 재면 None"""
    r = remote.run("python3 - <<'EOF'\n" + SAMPLE.replace("@SECS", str(IDLE_SECS)).replace("@SETTLE", str(SETTLE_SECS)) + "\nEOF", timeout=30)
    try:
        return json.loads(r.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def problems(res, prev):
    """이 시험 뒤 깨진 것 — 쉬는데 바쁜 프로세스, 셸 메모리가 한 번에 크게 는 것"""
    out = []
    if not res:
        return out
    for n, pct in sorted(res["busy"].items(), key=lambda x: -x[1]):
        if pct >= BUSY_PCT_OF.get(n, BUSY_PCT):
            out.append(f"쉬는데 CPU: {n} {pct:.0f}% (정리하고 {IDLE_SECS}초)")
    if prev:
        for n in SHELL:
            a, b = prev["rss"].get(n), res["rss"].get(n)
            if a and b and (b - a) / 1024 >= JUMP_MB:
                out.append(f"메모리: {n} +{(b - a) / 1024:.0f}MB (이 시험 사이)")
    return out


def summary(results):
    """처음과 끝 — [(이름, 처음 MB, 끝 MB, 가장 컸던 MB)] 많이 는 순서. 시험 사이에 다시 시작된 프로세스도 이름으로 합친다"""
    series = [r["res"]["rss"] for r in results if r.get("res")]
    if len(series) < 2:
        return []
    names = set(series[0]) & set(series[-1])
    rows = []
    for n in names:
        vals = [s[n] for s in series if n in s]
        first, last, peak = vals[0] / 1024, vals[-1] / 1024, max(vals) / 1024
        rows.append((n, round(first), round(last), round(peak)))
    rows.sort(key=lambda x: x[1] - x[2])
    return rows
