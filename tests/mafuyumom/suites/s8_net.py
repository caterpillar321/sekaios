"""방화벽 — 기본 정책, 밖에서 실제로 막히나(호스트 → VM 8765), 설정 › 방화벽에서 포트 열고 닫기 · 네트워크 프로필,
root 도우미가 이상한 인자를 거절하나 (보안 회귀)."""
import time
import urllib.request

from mm import config, remote
from mm.runner import test

SETTINGS = "sekai-settings"
HELPER = "/usr/libexec/sekai/sekai-firewall"


def probe():
    """밖에서 시험대의 8765 로 HTTP — 답이 오면 True (VM 은 QEMU 포트 연결, 실기는 LAN 의 다른 PC 에서)"""
    try:
        with urllib.request.urlopen(remote.probe_url(), timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


DECISIONS = "/home/miku/.local/state/sekai/firewall-decisions.json"


def quiet_prompt(t):
    """시험용 python3 은 "허용 안 함"으로 적어 둔다 — 안 그러면 웹 서버를 띄울 때마다 "허용할까요?" 창이 떠 화면을 가린다"""
    t.sh("mkdir -p ~/.local/state/sekai; python3 -c \"import json,os;p=os.path.expanduser('~/.local/state/sekai/firewall-decisions.json');"
         "d=json.load(open(p)) if os.path.exists(p) else {};d[os.path.realpath('/usr/bin/python3')]='deny';json.dump(d,open(p,'w'))\"")


def server(t, prompt=False):
    """VM 안에 작은 웹 서버 (8765) — 시험이 끝나면 끈다"""
    if not prompt:
        quiet_prompt(t)
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


def reveal(t, **kw):
    """그 위젯 — 설정 창을 최대화해 페이지 전체가 보이게 한 뒤 찾는다.
    (휠로 내리면 GTK3 접근성이 스크롤한 칸을 "안 보임"·좌표 없음으로 알린다 — 스크롤 전엔 창 밖 칸을 "보임"으로.
     내레이터 마우스 읽기에도 걸리는 문제라 따로 볼 것)"""
    win = t.window(SETTINGS)
    if win and not win.get("sekaiMaximized"):
        t.sh(f"hyprctl dispatch sekaimaximize on,address:{win['address']} >/dev/null")
        time.sleep(1)
    win = t.window(SETTINGS) or {}
    (wx, wy), (ww, wh) = win.get("at", [0, 0]), win.get("size", list(config.SCREEN))
    w = t.ui.wait(app=SETTINGS, timeout=5, **kw)
    w = w if w and wy <= w["cy"] <= wy + wh else None
    if not w and config.SCREEN[1] < 1000:
        # 최대화해도 페이지가 다 안 들어오는 작은 화면 — 휠로 내리면 GTK3 접근성이 그 칸을 "안 보임·좌표 없음"으로
        #   알려 시험이 누를 자리를 모른다 (접근성 과제로 따로 고칠 것 — a11y-plan). 1080p VM 에선 그대로 본다
        t.skip(f"작은 화면({config.SCREEN[0]}×{config.SCREEN[1]})이라 {kw.get('name') or kw.get('name_re')} 이(가) 스크롤 아래 "
               "— GTK3 스크롤 접근성 문제")
    return w


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
    t.expect(t.ui.wait(app=SETTINGS, role="label", name="네트워크 프로필", timeout=15), "방화벽 페이지가 그려졌다")
    t.shot("방화벽")
    port = reveal(t, role="text", name="포트 번호")
    t.expect(port, "[포트 번호] 칸 (보일 때까지 내렸다)")
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
        "app add ../x name home 80/tcp", "app add x '<b>' home 80/tcp", "app add x name everywhere 80/tcp",
        "app add x name home", "app add x name home 80/icmp", "app remove '../../etc/passwd'",
    ]
    for args in bad:
        r = t.root(f"{HELPER} {args}; echo rc=$?")
        t.expect("rc=0" not in r.out and "OK" not in r.out, f"거절: {args!r} ({r.out.strip()[-60:]})")
    t.expect("security-high" in t.sh("cat /usr/share/polkit-1/actions/org.sekaios.firewall.policy").out
             and "auth_admin" in t.sh("cat /usr/share/polkit-1/actions/org.sekaios.firewall.policy").out,
             "polkit: 관리자 인증이 필요하다")


@test("설정 › 방화벽 › 앱 추가 — 연결을 기다리는 앱을 골라 허용 → 밖에서 닿는다 → 제거 → 막힌다", suite="net", timeout=240)
def firewall_app_ui(t):
    server(t)                                         # python3 가 8765 에서 기다린다
    t.after(lambda: t.root("for z in home public; do firewall-cmd --permanent --zone=$z --remove-service=sekai-app-python3; done; "
                           "firewall-cmd --permanent --delete-service=sekai-app-python3; firewall-cmd --reload; true"))
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.after(lambda: t.kill("sekai-settings"))
    t.expect(t.launch("sekai-settings --page=firewall", SETTINGS, timeout=20), "설정 › 방화벽")
    add = reveal(t, role="button", name="앱 추가…")
    t.expect(add, "[앱 추가…]")
    t.click(add["cx"], add["cy"])
    # 시작 메뉴의 앱 이름으로 보인다 (python3 → "Python (v3.13)")
    pick = t.ui.wait(app=SETTINGS, role="radio button", name_re=r"(?i)python.*—\s+8765/tcp", timeout=10)
    t.shot("앱추가")
    t.expect(pick, f"목록에 Python — 8765/tcp ({pick and pick['name']})")
    t.click(pick["cx"], pick["cy"])
    where = t.ui.find(app=SETTINGS, role="combo box", name="허용할 네트워크")
    t.expect(where, "[허용할 네트워크]")
    pick_next(t, where)                               # 개인 네트워크만 → 모든 네트워크
    ok = t.ui.click(app=SETTINGS, role="button", name="허용", timeout=5)
    t.expect(ok, "[허용]")
    t.auth()
    t.expect(t.wait(lambda: "sekai-app-python3" in fw(t, "--zone=public", "--list-services"), 30, every=1),
             "공용 네트워크에 sekai-app-python3")
    t.expect(t.wait(probe, 10, every=1), "밖에서 8765 에 닿는다 (앱 허용)")
    row = reveal(t, role="label", name_re=r"(?i)^python( \(v[\d.]+\))?$")    # 맨 위 알림("… 을(를) 허용했습니다")은 빼고
    t.expect(row, "허용된 앱 목록에 Python")
    rm = next((b for b in (t.ui.find(app=SETTINGS, role="button", name="제거", all=True) or [])
               if b["cy"] is not None and abs(b["cy"] - row["cy"]) < 40), None)
    t.expect(rm, "그 줄의 [제거]")
    t.click(rm["cx"], rm["cy"])
    t.auth()
    t.expect(t.wait(lambda: "sekai-app-python3" not in fw(t, "--zone=public", "--list-services"), 30, every=1), "지웠다")
    t.expect(t.wait(lambda: not probe(), 10, every=1), "밖에서 다시 막힌다")
    t.expect(not t.root("test -e /etc/firewalld/services/sekai-app-python3.xml").ok, "서비스 파일도 지워졌다")


@test("앱이 처음 연결을 기다리면 『허용할까요?』 — 허용하면 밖에서 닿고, 허용 안 함은 다시 묻지 않는다", suite="net", timeout=240)
def firewall_prompt(t):
    t.after(lambda: t.root("for z in home public; do firewall-cmd --permanent --zone=$z --remove-service=sekai-app-python3; done; "
                           "firewall-cmd --permanent --delete-service=sekai-app-python3; firewall-cmd --reload; true"))
    t.after(lambda: quiet_prompt(t))
    t.sh(f"rm -f {DECISIONS}")
    server(t, prompt=True)
    allow = t.ui.wait(app="sekai-panel", role="button", name="모든 네트워크에서 허용", timeout=20)
    t.shot("허용할까요")
    t.expect(allow, "\"허용할까요?\" 창이 떴다")
    t.expect(t.ui.find(app="sekai-panel", role="label", name_re="(?i)python.*8765/tcp"), "창에 앱 이름과 포트")
    t.click(allow["cx"], allow["cy"])
    t.auth()
    t.expect(t.wait(lambda: "sekai-app-python3" in fw(t, "--zone=public", "--list-services"), 30, every=1),
             "모든 네트워크에 허용됐다")
    t.expect(t.wait(probe, 10, every=1), "밖에서 8765 에 닿는다")
    # 허용한 앱이 포트를 더 열어도, 임시 포트 범위의 UDP(브라우저의 QUIC·WebRTC 같은 나가는 쪽)도 다시 묻지 않는다
    #   (예전엔 유튜브 하나에 Chrome "허용할까요?"가 두 번씩 떴다 — 2026-10-05 메인 PC)
    t.sh("cd /tmp && setsid python3 -m http.server 8766 >/dev/null 2>&1 < /dev/null &")
    t.after("pkill -f '[h]ttp.server 8766'; true")
    t.sh("setsid perl -MIO::Socket::INET -e 'my $s=IO::Socket::INET->new(LocalPort=>45123,Proto=>q(udp)) or die; sleep 40' "
         ">/dev/null 2>&1 < /dev/null &")
    t.after("pkill -f '[L]ocalPort=>45123'; true")
    t.expect(t.wait(lambda: t.sh("ss -lunH | grep -q ':45123 '").ok, 5), "perl 이 UDP 45123 을 열었다")
    time.sleep(10)
    t.expect(not t.ui.find(app="sekai-panel", role="button", name="모든 네트워크에서 허용"),
             "허용한 앱의 새 포트·임시 포트 UDP 에는 묻지 않는다")
    t.sh("pkill -f '[h]ttp.server 8766'; pkill -f '[L]ocalPort=>45123'; true")
    # 허용 안 함 — 서비스를 지우고 기억도 지운 뒤 다시
    t.root("for z in home public; do firewall-cmd --permanent --zone=$z --remove-service=sekai-app-python3; done; "
           "firewall-cmd --permanent --delete-service=sekai-app-python3; firewall-cmd --reload")
    t.sh(f"rm -f {DECISIONS}")
    server(t, prompt=True)
    no = t.ui.wait(app="sekai-panel", role="button", name="허용 안 함", timeout=20)
    t.expect(no, "다시 묻는다")
    t.click(no["cx"], no["cy"])
    t.expect(t.wait(lambda: "deny" in t.sh(f"cat {DECISIONS}").out, 5), "허용 안 함을 기억했다")
    t.expect(not probe(), "밖에서 막혀 있다")
    server(t, prompt=True)                            # 같은 앱을 다시 띄워도
    time.sleep(10)
    t.expect(not t.ui.find(app="sekai-panel", role="button", name="허용 안 함"), "다시 묻지 않는다")
