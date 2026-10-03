"""Nenerobo — 실행 (명령줄 · 한 프로그램에 여러 창).

  nenerobo [-T 제목] [--working-directory 폴더] [--new-tab] [--hold] [-e|-x|-- 명령 인자…]

다른 터미널을 부르던 방식도 그대로 받는다 — xterm·foot(-T · -e), gnome-terminal(-- 명령 · --title),
x-terminal-emulator(-e), sekai-terminal. -e 뒤에 글 하나만 오고 빈칸이 있으면 셸처럼 나눈다 ("-e 'htop -d 5'").
이미 떠 있으면 그 프로그램이 새 창을 연다 (부른 쪽의 환경 변수·폴더로). --new-tab 이면 마지막 창에 탭으로.
"""
import os
import shlex
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, style  # noqa: E402

APP_ID = "org.sekaios.Nenerobo"
USAGE = """사용법: nenerobo [옵션] [-e|-x|-- 명령 인자…]
  -T, --title 제목              탭 제목을 고정한다
  -d, --working-directory 폴더  이 폴더에서 연다
      --new-tab                 새 창 대신 떠 있는 창에 탭으로
      --hold                    명령이 끝나도 탭을 닫지 않는다
  -e, -x, --                    셸 대신 실행할 명령 (뒤의 것 모두)
  -h, --help · --version"""


def parse(args):
    """→ {"title", "cwd", "argv", "new_tab", "hold"} 또는 문자열(잘못된 인자 · 도움말)"""
    o = {"title": None, "cwd": None, "argv": None, "new_tab": False, "hold": False}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "-x", "--execute", "--"):
            rest = args[i + 1:]
            if len(rest) == 1 and " " in rest[0]:
                try:
                    rest = shlex.split(rest[0])
                except ValueError:
                    pass
            o["argv"] = rest or None
            break
        if a in ("-h", "--help"):
            return USAGE
        if a == "--version":
            return f"Nenerobo {__version__}"
        if a in ("-T", "--title", "-t") and i + 1 < len(args):
            o["title"] = args[i + 1]
            i += 2
            continue
        if a.startswith("--title="):
            o["title"] = a.split("=", 1)[1]
        elif a in ("-d", "--working-directory", "--cwd", "--directory", "-D") and i + 1 < len(args):
            o["cwd"] = args[i + 1]
            i += 2
            continue
        elif a.startswith(("--working-directory=", "--cwd=", "--directory=")):
            o["cwd"] = a.split("=", 1)[1]
        elif a in ("--new-tab", "--tab"):
            o["new_tab"] = True
        elif a in ("--hold", "-H"):
            o["hold"] = True
        elif a.startswith(("--class", "--app-id", "--name", "-o", "--option", "--config")):
            if "=" not in a and i + 1 < len(args):     # 다른 터미널의 옵션 — 값과 함께 넘긴다
                i += 1
        elif a.startswith("-"):
            pass                                       # 모르는 옵션은 넘긴다 (다른 터미널용 스크립트가 깨지지 않게)
        else:
            o["argv"] = args[i:]                       # 옵션 없이 명령만 (foot · kitty 처럼)
            break
        i += 1
    return o


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.SEND_ENVIRONMENT)
        self.ap = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.ap = style.appearance()
        s = Gtk.Settings.get_default()
        s.set_property("gtk-application-prefer-dark-theme", self.ap["mode"] == "dark")
        s.set_property("gtk-decoration-layout", ":minimize,maximize,close")   # 윈도우처럼 오른쪽에 세 단추
        css = Gtk.CssProvider()
        css.load_from_string(style.css(self.ap, style.SCHEMES[self.ap["mode"]]))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_command_line(self, cl):
        o = parse(cl.get_arguments()[1:])
        if isinstance(o, str):
            cl.print_literal(o + "\n")
            return 0
        env = {}
        for kv in cl.get_environ() or []:
            k, _s, v = kv.partition("=")
            env[k] = v
        env = env or dict(os.environ)
        base = cl.get_cwd() or env.get("PWD") or os.path.expanduser("~")
        cwd = o["cwd"]
        if cwd:
            cwd = os.path.expanduser(cwd)
            if not os.path.isabs(cwd):
                cwd = os.path.join(base, cwd)
        else:
            cwd = base
        from .window import TermWindow
        wins = [w for w in self.get_windows() if isinstance(w, TermWindow)]
        if o["new_tab"] and wins:
            w = wins[0]
        else:
            w = TermWindow(self, self.ap)
        w.new_tab(argv=o["argv"], cwd=cwd, env=env, title=o["title"], hold=o["hold"])
        w.present()
        return 0


def main(argv=None):
    argv = sys.argv if argv is None else [sys.argv[0]] + list(argv)
    GLib.set_prgname(APP_ID)
    return App().run(argv)
