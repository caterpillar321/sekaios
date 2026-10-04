"""방화벽 — 기본 정책, 밖에서 실제로 막히나(호스트 → VM 8765), 설정 › 방화벽에서 포트 열고 닫기 · 네트워크 프로필,
root 도우미가 이상한 인자를 거절하나 (보안 회귀)."""
import time
import urllib.request

from mm import config
from mm.runner import test

SETTINGS = "sekai-settings"
HELPER = "/usr/libexec/sekai/sekai-firewall"


def probe():
    """호스트에서 VM 의 8765 로 HTTP — 답이 오면 True (QEMU 의 포트 연결을 거친다)"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{config.PROBE_PORT}/", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def server(t):
    """VM 안에 작은 웹 서버 (8765) — 시험이 끝나면 끈다"""
    # 끄기와 켜기는 따로 — 한 줄에 쓰면 그 줄(셸의 명령줄)에 "http.server 8765" 가 들어 있어 pkill 이 셸까지 죽인다
    t.sh("pkill -f '[h]ttp.server 8765'; true")
    t.sh("cd /tmp && setsid python3 -m http.server 8765 >/dev/null 2>&1 < /dev/null &")
    t.after("pkill -f '[h]ttp.server 8765'; true")
    t.wait(lambda: t.sh("ss -ltn | grep -q ':8765 '").ok, 5)


def pick_next(t, combo, up=False):
    """콤보에서 다음(위면 앞) 항목을 고른다 — 눌러 목록을 열고 ↓(↑)·Enter. 펼친 목록은 따로 뜬 팝업이라
    접근성 좌표로는 자리를 알 수 없다(창 기준으로 잘못 계산된다)"""
    t.click(combo["cx"], combo["cy"])
    time.sleep(0.6)
    t.key("up" if up else "down")
    time.sleep(0.2)
    t.key("ret")
    time.sleep(0.5)


def fw(t, *args):
    return t.root("firewall-cmd " + " ".join(args)).out.strip()


@test("방화벽 기본 정책 — 켜져 있고, 공용 네트워크는 들어오는 연결을 막는다", suite="net", quick=True)
def firewall_default(t):
    t.expect(t.root("firewall-cmd --state").out.strip() == "running", "firewalld 가 돈다")
    t.expect(fw(t, "--get-default-zone") == "public", "기본 존 = public (새 네트워크는 공용)")
    pub = fw(t, "--zone=public", "--list-services").split()
    t.note(f"공용 네트워크에서 허용: {pub}")
    t.expect(not ({"mdns", "llmnr", "samba-client"} & set(pub)), f"공용 네트워크에서 기기 찾기·공유는 닫혀 있다 ({pub})")
    home = fw(t, "--zone=home", "--list-services").split()
    t.expect({"mdns", "llmnr"} <= set(home), f"개인 네트워크에서는 기기 찾기가 열려 있다 ({home})")
    server(t)
    t.expect(t.sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/").out == "200", "VM 안에서는 웹 서버가 답한다")
    t.expect(not probe(), "밖(호스트)에서 8765 로는 막힌다")


@test("설정 › 방화벽에서 포트 열기 → 밖에서 닿는다 → 닫기 → 다시 막힌다", suite="net", timeout=240)
def firewall_port_ui(t):
    server(t)
    t.after(lambda: t.root("firewall-cmd --permanent --zone=public --remove-port=8765/tcp; "
                           "firewall-cmd --permanent --zone=home --remove-port=8765/tcp; firewall-cmd --reload; true"))
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.after(lambda: t.kill("sekai-settings"))
    t.expect(t.launch("sekai-settings --page=firewall", SETTINGS, timeout=20), "설정 › 방화벽")
    t.expect(t.ui.wait(app=SETTINGS, role="label", name="직접 연 포트", timeout=15), "방화벽 페이지가 그려졌다")
    t.shot("방화벽")
    port = t.ui.find(app=SETTINGS, role="text", name="포트 번호")
    t.expect(port, "[포트 번호] 칸")
    t.click(port["cx"], port["cy"])
    t.type("8765")
    # 어디서: 개인 네트워크만 → 모든 네트워크 (QEMU 연결은 공용 네트워크다)
    where = t.ui.find(app=SETTINGS, role="combo box", name="열 곳")
    t.expect(where, "[열 곳] 고르기")
    pick_next(t, where)                                # 개인 네트워크만 → 모든 네트워크
    t.expect(t.ui.wait(app=SETTINGS, role="combo box", name="열 곳", timeout=3), "[열 곳]")
    add = t.ui.click(app=SETTINGS, role="button", name="추가", timeout=5)
    t.expect(add, "[추가]")
    t.auth()
    t.expect(t.wait(lambda: "8765/tcp" in fw(t, "--zone=public", "--list-ports"), 30, every=1), "공용 네트워크에 8765/tcp 가 열렸다")
    t.expect(t.wait(probe, 10, every=1), "밖(호스트)에서 8765 에 닿는다")
    t.shot("열림")
    close = t.ui.wait(app=SETTINGS, role="button", name="닫기", timeout=10)
    t.expect(close, "열린 포트 줄의 [닫기]")
    t.click(close["cx"], close["cy"])
    t.auth()
    t.expect(t.wait(lambda: "8765/tcp" not in fw(t, "--zone=public", "--list-ports")
                    or "8765/tcp" not in fw(t, "--zone=home", "--list-ports"), 30, every=1), "한 존에서 닫혔다")
    # 두 존 모두 열었으니 남은 줄도 닫는다
    for _ in range(2):
        b = t.ui.wait(app=SETTINGS, role="button", name="닫기", timeout=5)
        if not b:
            break
        t.click(b["cx"], b["cy"])
        t.auth()
        time.sleep(2)
    t.expect(t.wait(lambda: "8765/tcp" not in fw(t, "--zone=public", "--list-ports"), 30, every=1), "공용 네트워크에서 닫혔다")
    t.expect(t.wait(lambda: not probe(), 10, every=1), "밖에서 다시 막힌다")


@test("설정 › 방화벽에서 네트워크를 개인으로 → 존이 home → 공용으로 되돌림", suite="net", timeout=200)
def firewall_profile_ui(t):
    conn = t.sh("nmcli -t -f UUID,DEVICE,NAME connection show --active | grep -v ':lo:' | head -1").out.strip()
    uuid, dev, name = (conn.split(":", 2) + ["", "", ""])[:3]
    t.expect(uuid and dev and name, f"연결된 네트워크 ({conn})")
    t.after(lambda: t.root(f"nmcli connection modify uuid {uuid} connection.zone '' ; nmcli device reapply {dev}; true"))
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.after(lambda: t.kill("sekai-settings"))
    t.expect(t.launch("sekai-settings --page=firewall", SETTINGS, timeout=20), "설정 › 방화벽")
    def prof_combo(timeout):
        """그 네트워크 줄(제목 = 네트워크 이름)과 같은 높이의 콤보"""
        lab = t.ui.wait(app=SETTINGS, role="label", name=name, timeout=timeout)
        if not lab:
            return None
        return next((c for c in (t.ui.find(app=SETTINGS, role="combo box", all=True) or [])
                     if c["cy"] is not None and abs(c["cy"] - lab["cy"]) < 40), None)
    prof = prof_combo(15)
    t.expect(prof, f"[{name}] 줄의 프로필 콤보")
    pick_next(t, prof)                                 # 공용 → 개인
    t.auth()
    t.expect(t.wait(lambda: fw(t, f"--get-zone-of-interface={dev}") == "home", 20, every=1),
             f"{dev} 의 존이 home ({fw(t, f'--get-zone-of-interface={dev}')})")
    t.shot("개인")
    t.expect(t.ui.wait(app=SETTINGS, role="label", name_re="^개인 —", timeout=10) or
             t.ui.wait(app=SETTINGS, role="label", name_re="개인 —", timeout=2), "화면도 개인 네트워크 설명으로")
    prof = prof_combo(10)
    pick_next(t, prof, up=True)                        # 개인 → 공용
    t.auth()
    t.expect(t.wait(lambda: fw(t, f"--get-zone-of-interface={dev}") == "public", 20, every=1), "공용으로 되돌렸다")


@test("방화벽 도우미는 허용 목록 밖의 인자를 거절한다 (root 도우미 보안 회귀)", suite="net", quick=True)
def helper_rejects(t):
    bad = [
        "service telnet home on", "service mdns trusted on", "service mdns home maybe",
        "port add 0/tcp home", "port add 70000/tcp home", "port add 22/sctp home", "port add 22/tcp;id home",
        "profile not-a-uuid home", "profile 11111111-2222-3333-4444-555555555555 trusted",
        "remote-login yes", "setup everything", "on now", "'service mdns home on\nid'",
    ]
    for args in bad:
        r = t.root(f"{HELPER} {args}; echo rc=$?")
        t.expect("rc=0" not in r.out and "OK" not in r.out, f"거절: {args!r} ({r.out.strip()[-60:]})")
    t.expect("security-high" in t.sh("cat /usr/share/polkit-1/actions/org.sekaios.firewall.policy").out
             and "auth_admin" in t.sh("cat /usr/share/polkit-1/actions/org.sekaios.firewall.policy").out,
             "polkit: 관리자 인증이 필요하다")
