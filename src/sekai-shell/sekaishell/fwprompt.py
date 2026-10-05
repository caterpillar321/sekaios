"""방화벽 "허용할까요?" — 윈도우처럼, 앱이 밖에서 들어오는 연결을 처음 기다리면 묻는다.

작업 표시줄(sekai-panel)이 몇 초마다 연결을 기다리는 내 앱(ss)을 보고, 지금 네트워크의 존에서 아직 열리지 않은
포트로 기다리는 앱이 있으면 창을 띄운다: [개인 네트워크에서 허용] [모든 네트워크에서 허용] [허용 안 함].
허용은 sekai-firewall app add (관리자 인증), 허용 안 함은 실행 파일 경로로 기억해 다시 묻지 않는다
(~/.local/state/sekai/firewall-decisions.json). 방화벽이 꺼져 있으면 묻지 않는다.
"""
import json
import os
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import dbg  # noqa: E402
from . import firewall  # noqa: E402

STATE = os.path.expanduser("~/.local/state/sekai/firewall-decisions.json")
EVERY = 4                       # 초


class FirewallPrompt:
    def __init__(self):
        self.asked = set()          # 묻는 중이거나, 고르지 않고 창을 닫은 exe — 이번 세션엔 다시 띄우지 않게 (포트가 바뀌어도).
        #   허용한 앱은 넣어 두지 않는다 — 나중에 허용을 지워(설정 › 방화벽) 포트가 다시 막히면 다시 묻는다
        self.busy = False
        self.dialog = None
        self.decisions = self._load()
        GLib.timeout_add_seconds(EVERY, self._tick)

    def _load(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self.decisions, f, ensure_ascii=False, indent=1)
            os.replace(STATE + ".tmp", STATE)
        except OSError as e:
            dbg("[방화벽] 결정 저장 실패", e)

    # ── 살피기 (작업 스레드 — ss·D-Bus 는 수십 ms) ──
    def _tick(self):
        if not self.busy and self.dialog is None:
            self.busy = True
            threading.Thread(target=self._scan, daemon=True).start()
        return True

    def _scan(self):
        found = None
        try:
            # 다른 곳(설정 앱·시험)이 결정 파일을 바꿨으면 다시 읽는다
            try:
                mt = os.path.getmtime(STATE)
            except OSError:
                mt = None
            if mt != getattr(self, "_mtime", None):
                if hasattr(self, "_mtime"):
                    self.asked.clear()                     # 결정을 지우거나 바꿨다 — 이번 세션에 물은 것도 다시
                self._mtime = mt
                self.decisions = self._load()
            if firewall.running():
                apps = [a for a in firewall.listening_apps()
                        # 허용·거부를 이미 고른 앱은 다시 묻지 않는다 (예전엔 허용한 앱도 포트가 늘면 또 물었다)
                        if a["exe"] not in self.decisions and a["exe"] not in self.asked]
                if apps:
                    zones = firewall.active_zones()
                    opened = set().union(*(firewall.zone_ports(z) for z in zones))
                    for a in apps:
                        closed = [p for p in a["ports"] if p not in opened]
                        if closed:
                            found = dict(a, closed=closed, zones=sorted(zones))
                            break
        except Exception as e:
            dbg("[방화벽] 살피기 실패", e)
        GLib.idle_add(self._found, found)

    def _found(self, a):
        self.busy = False
        if a:
            self.asked.add(a["exe"])
            self._ask(a)
        return False

    # ── 묻기 ──
    def _ask(self, a):
        d = Gtk.Window(title="방화벽")
        d.set_keep_above(True)
        d.set_resizable(False)
        d.get_style_context().add_class("sekai-fwprompt")
        self.dialog = d
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(20)
        d.add(box)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>방화벽이 이 앱의 연결을 막았습니다</b>")
        box.add(title)
        where = "공용 네트워크" if "public" in a["zones"] else "개인 네트워크"
        info = Gtk.Label(label=f"{a['name']} 이(가) 다른 기기에서 들어오는 연결을 기다립니다 ({', '.join(a['closed'])}).\n"
                               f"지금 네트워크({where})에서는 막혀 있습니다. 이 앱을 허용할까요?\n\n{a['exe']}", xalign=0)
        info.set_line_wrap(True)
        info.set_max_width_chars(60)
        info.set_selectable(True)
        box.add(info)
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        b_home = Gtk.Button(label="개인 네트워크에서 허용")
        b_all = Gtk.Button(label="모든 네트워크에서 허용")
        b_no = Gtk.Button(label="허용 안 함")
        for b in (b_no, b_all, b_home):
            btns.pack_start(b, False, False, 0)
        box.add(btns)
        b_home.connect("clicked", lambda *_: self._allow(a, "home"))
        b_all.connect("clicked", lambda *_: self._allow(a, "all"))
        b_no.connect("clicked", lambda *_: self._deny(a))
        d.connect("delete-event", lambda *_: (self._close(), False)[1])
        b_home.grab_focus()                 # 띄우기 전에 — 초점이 정해져 있지 않으면 GTK 가 첫 위젯(선택 가능한 안내 글)에 줘 글이 선택된다
        d.show_all()

    def _close(self):
        if self.dialog is not None:
            self.dialog.destroy()
        self.dialog = None

    def _deny(self, a):
        self.decisions[a["exe"]] = "deny"
        self._save()
        self._close()

    def _allow(self, a, where):
        from gi.repository import Gio
        self._close()
        cmd = ["pkexec", firewall.HELPER, "app", "add", a["id"], a["name"], where, *a["ports"]]
        try:
            proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            dbg("[방화벽] 도우미를 띄우지 못함", e.message)
            return

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error:
                out = ""
            if p.get_successful():
                self.decisions[a["exe"]] = "allow"
                self._save()
                self.asked.discard(a["exe"])
            else:
                dbg("[방화벽] 허용하지 못함 (인증 취소?)", (out or "").strip()[-200:])
        proc.communicate_utf8_async(None, None, done)
