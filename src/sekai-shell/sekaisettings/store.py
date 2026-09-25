"""SekaiOS 설정 — 저장소.

  ~/.config/sekai/settings.json   ← 진실의 원천. 사람이 읽을 수 있는 JSON.
  ~/.config/hypr/sekai.conf       ← 여기서 "생성"된다. hyprland.conf 가 source 한다.

값을 바꾸면 (1) JSON 에 쓰고 (2) sekai.conf 를 다시 쓰고
(3) hyprctl keyword 로 살아있는 세션에 즉시 반영한다.
전체 reload 를 피하는 이유: reload 는 플러그인 설정까지 다시 파싱해서
타이틀바 버튼이 중복되는 등 부작용이 있었다.
"""
import copy
import fcntl
import json
import os
import pwd
import re
import signal
import subprocess
import tempfile

from .util import dbg, hex_to_rgba, keyword, run
from sekaishell import theme

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".config", "sekai")
CFG_FILE = os.path.join(CFG_DIR, "settings.json")
HYPR_DIR = os.path.join(HOME, ".config", "hypr")
HYPR_FRAG = os.path.join(HYPR_DIR, "sekai.conf")

DEFAULTS = {
    "appearance": {
        "mode": "dark",           # dark / light — 아래 네 색을 sekaishell/theme.py 의 묶음으로 바꾼다
        "accent": "#39c5bb",      # 미쿠 틸
        "bg": "#151517",
        "surface": "#1e1e22",     # 작업 표시줄·메뉴·팝업의 면
        "fg": "#f1f1f3",
        "titlebar_bg": "#2c2c30", # 창 제목줄 — GTK 다크 앱 본문(#2d2d2d)에 맞춤
        "rounding": 10,
        "border_size": 1,
        "gaps_in": 6,
        "gaps_out": 0,          # 최대화한 창이 화면에 꽉 차게 (윈도우처럼)
        "inactive_opacity": 1.0,
        "blur": True,
        "shadow": True,
        "animations": True,
        "titlebar": True,
        "titlebar_height": 34,
        "cursor_size": 24,
    },
    "wallpaper": {
        "path": "/usr/share/backgrounds/sekai/hatsune.jpg",
        "mode": "fill",          # fill / fit / stretch / center / tile
        "color": "#151517",
    },
    "input": {
        "kb_layout": "us",
        "kb_variant": "",
        "kb_options": "korean:ralt_hangul,korean:rctrl_hanja",
        "repeat_rate": 25,
        "repeat_delay": 600,
        "sensitivity": 0.0,
        "natural_scroll": False,
        "tp_natural_scroll": True,
        "tp_tap": True,
        "follow_mouse": 2,       # 2 = 윈도우처럼 클릭해야 초점 이동 (1 = 마우스를 따라)
    },
    "display": {},               # {"DP-1": {...}}
    "layout": {
        "primary": "",           # 주 디스플레이 (모니터 이름). 비면 첫 모니터
    },
    "panel": {
        "height": 48,
        "clock_format": "%H:%M",
        "show_date": False,
    },
    "apps": {
        "terminal": "sekai-terminal",
        "browser": "chromium",
        "files": "thunar",
    },
    "locale": {
        "lang": "ko_KR.UTF-8",   # 그래픽 세션 언어 (sekai-session 이 읽는다)
    },
    "notifications": {
        "timeout": 6,            # 토스트가 떠 있는 시간(초)
        "history": 200,          # 보관할 알림 개수
    },
    "power": {
        "screen_off": 600,       # 초, 0 = 안 함
        "lock": 900,
        "suspend": 0,
    },
}

_FRAG_HEADER = """# ═══════════════════════════════════════════════════════════
#  이 파일은 sekai-settings 가 자동으로 생성합니다.
#  직접 고치면 다음 저장 때 덮어써집니다.
#  손으로 바꾸고 싶으면 hyprland.conf 에 직접 쓰세요
#  (이 파일이 나중에 source 되므로 여기 값이 우선합니다).
# ═══════════════════════════════════════════════════════════
"""


def atomic_write(path, text):
    """같은 폴더의 고유한 임시 파일에 다 쓰고(fsync) 바꿔치기 — 읽는 쪽은 언제나 온전한 파일만 본다.
    임시 파일 이름을 고정(…tmp)하면 두 프로그램이 동시에 저장할 때 서로의 반쯤 쓴 파일을
    바꿔치기해 JSON 이 깨졌다 (깨진 설정은 다음 실행 때 기본값으로 돌아간다)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


_BAD = object()
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_COLOR_KEYS = {("appearance", k) for k in ("accent", "bg", "surface", "fg", "titlebar_bg")} | \
    {("wallpaper", "color")}
# 모니터 한 대의 설정 (display 섹션은 기본값이 비어 있어 따로 적어 둔다)
_DISPLAY_KEYS = {"mode": "", "position": "", "scale": 1.0, "transform": 0, "vrr": 0, "enabled": True}


def _coerce(v, d):
    """저장된 값 v 를 기본값 d 의 타입에 맞춘다. 맞출 수 없으면 _BAD."""
    if isinstance(d, bool):                    # bool 은 int 의 하위 타입이라 먼저 본다
        if isinstance(v, bool):
            return v
        return bool(v) if isinstance(v, int) and v in (0, 1) else _BAD
    if isinstance(d, int):
        if isinstance(v, bool):
            return _BAD
        if isinstance(v, int):
            return v
        return int(v) if isinstance(v, float) and v.is_integer() else _BAD
    if isinstance(d, float):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else _BAD
    if isinstance(d, str):
        return v if isinstance(v, str) else _BAD
    return v


def _sanitize(saved):
    """손으로 고치거나 다른 버전이 쓴 설정 파일을 기본값의 모양에 맞춘다.
    "appearance": null, 숫자 자리의 글자, 깨진 색 같은 것이 있으면 시작하다 죽었다 (a['accent'] 등).
    틀린 값은 버린다 — 그 자리는 기본값이 쓰인다. 모르는 섹션·키는 그대로 둔다 (새 버전이 쓴 것일 수 있다)."""
    if not isinstance(saved, dict):
        dbg("설정 파일의 최상위가 사전이 아닙니다 — 기본값 사용")
        return {}
    out = {}
    for sec, vals in saved.items():
        base = DEFAULTS.get(sec)
        if not isinstance(base, dict):
            out[sec] = vals
            continue
        if not isinstance(vals, dict):
            dbg(f"설정 [{sec}] 이 사전이 아닙니다 ({type(vals).__name__}) — 기본값 사용")
            continue
        clean = {}
        for k, v in vals.items():
            if sec == "display":               # {"DP-1": {...}} — 모니터마다 사전
                if not isinstance(v, dict):
                    dbg(f"설정 [display] {k} 이 사전이 아닙니다 — 버림")
                    continue
                mon = {}
                for mk, mv in v.items():
                    fixed = _coerce(mv, _DISPLAY_KEYS[mk]) if mk in _DISPLAY_KEYS else mv
                    if fixed is _BAD:
                        dbg(f"설정 [display] {k}.{mk} = {mv!r} — 타입이 틀려 버림")
                        continue
                    mon[mk] = fixed
                clean[k] = mon
                continue
            if k not in base:
                clean[k] = v
                continue
            fixed = _coerce(v, base[k])
            if fixed is _BAD or ((sec, k) in _COLOR_KEYS and not _HEX.match(fixed)):
                dbg(f"설정 [{sec}] {k} = {v!r} — 틀린 값이라 기본값({base[k]!r})을 씀")
                continue
            clean[k] = fixed
        out[sec] = clean
    return out


def _merge(base, over):
    """기본값 위에 저장된 값을 덮어쓴다 (한 단계 중첩까지)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


# ── 값 변환기 (Hyprland 표기로) ─────────────────────────────
def _int(v):    return int(v)
def _f2(v):     return f"{float(v):.2f}"
def _bool(v):   return "true" if v else "false"
def _01(v):     return 1 if v else 0
def _str(v):    return str(v)
def _layout(v): return str(v) or "us"
def _rgba(v):   return hex_to_rgba(v)
def _accent(v): return hex_to_rgba(v, 0.53)


def _apply_wm_mode(mode):
    """모드가 바뀌었을 때 창 관리자 쪽 — keyword·GTK 설정으로는 바뀌지 않는 것들 (set_mode·되돌리기 공용)"""
    if os.environ.get("WAYLAND_DISPLAY"):
        # 제목줄 버튼 색은 설정을 다시 읽어야 바뀐다 (hyprbars 가 다시 읽을 때 버튼을 새로 만든다)
        run(["hyprctl", "reload"])
    else:
        # 기본 화면 모드(X11): xfwm4 창 테두리 테마를 바로 바꾼다
        run(["xfconf-query", "-c", "xfwm4", "-p", "/general/theme",
             "-s", "Sekai-Light" if mode == "light" else "Sekai"])


class Store:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self._listeners = []
        # 페이지를 다시 그려 달라는 요청을 받을 곳 — 설정 창이 넣는다 (on_rebuild(page_id 또는 None, 지연 ms, 섹션)).
        #   저장소는 GTK 를 모른다 (세션 시작 스크립트도 쓰므로)
        self.on_rebuild = None
        self.load()

    # ── 입출력 ──────────────────────────────────────────
    def load(self):
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, _sanitize(json.load(f)))
            dbg("설정 읽음", CFG_FILE)
        except FileNotFoundError:
            dbg("설정 파일 없음 — 기본값 사용")
        except Exception as e:
            # 깨진 파일은 다음 저장 때 덮어쓰이기 전에 옆에 남겨 둔다 (사용자 설정을 되살릴 수 있게)
            dbg("설정 읽기 실패, 기본값 사용:", e)
            try:
                os.replace(CFG_FILE, CFG_FILE + ".broken")
            except OSError:
                pass

    def _diff(self):
        out = {}
        for sec, vals in self.data.items():
            base = DEFAULTS.get(sec)
            if isinstance(vals, dict) and isinstance(base, dict):
                d = {k: v for k, v in vals.items() if base.get(k, object()) != v}
                if d:
                    out[sec] = d
            elif vals != base:
                out[sec] = vals
        return out

    def save(self):
        os.makedirs(CFG_DIR, exist_ok=True)
        text = json.dumps(self._diff(), indent=2, ensure_ascii=False) + "\n"
        # 저장은 한 번에 하나씩 (설정 앱 말고도 첫 부팅 설정·세션 시작 스크립트가 이 파일을 다룬다)
        with open(os.path.join(CFG_DIR, ".settings.lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            atomic_write(CFG_FILE, text)
        self.write_hypr_fragment()
        self.notify_panel()

    # ── 접근 ────────────────────────────────────────────
    def get(self, section, key=None, default=None):
        sec = self.data.get(section, {})
        if key is None:
            return sec
        return sec.get(key, DEFAULTS.get(section, {}).get(key, default))

    def set(self, section, key, value, apply=True):
        self.data.setdefault(section, {})[key] = value
        # 먼저 저장 — 적용 과정에서 신호를 받은 프로그램(sekai-desk, sekai-idle)이
        # 파일을 다시 읽으므로
        self.save()
        if apply:
            self.apply_one(section, key, value)
        for cb in self._listeners:
            try:
                cb(section, key, value)
            except Exception as e:
                dbg("리스너 예외", e)

    def connect(self, cb):
        self._listeners.append(cb)

    def disconnect(self, cb):
        """페이지를 다시 그릴 때 — 없어진 페이지의 리스너가 사라진 위젯을 건드리지 않게"""
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    def set_mode(self, mode):
        """다크 / 라이트 — 네 기본색을 그 모드의 묶음으로 바꾸고, 셸·창 제목줄·일반 앱에 알린다"""
        if mode not in theme.PALETTES:
            return
        a = self.data.setdefault("appearance", {})
        a["mode"] = mode
        a.update(theme.PALETTES[mode])
        self.save()                         # 조각 파일 다시 쓰기 + 패널·바탕화면에 SIGHUP
        theme.apply_system(mode)            # GTK·Chromium 등
        _apply_wm_mode(mode)
        for cb in self._listeners:
            try:
                cb("appearance", "mode", mode)
            except Exception as e:
                dbg("리스너 예외", e)

    def reset_section(self, section):
        old_mode = theme.mode_of(self.get("appearance"))
        self.data[section] = copy.deepcopy(DEFAULTS.get(section, {}))
        self.save()
        self.apply_all()
        if section == "appearance":
            # 색·모드가 바뀌었다 — 창의 CSS·GTK 설정과 일반 앱이 따라오게 (set_mode 와 같은 알림)
            mode = theme.mode_of(self.get("appearance"))
            theme.apply_system(mode)
            if mode != old_mode:
                _apply_wm_mode(mode)
            for key in ("accent", "mode"):
                for cb in self._listeners:
                    try:
                        cb("appearance", key, self.get("appearance", key))
                    except Exception as e:
                        dbg("리스너 예외", e)
        # 화면의 스위치·콤보가 옛 값을 보여 주지 않게 이 섹션을 쓰는 페이지들을 다시 그린다
        self.request_rebuild(section=section)

    def request_rebuild(self, page_id=None, delay_ms=0, section=None):
        """설정 창에 페이지를 다시 그려 달라고 — page_id 가 없으면 section 을 쓰는 페이지들
        (둘 다 없으면 모든 페이지)"""
        if self.on_rebuild is not None:
            try:
                self.on_rebuild(page_id, delay_ms, section)
            except Exception as e:
                dbg("다시 그리기 요청 실패", e)

    # ── Hyprland 조각 생성 ──────────────────────────────
    def write_hypr_fragment(self):
        a = self.get("appearance")
        i = self.get("input")
        lines = [_FRAG_HEADER]

        lines.append("general {")
        lines.append(f"    gaps_in = {int(a['gaps_in'])}")
        lines.append(f"    gaps_out = {int(a['gaps_out'])}")
        lines.append(f"    border_size = {int(a['border_size'])}")
        lines.append(f"    col.active_border = {hex_to_rgba(a['accent'], 0.53)}")
        lines.append("}")
        lines.append("")

        lines.append("decoration {")
        lines.append(f"    rounding = {int(a['rounding'])}")
        lines.append(f"    inactive_opacity = {float(a['inactive_opacity']):.2f}")
        lines.append("    blur {")
        lines.append(f"        enabled = {'true' if a['blur'] else 'false'}")
        lines.append("    }")
        lines.append("    shadow {")
        lines.append(f"        enabled = {'true' if a['shadow'] else 'false'}")
        lines.append("    }")
        lines.append("}")
        lines.append("")

        lines.append("animations {")
        lines.append(f"    enabled = {'true' if a['animations'] else 'false'}")
        lines.append("}")
        lines.append("")

        lines.append(f"env = XCURSOR_SIZE,{int(a['cursor_size'])}")
        lines.append("")

        lines.append("input {")
        lines.append(f"    kb_layout = {i['kb_layout'] or 'us'}")
        if i.get("kb_variant"):
            lines.append(f"    kb_variant = {i['kb_variant']}")
        if i.get("kb_options"):
            lines.append(f"    kb_options = {i['kb_options']}")
        lines.append(f"    repeat_rate = {int(i['repeat_rate'])}")
        lines.append(f"    repeat_delay = {int(i['repeat_delay'])}")
        lines.append(f"    sensitivity = {float(i['sensitivity']):.2f}")
        lines.append(f"    natural_scroll = {'true' if i['natural_scroll'] else 'false'}")
        lines.append(f"    follow_mouse = {int(i['follow_mouse'])}")
        lines.append("    touchpad {")
        lines.append(f"        natural_scroll = {'true' if i['tp_natural_scroll'] else 'false'}")
        lines.append(f"        tap-to-click = {'true' if i['tp_tap'] else 'false'}")
        lines.append("    }")
        lines.append("}")
        lines.append("")

        lines.append("plugin {")
        lines.append("    hyprbars {")
        lines.append(f"        bar_height = {int(a['titlebar_height'])}")
        lines.append(f"        enabled = {1 if a['titlebar'] else 0}")
        lines.append(f"        bar_color = {hex_to_rgba(a['titlebar_bg'])}")
        lines.append(f"        col.text = {hex_to_rgba(a['fg'])}")
        # 창 조작 버튼 — 오른쪽부터 역순 배치 → 화면에는 최소화 · 최대화 · 닫기.
        #   아이콘 sekai:* 는 SekaiOS 가 패치한 hyprbars 가 선으로 그린다 (크기 16 → 아이콘 10px)
        bb, bf = hex_to_rgba(a["titlebar_bg"]), hex_to_rgba(a["fg"])
        for icon, cmd in (("sekai:close", "hyprctl dispatch killactive"),
                          ("sekai:max", "hyprctl dispatch fullscreen 1"),
                          ("sekai:min", "hyprctl dispatch movetoworkspacesilent special:min")):
            lines.append(f"        hyprbars-button = {bb}, 16, {icon}, {cmd}, {bf}")
        lines.append("    }")
        lines.append("}")
        lines.append("")

        for name, m in sorted(self.get("display").items()):
            if not m.get("enabled", True):
                lines.append(f"monitor = {name}, disable")
                continue
            mode = m.get("mode") or "preferred"
            pos = m.get("position") or "auto"
            scale = m.get("scale", 1.0)
            line = f"monitor = {name}, {mode}, {pos}, {scale}"
            if m.get("transform"):
                line += f", transform, {int(m['transform'])}"
            if m.get("vrr"):
                line += f", vrr, {int(m['vrr'])}"       # 1 = 켜기, 2 = 전체 화면일 때만
            lines.append(line)
        prim = self.get("layout", "primary")
        if prim and self.get("display").get(prim, {}).get("enabled", True):
            # 주 디스플레이 — 로그인하면 첫 워크스페이스(창이 처음 뜨는 곳)가 여기
            lines.append(f"workspace = 1, monitor:{prim}, default:true")
        lines.append("")

        atomic_write(HYPR_FRAG, "\n".join(lines))
        dbg("조각 생성", HYPR_FRAG)
        self.publish_display()

    # ── 즉시 반영 ───────────────────────────────────────
    # (섹션, 키) → (hyprctl 키워드, 값 변환기)
    #   테이블은 클래스 레벨에 둔다. 값 변환은 반드시 지연 호출해야 한다
    #   (미리 계산하면 int("us") 같은 게 터진다).
    _KEYWORDS = {
        ("appearance", "gaps_in"):          ("general:gaps_in", _int),
        ("appearance", "gaps_out"):         ("general:gaps_out", _int),
        ("appearance", "border_size"):      ("general:border_size", _int),
        ("appearance", "rounding"):         ("decoration:rounding", _int),
        ("appearance", "inactive_opacity"): ("decoration:inactive_opacity", _f2),
        ("appearance", "blur"):             ("decoration:blur:enabled", _bool),
        ("appearance", "shadow"):           ("decoration:shadow:enabled", _bool),
        ("appearance", "animations"):       ("animations:enabled", _bool),
        ("appearance", "titlebar_height"):  ("plugin:hyprbars:bar_height", _int),
        ("appearance", "titlebar"):         ("plugin:hyprbars:enabled", _01),
        ("appearance", "accent"):           ("general:col.active_border", _accent),
        ("appearance", "titlebar_bg"):      ("plugin:hyprbars:bar_color", _rgba),
        ("appearance", "fg"):               ("plugin:hyprbars:col.text", _rgba),
        ("input", "kb_layout"):             ("input:kb_layout", _layout),
        ("input", "kb_variant"):            ("input:kb_variant", _str),
        ("input", "kb_options"):            ("input:kb_options", _str),
        ("input", "repeat_rate"):           ("input:repeat_rate", _int),
        ("input", "repeat_delay"):          ("input:repeat_delay", _int),
        ("input", "sensitivity"):           ("input:sensitivity", _f2),
        ("input", "natural_scroll"):        ("input:natural_scroll", _bool),
        ("input", "follow_mouse"):          ("input:follow_mouse", _int),
        ("input", "tp_natural_scroll"):     ("input:touchpad:natural_scroll", _bool),
        ("input", "tp_tap"):                ("input:touchpad:tap-to-click", _bool),
    }

    def publish_display(self):
        """로그인 화면이 같은 해상도로 뜨게 monitor 줄을 공용 폴더에 적는다.
        (로그인 화면은 내 홈을 못 읽는다. 받는 쪽이 형식을 엄격히 검사한다)"""
        d = "/var/lib/sekai/displays"
        if not os.path.isdir(d):
            return
        try:
            with open(HYPR_FRAG, encoding="utf-8") as f:
                mon = [ln for ln in f if ln.startswith("monitor = ")]
            prim = self.get("layout", "primary") or ""
            if prim:
                mon.append(f"# primary = {prim}\n")      # 로그인 화면이 입력 칸을 주 디스플레이에
            path = os.path.join(d, pwd.getpwuid(os.getuid()).pw_name + ".conf")
            # 누구나 쓰는 폴더 — 이름으로 바로 열면 남이 미리 둔 FIFO·링크에 막히거나 쓴다.
            #   예측할 수 없는 새 임시 파일(O_EXCL)에 쓰고 바꿔 넣는다
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".sekai-display-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(mon)
                    os.fchmod(f.fileno(), 0o644)      # 로그인 화면(_greetd)이 읽는다
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            dbg("해상도 게시 실패", e)
    def apply_one(self, section, key, value):
        hit = self._KEYWORDS.get((section, key))
        if hit:
            kw, conv = hit
            try:
                keyword(kw, conv(value))
            except Exception as e:
                dbg("변환 실패", section, key, value, e)
        elif section == "wallpaper":
            self.apply_wallpaper()
        elif section == "display":
            self.apply_display()
        elif section == "power":
            # sekai-idle 이 설정을 다시 읽고 swayidle 을 새로 띄운다
            for pid in run(["pgrep", "-f", r"^\S*python3\S* \S*sekai-idle( |$)"]).split():
                try:
                    os.kill(int(pid), signal.SIGHUP)
                except Exception:
                    pass

    def apply_all(self):
        self.write_hypr_fragment()
        for section in ("appearance", "input"):
            for k, v in self.get(section).items():
                self.apply_one(section, k, v)
        self.apply_display()
        self.apply_wallpaper()
        self.notify_panel()

    def apply_display(self):
        for name, m in self.get("display").items():
            if not m.get("enabled", True):
                keyword("monitor", f"{name}, disable")
                continue
            mode = m.get("mode") or "preferred"
            arg = f"{name}, {mode}, {m.get('position') or 'auto'}, {m.get('scale', 1.0)}"
            if m.get("transform"):
                arg += f", transform, {int(m['transform'])}"
            # vrr 는 끌 때도 0 을 명시해야 켜져 있던 것이 꺼진다
            arg += f", vrr, {int(m.get('vrr', 0))}"
            keyword("monitor", arg)

    def apply_wallpaper(self):
        try:
            subprocess.Popen(["sekai-wallpaper", "--apply"],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:                     # 없으면 배경만 못 바꾼다 — 되돌리기 등 나머지는 계속
            dbg("배경화면 적용 실패", e)

    # ── 패널에 알리기 ───────────────────────────────────
    def notify_panel(self):
        """sekai-panel 에 SIGHUP → 설정 다시 읽고 CSS 재적용."""
        # 바탕화면(sekai-desk)도 강조색·작업 표시줄 높이를 쓴다
        out = run(["pgrep", "-f", r"^\S*python3\S* \S*sekai-(panel|desk)( |$)"])
        for pid in out.split():
            try:
                os.kill(int(pid), signal.SIGHUP)
            except Exception:
                pass
