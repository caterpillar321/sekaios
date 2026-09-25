"""설정 페이지들.

각 모듈은 PAGES 리스트를 노출한다:
    {"id": str, "title": str, "icon": [아이콘 후보...], "build": f(store) -> Gtk.Widget,
     "sections": (설정 섹션...)}
build 는 페이지를 처음 열 때 한 번만 불린다 (지연 생성).
sections 는 이 페이지가 화면에 보여 주는 설정(store) 섹션 — 되돌리기(reset_section)는 그 섹션을 쓰는
페이지만 다시 그린다. 없으면 되돌리기로 다시 그리지 않는다.
오래 걸리는 작업이 도는 동안은 페이지(widgets.Page)의 busy 를 True 로 — 그동안은 다시 그리지 않는다.
"""
from . import (about, display, graphics, personalize, devices, network,  # noqa: F401
               notifications, locale, apps, users, update)
from . import bluetooth  # noqa: F401

MODULES = [about, display, graphics, personalize, devices, bluetooth, network,
           notifications, locale, apps, users, update]


def all_pages():
    out = []
    for m in MODULES:
        out.extend(m.PAGES)
    return out
