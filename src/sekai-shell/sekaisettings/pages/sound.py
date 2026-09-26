"""소리 — 윈도우 11 의 설정 › 시스템 › 소리.

출력(소리를 재생할 장치)·입력(녹음할 장치) 고르기와 볼륨, 앱마다의 볼륨·출력 장치(볼륨 믹서),
사운드 카드 프로필(아날로그 스테레오·HDMI·끄기 …). 예전엔 이걸 pavucontrol 에 맡겼다.

소리 서버(PipeWire 의 pipewire-pulse)와는 pactl 로 말한다:
  읽기  pactl -f json info / list — 스레드에서 (서버가 바쁘면 몇 초씩 걸린다 — 창이 멈추지 않게)
  바뀜  pactl subscribe — 페이지가 보이는 동안만 띄워 두고, 죽으면 조금 기다렸다 다시 띄운다
  쓰기  pactl set-… — Gio.Subprocess 로. 슬라이더는 마지막 값만, 한 번에 하나, 초당 20번까지
입력 테스트(레벨 막대)는 '테스트 시작'을 누른 동안만 parec 으로 조금씩 받아 본다 (pactl 과 같은 패키지).
"""
import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..util import dbg, run_async
from ..widgets import Page, button, icon_image, info, row

NORM = 65536                # PA_VOLUME_NORM — 100%
SEND_GAP = 0.05             # 슬라이더 값은 초당 20번까지만 보낸다
HOLD_SECS = 0.8             # 사람이 만진 컨트롤은 마지막으로 보낸 뒤 이만큼은 들어오는 값으로 덮지 않는다
REFRESH_MS = 150            # 바뀜 알림이 몰려와도 이 간격에 한 번만 다시 읽는다
WANT_SECS = 3               # 고른 장치·포트·프로필을 서버가 따라올 때까지 화면에 붙들어 두는 시간
RETRY_SECS = (1, 2, 5, 10, 30)
METER_NAME = "sekai-settings-meter"      # 입력 테스트 스트림 — 녹음 중인 앱 목록에서 뺀다
METER_RATE = 8000
FACILITIES = {"sink", "source", "sink-input", "source-output", "card", "server"}
EVENT_RE = re.compile(r"Event '([\w-]+)' on ([\w-]+)")

SPK_HIGH = ["audio-volume-high-symbolic", "audio-volume-high"]
SPK_MED = ["audio-volume-medium-symbolic", "audio-volume-medium", "audio-volume-high-symbolic"]
SPK_LOW = ["audio-volume-low-symbolic", "audio-volume-low", "audio-volume-high-symbolic"]
SPK_MUTED = ["audio-volume-muted-symbolic", "audio-volume-muted"]
MIC_ON = ["audio-input-microphone-symbolic", "audio-input-microphone"]
MIC_MUTED = ["microphone-sensitivity-muted-symbolic", "audio-input-microphone-muted-symbolic",
             "microphone-disabled-symbolic", "audio-input-microphone-muted"]
OUT_ICONS = ["audio-speakers", "audio-speakers-symbolic", "audio-card"]
IN_ICONS = ["audio-input-microphone", "audio-input-microphone-symbolic", "audio-card"]
APP_ICONS = ["audio-x-generic", "applications-multimedia", "audio-volume-high"]
CARD_ICONS = ["audio-card", "audio-card-symbolic", "audio-speakers"]

TEXT_MISSING = "소리 설정에 필요한 구성 요소가 설치되어 있지 않습니다. SekaiOS 업데이트를 받아 주세요."
TEXT_DOWN = ("오디오 서비스가 실행 중이 아닙니다. 소리 서버(PipeWire)에 연결하지 못했습니다 — "
             "다시 켜지면 저절로 다시 연결합니다.")
TEXT_LOADING = "소리 장치를 읽는 중…"

_SETPRIV = shutil.which("setpriv") is not None


# ── 사람이 읽을 이름 ──────────────────────────────────────────
# 소리 서버가 주는 설명은 영어일 때가 많다 (PipeWire 가 C 로캘로 돌면). 흔한 말만 옮긴다 —
#   제품 이름(Realtek, 모니터 이름 …)은 그대로 둔다
_KO_WORDS = {
    "Built-in Audio": "내장 오디오", "Dummy Output": "가상 출력",
    "High Definition Audio Controller": "HD 오디오 컨트롤러", "HD Audio Controller": "HD 오디오 컨트롤러",
    "Audio Controller": "오디오 컨트롤러", "USB Audio": "USB 오디오",
    "Analog Stereo Duplex": "아날로그 스테레오 입출력", "Analog Mono Duplex": "아날로그 모노 입출력",
    "Digital Stereo Duplex": "디지털 스테레오 입출력", "Multichannel Duplex": "멀티채널 입출력",
    "Stereo Duplex": "스테레오 입출력",
    "Analog Surround": "아날로그 서라운드", "Digital Surround": "디지털 서라운드",
    "Analog Stereo": "아날로그 스테레오", "Analog Mono": "아날로그 모노",
    "Digital Stereo": "디지털 스테레오", "Multichannel": "멀티채널", "Pro Audio": "프로 오디오",
    "Analog Output": "아날로그 출력", "Analog Input": "아날로그 입력",
    "Digital Output": "디지털 출력", "Digital Input": "디지털 입력",
    "Line Out": "라인 출력", "Line In": "라인 입력",
    "Internal Microphone": "내장 마이크", "Front Microphone": "앞면 마이크", "Rear Microphone": "뒷면 마이크",
    "Headset Microphone": "헤드셋 마이크", "Headphone Microphone": "헤드폰 마이크",
    "Dock Microphone": "도크 마이크", "Digital Microphone": "디지털 마이크", "Microphone": "마이크",
    "Headphones": "헤드폰", "Headphone": "헤드폰", "Speakers": "스피커", "Speaker": "스피커",
    "Speakerphone": "스피커폰", "Headset": "헤드셋", "Handsfree": "핸즈프리", "Hands-Free": "핸즈프리",
    "Output": "출력", "Input": "입력", "Stereo": "스테레오", "Mono": "모노", "Surround": "서라운드",
    "Chat": "채팅", "Game": "게임", "Audio": "오디오", "(Left)": "(왼쪽)", "(Right)": "(오른쪽)",
    "(IEC958)": "(S/PDIF)", "IEC958/AC3": "S/PDIF, AC3", "IEC958/DTS": "S/PDIF, DTS", "Unknown": "알 수 없음",
}
_KO_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in sorted(_KO_WORDS, key=len, reverse=True))
                    + r")(?![\w-])")


def _ko(text):
    t = " ".join(str(text or "").split())
    m = re.match(r"^Monitor of (.+)$", t)
    if m:
        return f"{_ko(m.group(1))} 모니터"
    if t == "Off":
        return "끄기"
    t = _KO_RE.sub(lambda m: _KO_WORDS[m.group(1)], t)
    t = re.sub(r"\bMic ?(\d)\b", r"마이크 \1", t)            # UCM: Mic1, Mic2
    return re.sub(r"\bHDMI(\d)\b", r"HDMI \1", t)


# 프로필 이름(output:analog-stereo+input:analog-stereo 등) → 한국어. 모르는 것은 서버의 설명을 옮겨 쓴다
_SURROUND = {"21": "2.1", "30": "3.0", "31": "3.1", "40": "4.0", "41": "4.1", "50": "5.0", "51": "5.1",
             "60": "6.0", "61": "6.1", "70": "7.0", "71": "7.1"}
_CODECS = {"sbc": "SBC", "sbc_xq": "SBC-XQ", "aac": "AAC", "aptx": "aptX", "aptx_hd": "aptX HD",
           "aptx_ll": "aptX LL", "aptx_ll_duplex": "aptX LL", "ldac": "LDAC", "faststream": "FastStream",
           "faststream_duplex": "FastStream", "lc3": "LC3", "lc3plus_h3": "LC3plus", "opus_05": "Opus",
           "opus_g": "Opus", "msbc": "mSBC", "cvsd": "CVSD", "lc3_swb": "LC3-SWB"}


def _mapping_label(m):
    """ALSA 매핑 이름 하나 (analog-stereo, hdmi-stereo-extra1 …) → 한국어. 모르면 None"""
    m = re.sub(r"-(output|input)$", "", m)
    fixed = {"analog-mono": "아날로그 모노", "analog-stereo": "아날로그 스테레오",
             "iec958-stereo": "디지털 스테레오 (S/PDIF)", "multichannel": "멀티채널",
             "mono-fallback": "모노", "stereo-fallback": "스테레오", "mono": "모노", "stereo": "스테레오",
             "analog-chat": "아날로그 채팅", "iec958-ac3-surround-40": "디지털 서라운드 4.0 (S/PDIF, AC3)",
             "iec958-ac3-surround-51": "디지털 서라운드 5.1 (S/PDIF, AC3)",
             "iec958-dts-surround-51": "디지털 서라운드 5.1 (S/PDIF, DTS)"}
    if m in fixed:
        return fixed[m]
    s = re.match(r"^analog-surround-(\d\d)$", m)
    if s and s.group(1) in _SURROUND:
        return f"아날로그 서라운드 {_SURROUND[s.group(1)]}"
    h = re.match(r"^hdmi-(stereo|surround|surround71|dts-surround)(?:-extra(\d+))?$", m)
    if h:
        kind = {"stereo": "스테레오", "surround": "서라운드 5.1", "surround71": "서라운드 7.1",
                "dts-surround": "서라운드 5.1 (DTS)"}[h.group(1)]
        n = f" {int(h.group(2)) + 1}" if h.group(2) else ""
        return f"HDMI{n} {kind}"
    return None


def _profile_label(name, desc):
    name = str(name or "")
    if name == "off":
        return "끄기"
    if name == "pro-audio":
        return "프로 오디오"
    bt = re.match(r"^(a2dp[-_]sink|a2dp[-_]duplex|headset[-_]head[-_]unit|handsfree[-_]head[-_]unit|"
                  r"bap[-_]sink|bap[-_]duplex)(?:[-_](.+))?$", name)
    if bt:
        what = {"a2dp-sink": "고음질 재생 (A2DP)", "a2dp-duplex": "고음질 재생 + 마이크 (A2DP)",
                "headset-head-unit": "헤드셋 — 마이크 사용 (HSP/HFP)",
                "handsfree-head-unit": "핸즈프리 — 마이크 사용 (HFP)",
                "bap-sink": "LE 오디오 재생", "bap-duplex": "LE 오디오 재생 + 마이크"}[bt.group(1).replace("_", "-")]
        if bt.group(2):
            codec = _CODECS.get(bt.group(2), bt.group(2).upper())
            what = what[:-1] + f", {codec})" if what.endswith(")") else f"{what} ({codec})"
        return what
    ucm = re.match(r"^(HiFi|Voice Call|VoiceCall|Voice)\s*(?:\((.*)\))?$", name)
    if ucm:
        what = "고음질" if ucm.group(1) == "HiFi" else "통화"
        return f"{what} ({_ko(ucm.group(2))})" if ucm.group(2) else what
    outs, ins = [], []
    for part in name.split("+"):
        kind, _, m = part.partition(":")
        label = _mapping_label(m) if kind in ("output", "input") else None
        if label is None:
            return _ko(desc) or name
        (outs if kind == "output" else ins).append(label)
    if len(outs) == 1 and len(ins) == 1 and outs[0] == ins[0]:
        return f"{outs[0]} 출력 + 입력"
    return " + ".join([f"{o} 출력" for o in outs] + [f"{i} 입력" for i in ins])


_PORTS = [
    (r"^analog-output-speaker", "스피커"), (r"^analog-output-headphones", "헤드폰"),
    (r"^analog-output-lineout", "라인 출력"), (r"^analog-output-mono", "아날로그 모노 출력"),
    (r"^analog-output$", "아날로그 출력"), (r"^iec958-stereo-output", "디지털 출력 (S/PDIF)"),
    (r"^analog-input-internal-mic", "내장 마이크"), (r"^analog-input-dock-mic", "도크 마이크"),
    (r"^analog-input-front-mic", "앞면 마이크"), (r"^analog-input-rear-mic", "뒷면 마이크"),
    (r"^analog-input-headset-mic", "헤드셋 마이크"), (r"^analog-input-headphone-mic", "헤드폰 마이크"),
    (r"^analog-input-mic", "마이크"), (r"^analog-input-linein", "라인 입력"),
    (r"^analog-input-aux", "보조 입력"), (r"^analog-input$", "아날로그 입력"),
    (r"^iec958-stereo-input", "디지털 입력 (S/PDIF)"),
    (r"^headset-output", "헤드셋"), (r"^headset-input", "헤드셋 마이크"),
    (r"^headphone-output", "헤드폰"), (r"^speaker-output", "스피커"),
    (r"^handsfree-output", "핸즈프리"), (r"^handsfree-input", "핸즈프리 마이크"),
]


def _port_label(name, desc):
    name = str(name or "")
    h = re.match(r"^hdmi-output-(\d+)$", name)
    if h:
        n = int(h.group(1))
        return "HDMI / DisplayPort" + (f" {n + 1}" if n else "")
    for pat, label in _PORTS:
        if re.match(pat, name):
            return label
    return _ko(desc) or name


def _fmt(spec):
    """'float32le 2ch 48000Hz' → '48 kHz · 32비트 부동소수점 · 스테레오'"""
    m = re.match(r"^(\S+)\s+(\d+)ch\s+(\d+)Hz", str(spec or ""))
    if not m:
        return ""
    f, ch, rate = m.group(1), int(m.group(2)), int(m.group(3))
    bits = {"u8": "8비트", "s16le": "16비트", "s16be": "16비트", "s24le": "24비트", "s24be": "24비트",
            "s24-32le": "24비트", "s24-32be": "24비트", "s32le": "32비트", "s32be": "32비트",
            "float32le": "32비트 부동소수점", "float32be": "32비트 부동소수점"}.get(f, f)
    k = rate / 1000
    khz = f"{k:g} kHz"
    chans = {1: "모노", 2: "스테레오", 6: "5.1 채널", 8: "7.1 채널"}.get(ch, f"{ch}채널")
    return f"{khz} · {bits} · {chans}"


# ── pactl 로 읽기 (스레드에서) ─────────────────────────────────
def _pactl(args, timeout=5):
    """(종료 코드, 출력). pactl 이 없으면 None"""
    env = dict(os.environ, LC_ALL="C.UTF-8")    # 소수점이 쉼표인 로캘에선 JSON 이 깨진다
    env.pop("LANGUAGE", None)
    try:
        p = subprocess.run(["pactl"] + args, capture_output=True, text=True, timeout=timeout, env=env,
                           errors="replace")
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, OSError) as e:
        dbg("pactl", args, e)
        return -1, ""
    return p.returncode, p.stdout


def _loads(text):
    try:
        return json.loads(text, strict=False)       # 설명 안의 제어 문자 정도는 봐준다
    except ValueError:
        return None


def _named(items):
    """이름 → 정보 사전이나 [{name: …}] 목록 → [(이름, 정보)] (pactl 판마다 모양이 다르다)"""
    if isinstance(items, dict):
        return [(str(k), v if isinstance(v, dict) else {}) for k, v in items.items()]
    if isinstance(items, list):
        return [(str(x.get("name")), x) for x in items if isinstance(x, dict) and x.get("name") is not None]
    return []


def _props(d):
    p = d.get("properties")
    return p if isinstance(p, dict) else {}


def _chans(d):
    """볼륨 → [(채널 이름, 값)] — 채널 순서는 channel_map 을 따른다 (여러 값으로 볼륨을 줄 때 그 순서다)"""
    v = d.get("volume")
    if not isinstance(v, dict):
        return []
    cmap = [c for c in str(d.get("channel_map") or "").split(",") if c]
    names = cmap if cmap and len(cmap) == len(v) and all(c in v for c in cmap) else list(v)
    out = []
    for c in names:
        x = v.get(c)
        val = x.get("value") if isinstance(x, dict) else x
        try:
            out.append((c, max(0, int(val))))
        except (TypeError, ValueError):
            return []
    return out


def _is_monitor(d):
    mon = d.get("monitor_of_sink")
    return (_props(d).get("device.class") == "monitor" or str(d.get("name", "")).endswith(".monitor")
            or (mon not in (None, "", "n/a") and not isinstance(mon, bool)))


def _unavailable(info_):
    return info_.get("availability") == "not available" or info_.get("available") is False


def _dev_icons(d, kind, port):
    p = _props(d)
    icons = []
    port = (port or "").lower()
    ff = str(p.get("device.form_factor", ""))
    if "hdmi" in port or "displayport" in port:
        icons += ["video-display", "video-display-symbolic"]
    if "headset" in port or ff in ("headset", "hands-free", "handset"):
        icons += ["audio-headset", "audio-headphones"]
    if "headphone" in port or ff == "headphone":
        icons += ["audio-headphones", "audio-headset"]
    if kind == "source" and ("mic" in port or ff in ("microphone", "webcam")):
        icons += ["audio-input-microphone"]
    name = str(p.get("device.icon_name") or "")
    if name:
        icons += [name, re.sub(r"-(pci|usb|bluetooth|analog|firewire)$", "", name)]
    return list(dict.fromkeys(icons + (OUT_ICONS if kind == "sink" else IN_ICONS)))


def _device(d, kind):
    p = _props(d)
    ports = []
    for pname, pi in _named(d.get("ports")):
        ports.append((pname, _port_label(pname, pi.get("description")), _unavailable(pi)))
    active = d.get("active_port") or ""
    port = next((x for x in ports if x[0] == active), None)
    sub = []
    if port is not None and len(ports) >= 1:
        sub.append(port[1])
    bus = str(p.get("device.bus", ""))
    if bus == "bluetooth":
        sub.append("블루투스")
    elif bus == "usb":
        sub.append("USB")
    desc = _ko(d.get("description") or p.get("device.description") or d.get("name"))
    if sub and sub[0] == desc:
        sub = sub[1:]
    return {"index": d.get("index"), "name": str(d.get("name", "")), "desc": desc or "이름 없는 장치",
            "sub": " · ".join(sub) or None, "icons": _dev_icons(d, kind, active),
            "chans": _chans(d), "mute": bool(d.get("mute")), "port": active,
            "ports": [(n, lab + (" (연결 안 됨)" if bad else "")) for n, lab, bad in ports
                      if not bad or n == active] if ports else [],
            "fmt": _fmt(d.get("sample_specification"))}


# 앱 이름·아이콘 — .desktop 항목의 (한국어) 이름을 쓴다. 한 번 찾은 것은 기억해 둔다
_apps = {}
_exec_map = None
_apps_lock = threading.Lock()
_GENERIC_MEDIA = {"playback", "audio stream", "audiostream", "audio-stream", "output", "record", "recording",
                  "capture", "simple dmedia layer", "audio", "sound", "pcm", "stream", "default", "(null)"}


def _desktop(cand):
    if not cand:
        return None
    cand = str(cand)
    try:
        return Gio.DesktopAppInfo.new(cand if cand.endswith(".desktop") else cand + ".desktop")
    except TypeError:
        return None


def _by_exec(binary):
    global _exec_map
    if _exec_map is None:
        m = {}
        for a in Gio.AppInfo.get_all():
            ex = os.path.basename(a.get_executable() or "")
            if ex and ex not in m and not a.get_nodisplay():
                m[ex] = a
        _exec_map = m
    return _exec_map.get(binary)


def _gicon_names(gicon):
    if gicon is None:
        return []
    if isinstance(gicon, Gio.ThemedIcon):
        return list(gicon.get_names())
    if isinstance(gicon, Gio.FileIcon):
        path = gicon.get_file().get_path()
        return [path] if path else []
    return []


def _app(p):
    """스트림 속성 → (앱 이름, 아이콘 후보)"""
    name = str(p.get("application.name") or "")
    binary = str(p.get("application.process.binary") or "")
    alsa = re.match(r"^ALSA plug-in \[(.+)\]$", name)
    if alsa:
        binary = binary or alsa.group(1)
        name = alsa.group(1)
    key = (str(p.get("application.id") or ""), str(p.get("pipewire.access.portal.app_id") or ""), binary, name,
           str(p.get("application.icon_name") or ""))
    with _apps_lock:
        got = _apps.get(key)
        if got is not None:
            return got
        app = None
        for cand in (key[0], key[1], binary, key[4], name.lower(), name.lower().replace(" ", "-")):
            app = _desktop(cand)
            if app is not None:
                break
        if app is None and binary:
            app = _by_exec(binary)
        label = app.get_name() if app is not None else (name or binary or "알 수 없는 앱")
        icons = [key[4]] if key[4] else []
        if app is not None:
            icons += _gicon_names(app.get_icon())
        icons += [c for c in (binary, name.lower()) if c] + APP_ICONS
        got = _apps[key] = (label, list(dict.fromkeys(icons)))
        return got


def _hidden_stream(p):
    return (p.get("application.name") == METER_NAME
            or p.get("media.name") == "Peak detect"     # 다른 볼륨 도구의 레벨 막대 스트림
            or "node.link-group" in p)                   # PipeWire 안쪽의 이어 붙이기(loopback·필터)


def _stream(d, kind, devs):
    p = _props(d)
    label, icons = _app(p)
    media = " ".join(str(p.get("media.name") or "").split())
    sub = media if media and media.lower() not in _GENERIC_MEDIA and media.lower() != label.lower() else ""
    dev = d.get("sink" if kind == "sink-input" else "source")
    return {"index": d.get("index"), "app": label, "icons": icons, "sub": sub,
            "chans": _chans(d), "mute": bool(d.get("mute")),
            "dev": next((x["name"] for x in devs if x["index"] == dev), None)}


def _card(d):
    p = _props(d)
    active = str(d.get("active_profile") or "")
    profiles = []
    for pname, pi in _named(d.get("profiles")):
        bad = _unavailable(pi)
        if bad and pname != active:
            continue
        profiles.append((pname, _profile_label(pname, pi.get("description")) + (" (연결 안 됨)" if bad else "")))
    profiles.sort(key=lambda x: x[0] == "off")      # '끄기'는 맨 아래로 (나머지는 서버가 준 순서)
    name = str(p.get("device.icon_name") or "")
    return {"name": str(d.get("name", "")), "desc": _ko(p.get("device.description") or d.get("name")),
            "icons": ([name] if name else []) + CARD_ICONS, "profiles": profiles, "active": active}


def fetch(allow_partial=False):
    """소리 서버의 지금 상태 → 화면에 그릴 모양. {"state": "ok" | "down" | "missing", …}"""
    r = _pactl(["-f", "json", "info"])
    if r is None:
        return {"state": "missing"}
    code, out = r
    inf = _loads(out) if code == 0 else None
    if not isinstance(inf, dict):
        return {"state": "down"}
    code, out = _pactl(["-f", "json", "list"]) or (-1, "")
    everything = _loads(out) if code == 0 else None
    if not isinstance(everything, dict):
        everything = {}
    raw = {}
    failed = 0
    for key, kind in (("sinks", "sinks"), ("sources", "sources"), ("sink_inputs", "sink-inputs"),
                      ("source_outputs", "source-outputs"), ("cards", "cards")):
        items = everything.get(key)
        if not isinstance(items, list):          # 한 번에 읽은 것이 깨졌다 — 종류마다 따로
            code, out = _pactl(["-f", "json", "list", kind]) or (-1, "")
            items = _loads(out) if code == 0 else None
            if not isinstance(items, list):
                failed += 1
        raw[key] = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    if failed and not allow_partial:
        # 서버는 살아 있는데 목록을 읽지 못했다 (블루투스 프로필을 바꾸는 중처럼 잠깐 굼뜰 때) — "장치 0개"로
        #   그리면 고른 장치가 풀리고 입력 테스트·믹서가 비었다. 지금 화면을 두고 조금 뒤에 다시 읽는다
        return {"state": "busy"}
    dummy = ("auto_null", "auto_null.monitor")     # 장치가 하나도 없을 때 서버가 세우는 가짜 출력
    sinks = [_device(d, "sink") for d in raw["sinks"] if d.get("name") not in dummy]
    sources = [_device(d, "source") for d in raw["sources"]
               if d.get("name") not in dummy and not _is_monitor(d)]
    all_sources = sources + [{"index": d.get("index"), "name": str(d.get("name", ""))}
                             for d in raw["sources"] if _is_monitor(d)]
    playing = [_stream(d, "sink-input", sinks) for d in raw["sink_inputs"] if not _hidden_stream(_props(d))]
    recording = [_stream(d, "source-output", all_sources) for d in raw["source_outputs"]
                 if not _hidden_stream(_props(d))]
    cards = [c for c in (_card(d) for d in raw["cards"]) if len(c["profiles"]) >= 2]   # 고를 것이 없으면 빼 둔다
    return {"state": "ok", "default_sink": str(inf.get("default_sink_name") or ""),
            "default_source": str(inf.get("default_source_name") or ""),
            "sinks": sinks, "sources": sources, "playing": sorted(playing, key=lambda s: s["index"] or 0),
            "recording": sorted(recording, key=lambda s: s["index"] or 0), "cards": cards}


# ── pactl 로 쓰기 ────────────────────────────────────────────
def _launcher(flags):
    l = Gio.SubprocessLauncher.new(flags)
    l.setenv("LC_ALL", "C.UTF-8", True)            # 이벤트 문구·숫자가 로캘을 타지 않게
    l.unsetenv("LANGUAGE")
    return l


def _deathsig(argv):
    """설정 창이 갑자기 죽어도 따라 끝나게 (오래 도는 subscribe·parec)"""
    return ["setpriv", "--pdeathsig", "TERM"] + argv if _SETPRIV else argv


def pactl_async(args, done=None, timeout=5):
    """pactl 을 기다리지 않고 — 끝나면 done(성공). 멈춘 소리 서버에 걸려 안 끝나면 timeout 초 뒤 끊는다"""
    try:
        proc = _launcher(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE).spawnv(
            ["pactl"] + [str(a) for a in args])
    except GLib.Error as e:
        dbg("pactl 실행 실패", args, e.message)
        if done:
            GLib.idle_add(lambda: (done(False), False)[1])
        return
    st = {"src": 0}

    def kill():
        st["src"] = 0
        proc.force_exit()
        return False
    st["src"] = GLib.timeout_add_seconds(timeout, kill)

    def finished(p, res):
        if st["src"]:
            GLib.source_remove(st["src"])
            st["src"] = 0
        try:
            _ok, _out, err = p.communicate_utf8_finish(res)
        except GLib.Error as e:
            err = e.message
        ok = p.get_if_exited() and p.get_exit_status() == 0
        if not ok:
            dbg("pactl 실패", args, (err or "").strip())
        if done:
            done(ok)
    proc.communicate_utf8_async(None, None, finished)


class _Sender:
    """값 보내기 — 마지막 값만, 한 번에 하나, 초당 20번까지.
    끄는 동안 값마다 띄우면 pactl 이 쌓이고, 늦게 끝난 옛 값이 이겨 슬라이더와 실제 값이 어긋난다"""

    def __init__(self):
        self.want = None
        self.busy = False
        self.src = 0
        self.last = 0.0
        self.done_at = 0.0

    def send(self, args):
        self.want = args
        self._kick()

    def _kick(self):
        if self.busy or self.src or self.want is None:
            return
        wait = SEND_GAP - (time.monotonic() - self.last)
        if wait > 0:
            self.src = GLib.timeout_add(max(1, int(wait * 1000)), self._fire)
        else:
            self._fire()

    def _fire(self):
        self.src = 0
        args, self.want = self.want, None
        if args is None:
            return False
        self.busy = True
        self.last = time.monotonic()
        pactl_async(args, self._done)
        return False

    def _done(self, _ok):
        self.busy = False
        self.done_at = time.monotonic()
        self._kick()

    @property
    def active(self):
        """보내는 중이거나 막 보냈다 — 들어오는 (옛) 값으로 컨트롤을 되돌리지 않는다"""
        return (self.busy or bool(self.src) or self.want is not None
                or time.monotonic() - self.done_at < HOLD_SECS)

    def flush(self):
        """없어지는 컨트롤 — 기다리던 마지막 값은 바로 보낸다 (창을 닫아도 끈 자리에 남게)"""
        if self.src:
            GLib.source_remove(self.src)
            self.src = 0
            if not self.busy:
                self._fire()


# ── 화면 조각 ────────────────────────────────────────────────
def _reveal(w, on):
    """no_show_all 인 위젯 보이기/숨기기 — show_all 이 안쪽으로 내려가지 않으니 안쪽은 따로
    (안쪽에서 따로 no_show_all 을 켠 위젯은 그대로 숨어 있다)"""
    if on and not w.get_visible():
        w.show()
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                c.show_all()
    elif not on and w.get_visible():
        w.hide()


def _clear(lb):
    for c in lb.get_children():
        lb.remove(c)
        c.destroy()


def _combo(tip=None, chars=26):
    """긴 장치 이름이 창을 넓히지 않게 줄여 보이는 콤보"""
    c = Gtk.ComboBoxText()
    for cell in c.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        cell.set_property("max-width-chars", chars)
    if tip:
        c.set_tooltip_text(tip)
    return c


def _fill(c, items):
    c.remove_all()
    for key, label in items:
        c.append(key, label)


class _Volume(Gtk.Box):
    """[음소거 단추] [────●──── 50] — 장치나 앱 스트림 하나의 볼륨.
    채널마다 비율을 기억해 두고 볼륨만 바꾼다 (한 값으로 보내면 좌우 밸런스가 풀린다)"""

    def __init__(self, page, kind, width=220):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.page, self.kind = page, kind          # kind: sink · source · sink-input · source-output
        self.mic = kind in ("source", "source-output")
        self.target = None                         # pactl 에 줄 장치 이름이나 스트림 번호
        self.names, self.ratio = [], []
        self.pct, self.muted = 0, False
        self.quiet = False
        self.vol_send, self.mute_send = _Sender(), _Sender()
        self._icons = None

        self.btn = Gtk.Button()
        self.btn.set_always_show_image(True)
        self.btn.set_valign(Gtk.Align.CENTER)
        self.btn.connect("clicked", self._on_mute)
        self.pack_start(self.btn, False, False, 0)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_digits(0)
        self.scale.set_size_request(width, -1)
        self.scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale.connect("value-changed", self._on_scale)
        page.hold_while_grabbed(self.scale)
        self.pack_start(self.scale, True, True, 0)
        self.connect("destroy", lambda *_: (self.vol_send.flush(), self.mute_send.flush()))
        self._icon()

    @property
    def grabbed(self):
        return self.scale in self.page.grabbed

    def update(self, target, chans, muted):
        """서버의 값으로 맞춘다 — 사람이 만지는 중이면 그 값은 그대로 둔다"""
        other = target != self.target
        self.target = target
        if other or not (self.vol_send.active or self.grabbed):
            names = [c for c, _ in chans]
            vals = [v for _, v in chans]
            top = max(vals) if vals else 0
            if top > 0:
                self.ratio = [v / top for v in vals]
            elif other or len(self.ratio) != len(names):
                self.ratio = [1.0] * len(names)
            self.names = names
            self.pct = int(round(top * 100 / NORM))
            self.quiet = True
            self.scale.set_range(0, max(100, self.pct))     # 다른 곳에서 100% 넘게 올려 둔 값도 그대로 보인다
            self.scale.set_value(self.pct)
            self.quiet = False
        if other or not self.mute_send.active:
            self.muted = muted
        self.scale.set_sensitive(bool(chans))
        self._icon()

    def _icon(self):
        if self.mic:
            icons = MIC_MUTED if self.muted else MIC_ON
        elif self.muted or self.pct == 0:
            icons = SPK_MUTED
        else:
            icons = SPK_HIGH if self.pct > 66 else SPK_MED if self.pct > 33 else SPK_LOW
        if icons is not self._icons:
            self._icons = icons
            self.btn.set_image(icon_image(icons, 16))
        self.btn.set_tooltip_text("음소거 해제" if self.muted else "음소거")

    def _on_mute(self, *_):
        if self.target is None:
            return
        self.muted = not self.muted
        self._icon()
        self.mute_send.send([f"set-{self.kind}-mute", self.target, "1" if self.muted else "0"])

    def _on_scale(self, sc):
        if self.quiet or self.target is None:
            return
        self.pct = int(round(sc.get_value()))
        if self.muted:                         # 음소거 중에 볼륨을 움직이면 소리를 켠다 (윈도우처럼)
            self._on_mute()
        self._icon()
        self.send()

    def send(self):
        top = self.pct * NORM / 100
        vals = [str(int(round(r * top))) for r in self.ratio] or [str(int(round(top)))]
        self.vol_send.send([f"set-{self.kind}-volume", self.target] + vals)

    # 좌우 밸런스 (-1 왼쪽 … 1 오른쪽) — PulseAudio 의 pa_cvolume_get/set_balance 와 같은 셈
    @property
    def has_lr(self):
        return any("left" in c for c in self.names) and any("right" in c for c in self.names)

    def balance(self):
        left = max((r for c, r in zip(self.names, self.ratio) if "left" in c), default=0)
        right = max((r for c, r in zip(self.names, self.ratio) if "right" in c), default=0)
        if left == right:
            return 0.0
        return -1 + right / left if left > right else 1 - left / right

    def set_balance(self, b):
        for i, c in enumerate(self.names):
            if "left" in c:
                self.ratio[i] = 1 - b if b > 0 else 1.0
            elif "right" in c:
                self.ratio[i] = 1 + b if b < 0 else 1.0
        if self.target is not None:
            self.send()


class _Meter:
    """입력 테스트 — parec 으로 기본 입력 장치의 소리를 조금씩 받아 가장 큰 값을 막대로.
    8 kHz 모노 16비트(초당 16 KB)라 가볍다. 켜 둔 동안만 녹음 스트림이 생긴다 (윈도우의 '테스트 시작'처럼)"""
    CHUNK = 800                                  # 바이트 — 0.05 초

    def __init__(self, on_level, on_end):
        self.on_level, self.on_end = on_level, on_end
        self.proc = self.cancel = None
        self.device = None
        self.carry = b""

    @property
    def running(self):
        return self.proc is not None

    def start(self, device):
        self.stop()
        argv = _deathsig(["parec", f"--device={device}", "--raw", "--format=s16le", "--channels=1",
                          f"--rate={METER_RATE}", "--latency-msec=50", f"--client-name={METER_NAME}",
                          "--stream-name=입력 테스트"])
        try:
            proc = _launcher(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE).spawnv(argv)
        except GLib.Error as e:
            dbg("parec 실행 실패", e.message)
            return False
        self.proc, self.device, self.carry = proc, device, b""
        self.cancel = Gio.Cancellable()
        self._read(proc, proc.get_stdout_pipe(), self.cancel)
        return True

    def _read(self, proc, stream, cancel):
        stream.read_bytes_async(self.CHUNK, GLib.PRIORITY_DEFAULT, cancel, self._got, (proc, cancel))

    def _got(self, stream, res, ctx):
        proc, cancel = ctx
        try:
            b = stream.read_bytes_finish(res)
            data = b.get_data() if b is not None else b""
        except GLib.Error:
            data = b""
        if cancel.is_cancelled() or proc is not self.proc:
            return
        if not data:                              # 끝났다 — 장치가 사라졌거나 서버가 멈췄다
            self.stop()
            self.on_end()
            return
        data = self.carry + bytes(data)
        cut = len(data) // 2 * 2
        self.carry = data[cut:]
        a = array.array("h")
        a.frombytes(data[:cut])
        if sys.byteorder == "big":
            a.byteswap()
        if a:
            self.on_level(max(max(a), -min(a)) / 32768.0)
        self._read(proc, stream, cancel)

    def stop(self):
        if self.cancel is not None:
            self.cancel.cancel()
        if self.proc is not None:
            self.proc.force_exit()
        self.proc = self.cancel = self.device = None


class _Devices:
    """출력 또는 입력 — 장치 고르기(라디오), 기본 장치의 볼륨·속성, (입력이면) 입력 테스트"""

    def __init__(self, page, kind):
        self.page, self.kind = page, kind
        out = kind == "sink"
        self.title, self.devs = page.sect("출력" if out else "입력")
        self.ctl = page.sect(None)[1]
        self.drawn = None
        self.want = None                    # (이름, 기한) 사람이 고른 기본 장치 — 서버가 따라올 때까지
        self.port_want = None
        self.port_items = None
        self.cur = None
        self.quiet = False
        self.open = False
        self.leader = Gtk.RadioButton()     # 목록에 없는 장치가 기본일 때 켜 둘 숨은 단추 (라디오는 늘 하나가 켜져 있다)
        self.radios = {}

        self.vol = _Volume(page, kind)
        row(self.ctl, "볼륨", None, control=self.vol)

        if not out:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            self.level = Gtk.LevelBar()
            self.level.set_mode(Gtk.LevelBarMode.CONTINUOUS)
            for off in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH, "full"):
                self.level.remove_offset_value(off)     # 낮은 값이 경고색(주황)으로 보이지 않게
            self.level.get_style_context().add_class("snd-meter")
            self.level.set_size_request(180, -1)
            self.level.set_valign(Gtk.Align.CENTER)
            box.pack_start(self.level, False, False, 0)
            self.test_btn = button("테스트 시작", self._toggle_test)
            box.pack_start(self.test_btn, False, False, 0)
            row(self.ctl, "입력 테스트", "‘테스트 시작’을 누르고 말해 보세요 — 막대가 움직이면 소리가 들어오고 있습니다",
                control=box)
            self.shown = 0.0
            self.meter = _Meter(self._on_level, self._meter_end)

        self.props_btn = button("펼치기", self._toggle_props)
        row(self.ctl, "장치 속성", "포트 · 좌우 밸런스 · 형식" if out else "포트 · 형식", control=self.props_btn)

        self.port_combo = _combo("이 장치에서 쓸 단자")
        self.port_combo.connect("changed", self._on_port)
        page.hold_while_grabbed(self.port_combo)
        self.port_row = row(self.ctl, "포트", "소리가 나갈 단자" if out else "소리가 들어올 단자",
                            control=self.port_combo)
        self.bal_row = None
        if out:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            l = Gtk.Label(label="왼쪽")
            l.get_style_context().add_class("row-sub")
            box.pack_start(l, False, False, 0)
            self.bal = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -100, 100, 1)
            self.bal.set_draw_value(False)
            self.bal.set_has_origin(False)          # 가운데가 기준 — 왼쪽 끝부터 칠하면 한쪽으로 쏠린 것처럼 보인다
            self.bal.set_size_request(200, -1)
            self.bal.add_mark(0, Gtk.PositionType.BOTTOM, None)
            self.bal.connect("value-changed", self._on_balance)
            page.hold_while_grabbed(self.bal)
            box.pack_start(self.bal, False, False, 0)
            r = Gtk.Label(label="오른쪽")
            r.get_style_context().add_class("row-sub")
            box.pack_start(r, False, False, 0)
            self.bal_row = row(self.ctl, "좌우 밸런스", "왼쪽·오른쪽 소리 크기의 균형", control=box)
        self.fmt = info("")
        self.fmt_row = row(self.ctl, "형식", "소리 서버가 이 장치와 주고받는 형식", control=self.fmt)
        for r in (self.port_row, self.bal_row, self.fmt_row):
            if r is not None:
                r.set_no_show_all(True)

    def hide(self):
        for w in (self.title, self.devs, self.ctl):
            _reveal(w, False)
        self.stop_test()

    def draw(self, devs, default):
        now = time.monotonic()
        if self.want and (self.want[0] == default or now > self.want[1]
                          or all(d["name"] != self.want[0] for d in devs)):
            self.want = None
        chosen = self.want[0] if self.want else default
        key = tuple((d["name"], d["desc"], d["sub"], tuple(d["icons"])) for d in devs)
        if key != self.drawn:
            self.drawn = key
            self.quiet = True
            self.leader.set_active(True)
            _clear(self.devs)
            self.radios = {}
            out = self.kind == "sink"
            if devs:
                row(self.devs, "소리를 재생할 위치 선택" if out else "말하기 또는 녹음할 장치 선택",
                    "고른 장치가 기본 출력 장치가 됩니다" if out else "고른 장치가 기본 입력 장치가 됩니다",
                    icon=OUT_ICONS if out else IN_ICONS).show_all()
            elif out:
                row(self.devs, "소리 장치를 찾을 수 없습니다", "스피커나 헤드폰을 연결하면 여기에 나타납니다",
                    icon=SPK_MUTED + OUT_ICONS).show_all()
            else:
                row(self.devs, "입력 장치를 찾을 수 없습니다", "마이크를 연결하면 여기에 나타납니다",
                    icon=MIC_MUTED + IN_ICONS).show_all()
            for d in devs:
                rb = Gtk.RadioButton.new_from_widget(self.leader)
                rb.connect("toggled", self._on_radio, d["name"])
                r = row(self.devs, d["desc"], d["sub"], icon=d["icons"], control=rb, activatable=True)
                r.radio = rb
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                r.set_tooltip_text(d["desc"])
                r.show_all()
                self.radios[d["name"]] = rb
            self.quiet = False
        self.quiet = True
        (self.radios.get(chosen) or self.leader).set_active(True)
        self.quiet = False
        _reveal(self.title, True)
        _reveal(self.devs, True)

        cur = next((d for d in devs if d["name"] == chosen), None)
        self.cur = cur
        _reveal(self.ctl, cur is not None)
        if cur is None:
            self.stop_test()
            return
        self.vol.update(cur["name"], cur["chans"], cur["mute"])

        # 속성 — 포트
        if self.port_want and (self.port_want[0] != cur["name"] or self.port_want[1] == cur["port"]
                               or now > self.port_want[2]):
            self.port_want = None
        items = cur["ports"]
        self.quiet = True
        if items != self.port_items:
            self.port_items = items
            _fill(self.port_combo, items)
        self.port_combo.set_active_id(self.port_want[1] if self.port_want else cur["port"])
        self.quiet = False
        # 속성 — 밸런스 · 형식
        if self.bal_row is not None and not (self.vol.vol_send.active or self.bal in self.page.grabbed):
            self.quiet = True
            self.bal.set_value(round(self.vol.balance() * 100))
            self.quiet = False
        self.fmt.set_text(cur["fmt"] or "-")
        self._show_props()

        if self.kind == "source" and self.meter.running and self.meter.device != cur["name"]:
            self.meter.start(cur["name"])          # 기본 입력 장치가 바뀌었다 — 새 장치로 다시 듣는다

    def _show_props(self):
        cur = self.cur
        _reveal(self.port_row, self.open and cur is not None and len(cur["ports"]) >= 2)
        if self.bal_row is not None:
            _reveal(self.bal_row, self.open and cur is not None and self.vol.has_lr)
        _reveal(self.fmt_row, self.open and cur is not None and bool(cur["fmt"]))

    def _toggle_props(self):
        self.open = not self.open
        self.props_btn.set_label("접기" if self.open else "펼치기")
        self._show_props()

    def _on_radio(self, rb, name):
        if self.quiet or not rb.get_active():
            return
        self.want = (name, time.monotonic() + WANT_SECS)
        pactl_async([f"set-default-{self.kind}", name], lambda _ok: self.page.soon())

    def _on_port(self, c):
        port = c.get_active_id()
        if self.quiet or not port or self.cur is None or port == self.cur["port"]:
            return
        self.port_want = (self.cur["name"], port, time.monotonic() + WANT_SECS)
        pactl_async([f"set-{self.kind}-port", self.cur["name"], port], lambda _ok: self.page.soon())

    def _on_balance(self, sc):
        if self.quiet or self.cur is None:
            return
        self.vol.set_balance(sc.get_value() / 100)

    # ── 입력 테스트 ──
    def _toggle_test(self):
        if self.meter.running:
            self.stop_test()
        elif self.cur is not None and self.meter.start(self.cur["name"]):
            self.test_btn.set_label("테스트 멈추기")

    def stop_test(self):
        if self.kind != "source":
            return
        self.meter.stop()
        self.test_btn.set_label("테스트 시작")
        self.shown = 0.0
        self.level.set_value(0)

    def _meter_end(self):
        self.stop_test()

    def _on_level(self, peak):
        # 데시벨로 (-60 dB → 0, 0 dB → 1) — 말소리가 막대 가운데쯤 오게. 떨어질 땐 천천히
        lvl = max(0.0, min(1.0, (20 * math.log10(peak) + 60) / 60)) if peak > 0 else 0.0
        self.shown = max(lvl, self.shown - 0.06)
        self.level.set_value(self.shown)


class _Streams:
    """볼륨 믹서(재생 중인 앱) · 녹음 중인 앱 — 앱마다 볼륨, 음소거, 장치"""

    def __init__(self, page, kind, title, empty=None):
        self.page, self.kind, self.empty = page, kind, empty
        self.title, self.lb = page.sect(title)
        self.rows = {}
        self.wants = {}                     # 스트림 → (장치, 기한) — 옮기는 중
        self.drawn = None
        self.quiet = False

    def hide(self):
        _reveal(self.title, False)
        _reveal(self.lb, False)

    def draw(self, streams, devs):
        items = [(d["name"], d["desc"]) for d in devs]
        key = (tuple((s["index"], s["app"], bool(s["sub"]), tuple(s["icons"])) for s in streams), tuple(items))
        if key != self.drawn:
            self.drawn = key
            _clear(self.lb)
            self.rows = {}
            if not streams and self.empty:
                row(self.lb, self.empty[0], self.empty[1], icon=APP_ICONS).show_all()
            play = self.kind == "sink-input"
            for s in streams:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                combo = None
                if len(items) > 1:                  # 장치가 하나뿐이면 옮길 곳이 없다
                    combo = _combo("출력 장치" if play else "입력 장치", chars=20)
                    _fill(combo, items)
                    combo.connect("changed", self._on_move, s["index"])
                    self.page.hold_while_grabbed(combo)
                    combo.set_valign(Gtk.Align.CENTER)
                    box.pack_start(combo, False, False, 0)
                vol = _Volume(self.page, self.kind, width=180)
                box.pack_start(vol, False, False, 0)
                r = row(self.lb, s["app"], s["sub"] or None, icon=s["icons"], control=box)
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                if s["sub"]:
                    r.sub_label.set_ellipsize(Pango.EllipsizeMode.END)
                    r.sub_label.set_line_wrap(False)
                r.show_all()
                self.rows[s["index"]] = (r, vol, combo)
        now = time.monotonic()
        for s in streams:
            r, vol, combo = self.rows[s["index"]]
            if s["sub"]:
                r.sub_label.set_text(s["sub"])
            vol.update(s["index"], s["chans"], s["mute"])
            if combo is not None:
                w = self.wants.get(s["index"])
                if w and (w[0] == s["dev"] or now > w[1]):
                    del self.wants[s["index"]]
                    w = None
                self.quiet = True
                if not combo.set_active_id(w[0] if w else s["dev"] or ""):
                    combo.set_active(-1)             # 목록에 없는 곳(모니터 등)으로 가는 스트림
                self.quiet = False
        for gone in set(self.wants) - {s["index"] for s in streams}:
            del self.wants[gone]
        on = bool(streams) or bool(self.empty)
        _reveal(self.title, on)
        _reveal(self.lb, on)

    def _on_move(self, c, index):
        dev = c.get_active_id()
        if self.quiet or not dev:
            return
        self.wants[index] = (dev, time.monotonic() + WANT_SECS)
        verb = "move-sink-input" if self.kind == "sink-input" else "move-source-output"
        pactl_async([verb, index, dev], lambda _ok: self.page.soon())


class _Cards:
    """고급 — 사운드 카드마다 프로필 (아날로그 스테레오 · HDMI · 끄기 …)"""

    def __init__(self, page):
        self.page = page
        self.title, self.lb = page.sect("고급")
        self.combos = {}
        self.wants = {}
        self.drawn = None
        self.quiet = False

    def hide(self):
        _reveal(self.title, False)
        _reveal(self.lb, False)

    def draw(self, cards):
        key = tuple((c["name"], c["desc"], tuple(c["icons"]), tuple(c["profiles"])) for c in cards)
        if key != self.drawn:
            self.drawn = key
            _clear(self.lb)
            self.combos = {}
            for c in cards:
                combo = _combo("프로필", chars=30)
                _fill(combo, c["profiles"])
                combo.connect("changed", self._on_profile, c["name"])
                self.page.hold_while_grabbed(combo)
                r = row(self.lb, c["desc"], "프로필 — 이 장치로 소리를 어떤 방식으로 재생·녹음할지 고릅니다",
                        icon=c["icons"], control=combo)
                r.title_label.set_ellipsize(Pango.EllipsizeMode.END)
                r.show_all()
                self.combos[c["name"]] = combo
        now = time.monotonic()
        for c in cards:
            w = self.wants.get(c["name"])
            if w and (w[0] == c["active"] or now > w[1]):
                self.wants.pop(c["name"], None)
                w = None
            self.quiet = True
            self.combos[c["name"]].set_active_id(w[0] if w else c["active"])
            self.quiet = False
        _reveal(self.title, bool(cards))
        _reveal(self.lb, bool(cards))

    def _on_profile(self, combo, card):
        prof = combo.get_active_id()
        if self.quiet or not prof:
            return
        self.wants[card] = (prof, time.monotonic() + WANT_SECS)
        pactl_async(["set-card-profile", card, prof], lambda _ok: self.page.soon(), timeout=10)


class SoundPage:
    def __init__(self, store):
        self.p = Page("소리", "소리를 재생할 장치와 녹음할 장치를 고르고, 볼륨과 앱마다의 소리를 조정합니다.")
        self.dead = False
        self.visible = False
        self.grabbed = set()                # 사람이 잡고 있는 슬라이더 · 펼쳐 둔 콤보
        self.pending = None                 # 잡고 있는 동안 읽은 상태 — 놓으면 그린다
        self.fetching = self.again = False
        self._busy_n = 0                    # 목록을 거푸 못 읽은 횟수 (_show)
        self.soon_src = 0
        self.sub = self.sub_cancel = None   # pactl subscribe
        self.sub_src = 0
        self.sub_fails = 0
        self.sub_started = 0.0
        self.restarting = False

        self.notice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.notice.get_style_context().add_class("notice")
        self.notice_text = Gtk.Label(label=TEXT_LOADING, xalign=0)
        self.notice_text.set_line_wrap(True)
        self.notice.pack_start(self.notice_text, True, True, 0)
        self.retry = Gtk.Button(label="오디오 서비스 다시 시작")
        self.retry.set_valign(Gtk.Align.CENTER)
        self.retry.connect("clicked", lambda *_: self._restart_service())
        self.retry.set_no_show_all(True)
        self.notice.pack_end(self.retry, False, False, 0)
        self.p.add_widget(self.notice)

        self.out = _Devices(self, "sink")
        self.inp = _Devices(self, "source")
        self.mix = _Streams(self, "sink-input", "볼륨 믹서",
                            ("지금 소리를 내는 앱이 없습니다",
                             "앱에서 소리를 재생하면 여기에 나타나고, 앱마다 볼륨과 출력 장치를 정할 수 있습니다"))
        self.rec = _Streams(self, "source-output", "녹음 중인 앱")
        self.cards = _Cards(self)

        self.p.connect("map", self._on_map)
        self.p.connect("unmap", self._on_unmap)
        self.p.connect("destroy", self._destroy)

    @property
    def widget(self):
        return self.p

    def sect(self, title):
        """(제목, 리스트박스) — 둘 다 처음엔 숨겨 둔다 (상태를 읽은 뒤 보일 것만 보인다)"""
        lbl = None
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("section-title")
            lbl.set_no_show_all(True)
            self.p.add_widget(lbl)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        lb.set_no_show_all(True)
        lb.connect("row-activated", lambda _lb, r: getattr(r, "radio", None) and r.radio.set_active(True))
        self.p.add_widget(lb)
        return lbl, lb

    # ── 끄는 동안은 다시 그리지 않는다 ──
    def hold_while_grabbed(self, w):
        """슬라이더를 끄는 동안·콤보 목록이 열린 동안엔 새 상태로 다시 그리지 않는다
        (다시 그리면 잡고 있던 슬라이더가 옛 값으로 튀거나, 목록이 없어진다)"""
        if isinstance(w, Gtk.ComboBox):
            w.connect("notify::popup-shown", lambda c, _p: self._grab(c, c.get_property("popup-shown")))
        else:
            w.connect("button-press-event", lambda s, _e: self._grab(s, True))
            w.connect("button-release-event", lambda s, _e: self._grab(s, False))
        w.connect("destroy", lambda s: self.grabbed.discard(s))

    def _grab(self, w, on):
        if on:
            self.grabbed.add(w)
        else:
            self.grabbed.discard(w)
            if not self.grabbed and self.pending is not None:
                GLib.idle_add(self._flush)
        return False

    def _flush(self):
        if self.pending is not None and not self.grabbed and not self.dead:
            data, self.pending = self.pending, None
            self._show(data)
        return False

    # ── 읽기 ──
    def soon(self, ms=REFRESH_MS):
        """바뀜 알림 — 몰려와도 ms 에 한 번만 읽는다"""
        if self.dead or self.soon_src:
            return
        self.soon_src = GLib.timeout_add(ms, self._soon_due)

    def _soon_due(self):
        self.soon_src = 0
        self.refresh()
        return False

    def refresh(self):
        if self.dead:
            return
        if self.fetching:                   # 읽는 중 — 끝나면 한 번 더
            self.again = True
            return
        self.fetching = True

        partial = self._busy_n >= 3                  # 세 번 거푸 못 읽었으면 읽은 만큼이라도 그린다

        def work():
            try:
                data = fetch(partial)
            except Exception as e:           # 읽기가 죽어도 페이지는 살아 있게
                import traceback
                traceback.print_exc()
                dbg("소리 상태 읽기 실패", e)
                data = {"state": "down"}
            GLib.idle_add(self._got, data)
        threading.Thread(target=work, daemon=True).start()

    def _got(self, data):
        self.fetching = False
        if self.dead:
            return False
        if self.again:
            self.again = False
            self.refresh()
        self._show(data)
        return False

    def _show(self, data):
        if self.grabbed:
            self.pending = data
            return
        self.pending = None
        st = data.get("state")
        if st == "busy":
            self._busy_n += 1
            self.soon(1000)
            return
        self._busy_n = 0
        if st != "ok":
            self.notice_text.set_text(TEXT_MISSING if st == "missing" else TEXT_DOWN)
            _reveal(self.notice, True)
            self.retry.set_visible(st == "down" and not self.restarting)
            for part in (self.out, self.inp, self.mix, self.rec, self.cards):
                part.hide()
            return
        _reveal(self.notice, False)
        self.out.draw(data["sinks"], data["default_sink"])
        self.inp.draw(data["sources"], data["default_source"])
        self.mix.draw(data["playing"], data["sinks"])
        self.rec.draw(data["recording"], data["sources"])
        self.cards.draw(data["cards"])

    def _restart_service(self):
        self.restarting = True
        self.retry.hide()
        self.notice_text.set_text("오디오 서비스를 다시 시작하는 중…")

        def done(_ok, _out, _err):
            self.restarting = False
            if not self.dead:
                GLib.timeout_add(1000, lambda: (self.refresh(), False)[1])
        run_async(["systemctl", "--user", "restart", "pipewire.service", "pipewire-pulse.service",
                   "wireplumber.service"], done)

    # ── 바뀜 알림 (pactl subscribe) ──
    def _on_map(self, *_):
        self.visible = True
        self.refresh()
        self._watch()

    def _on_unmap(self, *_):
        self.visible = False
        self._unwatch()
        self.inp.stop_test()                  # 다른 페이지로 옮기면 마이크를 놓는다

    def _watch(self):
        if self.dead or not self.visible or self.sub is not None:
            return
        if self.sub_src:
            GLib.source_remove(self.sub_src)
            self.sub_src = 0
        if not shutil.which("pactl"):         # 없다 — 다시 띄워도 소용없다 (읽기가 안내를 보인다)
            return
        try:
            proc = _launcher(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE).spawnv(
                _deathsig(["pactl", "subscribe"]))
        except GLib.Error as e:
            dbg("pactl subscribe 실행 실패", e.message)
            return
        self.sub, self.sub_started = proc, time.monotonic()
        cancel = self.sub_cancel = Gio.Cancellable()
        stream = Gio.DataInputStream.new(proc.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, cancel, self._sub_line, (proc, cancel))
        if self.sub_fails:                    # 다시 붙었다 — 끊긴 동안 바뀐 것(서버가 다시 뜬 것)을 읽는다
            self.soon(500)

    def _sub_line(self, stream, res, ctx):
        proc, cancel = ctx
        try:
            line, _n = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if cancel.is_cancelled() or proc is not self.sub:
            return
        if line is None:                      # 끝났다 — 서버가 멈췄거나 다시 시작했다
            self._sub_died(proc)
            return
        m = EVENT_RE.search(line)
        if m and m.group(2) in FACILITIES:    # client(우리가 띄우는 pactl 들)·module 은 흘려보낸다
            self.soon()
        stream.read_line_async(GLib.PRIORITY_DEFAULT, cancel, self._sub_line, ctx)

    def _sub_died(self, proc):
        proc.force_exit()
        self.sub = self.sub_cancel = None
        lived = time.monotonic() - self.sub_started
        self.sub_fails = 0 if lived > 30 else self.sub_fails + 1
        self.soon()                           # 서버가 멈췄나 — 안내를 보인다
        if self.visible and not self.dead:
            delay = RETRY_SECS[min(max(self.sub_fails, 1), len(RETRY_SECS)) - 1]
            self.sub_src = GLib.timeout_add_seconds(delay, self._retry)

    def _retry(self):
        self.sub_src = 0
        self._watch()
        if self.sub is not None:
            self.soon(500)                    # 다시 붙었다 — 끊긴 동안 바뀐 것(서버가 다시 뜬 것)을 읽는다
        return False

    def _unwatch(self):
        if self.sub_src:
            GLib.source_remove(self.sub_src)
            self.sub_src = 0
        if self.sub_cancel is not None:
            self.sub_cancel.cancel()
        if self.sub is not None:
            self.sub.force_exit()
        self.sub = self.sub_cancel = None

    def _destroy(self, *_):
        if self.dead:
            return
        self.visible = False
        self._unwatch()
        self.inp.stop_test()
        self.dead = True
        if self.soon_src:
            GLib.source_remove(self.soon_src)
            self.soon_src = 0


def build(store):
    return SoundPage(store).widget


PAGES = [{"id": "sound", "title": "소리",
          "icon": ["audio-speakers", "audio-volume-high", "multimedia-volume-control",
                   "audio-speakers-symbolic", "audio-volume-high-symbolic"],
          "build": build}]
