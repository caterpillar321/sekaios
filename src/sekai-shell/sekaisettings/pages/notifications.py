"""알림 — 토스트 동작과 기록."""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import Page, button, info, row, slider, spin, switch

# 방해 금지는 '설정'이 아니라 '지금 상태'라서 패널과 같은 상태 파일을 쓴다.
try:
    from sekaishell import config as shellconf
except Exception:          # 패널 모듈이 없는 환경(개발 중)
    shellconf = None


def _hist_count():
    if shellconf is None:
        return 0
    path = os.path.join(shellconf.STATE_DIR, "notifications.json")
    try:
        import json
        with open(path, encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return 0


def build(store):
    p = Page("알림", "알림 팝업과 알림 센터의 동작을 정합니다.")
    n = store.get("notifications")

    s = p.section("표시")
    if shellconf is not None:
        dnd_row = row(
            s, "방해 금지", "켜면 팝업을 띄우지 않고 알림 센터에만 쌓입니다",
            icon=["notifications-disabled", "preferences-system-notifications",
                  "dialog-information"],
            control=switch(shellconf.state("dnd", False),
                           lambda v: shellconf.set_state("dnd", bool(v))))
        _ = dnd_row
    row(s, "팝업이 떠 있는 시간", "초. 긴급 알림은 직접 닫을 때까지 남습니다",
        icon=["preferences-system-time", "alarm"],
        control=slider(n["timeout"], 2, 30, 1,
                       lambda v: store.set("notifications", "timeout", v)))

    s = p.section("기록")
    row(s, "보관할 알림 개수", None,
        control=spin(n["history"], 20, 1000, 10,
                     lambda v: store.set("notifications", "history", v)))
    cnt = row(s, "지금 보관 중", f"{_hist_count()}개",
              control=button("기록 지우기", lambda: _clear(cnt)))

    w = Gtk.Label(
        label="알림 센터는 작업 표시줄 오른쪽의 🔔 을 눌러 엽니다.\n"
              "알림 데몬은 sekai-panel 이 직접 제공합니다 "
              "(org.freedesktop.Notifications).", xalign=0)
    w.get_style_context().add_class("notice")
    w.set_line_wrap(True)
    p.add_widget(w)

    s = p.section("시험")
    row(s, "알림 보내기", "notify-send 로 시험 알림을 띄웁니다",
        control=button("보내기", _test))
    return p


def _clear(row_widget):
    if shellconf is None:
        return
    path = os.path.join(shellconf.STATE_DIR, "notifications.json")
    try:
        os.remove(path)
    except Exception:
        pass
    if hasattr(row_widget, "sub_label"):
        row_widget.sub_label.set_text("0개")


def _test():
    import subprocess
    subprocess.Popen(
        ["notify-send", "-a", "SekaiOS", "-i", "dialog-information",
         "시험 알림", "알림이 이렇게 보입니다."],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


PAGES = [{"id": "notifications", "title": "알림",
          "icon": ["preferences-system-notifications", "dialog-information",
                   "notification", "dialog-information-symbolic"],
          "build": build}]
