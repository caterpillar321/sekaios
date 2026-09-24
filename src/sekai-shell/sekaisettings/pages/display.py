"""디스플레이 — 해상도 / 주사율 / 배율 / 회전."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import hyprctl, spawn
from ..widgets import Page, button, combo, info, row, switch


TRANSFORMS = [(0, "가로 (기본)"), (1, "세로 90°"), (2, "가로 180°"), (3, "세로 270°"),
              (4, "가로 뒤집기"), (5, "세로 90° 뒤집기"),
              (6, "가로 180° 뒤집기"), (7, "세로 270° 뒤집기")]
SCALES = [("1.0", "100%"), ("1.25", "125%"), ("1.5", "150%"),
          ("1.75", "175%"), ("2.0", "200%")]


def _modes(mon):
    """availableModes 를 해상도@주사율 목록으로. 중복은 정리하고 높은 것부터."""
    seen, out = set(), []
    for m in mon.get("availableModes", []) or []:
        if m in seen:
            continue
        seen.add(m)
        out.append(m)

    def key(s):
        try:
            res, hz = s.split("@")
            w, h = res.split("x")
            return (int(w) * int(h), float(hz.rstrip("Hz")))
        except Exception:
            return (0, 0)

    out.sort(key=key, reverse=True)
    return out


def build(store):
    p = Page("디스플레이", "모니터의 해상도, 주사율, 배율을 바꿉니다.")
    mons = hyprctl("monitors", js=True) or []

    if not mons:
        w = Gtk.Label(label="모니터 정보를 읽지 못했습니다. "
                            "Hyprland 세션 안에서 실행해 주세요.", xalign=0)
        w.get_style_context().add_class("notice")
        p.add_widget(w)
        return p

    for mon in mons:
        name = mon.get("name", "?")
        desc = mon.get("description") or mon.get("model") or ""
        saved = store.get("display").get(name, {})

        s = p.section(f"{name}  ·  {desc}" if desc else name)

        cur_mode = "%dx%d@%.2fHz" % (mon.get("width", 0), mon.get("height", 0),
                                     mon.get("refreshRate", 0.0))
        modes = _modes(mon)
        # 현재 모드가 목록에 없으면(주사율 표기 차이) 맨 위에 끼워 넣는다
        items = [("preferred", "권장 (자동)")] + [(m, m) for m in modes]
        active = saved.get("mode") or "preferred"

        def on_mode(v, n=name):
            d = store.get("display").setdefault(n, {})
            d["mode"] = v
            store.set("display", n, d)
            # 적용됐는지 실제 모니터 상태로 확인한다.
            #   드라이버가 모드를 거부하면 Hyprland 는 조용히 권장 모드로 되돌아간다.
            GLib.timeout_add(1500, _verify, n, v)

        mode_row = row(s, "해상도 및 주사율", f"현재: {cur_mode}",
                       icon=["video-display", "preferences-desktop-display"],
                       control=combo(items, active, on_mode))

        # 드라이버가 거부했을 때의 안내 + 세션 재시작 버튼
        warn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        warn.get_style_context().add_class("notice")
        warn.set_no_show_all(True)
        warn_lbl = Gtk.Label(xalign=0)
        warn_lbl.set_line_wrap(True)
        warn.pack_start(warn_lbl, True, True, 0)
        restart = Gtk.Button(label="지금 다시 시작")
        restart.set_valign(Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_: spawn(["hyprctl", "dispatch", "exit"]))
        warn.pack_end(restart, False, False, 0)
        warn.label = warn_lbl
        p.add_widget(warn)

        def _verify(n, wanted, mode_row=mode_row, warn=warn):
            m = next((x for x in (hyprctl("monitors", js=True) or [])
                      if x.get("name") == n), None)
            if m is None:
                return False
            now = "%dx%d" % (m.get("width", 0), m.get("height", 0))
            mode_row.sub_label.set_text("현재: %s@%.2fHz" % (now, m.get("refreshRate", 0)))
            if wanted != "preferred" and not wanted.startswith(now + "@"):
                # VMware(vmwgfx)는 실행 중에 해상도를 키우는 걸 커널이 거부한다
                #   (drmModeSetCrtc: No space left on device). 세션 시작 때는 된다.
                warn.label.set_text(
                    f"{wanted.split('@')[0]} 은(는) 저장했지만 지금 바로 적용하지 못했습니다 "
                    f"(그래픽 드라이버가 거부해 {now} 로 유지).\n"
                    "세션을 다시 시작하면 적용됩니다.")
                warn.show_all()
            else:
                warn.hide()
            return False

        def on_scale(v, n=name):
            d = store.get("display").setdefault(n, {})
            d["scale"] = float(v)
            store.set("display", n, d)

        row(s, "배율", "글자와 UI 크기",
            control=combo(SCALES, "%.2g" % float(saved.get("scale", mon.get("scale", 1.0))),
                          on_scale))

        def on_tr(v, n=name):
            d = store.get("display").setdefault(n, {})
            d["transform"] = int(v)
            store.set("display", n, d)

        row(s, "화면 방향",
            control=combo(TRANSFORMS, int(saved.get("transform", mon.get("transform", 0))),
                          on_tr))

        def on_enabled(v, n=name):
            d = store.get("display").setdefault(n, {})
            d["enabled"] = bool(v)
            store.set("display", n, d)

        row(s, "이 모니터 사용", "끄면 화면이 꺼집니다",
            control=switch(saved.get("enabled", True), on_enabled))

        row(s, "위치", "다중 모니터 배치 (auto 는 자동 배열)",
            control=info(saved.get("position") or "auto"))

    s = p.section("기타")
    row(s, "기본값으로 되돌리기", "모든 모니터 설정을 지웁니다",
        control=button("되돌리기", lambda: (store.reset_section("display"),
                                          store.apply_display())))
    return p


PAGES = [{"id": "display", "title": "디스플레이",
          "icon": ["video-display", "preferences-desktop-display", "display",
                    "preferences-desktop-display-symbolic", "video-display-symbolic"],
          "build": build}]
