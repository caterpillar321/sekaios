"""SekaiOS 앱 실사용 — 결과는 화면보다 파일·프로세스로 확인한다 (흔들리지 않게)."""
import os
import tempfile
import time

from mm import remote
from mm.runner import test

HOME = "/home/miku"


@test("메모장: 쓰고 저장하고 다시 열기", suite="use", quick=True)
def notepad_save(t):
    t.kill("sekai-notepad")
    t.sh(f"rm -f {HOME}/mm-note.txt")
    t.after(lambda: t.kill("sekai-notepad"))
    t.gone("org.sekaios.Notepad", 5)
    w = t.launch("sekai-notepad", "org.sekaios.Notepad")
    t.expect(w, "메모장")
    t.click(w["at"][0] + 300, w["at"][1] + 250)
    t.type("MafuyuMom note 123")
    t.key("ctrl-s")
    dlg = t.ui.wait(app="(?i)notepad", role="text", timeout=8)
    time.sleep(0.8)
    t.shot("저장창")
    t.key("ctrl-a")
    t.type(f"{HOME}/mm-note.txt")
    t.key("ret")
    t.expect(t.wait(lambda: t.sh(f"cat {HOME}/mm-note.txt").out.strip() == "MafuyuMom note 123", 8),
             "파일에 쓴 글이 그대로 저장됐다")
    t.kill("sekai-notepad")
    t.gone("org.sekaios.Notepad", 5)
    w = t.launch(f"sekai-notepad {HOME}/mm-note.txt", "org.sekaios.Notepad")
    t.expect(w and "mm-note" in (w.get("title") or ""), f"다시 열기 — 제목 {w and w.get('title')}")
    body = t.ui.text(app="(?i)notepad", role="text", largest=True) or ""
    t.expect("MafuyuMom note 123" in body, f"내용이 다시 보인다 ({body[:40]!r})")
    t.kill("sekai-notepad")


@test("계산기: 단추로 7 + 5 = 12", suite="use", quick=True)
def calculator(t):
    t.close("org.sekaios.Calculator")
    t.expect(t.launch("sekai-calc", "org.sekaios.Calculator"), "계산기")
    for name in ("7", "+", "5", "="):
        t.expect(t.ui.click(app="Calculator", role="button", name=name, timeout=5), f"[{name}] 단추")
        time.sleep(0.2)
    t.shot("결과")
    labels = [n["name"] for n in (t.ui.find(app="Calculator", role="label", all=True) or [])]
    t.expect(any(l.replace(",", "").strip() == "12" for l in labels), f"12 가 보인다 ({labels[:6]})")
    t.close("org.sekaios.Calculator")


@test("탐색기: 새 폴더 → 이름 바꾸기 → 휴지통 → 되돌리기", suite="use", quick=True)
def files_ops(t):
    t.sh(f"rm -rf {HOME}/새\\ 폴더* {HOME}/mmdir* ; gio trash --empty 2>/dev/null; pkill -f /usr/bin/sekai-file[s]; true")
    w = t.launch(f"sekai-files {HOME}", "org.sekaios.Files")
    t.expect(w, "탐색기")
    time.sleep(1.5)
    t.click(w["at"][0] + w["size"][0] // 2, w["at"][1] + w["size"][1] - 120)   # 빈 자리 (초점)
    t.key("ctrl-shift-n")
    time.sleep(1)
    t.key("ctrl-a")
    t.type("mmdir")
    t.key("ret")
    t.expect(t.wait(lambda: t.sh(f"test -d {HOME}/mmdir").ok, 5), "새 폴더 mmdir 이 생겼다")
    t.shot("새폴더")
    t.key("f2")
    time.sleep(0.8)
    t.key("ctrl-a")
    t.type("mmdir2")
    t.key("ret")
    t.expect(t.wait(lambda: t.sh(f"test -d {HOME}/mmdir2 && ! test -e {HOME}/mmdir").ok, 5), "F2 로 이름 바꾸기 → mmdir2")
    t.key("delete")
    t.expect(t.wait(lambda: t.sh(f"! test -e {HOME}/mmdir2 && test -d {HOME}/.local/share/Trash/files/mmdir2").ok, 5),
             "Delete → 휴지통으로")
    t.key("ctrl-z")
    restored = t.wait(lambda: t.sh(f"test -d {HOME}/mmdir2").ok, 5)
    if restored:
        t.note("✓ Ctrl+Z 로 되돌렸다")
    else:
        t.finding("탐색기에서 휴지통으로 보낸 뒤 Ctrl+Z 로 되돌리기가 되지 않는다 (윈도우 탐색기는 된다)")
    t.sh(f"rm -rf {HOME}/mmdir* ; gio trash --empty 2>/dev/null; pkill -f /usr/bin/sekai-file[s]; true")


@test("사진: 그림 파일을 연다", suite="use")
def photos(t):
    from PIL import Image
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mm-photo.png")
    Image.new("RGB", (640, 400), (57, 197, 187)).save(p)
    remote.push(p, f"{HOME}/mm-photo.png")
    t.sh("pkill -f /usr/bin/sekai-photo[s]; true")
    w = t.launch(f"sekai-photos {HOME}/mm-photo.png", "org.sekaios.Photos")
    t.expect(w, "사진 창")
    t.expect("mm-photo" in (w.get("title") or "") or t.wait(lambda: "mm-photo" in (t.window("org.sekaios.Photos") or {}).get("title", ""), 5),
             "제목에 파일 이름")
    t.shot("사진")
    t.sh(f"pkill -f /usr/bin/sekai-photo[s]; rm -f {HOME}/mm-photo.png; true")


@test("터미널(Nenerobo): 명령을 쳐서 실행", suite="use", quick=True)
def terminal(t):
    t.sh("rm -f /tmp/mm-term.txt")
    w = t.launch("sekai-terminal", "org.sekaios.Nenerobo", timeout=15)
    t.expect(w, "터미널 창")
    time.sleep(1.5)
    t.type("echo mafuyumom > /tmp/mm-term.txt\n")
    t.expect(t.wait(lambda: t.sh("cat /tmp/mm-term.txt").out.strip() == "mafuyumom", 5), "명령이 실행됐다")
    t.shot("터미널")
    t.type("exit\n")
    t.gone("org.sekaios.Nenerobo", 5)


@test("Chromium: 로컬 페이지를 연다", suite="use", timeout=120)
def chromium(t):
    html = "<!doctype html><title>MM page</title><h1>MafuyuMom</h1>"
    t.sh(f"printf '%s' '{html}' > /tmp/mm.html; pkill chromium; true")
    t.gone("chromium", 8)
    t.sh("hyprctl dispatch exec 'chromium --no-first-run --no-default-browser-check file:///tmp/mm.html' >/dev/null")
    w = t.wait(lambda: [c for c in t.clients("chromium") if "MM page" in (c.get("title") or "")], 30)
    t.expect(w, "Chromium 창 제목에 페이지 제목")
    t.shot("크롬")
    t.sh("pkill chromium; true")


@test("작업 관리자: 프로세스를 골라 끝내기", suite="use", timeout=120)
def taskmgr(t):
    t.kill("sekai-taskmgr")
    t.sh("setsid sleep 997 >/dev/null 2>&1 < /dev/null &")
    t.after(lambda: (t.kill("sekai-taskmgr"), t.sh("pkill -f 'slee[p] 997'; true")))
    w = t.wait(lambda: t.launch("sekai-taskmgr", "sekai-taskmgr", timeout=10) or t.window("org.sekaios.TaskManager", 3), 15)
    t.expect(w, "작업 관리자 창")
    time.sleep(2)
    t.shot("작업관리자")
    srch = t.ui.click(app="(?i)taskmgr|작업 관리자", role="text", timeout=5)   # 이름·사용자·PID 검색 칸
    t.expect(srch, "검색 칸")
    t.type("sleep")
    time.sleep(1.5)
    cell = t.ui.wait(app="(?i)taskmgr|작업 관리자", name="sleep", timeout=10)
    t.shot("검색")
    t.expect(cell, "목록에 sleep")
    t.click(cell["cx"], cell["cy"])
    end = t.ui.wait(app="(?i)taskmgr|작업 관리자", role="button", name_re="작업 끝내기|끝내기", timeout=5, sensitive=True)
    t.expect(end, "[작업 끝내기] 단추")
    t.click(end["cx"], end["cy"])
    ok = t.ui.wait(app="(?i)taskmgr|작업 관리자", role="button", name_re="^끝내기$|^예$|프로세스 끝내기", timeout=3)
    if ok:
        t.click(ok["cx"], ok["cy"])
    t.expect(t.wait(lambda: not t.sh("pgrep -f 'slee[p] 997'").ok, 5), "sleep 997 이 끝났다")


@test("컴퓨터 관리: 서비스·이벤트 뷰어·장치 관리자·디스크 관리 페이지", suite="use", timeout=150)
def admin_pages(t):
    t.sh("pkill -f /usr/bin/sekai-admi[n]; true")
    t.sh("hyprctl dispatch exec sekai-admin >/dev/null")
    w = t.wait(lambda: [c for c in t.clients() if "admin" in (c.get("class") or "").lower() or c.get("title") == "컴퓨터 관리"], 15)
    t.expect(w, "컴퓨터 관리 창")
    for page in ("서비스", "이벤트 뷰어", "장치 관리자", "디스크 관리"):
        hit = t.ui.click(app="(?i)admin", name=page, timeout=5)
        t.expect(hit, f"[{page}] 페이지로")
        time.sleep(2.5)
        t.shot(page)
    t.sh("pkill -f /usr/bin/sekai-admi[n]; true")
