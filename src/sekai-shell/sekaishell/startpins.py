"""시작 메뉴에 고정한 앱 (오른쪽 타일) — 작업 표시줄 고정(pins.py)과 따로.

~/.config/sekai/start-pins.json 에 앱 id(.desktop 파일 이름에서 .desktop 을 뺀 것, 소문자) 목록.
파일이 없으면 기본값(설치된 것 중 파일·웹·터미널·편집기·계산기·설정)을 쓰고, 처음 고치는 순간 적는다.
"""
import json
import os

from .pins import replaced_id

PIN_FILE = os.path.expanduser("~/.config/sekai/start-pins.json")


def load(defaults):
    """고정 목록 — defaults 는 파일이 없을 때 쓸 id 목록"""
    try:
        with open(PIN_FILE, encoding="utf-8") as f:
            v = json.load(f)
        if isinstance(v, list):
            out = []
            for x in v:
                aid = replaced_id(str(x)).lower() if x else ""
                if aid and aid not in out:
                    out.append(aid)
            return out
    except FileNotFoundError:
        return list(defaults)
    except (OSError, ValueError):
        pass
    return list(defaults)


def save(ids):
    os.makedirs(os.path.dirname(PIN_FILE), exist_ok=True)
    tmp = f"{PIN_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PIN_FILE)


def is_pinned(app_id, defaults):
    return bool(app_id) and app_id.lower() in load(defaults)


def pin(app_id, defaults):
    ids = load(defaults)
    if app_id and app_id.lower() not in ids:
        ids.append(app_id.lower())
        save(ids)


def unpin(app_id, defaults):
    ids = load(defaults)
    if app_id and app_id.lower() in ids:
        ids.remove(app_id.lower())
        save(ids)
