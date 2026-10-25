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
EVERY = 4                       # 초 — 이때는 /proc/net 만 본다 (firewall.listen_signature)
FULL_EVERY = 15                 # 이만큼에 한 번은 기다리는 포트가 그대로여도 다 살핀다 (방화벽 설정·네트워크가 바뀐 경우)


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
            changed = mt != getattr(self, "_mtime", None)
            if changed:
                if hasattr(self, "_mtime"):
                    self.asked.clear()                     # 결정을 지우거나 바꿨다 — 이번 세션에 물은 것도 다시
                self._mtime = mt
                self.decisions = self._load()
            # 기다리는 포트가 그대로면 건너뛴다 — 예전엔 4초마다 systemctl·ss(때로 앱 목록 전부·nmcli)를 띄웠다
            sig = firewall.listen_signature()
            self._n = getattr(self, "_n", 0) + 1
            if not changed and sig == getattr(self, "_sig", None) and self._n % FULL_EVERY:
                GLib.idle_add(self._found, None)
                return
            self._sig = sig
            if firewall.running():
                listening = firewall.listening_apps()
                # 허용한 앱이 나중에 새 포트로도 기다리면 그 포트만 더할지 묻는다 — 허용할 때 열려 있던 포트만 서비스에
                #   들어가서, 뒤늦게 연 포트(Maya 의 18801)는 허용한 줄 알았는데 막혀 있었다 (2026-10-25).
                #   예전처럼 "허용할까요?"를 처음부터 다시 묻지는 않는다. 거부한 앱은 묻지 않는다
                for a in listening:
                    if self.decisions.get(a["exe"]) != "allow":
                        continue
                    svc = firewall.app_service(a["id"])
                    new = [p for p in a["ports"] if svc and p not in svc[0]]
                    if new and (a["exe"], tuple(new)) not in self.asked:
                        found = dict(a, more=new, have=sorted(svc[0]), where=svc[1])
                        break
                apps = [a for a in listening if a["exe"] not in self.decisions and a["exe"] not in self.asked]
                if apps and not found:
                    zones = firewall.active_zones()
                    opened = set().union(*(firewall.zone_ports(z) for z in zones))
                    for a in apps:
                        closed = [p for p in a["ports"] if p not in opened]
                        if closed:
                            found = dict(a, closed=closed, zones=sorted(zones),
                                         publics=firewall.public_connections() if "public" in zones else [])
                            break
        except Exception as e:
            dbg("[방화벽] 살피기 실패", e)
        GLib.idle_add(self._found, found)

    def _found(self, a):
        self.busy = False
        if a and a.get("more"):
            self.asked.add((a["exe"], tuple(a["more"])))
            self._ask_more(a)
        elif a:
            self.asked.add(a["exe"])
            self._ask(a)
        return False

    def _ask_more(self, a):
        """허용한 앱이 새 포트로도 기다린다 — 그 포트도 열지"""
        d = self._window()
        box = d.get_child()
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>허용한 앱이 새 포트를 씁니다</b>")
        box.add(title)
        where = "모든 네트워크" if a["where"] == "all" else "개인 네트워크"
        info = Gtk.Label(label=f"{a['name']} 이(가) 새 포트로도 연결을 기다립니다 ({', '.join(a['more'])}).\n"
                               f"이 앱은 {where}에서 허용되어 있지만 이 포트는 아직 막혀 있습니다. 이 포트도 열까요?\n\n{a['exe']}",
                         xalign=0)
        info.set_line_wrap(True)
        info.set_max_width_chars(60)
        info.set_selectable(True)
        box.add(info)
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        b_no = Gtk.Button(label="그대로 두기")
        b_yes = Gtk.Button(label="이 포트도 열기")
        for b in (b_no, b_yes):
            btns.pack_start(b, False, False, 0)
        box.add(btns)
        ports = sorted(set(a["have"]) | set(a["ports"]))[:16]
        b_yes.connect("clicked", lambda *_: (self._close(), self._helper(["app", "add", a["id"], a["name"], a["where"], *ports],
                                                                          lambda: None)))
        b_no.connect("clicked", lambda *_: self._close())
        d.connect("delete-event", lambda *_: (self._close(), False)[1])
        b_yes.grab_focus()
        d.show_all()

    def _window(self):
        d = Gtk.Window(title="방화벽")
        d.set_keep_above(True)
        d.set_resizable(False)
        d.get_style_context().add_class("sekai-fwprompt")
        self.dialog = d
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(20)
        d.add(box)
        return d

    # ── 묻기 ──
    def _ask(self, a):
        d = self._window()
        box = d.get_child()
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
        pub = a.get("publics") or []
        if pub:
            # 공용 네트워크에서 "개인에서 허용"을 고르면 지금 연결에선 여전히 막힌다 — 그걸 모르고 허용한 줄 알았다 (2026-10-25)
            names = ", ".join(n for _u, n in pub)
            note = Gtk.Label(label=f"지금 연결({names})은 공용 네트워크입니다. ‘개인 네트워크에서 허용’만 고르면 이 연결에서는 "
                                   f"계속 막힙니다. 집·회사처럼 믿을 수 있는 네트워크라면 ‘개인으로 바꾸고 허용’을 고르세요.",
                             xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(60)
            note.get_style_context().add_class("dim-label")
            box.add(note)
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        b_home = Gtk.Button(label="개인 네트워크에서 허용")
        b_all = Gtk.Button(label="모든 네트워크에서 허용")
        b_no = Gtk.Button(label="허용 안 함")
        for b in (b_no, b_all, b_home):
            btns.pack_start(b, False, False, 0)
        if pub:
            b_priv = Gtk.Button(label="개인으로 바꾸고 허용")
            btns.pack_start(b_priv, False, False, 0)
            b_priv.connect("clicked", lambda *_: self._allow(a, "home", make_private=[u for u, _n in pub]))
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

    def _allow(self, a, where, make_private=()):
        self._close()

        def done():
            self.decisions[a["exe"]] = "allow"
            self._save()
            self.asked.discard(a["exe"])
            for u in make_private:                # 관리자 인증은 방금 받아 둔 것(auth_admin_keep)으로 이어진다
                self._helper(["profile", u, "home"], lambda: None)
        self._helper(["app", "add", a["id"], a["name"], where, *a["ports"]], done)

    def _helper(self, args, on_ok):
        from gi.repository import Gio
        cmd = ["pkexec", firewall.HELPER, *args]
        try:
            proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            dbg("[방화벽] 도우미를 띄우지 못함", e.message)
            return

        def fin(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error:
                out = ""
            if p.get_successful():
                on_ok()
            else:
                dbg("[방화벽] 도우미 실패 (인증 취소?)", args[0], (out or "").strip()[-200:])
        proc.communicate_utf8_async(None, None, fin)
