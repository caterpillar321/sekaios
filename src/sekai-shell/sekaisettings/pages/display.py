"""디스플레이 — 배치 / 주 디스플레이 / 해상도 / 주사율 / 배율 / 회전.

바꾸면 바로 적용하고 "유지할까요?"를 15초 동안 묻는다. 답이 없으면 되돌린다
(모니터가 못 받아들이는 모드라 화면이 안 보여도 저절로 돌아오게).
"""
import copy

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..store import display_key
from ..util import hyprctl, keyword, spawn
from ..widgets import Page, button, combo, row, switch
from .arrange import ArrangeView


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
    # 꺼 둔 모니터도 보여야 다시 켤 수 있다 — "monitors" 는 켜진 것만, "monitors all" 은 꺼진 것까지
    all_mons = hyprctl("monitors", "all", js=True) or hyprctl("monitors", js=True) or []
    mons = [m for m in all_mons if not m.get("disabled")]   # 배치·주 디스플레이는 켜진 것만
    off_mons = [m for m in all_mons if m.get("disabled")]
    # 설정은 모니터마다 (store.display_key — 같은 단자라도 다른 모니터면 따로). 예전 설정(단자 이름)은 지금 꽂힌
    #   모니터의 것으로 옮긴다 — 로그인 때 작업 표시줄도 옮기지만 그보다 먼저 이 페이지를 열 수도 있다
    if store.migrate_display(all_mons):
        store.save()
    keys = {m.get("name"): display_key(m) for m in all_mons}     # 단자 이름 → 설정 열쇠

    def key_of(n):
        return keys.get(n) or n

    def entry(n):
        return store.get("display").get(key_of(n), {})
    ctl = {}                       # 모니터 이름 → 되돌릴 때 다시 맞출 위젯들
    quiet = {"on": False}          # 되돌리며 위젯을 바꿀 땐 변경 처리를 하지 않는다

    # 모니터를 켜거나 끄면 이 페이지의 구성(켜진 모니터의 해상도 등 / 꺼진 모니터의 켜기 스위치)이
    #   달라진다 — Hyprland 가 바꿔 끼울 틈을 두고 새로 그린다
    REBUILD_MS = 1000

    def rebuild_later():
        store.request_rebuild("display", REBUILD_MS)

    def can_disable(n):
        """켜져 있는 마지막 모니터는 끌 수 없다 — 끄면 아무것도 안 보인다 (지금 실제 상태로 본다)"""
        live = [m.get("name") for m in (hyprctl("monitors", js=True) or []) if not m.get("disabled")]
        return any(x != n for x in live)

    def set_enabled(n, v, sw):
        """켜기·끄기 스위치 공통 — 켜진 모니터 구역의 스위치든 꺼진 모니터 구역의 스위치든 같은 검사"""
        if quiet["on"]:
            return
        if not v and not can_disable(n):
            quiet["on"] = True
            sw.set_active(True)
            quiet["on"] = False
            return
        change(n, "enabled", bool(v))
        rebuild_later()

    def sync_widgets():
        quiet["on"] = True
        try:
            for n, w in ctl.items():
                d = entry(n)
                w["en"].set_active(d.get("enabled", True))
                if "res" not in w:                  # 꺼진 모니터 — 켜기 스위치만 있다
                    continue
                res, hz = _split(d.get("mode"))
                if not w["res"].set_active_id(res or "preferred"):
                    w["res"].set_active_id("preferred")
                w["fill"](w["rate"], w["res"].get_active_id(), hz)
                w["vrr"].set_active_id(str(int(d.get("vrr", 0))))
                w["scale"].set_active_id(_scale_id(d.get("scale", 1.0)))
                w["tr"].set_active_id(str(int(d.get("transform", 0))))
        finally:
            quiet["on"] = False

    def snapshot():
        """모니터 위치와, 그때 떠 있던 창들의 위치 — 배치를 바꾸기 직전에 찍어 둔다"""
        mons_ = {m.get("id"): (m.get("x", 0), m.get("y", 0)) for m in (hyprctl("monitors", js=True) or [])}
        wins = [(c.get("address"), c.get("monitor"), tuple(c.get("at", [0, 0])))
                for c in (hyprctl("clients", js=True) or [])
                if c.get("floating") and not c.get("fullscreen")]
        return mons_, wins

    def carry_windows(before):
        """모니터가 옮겨 간 만큼 그 모니터의 떠 있는 창도 옮긴다 (윈도우처럼 창이 제 화면을 따라가게).
        Hyprland 는 창을 전체 좌표 그대로 두어서, 창이 엉뚱한 화면으로 넘어가거나 화면 밖에 남는다.
        바꾸기 전에 있던 창만, 그때 좌표에서 옮긴다 (그 뒤에 열린 확인 창은 이미 새 자리에 뜬다)."""
        mons_before, wins = before
        after = {m.get("id"): (m.get("x", 0), m.get("y", 0)) for m in (hyprctl("monitors", js=True) or [])}
        for addr, mid, (x, y) in wins:
            if mid not in mons_before or mid not in after:
                continue
            dx, dy = after[mid][0] - mons_before[mid][0], after[mid][1] - mons_before[mid][1]
            if dx or dy:
                spawn(["hyprctl", "dispatch", "movewindowpixel", f"exact {int(x + dx)} {int(y + dy)},address:{addr}"])
        return False

    def restore(snap):
        cur = store.get("display")
        before = snapshot()
        store.data["display"] = snap
        store.save()
        store.apply_display()
        GLib.timeout_add(400, carry_windows, before)
        # 이번에 처음 설정한 모니터는 설정이 없던 상태(권장 모드)로
        for n in cur:
            if n not in snap:
                keyword("monitor", f"{n}, preferred, auto, 1")
        sync_widgets()
        if arrange["view"] is not None:          # 배치 그림도 실제 위치로
            GLib.timeout_add(400, refresh_arrange)
        # 켜고 끈 것을 되돌렸으면 페이지 구성도 되돌아가야 한다
        if {n for n, d in snap.items() if not d.get("enabled", True)} != \
                {n for n, d in cur.items() if not d.get("enabled", True)}:
            rebuild_later()

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
            if resp == Gtk.ResponseType.ACCEPT:
                store.save()                    # 이제야 저장·게시한다 (로그인 화면·부팅 화면 모드도)
            else:
                restore(snap)
        dlg.connect("response", done)
        dlg.show_all()

    def change(n, key, value, ask=True):
        if quiet["on"]:
            return
        snap = copy.deepcopy(store.get("display"))
        k = key_of(n)
        d = copy.deepcopy(snap.get(k, {}))
        d[key] = value
        if not ask:
            store.set("display", k, d)
            return
        # "유지"를 누르기 전에는 화면에만 적용하고 저장하지 않는다 — 모니터가 못 받는 모드를 골라 화면이
        #   까매진 채 전원을 끄거나 앱이 죽으면, 저장된 나쁜 모드가 부팅 화면·로그인 화면·바탕화면에 남았다
        store.data.setdefault("display", {})[k] = d
        store.apply_display()
        confirm(snap)

    arrange = {"view": None}
    prim_sw = {}                   # 모니터 이름 → "주 디스플레이로 사용" 스위치

    def refresh_arrange():
        now = hyprctl("monitors", js=True) or []
        if arrange["view"] is not None:
            arrange["view"].set_positions({m.get("name"): (m.get("x", 0), m.get("y", 0)) for m in now})
        return False

    def apply_layout(pos):
        """배치 적용 — 모든 모니터의 위치를 한꺼번에 적고 적용, 그리고 유지할지 묻는다"""
        snap = copy.deepcopy(store.get("display"))
        before = snapshot()
        disp = store.data.setdefault("display", {})
        for n, (x, y) in pos.items():
            d = dict(disp.get(key_of(n), {}))
            d["position"] = f"{int(x)}x{int(y)}"
            disp[key_of(n)] = d
        store.apply_display()                   # 저장은 "유지"를 누를 때 (confirm)

        def verify(tries=[0]):
            # 옮기는 도중 잠깐 겹치면 Hyprland 가 자리를 밀어낸다 — 다르면 한 번 더 보낸다
            now = {m.get("name"): (m.get("x"), m.get("y")) for m in (hyprctl("monitors", js=True) or [])}
            if any(now.get(n) != tuple(v) for n, v in pos.items() if n in now) and tries[0] < 1:
                tries[0] += 1
                store.apply_display()
                GLib.timeout_add(300, verify)
                return False
            carry_windows(before)
            return False
        GLib.timeout_add(300, verify)
        confirm(snap)

    def current_primary():
        names = [m.get("name") for m in mons]
        want = store.get("layout", "primary") or ""
        return want if want in names else (names[0] if names else "")

    def on_primary(v, n):
        if quiet["on"]:
            return
        if not v:                     # 끌 수는 없다 — 다른 모니터를 주 디스플레이로 고르면 옮겨 간다
            quiet["on"] = True
            prim_sw[n].set_active(True)
            quiet["on"] = False
            return
        store.set("layout", "primary", n)
        quiet["on"] = True
        try:
            for k, sw in prim_sw.items():
                sw.set_active(k == n)
        finally:
            quiet["on"] = False
        if arrange["view"] is not None:
            arrange["view"].set_primary(n)

    if len(mons) > 1:
        title = Gtk.Label(label="배치", xalign=0)
        title.get_style_context().add_class("section-title")
        p.add_widget(title)
        view = ArrangeView(mons, current_primary(), store.get("appearance", "accent"), apply_layout)
        arrange["view"] = view
        p.add_widget(view)

    for mon in off_mons:
        # 꺼 둔 모니터 — 다시 켜는 스위치만 (켜면 페이지를 새로 그려 모드·배율 칸이 나온다)
        name = mon.get("name", "?")
        s = p.section(f"{_monitor_title(mon)}  ·  {name}  (꺼짐)")

        def on_enable_off(v, n=name):
            set_enabled(n, v, ctl[n]["en"])

        en_off = switch(False, on_enable_off)
        row(s, "이 모니터 사용", "켜면 화면이 다시 나옵니다",
            icon=["video-display", "preferences-desktop-display"], control=en_off)
        ctl[name] = {"en": en_off}

    if not mons:
        w = Gtk.Label(label="모니터 정보를 읽지 못했습니다. "
                            "Hyprland 세션 안에서 실행해 주세요.", xalign=0)
        w.get_style_context().add_class("notice")
        p.add_widget(w)
        return p

    for num, mon in enumerate(mons, 1):
        name = mon.get("name", "?")
        saved = entry(name)

        s = p.section(f"{num}.  {_monitor_title(mon)}  ·  {name}" if len(mons) > 1
                      else f"{_monitor_title(mon)}  ·  {name}")
        if len(mons) > 1:
            sw = switch(name == current_primary(), lambda v, n=name: on_primary(v, n))
            prim_sw[name] = sw
            row(s, "주 디스플레이로 사용",
                "작업 표시줄의 알림 영역·바탕화면 아이콘·알림이 이 화면에 뜨고, 로그인하면 창이 여기서 열립니다",
                icon=["video-display", "preferences-desktop-display"], control=sw)

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
            set_enabled(n, v, ctl[n]["en"])

        en_switch = switch(saved.get("enabled", True), on_enabled)
        row(s, "이 모니터 사용", "끄면 화면이 꺼집니다 (마지막 남은 모니터는 끌 수 없음)",
            control=en_switch)
        ctl[name] = {"res": res_combo, "rate": rate_combo, "fill": fill_rates, "scale": scale_combo,
                     "tr": tr_combo, "en": en_switch, "vrr": vrr_combo}


    s = p.section("기타")
    def reset():
        names = [m.get("name") for m in all_mons] + list(store.get("display").keys())   # 꺼 둔 것도 다시 켠다
        store.reset_section("display")
        # 설정이 비면 apply_display 는 아무것도 안 보낸다 → 지금 화면에도 직접 권장값을
        for n in dict.fromkeys(names):
            if n:
                keyword("monitor", f"{n}, preferred, auto, 1")
        sync_widgets()
        # reset_section 도 다시 그리기를 요청하지만, 꺼 뒀던 모니터가 켜지기까지 틈이 있다 — 그 요청을
        #   조금 뒤로 미룬다 (같은 페이지 요청은 나중 것만 남는다. 그동안 칸은 위 sync_widgets 가 맞춘다)
        rebuild_later()

    row(s, "기본값으로 되돌리기", "모든 모니터 설정을 지웁니다",
        control=button("되돌리기", reset))
    return p


PAGES = [{"id": "display", "title": "디스플레이",
          "icon": ["video-display", "preferences-desktop-display", "display",
                    "preferences-desktop-display-symbolic", "video-display-symbolic"],
          "build": build, "sections": ("display", "layout", "appearance")}]   # appearance: 배치 그림의 강조색
