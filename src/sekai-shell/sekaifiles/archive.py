"""압축 — 압축 풀기와 "압축(ZIP) 파일로 보내기" (윈도우 11 탐색기처럼).

is_archive(path, content_type) -> bool
    탐색기가 두 번 클릭·메뉴에서 쓸지 정할 때. path 는 경로나 URI (내용 형식이 모호할 때 확장자를 본다).
    docx·jar 처럼 속은 zip 이어도 제 앱으로 여는 형식은 압축 파일로 보지 않는다
extract(files, parent, dest_dir=None, done=None, show_when_done=None)
    dest_dir 가 없으면 "압축(ZIP) 폴더 풀기" 창 — 대상 폴더(기본: <압축 파일 폴더>/<압축 파일 이름>)와
    [찾아보기…], "완료되면 추출된 파일 표시"(켜면 끝난 뒤 sekai-files <대상>). dest_dir 를 주면 묻지 않고
    거기에 푼다 (여러 개면 dest_dir/<각 이름>). done(ok, [대상 폴더 Gio.File])
    zip: zipfile — 윈도우에서 만든 zip 의 CP949 이름(UTF-8 표시 비트가 없는 것)도 읽는다, 암호(ZipCrypto)는 묻는다.
    tar·tar.gz·tar.bz2·tar.xz: tarfile. .gz·.bz2·.xz 한 파일: 파이썬. 나머지(7z·rar·iso·tar.zst·cab…)와
    AES 암호 zip: bsdtar(libarchive-tools) — 없으면 알린다.
    안전: 절대 경로·".."·대상 밖을 가리키는 링크·장치 파일은 풀지 않고 끝나면 알린다.
    이미 있는 파일은 복사와 같은 "파일 바꾸기 또는 건너뛰기" 창 (fileops 의 창을 그대로 쓴다)
compress(files, parent, done=None)
    "압축(ZIP) 파일로 보내기" — 첫 항목 옆에 <이름>.zip (겹치면 "(2)"). 이름은 UTF-8(표시 비트 포함),
    폴더는 안까지, 링크는 링크로. done(ok, [zip Gio.File]) — 탐색기가 새 zip 을 골라 이름을 바꾸게 할 수 있다
진행 창·오류 창은 fileops.Job 과 같다.
"""
import bz2
import gzip
import lzma
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
import zlib

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from sekaishell import fileops as F  # noqa: E402
from sekaishell.fileops import Cancelled, Job, display_text, josa  # noqa: E402

ZIP_TYPES = {"application/zip", "application/x-zip", "application/x-zip-compressed"}
TAR_TYPES = {"application/x-tar", "application/x-gtar", "application/x-compressed-tar",
             "application/x-bzip-compressed-tar", "application/x-bzip2-compressed-tar",
             "application/x-xz-compressed-tar", "application/x-tarz"}
SINGLE_TYPES = {"application/gzip", "application/x-gzip", "application/x-bzip", "application/x-bzip2",
                "application/x-xz", "application/x-lzma"}
OTHER_TYPES = {"application/x-7z-compressed", "application/vnd.rar", "application/x-rar",
               "application/x-rar-compressed", "application/x-iso9660-image", "application/x-cd-image",
               "application/vnd.ms-cab-compressed", "application/x-zstd-compressed-tar",
               "application/x-lzma-compressed-tar", "application/x-lz4-compressed-tar",
               "application/x-lzip-compressed-tar", "application/x-cpio", "application/x-xar",
               "application/x-lha", "application/x-lzh-compressed", "application/zstd"}
ALL_TYPES = ZIP_TYPES | TAR_TYPES | SINGLE_TYPES | OTHER_TYPES
GENERIC_TYPES = {"", "application/octet-stream", "text/plain", "application/x-zerosize"}
MULTI_EXTS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tar.lzma", ".tar.lz4", ".tar.z")
EXTS = MULTI_EXTS + (".zip", ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".7z", ".rar", ".iso",
                     ".cab", ".gz", ".bz2", ".xz", ".lzma", ".cpio", ".lzh", ".lha", ".xar", ".zst")
# 이미 압축된 형식 — 다시 압축해 봐야 줄지 않으니 그대로 담는다 (빠르게)
STORED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif", ".mp3", ".m4a", ".aac", ".ogg",
               ".opus", ".flac", ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".zip", ".7z", ".rar",
               ".gz", ".bz2", ".xz", ".zst", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".apk"}
CHUNK = 1 << 20
BSDTAR_OPTS = ["--no-same-owner", "--no-acls", "--no-xattrs", "--no-fflags"]
# bsdtar 는 암호가 필요한데 --passphrase 가 없으면 터미널에서 묻는다 — 터미널이 없으면 "Enter passphrase:" 를
#   끝없이 되풀이하며 멈춘다. 그래서 늘 주고(모르면 이 값), 새 세션에서 돌려 터미널에 닿지 않게 한다
NO_PASS = "\x01sekai-no-passphrase"
_UMASK = os.umask(0)
os.umask(_UMASK)


def _basename(path):
    p = path or ""
    if "://" in p:
        p = GLib.uri_unescape_string(p.split("?", 1)[0], None) or p
    return os.path.basename(p.rstrip("/"))


def is_archive(path, content_type):
    ct = (content_type or "").lower()
    if ct in ALL_TYPES:
        return True
    if ct not in GENERIC_TYPES:
        return False
    low = _basename(path).lower()
    return any(low.endswith(e) and len(low) > len(e) for e in EXTS)


def archive_stem(name):
    """압축 파일 이름 → 풀 폴더 이름 (사진.tar.gz → 사진)"""
    low = name.lower()
    for e in MULTI_EXTS + (".tgz", ".tbz", ".tbz2", ".txz", ".tzst"):
        if low.endswith(e) and len(name) > len(e):
            return name[:-len(e)]
    stem, ext = os.path.splitext(name)
    return stem if stem and ext else name + " 압축 풀기"


def _sniff(path):
    """실제 형식 — "zip"·"tar"·"gz"·"bz2"·"xz"·"other" (내용의 첫 바이트로)"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except (OSError, EOFError, zlib.error, lzma.LZMAError):
        pass
    if head[:2] == b"\x1f\x8b":
        return "gz"
    if head[:3] == b"BZh":
        return "bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if zipfile.is_zipfile(path):                      # 앞에 실행 파일이 붙은 zip (자동 풀림)
        return "zip"
    return "other"


# ── 창 ───────────────────────────────────────────────────────
def _password_dialog(job, reply, arc_name, wrong):
    w, body, foot = F.dialog_window(job.dialog_parent(), "암호 입력", modal=False, width=440)
    done = []

    def answer(v):
        if done:
            return
        done.append(v)
        w.destroy()
        reply(v)

    body.pack_start(F._label("암호가 필요합니다", "fo-title"), False, False, 0)
    body.pack_start(F._label(f"'{arc_name}'{josa(arc_name, '은')} 암호로 보호되어 있습니다. 암호를 입력하세요.",
                             width_chars=52), False, False, 0)
    entry = Gtk.Entry()
    entry.set_visibility(False)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    entry.set_activates_default(True)
    body.pack_start(entry, False, False, 4)
    if wrong:
        body.pack_start(F._label("암호가 올바르지 않습니다. 다시 입력하세요.", "fo-error"), False, False, 0)
    ok = F._button("확인", True, lambda: answer(entry.get_text()))
    ok.set_can_default(True)
    foot.pack_start(ok, False, False, 0)
    foot.pack_start(F._button("취소", cb=lambda: answer(None)), False, False, 0)
    w.set_default(ok)
    w.connect("delete-event", lambda *_: (answer(None), True)[1])
    w.connect("key-press-event", lambda _w, ev: (answer(None), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
    w.show_all()
    entry.grab_focus()
    return w


def _ask_password(job, arc_name, wrong=False):
    return job.ask(lambda reply: _password_dialog(job, reply, arc_name, wrong))


def _extract_dialog(parent, archives, on_ok):
    """"압축(ZIP) 폴더 풀기" — on_ok(대상 경로, 끝나면 보일지)"""
    all_zip = all(a.lower().endswith(".zip") for a in archives)
    w, body, foot = F.dialog_window(parent, "압축(ZIP) 폴더 풀기" if all_zip else "압축 풀기", modal=True, width=560)
    single = len(archives) == 1
    first = archives[0]
    default = os.path.join(os.path.dirname(first), archive_stem(os.path.basename(first))) if single else \
        os.path.dirname(first)
    body.pack_start(F._label("대상을 선택하고 파일 풀기", "fo-title"), False, False, 0)
    body.pack_start(F._label("다음 폴더에 파일 풀기:" if single else
                             f"다음 폴더에 압축 파일 {len(archives)}개를 각각 풉니다:", "fo-key"), False, False, 6)
    row = Gtk.Box(spacing=8)
    entry = Gtk.Entry()
    entry.set_text(display_text(default))
    entry.set_hexpand(True)
    entry.set_activates_default(True)
    row.pack_start(entry, True, True, 0)
    err = F._label("", "fo-error")
    err.set_no_show_all(True)

    def browse():
        dlg = Gtk.FileChooserDialog(title="압축을 풀 폴더 선택", transient_for=w,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_button("취소", Gtk.ResponseType.CANCEL)
        dlg.add_button("폴더 선택", Gtk.ResponseType.ACCEPT)
        dlg.set_default_response(Gtk.ResponseType.ACCEPT)
        cur = os.path.expanduser(entry.get_text().strip()) or default
        while cur and cur != "/" and not os.path.isdir(cur):
            cur = os.path.dirname(cur)
        dlg.set_current_folder(cur or os.path.expanduser("~"))

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT and d.get_filename():
                entry.set_text(display_text(d.get_filename()))
            d.destroy()
        dlg.connect("response", resp)
        dlg.show()
    row.pack_start(F._button("찾아보기…", cb=browse), False, False, 0)
    body.pack_start(row, False, False, 0)
    body.pack_start(err, False, False, 0)
    check = Gtk.CheckButton(label="완료되면 추출된 파일 표시")
    check.set_active(True)
    body.pack_start(check, False, False, 8)

    def go():
        dest = os.path.expanduser(entry.get_text().strip())
        if not dest or not os.path.isabs(dest):
            err.set_text("폴더의 전체 경로를 입력하세요 (예: /home/사용자/문서/새 폴더).")
            err.show()
            return
        w.destroy()
        on_ok(os.path.normpath(dest), check.get_active())
    ok = F._button("압축 풀기", True, go)
    ok.set_can_default(True)
    foot.pack_start(ok, False, False, 0)
    foot.pack_start(F._button("취소", cb=w.destroy), False, False, 0)
    w.set_default(ok)
    w.connect("key-press-event", lambda _w, ev: (w.destroy(), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
    w.show_all()
    entry.grab_focus()
    entry.select_region(0, -1)
    return w


# ── 풀어 놓기 (작업 스레드) ──────────────────────────────────
def _guess_meta(name, is_dir, size, mtime):
    ct = "inode/directory" if is_dir else (Gio.content_type_guess(name, None)[0] or "application/octet-stream")
    return {"dir": is_dir, "size": size or 0, "mtime": mtime or 0, "ctype": ct, "icon": None}


class _Sink:
    """풀 곳 — 안전 검사·충돌·쓰기. 경로는 모두 dest 안의 상대 경로(rel)로 받는다"""

    def __init__(self, job, dest, bytes_progress=True):
        self.job = job
        self.dest = dest
        self.real = os.path.realpath(dest)
        self.unsafe = []
        self.dir_times = []
        self.bytes_progress = bytes_progress        # False 면 진행률은 압축 파일에서 읽은 양 (tar·gz)

    def _inside(self, p):
        rp = os.path.realpath(p)
        return rp == self.real or rp.startswith(self.real.rstrip("/") + "/")

    def path_for(self, rel):
        p = os.path.join(self.dest, rel)
        return p if self._inside(os.path.dirname(p)) else None

    def refuse(self, name, why):
        self.unsafe.append((display_text(name), why))
        self.job.item_done(1, 0)

    def _mkdirs(self, d, disp):
        while True:
            try:
                os.makedirs(d, exist_ok=True)
                return True
            except FileExistsError:
                self.refuse(disp, "같은 이름의 파일이 있어 폴더를 만들 수 없음")
                return False
            except OSError as e:
                if self.job.error(e, disp, "mkdir") == "skip":
                    return False

    def mkdir(self, rel, mtime=None, disp=None):
        disp = disp or rel
        p = self.path_for(rel)
        if p is None:
            self.refuse(disp, "대상 폴더 밖을 가리킴")
            return
        if os.path.islink(p) or (os.path.lexists(p) and not os.path.isdir(p)):
            self.refuse(disp, "같은 이름의 파일이나 링크가 있음")
            return
        if self._mkdirs(p, disp) and mtime:
            self.dir_times.append((p, mtime))
        self.job.item_done(1, 0)

    def _resolve(self, p, disp, meta):
        """이미 있으면 묻는다 — (쓸 경로, 바꾸기인가) 또는 (None, _) 건너뛰기"""
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return p, False
        d_dir = stat.S_ISDIR(st.st_mode)
        folder, name = os.path.split(p)
        keep = name
        for n in range(2, 100000):
            keep = F.numbered_name(name, n, meta["dir"])
            if not os.path.lexists(os.path.join(folder, keep)):
                break
        dmeta = _guess_meta(name, d_dir, st.st_size, st.st_mtime)
        choice = self.job.conflict(disp, meta, dmeta, keep, nested=True)
        if choice == "keep":
            return os.path.join(folder, keep), False
        if choice == "replace" and not d_dir:
            return p, True
        return None, False

    def _prepare(self, rel, disp, meta):
        p = self.path_for(rel)
        if p is None:
            self.refuse(disp, "대상 폴더 밖을 가리킴")
            return None
        if not self._mkdirs(os.path.dirname(p), disp):
            self.job.item_done(1, meta["size"] if self.bytes_progress else 0)
            return None
        target, _replace = self._resolve(p, disp, meta)
        if target is None:
            self.job.item_done(1, meta["size"] if self.bytes_progress else 0)
        return target

    def file(self, rel, size, mtime, mode, open_stream, disp=None):
        """open_stream() → 읽을 것 (read(n)). 끝나면 True"""
        job = self.job
        disp = disp or rel
        job.cur_name = os.path.basename(disp)
        target = self._prepare(rel, disp, _guess_meta(os.path.basename(rel), False, size, mtime))
        if target is None:
            return False
        folder = os.path.dirname(target)
        while True:
            tmp = os.path.join(folder, f".{os.path.basename(target)[:80]}.{os.getpid()}.{threading.get_ident()}.part")
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except OSError as e:
                if job.error(e, disp, "extract") == "skip":
                    job.item_done(1, size if self.bytes_progress else 0)
                    return False
                continue
            try:
                with os.fdopen(fd, "wb") as out:
                    src = open_stream()
                    try:
                        got = 0
                        while True:
                            job.checkpoint()
                            buf = src.read(CHUNK)
                            if not buf:
                                break
                            out.write(buf)
                            got += len(buf)
                            if self.bytes_progress:
                                job.cur_bytes = got
                    finally:
                        close = getattr(src, "close", None)
                        if close:
                            close()
                m = (mode & 0o777) if mode else (0o666 & ~_UMASK)
                os.chmod(tmp, m | 0o200)                 # 주인은 늘 쓸 수 있게 (그래야 다시 풀거나 지울 수 있다)
                if mtime:
                    os.utime(tmp, (mtime, mtime))
                os.replace(tmp, target)
                job.cur_bytes = 0
                job.item_done(1, size if self.bytes_progress else 0)
                return True
            except Cancelled:
                _unlink(tmp)
                raise
            except OSError as e:
                _unlink(tmp)
                job.cur_bytes = 0
                if job.error(e, disp, "extract") == "skip":
                    job.item_done(1, size if self.bytes_progress else 0)
                    return False
            except (zipfile.BadZipFile, zlib.error, EOFError, lzma.LZMAError, tarfile.TarError):
                _unlink(tmp)
                job.cur_bytes = 0
                job.error(None, disp, "extract", can_retry=False,
                          reason="압축 파일이 손상되어 이 항목을 풀 수 없습니다.")
                job.item_done(1, size if self.bytes_progress else 0)
                return False

    def symlink(self, rel, link, disp=None):
        disp = disp or rel
        p = self.path_for(rel)
        if p is None or not link or os.path.isabs(link):
            self.refuse(disp, "대상 폴더 밖을 가리키는 링크")
            return
        resolved = os.path.normpath(os.path.join(os.path.dirname(p), link))
        if not (resolved == self.real or resolved.startswith(self.real.rstrip("/") + "/")
                or resolved == self.dest or resolved.startswith(self.dest.rstrip("/") + "/")) \
                or not self._inside(resolved):
            self.refuse(disp, "대상 폴더 밖을 가리키는 링크")
            return
        target = self._prepare(rel, disp, _guess_meta(os.path.basename(rel), False, 0, 0))
        if target is None:
            return
        tmp = target + f".{os.getpid()}.lnk"
        try:
            os.symlink(link, tmp)
            os.replace(tmp, target)
        except OSError as e:
            _unlink(tmp)
            self.refuse(disp, F._explain_errno(e.errno))
            return
        self.job.item_done(1, 0)

    def hardlink(self, rel, link_rel, size, disp=None):
        """묶음 안의 다른 파일과 같은 내용 — 풀어 둔 그 파일을 복사한다"""
        disp = disp or rel
        src = self.path_for(link_rel) if link_rel else None
        if src is None or os.path.islink(src) or not os.path.isfile(src) or not self._inside(src):
            self.refuse(disp, "대상 폴더 밖을 가리키는 연결")
            return
        st = os.stat(src)
        self.file(rel, st.st_size, st.st_mtime, st.st_mode, lambda: open(src, "rb"), disp)

    def finish(self):
        for p, mt in reversed(self.dir_times):
            try:
                os.utime(p, (mt, mt), follow_symlinks=False)
            except OSError:
                pass


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _safe_rel(name):
    """압축 파일 안의 이름 → 안전한 상대 경로 또는 None (절대 경로·".."·드라이브 문자)"""
    if not name or "\0" in name:
        return None
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":" and n[0].isalpha()):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


class _Counting:
    """읽은 양을 진행률로 (tar·gz 는 한 번에 읽으며 푼다 — 전체 항목 수를 미리 모른다)"""

    def __init__(self, f, job):
        self.f = f
        self.job = job
        self.pos = 0

    def read(self, n=-1):
        b = self.f.read(n)
        self.pos += len(b)
        self.job.done_bytes = self.pos
        return b

    def close(self):
        self.f.close()


# ── zip ──
def _unicode_path(info):
    """Info-ZIP 유니코드 경로 추가 필드(0x7075)의 UTF-8 이름"""
    extra, i = info.extra or b"", 0
    while i + 4 <= len(extra):
        tag, ln = struct.unpack("<HH", extra[i:i + 4])
        if tag == 0x7075 and ln > 5:
            try:
                return extra[i + 9:i + 4 + ln].decode("utf-8")
            except UnicodeDecodeError:
                return None
        i += 4 + ln
    return None


def zip_names(infos):
    """zip 항목들의 진짜 이름. UTF-8 표시 비트가 없는 이름(윈도우 탐색기로 만든 한국어 zip 은 CP949)은
    zipfile 이 CP437 로 읽는다 — 바이트로 되돌려 UTF-8 → CP949 → CP437 순으로 한 zip 전체에 맞는 것을 고른다"""
    names = {}
    raw = {}
    for i in infos:
        if i.flag_bits & 0x800:
            names[id(i)] = i.orig_filename
            continue
        up = _unicode_path(i)
        if up:
            names[id(i)] = up
            continue
        try:
            raw[id(i)] = i.orig_filename.encode("cp437")
        except UnicodeEncodeError:
            names[id(i)] = i.orig_filename
    enc = "cp437"
    if raw and not all(b.isascii() for b in raw.values()):
        for cand in ("utf-8", "cp949"):
            try:
                for b in raw.values():
                    b.decode(cand)
                enc = cand
                break
            except UnicodeDecodeError:
                continue
    for k, b in raw.items():
        names[k] = b.decode(enc, "replace")
    return [names[id(i)] for i in infos]


class _NeedBsdtar(Exception):
    """zipfile 이 못 푸는 zip (AES 암호·deflate64 등)"""


def _zip_password(job, zf, infos, arc_name):
    enc = next((i for i in infos if i.flag_bits & 1 and not i.is_dir()), None)
    if enc is None:
        return None
    wrong = False
    while True:
        pw = _ask_password(job, arc_name, wrong)
        for cand in _pw_bytes(pw):
            try:
                with zf.open(enc, pwd=cand) as f:
                    f.read(1)
                return cand
            except RuntimeError:
                continue
            except (zipfile.BadZipFile, zlib.error):
                continue
        wrong = True


def _pw_bytes(pw):
    out = []
    for e in ("utf-8", "cp949"):
        try:
            b = pw.encode(e)
        except UnicodeEncodeError:
            continue
        if b not in out:
            out.append(b)
    return out


def _x_zip(job, arc, dest, arc_name):
    try:
        zf = zipfile.ZipFile(arc)
    except (zipfile.BadZipFile, OSError) as e:
        raise _Broken(str(e))
    with zf:
        infos = zf.infolist()
        if any(i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2,
                                       zipfile.ZIP_LZMA) for i in infos):
            raise _NeedBsdtar()                      # AES(99)·deflate64(9)·PPMd…
        names = zip_names(infos)
        job.total_items = len(infos)
        job.total_bytes = sum(i.file_size for i in infos if not i.is_dir())
        job.scanning = False
        pwd = _zip_password(job, zf, infos, arc_name)
        sink = _Sink(job, dest)
        if not sink._mkdirs(dest, display_text(os.path.basename(dest))):
            return False
        for info, name in zip(infos, names):
            job.checkpoint()
            disp = display_text(name)
            rel = _safe_rel(name)
            if rel is None:
                sink.refuse(disp, "절대 경로나 '..' 가 들어 있음")
                continue
            mode = (info.external_attr >> 16) & 0xFFFF if info.create_system == 3 else 0
            mtime = _zip_time(info)
            if info.is_dir() or name.endswith(("/", "\\")) or stat.S_ISDIR(mode):
                sink.mkdir(rel, mtime, disp)
                continue
            if mode and stat.S_ISLNK(mode):
                try:
                    link = zf.read(info, pwd=pwd).decode("utf-8", "surrogateescape")
                except (RuntimeError, zipfile.BadZipFile, zlib.error) as e:
                    sink.refuse(disp, str(e))
                    continue
                sink.symlink(rel, link, disp)
                continue
            if mode and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                sink.refuse(disp, "장치·특수 파일")
                continue

            def opener(info=info):
                try:
                    return zf.open(info, pwd=pwd if info.flag_bits & 1 else None)
                except RuntimeError as e:              # 이 항목만 암호가 다르다
                    raise zipfile.BadZipFile(str(e))
            sink.file(rel, info.file_size, mtime, mode & 0o777 if mode else None, opener, disp)
        sink.finish()
        return sink


def _zip_time(info):
    try:
        return time.mktime(info.date_time + (0, 0, -1))
    except (OverflowError, ValueError):
        return None


# ── tar ──
def _x_tar(job, arc, dest):
    size = os.path.getsize(arc)
    job.total_bytes = size
    job.item_weight = 0
    job.scanning = False
    sink = _Sink(job, dest, bytes_progress=False)
    if not sink._mkdirs(dest, display_text(os.path.basename(dest))):
        return False
    raw = _Counting(open(arc, "rb"), job)
    try:
        with tarfile.open(fileobj=raw, mode="r|*") as tf:
            for m in tf:
                job.checkpoint()
                disp = display_text(m.name)
                rel = _safe_rel(m.name)
                if rel is None:
                    sink.refuse(disp, "절대 경로나 '..' 가 들어 있음")
                    continue
                mode = m.mode & 0o777
                if m.isdir():
                    sink.mkdir(rel, m.mtime, disp)
                elif m.isreg():
                    sink.file(rel, m.size, m.mtime, mode, lambda m=m: tf.extractfile(m), disp)
                elif m.issym():
                    sink.symlink(rel, m.linkname, disp)
                elif m.islnk():
                    sink.hardlink(rel, _safe_rel(m.linkname), m.size, disp)
                else:
                    sink.refuse(disp, "장치·특수 파일")
    except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as e:
        if isinstance(e, OSError) and job.cancellable.is_cancelled():
            raise Cancelled()
        raise _Broken(str(e))
    finally:
        raw.close()
    sink.finish()
    return sink


# ── .gz·.bz2·.xz 한 파일 ──
def _x_single(job, arc, dest, kind):
    name = os.path.basename(arc)
    for e in (".gz", ".bz2", ".xz", ".lzma", ".z"):
        if name.lower().endswith(e):
            name = name[:-len(e)]
            break
    else:
        name = name + " 압축 해제"
    job.total_items = 1
    job.total_bytes = os.path.getsize(arc)
    job.item_weight = 0
    job.scanning = False
    sink = _Sink(job, dest, bytes_progress=False)
    if not sink._mkdirs(dest, display_text(os.path.basename(dest))):
        return False
    raw = _Counting(open(arc, "rb"), job)
    make = {"gz": lambda: gzip.GzipFile(fileobj=raw), "bz2": lambda: bz2.BZ2File(raw),
            "xz": lambda: lzma.LZMAFile(raw)}[kind]
    try:
        st = os.stat(arc)
        sink.file(name, 0, st.st_mtime, None, make, display_text(name))
    finally:
        raw.close()
    return sink


# ── 그 밖 (bsdtar) ──
class _Broken(Exception):
    """압축 파일이 깨졌거나 모르는 형식"""


class _Unsupported(Exception):
    """풀 수 없는 것 (이유는 글로)"""


def _run(job, argv, on_line=None):
    """명령을 돌리며 취소를 본다 — (반환 코드, 표준 오류 줄들). on_line(줄) 은 표준 오류의 줄마다"""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if on_line is None else
                             subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        raise _Unsupported(str(e))
    out_lines, err_lines = [], []

    def cancel_watch():
        while p.poll() is None:
            if job.cancellable.is_cancelled():
                p.kill()
                return
            time.sleep(0.15)
    threading.Thread(target=cancel_watch, daemon=True).start()
    if on_line is None:
        t = threading.Thread(target=lambda: err_lines.extend(p.stderr.read().decode("utf-8", "replace")
                                                             .splitlines()), daemon=True)
        t.start()
        for line in p.stdout:
            out_lines.append(line.decode("utf-8", "surrogateescape").rstrip("\n"))
        t.join()
    else:
        # bsdtar -v 는 "x 이름" 을 쓰고, 그 항목에서 오류가 나면 같은 줄에 ": 이유" 를 붙인다 — 줄은 모두 남긴다
        for line in p.stderr:
            s = line.decode("utf-8", "replace").rstrip("\n")
            if s.startswith("x "):
                on_line(s[2:])
            err_lines.append(s)
    rc = p.wait()
    if job.cancellable.is_cancelled():
        raise Cancelled()
    return rc, out_lines, err_lines


def _needs_pass(lines):
    low = " ".join(lines).lower()
    return "passphrase" in low


def _x_bsdtar(job, arc, dest, arc_name):
    bsdtar = shutil.which("bsdtar")
    if not bsdtar:
        ext = os.path.splitext(arc_name)[1] or arc_name
        raise _Unsupported(f"이 형식({display_text(ext)})의 압축 파일을 풀려면 'libarchive-tools' 패키지를 "
                           "설치해야 합니다.")
    pw = None
    wrong = False
    while True:
        extra = ["--passphrase", pw if pw is not None else NO_PASS]
        rc, listing, err = _run(job, [bsdtar, "-t", "-f", arc] + extra)
        if rc == 0:
            break
        if _needs_pass(err):
            low = " ".join(err).lower()
            if "unsupported" in low or "not supported" in low:
                raise _Unsupported("이 형식의 암호로 보호된 압축 파일은 풀 수 없습니다.")
            pw = _ask_password(job, arc_name, wrong)
            wrong = True
            continue
        if "encrypt" in " ".join(err).lower():
            raise _Unsupported("이 형식의 암호로 보호된 압축 파일은 풀 수 없습니다.")
        raise _Broken("\n".join(err[-3:]))
    job.total_items = len(listing)
    job.scanning = False

    existed = os.path.isdir(dest)
    home = dest if existed else os.path.dirname(dest)
    sink = _Sink(job, dest)
    if not sink._mkdirs(home, display_text(os.path.basename(home))):
        return False
    staging = None
    try:
        def seen(name):
            job.cur_name = os.path.basename(name.rstrip("/"))
            job.item_done(1, 0)
        while True:
            # 임시 폴더에 풀고 검사한 뒤 옮긴다 — 대상 폴더와 같은 드라이브 (옮기기가 이름 바꾸기로 끝나게)
            staging = tempfile.mkdtemp(prefix=".sekai-extract-", dir=home)
            extra = ["--passphrase", pw if pw is not None else NO_PASS]
            rc, _o, err = _run(job, [bsdtar, "-x", "-v", "-f", arc, "-C", staging] + BSDTAR_OPTS + extra, seen)
            if rc != 0 and _needs_pass(err):
                low = " ".join(err).lower()
                if "unsupported" in low or "not supported" in low:
                    raise _Unsupported("이 형식의 암호로 보호된 압축 파일은 풀 수 없습니다.")
                shutil.rmtree(staging, ignore_errors=True)
                staging = None
                job.done_items = 0
                pw = _ask_password(job, arc_name, pw is not None)   # (AES zip 은 목록은 암호 없이 읽힌다)
                continue
            break
        if rc != 0:
            if not os.listdir(staging):
                raise _Broken("")
            # bsdtar 의 영어 오류 글 대신 풀지 못한 항목의 이름만 ("x 이름: 이유")
            bad = [x[2:].split(": ", 1)[0] for x in err if x.startswith("x ") and ": " in x]
            job.notes.append(f"'{arc_name}'에서 일부 항목을 풀지 못했습니다." + (" 압축 파일이 손상되었을 수 있습니다."
                                                                       if not bad else ""))
            job.notes.extend(f"· {display_text(x)}" for x in bad[:8])
        _check_staging(staging, dest, sink)
        if not existed and not os.path.lexists(dest):
            os.rename(staging, dest)                   # 새 폴더 — 통째로 (같은 드라이브라 바로 끝난다)
            staging = None
        else:
            _merge(job, sink, staging, "")
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
    return sink


def _check_staging(staging, dest, sink):
    """bsdtar 가 풀어 놓은 것 중 대상 밖을 가리키는 링크·장치 파일을 지운다"""
    real_dest = os.path.realpath(dest)
    for dirpath, dirnames, filenames in os.walk(staging):
        rel_dir = os.path.relpath(dirpath, staging)
        for nm in dirnames + filenames:
            p = os.path.join(dirpath, nm)
            rel = os.path.normpath(os.path.join(rel_dir, nm))
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                # 옮긴 뒤의 자리에서 풀어 본다 — 절대 경로이거나 대상 폴더 밖이면 지운다
                link = os.readlink(p)
                final = os.path.normpath(os.path.join(dest, rel_dir, link))
                if os.path.isabs(link) or not (final == dest or final.startswith(dest.rstrip("/") + "/")) or \
                        not _within(os.path.realpath(final), real_dest):
                    _unlink(p)
                    sink.unsafe.append((display_text(rel), "대상 폴더 밖을 가리키는 링크"))
            elif not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                _unlink(p)
                sink.unsafe.append((display_text(rel), "장치·특수 파일"))
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]


def _within(p, root):
    return p == root or p.startswith(root.rstrip("/") + "/")


def _merge(job, sink, staging, rel):
    """임시 폴더의 것을 대상으로 (같은 드라이브 — 이름만 바꾼다). 겹치면 묻는다"""
    src_dir = os.path.join(staging, rel)
    for e in sorted(os.scandir(src_dir), key=lambda x: x.name):
        job.checkpoint()
        r = os.path.join(rel, e.name) if rel else e.name
        disp = display_text(r)
        dst = sink.path_for(r)
        if dst is None:
            sink.unsafe.append((disp, "대상 폴더 밖을 가리킴"))
            continue
        is_dir = e.is_dir(follow_symlinks=False)
        if is_dir and os.path.isdir(dst) and not os.path.islink(dst):
            _merge(job, sink, staging, r)
            continue
        st = e.stat(follow_symlinks=False)
        target, _rep = sink._resolve(dst, disp, _guess_meta(e.name, is_dir, st.st_size, st.st_mtime))
        if target is None:
            continue
        try:
            os.replace(e.path, target)
        except OSError as err:
            if job.error(err, disp, "extract") == "skip":
                continue


# ── 압축 풀기 ────────────────────────────────────────────────
def _extract_one(job, arc, dest):
    arc_name = display_text(os.path.basename(arc))
    dest_name = display_text(os.path.basename(dest.rstrip("/")) or dest)
    job.title = f"{arc_name}에서 {dest_name}{josa(dest_name, '으로')} 압축을 푸는 중"
    job.total_items = job.total_bytes = job.done_items = job.done_bytes = 0
    job.cur_bytes = 0
    job.item_weight = F.ITEM_WEIGHT
    job.scanning = True
    kind = _sniff(arc)
    if kind is None:
        raise _Broken("")
    sink = None
    if kind == "zip":
        try:
            sink = _x_zip(job, arc, dest, arc_name)
        except _NeedBsdtar:
            job.total_items = job.total_bytes = job.done_items = job.done_bytes = 0
            job.scanning = True
            sink = _x_bsdtar(job, arc, dest, arc_name)
    elif kind == "tar":
        sink = _x_tar(job, arc, dest)
    elif kind in ("gz", "bz2", "xz"):
        sink = _x_single(job, arc, dest, kind)
    else:
        sink = _x_bsdtar(job, arc, dest, arc_name)
    if sink and sink.unsafe:
        names = [f"· {n} — {why}" for n, why in sink.unsafe[:8]]
        more = len(sink.unsafe) - len(names)
        job.notes.append(f"'{arc_name}'에서 안전하지 않은 항목 {len(sink.unsafe):,}개를 풀지 않았습니다.")
        job.notes.extend(names + ([f"· 그 밖 {more:,}개"] if more > 0 else []))
    return sink is not False


def _extract_job(job, pairs):
    for arc, dest in pairs:
        job.checkpoint()
        arc_name = display_text(os.path.basename(arc))
        try:
            if _extract_one(job, arc, dest):
                job.results.append(Gio.File.new_for_path(dest))
        except (_Broken, _Unsupported) as e:
            why = str(e) if isinstance(e, _Unsupported) else \
                "압축 파일이 손상되었거나 지원하지 않는 형식입니다."
            job.ask(lambda reply, why=why, n=arc_name: F.ask_choice(
                job.dialog_parent(), "압축을 풀 수 없습니다", f"'{n}'의 압축을 풀 수 없습니다.",
                why, [(True, "확인", True)], reply, default=True, icon="dialog-error-symbolic", modal=False))
    return bool(job.results)


def _show_folder(path):
    """추출된 파일 표시 — 우리 탐색기로"""
    try:
        Gio.Subprocess.new(["sekai-files", path], Gio.SubprocessFlags.NONE)
        return
    except GLib.Error:
        pass
    try:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
    except GLib.Error as e:
        print("[sekai-files] 폴더를 열지 못했습니다:", e.message, flush=True)


def _local_paths(files, parent, what):
    paths = []
    for f in files:
        g = F._gfile(f)
        p = g.get_path()
        if not p:
            F.notice(parent, "알림", f"이 위치의 항목은 {what} 수 없습니다.",
                     "컴퓨터에 연결된 드라이브의 파일만 할 수 있습니다.", icon="dialog-error-symbolic")
            return None
        paths.append(p)
    return paths


def extract(files, parent, dest_dir=None, done=None, show_when_done=None):
    """압축 풀기 — dest_dir 가 없으면 대상 폴더를 묻는다"""
    paths = _local_paths(files or [], parent, "압축을 풀")
    if not paths:
        return

    def start(dest, show):
        if len(paths) == 1:
            pairs = [(paths[0], dest)]
        else:
            pairs = [(p, os.path.join(dest, archive_stem(os.path.basename(p)))) for p in paths]

        def finished(ok, results):
            if ok and show and results:
                _show_folder(dest if len(paths) > 1 else results[0].get_path())
            if done:
                done(ok, results)
        Job("extract", parent, finished).start(lambda j: _extract_job(j, pairs))

    if dest_dir is not None:
        d = F._gfile(dest_dir).get_path()
        if not d:
            return
        start(d, bool(show_when_done))
    else:
        _extract_dialog(parent, paths, start)


# ── 압축하기 ─────────────────────────────────────────────────
def _walk(job, top, skip_real):
    """(경로, 압축 안 이름, lstat) — 링크는 따라가지 않는다"""
    base = os.path.basename(top.rstrip("/")) or "root"
    stack = [(top, base)]
    while stack:
        job.checkpoint()
        p, arc = stack.pop()
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if os.path.abspath(p) == skip_real:            # 만들고 있는 zip 자신
            continue
        yield p, arc, st
        if stat.S_ISDIR(st.st_mode):
            try:
                names = sorted(os.listdir(p), reverse=True)
            except OSError as e:
                if job.error(e, display_text(arc), "read") == "skip":
                    continue
                names = []
            for nm in names:
                stack.append((os.path.join(p, nm), arc + "/" + nm))


def _compress_job(job, paths):
    first = paths[0]
    folder = os.path.dirname(first.rstrip("/")) or "/"
    name0 = os.path.basename(first.rstrip("/"))
    stem = name0 if os.path.isdir(first) else F.split_ext(name0)[0]
    base = display_text(stem) + ".zip"
    zip_name = base
    for n in range(2, 100000):
        if not os.path.lexists(os.path.join(folder, zip_name)):
            break
        zip_name = F.numbered_name(base, n)
    tmp = os.path.join(folder, f".{zip_name}.{os.getpid()}.part")
    tmp_real = os.path.abspath(tmp)

    entries = []
    for p in paths:
        for item in _walk(job, p, tmp_real):
            entries.append(item)
            if stat.S_ISREG(item[2].st_mode):
                job.total_bytes += item[2].st_size
    job.total_items = len(entries)
    job.title = f"항목 {len(entries):,}개를 {zip_name}{josa(zip_name, '으로')} 압축하는 중"
    job.scanning = False
    ok = False
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for p, arc, st in entries:
                job.checkpoint()
                arc = display_text(arc)
                job.cur_name = os.path.basename(arc)
                _add(job, zf, p, arc, st)
        ok = True
    except Cancelled:
        raise
    except OSError as e:
        job.notes.append("압축 파일을 만들지 못했습니다.")
        job.notes.append(F.explain(e))
        return False
    finally:
        if not ok:
            _unlink(tmp)
    # 다 만든 뒤에 이름을 준다 — 반쯤 만든 zip 이 보이지 않게. 그사이 같은 이름이 생겼으면 다음 번호로
    final = os.path.join(folder, zip_name)
    for n in range(2, 100000):
        try:
            os.link(tmp, final)
            _unlink(tmp)
            break
        except FileExistsError:
            final = os.path.join(folder, F.numbered_name(base, n))
        except OSError:                                # 하드 링크가 없는 드라이브(FAT 등)
            os.rename(tmp, final)
            break
    job.results.append(Gio.File.new_for_path(final))
    return True


def _add(job, zf, path, arc, st):
    if stat.S_ISDIR(st.st_mode):
        zi = zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False)
        zf.writestr(zi, b"")
        job.item_done(1, 0)
        return
    if stat.S_ISLNK(st.st_mode):
        zi = zipfile.ZipInfo(arc, _dos_time(st.st_mtime))
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, os.readlink(path).encode("utf-8", "surrogateescape"))
        job.item_done(1, 0)
        return
    if not stat.S_ISREG(st.st_mode):
        job.item_done(1, 0)                            # 장치·소켓 — 담지 않는다
        return
    while True:
        try:
            src = open(path, "rb")
            break
        except OSError as e:
            if job.error(e, display_text(arc), "compress") == "skip":
                job.item_done(1, st.st_size)
                return
    with src:
        zi = zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False)
        zi.compress_type = zipfile.ZIP_STORED if os.path.splitext(arc)[1].lower() in STORED_EXTS \
            else zipfile.ZIP_DEFLATED
        got = 0
        with zf.open(zi, "w", force_zip64=st.st_size > 0x7FFF0000) as out:
            while True:
                job.checkpoint()
                buf = src.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
                got += len(buf)
                job.cur_bytes = got
    job.cur_bytes = 0
    job.item_done(1, st.st_size)


def _dos_time(ts):
    t = time.localtime(max(ts, 315532800))           # zip 은 1980년부터
    return t[:6]


def compress(files, parent, done=None):
    """"압축(ZIP) 파일로 보내기" — done(ok, [새 zip])"""
    paths = _local_paths(files or [], parent, "압축할")
    if not paths:
        return None
    return Job("compress", parent, done).start(lambda j: _compress_job(j, paths))
