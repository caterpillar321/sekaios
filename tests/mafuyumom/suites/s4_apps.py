"""앱 설치·제거 — 스토어(데비안 저장소 앱), 앱 설치 관리자(.deb), 설정 › 설치된 앱. 설치한 앱은 실제로 띄워 쓴다."""
import os
import subprocess
import tempfile
import time

from mm import remote
from mm.runner import test

STORE = "org.sekaios.Store"
APP = "galculator"          # GTK3 계산기 — 꾸러미 2개 · 1.4MB (데비안 저장소)


def installed(t, pkg):
    return t.sh(f"dpkg-query -W -f='${{Status}}' {pkg} 2>/dev/null").out.endswith("installed")


@test("스토어에서 검색해 설치 (데비안 저장소 앱)", suite="apps", timeout=400)
def store_install(t):
    if installed(t, APP):
        t.root(f"apt-get remove -y -q {APP} >/dev/null 2>&1")
    t.sh("pkill -f /usr/bin/sekai-stor[e]; true")
    t.expect(t.launch("sekai-store", STORE, timeout=20), "스토어가 떴다")
    t.expect(t.ui.click(app="Store", role="text", name="검색", timeout=15), "검색 칸")
    t.type(APP)
    t.key("ret")
    hit = t.ui.wait(app="Store", role="button", name_re="(?i)^galculator", timeout=30)
    t.shot("검색결과")
    t.expect(hit, "검색 결과에 Galculator")
    t.click(hit["cx"], hit["cy"])
    btn = t.ui.wait(app="Store", role="button", name="설치", timeout=15)
    t.shot("앱 정보")
    t.expect(btn, "앱 정보에 [설치] 단추")
    t.click(btn["cx"], btn["cy"])
    t.auth()
    t.expect(t.wait(lambda: installed(t, APP), 240, every=3), "설치됨 (dpkg)")
    t.expect(t.ui.wait(app="Store", role="button", name_re="열기|실행|제거", timeout=20), "스토어 단추가 [열기]·[제거]로 바뀌었다")
    t.shot("설치끝")
    t.sh("pkill -f /usr/bin/sekai-stor[e]; true")


@test("설치한 앱을 시작 메뉴에서 열어 계산한다", suite="apps")
def use_installed(t):
    if not installed(t, APP):
        t.skip("앞 시험에서 설치하지 못했다")
    t.key("meta_l")
    t.expect(t.ui.wait(app="sekai-panel", role="text", name="검색", timeout=5), "시작 메뉴")
    t.type("galculator")
    time.sleep(1)
    t.key("ret")
    t.after("pkill -x galculator; true")
    w = t.window("Galculator", timeout=15) or t.window("galculator", timeout=5)
    t.expect(w, "Galculator 창")
    time.sleep(1)
    for k in ("7", "plus", "5", "ret"):                # 7 + 5 =
        t.key(k if k != "plus" else "shift-equal")
    time.sleep(0.5)
    t.shot("계산")
    # 표시 칸은 직접 그린 그림이라 접근성으로 못 읽는다 — 복사(Ctrl+C)해 클립보드로 읽는다
    t.sh("wl-copy --clear; true")
    t.key("ctrl-c")
    got = t.wait(lambda: t.sh("wl-paste -n 2>/dev/null").out.strip(), 3)
    try:
        val = float(got.replace(",", ""))
    except ValueError:
        val = None
    t.expect(val == 12, f"7 + 5 = 12 (복사한 값 {got!r})")


@test("설정 › 설치된 앱에서 제거", suite="apps", timeout=300)
def remove_installed(t):
    if not installed(t, APP):
        t.skip("설치돼 있지 않다")
    t.sh("pkill -f /usr/bin/sekai-setting[s]; true")
    t.after("pkill -f /usr/bin/sekai-setting[s]; true")
    t.expect(t.launch("sekai-settings --page=installed", "sekai-settings", timeout=20), "설정 › 설치된 앱")
    time.sleep(2)
    # 넓은 창이면 왼쪽에 "설정 찾기" 칸도 있다 — 가장 오른쪽 칸이 앱 목록 검색
    fields = [x for x in (t.ui.find(app="sekai-settings", role="text", all=True) or []) if x["cx"] is not None]
    srch = max(fields, key=lambda x: x["cx"]) if fields else None
    if srch:
        t.click(srch["cx"], srch["cy"])
        t.type("galc")
        time.sleep(1)
    row = t.ui.wait(app="sekai-settings", role="label", name_re="(?i)galculator", timeout=10)
    t.expect(row, "목록에 Galculator")
    rm = [b for b in (t.ui.find(app="sekai-settings", role="button", name_re="제거", all=True) or [])
          if b["cy"] is not None and abs(b["cy"] - row["cy"]) < 40]
    if not rm:                                          # 줄을 눌러야 [제거]가 나오는 모양이면
        t.click(row["cx"], row["cy"])
        time.sleep(0.8)
        rm = t.ui.find(app="sekai-settings", role="button", name_re="제거", all=True) or []
    t.expect(rm, "[제거] 단추")
    t.click(rm[0]["cx"], rm[0]["cy"])
    # 확인 창의 [제거] — 줄의 [제거]와 이름이 같으니 자리로 가른다
    # 확인 창의 [제거]는 제거 계획(plan-remove)을 다 세운 뒤에 켜진다 — 꺼져 있을 때 누르면 아무 일도 없다
    ok = t.wait(lambda: [b for b in (t.ui.find(app="sekai-settings", role="button", name_re="^제거$|^예$", all=True) or [])
                         if b["cy"] is not None and (b["cx"], b["cy"]) != (rm[0]["cx"], rm[0]["cy"])
                         and "sensitive" in b["states"]], 30)
    if ok:
        # 단추가 켜지는 순간 "함께 제거되는 구성 요소" 줄이 들어오며 창이 한 줄 커진다 — 자리가 멈출 때까지
        #   (노트북 배율 200% 에서 커지기 전 좌표를 눌러 한 줄(27px) 아래를 눌렀다)
        def settled():
            a = ok[0]
            time.sleep(0.5)
            b = [x for x in (t.ui.find(app="sekai-settings", role="button", name=a["name"], all=True) or [])
                 if x["cy"] is not None and abs(x["cx"] - a["cx"]) < 40 and abs(x["cy"] - a["cy"]) < 80]
            if b and (b[0]["cx"], b[0]["cy"]) == (a["cx"], a["cy"]):
                return True
            if b:
                ok[0] = b[0]
            return False
        t.wait(settled, 5)
        t.shot("확인창")
        t.click(ok[0]["cx"], ok[0]["cy"])
    t.auth()
    t.expect(t.wait(lambda: not installed(t, APP), 180, every=3), "제거됨 (dpkg)")
    t.sh("pkill -f /usr/bin/sekai-setting[s]; true")


def _make_deb():
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/p/DEBIAN")
    os.makedirs(f"{d}/p/usr/share/doc/zz-mm-test")
    with open(f"{d}/p/DEBIAN/control", "w") as f:
        f.write("Package: zz-mm-test\nVersion: 1.0\nArchitecture: all\nMaintainer: MafuyuMom <mm@example.com>\n"
                "Description: MafuyuMom test package\n")
    with open(f"{d}/p/usr/share/doc/zz-mm-test/README", "w") as f:
        f.write("mm\n")
    subprocess.run(["dpkg-deb", "-Zxz", "--root-owner-group", "-b", f"{d}/p", f"{d}/zz-mm-test.deb"],
                   check=True, capture_output=True)
    return f"{d}/zz-mm-test.deb"


@test("앱 설치 관리자로 .deb 설치 (두 번 눌러 열기 → 설치 → 인증)", suite="apps", timeout=300, quick=True)
def deb_install(t):
    if installed(t, "zz-mm-test"):
        t.root("apt-get remove -y -q zz-mm-test >/dev/null 2>&1")
    remote.push(_make_deb(), "/home/miku/zz-mm-test.deb")
    t.sh("hyprctl dispatch exec 'sekai-appinstall /home/miku/zz-mm-test.deb' >/dev/null")
    btn = t.ui.wait(app="(?i)appinstall|AppInstaller|앱 설치", role="button", name="설치", timeout=30, sensitive=True)
    t.shot("설치관리자")
    t.expect(btn, "앱 설치 관리자에 [설치]")
    t.click(btn["cx"], btn["cy"])
    t.auth()
    t.expect(t.wait(lambda: installed(t, "zz-mm-test"), 120, every=2), "설치됨 (dpkg)")
    t.shot("설치끝")
    t.sh("pkill -f /usr/bin/sekai-appinstal[l]; true")
    t.root("apt-get remove -y -q zz-mm-test >/dev/null 2>&1; rm -f /home/miku/zz-mm-test.deb")
