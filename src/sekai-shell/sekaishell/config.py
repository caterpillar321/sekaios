"""설정/상태 파일 읽기.

  ~/.config/sekai/settings.json      사용자가 설정 앱에서 바꾸는 값 (여기서는 읽기만)
  ~/.local/state/sekai/notify.json   런타임 상태 (방해 금지 등). 여기서 읽고 쓴다.

설정 앱과 같은 파일을 양쪽에서 쓰면 서로 덮어쓰므로,
'설정'과 '상태'를 파일 단위로 갈라 둔다.
"""
import json
import os

CFG = os.path.expanduser("~/.config/sekai/settings.json")
STATE_DIR = os.path.expanduser("~/.local/state/sekai")
STATE = os.path.join(STATE_DIR, "notify.json")


def settings(section=None, key=None, default=None):
    try:
        with open(CFG, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if section is None:
        return data
    sec = data.get(section) or {}
    if key is None:
        return sec
    return sec.get(key, default)


def state(key=None, default=None):
    try:
        with open(STATE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if key is None:
        return data
    return data.get(key, default)


def set_state(key, value):
    data = state()
    data[key] = value
    os.makedirs(STATE_DIR, exist_ok=True)
    # 임시 파일은 프로세스마다 따로 — 패널과 설정 앱이 같은 때 쓰면 한 임시 파일을 서로 덮어 깨뜨린다
    tmp = f"{STATE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)
    return value
