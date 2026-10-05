"""설정 › 장치 암호화 — BitLocker 처럼 (드라이브 암호화는 설치할 때 켠다. 여기서는 키와 잠금 방식을 관리).

상태는 /etc/sekai/encryption.conf (누구나 읽기) 와 장치로 보고, 바꾸는 것은 root 도우미 sekai-encryption 을
pkexec 로 (복구 키를 보여 주므로 매번 인증). PIN 은 표준 입력으로 넘긴다 (인자는 ps 에 보인다).
"""
import glob
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..widgets import Page, button, info, row  # noqa: E402

HELPER = "/usr/libexec/sekai/sekai-encryption"
CONF = "/etc/sekai/encryption.conf"


def _conf():
    d = {}
    try:
        with open(CONF) as f:
            for ln in f:
                k, _, v = ln.strip().partition("=")
                d[k] = v
    except OSError:
        pass
    return d


def _secure_boot():
    for f in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        try:
            with open(f, "rb") as fh:
                return fh.read()[4:5] == b"\x01"
        except OSError:
            pass
    return False


def _tpm():
    return os.path.exists("/dev/tpmrm0") or os.path.exists("/dev/tpm0")


def helper(args, done, stdin=None):
    """pkexec 도우미를 백그라운드로 — done(성공, 출력, 오류)"""
    flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
    if stdin is not None:
        flags |= Gio.SubprocessFlags.STDIN_PIPE
    try:
        p = Gio.Subprocess.new(["pkexec", HELPER] + args, flags)
    except GLib.Error as e:
        msg = e.message
        GLib.idle_add(lambda: (done(False, "", msg), False)[1])
        return

    def fin(proc, res):
        try:
            _ok, out, err = proc.communicate_utf8_finish(res)
        except GLib.Error as e:
            out, err = "", e.message
        done(proc.get_successful(), out or "", err or "")
    p.communicate_utf8_async(stdin, None, fin)


def _reason(err, fallback):
    lines = [ln.replace("sekai-encryption: ", "") for ln in (err or "").splitlines() if ln.strip()]
    if any("dismissed" in ln or "Not authorized" in ln for ln in lines):
        return "인증을 취소했습니다"
    return lines[-1] if lines else fallback


def _show_key(parent, key, title="복구 키"):
    d = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    d.add_button("파일로 저장…", 1)
    d.add_button("닫기", Gtk.ResponseType.CLOSE)
    box = d.get_content_area()
    box.set_spacing(12)
    box.set_border_width(18)
    lab = Gtk.Label(label="부팅 화면에서 드라이브를 열 때 쓰는 48자리 키입니다. 이 PC 가 아닌 곳에 보관하세요.", xalign=0)
    lab.set_line_wrap(True)
    lab.set_max_width_chars(52)
    box.add(lab)
    k = Gtk.Label(label=key)
    k.set_selectable(True)
    k.get_style_context().add_class("recovery-key")
    k.set_markup(f"<span font_family='monospace' size='x-large' weight='bold'>{GLib.markup_escape_text(key)}</span>")
    k.get_accessible().set_name(f"복구 키 {key}")
    box.add(k)
    d.show_all()
    while d.run() == 1:
        fc = Gtk.FileChooserDialog(title="복구 키 저장", transient_for=d, action=Gtk.FileChooserAction.SAVE)
        fc.add_button("취소", Gtk.ResponseType.CANCEL)
        fc.add_button("저장", Gtk.ResponseType.OK)
        fc.set_do_overwrite_confirmation(True)
        fc.set_current_name("SekaiOS-복구-키.txt")
        if fc.run() == Gtk.ResponseType.OK:
            try:
                with open(fc.get_filename(), "w", encoding="utf-8") as f:
                    f.write("SekaiOS 드라이브 암호화 복구 키\n\n" + key + "\n")
            except OSError as e:
                lab.set_text(f"저장하지 못했습니다: {e}")
        fc.destroy()
    d.destroy()


def _ask_pin(parent):
    d = Gtk.Dialog(title="PIN 설정", transient_for=parent, modal=True)
    d.add_button("취소", Gtk.ResponseType.CANCEL)
    ok = d.add_button("설정", Gtk.ResponseType.OK)
    ok.get_style_context().add_class("accent-btn")
    ok.set_sensitive(False)
    box = d.get_content_area()
    box.set_spacing(10)
    box.set_border_width(18)
    box.add(Gtk.Label(label="켤 때 입력할 PIN (숫자·영문 4~32자)", xalign=0))
    a, b = Gtk.Entry(visibility=False), Gtk.Entry(visibility=False)
    a.get_accessible().set_name("PIN")
    b.get_accessible().set_name("PIN 확인")
    a.set_placeholder_text("PIN")
    b.set_placeholder_text("PIN 확인")
    for e in (a, b):
        box.add(e)

    def chk(*_):
        import re
        v = a.get_text()
        ok.set_sensitive(bool(re.fullmatch(r"[0-9A-Za-z]{4,32}", v)) and v == b.get_text())
    a.connect("changed", chk)
    b.connect("changed", chk)
    d.show_all()
    pin = a.get_text() if d.run() == Gtk.ResponseType.OK else None
    d.destroy()
    return pin


def build_encryption(store):
    p = Page("장치 암호화", "드라이브를 암호화하면 컴퓨터를 잃어버려도 남이 파일을 읽지 못합니다.")

    def rebuild():
        for c in p.box.get_children():
            p.box.remove(c)
        fill()
        p.box.show_all()
        return False

    def say(text):
        status.set_text(text)

    def act(args, ok_text, stdin=None, show_out=False):
        p.busy = True
        say("바꾸는 중… (관리자 인증)")

        def done(ok, out, err):
            p.busy = False
            if not ok:
                say(_reason(err, "바꾸지 못했습니다"))
                return
            say(ok_text)
            if show_out and out.strip():
                _show_key(p.get_toplevel(), out.strip().splitlines()[-1], "새 복구 키")
            GLib.idle_add(rebuild)
        helper(args, done, stdin)

    def fill():
        nonlocal status
        c = _conf()
        s = p.section("상태")
        if not c.get("LUKS_UUID"):
            row(s, "드라이브 암호화 꺼짐", "SekaiOS 를 설치할 때만 켤 수 있습니다 (다시 설치 — 설치 화면의 \"드라이브 암호화\")",
                icon=["changes-allow", "security-low"])
            status = Gtk.Label()
            return
        tpm_on, pin_on = c.get("TPM") == "1", c.get("PIN") == "1"
        how = ("TPM + PIN — 켤 때 PIN 을 묻습니다" if pin_on else "TPM — 켤 때 묻지 않습니다") if tpm_on else \
            "켤 때 암호(또는 복구 키)를 묻습니다"
        row(s, "드라이브 암호화 켜짐", how, icon=["changes-prevent", "security-high"])
        sb, tpm = _secure_boot(), _tpm()
        if not tpm:
            p.add_widget(info("이 PC 에서 TPM 2.0 을 찾지 못했습니다."))
        elif not sb:
            p.add_widget(info("보안 부팅이 꺼져 있습니다 — TPM 으로 저절로 열 수 없어 켤 때마다 복구 키를 묻습니다. "
                              "펌웨어 설정에서 보안 부팅을 켠 뒤 [다시 봉인]을 누르세요."))

        s = p.section("복구 키")
        row(s, "복구 키 보기", "보안 부팅을 끄거나 메인보드가 바뀌면 이 키로 엽니다",
            icon=["dialog-password", "changes-prevent"],
            control=button("보기", lambda: helper(["recovery-key"], lambda ok, out, err:
                                                   _show_key(p.get_toplevel(), out.strip()) if ok and out.strip()
                                                   else say(_reason(err, "보지 못했습니다")))))
        row(s, "새 복구 키 만들기", "예전 키는 더는 쓸 수 없게 됩니다 (키가 새어 나갔을 때)",
            icon=["view-refresh", "system-reboot"],
            control=button("새로 만들기", lambda: act(["recovery-new"], "새 복구 키를 만들었습니다", show_out=True)))

        if tpm and sb:
            s = p.section("잠금 방식")
            if tpm_on:
                if pin_on:
                    row(s, "PIN 끄기", "켤 때 PIN 을 묻지 않습니다 (TPM 만)", icon=["dialog-password"],
                        control=button("끄기", lambda: act(["tpm-enroll"], "PIN 을 껐습니다")))
                else:
                    def pin_on_click():
                        pin = _ask_pin(p.get_toplevel())
                        if pin:
                            act(["tpm-enroll", "--pin"], "PIN 을 켰습니다 — 다음 부팅부터 묻습니다", stdin=pin + "\n")
                    row(s, "PIN 켜기", "켤 때 PIN 을 물어 부팅 USB 로 우회하는 길을 막습니다 (더 안전)",
                        icon=["dialog-password"], control=button("켜기", pin_on_click))
                row(s, "다시 봉인", "보안 부팅·펌웨어 설정을 바꾼 뒤 — 지금 상태로 TPM 에 다시 묶습니다",
                    icon=["view-refresh"],
                    control=button("다시 봉인", lambda: act(["tpm-enroll"] + (["--pin"] if pin_on else []),
                                                         "다시 봉인했습니다") if not pin_on else pin_reseal()))
            else:
                row(s, "TPM 으로 잠그기", "켤 때 암호를 묻지 않게 보안 칩에 봉인합니다", icon=["security-high"],
                    control=button("켜기", lambda: act(["tpm-enroll"], "TPM 으로 잠갔습니다")))

        def pin_reseal():
            pin = _ask_pin(p.get_toplevel())
            if pin:
                act(["tpm-enroll", "--pin"], "다시 봉인했습니다", stdin=pin + "\n")

        status = Gtk.Label(xalign=0)
        status.get_style_context().add_class("dim-label")
        p.add_widget(status)

    status = None
    fill()
    return p


PAGES = [
    {"id": "encryption", "title": "장치 암호화",
     "icon": ["security-high", "changes-prevent", "drive-harddisk-system"],
     "build": build_encryption},
]
