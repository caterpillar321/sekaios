"""Windows 앱 (Wine) — 엔진, .exe 열기 창으로 설치·그냥 실행, 시작 메뉴 바로 가기, 스냅샷 되돌리기, 제거.
   시험용 Windows 프로그램은 assets/win (mingw 로 빌드 — build.sh): mmhello.exe(한글 창) · mmsetup.exe(창 없이
   C:\\Program Files\\MM Hello 에 복사하고 시작 메뉴에 "MM Hello"·"Uninstall MM Hello" 바로 가기를 만든다).
   진짜 설치 프로그램(7-Zip)은 slow."""
import json
import os
import time

from mm import remote
from mm.runner import test

OPEN = "org.sekaios.WineOpen"
APP = "(?i)sekai-wine|WineOpen|Windows 프로그램"   # 열기 창의 접근성 앱 이름
SETTINGS = "sekai-settings"
PANEL = "sekai-panel"
WIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "win", "bin")
DL = "/home/miku/mm-win"                  # 홈 아래 — 실제처럼 (Z: = 홈)
ROOT = "$HOME/.local/share/sekai/wine"
ENGINE = "/usr/lib/sekai/wine/wine-11.0"
HELLO_TITLE = "MM Hello 한글 창"


def push_exes(t):
    if not os.path.exists(os.path.join(WIN, "mmsetup.exe")):
        t.skip("시험용 exe 가 없습니다 — tests/mafuyumom/assets/win/build.sh")
    t.sh(f"mkdir -p {DL}")
    for f in ("mmhello.exe", "mmsetup.exe"):
        remote.push(os.path.join(WIN, f), f"{DL}/{f}")


def envs(t):
    try:
        return json.loads(t.sh("sekai-wine list").out or "[]")
    except ValueError:
        return []


def env_named(t, name):
    return next((e for e in envs(t) if e["name"] == name), None)


def remove_all(t, name="MM Hello"):
    for e in envs(t):
        if e["name"] == name:
            t.sh(f"sekai-wine remove {e['id']}", timeout=120)


def hello_window(t, timeout=40):
    def find():
        return next((c for c in t.clients() if c.get("title") == HELLO_TITLE), None)
    return t.wait(find, timeout, every=1)


def close_hello(t):
    t.sh("pkill -f '[m]mhello.exe'; true")


def open_window(t, path):
    t.kill("sekai-wine")
    t.after(lambda: t.kill("sekai-wine"))
    return t.launch(f"sekai-wine open {path}", OPEN, timeout=20)


def install_mm_hello(t):
    """열기 창으로 mmsetup.exe 를 "MM Hello" 라는 이름으로 설치 → 환경"""
    t.expect(open_window(t, f"{DL}/mmsetup.exe"), "Windows 프로그램 창이 떴다")
    t.expect(t.ui.wait(app=APP, role="radio button", name_re="^설치", timeout=10), "[설치] 고르기가 있다")
    name = t.ui.wait(app=APP, role="text", name="앱 이름", timeout=5)
    t.expect(name, "[앱 이름] 칸")
    t.click(name["cx"], name["cy"])
    t.key("ctrl-a")
    t.type("MM Hello")
    t.shot("설치 전")
    t.expect(t.ui.click(app=APP, role="button", name="설치", timeout=5), "[설치] 단추")
    t.expect(t.ui.wait(app=APP, role="label", name_re="^MM Hello 을\\(를\\) 설치했습니다", timeout=240),
             "설치가 끝나고 시작 메뉴에 올렸다고 한다")
    t.shot("설치 끝")
    env = env_named(t, "MM Hello")
    t.expect(env, "환경 'MM Hello' 가 생겼다")
    return env


@test("엔진 — Wine 11.0 (WoW64): 32비트용 i386 라이브러리 없이", suite="wine", quick=True)
def engine(t):
    v = t.sh(f"{ENGINE}/bin/wine --version").out.strip()
    t.expect(v == "wine-11.0", f"wine --version = {v}")
    t.expect(not t.sh(f"test -d {ENGINE}/lib/wine/i386-unix").ok, "i386-unix 가 없다 (WoW64)")
    t.expect(t.sh("dpkg --print-foreign-architectures").out.strip() == "", "i386 멀티아키를 켜지 않았다")
    mime = t.sh("xdg-mime query default application/x-ms-dos-executable").out.strip()
    t.expect(mime == "sekai-wine-open.desktop", f".exe 더블클릭 = Windows 프로그램 창 ({mime})")


@test("설치 — mmsetup.exe 를 열기 창으로: 환경·시작 메뉴·아이콘, 제거 바로 가기는 빼고, 한글 글꼴·Z: 정리, 실행",
      suite="wine", timeout=420)
def install_app(t):
    push_exes(t)
    remove_all(t)
    t.after(lambda: (close_hello(t), remove_all(t)))
    env = install_mm_hello(t)
    pfx = f"{ROOT}/envs/{env['id']}/prefix"
    t.expect(t.ui.find(app=APP, role="button", name="MM Hello 실행"), "[MM Hello 실행] 단추")
    t.expect(not t.ui.find(app=APP, role="label", name_re="(?i)uninstall"), "제거 바로 가기는 올리지 않았다")
    apps = env.get("apps", [])
    t.expect([a["name"] for a in apps] == ["MM Hello"], f"앱 목록 = {[a['name'] for a in apps]}")
    did = apps[0]["desktop"] if apps else "?"
    t.expect(t.sh(f"grep -q '^Exec=sekai-wine run {env['id']} --lnk' ~/.local/share/applications/{did}.desktop").ok,
             "시작 메뉴 바로 가기(.desktop)가 sekai-wine run 으로")
    t.expect(t.sh(f"ls ~/.local/share/icons/hicolor/*/apps/{did}.png").ok, "아이콘을 옮겼다")
    t.expect(t.sh(f"test -f \"{pfx}/drive_c/Program Files/MM Hello/mmhello.exe\"").ok, "C:\\Program Files\\MM Hello 에 설치됐다")
    t.expect(t.sh(f"test \"$(readlink {pfx}/dosdevices/z:)\" = \"$HOME\"").ok, "Z: = 홈 (/ 전체가 아니라)")
    t.expect(t.sh(f"grep -q 'Malgun Gothic' {pfx}/system.reg").ok, "한글 글꼴 대체(맑은 고딕 → Pretendard)")
    t.expect(t.ui.click(app=APP, role="button", name="MM Hello 실행", timeout=5), "[MM Hello 실행] 누름")
    w = hello_window(t)
    t.expect(w, f"'{HELLO_TITLE}' 창이 떴다 (Wine)")
    time.sleep(1.5)
    t.shot("MM Hello 창")


@test("시작 메뉴 — 설치한 Windows 앱을 검색해 연다", suite="wine", timeout=420)
def start_menu(t):
    push_exes(t)
    remove_all(t)
    t.after(lambda: (close_hello(t), remove_all(t)))
    install_mm_hello(t)
    t.kill("sekai-wine")
    time.sleep(1)
    t.key("meta_l")
    t.expect(t.ui.wait(app=PANEL, role="text", name="검색", timeout=5), "시작 메뉴")
    t.type("MM Hello")
    time.sleep(1.5)
    t.shot("검색결과")
    t.key("ret")
    t.expect(hello_window(t), "시작 메뉴에서 연 MM Hello 창")


@test("되돌리기 — '설치 전' 스냅샷으로 되돌리면 설치한 앱과 바로 가기가 사라진다 (btrfs)", suite="wine", timeout=480)
def restore_snapshot(t):
    if t.sh("stat -f -c %T ~/.local/share").out.strip() != "btrfs":
        t.skip("btrfs 가 아니다")
    push_exes(t)
    remove_all(t)
    t.after(lambda: (t.kill("sekai-settings"), remove_all(t)))
    env = install_mm_hello(t)
    t.kill("sekai-wine")
    did = env["apps"][0]["desktop"]
    pfx = f"{ROOT}/envs/{env['id']}/prefix"
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.expect(t.launch("sekai-settings --page=wineapps", SETTINGS, timeout=20), "설정 › Windows 앱")
    lab = t.ui.wait(app=SETTINGS, role="label", name="MM Hello", timeout=10)
    t.expect(lab, "[MM Hello] 줄")
    btn = next((b for b in t.ui.find(app=SETTINGS, role="button", name="되돌리기…", all=True) or []
                if b["cy"] is not None and abs(b["cy"] - lab["cy"]) < 40), None)
    t.expect(btn, "[되돌리기…] 단추")
    t.click(btn["cx"], btn["cy"])
    ok = t.wait(lambda: next((b for b in t.ui.find(app=SETTINGS, role="button", name="되돌리기", all=True,
                                                   not_frame="^설정$") or [] if b["cy"]), None), 10)
    t.expect(ok, "되돌리기 창")
    t.shot("되돌리기 창")
    t.click(ok["cx"], ok["cy"])
    t.expect(t.wait(lambda: not t.sh(f"test -f ~/.local/share/applications/{did}.desktop").ok, 30),
             "시작 메뉴 바로 가기가 사라졌다")
    t.expect(not t.sh(f"test -e \"{pfx}/drive_c/Program Files/MM Hello\"").ok, "설치한 파일도 사라졌다")
    t.expect(t.sh(f"test -d {pfx}/drive_c/windows").ok, "환경(C: 드라이브)은 남았다 — 설치 전 그대로")


@test("그냥 실행 — mmhello.exe 를 설치 없이 공용 환경에서", suite="wine", quick=True, timeout=300)
def run_once(t):
    push_exes(t)
    t.after(lambda: close_hello(t))
    t.expect(open_window(t, f"{DL}/mmhello.exe"), "Windows 프로그램 창")
    r = t.ui.wait(app=APP, role="radio button", name_re="^그냥 실행", timeout=10)
    t.expect(r and "checked" in r["states"], "설치 파일이 아니면 [그냥 실행] 이 골라져 있다")
    t.expect(t.ui.click(app=APP, role="button", name="실행", timeout=5), "[실행] 단추")
    t.expect(hello_window(t, timeout=120), f"'{HELLO_TITLE}' 창이 떴다")
    t.expect(t.wait(lambda: not t.window(OPEN, timeout=1), 10), "열기 창은 닫혔다")
    t.expect(t.sh(f"test -d {ROOT}/envs/quick/prefix/drive_c/windows").ok, "공용 환경(quick)")
    t.shot("그냥 실행")


@test("제거 — 설정 › Windows 앱 에서 [제거] → 환경·바로 가기·아이콘이 모두 사라진다", suite="wine", timeout=420)
def remove_app(t):
    push_exes(t)
    remove_all(t)
    t.after(lambda: (t.kill("sekai-settings"), remove_all(t)))
    env = install_mm_hello(t)
    t.kill("sekai-wine")
    did = env["apps"][0]["desktop"]
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.expect(t.launch("sekai-settings --page=wineapps", SETTINGS, timeout=20), "설정 › Windows 앱")
    lab = t.ui.wait(app=SETTINGS, role="label", name="MM Hello", timeout=10)
    t.expect(lab, "[MM Hello] 줄")
    btn = next((b for b in t.ui.find(app=SETTINGS, role="button", name="제거", all=True) or []
                if b["cy"] is not None and abs(b["cy"] - lab["cy"]) < 40), None)
    t.expect(btn, "[제거] 단추")
    t.click(btn["cx"], btn["cy"])
    ok = t.wait(lambda: next((b for b in t.ui.find(app=SETTINGS, role="button", name="제거", all=True,
                                                   not_frame="^설정$") or [] if b["cy"]), None), 10)
    t.expect(ok, "제거 확인 창")
    t.click(ok["cx"], ok["cy"])
    t.expect(t.wait(lambda: not t.sh(f"test -e {ROOT}/envs/{env['id']}").ok, 60), "환경을 지웠다")
    t.expect(not t.sh(f"test -e ~/.local/share/applications/{did}.desktop").ok, "바로 가기를 지웠다")
    t.expect(not t.sh(f"ls ~/.local/share/icons/hicolor/*/apps/{did}.png").ok, "아이콘을 지웠다")
    t.expect(not t.sh(f"ls -d {ROOT}/snapshots/{env['id']}@*").ok, "스냅샷도 지웠다")


SEVENZIP = ("https://www.7-zip.org/a/7z2301-x64.exe",
            "26cb6e9f56333682122fafe79dbcdfd51e9f47cc7217dccd29ac6fc33b5598cd")


def _fetch(url, sha):
    """호스트에 한 번 받아 두고(~/.cache/mafuyumom/dl) 해시를 확인한다"""
    import hashlib
    import urllib.request
    d = os.path.expanduser("~/.cache/mafuyumom/dl")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, os.path.basename(url))
    if not os.path.exists(p):
        urllib.request.urlretrieve(url, p + ".part")
        os.replace(p + ".part", p)
    with open(p, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != sha:
            os.remove(p)
            raise RuntimeError(f"{url} 해시가 다르다")
    return p


@test("진짜 설치 프로그램 — 7-Zip (NSIS 창: 설치 → 닫기) → 시작 메뉴에 7-Zip File Manager, 실행", suite="wine",
      slow=True, timeout=600)
def real_installer(t):
    try:
        local = _fetch(*SEVENZIP)
    except Exception as e:
        t.skip(f"7-Zip 을 받지 못했다: {e}")
    t.sh(f"mkdir -p {DL}")
    remote.push(local, f"{DL}/7z2301-x64.exe")
    remove_all(t, "7-Zip")
    t.after(lambda: (t.sh("pkill -f '[7]zFM.exe'; true"), remove_all(t, "7-Zip")))
    t.expect(open_window(t, f"{DL}/7z2301-x64.exe"), "Windows 프로그램 창")
    name = t.ui.wait(app=APP, role="text", name="앱 이름", timeout=5)
    t.click(name["cx"], name["cy"])
    t.key("ctrl-a")
    t.type("7-Zip")
    t.expect(t.ui.click(app=APP, role="button", name="설치", timeout=5), "[설치] 단추")

    def setup_win():
        return next((c for c in t.clients() if "7-Zip" in (c.get("title") or "") and "Setup" in c.get("title", "")), None)
    w = t.wait(setup_win, 120, every=1)
    t.expect(w, "7-Zip 설치 창(NSIS)이 떴다")
    time.sleep(2)
    t.shot("7-Zip 설치 창")
    t.sh(f"hyprctl dispatch focuswindow address:{w['address']} >/dev/null")
    time.sleep(0.5)
    t.key("ret")                                         # [Install]
    time.sleep(6)
    t.shot("7-Zip 설치 끝")
    w = setup_win()
    if w:
        t.sh(f"hyprctl dispatch focuswindow address:{w['address']} >/dev/null")
        time.sleep(0.5)
        t.key("ret")                                     # [Close]
    t.expect(t.ui.wait(app=APP, role="label", name_re="^7-Zip 을\\(를\\) 설치했습니다", timeout=120),
             "설치가 끝나고 시작 메뉴에 올렸다")
    env = env_named(t, "7-Zip")
    names = [a["name"] for a in (env or {}).get("apps", [])]
    t.expect(any("7-Zip File Manager" in n for n in names), f"시작 메뉴: {names}")
    t.expect(not any("ninstall" in n for n in names), "제거 바로 가기는 빼고")
    t.expect(t.ui.click(app=APP, role="button", name_re="^7-Zip File Manager 실행", timeout=5), "[7-Zip File Manager 실행]")
    t.expect(t.wait(lambda: next((c for c in t.clients() if "7zfm" in (c.get("class") or "").lower()
                                  or (c.get("title") or "").startswith("C:\\")), None), 40, every=1),
             "7-Zip File Manager 창이 떴다")
    time.sleep(1.5)
    t.shot("7-Zip 파일 관리자")
