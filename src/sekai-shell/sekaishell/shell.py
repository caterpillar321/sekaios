"""org.sekai.Shell — 단축키·스크립트가 셸(작업 표시줄 프로세스)을 부르는 창구.

sekai-ctl 이 gdbus 로 부른다. 신호(SIGUSR1 등)보다 나은 점:
인자를 넘길 수 있고, 어떤 명령이 있는지 한곳에 모여 있다.
"""
from gi.repository import GLib

from . import dbg
from . import dbusutil

NAME = "org.sekai.Shell"
PATH = "/org/sekai/Shell"
XML = """
<node>
  <interface name="org.sekai.Shell">
    <method name="Switch"><arg type="i" name="step" direction="in"/></method>
    <method name="SwitchCommit"/>
    <method name="Volume"><arg type="s" name="action" direction="in"/></method>
    <method name="Brightness"><arg type="s" name="action" direction="in"/></method>
    <method name="Snap"><arg type="s" name="direction" direction="in"/></method>
    <method name="Clipboard"/>
    <method name="StartMenu"/>
    <method name="NotificationCenter"/>
    <method name="QuickSettings"/>
    <method name="Osd">
      <arg type="s" name="icon" direction="in"/>
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="Desktop">
      <arg type="s" name="action" direction="in"/>
      <arg type="s" name="arg" direction="in"/>
    </method>
  </interface>
</node>
"""


class ShellService:
    """handlers: 메서드 이름 → 함수(인자...)"""

    def __init__(self, handlers):
        self.handlers = handlers
        self.conn = dbusutil.bus()
        info = dbusutil.node_info(XML)
        self.reg = dbusutil.export(self.conn, PATH, info.interfaces[0], self._call)
        dbusutil.own_name(NAME, on_lost=lambda n: dbg(f"[shell] {n} 이름을 잃었습니다"),
                          replace=True)

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation):
        fn = self.handlers.get(method)
        args = params.unpack() if params is not None else ()
        # 곧바로 답하고 일은 메인 루프에서 — 단축키 쪽이 기다리지 않게
        invocation.return_value(None)
        if fn is None:
            dbg(f"[shell] 모르는 메서드 {method}")
            return
        GLib.idle_add(lambda: (fn(*args), False)[1])
