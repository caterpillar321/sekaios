"""블루투스 — bluez(시스템 버스의 org.bluez) 상태를 들고 있는 공용 모듈.

패널(빠른 설정)과 설정 앱(블루투스 페이지)이 같이 쓴다. 프로세스마다 하나 — get().
  · 상태: ObjectManager(GetManagedObjects · InterfacesAdded/Removed)와 PropertiesChanged 로 늘 최신.
    bluetoothd 가 나중에 떠도 붙고, 사라지면 available=False.
  · 켜고 끄기: 끌 때는 Powered=false 뒤 이 어댑터 자신의 rfkill(hci0)을 soft block 한다 —
    bluez 는 켜짐 상태를 기억하지 않아(AutoEnable) 부팅마다 다시 켜지지만, systemd-rfkill 은 막힘을 기억한다.
    켤 때는 막힘을 먼저 푼다. /dev/rfkill 은 활성 세션 사용자에게 uaccess 로 열린다.
  · 짝 맺기 에이전트(org.bluez.Agent1): register_agent(ui) — 질문을 ui 에 넘기고, 답이 오면 D-Bus 로 돌려준다.
D-Bus 호출은 모두 비동기 — 메인 루프를 막지 않는다. 콜백은 메인 루프에서 불린다.

에이전트 ui 는 아래 메서드를 가진 객체 (없는 메서드의 질문은 거절한다). dev 는 devices() 의 한 항목:
    confirm(dev, code, reply)        "장치에 이 숫자(6자리 문자열)가 보이나요?" — reply(True/False)
    display(dev, code, entered)      장치에서 이 숫자/PIN 을 입력하라고 보여 주기만 (답 없음).
                                     entered 는 지금까지 입력한 글자 수 (PIN 이면 None)
    ask_code(dev, numeric, reply)    암호 입력 — numeric 이면 숫자 암호 키(0~999999), 아니면 PIN(1~16글자).
                                     reply(문자열 또는 None=취소)
    authorize(dev, uuid, reply)      짝 맺지 않은 장치의 짝 맺기(uuid=None)·서비스 연결을 허락할까 — reply(True/False)
    cancel()                         bluez 가 질문을 거뒀다 (시간 초과·장치 쪽 취소) — 떠 있는 대화상자를 닫는다
"""
import os
import re
import struct

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import dbg

BUS_TYPE = Gio.BusType.SYSTEM
BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
BATTERY = "org.bluez.Battery1"
AGENT_MANAGER = "org.bluez.AgentManager1"
IFACES = (ADAPTER, DEVICE, BATTERY)
AGENT_PATH = "/org/sekaios/bluetooth/agent"
AGENT_CAPABILITY = "KeyboardDisplay"

# 검색 중엔 장치마다 초마다 바뀌는 값 — 이것만 바뀌었으면 알림을 1초에 한 번으로 모은다
NOISY = frozenset(("RSSI", "TxPower", "ManufacturerData", "ServiceData",
                   "AdvertisingFlags", "AdvertisingData"))

# linux/rfkill.h — 읽을 때도 이 8바이트(V1)만 온다 (RFKILL_IOCTL_MAX_SIZE 로 늘리지 않는 한)
RFKILL_DEV = "/dev/rfkill"
RFKILL_TYPE_BLUETOOTH = 2
RFKILL_OP_ADD, RFKILL_OP_DEL, RFKILL_OP_CHANGE, RFKILL_OP_CHANGE_ALL = 0, 1, 2, 3
RFKILL_EVENT = struct.Struct("=IBBBB")        # idx, type, op, soft, hard

POWER_WAIT_US = 10 * 1000000                  # 막힘을 푼 뒤 어댑터가 올라오기를 기다리는 한도
CALL_MS = 25000
CONNECT_MS = 60000
PAIR_MS = 120000                              # 사람이 암호를 확인하는 동안 기다린다

AGENT_XML = """<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey">
    <arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/>
  </method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>"""
REJECTED = "org.bluez.Error.Rejected"
CANCELED = "org.bluez.Error.Canceled"

# ── 오류 → 사람이 읽을 말 ───────────────────────────────────
NO_SERVICE = "블루투스 서비스(bluetoothd)가 실행되고 있지 않습니다"
NO_ADAPTER = "블루투스 어댑터를 찾지 못했습니다"
NO_RESPONSE = "장치가 응답하지 않습니다. 장치가 켜져 있고 가까이 있는지 확인하세요"
NO_PROFILE = "이 장치에서 이 PC 가 쓸 수 있는 기능(오디오·입력 등)이 없습니다"
HARD_BLOCKED = "하드웨어 스위치(비행기 모드 키 등)로 꺼져 있습니다"
RFKILL_DENIED = "무선 차단(rfkill)을 풀지 못했습니다 — /dev/rfkill 을 열 권한이 없습니다"

_ERRORS = {
    "org.bluez.Error.Blocked": "블루투스가 무선 차단(rfkill)으로 꺼져 있습니다",
    "org.bluez.Error.NotReady": "블루투스가 꺼져 있습니다",
    "org.bluez.Error.InProgress": "이미 진행 중입니다",
    "org.bluez.Error.AlreadyConnected": "이미 연결되어 있습니다",
    "org.bluez.Error.AlreadyExists": "이미 짝을 맺은 장치입니다",
    "org.bluez.Error.DoesNotExist": "장치를 찾을 수 없습니다",
    "org.bluez.Error.NotConnected": "연결되어 있지 않습니다",
    "org.bluez.Error.NotAvailable": "이 장치는 지원하지 않는 기능입니다",
    "org.bluez.Error.NotSupported": "이 장치는 지원하지 않는 기능입니다",
    "org.bluez.Error.NotPermitted": "권한이 없습니다",
    "org.bluez.Error.NotAuthorized": "권한이 없습니다",
    "org.bluez.Error.AuthenticationFailed": "인증에 실패했습니다. 암호가 맞는지 확인하세요",
    "org.bluez.Error.AuthenticationCanceled": "짝 맺기를 취소했습니다",
    "org.bluez.Error.AuthenticationRejected": "짝 맺기를 거절했습니다",
    "org.bluez.Error.AuthenticationTimeout": "장치가 제때 답하지 않았습니다",
    "org.bluez.Error.ConnectionAttemptFailed": NO_RESPONSE,
    "org.freedesktop.DBus.Error.AccessDenied": "권한이 없습니다",
    "org.freedesktop.DBus.Error.NoReply": "시간이 초과되었습니다",
    "org.freedesktop.DBus.Error.Timeout": "시간이 초과되었습니다",
    "org.freedesktop.DBus.Error.TimedOut": "시간이 초과되었습니다",
    "org.freedesktop.DBus.Error.ServiceUnknown": NO_SERVICE,
    "org.freedesktop.DBus.Error.NameHasNoOwner": NO_SERVICE,
    "org.freedesktop.DBus.Error.UnknownObject": "장치를 찾을 수 없습니다",
    "org.freedesktop.DBus.Error.UnknownMethod": "이 bluez 가 지원하지 않는 요청입니다",
}
# org.bluez.Error.Failed 등은 이름만으론 알 수 없다 — 메시지(br-connection-… 등) 조각으로
_FRAGMENTS = (
    (("key-missing",), "장치가 이 PC 와의 짝 정보를 잃었습니다. 제거한 뒤 다시 짝을 맺으세요"),
    (("profile-unavailable", "protocol not available", "not-supported"), NO_PROFILE),
    (("refused", "by-remote"), "장치가 연결을 거절했습니다"),
    (("page-timeout", "connection-timeout", "abort-by-local", "aborted-by-local", "host is down",
      "timed out", "timeout"), NO_RESPONSE),
    (("canceled", "cancelled"), "취소했습니다"),
    (("concurrent-connection-limit",), "동시에 연결할 수 있는 장치 수를 넘었습니다"),
    (("busy", "in progress"), "장치가 바쁩니다. 잠시 후 다시 해 보세요"),
    (("rfkill", "blocked"), "블루투스가 무선 차단(rfkill)으로 꺼져 있습니다"),
    (("not-powered", "not ready"), "블루투스가 꺼져 있습니다"),
)
_REMOTE = re.compile(r"^GDBus\.Error:([\w.\-]+):\s*(.*)$", re.S)
_GENERIC = ("", "org.bluez.Error.Failed", "org.freedesktop.DBus.Error.Failed")


def error_text(name, raw=""):
    """D-Bus 오류 이름과 메시지 → 한 줄"""
    if name not in _GENERIC and name in _ERRORS:
        return _ERRORS[name]
    low = (raw or "").lower()
    for keys, text in _FRAGMENTS:
        if any(k in low for k in keys):
            return text
    return _ERRORS.get(name) or (raw or "").strip() or name or "알 수 없는 오류"


def _err(e):
    """GLib.Error → (D-Bus 오류 이름, 한 줄)"""
    msg = getattr(e, "message", None) or str(e)
    m = _REMOTE.match(msg)
    name, raw = (m.group(1), m.group(2).strip()) if m else ("", msg.strip())
    if not name and isinstance(e, GLib.Error) and e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        name = "org.freedesktop.DBus.Error.Timeout"
    if "already-connected" in raw.lower():         # Failed 로 오기도 한다 — 이름으로 맞춰 연결 성공으로 치게
        name = "org.bluez.Error.AlreadyConnected"
    return name, error_text(name, raw)


# 에이전트가 허락을 물을 때 보여 줄 기능 이름 (UUID 앞 8자리)
_SERVICES = {
    "0000110a": "오디오 보내기", "0000110b": "오디오", "0000110d": "오디오",
    "0000110c": "리모컨", "0000110e": "리모컨", "0000110f": "리모컨",
    "00001108": "헤드셋 통화", "00001112": "헤드셋 통화",
    "0000111e": "핸즈프리 통화", "0000111f": "핸즈프리 통화",
    "00001124": "입력 장치(키보드·마우스)", "00001812": "입력 장치(키보드·마우스)",
    "00001105": "파일 받기", "00001106": "파일 전송",
    "00001115": "네트워크 공유", "00001116": "네트워크 공유", "00001117": "네트워크 공유",
    "0000112f": "연락처", "00001132": "메시지",
}


def service_name(uuid):
    return _SERVICES.get((uuid or "").lower()[:8], uuid or "")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _addr_of(path):
    """…/dev_AA_BB_CC_DD_EE_FF → AA:BB:CC:DD:EE:FF (장치 정보를 아직 모를 때)"""
    tail = (path or "").rsplit("/", 1)[-1]
    return tail[4:].replace("_", ":") if tail.startswith("dev_") else tail


def _call_cb(cb, ok, err):
    if cb is None:
        return
    try:
        cb(ok, err)
    except Exception as e:
        dbg("[bluetooth] 콜백 실패", repr(e))


class Bluetooth:
    def __init__(self):
        self.available = False     # 쓸 수 있는 어댑터가 있나
        self.powered = False
        self.blocked = False       # rfkill soft block (어댑터가 올라오지 않은 노트북 무선 스위치도 포함)
        self.hard_blocked = False  # 하드웨어 스위치로 막힘 — 소프트웨어로는 못 푼다
        self.service = False       # bluetoothd 가 버스에 있나
        self.ready = False         # 처음 상태를 다 읽었나 (그 전의 available=False 는 "없음"이 아니다)
        self.discovering = False
        self.adapter = None        # 쓰는 어댑터의 객체 경로 (/org/bluez/hci0)
        self._conn = None
        self._owner = None         # org.bluez 의 고유 이름 — 에이전트 호출이 진짜 bluetoothd 에서 왔는지 본다
        self._gen = 0              # bluetoothd 가 다시 뜰 때마다 +1 — 옛 GetManagedObjects 답은 버린다
        self._objs = {}            # 경로 → {인터페이스: {속성: 값}}
        self._cbs = {}
        self._next_id = 1
        self._notify_src = 0
        self._notify_soon = False
        self._rf = {}              # 블루투스 rfkill 번호 → (soft, hard)
        self._rf_fd = -1
        self._power_gen = 0        # 켜고 끄기가 겹치면 나중 요청만 끝까지 간다
        self._agent_ui = None
        self._agent_obj = 0        # register_object 번호
        self._agent_on = False     # bluez 에 등록했나 (또는 등록하는 중)
        self._ask = None           # 답을 기다리는 질문 (번호, invocation, ui)
        self._ask_n = 0
        self._rfkill_open()
        Gio.bus_get(BUS_TYPE, None, self._on_bus)

    # ── 알림 ────────────────────────────────────────────────
    def on_change(self, cb):
        cid = self._next_id
        self._next_id += 1
        self._cbs[cid] = cb
        return cid

    def off(self, cb_id):
        self._cbs.pop(cb_id, None)

    def _changed(self, soon=True):
        if self._notify_src:
            if not soon or self._notify_soon:
                return                              # 이미 알릴 차례가 잡혀 있다
            GLib.source_remove(self._notify_src)    # 느린 알림(신호 세기) 대신 곧바로
        self._notify_soon = soon
        self._notify_src = GLib.idle_add(self._fire) if soon else GLib.timeout_add(1000, self._fire)

    def _fire(self):
        self._notify_src = 0
        for cb in list(self._cbs.values()):
            try:
                cb()
            except Exception as e:
                dbg("[bluetooth] 알림 처리 실패", repr(e))
        return False

    # ── 읽기 ────────────────────────────────────────────────
    def _aprop(self, key, default=None):
        if self.adapter is None:
            return default
        return self._objs.get(self.adapter, {}).get(ADAPTER, {}).get(key, default)

    @property
    def adapter_name(self):
        """다른 장치에 보이는 이 PC 의 이름"""
        return self._aprop("Alias") or self._aprop("Name") or ""

    @property
    def adapter_address(self):
        return self._aprop("Address", "")

    @property
    def discoverable(self):
        return bool(self._aprop("Discoverable", False))

    @property
    def discoverable_timeout(self):
        """찾을 수 있게 한 뒤 저절로 꺼지기까지 초 (0 = 끄지 않음)"""
        return int(self._aprop("DiscoverableTimeout", 0) or 0)

    def devices(self):
        """쓰는 어댑터의 장치들 — 짝 맺은 것 먼저, 이름 순"""
        out = [self._info(p) for p, o in self._objs.items()
               if DEVICE in o and o[DEVICE].get("Adapter") == self.adapter]
        out.sort(key=lambda d: (not d["paired"], d["name"].casefold(), d["address"]))
        return out

    def device(self, path):
        return self._info(path) if DEVICE in self._objs.get(path, {}) else None

    def _info(self, path):
        o = self._objs.get(path, {})
        d = o.get(DEVICE, {})
        pct = o.get(BATTERY, {}).get("Percentage")
        addr = d.get("Address") or _addr_of(path)
        alias = d.get("Alias") or ""
        # 이름이 없으면 bluez 는 Alias 에 주소(AA-BB-…)를 넣어 준다 — 그건 이름으로 치지 않는다
        named = bool(d.get("Name")) or bool(alias and alias.replace("-", ":").upper() != addr.upper())
        return {"path": path, "address": addr, "name": alias if named else addr, "named": named,
                "icon": d.get("Icon") or "bluetooth",
                "paired": bool(d.get("Paired")), "connected": bool(d.get("Connected")),
                "trusted": bool(d.get("Trusted")), "blocked": bool(d.get("Blocked")),
                "battery": int(pct) if pct is not None else None,
                "rssi": d.get("RSSI")}

    # ── 버스 ────────────────────────────────────────────────
    def _on_bus(self, _src, res):
        try:
            conn = Gio.bus_get_finish(res)
        except GLib.Error as e:
            dbg("[bluetooth] 시스템 버스에 붙지 못했습니다:", e.message)
            self.ready = True
            self._changed()
            return
        self._conn = conn
        for iface, sig, fn in ((OM, "InterfacesAdded", self._on_added),
                               (OM, "InterfacesRemoved", self._on_removed),
                               (PROPS, "PropertiesChanged", self._on_props)):
            conn.signal_subscribe(BLUEZ, iface, sig, None, None, Gio.DBusSignalFlags.NONE, fn)
        Gio.bus_watch_name_on_connection(conn, BLUEZ, Gio.BusNameWatcherFlags.NONE,
                                         self._on_appeared, self._on_vanished)

    def _on_appeared(self, conn, _name, owner):
        dbg("[bluetooth] bluetoothd:", owner)
        self._owner = owner
        self.service = True
        self._gen += 1
        self._objs = {}
        conn.call(BLUEZ, "/", OM, "GetManagedObjects", None, GLib.VariantType("(a{oa{sa{sv}}})"),
                  Gio.DBusCallFlags.NONE, CALL_MS, None, self._on_objects, self._gen)
        if self._agent_ui is not None:
            self._agent_register()

    def _on_vanished(self, _conn, _name):
        dbg("[bluetooth] bluetoothd 없음")
        self._owner = None
        self.service = False
        self._gen += 1
        self._objs = {}
        self._agent_on = False                  # bluez 가 다시 뜨면 새로 등록한다
        self._ask_drop()
        self.ready = True
        self._recompute()
        self._changed()

    def _on_objects(self, conn, res, gen):
        if gen != self._gen:
            return
        try:
            objs = conn.call_finish(res).unpack()[0]
        except GLib.Error as e:
            dbg("[bluetooth] GetManagedObjects 실패:", e.message)
            objs = {}
        # 이 답이 기준이다 — 먼저 온 신호들은 이 상태보다 옛것이다
        self._objs = {}
        for path, ifaces in objs.items():
            o = {i: dict(v) for i, v in ifaces.items() if i in IFACES}
            if o:
                self._objs[path] = o
        self.ready = True
        self._recompute()
        self._changed()

    def _on_added(self, _c, _sender, _path, _iface, _sig, params):
        path, ifaces = params.unpack()
        o = self._objs.setdefault(path, {})
        for i, props in ifaces.items():
            if i in IFACES:
                o[i] = dict(props)
        if not o:
            self._objs.pop(path, None)
            return
        self._recompute()
        self._changed()

    def _on_removed(self, _c, _sender, _path, _iface, _sig, params):
        path, ifaces = params.unpack()
        o = self._objs.get(path)
        if o is None:
            return
        for i in ifaces:
            o.pop(i, None)
        if not o:
            self._objs.pop(path, None)
        self._recompute()
        self._changed()

    def _on_props(self, _c, _sender, path, _iface, _sig, params):
        iface, changed, invalid = params.unpack()
        props = self._objs.get(path, {}).get(iface) if iface in IFACES else None
        if props is None:
            return
        props.update(changed)
        for k in invalid:
            props.pop(k, None)
        if iface == ADAPTER:
            self._recompute()
        self._changed(soon=not (set(changed) | set(invalid)) <= NOISY)

    def _recompute(self):
        adapters = sorted(p for p, o in self._objs.items() if ADAPTER in o)
        if self.adapter not in adapters:
            self.adapter = adapters[0] if adapters else None
        self.available = self.adapter is not None
        self.powered = bool(self._aprop("Powered", False))
        self.discovering = bool(self._aprop("Discovering", False))
        self._update_blocked()

    # ── rfkill ──────────────────────────────────────────────
    def _rfkill_open(self):
        for mode in (os.O_RDWR, os.O_RDONLY):
            try:
                self._rf_fd = os.open(RFKILL_DEV, mode | os.O_NONBLOCK | os.O_CLOEXEC)
                break
            except OSError:
                continue
        if self._rf_fd < 0:
            dbg("[bluetooth] /dev/rfkill 을 열지 못했습니다 — sysfs 로 읽기만 합니다")
            self._rfkill_sysfs()
            self._update_blocked()
            return
        self._rfkill_read()                    # 열자마자 지금 있는 rfkill 장치들이 ADD 로 줄지어 온다
        self._update_blocked()
        GLib.io_add_watch(self._rf_fd, GLib.PRIORITY_DEFAULT,
                          GLib.IOCondition.IN | GLib.IOCondition.ERR | GLib.IOCondition.HUP,
                          self._rfkill_io)

    def _rfkill_io(self, _fd, cond):
        if cond & (GLib.IOCondition.ERR | GLib.IOCondition.HUP):
            dbg("[bluetooth] /dev/rfkill 이 닫혔습니다")
            os.close(self._rf_fd)
            self._rf_fd = -1
            return False
        self._rfkill_read()
        if self._update_blocked():
            self._changed()
        return True

    def _rfkill_read(self):
        while True:
            try:
                data = os.read(self._rf_fd, 64)
            except BlockingIOError:
                return
            except OSError as e:
                dbg("[bluetooth] rfkill 읽기 실패", e)
                return
            if len(data) < RFKILL_EVENT.size:
                return
            idx, typ, op, soft, hard = RFKILL_EVENT.unpack_from(data)
            if typ != RFKILL_TYPE_BLUETOOTH:
                continue
            if op == RFKILL_OP_DEL:
                self._rf.pop(idx, None)
            elif op in (RFKILL_OP_ADD, RFKILL_OP_CHANGE):
                self._rf[idx] = (bool(soft), bool(hard))

    def _rfkill_sysfs(self):
        rf = {}
        base = "/sys/class/rfkill"
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        for n in names:
            if not n.startswith("rfkill") or not n[6:].isdigit():
                continue
            if _read(f"{base}/{n}/type") != "bluetooth":
                continue
            rf[int(n[6:])] = (_read(f"{base}/{n}/soft") == "1", _read(f"{base}/{n}/hard") == "1")
        self._rf = rf

    def _update_blocked(self):
        """blocked·hard_blocked 를 새로 — 바뀌었으면 True"""
        if self._rf_fd < 0:
            self._rfkill_sysfs()
        soft = any(s for s, _h in self._rf.values())
        hard = any(h for _s, h in self._rf.values())
        if not self._rf and self._aprop("PowerState") == "off-blocked":
            soft = True                         # rfkill 을 못 읽는다 — bluez 가 알려 준 대로
        changed = (soft, hard) != (self.blocked, self.hard_blocked)
        self.blocked, self.hard_blocked = soft, hard
        return changed

    def _rfkill_write(self, soft, idx=None):
        """idx 가 없으면 블루투스 전부. 쓸 수 없으면 False"""
        if self._rf_fd < 0:
            return False
        op = RFKILL_OP_CHANGE_ALL if idx is None else RFKILL_OP_CHANGE
        try:
            os.write(self._rf_fd, RFKILL_EVENT.pack(idx or 0, RFKILL_TYPE_BLUETOOTH, op, int(soft), 0))
            return True
        except OSError as e:
            dbg("[bluetooth] rfkill 쓰기 실패", e)
            return False

    def _rfkill_of(self, adapter):
        """어댑터(…/hci0) 자신의 rfkill 번호 — 노트북 무선 스위치 같은 다른 rfkill 은 건드리지 않게"""
        hci = (adapter or "").rsplit("/", 1)[-1]
        for idx in self._rf:
            if hci and _read(f"/sys/class/rfkill/rfkill{idx}/name") == hci:
                return idx
        return None

    # ── 호출 도우미 ─────────────────────────────────────────
    def _call(self, path, iface, method, params=None, done=None, timeout=CALL_MS):
        """bluez 메서드를 비동기로. done(ok, 한 줄, 오류 이름) 은 메인 루프에서 (곧바로 실패해도 나중에)"""
        if self._conn is None or not self.service:
            if done is not None:
                GLib.idle_add(lambda: (done(False, NO_SERVICE, ""), False)[1])
            return

        def fin(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as e:
                name, text = _err(e)
                dbg(f"[bluetooth] {iface}.{method} {path}: {e.message}")
                if done is not None:
                    done(False, text, name)
                return
            if done is not None:
                done(True, None, "")
        self._conn.call(BLUEZ, path, iface, method, params, None, Gio.DBusCallFlags.NONE, timeout, None, fin)

    def _set(self, path, iface, prop, value, done=None):
        self._call(path, PROPS, "Set", GLib.Variant("(ssv)", (iface, prop, value)), done)

    def _simple(self, path, iface, method, params, cb, ok_names=(), timeout=CALL_MS):
        """cb(ok, err) — ok_names 의 오류(이미 연결됨 등)는 성공으로 친다"""
        def done(ok, err, name):
            if not ok and name in ok_names:
                ok, err = True, None
            _call_cb(cb, ok, err)
        self._call(path, iface, method, params, done, timeout)

    def _need_adapter(self, cb):
        if self.adapter is None:
            GLib.idle_add(lambda: (_call_cb(cb, False, NO_SERVICE if not self.service else NO_ADAPTER),
                                   False)[1])
            return False
        return True

    # ── 켜고 끄기 ───────────────────────────────────────────
    def set_powered(self, on, cb=None):
        """cb(ok, err). 더 나중 요청에 밀린 요청은 cb(False, None)"""
        self._power_gen += 1
        gen = self._power_gen
        if not self.service:
            GLib.idle_add(lambda: (_call_cb(cb, False, NO_SERVICE), False)[1])
            return
        if on:
            if self.hard_blocked:
                GLib.idle_add(lambda: (_call_cb(cb, False, HARD_BLOCKED), False)[1])
                return
            # 막힘을 풀어도 bluez 가 알아채기까지 잠깐 걸린다 (그동안 켜기는 Blocked 로 실패) — 되풀이한다.
            #   노트북 무선 스위치를 풀면 USB 어댑터가 그제야 나타나기도 한다 — 그것도 기다린다
            unblocked = self.blocked and self._rfkill_write(False)
            retry = unblocked or self.adapter is None
            self._power_on(gen, cb, GLib.get_monotonic_time() + POWER_WAIT_US, retry)
            return
        adapter = self.adapter
        if adapter is None:
            GLib.idle_add(lambda: (_call_cb(cb, True, None), False)[1])
            return

        def off_done(ok, err, _name):
            if gen != self._power_gen:           # 그새 다시 켜라고 했다 — 막지 않는다
                _call_cb(cb, False, None)
                return
            # 막아 두어야 다시 시작해도 꺼진 채로 (systemd-rfkill 이 기억한다)
            idx = self._rfkill_of(adapter)
            blocked = idx is not None and self._rfkill_write(True, idx)
            _call_cb(cb, ok or blocked, None if ok or blocked else err)
        self._set(adapter, ADAPTER, "Powered", GLib.Variant("b", False), off_done)

    def _power_on(self, gen, cb, deadline, retry):
        if gen != self._power_gen:
            _call_cb(cb, False, None)
            return
        if self.adapter is None:
            if retry and self.service and GLib.get_monotonic_time() < deadline:
                GLib.timeout_add(400, lambda: (self._power_on(gen, cb, deadline, retry), False)[1])
            else:
                _call_cb(cb, False, NO_SERVICE if not self.service else NO_ADAPTER)
            return

        def done(ok, err, name):
            # rfkill 은 풀렸는데(다른 곳에서 풀었다) bluez 가 아직 모른다 — 그것도 기다린다
            lagging = name == "org.bluez.Error.Blocked" and not self.blocked
            if ok:
                _call_cb(cb, True, None)
            elif (retry or lagging) and gen == self._power_gen and GLib.get_monotonic_time() < deadline and \
                    name in ("org.bluez.Error.Blocked", "org.bluez.Error.Busy", "org.bluez.Error.NotReady",
                             "org.bluez.Error.InProgress", "org.bluez.Error.Failed"):
                GLib.timeout_add(400, lambda: (self._power_on(gen, cb, deadline, retry), False)[1])
            else:
                if name == "org.bluez.Error.Blocked" and not retry and self.blocked:
                    err = RFKILL_DENIED                 # 막혀 있는데 풀지 못했다 (/dev/rfkill 권한)
                _call_cb(cb, False, None if gen != self._power_gen else err)
        self._set(self.adapter, ADAPTER, "Powered", GLib.Variant("b", True), done)

    # ── 어댑터 ──────────────────────────────────────────────
    def set_adapter_name(self, name, cb=None):
        """다른 장치에 보이는 이름. 빈 문자열이면 기본 이름(컴퓨터 이름)으로"""
        if self._need_adapter(cb):
            self._set(self.adapter, ADAPTER, "Alias", GLib.Variant("s", name or ""),
                      lambda ok, err, _n: _call_cb(cb, ok, err))

    def set_discoverable(self, on, cb=None):
        """다른 장치가 이 PC 를 찾을 수 있게 — DiscoverableTimeout(보통 3분) 뒤 bluez 가 저절로 끈다"""
        if self._need_adapter(cb):
            self._set(self.adapter, ADAPTER, "Discoverable", GLib.Variant("b", bool(on)),
                      lambda ok, err, _n: _call_cb(cb, ok, err))

    def start_discovery(self, cb=None):
        """검색은 부른 프로그램(버스 연결)마다 따로 — 프로그램이 끝나면 bluez 가 알아서 멈춘다"""
        if self._need_adapter(cb):
            self._simple(self.adapter, ADAPTER, "StartDiscovery", None, cb,
                         ok_names=("org.bluez.Error.InProgress",))

    def stop_discovery(self, cb=None):
        # 우리가 시작하지 않았으면(또는 이미 멈췄으면) 오류가 온다 — 멈춘 것은 같으니 성공으로
        if self.adapter is None:
            GLib.idle_add(lambda: (_call_cb(cb, True, None), False)[1])
            return
        self._call(self.adapter, ADAPTER, "StopDiscovery", None,
                   lambda _ok, _err, _n: _call_cb(cb, True, None))

    # ── 장치 ────────────────────────────────────────────────
    def connect(self, path, cb=None):
        self._simple(path, DEVICE, "Connect", None, cb,
                     ok_names=("org.bluez.Error.AlreadyConnected",), timeout=CONNECT_MS)

    def disconnect(self, path, cb=None):
        self._simple(path, DEVICE, "Disconnect", None, cb, ok_names=("org.bluez.Error.NotConnected",))

    def pair(self, path, cb=None):
        """짝 맺기 — 암호 확인·입력은 등록된 에이전트(register_agent)로 온다"""
        self._simple(path, DEVICE, "Pair", None, cb,
                     ok_names=("org.bluez.Error.AlreadyExists",), timeout=PAIR_MS)

    def cancel_pairing(self, path, cb=None):
        self._simple(path, DEVICE, "CancelPairing", None, cb, ok_names=("org.bluez.Error.DoesNotExist",))

    def remove(self, path, cb=None):
        """짝 정보까지 지운다 (다시 쓰려면 짝을 새로 맺어야 한다)"""
        adapter = self._objs.get(path, {}).get(DEVICE, {}).get("Adapter") or self.adapter
        if adapter is None:
            self._need_adapter(cb)
            return
        self._simple(adapter, ADAPTER, "RemoveDevice", GLib.Variant("(o)", (path,)), cb,
                     ok_names=("org.bluez.Error.DoesNotExist",))

    def set_trusted(self, path, on, cb=None):
        """믿는 장치는 먼저 연결해 와도 묻지 않는다"""
        self._set(path, DEVICE, "Trusted", GLib.Variant("b", bool(on)),
                  lambda ok, err, _n: _call_cb(cb, ok, err))

    def set_alias(self, path, name, cb=None):
        """장치 이름 바꾸기. 빈 문자열이면 장치가 알려 준 이름으로"""
        self._set(path, DEVICE, "Alias", GLib.Variant("s", name or ""),
                  lambda ok, err, _n: _call_cb(cb, ok, err))

    # ── 짝 맺기 에이전트 ────────────────────────────────────
    def register_agent(self, ui):
        """ui 가 짝 맺기 질문에 답한다 (위 설명). 마지막에 등록한 ui 가 이긴다. bluetoothd 가 다시 뜨면 다시 등록"""
        if ui is not self._agent_ui:
            self._ask_drop()
        self._agent_ui = ui
        self._agent_register()

    def unregister_agent(self, ui=None):
        """ui 를 주면 그 ui 가 지금 것일 때만 (새 페이지가 먼저 등록한 뒤 옛 페이지가 없어지는 경우)"""
        if ui is not None and ui is not self._agent_ui:
            return
        self._ask_drop()
        self._agent_ui = None
        if self._agent_on:
            self._agent_on = False
            self._call("/org/bluez", AGENT_MANAGER, "UnregisterAgent", GLib.Variant("(o)", (AGENT_PATH,)))

    def _agent_register(self):
        if self._conn is None or not self.service or self._agent_on:
            return
        if not self._agent_obj:
            try:
                info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
                self._agent_obj = self._conn.register_object(AGENT_PATH, info, self._agent_call, None, None)
            except GLib.Error as e:
                dbg("[bluetooth] 에이전트 객체를 올리지 못했습니다:", e.message)
                return
        self._agent_on = True

        def registered(ok, err, name):
            if not ok and name != "org.bluez.Error.AlreadyExists":
                dbg("[bluetooth] 에이전트 등록 실패:", err)
                self._agent_on = False
                return
            if self._agent_ui is not None and self._agent_on:
                self._call("/org/bluez", AGENT_MANAGER, "RequestDefaultAgent",
                           GLib.Variant("(o)", (AGENT_PATH,)))
        self._call("/org/bluez", AGENT_MANAGER, "RegisterAgent",
                   GLib.Variant("(os)", (AGENT_PATH, AGENT_CAPABILITY)), registered)

    def _dev(self, path):
        return self.device(path) or {"path": path, "address": _addr_of(path), "name": _addr_of(path),
                                     "named": False, "icon": "bluetooth", "paired": False,
                                     "connected": False, "trusted": False, "blocked": False,
                                     "battery": None, "rssi": None}

    def _agent_call(self, _conn, sender, _path, _iface, method, params, inv):
        # 시스템 버스의 누구나 부를 수 있다 — bluetoothd 가 보낸 것만 받는다 (가짜 질문 창을 띄우지 못하게)
        if not self._owner or sender != self._owner:
            inv.return_dbus_error(REJECTED, "bluetoothd 가 보낸 요청이 아닙니다")
            return
        args = params.unpack() if params is not None else ()
        if method == "Release":
            self._agent_on = False
            self._ask_drop()
            inv.return_value(None)
        elif method == "Cancel":
            self._ask_drop()
            inv.return_value(None)
        elif method in ("DisplayPasskey", "DisplayPinCode"):
            # 보여 주기만 — 곧바로 답한다 (장치에서 입력이 끝나면 짝 맺기가 끝난다)
            ui = self._agent_ui
            code = f"{args[1]:06d}" if method == "DisplayPasskey" else str(args[1])
            entered = args[2] if method == "DisplayPasskey" else None
            fn = getattr(ui, "display", None)
            if fn is None:
                inv.return_dbus_error(REJECTED, "표시할 곳이 없습니다")
                return
            try:
                fn(self._dev(args[0]), code, entered)
            except Exception as e:
                dbg("[bluetooth] 에이전트 표시 실패", repr(e))
            inv.return_value(None)
        elif method == "RequestConfirmation":
            self._ask_ui(inv, "confirm", (self._dev(args[0]), f"{args[1]:06d}"), _yes_no)
        elif method == "RequestPinCode":
            self._ask_ui(inv, "ask_code", (self._dev(args[0]), False), _pin)
        elif method == "RequestPasskey":
            self._ask_ui(inv, "ask_code", (self._dev(args[0]), True), _passkey)
        elif method in ("RequestAuthorization", "AuthorizeService"):
            dev = self._dev(args[0])
            if dev["paired"]:                   # 이미 짝 맺은 장치는 묻지 않는다
                inv.return_value(None)
                return
            uuid = args[1] if method == "AuthorizeService" else None
            self._ask_ui(inv, "authorize", (dev, uuid), _yes_no)
        else:
            inv.return_dbus_error(REJECTED, method)

    def _ask_ui(self, inv, fn_name, args, conv):
        """ui.<fn_name>(*args, reply) 로 묻고, reply(답) 이 오면 conv(답) 으로 D-Bus 에 돌려준다"""
        self._ask_drop()                        # bluez 는 한 번에 하나만 묻는다 — 남은 게 있으면 거둔다
        ui = self._agent_ui
        fn = getattr(ui, fn_name, None)
        if fn is None:
            inv.return_dbus_error(REJECTED, "물어볼 곳이 없습니다")
            return
        self._ask_n += 1
        n = self._ask_n
        self._ask = (n, inv, ui)

        def reply(answer):
            if self._ask is None or self._ask[0] != n:
                return                          # 이미 거둬졌다 (Cancel·시간 초과·해제)
            self._ask = None
            try:
                out = conv(answer)
            except (TypeError, ValueError):
                out = REJECTED
            if isinstance(out, str):
                inv.return_dbus_error(out, "사용자가 거절했습니다" if out == REJECTED else "사용자가 취소했습니다")
            else:
                inv.return_value(out)
        try:
            fn(*args, reply)
        except Exception as e:
            dbg("[bluetooth] 에이전트 질문 실패", repr(e))
            if self._ask is not None and self._ask[0] == n:
                self._ask = None
                inv.return_dbus_error(REJECTED, str(e))

    def _ask_drop(self):
        """답을 기다리던 질문을 거둔다 — bluez 쪽엔 취소로 답하고, ui 의 대화상자를 닫게 한다"""
        ask, self._ask = self._ask, None
        if ask is not None:
            ask[1].return_dbus_error(CANCELED, "취소되었습니다")
        ui = ask[2] if ask is not None else self._agent_ui
        fn = getattr(ui, "cancel", None)
        if fn is not None:
            try:
                fn()
            except Exception as e:
                dbg("[bluetooth] 에이전트 취소 실패", repr(e))


# 에이전트 답 → D-Bus 반환값 (문자열이면 그 이름의 오류로 답한다)
def _yes_no(answer):
    return None if answer else REJECTED


def _pin(answer):
    if answer is None:
        return CANCELED
    s = str(answer).strip()
    return GLib.Variant("(s)", (s,)) if 1 <= len(s) <= 16 else REJECTED


def _passkey(answer):
    if answer is None:
        return CANCELED
    s = str(answer).strip()
    if not s.isdigit() or int(s) > 999999:
        return REJECTED
    return GLib.Variant("(u)", (int(s),))


_inst = None


def get():
    """프로세스 안에서 하나 (처음 부를 때 만든다 — 시스템 버스에 붙는 것은 비동기)"""
    global _inst
    if _inst is None:
        _inst = Bluetooth()
    return _inst
