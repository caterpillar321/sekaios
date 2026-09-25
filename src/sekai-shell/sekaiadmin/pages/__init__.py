"""컴퓨터 관리 페이지들 — 페이지 모듈의 규칙은 app.py 머리 설명.

MODULES 에 적힌 모듈을 차례로 가져와 PAGE 를 모은다. 파일이 없는 모듈(아직 만들지 않은 페이지)은
조용히 건너뛰고, 가져오다 오류가 난 모듈은 그 오류를 찍고 건너뛴다 (창은 뜬다).
메뉴 순서는 묶음(GROUPS 순서) → PAGE["order"] (작을수록 위).
"""
import importlib
import sys
import traceback

GROUPS = [("system", "시스템 도구"), ("storage", "저장소"), ("services", "서비스 및 응용 프로그램")]

# 모듈 이름과 자리 (order 는 각 모듈의 PAGE 가 정한다 — 아래는 약속한 값)
#   system:   events 10 · scheduler 20 · users 30 · devices 40
#   storage:  disks 10
#   services: services 10
MODULES = ["events", "scheduler", "users", "devices", "disks", "services"]

_REQUIRED = ("id", "title", "group", "build")


def _load(name):
    full = f"{__name__}.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name == full:                   # 파일이 없다 — 아직 없는 페이지
            return None
        traceback.print_exc()
    except Exception:
        traceback.print_exc()
    print(f"[sekai-admin] 페이지 '{name}'을(를) 불러오지 못해 메뉴에서 뺍니다", file=sys.stderr, flush=True)
    return None


def all_pages():
    gidx = {g: i for i, (g, _t) in enumerate(GROUPS)}
    pages, seen = [], set()
    for name in MODULES:
        m = _load(name)
        pg = getattr(m, "PAGE", None) if m is not None else None
        if m is not None and not (isinstance(pg, dict) and all(k in pg for k in _REQUIRED)):
            print(f"[sekai-admin] '{name}' 모듈에 올바른 PAGE 가 없습니다", file=sys.stderr, flush=True)
            continue
        if pg is None or pg["id"] in seen or pg["group"] not in gidx:
            continue
        seen.add(pg["id"])
        pages.append(pg)
    pages.sort(key=lambda p: (gidx[p["group"]], p.get("order", 50)))
    return pages
