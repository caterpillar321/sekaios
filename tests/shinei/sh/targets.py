"""찍을 화면 — 창(앱을 띄워)과 레이어(작업 표시줄의 팝업). dynamic 은 내용이 늘 바뀌어(시계·그래프) 기준 그림과 비교하지 않는다."""

SETTINGS_PAGES = ["about", "power", "display", "graphics", "sound", "wallpaper", "appearance", "multitasking", "input",
                  "shortcuts", "bluetooth", "printers", "network", "firewall", "notifications", "locale", "defaults", "installed",
                  "wineapps", "account", "users", "a11y", "update", "recovery", "encryption"]

TARGETS = []
for _p in SETTINGS_PAGES:
    TARGETS.append({"id": f"settings-{_p}", "kind": "window", "cmd": f"sekai-settings --page={_p}", "cls": "sekai-settings",
                    "proc": "sekai-settings", "app": "sekai-settings", "dynamic": _p in ("about", "update", "recovery")})
TARGETS += [
    {"id": "notepad",  "kind": "window", "cmd": "sekai-notepad", "cls": "org.sekaios.Notepad", "proc": "sekai-notepad", "app": "(?i)notepad"},
    {"id": "calc",     "kind": "window", "cmd": "sekai-calc", "cls": "org.sekaios.Calculator", "proc": "sekai-calc", "app": "Calculator"},
    {"id": "files",    "kind": "window", "cmd": "sekai-files /usr/share", "cls": "org.sekaios.Files", "proc": "sekai-files", "app": "(?i)files|탐색기"},
    {"id": "taskmgr",  "kind": "window", "cmd": "sekai-taskmgr", "cls": "sekai-taskmgr", "proc": "sekai-taskmgr", "app": "(?i)taskmgr|작업 관리자", "dynamic": True},
    # 컴퓨터 관리의 이벤트 뷰어는 긴 기록 줄을 일부러 … 로 줄인다 (윈도우와 같게) — 표 칸 잘림은 보지 않는다
    {"id": "admin",    "kind": "window", "cmd": "sekai-admin", "cls": "sekai-admin", "proc": "sekai-admin", "app": "(?i)admin", "dynamic": True,
     "ignore_roles": ["table cell"]},
    {"id": "store",    "kind": "window", "cmd": "sekai-store", "cls": "org.sekaios.Store", "proc": "sekai-store", "app": "Store", "dynamic": True, "settle": 4},
    {"id": "terminal", "kind": "window", "cmd": "sekai-terminal", "cls": "org.sekaios.Nenerobo", "proc": "nenerobo", "app": "(?i)nenerobo|terminal|터미널"},
    {"id": "startmenu", "kind": "layer", "open": "key:meta_l", "close": "key:esc", "app": "sekai-panel"},
    {"id": "quicksettings", "kind": "layer", "open": "sh:sekai-ctl quicksettings", "close": "key:esc", "app": "sekai-panel"},
    {"id": "notifcenter", "kind": "layer", "open": "sh:sekai-ctl notifications", "close": "key:esc", "app": "sekai-panel", "dynamic": True},
]


def by_id(ids):
    if not ids:
        return TARGETS
    sel = [t for t in TARGETS if any(t["id"] == i or t["id"].startswith(i + "-") for i in ids)]
    if not sel:
        raise SystemExit(f"모르는 대상: {', '.join(ids)}")
    return sel
