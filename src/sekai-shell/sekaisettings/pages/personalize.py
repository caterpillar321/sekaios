"""개인 설정 — 배경화면, 색, 창 모양."""
import glob
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf, GLib  # noqa: E402

from ..util import spawn
from ..widgets import (Page, button, color_button, combo, entry, info, row,
                       slider, spin, switch)

WALL_DIRS = ["/usr/share/backgrounds/sekai", "/usr/share/backgrounds",
             os.path.expanduser("~/Pictures"), os.path.expanduser("~/그림")]
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MODES = [("fill", "채우기"), ("fit", "맞춤"), ("stretch", "늘이기"),
         ("center", "가운데"), ("tile", "바둑판")]

ACCENT_PRESETS = [
    ("#39c5bb", "미쿠 틸"), ("#00e5ff", "세카이 시안"), ("#ff6b8a", "테토 핑크"),
    ("#ffcc22", "린 옐로"), ("#9b7bff", "루카 바이올렛"), ("#4c8dff", "카이토 블루"),
    ("#3ddc84", "그린"), ("#ff8a3d", "오렌지"),
]


def _wallpapers():
    out = []
    for d in WALL_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            if f.lower().endswith(EXTS) and os.path.isfile(f):
                out.append(f)
    # 중복 제거, 순서 유지
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq[:60]


def build_wallpaper(store):
    p = Page("배경화면", "바탕화면 그림을 고릅니다.")

    cur = Gtk.Label(label=store.get("wallpaper", "path"), xalign=0)
    cur.get_style_context().add_class("row-sub")
    cur.set_ellipsize(3)  # PANGO_ELLIPSIZE_END

    s = p.section("현재")
    r = row(s, "선택된 그림", store.get("wallpaper", "path"),
            icon=["preferences-desktop-wallpaper", "image-x-generic"])
    row(s, "표시 방식", "그림이 화면에 맞춰지는 방법",
        control=combo(MODES, store.get("wallpaper", "mode"),
                      lambda v: store.set("wallpaper", "mode", v)))

    def pick():
        d = Gtk.FileChooserDialog(title="배경화면 고르기",
                                  action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "열기", Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("그림 파일")
        for e in EXTS:
            flt.add_pattern("*" + e)
        d.add_filter(flt)
        if d.run() == Gtk.ResponseType.OK:
            path = d.get_filename()
            store.set("wallpaper", "path", path)
            r.sub_label.set_text(path)
            _refresh_selection(path)
        d.destroy()

    row(s, "파일에서 고르기", "직접 그림 파일을 지정합니다",
        control=button("찾아보기…", pick))

    # ── 썸네일 격자 ──
    p.box.pack_start(_label("설치된 배경화면"), False, False, 0)
    flow = Gtk.FlowBox()
    flow.set_valign(Gtk.Align.START)
    flow.set_max_children_per_line(6)
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.get_style_context().add_class("wall-grid")
    p.box.pack_start(flow, False, False, 0)

    tiles = {}

    def _refresh_selection(path):
        for k, t in tiles.items():
            ctx = t.get_style_context()
            (ctx.add_class if k == path else ctx.remove_class)("selected")

    for f in _wallpapers():
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(f, 160, 100, True)
        except Exception:
            continue
        b = Gtk.Button()
        b.get_style_context().add_class("wall-tile")
        b.set_image(Gtk.Image.new_from_pixbuf(pb))
        b.set_tooltip_text(os.path.basename(f))
        b.connect("clicked", lambda _w, path=f: (
            store.set("wallpaper", "path", path),
            r.sub_label.set_text(path),
            _refresh_selection(path)))
        tiles[f] = b
        flow.add(b)

    _refresh_selection(store.get("wallpaper", "path"))
    if not tiles:
        p.add_widget(_notice("설치된 배경화면이 없습니다. "
                             "위의 '찾아보기'로 직접 고르세요."))
    return p


def _label(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("section-title")
    return l


def _notice(text):
    l = Gtk.Label(label=text, xalign=0)
    l.get_style_context().add_class("notice")
    l.set_line_wrap(True)
    return l


def _bare_row(widget):
    """제목 없이 위젯만 담은 항목 줄"""
    r = Gtk.ListBoxRow()
    r.set_activatable(False)
    r.get_style_context().add_class("row")
    r.add(widget)
    return r


class _Preview(Gtk.DrawingArea):
    """보통은 w×h, 좁으면 min_w×min_h 까지 줄어드는 그림 칸 (가로·세로 비율은 그대로)"""

    def __init__(self, w, h, min_w, min_h):
        super().__init__()
        self._size = (w, h, min_w, min_h)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self):
        w, _h, mw, _mh = self._size
        return mw, w

    def do_get_preferred_height_for_width(self, width):
        w, h, mw, mh = self._size
        hh = max(mh, min(h, round(width * h / w)))
        return hh, hh

    def do_get_preferred_height(self):
        return self._size[1], self._size[1]


def _draw_mode_preview(area, cr, mode, store):
    """모드 미리보기 — 배경 위에 작은 창과 작업 표시줄"""
    from sekaishell import theme as _t
    pal = _t.PALETTES[mode]
    accent = store.get("appearance", "accent") or "#39c5bb"

    def rgb(h, a=1.0):
        h = h.lstrip("#")
        cr.set_source_rgba(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)
    w, h = area.get_allocated_width(), area.get_allocated_height()

    def rrect(x, y, ww, hh, r):
        import math
        cr.new_sub_path()
        cr.arc(x + ww - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + ww - r, y + hh - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + hh - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
    rgb("#2a6f76" if mode == "dark" else "#8fd3d0")         # 배경 사진 대신 단색
    rrect(0, 0, w, h, 8); cr.fill()
    rgb(pal["titlebar_bg"]); rrect(w * 0.18, h * 0.14, w * 0.64, h * 0.56, 5); cr.fill()
    rgb(pal["surface"]); cr.rectangle(w * 0.18, h * 0.26, w * 0.64, h * 0.44); cr.fill()
    rgb(pal["fg"], 0.55); cr.rectangle(w * 0.24, h * 0.34, w * 0.30, 4); cr.fill()
    rgb(pal["fg"], 0.30); cr.rectangle(w * 0.24, h * 0.44, w * 0.42, 4); cr.fill()
    rgb(accent); rrect(w * 0.24, h * 0.54, w * 0.16, 9, 3); cr.fill()
    rgb(pal["surface"], 0.95); cr.rectangle(0, h * 0.84, w, h * 0.16); cr.fill()
    rgb(accent); cr.arc(w * 0.08, h * 0.92, 4, 0, 6.3); cr.fill()
    return False


def build_appearance(store):
    p = Page("색 및 모양", "강조색과 창 테두리, 여백을 조정합니다.")
    a = store.get("appearance")

    # ── 모드 (다크 / 라이트) ──
    s = p.section("모드")
    modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    mode_btns = {}

    def pick_mode(m):
        if store.get("appearance", "mode") == m:
            _sync_modes()
            return
        store.set_mode(m)
        from sekaishell import theme as _t
        for key, btn in color_btns.items():
            _set_color_button(btn, _t.PALETTES[m][key])
        _sync_modes()

    def _sync_modes():
        cur = store.get("appearance", "mode") or "dark"
        for m, b in mode_btns.items():
            ctx = b.get_style_context()
            (ctx.add_class if m == cur else ctx.remove_class)("mode-on")

    for m, label in (("light", "라이트"), ("dark", "다크")):
        b = Gtk.Button()
        b.get_style_context().add_class("mode-card")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prev = _Preview(168, 100, 120, 72)       # 창이 좁으면 작아진다 (그림은 받은 크기에 맞춰 그린다)
        prev.connect("draw", _draw_mode_preview, m, store)
        v.pack_start(prev, False, False, 0)
        v.pack_start(Gtk.Label(label=label), False, False, 0)
        b.add(v)
        b.connect("clicked", lambda _b, m=m: pick_mode(m))
        mode_btns[m] = b
        modes.pack_start(b, False, False, 0)
    row(s, "앱과 작업 표시줄의 밝기", "창·메뉴·설정 앱 그리고 일반 앱(GTK·Chromium)의 색이 함께 바뀝니다",
        icon=["preferences-desktop-theme", "applications-graphics"])
    s.add(_bare_row(modes))
    _sync_modes()

    s = p.section("색")
    accent_btn = color_button(a["accent"], lambda v: store.set("appearance", "accent", v))
    row(s, "강조색", "선택 표시, 테두리, 패널 포인트 색",
        icon=["preferences-desktop-theme", "applications-graphics"],
        control=accent_btn)

    # 프리셋 팔레트
    pal = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    pal.get_style_context().add_class("palette")
    for hexv, name in ACCENT_PRESETS:
        b = Gtk.Button()
        b.set_size_request(30, 30)
        b.set_tooltip_text(f"{name}  {hexv}")
        b.get_style_context().add_class("swatch")
        # 버튼마다 색을 주기 위해 개별 CSS 공급자를 붙인다
        prov = Gtk.CssProvider()
        prov.load_from_data(
            ("button { background: %s; background-image: none; }" % hexv).encode())
        # 전역 style 은 PRIORITY_USER 로 올라가므로 그보다 높아야 색이 먹는다
        b.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        b.connect("clicked", lambda _w, v=hexv: (
            store.set("appearance", "accent", v),
            _set_color_button(accent_btn, v)))
        pal.pack_start(b, False, False, 0)
    row(s, "프리셋", "자주 쓰는 색", control=pal)

    color_btns = {
        "surface": color_button(a["surface"], lambda v: store.set("appearance", "surface", v)),
        "titlebar_bg": color_button(a["titlebar_bg"], lambda v: store.set("appearance", "titlebar_bg", v)),
        "fg": color_button(a["fg"], lambda v: store.set("appearance", "fg", v)),
    }
    row(s, "패널 배경색", "작업 표시줄·시작 메뉴·팝업의 바탕 (모드를 바꾸면 그 모드의 색으로)",
        control=color_btns["surface"])
    row(s, "제목 표시줄 색", "창 위쪽 막대", control=color_btns["titlebar_bg"])
    row(s, "글자색", control=color_btns["fg"])

    s = p.section("창")
    row(s, "제목 표시줄", "창 위에 제목과 버튼을 보여줍니다",
        icon=["window-new", "preferences-system-windows"],
        control=switch(a["titlebar"], lambda v: store.set("appearance", "titlebar", v)))
    row(s, "제목 표시줄 높이", None,
        control=spin(a["titlebar_height"], 18, 60, 1,
                     lambda v: store.set("appearance", "titlebar_height", v)))
    row(s, "모서리 둥글기", None,
        control=slider(a["rounding"], 0, 24, 1,
                       lambda v: store.set("appearance", "rounding", v)))
    row(s, "테두리 두께", None,
        control=slider(a["border_size"], 0, 6, 1,
                       lambda v: store.set("appearance", "border_size", v)))
    row(s, "창 사이 여백", None,
        control=slider(a["gaps_in"], 0, 30, 1,
                       lambda v: store.set("appearance", "gaps_in", v)))
    row(s, "화면 가장자리 여백", None,
        control=slider(a["gaps_out"], 0, 60, 1,
                       lambda v: store.set("appearance", "gaps_out", v)))
    row(s, "비활성 창 투명도", "1.00 이면 불투명",
        control=slider(a["inactive_opacity"], 0.5, 1.0, 0.01,
                       lambda v: store.set("appearance", "inactive_opacity", v),
                       digits=2))

    s = p.section("효과")
    row(s, "애니메이션", "창이 열리고 닫힐 때의 움직임",
        control=switch(a["animations"],
                       lambda v: store.set("appearance", "animations", v)))
    row(s, "배경 흐림", "반투명 창 뒤를 흐리게 (성능에 영향)",
        control=switch(a["blur"], lambda v: store.set("appearance", "blur", v)))
    row(s, "그림자", control=switch(a["shadow"],
                                  lambda v: store.set("appearance", "shadow", v)))

    s = p.section("작업 표시줄")
    pn = store.get("panel")
    row(s, "높이", "픽셀",
        icon=["preferences-system-windows", "view-list"],
        control=spin(pn["height"], 28, 72, 2,
                     lambda v: store.set("panel", "height", v)))
    row(s, "시계 형식", "strftime 형식 (예: %H:%M, %p %I:%M)",
        control=entry(pn["clock_format"],
                      lambda v: store.set("panel", "clock_format", v),
                      width=14, placeholder="%H:%M"))

    s = p.section("글꼴")
    from sekaishell import theme as _theme
    row(s, "글꼴 다듬기",
        "윈도우의 ClearType 과 같은 것 — 글자 가장자리를 모니터의 빨강·초록·파랑 점에 맞춰 또렷하게. "
        "글자 둘레에 색이 번져 보이면 BGR 이나 회색조로 (OLED 모니터는 회색조). 열려 있던 앱은 다시 열면 적용됩니다",
        control=combo([("rgb", "선명하게 (RGB)"), ("bgr", "선명하게 (BGR)"), ("gray", "부드럽게 (회색조)")],
                      _theme.font_smoothing(), _theme.set_font_smoothing))

    s = p.section("커서")
    row(s, "커서 크기", "다시 로그인해야 완전히 적용됩니다",
        control=spin(a["cursor_size"], 12, 64, 2,
                     lambda v: store.set("appearance", "cursor_size", v)))

    s = p.section("되돌리기")
    row(s, "모양 기본값으로", "색과 창 설정을 처음 상태로 돌립니다",
        control=button("되돌리기", lambda: store.reset_section("appearance")))   # 적용·다시 그리기까지 한다
    return p


def _set_color_button(btn, hexstr):
    from gi.repository import Gdk
    c = Gdk.RGBA()
    c.parse(hexstr)
    btn.set_rgba(c)


PAGES = [
    {"id": "wallpaper", "title": "배경화면",
     "icon": ["preferences-desktop-wallpaper", "image-x-generic",
              "image-x-generic-symbolic"],
     "build": build_wallpaper, "sections": ("wallpaper",)},
    {"id": "appearance", "title": "색 및 모양",
     "icon": ["preferences-desktop-theme", "applications-graphics",
              "applications-graphics-symbolic"],
     "build": build_appearance, "sections": ("appearance", "panel")},
]
