"""환경 변수 — 윈도우의 "시스템 속성 → 환경 변수"처럼 사용자 변수와 시스템 변수.

저장 형식·적용은 sekaishell.userenv. 사용자 변수는 이 앱이 바로 쓰고, 시스템 변수는
/usr/libexec/sekai/sekai-envvars(pkexec)가 쓴다. PATH 처럼 폴더를 잇는 값은 목록으로 편집하고,
윈도우처럼 기본은 "기존 값 뒤에 붙이기"(${PATH}:…) — 통째로 바꾸면 명령을 못 찾게 될 수 있다.
"""
import getpass
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sekaishell import userenv  # noqa: E402
from sekaishell.sysutil import atomic_write  # noqa: E402

from ..util import failure_reason, run_async  # noqa: E402
from ..widgets import Page, StatusLine, button, clear, confirm, notice, parent_window, row, subsection  # noqa: E402

HELPER = "/usr/libexec/sekai/sekai-envvars"
ICON = ["text-x-script", "application-x-executable", "utilities-terminal"]   # preferences-system 은 설정 앱 아이콘과 같아 보인다
MODES = [("append", "기존 값 뒤에 붙이기"), ("prepend", "기존 값 앞에 붙이기"), ("replace", "기존 값을 바꾸기")]


def split_list(name, raw):
    """목록 값 → (방식, [항목]) — "${PATH}:/a" 는 뒤에 붙이기, "/a:${PATH}" 는 앞에 붙이기"""
    parts = [p for p in raw.split(":") if p]
    refs = (f"${{{name}}}", f"${name}")
    if parts and parts[0] in refs:
        return "append", parts[1:]
    if parts and parts[-1] in refs:
        return "prepend", parts[:-1]
    return "replace", parts


def join_list(name, mode, items):
    items = [i for i in items if i]
    if mode == "append":
        return ":".join([f"${{{name}}}"] + items)
    if mode == "prepend":
        return ":".join(items + [f"${{{name}}}"])
    return ":".join(items)


def describe(name, raw):
    if not userenv.is_list(name):
        return raw
    mode, items = split_list(name, raw)
    where = {"append": f" · 기존 {name} 뒤에", "prepend": f" · 기존 {name} 앞에", "replace": " · 기존 값을 바꿈"}[mode]
    return (", ".join(items) or "(항목 없음)") + where


class EnvVarsPage(StatusLine):
    def __init__(self, store):
        self.store = store
        self.dead = False
        self.say_src = 0
        self.p = Page("환경 변수", "앱이 시작할 때 읽는 값입니다. 저장하면 새로 여는 앱부터 적용되고, "
                                  "이미 열린 앱은 다시 열어야 바뀝니다. 값 안의 $이름 · ${이름} 은 그 변수의 값으로 바뀝니다.")
        self.p.connect("destroy", lambda *_: setattr(self, "dead", True))
        self.msg = notice()
        self.msg.set_no_show_all(True)
        self.p.add_widget(self.msg)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.p.add_widget(self.body)
        try:
            userenv.refresh()          # 설정 앱을 연 뒤에 다른 곳에서 바뀌었어도 지금 값으로
        except Exception:
            pass
        self.draw()

    @property
    def widget(self):
        return self.p

    # ── 그리기 ──
    def draw(self):
        clear(self.body)
        user = userenv.read(userenv.user_file())
        system = userenv.read(userenv.SYSTEM_FILE)
        self._draw_scope(f"사용자 변수 — {getpass.getuser()}", "user", user)
        self._draw_scope("시스템 변수 — 모든 사용자 (관리자)", "system", system)

        s = subsection(self.body, "지금 적용된 값")
        path = userenv.target().get("PATH", "")
        r = row(s, "PATH — 명령을 찾는 폴더 (위에서부터)", "\n".join(p for p in path.split(":") if p) or "(비어 있음)")
        r.sub_label.set_selectable(True)
        self.body.show_all()

    def _draw_scope(self, title, scope, items):
        s = subsection(self.body, title)
        for name, raw in items:
            box = Gtk.Box(spacing=6)
            box.pack_start(button("편집…", lambda n=name, v=raw: self._edit(scope, n, v)), False, False, 0)
            box.pack_start(button("삭제", lambda n=name: self._delete(scope, n)), False, False, 0)
            r = row(s, name, describe(name, raw), control=box)
            if getattr(r, "sub_label", None):
                r.sub_label.set_selectable(True)
        if not items:
            row(s, "변수가 없습니다", "사용자 변수는 이 계정에만, 시스템 변수는 이 PC 의 모든 계정에 적용됩니다"
                if scope == "user" else "시스템 변수를 바꾸려면 관리자 인증이 필요합니다")
        row(s, "새 변수", "이름과 값을 정해 추가합니다 — 같은 이름이 있으면 바꿉니다",
            control=button("새로 만들기…", lambda: self._edit(scope, None, "")))

    # ── 편집 창 ──
    def _edit(self, scope, name, raw):
        win = parent_window(self.p)
        d = Gtk.Dialog(title=("새 " if name is None else "") + ("사용자 변수" if scope == "user" else "시스템 변수"),
                       transient_for=win, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "확인", Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)
        d.set_default_size(520, -1)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        box.add(grid)
        name_e = Gtk.Entry(text=name or "", hexpand=True)
        name_e.set_placeholder_text("예: JAVA_HOME")
        name_e.set_sensitive(name is None)
        name_e.get_accessible().set_name("변수 이름")
        grid.attach(Gtk.Label(label="변수 이름", xalign=0), 0, 0, 1, 1)
        grid.attach(name_e, 1, 0, 1, 1)

        # 보통 값 — 한 줄 (이름과 같은 칸에 맞춰)
        value_e = Gtk.Entry(text=raw, hexpand=True, activates_default=True)
        value_e.get_accessible().set_name("변수 값")
        value_l = Gtk.Label(label="변수 값", xalign=0)
        grid.attach(value_l, 0, 1, 1, 1)
        grid.attach(value_e, 1, 1, 1, 1)

        # 목록 값 — 폴더 목록 + 붙이는 방식
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mode_c = Gtk.ComboBoxText()
        for k, label in MODES:
            mode_c.append(k, label)
        mode_c.get_accessible().set_name("붙이는 방식")
        lst.pack_start(mode_c, False, False, 0)
        store = Gtk.ListStore(str)
        view = Gtk.TreeView(model=store, headers_visible=False, reorderable=True)
        cell = Gtk.CellRendererText(editable=True)
        cell.connect("edited", lambda _c, path, text: store.set_value(store.get_iter(path), 0, text.strip()))
        view.append_column(Gtk.TreeViewColumn("폴더", cell, text=0))
        view.get_accessible().set_name("폴더 목록")
        sc = Gtk.ScrolledWindow(min_content_height=160)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(view)
        lst.pack_start(sc, True, True, 0)
        btns = Gtk.Box(spacing=6)
        lst.pack_start(btns, False, False, 0)

        def selected():
            m, it = view.get_selection().get_selected()
            return it

        def add_typed():
            it = store.append([""])
            view.set_cursor(store.get_path(it), view.get_column(0), True)

        def browse():
            fc = Gtk.FileChooserDialog(title="폴더 고르기", transient_for=d, action=Gtk.FileChooserAction.SELECT_FOLDER)
            fc.add_buttons("취소", Gtk.ResponseType.CANCEL, "고르기", Gtk.ResponseType.OK)
            fc.set_current_folder(os.path.expanduser("~"))
            path = fc.get_filename() if fc.run() == Gtk.ResponseType.OK else None
            fc.destroy()
            if path:
                store.append([path])

        def remove():
            it = selected()
            if it:
                store.remove(it)

        def move(step):
            it = selected()
            if not it:
                return
            other = store.iter_previous(it) if step < 0 else store.iter_next(it)
            if other:
                store.swap(it, other)
        for label, fn in (("새로…", add_typed), ("찾아보기…", browse), ("삭제", remove),
                          ("위로", lambda: move(-1)), ("아래로", lambda: move(1))):
            btns.pack_start(button(label, fn), False, False, 0)
        box.add(lst)

        warn = Gtk.Label(xalign=0)
        warn.set_line_wrap(True)
        warn.get_style_context().add_class("row-sub")
        box.add(warn)

        state = {"list": None}

        def fill_list(n, value):
            mode, items = split_list(n, value) if value else ("append", [])
            mode_c.set_active_id(mode)
            store.clear()
            for i in items:
                store.append([i])

        def current_value(n):
            if state["list"]:
                return join_list(n, mode_c.get_active_id() or "append", [r[0].strip() for r in store])
            return value_e.get_text()

        def update(*_):
            n = name_e.get_text().strip()
            as_list = userenv.is_list(n)
            if as_list != state["list"]:
                # 보통 값 ↔ 목록 사이를 오갈 때 적던 값을 넘긴다
                if as_list:
                    fill_list(n, value_e.get_text() if state["list"] is not None else raw)
                elif state["list"]:
                    value_e.set_text(join_list(n, mode_c.get_active_id() or "append", [r[0] for r in store]))
                state["list"] = as_list
                value_l.set_visible(not as_list)
                value_e.set_visible(not as_list)
                lst.set_visible(as_list)
            msgs = []
            why = userenv.check_name(n) if n else None
            if why:
                msgs.append(why)
            if n in userenv.RISKY:
                msgs.append(f"{n} 을(를) 잘못 넣으면 앱이 뜨지 않거나 로그인이 안 될 수 있습니다.")
            if as_list and mode_c.get_active_id() == "replace":
                msgs.append(f"기존 {n} 을(를) 통째로 바꿉니다 — 기본 폴더가 빠지면 명령을 찾지 못합니다.")
            if scope == "system":
                msgs.append("이 PC 의 모든 계정에 적용됩니다.")
            warn.set_text("\n".join(msgs))
            warn.set_visible(bool(msgs))
        name_e.connect("changed", update)
        mode_c.connect("changed", update)
        # 보일 것을 정한 뒤에 보인다 — 목록을 먼저 그렸다 숨기면 창이 그 높이로 남았다
        lst.show_all()           # 안쪽은 미리 보여 둔다 — no_show_all 인 위젯에는 show_all 이 아무것도 안 한다
        lst.hide()
        for w in (value_l, value_e, lst, warn):
            w.set_no_show_all(True)
        update()
        d.show_all()

        def responded(dlg, resp):
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            n = name_e.get_text().strip()
            v = current_value(n)
            why = userenv.check_name(n) or userenv.check_value(v)
            if not why and state["list"] and not [r for r in store if r[0].strip()]:
                why = "폴더를 하나 이상 넣으세요"
            if why:
                warn.set_text(why)
                warn.show()
                return            # 창은 그대로 — 고쳐서 다시
            dlg.destroy()
            self._save(scope, n, v)
        d.connect("response", responded)

    def _delete(self, scope, name):
        confirm(parent_window(self.p), f"{name} 을(를) 지울까요?",
                "새로 여는 앱부터 이 변수가 없어집니다." if scope == "user" else
                "이 PC 의 모든 계정에서 새로 여는 앱부터 이 변수가 없어집니다.",
                "삭제", lambda: self._save(scope, name, None))

    # ── 저장 ──
    def _save(self, scope, name, value):
        if scope == "user":
            items = dict(userenv.read(userenv.user_file()))
            if value is None:
                items.pop(name, None)
            else:
                items[name] = value
            path = userenv.user_file()
            try:
                if items:
                    atomic_write(path, userenv.dump(list(items.items())))
                elif os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                self.say(f"저장하지 못했습니다: {e.strerror}", error=True)
                return
            self._applied("지웠습니다" if value is None else "저장했습니다")
            return
        if self.p.busy:
            return
        self.p.busy = True
        self.say("바꾸는 중…")
        args = ["unset", name] if value is None else ["set", name, value]

        def done(ok, out, err):
            self.p.busy = False
            if not ok:
                self.say(failure_reason(out or err, "바꾸지 못했습니다"), error=True)
                return
            self._applied("지웠습니다" if value is None else "저장했습니다")
        run_async(["pkexec", HELPER] + args, done)

    def _applied(self, text):
        # 이 세션의 systemd 사용자 서비스·D-Bus 로 뜨는 앱에도 (패널·탐색기는 파일을 보고 스스로 고친다)
        try:
            userenv.push_session(userenv.refresh())
        except Exception:
            pass
        if self.dead:
            return
        self.draw()
        self.say(f"{text} — 새로 여는 앱부터 적용됩니다")


def build(store):
    return EnvVarsPage(store).widget


PAGES = [{"id": "envvars", "title": "환경 변수", "icon": ICON, "build": build}]
