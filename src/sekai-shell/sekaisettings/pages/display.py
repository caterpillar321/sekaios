"""디스플레이 — 해상도 / 주사율 / 배율 / 회전.

바꾸면 바로 적용하고 "유지할까요?"를 15초 동안 묻는다. 답이 없으면 되돌린다
(모니터가 못 받아들이는 모드라 화면이 안 보여도 저절로 돌아오게).
"""
import copy

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import hyprctl, keyword, spawn
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


CONFIRM_SECS = 15
VRR = [("0", "끄기"), ("1", "켜기"), ("2", "전체 화면일 때만 (게임·영상)")]


def _split(mode):
    """'2560x1440@164.96Hz' → ('2560x1440', '164.96')"""
    res, _, hz = (mode or "").partition("@")
    return res, hz.rstrip("Hz")


def _by_res(modes):
    """해상도 → 주사율 목록 (높은 것부터). 해상도 순서는 modes 순서(큰 것부터) 그대로."""
    out = {}
    for m in modes:
        res, hz = _split(m)
        if res and hz:
            out.setdefault(res, [])
            if hz not in out[res]:
                out[res].append(hz)
    for res in out:
        out[res] = _dedup_rates(sorted(out[res], key=lambda h: -float(h)))
    return out


VENDORS = {"ASUSTek COMPUTER INC": "ASUS", "Samsung Electric Company": "Samsung",
           "LG Electronics": "LG", "Dell Inc.": "Dell", "Hewlett Packard": "HP", "HP Inc.": "HP",
           "Lenovo Group Limited": "Lenovo", "Acer Technologies": "Acer", "BenQ Corporation": "BenQ",
           "Microstep": "MSI", "Gigabyte Technology Co. Ltd.": "Gigabyte", "AOC": "AOC",
           "Philips Consumer Electronics Company": "Philips", "VIEWSONIC CORPORATION": "ViewSonic"}


def _monitor_title(mon):
    """'ASUS VG249QE5A' — 제조사 긴 이름 줄이고 시리얼 번호는 뺀다"""
    make = (mon.get("make") or "").strip()
    model = (mon.get("model") or "").strip()
    if not (make or model):
        return mon.get("name", "?")
    make = VENDORS.get(make, make)
    return f"{make} {model}".strip()


def _nice_mode(w, h, hz):
    return f"{w} × {h}, {_hz_label(str(hz))}"


def _dedup_rates(hzs):
    """59.94 와 60, 119.88 과 120 처럼 사실상 같은 주사율(NTSC 표기 차이)은 정수 쪽만 보인다"""
    ints = {round(float(h)) for h in hzs if abs(float(h) - round(float(h))) < 0.02}
    out = []
    for h in hzs:
        v = float(h)
        if abs(v - round(v)) >= 0.02 and round(v * 1.001) in ints:
            continue
        if any(abs(v - float(o)) < 0.05 for o in out):     # 59.94 와 59.93 같은 것
            continue
        out.append(h)
    return out


def _hz_label(hz):
    v = float(hz)
    return f"{v:.0f} Hz" if abs(v - round(v)) < 0.02 else f"{v:.2f} Hz"


def _scale_id(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "1.0"
    return next((k for k, _ in SCALES if abs(float(k) - v) < 0.01), "1.0")


def build(store):
    p = Page("디스플레이", "모니터의 해상도, 주사율, 배율을 바꿉니다.")
    mons = hyprctl("monitors", js=True) or []
    ctl = {}                       # 모니터 이름 → 되돌릴 때 다시 맞출 위젯들
    quiet = {"on": False}          # 되돌리며 위젯을 바꿀 땐 변경 처리를 하지 않는다

    def sync_widgets():
        quiet["on"] = True
        try:
            for n, w in ctl.items():
                d = store.get("display").get(n, {})
                res, hz = _split(d.get("mode"))
                if not w["res"].set_active_id(res or "preferred"):
                    w["res"].set_active_id("preferred")
                w["fill"](w["rate"], w["res"].get_active_id(), hz)
                w["vrr"].set_active_id(str(int(d.get("vrr", 0))))
                w["scale"].set_active_id(_scale_id(d.get("scale", 1.0)))
                w["tr"].set_active_id(str(int(d.get("transform", 0))))
                w["en"].set_active(d.get("enabled", True))
        finally:
            quiet["on"] = False

    def restore(snap):
        cur = store.get("display")
        store.data["display"] = snap
        store.save()
        store.apply_display()
        # 이번에 처음 설정한 모니터는 설정이 없던 상태(권장 모드)로
        for n in cur:
            if n not in snap:
                keyword("monitor", f"{n}, preferred, auto, 1")
        sync_widgets()

    def confirm(snap):
        dlg = Gtk.MessageDialog(transient_for=p.get_toplevel() if p.get_toplevel().is_toplevel()
                                else None, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text="이 화면 설정을 유지할까요?")
        dlg.add_button("되돌리기", Gtk.ResponseType.REJECT)
        dlg.add_button("유지", Gtk.ResponseType.ACCEPT)
        dlg.set_default_response(Gtk.ResponseType.REJECT)
        left = {"n": CONFIRM_SECS}

        def tick():
            left["n"] -= 1
            if left["n"] <= 0:
                dlg.response(Gtk.ResponseType.REJECT)
                return False
            dlg.format_secondary_text(f"{left['n']}초 뒤 원래대로 되돌립니다.")
            return True
        dlg.format_secondary_text(f"{CONFIRM_SECS}초 뒤 원래대로 되돌립니다.")
        src = GLib.timeout_add_seconds(1, tick)

        def done(d, resp):
            GLib.source_remove(src) if left["n"] > 0 else None
            d.destroy()
            if resp != Gtk.ResponseType.ACCEPT:
                restore(snap)
        dlg.connect("response", done)
        dlg.show_all()

    def change(n, key, value, ask=True):
        if quiet["on"]:
            return
        snap = copy.deepcopy(store.get("display"))
        d = copy.deepcopy(snap.get(n, {}))
        d[key] = value
        store.set("display", n, d)
        if ask:
            confirm(snap)

    if not mons:
        w = Gtk.Label(label="모니터 정보를 읽지 못했습니다. "
                            "Hyprland 세션 안에서 실행해 주세요.", xalign=0)
        w.get_style_context().add_class("notice")
        p.add_widget(w)
        return p

    for mon in mons:
        name = mon.get("name", "?")
        saved = store.get("display").get(name, {})

        s = p.section(f"{_monitor_title(mon)}  ·  {name}")

        cur_mode = _nice_mode(mon.get("width", 0), mon.get("height", 0), mon.get("refreshRate", 0.0))
        rates = _by_res(_modes(mon))
        # "자동"이 실제로 무엇이 되는지 보여 준다 — 모니터가 알려 준 기본 모드(보통 목록 첫째, 대개 60 Hz)
        first = (mon.get("availableModes") or [""])[0]
        f_res, f_hz = _split(first)
        auto_label = (f"자동 ({f_res.replace('x', ' × ')}, {_hz_label(f_hz)})" if f_res and f_hz
                      else "자동 (모니터 권장)")
        res_items = [("preferred", auto_label)] + [
            (r, r.replace("x", " × ") + ("  (권장)" if r == f_res else "")) for r in rates]
        s_res, s_hz = _split(saved.get("mode"))

        def rate_items(res, rates=rates):            # 기본 인자로 묶는다 — 반복문의 늦은 바인딩 방지
            return [(h, _hz_label(h)) for h in rates.get(res, [])]

        def fill_rates(rc, res, active=None, rates=rates, rate_items=rate_items):
            """주사율 목록을 해상도에 맞게 다시 채운다 (신호 없이)"""
            prev, quiet["on"] = quiet["on"], True
            try:
                rc.remove_all()
                items = rate_items(res) or [("auto", "자동")]
                if active and active not in [k for k, _ in items] and rates.get(res):
                    items.append((active, _hz_label(active)))   # 저장된 값이 목록에서 빠졌으면(59.94 등) 그대로 보인다
                for k, label in items:
                    rc.append(k, label)
                if not (active and rc.set_active_id(active)):
                    rc.set_active(0)
                rc.set_sensitive(res != "preferred" and bool(rates.get(res)))
            finally:
                quiet["on"] = prev

        def apply_mode(n, res, hz):
            v = "preferred" if res == "preferred" else f"{res}@{hz}Hz"
            change(n, "mode", v)
            # 적용됐는지 실제 모니터 상태로 확인한다.
            #   드라이버가 모드를 거부하면 Hyprland 는 조용히 권장 모드로 되돌아간다.
            GLib.timeout_add(1500, _verify, n, v)

        def on_res(v, n=name):
            if quiet["on"] or v is None:
                return
            rc = ctl[n]["rate"]
            # 해상도를 바꾸면 그 해상도의 가장 높은 주사율로 (윈도우와 같게)
            fill_rates(rc, v)
            apply_mode(n, v, rc.get_active_id())

        def on_rate(v, n=name):
            if quiet["on"] or v is None or v == "auto":
                return
            apply_mode(n, ctl[n]["res"].get_active_id(), v)

        res_combo = combo(res_items, s_res if s_res in rates else "preferred", on_res)
        rate_combo = Gtk.ComboBoxText()
        rate_combo.connect("changed", lambda w: on_rate(w.get_active_id()))
        fill_rates(rate_combo, res_combo.get_active_id(), s_hz)

        mode_row = row(s, "해상도", f"지금: {cur_mode}",
                       icon=["video-display", "preferences-desktop-display"], control=res_combo)
        row(s, "주사율", "1초에 화면을 몇 번 새로 그리는지 — 높을수록 부드럽습니다",
            control=rate_combo)

        vrr_note = Gtk.Label(xalign=0)
        vrr_note.get_style_context().add_class("notice")
        vrr_note.set_line_wrap(True)
        vrr_note.set_no_show_all(True)

        def check_vrr(n, want, note=vrr_note):
            """켜기로 했는데 실제로 안 켜졌으면 알려 준다 (모니터·연결 단자·드라이버가 지원해야 켜진다)"""
            m = next((x for x in (hyprctl("monitors", js=True) or []) if x.get("name") == n), None)
            if want == 1 and m is not None and not m.get("vrr"):
                note.set_text("가변 주사율이 켜지지 않았습니다. 모니터가 지원하는지 확인해 주세요. "
                              "NVIDIA 카드는 HDMI 2.1 또는 DisplayPort 연결에서만 됩니다.")
                note.show()
            else:
                note.hide()
            return False

        def on_vrr(v, n=name):
            if quiet["on"]:
                return
            change(n, "vrr", int(v))
            GLib.timeout_add(1500, check_vrr, n, int(v))

        vrr_combo = combo(VRR, str(int(saved.get("vrr", 0))), on_vrr)
        row(s, "가변 주사율 (VRR)",
            "G-Sync·FreeSync — 게임의 프레임에 맞춰 주사율을 바꿔 끊김·찢어짐을 줄입니다",
            control=vrr_combo)
        p.add_widget(vrr_note)
        if int(saved.get("vrr", 0)) == 1:
            GLib.timeout_add(500, check_vrr, name, 1)

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
            mode_row.sub_label.set_text("지금: " + _nice_mode(m.get("width", 0), m.get("height", 0),
                                                              m.get("refreshRate", 0)))
            w_res, w_hz = _split(wanted)
            rate_ok = not w_hz or abs(float(w_hz) - float(m.get("refreshRate", 0))) < 0.6
            if wanted != "preferred" and (w_res != now or not rate_ok):
                # VMware(vmwgfx)는 실행 중에 해상도를 키우는 걸 커널이 거부한다
                #   (drmModeSetCrtc: No space left on device). 세션 시작 때는 된다.
                warn.label.set_text(
                    f"{w_res.replace('x', ' × ')}, {_hz_label(w_hz)} 은(는) 저장했지만 지금 바로 적용하지 "
                    f"못했습니다 (그래픽 드라이버가 거부해 "
                    f"{_nice_mode(m.get('width', 0), m.get('height', 0), m.get('refreshRate', 0))} 로 유지).\n"
                    "세션을 다시 시작하면 적용됩니다. 그래도 안 되면 케이블·연결 단자가 그 주사율을 "
                    "지원하는지 확인해 주세요.")
                warn.show_all()
            else:
                warn.hide()
            return False

        def on_scale(v, n=name):
            change(n, "scale", float(v))

        scale_combo = combo(SCALES, _scale_id(saved.get("scale", mon.get("scale", 1.0))), on_scale)
        row(s, "배율", "글자와 UI 크기", control=scale_combo)

        def on_tr(v, n=name):
            change(n, "transform", int(v))

        tr_combo = combo(TRANSFORMS, int(saved.get("transform", mon.get("transform", 0))), on_tr)
        row(s, "화면 방향", control=tr_combo)

        def on_enabled(v, n=name):
            if quiet["on"]:
                return
            if not v:
                # 켜져 있는 마지막 모니터는 끌 수 없다 — 끄면 아무것도 안 보인다
                active = [m for m in (hyprctl("monitors", js=True) or []) if not m.get("disabled")]
                if len(active) <= 1:
                    quiet["on"] = True
                    ctl[n]["en"].set_active(True)
                    quiet["on"] = False
                    return
            change(n, "enabled", bool(v))

        en_switch = switch(saved.get("enabled", True), on_enabled)
        row(s, "이 모니터 사용", "끄면 화면이 꺼집니다 (마지막 남은 모니터는 끌 수 없음)",
            control=en_switch)
        ctl[name] = {"res": res_combo, "rate": rate_combo, "fill": fill_rates, "scale": scale_combo,
                     "tr": tr_combo, "en": en_switch, "vrr": vrr_combo}

        row(s, "위치", "다중 모니터 배치 (auto 는 자동 배열)",
            control=info(saved.get("position") or "auto"))

    s = p.section("기타")
    def reset():
        names = [m.get("name") for m in mons] + list(store.get("display").keys())
        store.reset_section("display")
        # 설정이 비면 apply_display 는 아무것도 안 보낸다 → 지금 화면에도 직접 권장값을
        for n in dict.fromkeys(names):
            if n:
                keyword("monitor", f"{n}, preferred, auto, 1")
        sync_widgets()

    row(s, "기본값으로 되돌리기", "모든 모니터 설정을 지웁니다",
        control=button("되돌리기", reset))
    return p


PAGES = [{"id": "display", "title": "디스플레이",
          "icon": ["video-display", "preferences-desktop-display", "display",
                    "preferences-desktop-display-symbolic", "video-display-symbolic"],
          "build": build}]
