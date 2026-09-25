"""작업 표시줄에 고정한 앱.

~/.config/sekai/pinned.json 에 앱 id(.desktop 파일 이름에서 .desktop 을 뺀 것) 목록.
id 는 파일 이름의 대소문자 그대로 적고, 비교만 대소문자를 가리지 않는다 —
Gio 는 데스크톱 id 의 대소문자를 가려서 소문자로 적으면 org.gnome.Evince 같은 앱을 못 찾았다
(꺼져 있을 때 작업 표시줄에서 사라졌다). 예전 판이 소문자로 적은 항목도 읽을 때 실제 이름으로 찾는다.
시작 메뉴·작업 표시줄의 우클릭 메뉴로 고정/해제한다.
실행 중인 창과 고정한 앱은 창의 클래스로 짝짓는다 (appicon 과 같은 규칙).
"""
import json
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio  # noqa: E402

PIN_FILE = os.path.expanduser("~/.config/sekai/pinned.json")
DEFAULT_PINS = ["sekai-files", "chromium"]     # 윈도우의 파일 탐색기·브라우저처럼
# 다른 DE 의 앱 → 그것을 대신하는 SekaiOS 앱. 고정 목록(작업 표시줄·시작 메뉴)에 옛 id 가 남아 있으면
#   새 앱이 설치돼 있을 때 새 것으로 보여 준다 (파일은 다음에 고칠 때 새 id 로 적힌다)
REPLACED = {"thunar": "sekai-files", "org.xfce.mousepad": "sekai-notepad", "mousepad": "sekai-notepad",
            "galculator": "sekai-calc", "org.xfce.ristretto": "sekai-photos"}
_class_cache = {}
_index = None                                  # 소문자 id → 실제 id (앱 목록에서 한 번에 만든다)
_monitor = None
_listeners = []


def _forget(*_):
    global _index
    _index = None
    _class_cache.clear()


def _ids():
    """앱을 깔거나 지우면 다시 만든다 (지운 앱이 남거나 새 앱을 못 찾지 않게)"""
    global _index, _monitor
    if _monitor is None:
        _monitor = Gio.AppInfoMonitor.get()
        _monitor.connect("changed", _forget)
    if _index is None:
        _index = {}
        for app in Gio.AppInfo.get_all():           # NoDisplay 앱도 들어 있다
            aid = app.get_id() or ""
            if aid.endswith(".desktop"):
                _index.setdefault(aid[:-len(".desktop")].lower(), aid[:-len(".desktop")])
    return _index


def real_id(app_id):
    """대소문자를 가리지 않고 찾은 실제 데스크톱 id (.desktop 뺀 것). 없으면 None"""
    if not app_id:
        return None
    found = _ids().get(app_id.lower())
    if found != app_id and _exists(app_id):
        return app_id                           # 대소문자만 다른 파일이 둘이면 적힌 그대로를
    return found


def replaced_id(app_id):
    """옛 앱 id 를 대신하는 SekaiOS 앱 id (그 앱이 없으면 그대로)"""
    new = REPLACED.get((app_id or "").lower())
    if new and os.path.exists(f"/usr/share/applications/{new}.desktop"):
        return new
    return app_id


def load():
    try:
        with open(PIN_FILE, encoding="utf-8") as f:
            v = json.load(f)
        if isinstance(v, list):
            out, seen = [], set()
            for x in v:
                x = replaced_id(str(x))
                aid = real_id(x) or x
                if aid and aid.lower() not in seen:
                    seen.add(aid.lower())
                    out.append(aid)
            return out
    except FileNotFoundError:
        return [p for p in DEFAULT_PINS if _exists(p)]
    except (OSError, ValueError):
        pass
    return []


def save(pins):
    os.makedirs(os.path.dirname(PIN_FILE), exist_ok=True)
    tmp = PIN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pins, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PIN_FILE)
    for cb in list(_listeners):
        try:
            cb()
        except Exception:
            pass


def on_change(cb):
    _listeners.append(cb)


def is_pinned(app_id):
    return bool(app_id) and app_id.lower() in {p.lower() for p in load()}


def pin(app_id):
    pins = load()
    if app_id and app_id.lower() not in {p.lower() for p in pins}:
        pins.append(real_id(app_id) or app_id)
        save(pins)


def unpin(app_id):
    pins = load()
    keep = [p for p in pins if not app_id or p.lower() != app_id.lower()]
    if len(keep) != len(pins):
        save(keep)


def _exists(app_id):
    try:
        return Gio.DesktopAppInfo.new(app_id + ".desktop") is not None
    except TypeError:
        return False


def app_info(app_id):
    aid = real_id(app_id)
    if aid is None:
        return None
    try:
        return Gio.DesktopAppInfo.new(aid + ".desktop")
    except TypeError:
        return None


def app_id_for_class(cls):
    """창 클래스 → 실제 앱 id (없으면 None). 예: 'thunar' → 'thunar', 'org.gnome.Evince' → 'org.gnome.Evince'"""
    if not cls:
        return None
    if cls in _class_cache:
        return _class_cache[cls]
    low = cls.lower()
    found = real_id(cls) or real_id(low.split(".")[-1])
    if found is None:
        for app in Gio.AppInfo.get_all():
            try:
                wm = app.get_startup_wm_class()
            except AttributeError:
                wm = None
            if wm and wm.lower() == low:
                found = (app.get_id() or "")[:-len(".desktop")] or None
                break
    _class_cache[cls] = found
    return found
