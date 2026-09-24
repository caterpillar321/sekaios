"""그래픽 — 그래픽 카드와 드라이버, 지금 화면이 어떤 방식으로 그려지는지.

NVIDIA 카드가 있으면 NVIDIA 공식 드라이버를 설치·제거할 수 있다.
관리자 권한이 필요한 일은 /usr/libexec/sekai/sekai-gpu 가 한다 (pkexec).
이 페이지는 그 출력(GPU/SECUREBOOT/NVIDIA/MOK/PROGRESS/MOKPASS/REBOOT/ERROR 줄)을 읽어 보여 줄 뿐이다.
"""
import os
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..widgets import Page, button, info, row

HELPER = "/usr/libexec/sekai/sekai-gpu"

VENDOR_NAME = {"nvidia": "NVIDIA", "intel": "Intel", "amd": "AMD", "vmware": "VMware",
               "virtio": "가상 GPU", "qemu": "가상 GPU"}
DRIVER_NAME = {"nvidia": "NVIDIA 드라이버", "nouveau": "nouveau (기본 드라이버)",
               "i915": "Intel (i915)", "xe": "Intel (xe)", "amdgpu": "AMD (amdgpu)",
               "radeon": "AMD (radeon)", "vmwgfx": "VMware SVGA", "virtio-pci": "virtio",
               "virtio_gpu": "virtio", "bochs-drm": "기본 VGA", "simpledrm": "펌웨어 화면"}


def _status():
    try:
        res = subprocess.run([HELPER, "status"], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    st = {"gpus": [], "sb": False, "nvidia": None, "mok": "-"}
    for line in res.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "GPU":
            parts = rest.split(" ", 3)
            if len(parts) == 4:
                st["gpus"].append(tuple(parts))
        elif kind == "SECUREBOOT":
            st["sb"] = rest.strip() == "on"
        elif kind == "NVIDIA":
            st["nvidia"] = None if rest.strip() == "-" else rest.strip()
        elif kind == "MOK":
            st["mok"] = rest.strip()
    return st


def _mode():
    """(제목, 설명) — 지금 화면을 그리는 방식"""
    # 기본 화면 모드 = X11 세션 (환경 변수가 안 넘어온 경우에도 알 수 있게 Wayland 여부로도 본다)
    if os.environ.get("SEKAI_BASIC") or not os.environ.get("WAYLAND_DISPLAY"):
        why = os.environ.get("SEKAI_BASIC_REASON")
        return ("기본 화면 모드",
                "그래픽 드라이버 없이 펌웨어 화면으로 그립니다. 창 효과가 없고 해상도를 바꿀 수 없습니다."
                + (" (그래픽 초기화에 실패해서 이 모드로 시작했습니다)" if why == "crash" else ""))
    if os.environ.get("SEKAI_SOFTWARE_RENDER"):
        return ("CPU 렌더링",
                "그래픽 카드의 3D 가속을 쓸 수 없어 CPU 로 그립니다. 동작하지만 느릴 수 있습니다.")
    return ("하드웨어 가속", "그래픽 카드가 화면을 그립니다.")


class GraphicsPage:
    def __init__(self, store):
        self.busy = False
        self.st = None
        self.p = Page("그래픽", "그래픽 카드와 드라이버를 확인하고, NVIDIA 드라이버를 설치합니다.")

        s = self.p.section()
        t, sub = _mode()
        row(s, t, sub, icon=["video-display", "preferences-desktop-display"],
            control=info("지금 화면"))

        self.gpu_box = self.p.section("그래픽 카드")
        self.gpu_loading = row(self.gpu_box, "찾는 중…")

        # ── NVIDIA ──
        self.nv_title = Gtk.Label(label="NVIDIA 드라이버", xalign=0)
        self.nv_title.get_style_context().add_class("section-title")
        self.nv_title.set_no_show_all(True)
        self.p.add_widget(self.nv_title)
        self.nv = Gtk.ListBox()
        self.nv.set_selection_mode(Gtk.SelectionMode.NONE)
        self.nv.get_style_context().add_class("section")
        self.nv.set_no_show_all(True)
        self.p.add_widget(self.nv)

        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.install_btn = button("설치", lambda: self._run("install-nvidia"), cls="accent-btn")
        self.remove_btn = button("제거", lambda: self._run("remove-nvidia"))
        self.mok_btn = button("비밀번호 다시 만들기", lambda: self._run("enroll-mok"))
        self.reboot_btn = button("다시 시작", lambda: subprocess.Popen(["systemctl", "reboot"]),
                                 cls="accent-btn")
        for b in (self.install_btn, self.remove_btn, self.mok_btn, self.reboot_btn):
            b.set_no_show_all(True)
            ctl.pack_start(b, False, False, 0)
        self.nv_row = row(self.nv, "확인하는 중…", " ",
                          icon=["nvidia-settings", "nvidia", "video-display"], control=ctl)

        self.progress = Gtk.ProgressBar()
        self.progress.get_style_context().add_class("update-progress")
        self.progress.set_no_show_all(True)
        self.p.add_widget(self.progress)
        self.progress_text = Gtk.Label(xalign=0)
        self.progress_text.get_style_context().add_class("row-sub")
        self.progress_text.set_ellipsize(Pango.EllipsizeMode.END)
        self.progress_text.set_no_show_all(True)
        self.p.add_widget(self.progress_text)

        # 보안 부팅 키 등록 안내 — 비밀번호는 설치할 때 한 번만 보여 준다
        self.mok_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.mok_card.get_style_context().add_class("mok-card")
        self.mok_card.set_no_show_all(True)
        self.mok_card.set_margin_top(8)
        head = Gtk.Label(xalign=0)
        head.set_markup("<b>다시 시작하면 파란 화면(MOK 관리)이 한 번 뜹니다</b>")
        head.get_style_context().add_class("row-title")
        self.mok_pw = Gtk.Label(xalign=0, selectable=True)
        self.mok_pw.get_style_context().add_class("mok-password")
        steps = Gtk.Label(xalign=0)
        steps.set_line_wrap(True)
        steps.get_style_context().add_class("row-sub")
        steps.set_text(
            "보안 부팅이 켜져 있어서, 이 컴퓨터에서 만든 드라이버를 믿도록 펌웨어에 등록해야 합니다.\n"
            "  1. 아무 키나 눌러 “Perform MOK management” 화면으로 들어갑니다 (10초 안에)\n"
            "  2. Enroll MOK → Continue → Yes 를 고릅니다\n"
            "  3. 비밀번호에 위 숫자 8자리를 넣습니다 (화면에 표시되지 않습니다)\n"
            "  4. Reboot 를 고르면 끝입니다\n"
            "휴대폰으로 이 화면을 찍어 두세요. 놓쳤다면 여기서 ‘비밀번호 다시 만들기’를 누르면 됩니다.")
        for w in (head, self.mok_pw, steps):
            w.set_margin_start(16)
            w.set_margin_end(16)
            self.mok_card.pack_start(w, False, False, 0)
        head.set_margin_top(12)
        steps.set_margin_bottom(12)
        self.p.add_widget(self.mok_card)

        s = self.p.section("정보")
        self.sb_label = info("…")
        row(s, "보안 부팅", "켜져 있으면 직접 만든 드라이버는 등록한 키로 서명되어야 올라갑니다",
            control=self.sb_label)
        row(s, "드라이버 출처",
            "NVIDIA 가 운영하는 데비안 13 공식 저장소 (developer.download.nvidia.com). "
            "설치를 누를 때만 추가됩니다.",
            control=info("NVIDIA 공식"))

        threading.Thread(target=self._load, daemon=True).start()

    @property
    def widget(self):
        return self.p

    # ── 상태 표시 ──
    def _load(self):
        st = _status()
        GLib.idle_add(self._show, st)

    def _show(self, st):
        self.st = st
        for r in self.gpu_box.get_children():
            self.gpu_box.remove(r)
        if st is None:
            row(self.gpu_box, "그래픽 도우미가 없습니다", HELPER)
            self.gpu_box.show_all()
            return False
        if not st["gpus"]:
            row(self.gpu_box, "그래픽 카드를 찾지 못했습니다")
        for slot, ven, drv, name in st["gpus"]:
            d = DRIVER_NAME.get(drv, drv) if drv != "-" else "드라이버 없음 — 펌웨어 화면으로 표시 중"
            row(self.gpu_box, name, f"{VENDOR_NAME.get(ven, ven)} · {d}",
                icon=["video-display", "preferences-desktop-display"], control=info(slot))
        self.gpu_box.show_all()
        self.sb_label.set_text("켜짐" if st["sb"] else "꺼짐")

        has_nv = any(v == "nvidia" for _s, v, _d, _n in st["gpus"])
        for w in (self.nv_title, self.nv):
            w.set_visible(has_nv or bool(st["nvidia"]))
        # no_show_all 이 켜진 목록은 show_all 이 안쪽으로 내려가지 않는다 → 행을 직접
        self.nv_row.show_all()
        self._nv_state()
        return False

    def _set(self, title, sub=""):
        self.nv_row.title_label.set_text(title)
        self.nv_row.sub_label.set_text(sub)

    def _nv_state(self):
        st = self.st or {}
        loaded = any(d == "nvidia" for _s, v, d, _n in st.get("gpus", []))
        ver = st.get("nvidia")
        for b in (self.install_btn, self.remove_btn, self.mok_btn, self.reboot_btn):
            b.hide()
        if self.busy:
            return
        if not ver:
            self._set("설치되어 있지 않음",
                      "설치하면 3D 가속·고해상도·여러 모니터를 쓸 수 있습니다. 인터넷 연결이 필요하고 "
                      "10분쯤 걸립니다." + (" 보안 부팅 키 등록을 위해 다시 시작할 때 한 번 입력이 필요합니다."
                                          if st.get("sb") else ""))
            self.install_btn.show()
        elif loaded:
            self._set(f"사용 중 — 버전 {ver}", "NVIDIA 드라이버가 그래픽 카드를 쓰고 있습니다.")
            self.remove_btn.show()
        else:
            sub = "다시 시작하면 적용됩니다."
            if st.get("mok") == "pending":
                sub = "다시 시작할 때 파란 화면에서 키를 등록하면 적용됩니다."
                self.mok_btn.show()
            elif st.get("mok") == "missing":
                sub = "보안 부팅 키가 등록되지 않아 드라이버가 올라가지 못했습니다. 비밀번호를 만들고 다시 시작해 주세요."
                self.mok_btn.show()
            self._set(f"설치됨 — 버전 {ver} (아직 쓰이지 않음)", sub)
            self.reboot_btn.show()
            self.remove_btn.show()

    # ── 도우미 실행 ──
    def _run(self, action):
        if self.busy:
            return
        self.busy = True
        self._nv_state()
        self.progress.set_fraction(0)
        self.progress.show()
        self.progress_text.set_text("관리자 인증을 기다리는 중…")
        self.progress_text.show()
        self._set({"install-nvidia": "NVIDIA 드라이버를 설치하는 중…",
                   "remove-nvidia": "NVIDIA 드라이버를 지우는 중…",
                   "enroll-mok": "보안 부팅 키 등록을 예약하는 중…"}[action],
                  "창을 닫아도 작업은 계속됩니다")
        self._got = {"reboot": False, "error": None, "pw": None}
        threading.Thread(target=self._worker, args=(action,), daemon=True).start()

    def _worker(self, action):
        try:
            p = subprocess.Popen(["pkexec", HELPER, action], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            GLib.idle_add(self._finish, action, f"실행하지 못했습니다: {e}")
            return
        for line in p.stdout:
            GLib.idle_add(self._line, line.rstrip("\n"))
        rc = p.wait()
        GLib.idle_add(self._finish, action, "인증이 취소되었습니다" if rc in (126, 127) else None)

    def _line(self, line):
        kind, _, rest = line.partition(" ")
        if kind == "PROGRESS":
            pct, _, text = rest.partition(" ")
            try:
                self.progress.set_fraction(max(0, min(100, int(pct))) / 100)
            except ValueError:
                pass
            self.progress_text.set_text(text)
        elif kind == "MOKPASS":
            self._got["pw"] = rest.strip()
        elif kind == "REBOOT":
            self._got["reboot"] = True
        elif kind == "ERROR":
            self._got["error"] = rest
        return False

    def _finish(self, action, err):
        self.busy = False
        self.progress.hide()
        self.progress_text.hide()
        err = err or self._got["error"]
        pw = self._got["pw"]
        if pw:
            self.mok_pw.set_markup(f"비밀번호  <span size='xx-large' weight='bold' "
                                   f"letter_spacing='4000'>{pw[:4]} {pw[4:]}</span>")
            self.mok_card.set_no_show_all(False)      # no_show_all 이면 show_all 이 무시된다
            self.mok_card.show_all()
        st = _status()
        if st:
            self.st = st
        self._nv_state()
        if err:
            self._set("완료하지 못했습니다", err)
        elif action == "install-nvidia":
            self._set("설치했습니다 — 다시 시작하면 적용됩니다",
                      "다시 시작할 때 아래 안내대로 키를 등록해 주세요." if pw else
                      "다시 시작하면 NVIDIA 드라이버로 화면이 그려집니다.")
        elif action == "remove-nvidia":
            self._set("지웠습니다 — 다시 시작하면 기본 드라이버로 돌아갑니다")
        if self._got["reboot"] and not err:
            self.install_btn.hide()
            self.reboot_btn.show()
        return False


def build(store):
    return GraphicsPage(store).widget


PAGES = [{"id": "graphics", "title": "그래픽",
          "icon": ["hardinfo", "video-display", "preferences-desktop-display"],
          "build": build}]
