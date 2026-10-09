"""바탕 화면 폴더와 실행기(.desktop) — 바탕 화면(sekai-desk)·파일 탐색기·작업 표시줄이 함께 쓴다.

실행기를 믿는 기준은 Thunar·GNOME 과 같다: 실행 권한이 있고 내 파일이며 신뢰 표시(metadata::trusted, 또는 Thunar 의
metadata::xfce-exe-checksum 이 지금 내용과 맞음)가 있어야 한다. 시스템 앱 폴더에 root 가 둔 것은 믿는다.
브라우저로 받은 '청구서.pdf.desktop' 은 어느 것도 아니다.
예전에는 바탕 화면과 파일 탐색기가 따로 들고 있었고, 바탕 화면 쪽은 시스템 앱 규칙이 없었다 (카나데)."""
import hashlib
import os
import stat
import subprocess

from gi.repository import Gio, GLib

from . import dbg

SYSTEM_APP_DIRS = [os.path.join(d, "applications") for d in GLib.get_system_data_dirs()] +     ["/var/lib/flatpak/exports/share/applications"]


def desktop_dir(create=True):
    """바탕 화면 폴더 (xdg-user-dir DESKTOP, 없거나 홈이면 ~/Desktop)"""
    try:
        p = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                           text=True, timeout=2).stdout.strip()
    except Exception:
        p = ""
    if not p or p == os.path.expanduser("~"):
        p = os.path.expanduser("~/Desktop")
    if create:
        os.makedirs(p, exist_ok=True)
    return p


def launcher_trusted(path):
    """실행 권한이 있고 내 파일이며 신뢰 표시(metadata::trusted 또는 Thunar 의 체크섬)가 있어야 한다.
    시스템 앱 폴더에 root 가 둔 것은 믿는다. 브라우저로 받은 '청구서.pdf.desktop' 은 어느 것도 아니다."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    real = os.path.realpath(path)
    if st.st_uid == 0 and any(real.startswith(d.rstrip("/") + "/") for d in SYSTEM_APP_DIRS):
        return True
    if st.st_uid != os.getuid() or not os.access(path, os.X_OK):
        return False
    try:
        info = Gio.File.new_for_path(path).query_info(
            "metadata::trusted,metadata::xfce-exe-checksum", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    if info.get_attribute_as_string("metadata::trusted") == "true":
        return True
    want = info.get_attribute_as_string("metadata::xfce-exe-checksum")
    if not want or st.st_size > 1 << 20:
        return False
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == want
    except OSError:
        return False


def mark_trusted(path):
    """'신뢰하고 실행' — 읽을 수 있는 쪽에만 실행 권한 + 신뢰 표시 """
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, stat.S_IMODE(mode) | stat.S_IXUSR | ((mode & 0o044) >> 2))
    except OSError as e:
        dbg("실행 권한을 못 붙임", path, e)
    try:
        Gio.File.new_for_path(path).set_attribute_string(
            "metadata::trusted", "true", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error as e:
        dbg("신뢰 표시를 못 붙임", path, e)


def launcher_command(path):
    try:
        if os.path.getsize(path) > 1 << 20:
            return ""
        kf = GLib.KeyFile()
        kf.load_from_file(path, GLib.KeyFileFlags.NONE)
        return kf.get_string("Desktop Entry", "Exec")
    except (OSError, GLib.Error):
        return ""
