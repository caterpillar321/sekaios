"""Gio 기반 D-Bus 헬퍼.

외부 의존성(dasbus/pydbus)을 늘리지 않으려고 Gio 를 직접 쓴다.
"""
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import dbg

SESSION = Gio.BusType.SESSION


def bus():
    return Gio.bus_get_sync(SESSION, None)


def node_info(xml):
    return Gio.DBusNodeInfo.new_for_xml(xml)


def export(conn, path, iface, on_call, on_get=None, on_set=None):
    """객체를 버스에 등록하고 등록 ID 를 돌려준다."""
    return conn.register_object(path, iface, on_call, on_get, on_set)


def own_name(name, on_acquired=None, on_lost=None, replace=False):
    flags = Gio.BusNameOwnerFlags.NONE
    if replace:
        flags |= Gio.BusNameOwnerFlags.REPLACE
    return Gio.bus_own_name(
        SESSION, name, flags, None,
        (lambda c, n: on_acquired(n)) if on_acquired else None,
        (lambda c, n: on_lost(n)) if on_lost else None)


def watch_vanish(conn, name, on_vanished):
    """버스 이름이 사라지면 알려 준다 (트레이 앱이 죽는 경우)."""
    return Gio.bus_watch_name_on_connection(
        conn, name, Gio.BusNameWatcherFlags.NONE, None,
        lambda c, n: on_vanished(n))


def emit(conn, path, iface, signal, variant=None):
    try:
        conn.emit_signal(None, path, iface, signal, variant)
    except Exception as e:
        dbg("시그널 실패", signal, e)


# ── 비동기 호출 ─────────────────────────────────────────────
def call(conn, service, path, iface, method, params=None,
         on_reply=None, timeout=3000):
    """메서드를 비동기로 부른다. 메인루프를 막지 않는 게 중요하다."""
    def done(src, res):
        try:
            out = src.call_finish(res)
        except Exception as e:
            dbg(f"호출 실패 {service}{path} {iface}.{method}: {e}")
            out = None
        if on_reply:
            on_reply(out)

    conn.call(service, path, iface, method, params, None,
              Gio.DBusCallFlags.NONE, timeout, None, done)


def get_all(conn, service, path, iface, on_reply, timeout=3000):
    """org.freedesktop.DBus.Properties.GetAll 을 비동기로."""
    def done(out):
        if out is None:
            on_reply({})
            return
        try:
            on_reply(out.unpack()[0])
        except Exception as e:
            dbg("GetAll 해석 실패", e)
            on_reply({})

    call(conn, service, path, "org.freedesktop.DBus.Properties", "GetAll",
         GLib.Variant("(s)", (iface,)), done, timeout)


def get_prop(conn, service, path, iface, prop, on_reply, timeout=3000):
    def done(out):
        if out is None:
            on_reply(None)
            return
        try:
            on_reply(out.unpack()[0])
        except Exception:
            on_reply(None)

    call(conn, service, path, "org.freedesktop.DBus.Properties", "Get",
         GLib.Variant("(ss)", (iface, prop)), done, timeout)


def subscribe(conn, sender, iface, signal, path, handler):
    """시그널 구독. handler(sender, path, signal, params_tuple)"""
    return conn.signal_subscribe(
        sender, iface, signal, path, None, Gio.DBusSignalFlags.NONE,
        lambda c, snd, pth, ifc, sig, params: handler(snd, pth, sig, params.unpack()))
