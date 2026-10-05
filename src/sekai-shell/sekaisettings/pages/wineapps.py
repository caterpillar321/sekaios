"""설정 › Windows 앱 — Wine 으로 설치한 앱의 환경(C: 드라이브)과 엔진 (sekaiwine.core).

앱마다 환경이 하나라 "앱 제거" = 그 환경을 통째로 지우기 (설치한 프로그램·설정·바로 가기·스냅샷까지).
btrfs 면 설치 전에 떠 둔 스냅샷으로 되돌릴 수 있다.
"""
import os
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..util import human_bytes  # noqa: E402
from ..widgets import Page, button, info, row  # noqa: E402

try:
    from sekaiwine import core
except ImportError:                       # 소스 트리에서
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from sekaiwine import core


def _confirm(parent, title, text, ok_label):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                          buttons=Gtk.ButtonsType.NONE, text=title)
    d.format_secondary_text(text)
    d.add_button("취소", Gtk.ResponseType.CANCEL)
    ok = d.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("accent-btn")
    r = d.run()
    d.destroy()
    return r == Gtk.ResponseType.OK


def remove_env(parent, env, done=None):
    """확인 → 지우기 (설치된 앱 페이지도 부른다)"""
    names = ", ".join(a["name"] for a in env.get("apps", [])) or "앱 없음"
    if not _confirm(parent, f"{env['name']} 제거",
                    f"이 환경에 설치한 Windows 프로그램과 그 설정을 모두 지웁니다 ({names}). "
                    "되돌릴 수 없습니다. 내 문서 폴더의 파일은 지워지지 않습니다.", "제거"):
        return
    threading.Thread(target=lambda: (core.remove(env), done and GLib.idle_add(done)), daemon=True).start()


def _pick_installer(parent):
    d = Gtk.FileChooserDialog(title="Windows 프로그램 고르기", transient_for=parent,
                              action=Gtk.FileChooserAction.OPEN)
    d.add_button("취소", Gtk.ResponseType.CANCEL)
    d.add_button("열기", Gtk.ResponseType.OK)
    f = Gtk.FileFilter()
    f.set_name("Windows 프로그램 (.exe · .msi)")
    for pat in ("*.exe", "*.EXE", "*.msi", "*.MSI"):
        f.add_pattern(pat)
    d.add_filter(f)
    d.set_current_folder(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~"))
    path = d.get_filename() if d.run() == Gtk.ResponseType.OK else None
    d.destroy()
    if path:
        subprocess.Popen(["sekai-wine", "open", path], start_new_session=True)


def _restore_dialog(parent, env, done):
    snaps = core.snapshots(env)
    if not snaps:
        return
    d = Gtk.Dialog(title=f"{env['name']} 되돌리기", transient_for=parent, modal=True)
    d.add_button("취소", Gtk.ResponseType.CANCEL)
    ok = d.add_button("되돌리기", Gtk.ResponseType.OK)
    ok.get_style_context().add_class("accent-btn")
    box = d.get_content_area()
    box.set_spacing(10)
    box.set_border_width(16)
    lab = Gtk.Label(label="이 시점 뒤에 이 환경에서 바뀐 것(설치·설정)이 모두 사라집니다.", xalign=0)
    lab.set_line_wrap(True)
    box.add(lab)
    combo = Gtk.ComboBoxText()
    for s in snaps:
        when = s["name"].split("@", 1)[1]
        when = f"{when[:4]}-{when[4:6]}-{when[6:8]} {when[9:11]}:{when[11:13]}"
        combo.append(s["name"], f"{when} — {s['label'] or '스냅샷'}")
    combo.set_active(0)
    combo.get_accessible().set_name("되돌릴 시점")
    box.add(combo)
    d.show_all()
    r, snap = d.run(), combo.get_active_id()
    d.destroy()
    if r == Gtk.ResponseType.OK and snap:
        threading.Thread(target=lambda: (core.restore(env, snap), GLib.idle_add(done)), daemon=True).start()


def build_wineapps(store):
    p = Page("Windows 앱", "Windows 프로그램은 앱마다 따로 된 환경(C: 드라이브)에 설치됩니다. "
                         "한 앱이 망가져도 다른 앱은 그대로이고, 제거하면 흔적 없이 지워집니다.")

    def rebuild():
        if not p.get_toplevel():
            return False
        for c in p.box.get_children():
            p.box.remove(c)
        fill()
        p.box.show_all()
        return False

    def fill():
        s = p.section("설치")
        row(s, "Windows 프로그램 설치", ".exe · .msi 설치 파일을 고릅니다 (파일 탐색기에서 더블클릭해도 됩니다)",
            icon=["application-x-ms-dos-executable", "application-x-executable"],
            control=button("설치…", lambda: _pick_installer(p.get_toplevel())))

        envs = [e for e in core.envs() if e["id"] != core.QUICK]
        s = p.section(f"설치한 앱 ({len(envs)}개)")
        if not envs:
            row(s, "아직 설치한 Windows 앱이 없습니다", None, icon=["dialog-information"])
        for env in envs:
            apps = env.get("apps", [])
            sub = " · ".join(x for x in (", ".join(a["name"] for a in apps) or "바로 가기 없음",
                                          (core.engine(env["engine"]) or {"label": env["engine"] + " (없음)"})["label"]) if x)
            ctl = Gtk.Box(spacing=6)
            if apps:
                ctl.pack_start(button("실행", lambda e=env, a=apps[0]: core.spawn(e, a["lnk"])), False, False, 0)
            ctl.pack_start(button("C: 드라이브", lambda e=env: subprocess.Popen(
                ["sekai-files", os.path.join(core.prefix(e), "drive_c")], start_new_session=True)), False, False, 0)
            if core.snapshots(env):
                ctl.pack_start(button("되돌리기…", lambda e=env: _restore_dialog(p.get_toplevel(), e, rebuild)),
                               False, False, 0)
            ctl.pack_start(button("제거", lambda e=env: remove_env(p.get_toplevel(), e, rebuild)), False, False, 0)
            icon = [apps[0]["icon"]] if apps else []
            r = row(s, env["name"], sub, icon=icon + ["application-x-ms-dos-executable"], control=ctl)

            def sized(r=r, env=env, sub=sub):
                n = core.size(env)
                GLib.idle_add(lambda: (r.get_toplevel() and _set_sub(r, f"{sub} · {human_bytes(n)}"), False)[1])
            threading.Thread(target=sized, daemon=True).start()

        s = p.section("엔진")
        engs = core.engines()
        if not engs:
            row(s, "Wine 엔진이 없습니다", "sekai-wine-11.0 패키지를 설치하세요", icon=["dialog-warning"])
        for e in engs:
            used = sum(1 for x in envs if x["engine"] == e["id"])
            row(s, e["label"], ("앱용 (WoW64 — 32비트 앱도)" if e["kind"] == "wine" else "게임용 (Proton)") +
                (f" · 앱 {used}개가 씀" if used else ""), icon=["application-x-executable"])
        if not core.btrfs():
            p.add_widget(info("이 PC 의 저장소가 btrfs 가 아니라 설치 전 스냅샷(되돌리기)은 쓸 수 없습니다."))

    fill()
    return p


def _set_sub(r, text):
    """row 의 부제목 라벨을 바꾼다 (row 가 만든 두 번째 라벨)"""
    labels = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            labels.append(w)
        elif isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    walk(r)
    if len(labels) >= 2:
        labels[1].set_text(text)


PAGES = [
    {"id": "wineapps", "title": "Windows 앱",
     "icon": ["wine", "application-x-ms-dos-executable", "applications-other"],
     "build": build_wineapps},
]
