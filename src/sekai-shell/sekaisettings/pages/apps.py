"""기본 앱 · 설치된 앱 · 계정."""
import getpass
import os
import pwd
import re
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from sekaishell import appmgr  # noqa: E402

from ..util import LOCK_NOW, run, spawn
from ..widgets import Page, button, combo, entry, icon_image, info, row

TERMINALS = [("sekai-terminal", "SekaiOS 터미널 (자동)"), ("nenerobo", "Nenerobo"), ("kitty", "Kitty"),
             ("foot", "Foot"), ("xterm", "XTerm")]
BROWSERS = [("chromium", "Chromium"), ("google-chrome", "Google Chrome"),
            ("firefox-esr", "Firefox ESR")]
FILERS = [("sekai-files", "파일 탐색기"), ("thunar", "Thunar"), ("pcmanfm", "PCManFM")]

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
SEKAI_PKG = re.compile(r"^(sekai-|sekaios-)")
FLATPAK_EXPORTS = "/var/lib/flatpak/exports/share/applications/"


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
    row(s, "스토어", "데비안 저장소의 앱 2,000여 개를 찾아 설치합니다",
        icon=["system-software-install", "package-x-generic"],
        control=button("스토어 열기", lambda: spawn("sekai-store")))
    if not shutil.which("google-chrome"):
        row(s, "Google Chrome 설치", "구글 계정 동기화가 되는 정식 크롬을 내려받습니다",
            icon=["google-chrome", "web-browser"],
            control=button("설치 도우미 열기",
                           lambda: spawn("sekai-install-chrome")))
    row(s, "설치 파일로 앱 설치", "내려받은 .deb 설치 파일을 엽니다 (파일 탐색기에서 두 번 눌러도 됩니다)",
        icon=["system-software-install", "package-x-generic"],
        control=button("파일 선택…", lambda: spawn("sekai-appinstall")))
    row(s, "터미널에서 설치 (고급)", "apt 로 직접 설치합니다",
        icon=["utilities-terminal", "terminal"],
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
    """설치된 .desktop 을 읽어 (이름, 아이콘, Exec, 파일 이름, 경로) 목록으로."""
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
                apps.append((name, icon, execc, fn, os.path.join(d, fn)))
    apps.sort(key=lambda t: t[0].lower())
    return apps


def build_installed(store):
    p = Page("설치된 앱", "이 컴퓨터에 등록된 프로그램 목록입니다. 앱을 제거해도 내 문서·설정(홈 폴더)은 지워지지 않습니다.")
    apps = _desktop_entries()

    search = Gtk.SearchEntry()
    search.set_placeholder_text("앱 이름으로 찾기")
    p.box.pack_start(search, False, False, 0)

    lb = p.section(f"전체 {len(apps)}개")
    count = [c for c in p.box.get_children() if isinstance(c, Gtk.Label)][-1]   # 방금 만든 섹션 제목
    rows = {}                                   # .desktop 경로 → 줄
    rows_count = lambda: count.set_text(f"전체 {len(rows)}개")             # noqa: E731 — 제거한 뒤 다시 센다

    for name, icon, execc, fn, path in apps:
        cmd = " ".join(w for w in execc.split() if not w.startswith("%"))
        ctl = Gtk.Box(spacing=6)
        ctl.pack_start(button("실행", lambda c=cmd: spawn(c)), False, False, 0)
        rm = button("제거")
        rm.set_no_show_all(True)                # 어느 패키지인지 알아낸 뒤에 보인다
        ctl.pack_start(rm, False, False, 0)
        r = row(lb, name, fn, icon=[icon] if icon else ["application-x-executable"], control=ctl)
        r.search_key = (name + " " + fn).lower()
        r.app_name, r.app_path, r.rm_btn, r.pkg, r.flatpak = name, path, rm, None, None
        rm.connect("clicked", lambda _b, r=r: _remove_dialog(p, r, rows, rows_count))
        rows[path] = r

    def filt(w):
        q = w.get_text().strip().lower()
        for r in lb.get_children():
            r.set_visible(not q or q in getattr(r, "search_key", ""))
    search.connect("search-changed", filt)

    # 앱마다 어느 패키지인지 (dpkg 한 번에) — 패키지가 아닌 것(직접 만든 바로 가기 등)은 제거 단추가 없다
    def find():
        found = appmgr.packages_of([x for x in rows if not x.startswith(FLATPAK_EXPORTS)])
        fps = appmgr.flatpak_installed_apps() if any(x.startswith(FLATPAK_EXPORTS) for x in rows) else {}
        GLib.idle_add(got, found, fps)

    def got(found, fps):
        # Flathub 앱 — 바로 가기가 flatpak 의 내보내기 자리에 있다 (앱 id.desktop)
        for path, r in rows.items():
            if path.startswith(FLATPAK_EXPORTS):
                fid = os.path.basename(path)[:-len(".desktop")]
                if fid in fps:
                    r.pkg = None
                    r.flatpak = f"app/{fid}/x86_64/{fps[fid]}"
                    r.rm_btn.set_tooltip_text("Flathub 앱을 제거합니다")
                    r.rm_btn.show()
        for path, pkg in found.items():
            r = rows.get(path)
            if r is not None:
                r.pkg = pkg
                if SEKAI_PKG.match(pkg):
                    # SekaiOS 의 앱 — 지우면 데스크톱이 같이 지워진다 (도우미도 막는다). 윈도우처럼 회색으로
                    r.rm_btn.set_sensitive(False)
                    r.rm_btn.set_tooltip_text("SekaiOS 기본 앱이라 제거할 수 없습니다")
                else:
                    r.rm_btn.set_tooltip_text(f"패키지 {pkg} 를 제거합니다")
                r.rm_btn.show()
        return False
    threading.Thread(target=find, daemon=True).start()
    return p


def _remove_dialog(page, r, rows, recount):
    """앱 제거 — 함께 지워지는 것을 먼저 보여 주고, [제거] 를 누르면 사용자 계정 컨트롤 → 제거."""
    pkg = r.pkg
    if not pkg and not r.flatpak:
        return
    d = Gtk.Dialog(title=f"{r.app_name} 제거", transient_for=page.get_toplevel(), modal=True)
    d.set_default_size(460, -1)
    cancel = d.add_button("취소", Gtk.ResponseType.CANCEL)
    ok = d.add_button("제거", Gtk.ResponseType.OK)
    ok.get_style_context().add_class("accent-btn")
    ok.set_sensitive(False)
    box = d.get_content_area()
    box.set_spacing(10)
    box.set_border_width(16)
    head = Gtk.Label(xalign=0)
    head.set_markup(f"<b>{GLib.markup_escape_text(r.app_name)}</b> 을(를) 이 컴퓨터에서 제거합니다.")
    head.set_line_wrap(True)
    box.add(head)
    msg = Gtk.Label(label="함께 지워지는 것을 확인하는 중…", xalign=0)
    msg.set_line_wrap(True)
    msg.set_max_width_chars(56)
    msg.set_selectable(True)
    box.add(msg)
    bar = Gtk.ProgressBar()
    bar.get_style_context().add_class("update-progress")
    bar.set_no_show_all(True)
    box.add(bar)
    state = {"busy": False, "plan": appmgr.Plan(), "err": None, "done": False}

    # 같은 패키지에서 온 다른 앱도 함께 사라진다 — 미리 알려 준다
    def others(pkgs):
        return [x.app_name for x in rows.values() if x is not r and x.pkg in pkgs]

    def planned(rc):
        pl = state["plan"]
        if pl.error or not pl.done:
            msg.set_text(pl.error or f"확인하지 못했습니다 (코드 {rc})")
            return False
        if pl.blocks:
            msg.set_text("\n".join(pl.blocks))
            ok.hide()
            cancel.set_label("닫기")
            return False
        lines = []
        apps_too = others({pkg, *pl.dele})
        if apps_too:
            lines.append("같이 설치된 앱도 함께 제거됩니다: " + ", ".join(apps_too[:8]))
        if pl.dele:
            lines.append(f"함께 제거되는 구성 요소 {len(pl.dele)}개: " + ", ".join(pl.dele[:10])
                         + (" …" if len(pl.dele) > 10 else ""))
        lines.append("내 문서와 앱 설정(홈 폴더)은 그대로 남습니다.")
        msg.set_text("\n".join(lines))
        ok.set_sensitive(True)
        return False

    if r.flatpak:
        msg.set_text("Flathub 앱과, 더 이상 쓰는 앱이 없는 실행 환경(런타임)을 함께 정리합니다.\n"
                     "내 문서와 앱 설정(홈 폴더의 .var/app)은 그대로 남습니다.")
        ok.set_sensitive(True)
    else:
        appmgr.run_helper(["plan-remove", pkg], state["plan"].feed, planned)

    def line(kind, rest):
        if kind == "PROGRESS":
            pct, _, text = rest.partition(" ")
            try:
                bar.set_fraction(max(0, min(100, int(pct))) / 100)
            except ValueError:
                pass
            msg.set_text(text)
        elif kind == "ERROR":
            state["err"] = rest
        return False

    def removed(rc):
        state["busy"] = False
        bar.hide()
        if rc in appmgr.CANCELLED and not state["err"]:
            msg.set_text("인증이 취소되어 제거하지 않았습니다.")
            ok.set_sensitive(True)
            cancel.set_sensitive(True)
            return False
        if rc != 0 or state["err"]:
            msg.set_text(state["err"] or f"제거 도우미가 비정상 종료했습니다 (코드 {rc})")
            ok.hide()
            cancel.set_sensitive(True)
            cancel.set_label("닫기")
            return False
        gone = {pkg, *state["plan"].dele} - {None}
        for x in list(rows.values()):
            if x is r or (x.pkg and x.pkg in gone):
                rows.pop(x.app_path, None)
                x.destroy()
        recount()
        d.destroy()
        return False

    def on_response(_d, resp):
        if state["busy"]:
            return
        if resp != Gtk.ResponseType.OK:
            d.destroy()
            return
        if not ok.get_sensitive():
            return
        state["busy"] = True
        ok.set_sensitive(False)
        cancel.set_sensitive(False)
        bar.set_fraction(0)
        bar.show()
        msg.set_text("관리자 인증을 기다리는 중…")
        if r.flatpak:
            appmgr.run_flatpak("remove", r.flatpak, line, removed)
        else:
            appmgr.run_helper(["remove", pkg], line, removed, repo=True)

    d.connect("response", on_response)
    d.connect("delete-event", lambda *_: state["busy"])   # 제거 중엔 닫지 않는다 (결과를 보여 줄 곳)
    d.show_all()


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
