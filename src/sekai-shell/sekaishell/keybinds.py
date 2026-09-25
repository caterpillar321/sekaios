"""단축키 — 기본값과 설명의 원천은 Hyprland 기본 설정의 bind 줄 하나뿐이다.

  /usr/share/sekai/hypr/hyprland.conf
      #: 앱 실행                                       ← 그 아래 줄들의 묶음 이름 (설정 › 단축키)
      bindd = $mainMod, E, 파일 탐색기, exec, sekai-files   ← 설명(d)이 화면에 보이는 이름
  사용자가 바꾼 것만 settings.json 의 "keybinds" 에:
      "changed": {"SUPER+E": "SUPER+w", "SUPER+N": ""}   기본 키 조합(= 항목 이름) → 새 조합, "" = 끔
      "custom":  [{"name": "브라우저", "command": "chromium", "key": "SUPER+b"}]
  → ~/.config/hypr/sekai.conf 끝에 unbind(기본 키 떼기) + bindd(새 키) (hypr_lines).
    그 조각은 hyprland.conf 다음에 source 되므로 조각이 없거나 깨져도 기본 단축키는 그대로 산다.
  → 세션 중에는 바뀐 키 조합만 hyprctl keyword 로 다시 건다 (apply_live — reload 는 keyword 로
    건 것들을 지우고 플러그인 설정까지 다시 읽어서 피한다).
  → 기본 화면 모드(X11): lib/x11/sxhkdrc 에서 같은 키 조합의 줄을 바꿔 ~/.config/sekai/sxhkdrc 로.
    세션(lib/x11/session)은 기본 파일로 sxhkd 를 띄우므로, 작업 표시줄이 시작할 때 사용자 파일이 있으면
    그것으로 다시 띄운다 (restart_sxhkd(only_if_needed=True)).

키 조합 표기: "SUPER+CTRL+ALT+SHIFT+키" — 수식 키는 이 순서, 키는 XKB 키 이름 (e, Return, F4, Print…).
항목 이름은 hyprland.conf 에 적힌 그대로의 기본 조합이다 — 기본 키를 바꾸면 그 항목의 사용자 설정은 버려진다.
"""
import os
import re
import subprocess
import tempfile

from . import dbg

HERE = os.path.dirname(os.path.abspath(__file__))
_CONF_PATHS = ("/usr/share/sekai/hypr/hyprland.conf",
               os.path.join(HERE, "..", "..", "sekai-desktop", "usr", "share", "sekai", "hypr",
                            "hyprland.conf"))
_X11_PATHS = ("/usr/lib/sekai/x11/sxhkdrc", os.path.join(HERE, "..", "lib", "x11", "sxhkdrc"))
X11_USER = os.path.expanduser("~/.config/sekai/sxhkdrc")

MODS = ("SUPER", "CTRL", "ALT", "SHIFT")
_MOD_NAMES = {"SUPER": "Win", "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}
_KEY_RE = re.compile(r"^[A-Za-z0-9_:]{1,40}$")
CUSTOM_GROUP = "사용자 지정"

# 바꿀 수 없게 잠가 두는 것 (정규화한 키 조합 → 까닭)
_LOCKED = {
    "SUPER+l": "보안을 위해 바꿀 수 없습니다 (윈도우와 같음)",
    "CTRL+escape": "기본 화면 모드에서 Win 키가 이 키로 시작 메뉴를 연다 — 바꿀 수 없습니다",
    "ALT+tab": "Alt 를 떼는 순간 창이 바뀌는 방식이라 Alt+Tab 으로 고정됩니다",
    "ALT+SHIFT+tab": "Alt 를 떼는 순간 창이 바뀌는 방식이라 Alt+Shift+Tab 으로 고정됩니다",
}
# 어떤 기능에도 줄 수 없는 키 (컴포지터·커널이 먼저 가져간다)
RESERVED = {f"CTRL+ALT+f{i}": "가상 터미널 전환 (시스템이 먼저 받습니다)" for i in range(1, 13)}
# 수식 키 없이 혼자 단축키가 되어도 되는 키 — 글자·숫자를 혼자 쓰면 글을 칠 수 없게 된다
_PLAIN_OK = re.compile(r"^(F\d{1,2}|Print|Pause|Scroll_Lock|Menu|XF86\w+)$", re.I)


# ── 키 조합 ─────────────────────────────────────────────────
def mods_of(s):
    """Hyprland 의 stringToModMask 와 같은 규칙 — 글자가 들어 있으면 그 수식 키"""
    s = (s or "").upper()
    out = set()
    if "SHIFT" in s:
        out.add("SHIFT")
    if "CTRL" in s or "CONTROL" in s:
        out.add("CTRL")
    if "ALT" in s or "MOD1" in s:
        out.add("ALT")
    if any(w in s for w in ("SUPER", "WIN", "LOGO", "MOD4", "META")):
        out.add("SUPER")
    return out


def combo(mods, key):
    return "+".join([m for m in MODS if m in mods] + [key])


def split(c):
    """"SUPER+SHIFT+s" → ({"SUPER","SHIFT"}, "s"). 틀린 모양이면 None"""
    if not isinstance(c, str) or not c:
        return None
    parts = c.split("+")
    key, mods = parts[-1], parts[:-1]
    if not _KEY_RE.match(key) or any(m not in MODS for m in mods) or len(set(mods)) != len(mods):
        return None
    return set(mods), key


def valid(c):
    return split(c) is not None


def norm(c):
    """비교용 — 키 이름의 대소문자를 없앤다 (Hyprland·XKB 는 대소문자를 가리지 않고 찾는다)"""
    s = split(c)
    return combo(s[0], s[1].lower()) if s else ""


def is_win_alone(c):
    s = split(c)
    return bool(s) and "SUPER" in s[0] and s[1].lower() in ("super_l", "super_r")


def plain_ok(key):
    """수식 키 없이 써도 되는 키인가"""
    return bool(_PLAIN_OK.match(key or ""))


_PRETTY = {
    "left": "←", "right": "→", "up": "↑", "down": "↓", "return": "Enter", "kp_enter": "Enter",
    "escape": "Esc", "print": "PrtSc", "space": "Space", "tab": "Tab", "backspace": "Backspace",
    "delete": "Del", "insert": "Ins", "prior": "PgUp", "page_up": "PgUp", "next": "PgDn",
    "page_down": "PgDn", "home": "Home", "end": "End", "menu": "메뉴 키", "pause": "Pause",
    "grave": "`", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "semicolon": ";", "apostrophe": "'", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "mouse_down": "휠 아래로", "mouse_up": "휠 위로",
    "mouse:272": "왼쪽 단추로 끌기", "mouse:273": "오른쪽 단추로 끌기",
    "xf86audioraisevolume": "볼륨 올림 키", "xf86audiolowervolume": "볼륨 내림 키",
    "xf86audiomute": "음소거 키", "xf86audiomicmute": "마이크 끔 키",
    "xf86monbrightnessup": "밝기 올림 키", "xf86monbrightnessdown": "밝기 내림 키",
    "xf86audioplay": "재생 키", "xf86audiopause": "일시 정지 키", "xf86audionext": "다음 곡 키",
    "xf86audioprev": "이전 곡 키", "xf86audiostop": "정지 키",
}


def pretty(c):
    """"SUPER+SHIFT+s" → "Win + Shift + S" (화면에 보일 이름). 빈 것은 "없음" """
    s = split(c)
    if not s:
        return "없음"
    mods, key = s
    if "SUPER" in mods and key.lower() in ("super_l", "super_r"):
        rest = [_MOD_NAMES[m] for m in MODS if m in mods and m != "SUPER"]
        return " + ".join(rest + ["Win (혼자 눌렀다 떼기)"])
    k = _PRETTY.get(key.lower())
    if k is None:
        k = key[4:] if key.lower().startswith("xf86") else (key.upper() if len(key) == 1 else key)
        k = k.replace("KP_", "숫자 패드 ")
    return " + ".join([_MOD_NAMES[m] for m in MODS if m in mods] + [k])


# ── hyprland.conf 읽기 ──────────────────────────────────────
class Entry:
    """단축키 한 줄 — 기본(hyprland.conf) 또는 사용자 지정"""
    __slots__ = ("id", "group", "desc", "flags", "key", "mods", "dispatcher", "arg", "lock", "custom")

    def __init__(self, id, group, desc, flags, mods, key, dispatcher, arg, custom=None):
        self.id = id                      # 기본 키 조합 (사용자 지정이면 "custom:번호")
        self.group = group
        self.desc = desc
        self.flags = flags                # d 를 뺀 bind 플래그 (r e l m …)
        self.mods = mods                  # 수식 키 집합
        self.key = key                    # hyprland.conf 에 적힌 그대로 (unbind 는 글자까지 같아야 한다)
        self.dispatcher = dispatcher
        self.arg = arg
        self.custom = custom              # 사용자 지정이면 그 번호
        self.lock = None
        if custom is None:
            if "m" in flags or key.startswith("mouse"):
                self.lock = "마우스 동작은 바꿀 수 없습니다"
            else:
                self.lock = _LOCKED.get(norm(id))


def _strip_comment(line):
    """hyprlang 규칙 — # 부터는 주석, ## 는 글자 # 하나"""
    out, i = [], 0
    while i < len(line):
        ch = line[i]
        if ch == "#":
            if line[i + 1:i + 2] == "#":
                out.append("#")
                i += 2
                continue
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def parse_conf(text):
    """bind 줄들 → [Entry] (submap 안의 것은 뺀다)"""
    vars_, group, submap, out = {}, "기타", "", []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#:"):
            group = s[2:].strip() or "기타"
            continue
        s = _strip_comment(s)
        if not s or "=" not in s:
            continue
        lhs, rhs = (p.strip() for p in s.split("=", 1))
        if lhs.startswith("$"):
            vars_[lhs[1:]] = rhs
            continue
        if lhs == "submap":
            submap = "" if rhs == "reset" else rhs
            continue
        m = re.match(r"^bind([a-z]*)$", lhs)
        if not m or submap:
            continue
        for name in sorted(vars_, key=len, reverse=True):
            rhs = rhs.replace("$" + name, vars_[name])
        flags = m.group(1)
        has_d, mouse = "d" in flags, "m" in flags
        n = (3 if mouse else 4) + (1 if has_d else 0)
        args = [a.strip() for a in rhs.split(",", n - 1)]
        args += [""] * (n - len(args))
        mods, key = mods_of(args[0]), args[1]
        if not key:
            continue
        desc = args[2] if has_d else ""
        disp = args[2 + has_d]
        arg = "" if mouse else args[3 + has_d]
        out.append(Entry(combo(mods, key), group, desc or f"{disp} {arg}".strip(),
                         flags.replace("d", ""), mods, key, disp, arg))
    return out


def _first(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


_cache = {"key": None, "entries": []}


def entries():
    """기본 단축키 목록 (hyprland.conf 가 바뀌지 않았으면 읽어 둔 것)"""
    path = _first(_CONF_PATHS)
    if path is None:
        return []
    try:
        key = (path, os.stat(path).st_mtime)
        if _cache["key"] != key:
            with open(path, encoding="utf-8") as f:
                _cache["entries"] = parse_conf(f.read())
            _cache["key"] = key
    except OSError as e:
        dbg("[keybinds] 기본 설정을 못 읽음", e)
    return _cache["entries"]


# ── 사용자 설정 ─────────────────────────────────────────────
def clean_name(s, limit=60):
    """이름 — 줄바꿈 등 제어 문자를 빼고, 쉼표는 Hyprland 의 인자 구분이라 빈칸으로"""
    s = "".join(ch for ch in str(s or "") if ch.isprintable()).replace(",", " ")
    return " ".join(s.split())[:limit]


def clean_section(sec):
    """settings.json 의 "keybinds" 를 믿을 수 있는 모양으로 (틀린 것은 버린다)"""
    sec = sec if isinstance(sec, dict) else {}
    ch = sec.get("changed")
    changed = {k: v for k, v in ch.items()
               if valid(k) and isinstance(v, str) and (v == "" or valid(v))} \
        if isinstance(ch, dict) else {}
    custom = []
    for c in sec.get("custom") if isinstance(sec.get("custom"), list) else ():
        if not isinstance(c, dict):
            continue
        name, cmd, key = c.get("name"), c.get("command"), c.get("key", "")
        if not isinstance(name, str) or not isinstance(cmd, str) or not isinstance(key, str):
            continue
        cmd = " ".join(cmd.splitlines()).strip()
        if not cmd or (key and not valid(key)):
            continue
        custom.append({"name": clean_name(name) or cmd[:40], "command": cmd, "key": key})
    return {"changed": changed, "custom": custom}


def custom_entries(sec):
    return [Entry(f"custom:{i}", CUSTOM_GROUP, c["name"], "", set(), "", "exec", c["command"], custom=i)
            for i, c in enumerate(clean_section(sec)["custom"])]


def _locked_combos(ents):
    return {norm(e.id) for e in ents if e.lock} | set(RESERVED)


def claimed(sec, ents=None):
    """이 설정이 건드리는 키 조합 (정규화) — 바뀐 항목의 기본 키·새 키, 사용자 지정의 키"""
    ents = entries() if ents is None else ents
    sec = clean_section(sec)
    by_id = {e.id: e for e in ents}
    locked = _locked_combos(ents)
    out = set()
    for k, v in sec["changed"].items():
        e = by_id.get(k)
        if e is None or e.lock:
            continue
        out.add(norm(k))
        if v and norm(v) not in locked:
            out.add(norm(v))
    for c in sec["custom"]:
        if c["key"] and norm(c["key"]) not in locked:
            out.add(norm(c["key"]))
    return out


def bindings(sec, ents=None):
    """[(항목, 지금 키 조합 또는 "")] — 기본 항목(hyprland.conf 순서) 다음 사용자 지정.
    사용자가 다른 것에 준 키를 기본값으로 쓰던 항목은 "" (그 키를 빼앗겼다)"""
    ents = entries() if ents is None else ents
    sec = clean_section(sec)
    locked = _locked_combos(ents)
    ch = sec["changed"]
    taken = {norm(v) for k, v in ch.items() if v} | {norm(c["key"]) for c in sec["custom"] if c["key"]}
    taken -= locked
    out = []
    for e in ents:
        if e.lock:
            out.append((e, e.id))
        elif e.id in ch:
            v = ch[e.id]
            out.append((e, v if v and norm(v) not in locked else ""))
        else:
            out.append((e, "" if norm(e.id) in taken else e.id))
    for e, c in zip(custom_entries(sec), sec["custom"]):
        out.append((e, c["key"] if c["key"] and norm(c["key"]) not in locked else ""))
    return out


def _esc(s):
    """hyprlang 에서 # 은 주석 시작 — 글자로 쓰려면 ## """
    return " ".join(str(s).splitlines()).replace("#", "##")


def bind_line(e, c):
    """(키워드, 값) — 항목 e 를 키 조합 c 에"""
    mods, key = split(c)
    flags = e.flags.replace("r", "")
    if is_win_alone(c):
        flags = flags.replace("e", "") + "r"          # Win 키만 — 떼는 순간 (e 와 r 은 같이 못 쓴다)
    mods_s = " ".join(m for m in MODS if m in mods)
    return (f"bind{flags}d",
            f"{mods_s}, {key}, {_esc(clean_name(e.desc))}, {e.dispatcher}, {_esc(e.arg)}")


def _unbind_args(touched, ents, *secs):
    """떼어 낼 "수식, 키" 들 — Hyprland 의 unbind 는 키 이름을 글자 그대로 비교하므로
    그 조합을 적었던 모든 표기(hyprland.conf 의 것, 전에 저장한 것)로 뗀다"""
    exact = {}
    for e in ents:
        exact.setdefault(norm(e.id), set()).add((frozenset(e.mods), e.key))
    for sec in secs:
        sec = clean_section(sec)
        for c in list(sec["changed"].values()) + [x["key"] for x in sec["custom"]]:
            if c:
                m, k = split(c)
                exact.setdefault(norm(c), set()).add((frozenset(m), k))
    out = []
    for t in sorted(touched):
        m, k = split(t)
        for mods, key in sorted(exact.get(t, {(frozenset(m), k)}), key=lambda x: x[1]):
            out.append(f"{' '.join(x for x in MODS if x in mods)}, {key}")
    return out


def hypr_lines(sec):
    """Hyprland 조각(sekai.conf)에 넣을 줄 — 바꾼 것이 없으면 []"""
    ents = entries()
    touched = claimed(sec, ents)
    if not touched:
        return []
    lines = ["# ── 단축키 (설정 › 단축키 에서 바꾼 것) ─────────────",
             "#   기본 키를 떼고(unbind) 새 키로 건다. 이 파일이 없어도 기본 단축키는 그대로다."]
    lines += [f"unbind = {u}" for u in _unbind_args(touched, ents, sec)]
    for e, c in bindings(sec, ents):
        if c and norm(c) in touched:
            kw, val = bind_line(e, c)
            lines.append(f"{kw} = {val}")
    return lines + [""]


# ── 세션에 바로 반영 ────────────────────────────────────────
def _hyprctl(*args):
    try:
        return subprocess.run(["hyprctl"] + [str(a) for a in args], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except Exception as e:
        dbg("[keybinds] hyprctl 실패", args, e)
        return ""


def apply_live(old, new):
    """old → new 로 바뀐 키 조합만 다시 건다. 건드린 조합은 모두 떼고 new 에서 그 조합을 쓰는 것을 다시
    거므로, 세션이 old 대로였다면 결과는 new 로 새로 로그인한 것과 같다."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        ents = entries()
        touched = claimed(old, ents) | claimed(new, ents)
        for u in _unbind_args(touched, ents, old, new):
            _hyprctl("keyword", "unbind", u)
        for e, c in bindings(new, ents):
            if c and norm(c) in touched:
                kw, val = bind_line(e, c)
                out = _hyprctl("keyword", kw, val)
                if out != "ok":
                    dbg("[keybinds] 못 걸었다", kw, val, out)
    elif x11_session():
        restart_sxhkd()


# ── 기본 화면 모드 (X11, sxhkd) ─────────────────────────────
_X11_MODS = {"super": "SUPER", "mod4": "SUPER", "ctrl": "CTRL", "control": "CTRL",
             "alt": "ALT", "mod1": "ALT", "shift": "SHIFT"}
# sxhkd 는 키 이름의 대소문자를 가린다 (XStringToKeysym)
_X11_KEYS = {"left": "Left", "right": "Right", "up": "Up", "down": "Down", "return": "Return",
             "escape": "Escape", "tab": "Tab", "print": "Print", "space": "space",
             "backspace": "BackSpace", "delete": "Delete", "home": "Home", "end": "End"}


def x11_session():
    return not os.environ.get("WAYLAND_DISPLAY") and bool(os.environ.get("DISPLAY"))


def _expand(s):
    """sxhkd 의 {a,b} 묶음 하나를 펼친다 (기본 파일은 한 줄에 한 묶음까지만 쓴다)"""
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    return [s[:m.start()] + p.strip() + s[m.end():] for p in m.group(1).split(",")]


def _x11_norm(h):
    parts = [p.strip() for p in h.split("+")]
    key = parts[-1].lstrip("@~")
    mods = set()
    for p in parts[:-1]:
        if p.lower() not in _X11_MODS:
            return ""
        mods.add(_X11_MODS[p.lower()])
    return norm(combo(mods, key)) if _KEY_RE.match(key) else ""


def parse_sxhkd(text):
    """[(정규화한 키 조합, 핫키 글, 명령)] — {a,b} 는 펼쳐서"""
    out, hot = [], None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if hot is None:
                continue
            hks, cmds = _expand(hot), _expand(line.strip())
            if len(cmds) != len(hks):
                cmds = [line.strip()] * len(hks)
            for h, c in zip(hks, cmds):
                out.append((_x11_norm(h), h.strip(), c))
            hot = None
        else:
            hot = line.strip()
    return out


def _x11_default():
    p = _first(_X11_PATHS)
    if p is None:
        return None, ""
    try:
        with open(p, encoding="utf-8") as f:
            return p, f.read()
    except OSError:
        return None, ""


def x11_combos():
    """기본 화면 모드에도 있는 키 조합 (정규화) — 없는 것은 그 모드에선 동작하지 않는다"""
    return {n for n, _h, _c in parse_sxhkd(_x11_default()[1]) if n}


def to_sxhkd(c):
    mods, key = split(c)
    key = _X11_KEYS.get(key.lower(), key.lower() if len(key) == 1 else key)
    return " + ".join([m.lower() for m in MODS if m in mods] + [key])


def x11_text(sec):
    """사용자 sxhkdrc 전체 — 바꾼 것이 없으면 None (기본 파일을 그대로 쓴다)"""
    ents = entries()
    if not claimed(sec, ents):
        return None
    _p, default = _x11_default()
    rows = parse_sxhkd(default)
    if not rows:
        return None
    sec = clean_section(sec)
    locked = _locked_combos(ents)
    remap = {norm(e.id): c for e, c in bindings(sec, ents)
             if e.custom is None and not e.lock and e.id in sec["changed"]}
    taken = {norm(c) for c in remap.values() if c} | \
        {norm(c["key"]) for c in sec["custom"] if c["key"]}
    taken -= locked
    out = ["# 자동 생성 — 설정 › 단축키 (기본: /usr/lib/sekai/x11/sxhkdrc). 직접 고치면 덮어써집니다.", ""]
    for n, hk, cmd in rows:
        if n and n in remap:
            new = remap[n]
            if not new or is_win_alone(new):
                continue                        # 끈 것 · Win 키 혼자는 이 모드에서 못 받는다
            hk = to_sxhkd(new)
        elif n and n in taken:
            continue                            # 사용자가 이 키를 다른 데 줬다
        out += [hk, "\t" + cmd]
    for c in sec["custom"]:
        # sxhkd 는 명령의 {a,b} 도 펼친다 — 그런 명령은 이 모드에서 뺀다
        if c["key"] and norm(c["key"]) not in locked and not is_win_alone(c["key"]) \
                and not re.search(r"[{}]", c["command"]):
            out += [to_sxhkd(c["key"]), "\t" + c["command"]]
    return "\n".join(out) + "\n"


def write_x11(sec):
    """~/.config/sekai/sxhkdrc 를 다시 쓴다 (바꾼 것이 없으면 지운다 — 기본 파일을 쓰게)"""
    text = x11_text(sec)
    if text is None:
        try:
            os.remove(X11_USER)
        except FileNotFoundError:
            pass
        return
    d = os.path.dirname(X11_USER)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sxhkdrc.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, X11_USER)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _sxhkd_config():
    """지금 떠 있는 내 sxhkd 가 읽은 설정 파일 ("" = 떠 있지 않다)"""
    try:
        pids = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "sxhkd"], capture_output=True,
                              text=True, timeout=3).stdout.split()
    except Exception:
        return ""
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        return argv[argv.index("-c") + 1] if "-c" in argv[:-1] else "?"
    return ""


def restart_sxhkd(only_if_needed=False):
    """sxhkd 를 새 설정으로 다시 띄운다. 옛 것이 키를 놓을 때까지 기다린다
    (쥔 채로 새 것이 뜨면 그 키들을 못 잡는다).
    only_if_needed: 이미 그 파일을 읽고 있으면 그대로 둔다 (작업 표시줄이 시작할 때)"""
    running = _sxhkd_config()
    if not running:
        return
    cfg = X11_USER if os.path.exists(X11_USER) and os.path.getsize(X11_USER) > 0 \
        else (_x11_default()[0] or "")
    if not cfg or (only_if_needed and running == cfg):
        return
    try:
        subprocess.Popen(["sh", "-c", 'u=$(id -u); pkill -u "$u" -x sxhkd; i=0; '
                          'while pgrep -u "$u" -x sxhkd >/dev/null && [ $i -lt 30 ]; do sleep 0.1; '
                          'i=$((i+1)); done; exec sxhkd -c "$1"', "sh", cfg],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        dbg("[keybinds] sxhkd 다시 띄우기 실패", e)
