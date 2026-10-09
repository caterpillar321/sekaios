"""설정 — 모든 페이지가 오류 없이 열리는가, 그리고 실제로 바꾸면 반영되는가."""
import time

from mm.runner import test

SETTINGS = "sekai-settings"
PAGES = ["about", "power", "display", "graphics", "sound", "wallpaper", "appearance", "multitasking", "input",
         "shortcuts", "bluetooth", "printers", "network", "firewall", "notifications", "locale", "defaults", "installed",
         "account", "users", "a11y", "update", "recovery"]


def open_page(t, page):
    t.sh("pkill -f /usr/bin/sekai-setting[s]; true")
    t.gone(SETTINGS, 5)
    return t.launch(f"sekai-settings --page={page}", SETTINGS, timeout=20)


def store_set(t, section, key, value):
    import json
    import shlex
    code = f"from sekaishell.store import Store; Store().set({json.dumps(section)}, {json.dumps(key)}, {json.dumps(value)})"
    return t.sh(f"python3 -c {shlex.quote(code)}")


def make_page_test(page):
    def _(t):
        w = open_page(t, page)
        t.expect(w, "설정 창이 떴다")
        time.sleep(2.5)                                   # 페이지가 내용을 채우는 시간 (Traceback 은 불변식이 잡는다)
        t.expect(t.clients(SETTINGS), "창이 살아 있다")
        t.shot(page)
        nameless = t.ui.unnamed("sekai-settings")
        if nameless:
            t.finding(f"이름 없는 단추·스위치 {len(nameless)}개 (내레이터가 이름을 못 읽는다)")
    _.__name__ = f"page_{page}"
    return test(f"설정 › {page} 페이지가 열린다", suite="settings", quick=page in ("about", "a11y"))(_)


for _p in PAGES:
    make_page_test(_p)


@test("라이트·다크 모드 바꾸기 → 앱 테마(gsettings)·작업 표시줄이 따른다", suite="settings", quick=True)
def theme_mode(t):
    def scheme():
        return t.sh("gsettings get org.gnome.desktop.interface color-scheme").out.strip()
    try:
        t.sh("python3 -c 'from sekaishell.store import Store; Store().set_mode(\"light\")'")
        t.expect(t.wait(lambda: "light" in scheme() or "default" in scheme(), 5), f"라이트 → color-scheme {scheme()}")
        t.expect("Sekai-Light" in t.sh("gsettings get org.gnome.desktop.interface gtk-theme").out, "GTK 테마 Sekai-Light")
        t.shot("라이트")
    finally:
        t.sh("python3 -c 'from sekaishell.store import Store; Store().set_mode(\"dark\")'")
    t.expect(t.wait(lambda: "dark" in scheme(), 5), f"다크로 되돌림 → {scheme()}")


@test("텍스트 크기 150% → 반영, 되돌리기", suite="settings")
def text_scale(t):
    try:
        store_set(t, "a11y", "text_scale", 1.5)
        v = t.wait(lambda: t.sh("gsettings get org.gnome.desktop.interface text-scaling-factor").out.strip() == "1.5", 5)
        t.expect(v, "text-scaling-factor 1.5")
        open_page(t, "a11y")
        time.sleep(2)
        t.shot("150퍼센트")
    finally:
        store_set(t, "a11y", "text_scale", 1.0)
        t.sh("pkill -f /usr/bin/sekai-setting[s]; true")


@test("설정 앱에서 스위치를 눌러 애니메이션 끄기 → 합성기 반영", suite="settings")
def toggle_animations(t):
    def anim():
        return (t.hypr("getoption animations:enabled") or {}).get("int")
    open_page(t, "a11y")
    time.sleep(2)
    before = anim()
    sw = t.ui.find(app="sekai-settings", role="toggle button", all=True) or []
    t.note(f"스위치 {len(sw)}개 보임")
    # 애니메이션 효과 줄의 스위치 — 라벨 옆(같은 높이)에서 가장 가까운 스위치
    lab = t.ui.find(app="sekai-settings", role="label", name="애니메이션 효과")
    t.expect(lab, "'애니메이션 효과' 줄이 보인다")
    cand = [s for s in sw if s["cy"] is not None and abs(s["cy"] - lab["cy"]) < 30] if lab else []
    if not cand:   # GTK 스위치의 역할 이름이 다를 수 있다
        cand = [s for s in (t.ui.find(app="sekai-settings", role="switch", all=True) or [])
                if s["cy"] is not None and abs(s["cy"] - lab["cy"]) < 30]
    t.expect(cand, "그 줄의 스위치를 찾았다")
    t.click(cand[0]["cx"], cand[0]["cy"])
    t.expect(t.wait(lambda: anim() != before, 5), f"animations:enabled {before} → {anim()}")
    t.click(cand[0]["cx"], cand[0]["cy"])
    t.expect(t.wait(lambda: anim() == before, 5), "다시 눌러 되돌렸다")
    t.sh("pkill -f /usr/bin/sekai-setting[s]; true")
