"""기본 앱 · 설치된 앱 · 계정."""
import getpass
import os
import pwd
import shutil
import subprocess

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import run, spawn
from ..widgets import Page, button, combo, entry, icon_image, info, row

TERMINALS = [("foot", "Foot"), ("xterm", "XTerm")]
BROWSERS = [("chromium", "Chromium"), ("google-chrome", "Google Chrome"),
            ("firefox-esr", "Firefox ESR")]
FILERS = [("thunar", "Thunar"), ("pcmanfm", "PCManFM")]

def _name_keys():
    lang = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").split(".")[0]
    keys = []
    if lang and lang not in ("C", "POSIX"):
        keys.append(f"Name[{lang}]")
        if "_" in lang:
            keys.append(f"Name[{lang.split('_')[0]}]")
    return keys


_NAME_KEYS = _name_keys()

DESKTOP_DIRS = ["/usr/share/applications",
                os.path.expanduser("~/.local/share/applications")]


def _available(items):
    """실제로 설치된 것만 남긴다. 하나도 없으면 원본을 그대로 돌려준다."""
    out = [(k, v) for k, v in items if shutil.which(k)]
    return out or items


def build_defaults(store):
    p = Page("기본 앱", "파일과 링크를 열 때 쓸 프로그램을 정합니다.")
    a = store.get("apps")

    s = p.section("기본으로 사용할 앱")
    row(s, "터미널", "Super+Enter 로 열리는 프로그램",
        icon=["utilities-terminal", "terminal"],
        control=combo(_available(TERMINALS), a["terminal"],
                      lambda v: store.set("apps", "terminal", v)))
    row(s, "웹 브라우저", None,
        icon=["web-browser", "internet-web-browser"],
        control=combo(_available(BROWSERS), a["browser"],
                      lambda v: (store.set("apps", "browser", v),
                                 _set_default_browser(v))))
    row(s, "파일 관리자", None,
        icon=["system-file-manager", "folder"],
        control=combo(_available(FILERS), a["files"],
                      lambda v: store.set("apps", "files", v)))

    s = p.section("추가 설치")
    if not shutil.which("google-chrome"):
        row(s, "Google Chrome 설치", "구글 계정 동기화가 되는 정식 크롬을 내려받습니다",
            icon=["google-chrome", "web-browser"],
            control=button("설치 도우미 열기",
                           lambda: spawn("sekai-install-chrome")))
    row(s, "소프트웨어 설치", "터미널에서 apt 로 설치합니다",
        icon=["system-software-install", "package-x-generic"],
        control=button("터미널 열기",
                       lambda: spawn([store.get("apps", "terminal", "foot"),
                                      "-e", "bash", "-lc",
                                      "echo '예: sudo apt install <패키지>'; exec bash"])))
    return p


def _set_default_browser(cmd):
    """xdg-settings 로 기본 브라우저를 등록한다 (.desktop 이 있을 때만)."""
    for name in (f"{cmd}.desktop", f"{cmd}-browser.desktop"):
        for d in DESKTOP_DIRS:
            if os.path.exists(os.path.join(d, name)):
                run(["xdg-settings", "set", "default-web-browser", name])
                return


def _desktop_entries():
    """설치된 .desktop 을 읽어 (이름, 아이콘, Exec) 목록으로."""
    apps, seen = [], set()
    for d in DESKTOP_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".desktop") or fn in seen:
                continue
            seen.add(fn)
            name = icon = execc = None
            loc_names = {}
            nodisplay = False
            try:
                with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as f:
                    in_entry = False
                    for line in f:
                        line = line.strip()
                        if line.startswith("["):
                            in_entry = line == "[Desktop Entry]"
                            continue
                        if not in_entry or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k == "Name" and not name:
                            name = v
                        elif k in _NAME_KEYS and v:
                            loc_names[k] = v
                        elif k == "Icon" and not icon:
                            icon = v
                        elif k == "Exec" and not execc:
                            execc = v
                        elif k in ("NoDisplay", "Hidden") and v.lower() == "true":
                            nodisplay = True
            except Exception:
                continue
            for key in _NAME_KEYS:              # 세션 언어의 이름이 있으면 그걸로
                if key in loc_names:
                    name = loc_names[key]
                    break
            if name and execc and not nodisplay:
                apps.append((name, icon, execc, fn))
    apps.sort(key=lambda t: t[0].lower())
    return apps


def build_installed(store):
    p = Page("설치된 앱", "이 컴퓨터에 등록된 프로그램 목록입니다.")
    apps = _desktop_entries()

    search = Gtk.SearchEntry()
    search.set_placeholder_text("앱 이름으로 찾기")
    p.box.pack_start(search, False, False, 0)

    lb = p.section(f"전체 {len(apps)}개")

    for name, icon, execc, fn in apps:
        cmd = " ".join(w for w in execc.split() if not w.startswith("%"))
        r = row(lb, name, fn, icon=[icon] if icon else ["application-x-executable"],
                control=button("실행", lambda c=cmd: spawn(c)))
        r.search_key = (name + " " + fn).lower()

    def filt(w):
        q = w.get_text().strip().lower()
        for r in lb.get_children():
            r.set_visible(not q or q in getattr(r, "search_key", ""))
    search.connect("search-changed", filt)
    return p


def build_account(store):
    p = Page("계정", "로그인 계정 정보입니다.")
    user = getpass.getuser()
    try:
        ent = pwd.getpwnam(user)
        real = (ent.pw_gecos or "").split(",")[0] or user
        home, shell, uid = ent.pw_dir, ent.pw_shell, ent.pw_uid
    except Exception:
        real, home, shell, uid = user, os.path.expanduser("~"), "-", os.getuid()

    s = p.section("내 계정")
    row(s, "사용자 이름", icon=["avatar-default", "user-identity"], control=info(user))
    row(s, "표시 이름", control=info(real))
    row(s, "UID", control=info(str(uid)))
    row(s, "홈 디렉터리", control=info(home))
    row(s, "로그인 셸", control=info(shell))
    groups = run(["id", "-nG"]).strip()
    row(s, "그룹", control=info(groups or "-"))
    row(s, "관리자 권한", control=info("있음" if "sudo" in groups.split() else "없음"))

    s = p.section("보안")

    def change_pw():
        win = p.get_toplevel()
        d = Gtk.Dialog(title="비밀번호 변경", transient_for=win, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "변경", Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        fields = {}
        for key, label in (("old", "현재 비밀번호"), ("new", "새 비밀번호"),
                           ("new2", "새 비밀번호 확인")):
            box.add(Gtk.Label(label=label, xalign=0))
            e = Gtk.Entry()
            e.set_visibility(False)
            box.add(e)
            fields[key] = e
        msg = Gtk.Label(label="", xalign=0)
        msg.get_style_context().add_class("notice")
        box.add(msg)
        d.show_all()

        while True:
            if d.run() != Gtk.ResponseType.OK:
                break
            old, new, new2 = (fields[k].get_text() for k in ("old", "new", "new2"))
            if new != new2:
                msg.set_text("새 비밀번호가 서로 다릅니다.")
                continue
            if len(new) < 4:
                msg.set_text("비밀번호가 너무 짧습니다.")
                continue
            try:
                proc = subprocess.run(["passwd"], input=f"{old}\n{new}\n{new}\n",
                                      text=True, capture_output=True, timeout=15)
                if proc.returncode == 0:
                    msg.set_text("변경되었습니다.")
                    GLib.timeout_add(700, lambda: (d.destroy(), False)[1])
                    return
                msg.set_text((proc.stderr or proc.stdout).strip().splitlines()[-1:][0]
                             if (proc.stderr or proc.stdout).strip() else "변경 실패")
            except Exception as e:
                msg.set_text(f"변경 실패: {e}")
        d.destroy()

    row(s, "비밀번호 변경", "현재 비밀번호를 알아야 합니다",
        icon=["dialog-password", "changes-prevent"],
        control=button("변경…", change_pw))
    row(s, "화면 잠그기", control=button("잠그기",
                                     lambda: spawn("sekai-lock")))
    return p


PAGES = [
    {"id": "defaults", "title": "기본 앱",
     "icon": ["preferences-desktop-default-applications",
              "application-x-executable"],
     "build": build_defaults},
    {"id": "installed", "title": "설치된 앱",
     "icon": ["applications-other", "applications-system", "view-grid-symbolic"],
     "build": build_installed},
    {"id": "account", "title": "계정",
     "icon": ["avatar-default", "system-users", "user-identity",
              "avatar-default-symbolic"],
     "build": build_account},
]
