"""SekaiOS 설정 — 저장소.

  ~/.config/sekai/settings.json   ← 진실의 원천. 사람이 읽을 수 있는 JSON.
  ~/.config/hypr/sekai.conf       ← 여기서 "생성"된다. hyprland.conf 가 source 한다.

값을 바꾸면 (1) JSON 에 쓰고 (2) sekai.conf 를 다시 쓰고
(3) hyprctl keyword 로 살아있는 세션에 즉시 반영한다.
전체 reload 를 피하는 이유: reload 는 플러그인 설정까지 다시 파싱해서
타이틀바 버튼이 중복되는 등 부작용이 있었다.
"""
import copy
import json
import os
import pwd
import signal
import subprocess

from .util import dbg, hex_to_rgba, keyword, run

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".config", "sekai")
CFG_FILE = os.path.join(CFG_DIR, "settings.json")
HYPR_DIR = os.path.join(HOME, ".config", "hypr")
HYPR_FRAG = os.path.join(HYPR_DIR, "sekai.conf")

DEFAULTS = {
    "appearance": {
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
        "follow_mouse": 1,
    },
    "display": {},               # {"DP-1": {...}}
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


class Store:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self._listeners = []
        self.load()

    # ── 입출력 ──────────────────────────────────────────
    def load(self):
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, json.load(f))
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
        tmp = CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._diff(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, CFG_FILE)
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

    def reset_section(self, section):
        self.data[section] = copy.deepcopy(DEFAULTS.get(section, {}))
        self.save()
        self.apply_all()

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
        lines.append("")

        os.makedirs(HYPR_DIR, exist_ok=True)
        tmp = HYPR_FRAG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, HYPR_FRAG)
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
            path = os.path.join(d, pwd.getpwuid(os.getuid()).pw_name + ".conf")
            old = os.umask(0o022)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(mon)
            finally:
                os.umask(old)
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
        subprocess.Popen(["sekai-wallpaper", "--apply"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
