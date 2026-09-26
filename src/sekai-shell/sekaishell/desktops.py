"""가상 데스크톱 — 윈도우의 가상 데스크톱을 Hyprland 워크스페이스 1…N 으로.

  ~/.config/sekai/desktops.json   {"names": ["", "회사"]}  — 개수와 이름 ("" = "데스크톱 N")
    settings.json 과 따로 두는 까닭: 단축키(작업 표시줄 프로세스)와 설정 앱이 둘 다 바꾼다.
    한 파일을 두 프로그램이 각자 메모리의 값으로 저장하면 서로의 변경을 덮어쓴다.
  로그인할 때는 Hyprland 조각(sekai.conf)의 workspace 규칙(defaultName·persistent — rule_lines)이
  같은 개수·이름을 만든다. 세션 중에는 여기서 hyprctl keyword/dispatch 로 바로 바꾼다.

  Hyprland 는 워크스페이스를 지울 수 없다 (비면 저절로 사라진다). 그래서 "제거"는 윈도우처럼
  그 안의 창을 옆 데스크톱으로 옮기고 뒤의 것을 한 칸씩 당긴다. 맨 뒤에 남는 빈 워크스페이스는
  keyword 로 persistent 를 풀 수 없어 다시 로그인할 때까지 남지만, 창이 없어 목록·이동에서 빠진다.

  "지금 데스크톱만" 보이기 (작업 표시줄 · Alt+Tab): 최소화한 창은 special:min 에 있어 원래 데스크톱을
  모른다 → 창을 볼 때마다 마지막 일반 워크스페이스를 기억해 둔다 (remember / visible).
"""
import fcntl
import json
import os
import subprocess
import tempfile

from . import dbg

FILE = os.path.expanduser("~/.config/sekai/desktops.json")
MAX = 20               # 이보다 많으면 작업 보기(Win+Tab) 격자가 알아볼 수 없을 만큼 작아진다
NAME_MAX = 40
MODES = ("current", "all")


class Note(str):
    """데스크톱 이름이 아니라 안내 글 — 화면에 작게 보인다"""


def default_name(i):
    return f"데스크톱 {i}"


def clean_name(s):
    """쉼표는 Hyprland 규칙의 구분자라 빈칸으로, 줄바꿈 등 제어 문자는 뺀다"""
    s = "".join(ch for ch in str(s or "") if ch.isprintable()).replace(",", " ")
    return " ".join(s.split())[:NAME_MAX]


# ── 저장 ────────────────────────────────────────────────────
def load():
    """이름 목록 (길이 = 데스크톱 개수, 적어도 1)"""
    try:
        with open(FILE, encoding="utf-8") as f:
            names = json.load(f).get("names")
    except Exception:
        names = None
    if not isinstance(names, list):
        return [""]
    out = [clean_name(n) if isinstance(n, str) else "" for n in names][:MAX]
    return out or [""]


def save(names, regen=True):
    names = [clean_name(n) for n in names][:MAX] or [""]
    # 기본 이름과 같으면 "" 로 — 앞의 것을 지워 번호가 당겨지면 이름도 따라가게 (윈도우처럼)
    names = ["" if n == default_name(i) else n for i, n in enumerate(names, 1)]
    d = os.path.dirname(FILE)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".desktops.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".desktops.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"names": names}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, FILE)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    if regen:
        _regen_fragment()
    return names


def _regen_fragment():
    """다음 로그인(과 Hyprland 가 설정을 다시 읽을 때)에도 같게 — 조각 파일을 새로 쓴다"""
    try:
        from sekaisettings.store import Store
        Store().write_hypr_fragment()
    except Exception as e:
        dbg("[desktops] 조각 다시 쓰기 실패", e)


def display(names, i):
    return (names[i - 1] if 0 < i <= len(names) else "") or default_name(i)


def _esc(s):
    return s.replace("#", "##")               # hyprlang 에서 # 은 주석 시작


def rule(names, i):
    return f"{i}, defaultName:{_esc(display(names, i))}, persistent:true"


def rule_lines(names):
    """Hyprland 조각에 넣을 workspace 규칙 — 로그인하면 이 개수·이름으로 만들어져 있다"""
    return ["# ── 가상 데스크톱 (설정 › 멀티태스킹) ─────────────"] + \
        [f"workspace = {rule(names, i)}" for i in range(1, len(names) + 1)]


def mode(key):
    """"taskbar" / "alttab" → "current" | "all" (설정 › 멀티태스킹)"""
    from .config import settings
    v = settings("multitasking", key, "current")
    return v if v in MODES else "current"


# ── Hyprland ────────────────────────────────────────────────
def hyprland():
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def _ctl(*args, js=False):
    cmd = ["hyprctl"] + (["-j"] if js else []) + [str(a) for a in args]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except Exception as e:
        dbg("[desktops] hyprctl 실패", args, e)
        return None if js else ""
    if not js:
        return out.strip()
    try:
        return json.loads(out)
    except ValueError:
        return None


def _batch(cmds):
    # ; 로 잇는다 — 창 주소·번호만 들어가므로 ; 가 섞일 일이 없다
    for i in range(0, len(cmds), 40):
        _ctl("--batch", "; ".join(cmds[i:i + 40]))


class Live:
    """지금 Hyprland 의 워크스페이스 상태"""

    def __init__(self):
        self.ws = {}          # id → {"windows": n, "name": …}
        self.active = None    # 초점이 있는 모니터가 보여 주는 워크스페이스
        self.others = set()   # 다른 모니터들이 보여 주는 워크스페이스
        self.mon_of = {}      # 보이는 워크스페이스 → 모니터 이름
        self.focused_mon = None
        for w in _ctl("workspaces", js=True) or []:
            if isinstance(w, dict) and isinstance(w.get("id"), int) and w["id"] > 0:
                self.ws[w["id"]] = w
        for m in _ctl("monitors", js=True) or []:
            wid = (m.get("activeWorkspace") or {}).get("id") if isinstance(m, dict) else None
            if isinstance(wid, int):
                self.mon_of[wid] = m.get("name")
                if m.get("focused"):
                    self.active = wid
                    self.focused_mon = m.get("name")
                else:
                    self.others.add(wid)

    def desk(self, names):
        """지금 데스크톱 — 데스크톱(1~N)을 보여 주는 모니터의 것. 둘째 모니터에 초점이 있어도
        (그 모니터는 N+1 번이나 데스크톱 번호 밖을 보여 준다) 데스크톱은 첫 모니터의 것이다"""
        n = len(names)
        cands = [w for w in [self.active, *sorted(self.others)] if isinstance(w, int) and 1 <= w <= n]
        return cands[0] if cands else self.active

    def count(self, names):
        """데스크톱 개수 — 저장된 개수, 그리고 창이 있는 워크스페이스 중 가장 큰 번호
        (Win+Tab 에서 끌어 놓는 등 다른 길로 생긴 것도 목록에 나오게).
        다른 모니터가 보여 주는 워크스페이스는 뺀다 — Hyprland 는 모니터마다 제 워크스페이스를 가져서
        둘째 모니터는 N+1 번을 보여 준다. 윈도우의 데스크톱처럼 셈하면 모니터가 데스크톱으로 보인다"""
        n = len(names)
        desk = self.desk(names)
        # 데스크톱을 보여 주지 않는 모니터의 워크스페이스는 세지 않는다 — 둘째 모니터에 초점이 있을 때
        #   그 번호(21 등)를 데스크톱으로 세어 데스크톱이 20개로 불어나 저장됐다
        skip = ({self.active} | self.others) - {desk}
        for i, w in self.ws.items():
            if n < i <= MAX and i not in skip and (w.get("windows", 0) > 0 or i == desk):
                n = i
        return n


def _pad(names, n):
    return list(names) + [""] * max(0, n - len(names))


def _name_live(names, i, live=None):
    """워크스페이스 i 의 규칙(다음에 생길 때 이름)과 지금 이름을 맞춘다 — 이미 맞으면 그대로
    (Win+숫자마다 부르므로 hyprctl 을 덜 부른다)"""
    w = live.ws.get(i) if live else None
    if w is not None and w.get("name") == display(names, i):
        return
    _ctl("keyword", "workspace", rule(names, i))
    # 아직 없는 워크스페이스면 Hyprland 가 거절한다 — 생길 때 위 규칙의 이름을 쓴다
    _ctl("dispatch", "renameworkspace", f"{i} {display(names, i)}")


def _desk(live, names):
    """(지금 데스크톱 워크스페이스, 그것을 보여 주는 모니터) — 데스크톱 수를 늘리기 전의 이름 목록으로 정한다
    (늘린 뒤에 정하면 둘째 모니터가 보여 주던 N+1 번이 데스크톱처럼 보여 그 모니터를 골랐다)"""
    d = live.desk(names)
    return d, live.mon_of.get(d)


def _focus_mon(live, mon):
    """초점을 데스크톱 모니터로 — workspace 명령은 초점 모니터에 걸려, 둘째 모니터에서 데스크톱을
    바꾸거나 만들면 데스크톱이 그 모니터로 넘어갔다. 데스크톱을 여는 곳은 모두 focusworkspaceoncurrentmonitor
    (그 워크스페이스가 다른 모니터에 딸려 있어도 이 모니터로 가져온다 — workspace 는 원래 모니터로 초점을 옮겼다)"""
    if mon and mon != live.focused_mon:
        _ctl("dispatch", "focusmonitor", mon)
        live.focused_mon = mon


def _evict(ids, live, desk_ws=None):
    """ids 중 데스크톱이 아닌 모니터(둘째 모니터)가 보여 주고 있는 번호가 있으면 그 모니터를 데스크톱 번호
    밖(MAX 넘어)의 빈 워크스페이스로 옮긴다 — 새 데스크톱이 남의 화면을 가져오지 않게. 초점이 그 모니터에
    있어도 옮긴다. Hyprland 에는 초점 없는 모니터의 워크스페이스를 바꾸는 명령이 없어 잠깐 초점을 옮긴다"""
    hit = [k for k in ids if k in ({live.active} | live.others) - {desk_ws}]
    if not hit:
        return
    mons = [m for m in _ctl("monitors", js=True) or [] if isinstance(m, dict)]
    me = next((m.get("name") for m in mons if m.get("focused")), None)
    if not me:
        return
    free = max(list(live.ws) + list(live.others) + [MAX]) + 1
    pos = _ctl("cursorpos").replace(",", " ").split()
    cmds = []
    for m in mons:
        k = (m.get("activeWorkspace") or {}).get("id")
        if k in hit:
            cmds += [f"dispatch focusmonitor {m.get('name')}", f"dispatch workspace {free}"]
            for c in _ctl("clients", js=True) or []:
                if isinstance(c, dict) and (c.get("workspace") or {}).get("id") == k and c.get("address"):
                    cmds.append(f"dispatch movetoworkspacesilent {free},address:{c['address']}")
            free += 1
    cmds.append(f"dispatch focusmonitor {me}")
    if len(pos) == 2:
        cmds.append(f"dispatch movecursor {pos[0]} {pos[1]}")    # 초점을 옮기며 튄 커서를 제자리로
    _batch(cmds)
    for k in hit:
        live.others.discard(k)
        live.ws.pop(k, None)
        if live.active == k:
            live.active = None              # 초점 모니터가 비켰다 — 데스크톱이 아니다


def _ensure(i, live, names, desk_ws=None):
    """i 번까지 데스크톱이 있게 — 없으면 만들어 저장한다. 돌려주는 값: 이름 목록"""
    n = live.count(names)
    if i <= len(names):
        return names
    _evict(range(n + 1, i + 1), live, desk_ws)
    names = save(_pad(names, max(i, n)))
    for j in range(n + 1, i + 1):
        _ctl("keyword", "workspace", rule(names, j))
    return names


# ── 동작 (단축키 · 설정 앱) — 화면에 보일 글을 돌려준다 ──────────
def go(i, live=None, names=None):
    live, names = live or Live(), names if names is not None else load()
    if not 1 <= i <= MAX:
        return None
    desk_ws, desk_mon = _desk(live, names)
    names = _ensure(i, live, names, desk_ws)
    _focus_mon(live, desk_mon)
    _ctl("dispatch", "focusworkspaceoncurrentmonitor", i)
    _name_live(names, i, live)
    return display(names, i)


def step(d):
    """옆 데스크톱으로 — 끝에서는 넘어가지 않는다 (윈도우처럼)"""
    live, names = Live(), load()
    cur, n = live.desk(names), live.count(names)
    if cur is None or not 1 <= cur <= n:
        return None
    t = cur + d
    if not 1 <= t <= n:
        return display(names, cur)
    return go(t, live, names)


def new(switch=True):
    """맨 뒤에 새 데스크톱 (Win+Ctrl+D 는 만들고 그리로 간다)"""
    live, names = Live(), load()
    n = live.count(names)
    if n >= MAX:
        return Note(f"데스크톱은 {MAX}개까지 만들 수 있습니다")
    k = n + 1
    desk_ws, desk_mon = _desk(live, names)
    _evict([k], live, desk_ws)
    names = save(_pad(names, n) + [""])
    _ctl("keyword", "workspace", rule(names, k))       # 규칙이 먼저 — 새로 생길 때 이 이름으로
    if switch:
        _focus_mon(live, desk_mon)
        _ctl("dispatch", "focusworkspaceoncurrentmonitor", k)
    # 번호가 같은 빈 워크스페이스가 남아 있을 수 있다 (앞서 지운 데스크톱) — 이름을 새로 붙이면
    #   Hyprland 가 규칙(persistent)도 다시 읽는다
    _ctl("dispatch", "renameworkspace", f"{k} {display(names, k)}")
    return display(names, k)


def remove(k=None):
    """데스크톱 k(없으면 지금 것)를 닫는다 — 창은 왼쪽 데스크톱으로 (첫 데스크톱이면 오른쪽 것과 합친다)"""
    live, names = Live(), load()
    n = live.count(names)
    cur, desk_mon = _desk(live, names)
    k = k or cur
    if not k or n <= 1 or not 1 <= k <= n:
        return None

    def final(j):
        # k 의 창은 k-1 로 (k 가 1 이면 2 와 합쳐져 1 이 된다), 뒤의 것은 한 칸씩 당긴다
        return j if j < k else (max(1, k - 1) if j == k else j - 1)

    cmds = []
    for c in _ctl("clients", js=True) or []:
        j = ((c.get("workspace") or {}).get("id") or 0) if isinstance(c, dict) else 0
        if 1 <= j <= n and final(j) != j and c.get("address"):
            cmds.append(f"dispatch movetoworkspacesilent {final(j)},address:{c['address']}")
    _batch(cmds)
    for a, h in list(_home.items()):         # 최소화한 창이 기억하는 데스크톱도 당긴다
        if 1 <= h <= n:
            _home[a] = final(h)
    names = _pad(names, n)
    del names[k - 1]
    names = save(names)
    live = Live()
    for i in range(k, n):
        _name_live(names, i, live)
    if cur is not None and k <= cur <= n:
        _focus_mon(live, desk_mon)
        _ctl("dispatch", "focusworkspaceoncurrentmonitor", final(cur))
        return display(names, final(cur))
    return None


def rename(k, name):
    live, names = Live(), load()
    names = _pad(names, max(k, live.count(names)))
    names[k - 1] = clean_name(name)
    names = save(names)
    _name_live(names, k, live)
    return display(names, k)


def move(i):
    """지금 창을 데스크톱 i 로 (창을 따라간다)"""
    live, names = Live(), load()
    if not 1 <= i <= MAX:
        return None
    a = _ctl("activewindow", js=True)
    addr = a.get("address") if isinstance(a, dict) else None
    desk_ws, desk_mon = _desk(live, names)
    names = _ensure(i, live, names, desk_ws)
    if addr and desk_mon and desk_mon != live.focused_mon:
        # 둘째 모니터의 창 — 데스크톱은 첫 모니터에 있다: 그리로 가서 창을 불러온다
        #   (그냥 movetoworkspace 하면 새 데스크톱이 둘째 모니터에 생겼다)
        _focus_mon(live, desk_mon)
        _ctl("dispatch", "focusworkspaceoncurrentmonitor", i)
        _ctl("dispatch", "movetoworkspace", f"{i},address:{addr}")
    else:
        _ctl("dispatch", "movetoworkspace", i)
    _name_live(names, i, live)
    return display(names, i)


def current():
    live, names = Live(), load()
    d = live.desk(names)
    return display(names, d) if d and 0 < d <= live.count(names) else None


def handle(action, arg=""):
    """sekai-ctl desktop <action> [arg] — 화면 가운데에 보여 줄 글 (없으면 None, 안내 글이면 Note)"""
    if not hyprland():
        return Note("기본 화면 모드에서는 가상 데스크톱을 쓸 수 없습니다")
    try:
        if action == "next":
            return step(1)
        if action == "prev":
            return step(-1)
        if action == "new":
            return new()
        if action == "close":
            return remove()
        if action == "go":
            return go(int(arg))
        if action == "move":
            return move(int(arg))
        if action == "show":
            return current()
    except (TypeError, ValueError) as e:
        dbg("[desktops] 잘못된 인자", action, arg, e)
    return None


# ── 창이 어느 데스크톱 것인가 ───────────────────────────────
_home = {}          # 창 주소 → 마지막으로 본 일반 워크스페이스 (최소화하면 special:min 으로 가서 모른다)


def remember(clients):
    seen = set()
    for c in clients or ():
        a = c.get("address")
        if not a:
            continue
        seen.add(a)
        wid = (c.get("workspace") or {}).get("id", 0) or 0
        if wid > 0:
            _home[a] = wid
    for a in list(_home):
        if a not in seen:
            del _home[a]


def home(c):
    wid = (c.get("workspace") or {}).get("id", 0) or 0
    return wid if wid > 0 else _home.get(c.get("address"))


def visible(c, shown, n=None):
    """shown(지금 화면에 보이는 워크스페이스들)의 창인가 — 최소화한 창은 원래 데스크톱으로 가린다.
    어느 데스크톱 것인지 모르면(작업 표시줄이 다시 뜬 뒤 등) 보인다고 한다 (숨겨서 못 찾는 것보다 낫다).
    n(데스크톱 개수)보다 큰 데스크톱을 기억하면 그 데스크톱은 이미 닫혔다 — 역시 보인다고 한다
    (설정 앱에서 데스크톱을 닫으면 이 프로세스의 기억은 그대로라, 최소화한 창에 닿을 길이 없었다)"""
    ws = c.get("workspace") or {}
    wid = ws.get("id", 0) or 0
    if wid > 0:
        return wid in shown
    if ws.get("name") == "special:min":
        h = _home.get(c.get("address"))
        return h is None or h in shown or (n is not None and h > n)
    return True
