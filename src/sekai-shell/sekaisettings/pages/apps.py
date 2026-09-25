"""기본 앱 · 설치된 앱 · 계정."""
import getpass
import os
import pwd
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..util import LOCK_NOW, run, spawn
from ..widgets import Page, button, combo, entry, icon_image, info, row

TERMINALS = [("sekai-terminal", "SekaiOS 터미널 (자동)"), ("kitty", "Kitty"), ("foot", "Foot"), ("xterm", "XTerm")]
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

def _desktop_dirs():
    """.desktop 을 찾을 폴더 — XDG 규칙의 우선순위대로: 사용자(XDG_DATA_HOME) → XDG_DATA_DIRS 차례.
    같은 이름은 앞의 것이 이긴다. 예전엔 /usr/share 를 먼저 봐서 사용자가 고치거나 숨긴 항목
    (~/.local/share/applications)과 SekaiOS 재정의(/usr/share/sekai/data)가 무시됐다."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [home] + [d for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    if "/usr/share" not in dirs:
        dirs.append("/usr/share")
    return list(dict.fromkeys(os.path.join(d, "applications") for d in dirs))


DESKTOP_DIRS = _desktop_dirs()


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
                       lambda: spawn([store.get("apps", "terminal", "sekai-terminal"),
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
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Gtk.Spinner()
        spinner.set_no_show_all(True)
        status.pack_start(spinner, False, False, 0)
        msg = Gtk.Label(label="", xalign=0)
        msg.get_style_context().add_class("notice")
        status.pack_start(msg, True, True, 0)
        box.add(status)

        # passwd 는 현재 비밀번호가 틀리면 몇 초 뒤에야 답한다 (PAM 실패 지연) — 작업 스레드에서 돌리고
        #   그동안은 칸·버튼·닫기를 막는다 (예전엔 설정 창 전체가 최대 15초 멈췄다)
        busy = {"on": False}

        def set_busy(on):
            busy["on"] = on
            for e in fields.values():
                e.set_sensitive(not on)
            for resp in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.OK):
                d.set_response_sensitive(resp, not on)
            spinner.set_visible(on)
            (spinner.start if on else spinner.stop)()

        def work(old, new):
            # 비밀번호는 표준 입력으로만 (명령줄에 넣으면 ps 로 누구나 본다)
            try:
                proc = subprocess.run(["passwd"], input=f"{old}\n{new}\n{new}\n",
                                      text=True, capture_output=True, timeout=15)
                out = (proc.stderr or proc.stdout).strip()
                GLib.idle_add(done, proc.returncode == 0, out.splitlines()[-1] if out else "변경 실패")
            except Exception as e:
                GLib.idle_add(done, False, f"변경 실패: {e}")

        def done(ok, text):
            if ok:
                spinner.stop()
                spinner.hide()
                msg.set_text("변경되었습니다.")
                GLib.timeout_add(700, lambda: (d.destroy(), False)[1])   # 닫힐 때까지 막아 둔 채로
                return False
            set_busy(False)
            msg.set_text(text)
            return False

        def on_response(_d, resp):
            if busy["on"]:
                return
            if resp != Gtk.ResponseType.OK:
                d.destroy()
                return
            old, new, new2 = (fields[k].get_text() for k in ("old", "new", "new2"))
            if new != new2:
                msg.set_text("새 비밀번호가 서로 다릅니다.")
                return
            if len(new) < 4:
                msg.set_text("비밀번호가 너무 짧습니다.")
                return
            set_busy(True)
            msg.set_text("바꾸는 중…")
            threading.Thread(target=work, args=(old, new), daemon=True).start()

        d.connect("response", on_response)
        # 도는 동안은 창 닫기(X·Esc)도 막는다 — 닫히면 결과를 보여 줄 곳이 없다
        d.connect("delete-event", lambda *_: busy["on"])
        d.show_all()

    row(s, "비밀번호 변경", "현재 비밀번호를 알아야 합니다",
        icon=["dialog-password", "changes-prevent"],
        control=button("변경…", change_pw))
    row(s, "화면 잠그기", control=button("잠그기", lambda: spawn(LOCK_NOW)))
    return p


PAGES = [
    {"id": "defaults", "title": "기본 앱",
     "icon": ["preferences-desktop-default-applications",
              "application-x-executable"],
     "build": build_defaults, "sections": ("apps",)},
    {"id": "installed", "title": "설치된 앱",
     "icon": ["applications-other", "applications-system", "view-grid-symbolic"],
     "build": build_installed},
    {"id": "account", "title": "계정",
     "icon": ["avatar-default", "system-users", "user-identity",
              "avatar-default-symbolic"],
     "build": build_account},
]
