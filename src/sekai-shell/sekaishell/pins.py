"""작업 표시줄에 고정한 앱.

~/.config/sekai/pinned.json 에 앱 id(.desktop 파일 이름에서 .desktop 을 뺀 것, 소문자) 목록.
시작 메뉴·작업 표시줄의 우클릭 메뉴로 고정/해제한다.
실행 중인 창과 고정한 앱은 창의 클래스로 짝짓는다 (appicon 과 같은 규칙).
"""
import json
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio  # noqa: E402

PIN_FILE = os.path.expanduser("~/.config/sekai/pinned.json")
DEFAULT_PINS = ["thunar", "chromium"]          # 윈도우의 파일 탐색기·브라우저처럼
_class_cache = {}
_listeners = []


def load():
    try:
        with open(PIN_FILE, encoding="utf-8") as f:
            v = json.load(f)
        if isinstance(v, list):
            return [str(x).lower() for x in v]
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
    return bool(app_id) and app_id.lower() in load()


def pin(app_id):
    pins = load()
    if app_id and app_id.lower() not in pins:
        pins.append(app_id.lower())
        save(pins)


def unpin(app_id):
    pins = load()
    if app_id and app_id.lower() in pins:
        pins.remove(app_id.lower())
        save(pins)


def _exists(app_id):
    try:
        return Gio.DesktopAppInfo.new(app_id + ".desktop") is not None
    except TypeError:
        return False


def app_info(app_id):
    try:
        return Gio.DesktopAppInfo.new(app_id + ".desktop")
    except TypeError:
        return None


def app_id_for_class(cls):
    """창 클래스 → 앱 id (없으면 None). 예: 'thunar' → 'thunar', 'org.xfce.mousepad' → 'org.xfce.mousepad'"""
    if not cls:
        return None
    if cls in _class_cache:
        return _class_cache[cls]
    low = cls.lower()
    found = None
    for cand in (cls, low, low.split(".")[-1]):
        if _exists(cand):
            found = cand.lower()
            break
    if found is None:
        for app in Gio.AppInfo.get_all():
            try:
                wm = app.get_startup_wm_class()
            except AttributeError:
                wm = None
            if wm and wm.lower() == low:
                found = (app.get_id() or "")[:-len(".desktop")].lower() or None
                break
    _class_cache[cls] = found
    return found
