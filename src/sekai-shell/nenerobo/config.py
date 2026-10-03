"""Nenerobo — 설정 (~/.config/sekai/nenerobo.json) 과 프로필 (GTK 없음).

프로필 = 새 탭에서 무엇을 여는가 (윈도우 터미널의 프로필처럼):
  shell   — 내 셸 (기본)
  admin   — 관리자 셸: SekaiOS 사용자 계정 컨트롤 창으로 묻고 root 셸 (pkexec)
  command — 내가 적은 명령줄
  ssh     — SSH 접속 (호스트 · 사용자 · 포트)
  ~/.ssh/config 의 Host (와일드카드 없는 것)도 "SSH: 이름" 프로필로 보인다 (끌 수 있다).
"""
import json
import os
import pwd
import shlex
import uuid

PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sekai", "nenerobo.json")
HANGUL_FONT = "NanumGothicCoding"      # 글꼴에 한글이 없으면 이것으로 (고정폭 칸에 맞는 한글)

DEFAULTS = {
    "font_family": "JetBrains Mono",
    "font_size": 11,
    "scheme": "auto",
    "cursor_shape": "ibeam",           # ibeam · block · underline
    "cursor_blink": True,
    "bold_bright": False,
    "scrollback": 10000,
    "new_tab_same_dir": True,
    "confirm_close": True,
    "copy_on_select": False,
    "show_ssh_hosts": True,
    "default_profile": "shell",
    "profiles": [],                    # 사용자가 만든 것 (command · ssh)
}
BUILTIN = [
    {"id": "shell", "name": "셸", "kind": "shell", "builtin": True},
    {"id": "admin", "name": "관리자 셸", "kind": "admin", "builtin": True},
]
CURSORS = [("ibeam", "세로 막대"), ("block", "블록"), ("underline", "밑줄")]


class Config:
    def __init__(self):
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(PATH, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            for k, v in d.items():
                if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                    self.data[k] = v
        self.data["profiles"] = [p for p in self.data["profiles"]
                                 if isinstance(p, dict) and p.get("kind") in ("command", "ssh") and p.get("id")]

    def save(self):
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def set(self, k, v):
        self.data[k] = v
        self.save()

    # ── 글꼴 ──
    def font(self):
        fam = (self["font_family"] or "Monospace").strip()
        if HANGUL_FONT.lower() not in fam.lower():
            fam += f", {HANGUL_FONT}"
        try:
            size = max(5, min(48, float(self["font_size"])))
        except (TypeError, ValueError):
            size = 11
        return f"{fam} {size:g}"

    # ── 프로필 ──
    def profiles(self):
        out = [dict(p) for p in BUILTIN]
        out[0]["name"] = f"셸 ({os.path.basename(user_shell())})"
        out += [dict(p) for p in self["profiles"]]
        if self["show_ssh_hosts"]:
            mine = {p.get("host") for p in self["profiles"] if p.get("kind") == "ssh"}
            for h in ssh_hosts():
                if h not in mine:
                    out.append({"id": f"sshcfg:{h}", "name": f"SSH: {h}", "kind": "ssh", "host": h, "auto": True})
        return out

    def profile(self, pid):
        for p in self.profiles():
            if p["id"] == pid:
                return p
        return None

    def default_profile(self):
        return self.profile(self["default_profile"]) or self.profiles()[0]

    def save_profile(self, p):
        if not p.get("id"):
            p["id"] = uuid.uuid4().hex[:12]
        lst = [x for x in self["profiles"] if x["id"] != p["id"]]
        idx = next((i for i, x in enumerate(self["profiles"]) if x["id"] == p["id"]), len(lst))
        lst.insert(idx, p)
        self.set("profiles", lst)
        return p

    def delete_profile(self, pid):
        self.set("profiles", [x for x in self["profiles"] if x["id"] != pid])
        if self["default_profile"] == pid:
            self.set("default_profile", "shell")


def user_shell():
    try:
        sh = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        sh = ""
    return sh if sh and os.access(sh, os.X_OK) else "/bin/bash"


def profile_argv(p):
    """프로필 → 실행할 argv (None 이면 내 셸)"""
    kind = p.get("kind")
    if kind == "admin":
        # SekaiOS 사용자 계정 컨트롤 창으로 묻고 root 셸 — 지금 폴더 그대로 (--keep-cwd)
        #   (/usr/bin/bash — /bin 은 usrmerge 링크라 그 경로로는 권한 창이 "패키지로 설치된 파일이 아님"이라 했다)
        sh = "/usr/bin/bash" if os.path.exists("/usr/bin/bash") else "/bin/bash"
        return ["pkexec", "--keep-cwd", sh]
    if kind == "command":
        try:
            argv = shlex.split(p.get("command") or "")
        except ValueError:
            argv = []
        return argv or None
    if kind == "ssh":
        host = (p.get("host") or "").strip()
        if not host:
            return None
        argv = ["ssh"]
        port = str(p.get("port") or "").strip()
        if port and port != "22":
            argv += ["-p", port]
        user = (p.get("user") or "").strip()
        argv.append(f"{user}@{host}" if user else host)
        return argv
    return None


def profile_desc(p):
    kind = p.get("kind")
    if kind == "shell":
        return user_shell()
    if kind == "admin":
        return "관리자 권한(root)으로 — 열 때 암호를 묻습니다"
    argv = profile_argv(p)
    out = " ".join(shlex.quote(a) for a in argv) if argv else "(명령 없음)"
    if p.get("cwd"):
        out += f"  ·  시작 폴더 {p['cwd']}"
    return out


def ssh_hosts(path=None):
    """~/.ssh/config 의 Host 이름들 (와일드카드·부정 패턴 빼고, 적힌 순서대로)"""
    path = path or os.path.expanduser("~/.ssh/config")
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "host":
                    for h in parts[1].split():
                        if not any(c in h for c in "*?!") and h not in out:
                            out.append(h)
    except OSError:
        pass
    return out
