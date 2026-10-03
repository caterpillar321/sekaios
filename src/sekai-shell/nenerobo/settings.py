"""Nenerobo — 설정 창 (윈도우 터미널의 설정처럼: 왼쪽에 항목, 바꾸면 바로 적용 — 저장 단추 없음).

  시작    — 기본 프로필 · 새 탭은 지금 폴더에서 · 여러 탭 닫을 때 묻기 · 고르면 복사 · SSH 설정의 호스트
  모양    — 색 구성표(견본) · 글꼴(고정폭만) · 크기 · 커서 · 굵은 글씨 · 스크롤 기록
  프로필  — 셸 · 관리자 셸 · 내가 만든 명령/SSH 프로필 (추가 · 편집 · 삭제) · ~/.ssh/config 의 호스트
  단축키  — 목록
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import style  # noqa: E402
from .config import CURSORS, profile_desc  # noqa: E402
from .window import window_buttons  # noqa: E402

SHORTCUTS = [
    ("복사 (고른 글이 없으면 실행 중인 프로그램 멈추기)", "Ctrl+C"), ("붙여넣기", "Ctrl+V"),
    ("복사 · 붙여넣기 (언제나)", "Ctrl+Shift+C · Ctrl+Shift+V"),
    ("새 탭 (기본 프로필)", "Ctrl+Shift+T"), ("N번째 프로필로 새 탭", "Ctrl+Shift+1 ~ 9"),
    ("탭 복제", "Ctrl+Shift+D"), ("탭 닫기", "Ctrl+Shift+W"), ("새 창", "Ctrl+Shift+N"),
    ("다음 · 이전 탭", "Ctrl+Tab · Ctrl+Shift+Tab (Ctrl+PgDn · PgUp)"), ("N번째 탭으로", "Ctrl+Alt+1 ~ 9"),
    ("찾기", "Ctrl+Shift+F"), ("글자 크게 · 작게 · 원래대로", "Ctrl+= · Ctrl+- · Ctrl+0 (Ctrl+휠)"),
    ("전체 화면", "F11 · Alt+Enter"), ("설정", "Ctrl+,"), ("링크 열기", "Ctrl+클릭"),
    ("탭 이름 바꾸기", "탭을 두 번 클릭"), ("탭 닫기 (마우스)", "탭을 가운데 단추로 클릭"),
]


def titlebar(win, title, kinds):
    bar = Gtk.Box()
    bar.add_css_class("nr-titlebar")
    lab = Gtk.Label(label=title, xalign=0)
    lab.add_css_class("nr-title")
    lab.set_hexpand(True)
    bar.append(lab)
    window_buttons(win, bar, kinds)
    h = Gtk.WindowHandle()
    h.set_child(bar)
    win.set_titlebar(h)


def card():
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("nr-card")
    return lb


def row(lb, title, sub=None, control=None):
    r = Gtk.ListBoxRow(activatable=False)
    box = Gtk.Box(spacing=16)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    texts.set_hexpand(True)
    texts.set_valign(Gtk.Align.CENTER)
    t = Gtk.Label(label=title, xalign=0, wrap=True)
    texts.append(t)
    if sub:
        s = Gtk.Label(label=sub, xalign=0, wrap=True, ellipsize=Pango.EllipsizeMode.NONE)
        s.add_css_class("nr-sub")
        texts.append(s)
    box.append(texts)
    if control is not None:
        control.set_valign(Gtk.Align.CENTER)
        box.append(control)
    r.set_child(box)
    lb.append(r)
    return r


def dropdown(items, current, on_change):
    """items = [(id, 보이는 이름)]"""
    dd = Gtk.DropDown.new_from_strings([n for _i, n in items])
    ids = [i for i, _n in items]
    dd.set_selected(ids.index(current) if current in ids else 0)
    dd.connect("notify::selected", lambda d, _p: on_change(ids[d.get_selected()]) if d.get_selected() < len(ids)
               else None)
    return dd


class SettingsWindow(Gtk.Window):
    def __init__(self, app, parent=None):
        super().__init__(application=app, title="터미널 설정")
        self.app = app
        self.cfg = app.cfg
        self.set_default_size(880, 640)
        if parent is not None:
            self.set_transient_for(parent)
        titlebar(self, "설정", ("min", "max", "close"))
        body = Gtk.Box()
        body.add_css_class("nr-settings")
        self.set_child(body)
        side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        side.add_css_class("nr-side")
        side.set_size_request(200, -1)
        body.append(side)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        body.append(self.stack)
        self.pages = {}
        for pid, name, build in (("start", "시작", self._page_start), ("look", "모양", self._page_look),
                                 ("profiles", "프로필", self._page_profiles), ("keys", "단축키", self._page_keys)):
            r = Gtk.ListBoxRow()
            r.set_child(Gtk.Label(label=name, xalign=0))
            r.pid = pid
            side.append(r)
            self.pages[pid] = build
            self._rebuild(pid)
        side.connect("row-selected", lambda _l, r: r and self.stack.set_visible_child_name(r.pid))
        side.select_row(side.get_row_at_index(0))

    # ── 공통 ──
    def _rebuild(self, pid):
        old = self.stack.get_child_by_name(pid)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("nr-page")
        sc.set_child(box)
        self.pages[pid](box)
        if old is not None:
            vis = self.stack.get_visible_child_name() == pid
            self.stack.remove(old)
            self.stack.add_named(sc, pid)
            if vis:
                self.stack.set_visible_child_name(pid)
        else:
            self.stack.add_named(sc, pid)

    def _changed(self, key, val):
        self.cfg.set(key, val)
        self.app.apply_config()

    def _switch(self, key):
        sw = Gtk.Switch(active=bool(self.cfg[key]))
        sw.connect("notify::active", lambda s, _p: self._changed(key, s.get_active()))
        return sw

    def _spin(self, key, lo, hi, step, digits=0):
        sp = Gtk.SpinButton.new_with_range(lo, hi, step)
        sp.set_digits(digits)
        sp.set_value(float(self.cfg[key]))
        sp.connect("value-changed", lambda s: self._changed(key, int(s.get_value()) if digits == 0 else s.get_value()))
        return sp

    @staticmethod
    def _h1(box, text):
        h = Gtk.Label(label=text, xalign=0)
        h.add_css_class("nr-h1")
        box.append(h)

    @staticmethod
    def _sec(box, text):
        h = Gtk.Label(label=text, xalign=0)
        h.add_css_class("nr-sec")
        box.append(h)

    # ── 시작 ──
    def _page_start(self, box):
        self._h1(box, "시작")
        c = card()
        box.append(c)
        ps = self.cfg.profiles()
        row(c, "기본 프로필", "새 탭(Ctrl+Shift+T)·새 창에서 여는 것",
            dropdown([(p["id"], p["name"]) for p in ps], self.cfg["default_profile"],
                     lambda v: self._changed("default_profile", v)))
        row(c, "새 탭은 지금 탭의 폴더에서 열기", "끄면 늘 홈 폴더에서 엽니다", self._switch("new_tab_same_dir"))
        row(c, "여러 탭을 한꺼번에 닫을 때 묻기", "실행 중인 프로그램이 있으면 이 설정과 상관없이 묻습니다",
            self._switch("confirm_close"))
        row(c, "고르면 바로 복사", "마우스로 글을 고르기만 해도 클립보드에 복사합니다", self._switch("copy_on_select"))
        sw = self._switch("show_ssh_hosts")
        sw.connect("notify::active", lambda *_: GLib.idle_add(lambda: (self._rebuild("profiles"), False)[1]))
        row(c, "SSH 설정의 호스트를 프로필로 보이기", "~/.ssh/config 의 Host 를 새 탭 메뉴에 \"SSH: 이름\" 으로 보입니다", sw)

    # ── 모양 ──
    def _page_look(self, box):
        self._h1(box, "모양")
        ap = self.app.ap
        items = [(style.AUTO, "SekaiOS (다크·라이트 모드를 따름)")] + [(k, v["name"]) for k, v in style.SCHEMES.items()]
        prev = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        def show_preview(sid):
            while (ch := prev.get_first_child()) is not None:
                prev.remove(ch)
            prev.append(self._preview(style.scheme(sid, ap)))

        def changed(v):
            self._changed("scheme", v)
            show_preview(v)
        c = card()
        box.append(c)
        row(c, "색 구성표", None, dropdown(items, self.cfg["scheme"], changed))
        r = Gtk.ListBoxRow(activatable=False)
        r.set_child(prev)
        c.append(r)
        show_preview(self.cfg["scheme"])

        self._sec(box, "글꼴")
        c = card()
        box.append(c)
        dlg = Gtk.FontDialog(title="글꼴 고르기 (고정폭)")
        flt = Gtk.CustomFilter.new(lambda item: isinstance(item, Pango.FontFamily) and item.is_monospace()
                                   if isinstance(item, Pango.FontFamily) else True)
        dlg.set_filter(flt)
        fb = Gtk.FontDialogButton(dialog=dlg, level=Gtk.FontLevel.FAMILY)
        fb.set_font_desc(Pango.FontDescription.from_string(self.cfg["font_family"] or "Monospace"))
        fb.connect("notify::font-desc", lambda b, _p: b.get_font_desc() and self._changed(
            "font_family", b.get_font_desc().get_family()))
        row(c, "글꼴", "고정폭 글꼴만 보입니다. 한글은 글꼴에 없으면 나눔고딕코딩으로 채웁니다", fb)
        row(c, "글자 크기", None, self._spin("font_size", 6, 32, 1))

        self._sec(box, "커서와 글자")
        c = card()
        box.append(c)
        row(c, "커서 모양", None, dropdown(CURSORS, self.cfg["cursor_shape"],
                                         lambda v: self._changed("cursor_shape", v)))
        row(c, "커서 깜박임", None, self._switch("cursor_blink"))
        row(c, "굵은 글씨를 밝은 색으로", "오래된 프로그램이 굵게 쓴 글자를 밝은 색으로 보입니다", self._switch("bold_bright"))
        row(c, "스크롤 기록", "위로 올려 볼 수 있는 줄 수", self._spin("scrollback", 0, 1000000, 1000))

    def _preview(self, sc):
        """구성표 견본 — 바탕에 글자, 16색 칸"""
        out = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        out.set_margin_top(4)
        out.set_margin_bottom(8)
        out.set_margin_start(16)
        out.set_margin_end(16)
        lab = Gtk.Label(xalign=0)
        lab.add_css_class("nr-preview")
        p = sc["palette"]
        lab.set_markup(
            f"<span foreground='{p[2]}'>miku@sekai</span>:<span foreground='{p[4]}'>~/프로젝트</span>$ ls\n"
            f"<span foreground='{p[4]}'>문서</span>  <span foreground='{p[2]}'>run.sh</span>  "
            f"<span foreground='{p[1]}'>오류.log</span>  <span foreground='{p[3]}'>설정.json</span>  "
            f"<span foreground='{p[5]}'>사진.png</span>")
        prov = Gtk.CssProvider()
        prov.load_from_string(f"label {{ background: {sc['bg']}; color: {sc['fg']}; }}")
        lab.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        out.append(lab)
        grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        for i, col in enumerate(p):
            sw = Gtk.Box()
            sw.add_css_class("nr-swatch")
            pv = Gtk.CssProvider()
            pv.load_from_string(f"box {{ background: {col}; border: 1px solid rgba(128,128,128,0.35); }}")
            sw.get_style_context().add_provider(pv, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            grid.attach(sw, i % 8, i // 8, 1, 1)
        out.append(grid)
        return out

    # ── 프로필 ──
    def _page_profiles(self, box):
        self._h1(box, "프로필")
        sub = Gtk.Label(label="새 탭 메뉴(▾)에 보이는 것들입니다. 위에서부터 Ctrl+Shift+1, 2, 3 … 으로도 엽니다.",
                        xalign=0, wrap=True)
        sub.add_css_class("nr-sub")
        sub.set_margin_bottom(12)
        box.append(sub)
        c = card()
        box.append(c)
        for i, p in enumerate(self.cfg.profiles()):
            tag = "기본 제공" if p.get("builtin") else "SSH 설정에서" if p.get("auto") else None
            name = p["name"] + ("  ·  기본" if p["id"] == self.cfg["default_profile"] else "")
            ctrl = Gtk.Box(spacing=6)
            if tag:
                t = Gtk.Label(label=tag)
                t.add_css_class("nr-sub")
                ctrl.append(t)
            else:
                e = Gtk.Button(label="편집…")
                e.connect("clicked", lambda _b, pp=p: self._edit(pp))
                ctrl.append(e)
                d = Gtk.Button(label="삭제")
                d.connect("clicked", lambda _b, pp=p: self._delete(pp))
                ctrl.append(d)
            row(c, f"{i + 1}. {name}" if i < 9 else name, profile_desc(p), ctrl)
        add = Gtk.Button(label="새 프로필 추가…")
        add.add_css_class("suggested-action")
        add.set_halign(Gtk.Align.START)
        add.set_margin_top(14)
        add.connect("clicked", lambda *_: self._edit(None))
        box.append(add)

    # ── 단축키 ──
    def _page_keys(self, box):
        self._h1(box, "단축키")
        c = card()
        box.append(c)
        for what, keys in SHORTCUTS:
            k = Gtk.Label(label=keys, xalign=1)
            k.add_css_class("nr-sub")
            row(c, what, None, k)

    def _delete(self, p):
        d = Gtk.AlertDialog(message=f"{p['name']} 프로필을 지울까요?", detail="새 탭 메뉴에서 사라집니다.",
                            buttons=["취소", "삭제"], cancel_button=0, default_button=0, modal=True)

        def done(dlg, res):
            try:
                if dlg.choose_finish(res) == 1:
                    self.cfg.delete_profile(p["id"])
                    self._profiles_changed()
            except GLib.Error:
                pass
        d.choose(self, None, done)

    def _profiles_changed(self):
        self.app.apply_config()
        self._rebuild("profiles")
        self._rebuild("start")

    def _edit(self, p):
        ProfileEditor(self, p).present()


class ProfileEditor(Gtk.Window):
    """프로필 하나 — 명령 실행 또는 SSH 접속"""

    def __init__(self, sw, p):
        super().__init__(title="프로필 편집" if p else "새 프로필", modal=True, transient_for=sw)
        self.sw, self.cfg = sw, sw.cfg
        self.p = dict(p) if p else {"kind": "command"}
        self.set_application(sw.app)
        self.set_default_size(520, -1)
        self.set_resizable(False)
        titlebar(self, "프로필 편집" if p else "새 프로필", ("close",))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("nr-page")
        box.add_css_class("nr-settings")
        self.set_child(box)
        g = Gtk.Grid(column_spacing=14, row_spacing=10)
        box.append(g)

        def lab(text, r):
            l_ = Gtk.Label(label=text, xalign=0)
            g.attach(l_, 0, r, 1, 1)
            return l_
        self.name = Gtk.Entry(text=self.p.get("name", ""), hexpand=True, placeholder_text="예: 개발 서버")
        lab("이름", 0)
        g.attach(self.name, 1, 0, 2, 1)
        self.kind = Gtk.DropDown.new_from_strings(["명령 실행", "SSH 접속"])
        self.kind.set_selected(1 if self.p.get("kind") == "ssh" else 0)
        lab("종류", 1)
        g.attach(self.kind, 1, 1, 2, 1)
        self.cmd = Gtk.Entry(text=self.p.get("command", ""), placeholder_text="예: htop  ·  python3  ·  distrobox enter dev")
        self.l_cmd = lab("명령줄", 2)
        g.attach(self.cmd, 1, 2, 2, 1)
        self.host = Gtk.Entry(text=self.p.get("host", ""), placeholder_text="주소 또는 ~/.ssh/config 의 이름")
        self.user = Gtk.Entry(text=self.p.get("user", ""), placeholder_text="(비우면 기본)")
        self.port = Gtk.Entry(text=str(self.p.get("port", "") or ""), placeholder_text="22", width_chars=6)
        self.l_host = lab("호스트", 3)
        g.attach(self.host, 1, 3, 2, 1)
        self.l_user = lab("사용자 · 포트", 4)
        g.attach(self.user, 1, 4, 1, 1)
        g.attach(self.port, 2, 4, 1, 1)
        self.cwd = Gtk.Entry(text=self.p.get("cwd", ""), hexpand=True, placeholder_text="(비우면 지금 폴더 또는 홈)")
        pick = Gtk.Button(label="찾아보기…")
        pick.connect("clicked", self._pick)
        lab("시작 폴더", 5)
        g.attach(self.cwd, 1, 5, 1, 1)
        g.attach(pick, 2, 5, 1, 1)
        items = [("", "전체 설정을 따름")] + [(k, v["name"]) for k, v in style.SCHEMES.items()]
        self.scheme_ids = [i for i, _n in items]
        self.scheme = Gtk.DropDown.new_from_strings([n for _i, n in items])
        cur = self.p.get("scheme") or ""
        self.scheme.set_selected(self.scheme_ids.index(cur) if cur in self.scheme_ids else 0)
        lab("색 구성표", 6)
        g.attach(self.scheme, 1, 6, 2, 1)
        self.err = Gtk.Label(xalign=0)
        self.err.add_css_class("error")
        self.err.set_visible(False)
        box.append(self.err)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="취소")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="저장")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        btns.append(cancel)
        btns.append(save)
        box.append(btns)
        self.kind.connect("notify::selected", lambda *_: self._kind_changed())
        self._kind_changed()
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.close(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)

    def _kind_changed(self):
        ssh = self.kind.get_selected() == 1
        for w in (self.cmd, self.l_cmd):
            w.set_visible(not ssh)
        for w in (self.host, self.l_host, self.user, self.port, self.l_user):
            w.set_visible(ssh)

    def _pick(self, *_):
        d = Gtk.FileDialog(title="시작 폴더 고르기")

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self.cwd.set_text(f.get_path())
        d.select_folder(self, None, done)

    def _save(self, *_):
        ssh = self.kind.get_selected() == 1
        name = self.name.get_text().strip()
        p = {"id": self.p.get("id", ""), "name": name, "kind": "ssh" if ssh else "command",
             "cwd": self.cwd.get_text().strip(), "scheme": self.scheme_ids[self.scheme.get_selected()]}
        if ssh:
            p.update(host=self.host.get_text().strip(), user=self.user.get_text().strip(),
                     port=self.port.get_text().strip())
            if not name:
                p["name"] = name = f"SSH: {p['host']}"
        else:
            p["command"] = self.cmd.get_text().strip()
        why = ("호스트를 적어 주세요" if ssh and not p.get("host") else
               "실행할 명령을 적어 주세요" if not ssh and not p.get("command") else
               "이름을 적어 주세요" if not name else
               "포트는 숫자로 적어 주세요" if ssh and p.get("port") and not p["port"].isdigit() else
               "시작 폴더가 없습니다" if p["cwd"] and not os.path.isdir(os.path.expanduser(p["cwd"])) else "")
        if why:
            self.err.set_text(why)
            self.err.set_visible(True)
            return
        self.cfg.save_profile(p)
        self.sw._profiles_changed()
        self.close()
