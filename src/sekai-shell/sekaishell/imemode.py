"""한/영 입력 상태 — 트레이의 ibus 아이콘(태극) 대신 "가"/"A" 를 그리려고.

ibus 패널(ibus-ui-gtk3)의 트레이 아이콘은 엔진 아이콘(ibus-hangul 의 태극)을 그대로 보여 준다.
작게 줄이면 뭉개지고, 지금 한글인지 영문인지도 알 수 없다.
한/영 상태는 한글 엔진의 "InputMode" 속성(state 1 = 한글)인데, 엔진은 이것을 ibus 자체 버스에서
패널에만 보낸다. 다만 ibus 패널이 받은 속성을 같은 버스에 알림으로 다시 내보내므로
(com.canonical.IBus.Panel.Private 의 PropertiesRegistered·PropertyUpdated) 그것을 듣는다.
ibus 가 다시 시작되면 IBus.Bus 가 새로 붙으며 connected 를 알린다 — 그때 다시 구독한다.
"""
import gi
from gi.repository import Gio

from . import dbg

PRIVATE = "com.canonical.IBus.Panel.Private"
PANEL_PATH = "/org/freedesktop/IBus/Panel"
KEY = "InputMode"


def _prop(t):
    """IBusProperty 직렬 구조 → (key, label, state). 모양이 다르면 None"""
    try:
        if t[0] != "IBusProperty":
            return None
        return t[2], t[4][2], int(t[9])
    except (IndexError, TypeError, ValueError):
        return None


class HangulMode:
    def __init__(self):
        self.state = None      # True 한글 · False 영문 · None 모름(초점 없음, 한글 엔진 아님)
        self.label = ""        # 엔진이 붙인 속성 이름("한글 상태") — 트레이 메뉴에서 같은 항목을 찾는 데 쓴다
        self._cbs = []
        self._conn = None
        self._sub = None
        try:
            gi.require_version("IBus", "1.0")
            from gi.repository import IBus
            self._bus = IBus.Bus()
        except (ImportError, ValueError) as e:
            dbg("IBus 없음 — 한/영 표시 끔:", e)
            self._bus = None
            return
        self._bus.connect("connected", lambda *_a: self._subscribe())
        self._bus.connect("disconnected", lambda *_a: self._drop())
        if self._bus.is_connected():
            self._subscribe()

    def on_change(self, cb):
        self._cbs.append(cb)

    def _subscribe(self):
        self._drop()
        conn = self._bus.get_connection()
        if conn is None:
            return
        self._conn = conn
        self._sub = conn.signal_subscribe(None, PRIVATE, None, PANEL_PATH, None,
                                          Gio.DBusSignalFlags.NONE, self._on_signal)

    def _drop(self):
        if self._conn is not None and self._sub is not None:
            try:
                self._conn.signal_unsubscribe(self._sub)
            except Exception:
                pass
        self._conn = self._sub = None
        self._set(None)

    def _on_signal(self, _conn, _sender, _path, _iface, name, params):
        try:
            v = params.unpack()[0]
        except Exception:
            return
        if name == "PropertiesRegistered":
            # 초점이 옮겨 갈 때마다 그 입력 칸의 속성 전체가 온다 — 한/영은 입력 칸마다 따로다
            state = None
            try:
                props = v[2] if v[0] == "IBusPropList" else []
            except (IndexError, TypeError):
                props = []
            for t in props:
                p = _prop(t)
                if p and p[0] == KEY:
                    self.label, state = p[1], p[2] == 1
            self._set(state)
        elif name == "PropertyUpdated":
            p = _prop(v)
            if p and p[0] == KEY:
                self.label = p[1]
                self._set(p[2] == 1)

    def _set(self, state):
        if state == self.state:
            return
        self.state = state
        for cb in self._cbs:
            try:
                cb()
            except Exception as e:
                dbg("한/영 표시 갱신 실패", e)
