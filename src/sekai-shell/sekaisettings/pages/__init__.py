"""설정 페이지들.

각 모듈은 PAGES 리스트를 노출한다:
    {"id": str, "title": str, "icon": [아이콘 후보...], "build": f(store) -> Gtk.Widget}
build 는 페이지를 처음 열 때 한 번만 불린다 (지연 생성).
"""
from . import (about, display, personalize, devices, network,  # noqa: F401
               notifications, locale, apps, update)

MODULES = [about, display, personalize, devices, network,
           notifications, locale, apps, update]


def all_pages():
    out = []
    for m in MODULES:
        out.extend(m.PAGES)
    return out
