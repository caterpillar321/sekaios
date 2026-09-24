"""가족 및 다른 사용자 — 이 컴퓨터를 쓰는 계정 추가·삭제·관리자 권한.

관리자 권한이 필요한 일은 /usr/libexec/sekai/sekai-users 가 한다 (pkexec).
"""
import getpass
import grp
import pwd
import re
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..widgets import Page, button, row

HELPER = "/usr/libexec/sekai/sekai-users"


def human_users():
    out = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")):
            out.append(p)
    return sorted(out, key=lambda p: p.pw_uid)


def is_admin(name):
    try:
        return name in grp.getgrnam("sudo").gr_mem
    except KeyError:
        return False


def logged_in():
    try:
        out = subprocess.run(["loginctl", "list-sessions", "--no-legend"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return set()
    return {ln.split()[2] for ln in out.splitlines() if len(ln.split()) > 2}


class UsersPage:
    def __init__(self, store):
        self.me = getpass.getuser()
        self.p = Page("가족 및 다른 사용자", "이 컴퓨터를 쓰는 사람마다 따로 계정을 만들면 "
                      "파일·설정·앱 로그인이 서로 섞이지 않습니다.")
        s = self.p.section()
        row(s, "다른 사용자 추가", "새 계정을 만듭니다",
            icon=["list-add", "contact-new", "system-users"],
            control=button("계정 추가…", self.add_dialog))
        self.list = self.p.section("이 컴퓨터의 계정")
        self.msg = Gtk.Label(xalign=0)
        self.msg.get_style_context().add_class("notice")
        self.msg.set_line_wrap(True)
        self.msg.set_no_show_all(True)
        self.p.add_widget(self.msg)
        self.fill()

    @property
    def widget(self):
        return self.p

    def fill(self):
        for r in self.list.get_children():
            self.list.remove(r)
        on = logged_in()
        for u in human_users():
            real = (u.pw_gecos or "").split(",")[0].strip() or u.pw_name
            tags = ["관리자" if is_admin(u.pw_name) else "표준 사용자"]
            if u.pw_name == self.me:
                tags.append("나")
            elif u.pw_name in on:
                tags.append("로그인됨")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if u.pw_name != self.me:
                adm = is_admin(u.pw_name)
                box.pack_start(button("관리자 권한 빼기" if adm else "관리자로 만들기",
                                      lambda n=u.pw_name, a=adm: self.run(["admin", n, "off" if a else "on"])),
                               False, False, 0)
                box.pack_start(button("삭제…", lambda n=u.pw_name, r=real: self.remove_dialog(n, r)),
                               False, False, 0)
            row(self.list, f"{real}  ({u.pw_name})", " · ".join(tags),
                icon=["avatar-default", "user-identity"], control=box)
        self.list.show_all()

    def show_msg(self, text):
        self.msg.set_text(text)
        self.msg.set_visible(bool(text))

    # ── 도우미 ──
    def run(self, args, stdin=None, done=None):
        self.show_msg("")

        def work():
            try:
                r = subprocess.run(["pkexec", HELPER] + args, input=stdin, capture_output=True,
                                   text=True, timeout=120)
                if r.returncode in (126, 127):
                    res = "인증이 취소되었습니다"
                else:
                    res = None if r.returncode == 0 else (r.stdout.strip() or "실패했습니다")
            except Exception as e:
                res = f"실행하지 못했습니다: {e}"
            GLib.idle_add(finish, res)

        def finish(err):
            self.fill()
            if err:
                self.show_msg(err)
            if done:
                done(err)
            return False
        threading.Thread(target=work, daemon=True).start()

    # ── 추가 ──
    def add_dialog(self):
        win = self.p.get_toplevel()
        d = Gtk.Dialog(title="계정 추가", transient_for=win, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "만들기", Gtk.ResponseType.OK)
        ok = d.get_widget_for_response(Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        g = Gtk.Grid(column_spacing=12, row_spacing=8)
        f = {k: Gtk.Entry() for k in ("full", "user", "pw", "pw2")}
        for k in ("pw", "pw2"):
            f[k].set_visibility(False)
        f["full"].set_placeholder_text("표시 이름 — 한글도 됩니다")
        f["user"].set_placeholder_text("영문 소문자로 시작")
        for i, (k, t) in enumerate((("full", "이름"), ("user", "사용자 이름"),
                                   ("pw", "암호"), ("pw2", "암호 확인"))):
            g.attach(Gtk.Label(label=t, xalign=0), 0, i, 1, 1)
            f[k].set_width_chars(26)
            g.attach(f[k], 1, i, 1, 1)
        box.add(g)
        adm = Gtk.CheckButton(label="관리자 권한 주기 (프로그램 설치·시스템 설정을 바꿀 수 있음)")
        box.add(adm)
        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("row-sub")
        box.add(err)
        touched = {"user": False}

        def check(*_):
            u, p1, p2 = f["user"].get_text(), f["pw"].get_text(), f["pw2"].get_text()
            msg = ""
            if u and not re.match(r"^[a-z][a-z0-9_-]{0,30}$", u):
                msg = "사용자 이름은 영문 소문자로 시작하고 영문 소문자·숫자·_·- 만 쓸 수 있습니다"
            elif u and any(p.pw_name == u for p in pwd.getpwall()):
                msg = "이미 있는 사용자 이름입니다"
            elif p2 and p1 != p2:
                msg = "암호가 서로 다릅니다"
            err.set_text(msg)
            ok.set_sensitive(bool(u and p1 and p1 == p2 and not msg))

        def auto(*_):
            if not touched["user"]:
                base = re.sub(r"[^a-z0-9]", "", f["full"].get_text().lower().split(" ")[0])
                f["user"].set_text(base[:20] if base[:1].isalpha() else "")
            check()
        f["full"].connect("changed", auto)
        f["user"].connect("key-press-event", lambda *_: touched.update(user=True))
        for k in ("user", "pw", "pw2"):
            f[k].connect("changed", check)
        check()
        d.show_all()
        if d.run() == Gtk.ResponseType.OK:
            name = f["user"].get_text()
            stdin = f["full"].get_text().strip() + "\n" + f["pw"].get_text() + "\n"
            self.run(["add", name, "admin" if adm.get_active() else "standard"], stdin=stdin,
                     done=lambda e: e or self.show_msg(f"{name} 계정을 만들었습니다. "
                                                        "시작 메뉴 › 사용자 전환으로 바로 들어가 볼 수 있습니다."))
        d.destroy()

    # ── 삭제 ──
    def remove_dialog(self, name, real):
        win = self.p.get_toplevel()
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.NONE, text=f"{real} 계정을 삭제할까요?")
        d.format_secondary_text("계정을 지우면 이 사람은 더 이상 로그인할 수 없습니다. "
                                "파일(홈 폴더)을 남길지 함께 지울지 고르세요.")
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "계정만 삭제", 1, "파일까지 삭제", 2)
        r = d.run()
        d.destroy()
        if r in (1, 2):
            self.run(["remove", name, "keep" if r == 1 else "delete"])


def build(store):
    return UsersPage(store).widget


PAGES = [{"id": "users", "title": "가족 및 다른 사용자",
          "icon": ["system-users", "user-others", "avatar-default"],
          "build": build}]
