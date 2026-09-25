"""파일 작업 — 복사·이동·휴지통·영구 삭제·복원·이름 바꾸기·새로 만들기·클립보드 (윈도우 11 탐색기처럼).

0.5초 넘게 걸리는 작업만 진행 창("항목 N개를 A에서 B(으)로 복사하는 중")이 뜬다. 여러 작업이 함께 돌면
한 진행 창에 줄이 하나씩 생긴다. 이름이 겹치면 "파일 바꾸기 또는 건너뛰기", 오류면 다시 시도·건너뛰기·취소를 묻는다.
탐색기(sekai-files)와 바탕 화면(sekai-desk)이 함께 쓰므로 탐색기에 기대지 않는다 (Gtk·Gio 만).

── 부르는 법 ─────────────────────────────────────────────────
모두 메인(GTK) 스레드에서 부른다. 파일을 읽고 쓰는 일은 작업 스레드가 하고, 끝나면 메인 스레드에서
done(ok, result_files) 를 부른다 (done 은 없어도 된다):
    ok             끝까지 했으면 True — 몇 개를 건너뛰었어도. 취소했거나 묻는 창에서 "아니요"면 False
    result_files   새 자리의 맨 위 항목들 [Gio.File] — 복사·이동·복원한 것 (탐색기가 골라 보여 줄 것).
                   휴지통·삭제는 []

copy(sources, dest_dir, parent, done=None)
    sources: [Gio.File] (경로 문자열도 받는다) · dest_dir: Gio.File · parent: Gtk.Window 또는 None
    같은 폴더에 붙여넣으면 "이름 - 복사본.ext", "이름 - 복사본 (2).ext". 링크는 링크로 복사(따라가지 않음),
    수정한 시각·권한은 Gio 가 옮겨 주는 만큼 옮긴다. 폴더를 제 안(하위 폴더)으로 복사·이동하려 하면 거절한다
move(sources, dest_dir, parent, done=None)
    같은 파일 시스템이면 이름만 바꾼다(바로 끝남). 다르면 복사한 뒤, 잘 복사된 것만 원본을 지운다
trash(files, parent, done=None)
    휴지통을 쓸 수 없는 곳이면 "휴지통으로 이동할 수 없습니다. 영구적으로 삭제하시겠습니까?"
delete_permanently(files, parent, done=None)   먼저 "영구적으로 삭제하시겠습니까?" (예/아니요, 기본 아니요)
restore_from_trash(files, parent, done=None)   files: trash:/// 항목. 원래 자리(trash::orig-path)로 —
                                               없어진 상위 폴더는 다시 만들고, 이름이 겹치면 복사처럼 묻는다
empty_trash(parent, done=None)                 "휴지통에 있는 항목 N개를 영구적으로 삭제하시겠습니까?"
rename(gfile, new_name) -> (새 Gio.File, None) | (None, "오류 글")
    바로 끝나는 일이라 동기. 대소문자만 바꾸는 것도 된다 (대소문자를 가리지 않는 FAT·NTFS 에서도)
validate_name(name) -> None | "오류 글"         이름 칸에서 미리 검사할 때
new_folder(dir_gfile, parent=None) -> Gio.File | None       "새 폴더", "새 폴더 (2)", …
new_text_file(dir_gfile, parent=None) -> Gio.File | None    "새 텍스트 문서.txt", "새 텍스트 문서 (2).txt", …
    만들지 못하면 오류 창을 띄우고 None

clipboard_set(files, cut, hold=True)
    복사(cut=False)·잘라내기(cut=True). 다른 앱과 주고받게 x-special/gnome-copied-files · text/uri-list ·
    application/x-kde-cutselection · 글자(경로, 터미널에 붙여넣기)를 한꺼번에 내놓는다 — GTK 의 선택 영역 API
    (X11·Wayland 모두 GTK 가 처리). 클립보드는 이 프로세스가 지키므로 hold=True 면 다른 앱이 클립보드를 가져갈
    때까지 Gio.Application 을 붙잡아 둔다 (창을 다 닫아도 붙여넣을 수 있게 — 윈도우 탐색기처럼 늘 떠 있는 셈)
clipboard_get(callback)
    callback(files, cut) 를 메인 스레드에서. x-special/gnome-copied-files · text/uri-list(크롬 등) ·
    경로 글자를 읽는다. 쓸 만한 게 없으면 ([], False)
clipboard_clear()                              잘라낸 것을 붙여넣은 뒤 (move 가 스스로 부른다)
clipboard_owned() -> (files, cut) | None       이 프로세스가 지금 클립보드에 둔 것 (잘라낸 항목을 흐리게 그릴 때)

── 다른 부품(archive·properties)이 함께 쓰는 것 ──
Job                         작업 스레드 하나 + 진행 창 한 줄 + 묻는 창 (충돌·오류) — 아래 클래스 설명
ask_choice(...) · notice(...) · dialog_window(...)   이 모듈 모양의 묻는 창
fmt_size(n) "1.23 MB" · fmt_size_exact(n) "1.23 MB (1,290,000 바이트)" · fmt_date(ts) · josa(단어, "으로")
display_text(s)             파일 이름을 라벨에 넣을 수 있는 글자로 (UTF-8 이 아닌 이름도)
CSS                         이 모듈 창들의 모양. 처음 창을 띄울 때 install_css() 가 화면에 스스로 얹는다
                            (@accent·@fg·@surface 만 빌려 쓴다 — 탐색기(settings.css)·바탕 화면(style.css) 어디서나)
"""
import errno
import os
import re
import threading
import time
import traceback
import urllib.parse
from collections import deque

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import dbg  # noqa: E402

# ── 모양 ─────────────────────────────────────────────────────
# 색은 설정 앱·탐색기와 같은 규칙(settings.css)으로 @accent·@fg·@surface 에서 만든다. 이름이 겹치지 않게 fo_ 를 붙인다.
# 모든 규칙은 .sekai-fo 창 안에만 — 부르는 프로그램의 다른 창 모양은 건드리지 않는다
CSS = """
@define-color fo_bg      mix(@surface, @fg, 0.02);
@define-color fo_card    mix(@surface, @fg, 0.07);
@define-color fo_hover   mix(@surface, @fg, 0.12);
@define-color fo_pressed mix(@surface, @fg, 0.16);
@define-color fo_foot    mix(@surface, @fg, 0.045);
@define-color fo_line    alpha(@fg, 0.08);
@define-color fo_text2   mix(@fg, @surface, 0.28);
@define-color fo_text3   mix(@fg, @surface, 0.48);
@define-color fo_on_accent mix(@accent, #000000, 0.82);
@define-color fo_warn    mix(#d69a00, @fg, 0.20);
@define-color fo_err     mix(#e0453a, @fg, 0.25);
window.sekai-fo, window.sekai-fo.background { background: @fo_bg; color: @fg; }
.sekai-fo label { color: @fg; }
.sekai-fo .fo-body { padding: 18px 22px 16px 22px; }
.sekai-fo .fo-foot { padding: 12px 22px; background: @fo_foot; border-top: 1px solid @fo_line; }
.sekai-fo label.fo-head { font-size: 13px; color: @fo_text2; }
.sekai-fo label.fo-title { font-size: 17px; font-weight: 600; }
.sekai-fo label.fo-name { font-weight: 600; }
.sekai-fo label.fo-pct { font-size: 20px; font-weight: 600; }
.sekai-fo label.fo-key { font-size: 12px; color: @fo_text2; }
.sekai-fo label.fo-val { font-size: 12px; }
.sekai-fo label.fo-sub { font-size: 12px; color: @fo_text2; }
.sekai-fo label.fo-tag { font-size: 11px; color: @accent; font-weight: 600; }
.sekai-fo label.fo-error { color: @fo_err; font-size: 12px; }
.sekai-fo image.fo-dim { color: @fo_text2; }
.sekai-fo button {
    background: @fo_hover; background-image: none;
    border: 1px solid @fo_line; border-radius: 6px;
    color: @fg; padding: 5px 16px; box-shadow: none; text-shadow: none;
}
.sekai-fo button:hover { background: @fo_pressed; }
.sekai-fo button:active { background: alpha(@accent, 0.25); }
.sekai-fo button:disabled, .sekai-fo button:disabled label { color: @fo_text3; }
.sekai-fo button.fo-accent { background: @accent; border-color: @accent; }
.sekai-fo button.fo-accent label { color: @fo_on_accent; font-weight: 600; }
.sekai-fo button.fo-accent:hover { background: mix(@accent, #ffffff, 0.12); }
.sekai-fo button.fo-flat { background: transparent; border-color: transparent; padding: 4px 8px; }
.sekai-fo button.fo-flat:hover { background: @fo_hover; }
.sekai-fo button.fo-icon { background: transparent; border-color: transparent; padding: 4px; min-width: 26px; min-height: 26px; }
.sekai-fo button.fo-icon:hover { background: @fo_hover; }
.sekai-fo button.fo-choice { background: @fo_card; border: 1px solid @fo_line; padding: 10px 14px; }
.sekai-fo button.fo-choice:hover { background: @fo_hover; border-color: alpha(@accent, 0.6); }
.sekai-fo button.fo-choice image { color: @accent; }
.sekai-fo .fo-card { background: @fo_card; border: 1px solid @fo_line; border-radius: 8px; padding: 10px 12px; }
.sekai-fo entry {
    background: @fo_bg; color: @fg; caret-color: @accent;
    border: 1px solid @fo_line; border-bottom: 2px solid alpha(@fg, 0.18);
    border-radius: 6px; padding: 5px 10px; box-shadow: none;
}
.sekai-fo entry:focus { border-bottom-color: @accent; }
.sekai-fo progressbar trough { background: alpha(@fg, 0.12); border: none; border-radius: 999px; min-height: 4px; }
.sekai-fo progressbar progress { background: @accent; border: none; border-radius: 999px; min-height: 4px; }
.sekai-fo progressbar.fo-paused progress { background: @fo_warn; }
.sekai-fo separator { background: @fo_line; min-height: 1px; min-width: 1px; }
.sekai-fo .fo-prow { padding: 16px 22px 12px 22px; }
.sekai-fo list, .sekai-fo list row { background: transparent; }
.sekai-fo list row { border-radius: 6px; padding: 6px 8px; }
.sekai-fo list row:hover { background: @fo_hover; }
.sekai-fo list row:selected { background: alpha(@accent, 0.22); }
.sekai-fo list row:selected label { color: @fg; }
.sekai-fo scrolledwindow.fo-frame { border: 1px solid @fo_line; border-radius: 8px; }
.sekai-fo notebook, .sekai-fo notebook > stack:not(:only-child) { background: transparent; border: none; box-shadow: none; }
.sekai-fo notebook > header { background: transparent; border: none; border-bottom: 1px solid @fo_line; box-shadow: none; }
.sekai-fo notebook > header tab { padding: 6px 14px; border: none; background: transparent; box-shadow: none; }
.sekai-fo notebook > header tab label { color: @fo_text2; }
.sekai-fo notebook > header tab:hover label { color: @fg; }
.sekai-fo notebook > header tab:checked { box-shadow: inset 0 -2px @accent; }
.sekai-fo notebook > header tab:checked label { color: @fg; font-weight: 600; }
.sekai-fo notebook > stack { background: transparent; }
.sekai-fo combobox button { padding: 4px 10px; }
.sekai-fo combobox window.popup, .sekai-fo menu { background: @fo_card; color: @fg; border: 1px solid @fo_line; }
"""

_css_keys = set()


def install_css(css=None, key="fileops"):
    """이 부품들의 모양을 화면에 한 번 얹는다 (부르는 프로그램이 settings.css 를 쓰든 안 쓰든 같은 모양).
    우선순위는 USER+1 — 부르는 프로그램의 일반 규칙(button 등)보다 먼저, 그래도 .sekai-fo 창 안에만"""
    if key in _css_keys:
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    prov = Gtk.CssProvider()
    try:
        prov.load_from_data((CSS if css is None else css).encode())
    except GLib.Error as e:
        print("[sekai] 파일 작업 CSS 오류:", e.message, flush=True)
        return
    Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    _css_keys.add(key)


# ── 글자 ─────────────────────────────────────────────────────
def display_text(s):
    """파일 이름 → 라벨에 넣을 글자 (UTF-8 이 아닌 이름의 깨진 바이트는 �). GTK 에 대리 문자를 넘기면 예외가 난다"""
    if s is None:
        return ""
    if isinstance(s, bytes):
        return s.decode("utf-8", "replace")
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


_JOSA = {"으로": ("으로", "로"), "을": ("을", "를"), "이": ("이", "가"), "은": ("은", "는"), "과": ("과", "와")}


def josa(word, particle):
    """받침에 맞는 조사 — josa("사진", "으로") → "으로", josa("문서", "이") → "가".
    한글·숫자로 끝나지 않으면(Desktop 등) 윈도우처럼 "(으)로"·"을(를)"·"이(가)" 로 둘 다 적는다"""
    with_b, without = _JOSA[particle]
    last = ""
    for c in reversed(word or ""):
        if not c.isspace() and c not in ")]}'\"":
            last = c
            break
    if "가" <= last <= "힣":
        jong = (ord(last) - 0xAC00) % 28
        has, rieul = jong != 0, jong == 8
    elif last and last in "0123456789":
        has, rieul = last in "013678", last in "178"      # 영 일 삼 육 칠 팔
    else:
        return "(으)로" if particle == "으로" else f"{with_b}({without})"
    if particle == "으로":
        return "로" if (not has or rieul) else "으로"
    return with_b if has else without


# 데비안의 파일 형식 이름(shared-mime-info)에 한국어가 빠진 것 — 윈도우에서 쓰는 이름으로.
#   번역돼 있는 것(JPEG 그림, PDF 문서 …)은 그대로 쓴다
_TYPE_KO = {
    "text/plain": "텍스트 문서", "inode/directory": "파일 폴더", "inode/symlink": "바로 가기",
    "application/x-shellscript": "셸 스크립트", "application/x-desktop": "바로 가기",
    "application/octet-stream": "파일", "application/x-zerosize": "빈 파일",
    "application/x-executable": "응용 프로그램", "application/x-pie-executable": "응용 프로그램",
    "application/x-sharedlib": "응용 프로그램 확장", "application/x-iso9660-image": "디스크 이미지 파일",
    "application/x-cd-image": "디스크 이미지 파일", "text/x-log": "로그 파일",
}


def type_description(ctype, name=None):
    """파일 형식의 한국어 이름 (윈도우의 "유형"). 번역이 없는 영어 이름은 윈도우처럼 "<확장자> 파일" —
    name 을 모르면 영어 그대로 둔다"""
    if not ctype:
        return "파일"
    d = _TYPE_KO.get(ctype)
    if d:
        return d
    try:
        d = Gio.content_type_get_description(ctype) or ""
    except Exception:
        d = ""
    if not any("\uac00" <= c <= "\ud7a3" for c in d) and name:
        ext = os.path.splitext(name)[1][1:]
        if ext and len(ext) <= 8 and ext.isalnum():
            return f"{ext.upper()} 파일"
    return d or ctype


def fmt_size(n):
    """윈도우처럼 세 자리 — 512 바이트, 1.23 MB, 12.3 GB, 123 KB (1000 이 넘으면 다음 단위 0.97 …)"""
    n = int(n or 0)
    if n < 1000:
        return f"{n} 바이트"
    v = float(n)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        v /= 1024.0
        if v < 1000 or unit == "PB":
            if v < 10:
                s = f"{int(v * 100) / 100:.2f}"
            elif v < 100:
                s = f"{int(v * 10) / 10:.1f}"
            else:
                s = f"{int(v)}"
            return f"{s} {unit}"
    return f"{n} 바이트"


def fmt_size_exact(n):
    """1.23 MB (1,290,000 바이트)"""
    n = int(n or 0)
    if n < 1000:
        return f"{n} 바이트"
    return f"{fmt_size(n)} ({n:,} 바이트)"


def fmt_date(ts):
    """탐색기 목록과 같은 모양 — 2026-09-25 오후 3:21"""
    if not ts:
        return ""
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d} {'오전' if t.tm_hour < 12 else '오후'} {h}:{t.tm_min:02d}"


def _fmt_eta(secs):
    secs = int(secs + 0.5)
    if secs < 60:
        return f"약 {max(1, secs)}초"
    m = (secs + 30) // 60
    if m < 60:
        return f"약 {m}분"
    h, m = divmod(m, 60)
    return f"약 {h}시간 {m}분" if m else f"약 {h}시간"


# ── 이름 ─────────────────────────────────────────────────────
_MULTI_EXT = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tar.lzma", ".tar.z")
_NUM_TAIL = re.compile(r" \((\d+)\)$")


def split_ext(name, is_dir=False):
    """이름 → (앞, 확장자). 폴더·점 파일(.bashrc)·확장자 없는 것은 확장자가 "". archive.tar.gz 는 한 덩어리"""
    if is_dir:
        return name, ""
    low = name.lower()
    for e in _MULTI_EXT:
        if low.endswith(e) and len(name) > len(e):
            return name[:-len(e)], name[-len(e):]
    stem, ext = os.path.splitext(name)
    if not stem or not ext or len(ext) > 12 or " " in ext:
        return name, ""
    return stem, ext


def numbered_name(name, n, is_dir=False):
    """둘 다 유지 — "이름 (2).ext" (이미 " (3)" 이 붙어 있으면 떼고 다시 센다)"""
    stem, ext = split_ext(name, is_dir)
    stem = _NUM_TAIL.sub("", stem) or stem
    return f"{stem} ({n}){ext}"


def copy_name(name, n, is_dir=False):
    """같은 폴더에 붙여넣기 — "이름 - 복사본.ext", "이름 - 복사본 (2).ext" """
    stem, ext = split_ext(name, is_dir)
    return f"{stem} - 복사본{ext}" if n <= 1 else f"{stem} - 복사본 ({n}){ext}"


def _free_name(dir_gfile, name, is_dir, maker=numbered_name, first=2):
    """dir 안에서 비어 있는 이름 (작업 스레드 — 파일 시스템을 본다)"""
    for n in range(first, 100000):
        cand = maker(name, n, is_dir)
        if not dir_gfile.get_child(cand).query_exists(None):
            return cand
    return maker(name, int(time.time()), is_dir)


def validate_name(name):
    """이름 검사 — 괜찮으면 None, 아니면 이유 (윈도우 탐색기의 말투)"""
    if not name or not name.strip():
        return "파일 이름을 입력해야 합니다."
    if "/" in name or "\0" in name:
        return "파일 이름에는 다음 문자를 사용할 수 없습니다: /"
    if name.strip() in (".", ".."):
        return "'.' 또는 '..'은(는) 이름으로 사용할 수 없습니다."
    if len(name.encode("utf-8", "surrogateescape")) > 255:
        return "이름이 너무 깁니다. 더 짧은 이름을 입력하세요."
    return None


# ── 오류 설명 ────────────────────────────────────────────────
_IOE = Gio.IOErrorEnum
_NET_CODES = (_IOE.HOST_NOT_FOUND, _IOE.HOST_UNREACHABLE, _IOE.NETWORK_UNREACHABLE, _IOE.TIMED_OUT,
              _IOE.NOT_MOUNTED, _IOE.CONNECTION_REFUSED, _IOE.CONNECTION_CLOSED, _IOE.BROKEN_PIPE,
              _IOE.NOT_CONNECTED, _IOE.CLOSED)


def _is(err, *codes):
    return isinstance(err, GLib.Error) and any(err.matches(Gio.io_error_quark(), c) for c in codes)


def explain(err):
    """GLib.Error·OSError → 사람이 읽을 이유 한두 문장"""
    if isinstance(err, OSError) and not isinstance(err, GLib.Error):
        return _explain_errno(err.errno, err.strerror)
    if not isinstance(err, GLib.Error):
        return str(err) or "알 수 없는 오류가 발생했습니다."
    if _is(err, _IOE.PERMISSION_DENIED):
        return "권한이 없습니다. 소유자나 관리자의 허락이 필요합니다."
    if _is(err, _IOE.NO_SPACE):
        return "대상 드라이브에 공간이 부족합니다. 공간을 확보한 뒤 다시 시도하세요."
    if _is(err, _IOE.READ_ONLY):
        return "읽기 전용 드라이브라 바꿀 수 없습니다."
    if _is(err, _IOE.NOT_FOUND):
        return "항목을 찾을 수 없습니다. 이미 옮겨졌거나 삭제되었을 수 있습니다."
    if _is(err, _IOE.FILENAME_TOO_LONG):
        return "이름이 너무 깁니다. 대상 드라이브에서 쓸 수 있는 길이를 넘습니다."
    if _is(err, _IOE.INVALID_FILENAME):
        return "대상 드라이브에서 쓸 수 없는 문자가 이름에 들어 있습니다."
    if _is(err, _IOE.BUSY):
        return "다른 프로그램이 이 항목을 사용하고 있습니다."
    if _is(err, _IOE.NOT_EMPTY):
        return "폴더가 비어 있지 않습니다."
    if _is(err, _IOE.TOO_MANY_OPEN_FILES):
        return "열린 파일이 너무 많습니다. 잠시 뒤 다시 시도하세요."
    if _is(err, _IOE.TOO_MANY_LINKS):
        return "링크가 너무 여러 번 이어져 있습니다."
    if _is(err, *_NET_CODES):
        return "드라이브나 네트워크 위치와 연결이 끊어졌습니다."
    if _is(err, _IOE.NOT_SUPPORTED, _IOE.NOT_REGULAR_FILE):
        return "이 위치나 이 종류의 항목(장치·소켓 같은 특수 파일)에서는 할 수 없는 작업입니다."
    if "too large" in (err.message or "").lower():
        return "파일이 너무 커서 대상 드라이브에 넣을 수 없습니다."
    return "오류가 발생했습니다."


def _explain_errno(no, text=""):
    if no in (errno.EACCES, errno.EPERM):
        return "권한이 없습니다. 소유자나 관리자의 허락이 필요합니다."
    if no in (errno.ENOSPC, errno.EDQUOT):
        return "대상 드라이브에 공간이 부족합니다. 공간을 확보한 뒤 다시 시도하세요."
    if no == errno.EROFS:
        return "읽기 전용 드라이브라 바꿀 수 없습니다."
    if no == errno.ENOENT:
        return "항목을 찾을 수 없습니다. 이미 옮겨졌거나 삭제되었을 수 있습니다."
    if no == errno.ENAMETOOLONG:
        return "이름이 너무 깁니다. 더 짧은 이름을 입력하세요."
    if no == errno.EINVAL:
        return "이 드라이브에서는 이름에 쓸 수 없는 문자가 들어 있습니다."
    if no == errno.EBUSY:
        return "다른 프로그램이 이 항목을 사용하고 있습니다."
    if no == errno.EEXIST:
        return "같은 이름의 항목이 이미 있습니다."
    if no == errno.EFBIG:
        return "파일이 너무 커서 대상 드라이브에 넣을 수 없습니다."
    if no == errno.EIO:
        return "드라이브를 읽거나 쓰는 중 입출력 오류가 났습니다. 드라이브 연결을 확인하세요."
    if no == errno.EXDEV:
        return "다른 드라이브로는 이렇게 옮길 수 없습니다."
    return "오류가 발생했습니다."


def _detail(err):
    """창에 작게 붙일 원래 오류 글"""
    if err is None:
        return ""
    msg = getattr(err, "message", None) if isinstance(err, GLib.Error) else getattr(err, "strerror", None)
    return display_text(msg or str(err) or "")


# ── 작은 도움 ────────────────────────────────────────────────
NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
COPY_FLAGS = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA
SCAN_ATTRS = "standard::name,standard::display-name,standard::type,standard::size,id::filesystem"
META_ATTRS = ("standard::name,standard::display-name,standard::type,standard::size,standard::icon,"
              "standard::content-type,time::modified")
ITEM_WEIGHT = 16 * 1024          # 진행률에서 항목 하나의 무게 — 작은 파일이 많아도 막대가 움직이게
SHOW_DELAY = 500                 # 이보다 오래 걸리는 작업만 진행 창


def _gfile(x):
    if isinstance(x, Gio.File):
        return x
    if isinstance(x, str) and "://" in x and not x.startswith("/"):
        return Gio.File.new_for_uri(x)
    return Gio.File.new_for_path(str(x))


def _idle(fn, *args):
    def run():
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()
        return False
    GLib.idle_add(run)


def run_in_thread(work, done=None):
    """work() 를 작업 스레드에서, 끝나면 메인 스레드에서 done(결과, 예외 또는 None)"""
    def go():
        try:
            res, exc = work(), None
        except Exception as e:
            res, exc = None, e
        if done is not None:
            _idle(done, res, exc)
    threading.Thread(target=go, daemon=True, name="sekai-fileops-bg").start()


def _qinfo(f, attrs=SCAN_ATTRS, cancellable=None):
    """정보 — 없으면 None (링크는 따라가지 않는다). 다른 오류는 그대로 올린다"""
    try:
        return f.query_info(attrs, NOFOLLOW, cancellable)
    except GLib.Error as e:
        if _is(e, _IOE.NOT_FOUND):
            return None
        raise


def _info_name(f, info=None):
    if info is not None:
        dn = info.get_display_name() if info.has_attribute("standard::display-name") else None
        if dn:
            return dn
    return display_text(f.get_basename() or f.get_uri())


def _is_dir(info):
    return info is not None and info.get_file_type() == Gio.FileType.DIRECTORY


def _meta(f, info):
    """충돌 창에 보여 줄 것"""
    ct = info.get_content_type() if info.has_attribute("standard::content-type") else None
    mt = info.get_attribute_uint64("time::modified") if info.has_attribute("time::modified") else 0
    return {"dir": _is_dir(info), "size": info.get_size(), "mtime": mt, "ctype": ct,
            "icon": info.get_icon() if info.has_attribute("standard::icon") else None}


def _place_name(f):
    """진행 창에 쓸 폴더 이름 (작업 스레드) — 드라이브 뿌리면 드라이브 이름"""
    if f is None:
        return ""
    if f.get_uri_scheme() == "trash":
        return "휴지통"
    path = f.get_path()
    if path == "/":
        return "파일 시스템"
    try:
        m = f.find_enclosing_mount(None)
        if m is not None and m.get_root().equal(f):
            return display_text(m.get_name())
    except GLib.Error:
        pass
    try:
        return f.query_info("standard::display-name", 0, None).get_display_name()
    except GLib.Error:
        return display_text(f.get_basename() or f.get_uri())


def _real(f):
    p = f.get_path() if f is not None else None
    return os.path.realpath(p) if p else None


# ── 창 ───────────────────────────────────────────────────────
def _alive(w):
    return w is not None and not getattr(w, "_fo_dead", False) and w.get_mapped()


def _track(w):
    """부모 창이 사라졌는지 알 수 있게 (파괴된 창을 부모로 삼으면 GTK 가 경고한다)"""
    if w is not None and not getattr(w, "_fo_tracked", False):
        w._fo_tracked = True
        w.connect("destroy", lambda *_: setattr(w, "_fo_dead", True))
    return w


def dialog_window(parent, title, modal=True, width=440, resizable=False):
    """이 모듈 모양의 창 — (창, 본문 상자, 아래 단추 상자). 단추는 오른쪽 정렬로 pack_start 한다"""
    install_css()
    w = Gtk.Window(title=title)
    w.get_style_context().add_class("sekai-fo")
    w.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    if _alive(parent):
        w.set_transient_for(parent)
        w.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    else:
        w.set_position(Gtk.WindowPosition.CENTER)
    w.set_modal(modal)
    w.set_resizable(resizable)
    w.set_default_size(width, -1)
    w.set_icon_name("system-file-manager")
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    body.get_style_context().add_class("fo-body")
    foot_band = Gtk.Box()
    foot_band.get_style_context().add_class("fo-foot")
    foot = Gtk.Box(spacing=8)
    foot.set_halign(Gtk.Align.END)
    foot.set_hexpand(True)
    foot_band.pack_start(foot, True, True, 0)
    outer.pack_start(body, True, True, 0)
    outer.pack_end(foot_band, False, False, 0)
    w.add(outer)
    w.foot_band = foot_band
    _track(w)
    return w, body, foot


def _label(text, cls=None, wrap=True, xalign=0.0, selectable=False, width_chars=None):
    lbl = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if width_chars:
        lbl.set_max_width_chars(width_chars)
    if selectable:
        lbl.set_selectable(True)
        lbl.set_can_focus(False)
    if cls:
        for c in cls.split():
            lbl.get_style_context().add_class(c)
    return lbl


def _button(label, accent=False, cb=None):
    b = Gtk.Button(label=label)
    if accent:
        b.get_style_context().add_class("fo-accent")
    if cb is not None:
        b.connect("clicked", lambda *_: cb())
    return b


def _icon(names_or_gicon, size=32):
    if isinstance(names_or_gicon, Gio.Icon):
        img = Gtk.Image.new_from_gicon(names_or_gicon, Gtk.IconSize.DIALOG)
    else:
        th = Gtk.IconTheme.get_default()
        names = [names_or_gicon] if isinstance(names_or_gicon, str) else list(names_or_gicon)
        name = next((n for n in names if n and th.has_icon(n)), names[-1] if names else "text-x-generic")
        img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    img.set_valign(Gtk.Align.START)
    return img


def ask_choice(parent, title, heading, text, choices, on_answer, default=None,
               icon="dialog-warning-symbolic", extra=None, modal=True):
    """묻는 창. choices = [(값, 글, 강조?)] — 누른 값으로 on_answer(값), 닫기·Esc 는 on_answer(None).
    default 값의 단추에 처음 초점이 간다 (되돌릴 수 없는 일에는 "아니요"). 창을 돌려준다"""
    w, body, foot = dialog_window(parent, title, modal=modal, width=460)
    answered = []

    def answer(v):
        if answered:
            return
        answered.append(v)
        w.destroy()
        on_answer(v)

    row = Gtk.Box(spacing=14)
    if icon:
        img = _icon(icon, 32)
        img.get_style_context().add_class("fo-dim")
        row.pack_start(img, False, False, 0)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if heading:
        col.pack_start(_label(heading, "fo-title", width_chars=46), False, False, 0)
    if text:
        col.pack_start(_label(text, width_chars=52), False, False, 0)
    if extra is not None:
        col.pack_start(extra, False, False, 4)
    row.pack_start(col, True, True, 0)
    body.pack_start(row, True, True, 0)
    focus = None
    for val, lbl, accent in choices:
        b = _button(lbl, accent, lambda v=val: answer(v))
        b.set_size_request(96, -1)
        foot.pack_start(b, False, False, 0)
        if val == default:
            focus = b
    w.connect("delete-event", lambda *_: (answer(None), True)[1])
    w.connect("key-press-event", lambda _w, ev: (answer(None), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
    w.show_all()
    if focus is not None:
        focus.grab_focus()
    return w


def notice(parent, title, heading, text="", icon="dialog-information-symbolic", on_close=None):
    """알리기만 — [확인]"""
    return ask_choice(parent, title, heading, text, [(True, "확인", True)],
                      lambda _v: on_close and on_close(), default=True, icon=icon)


def _error_notice(parent, title, heading, err):
    notice(parent, title, heading, explain(err) + ("\n" + _detail(err) if _detail(err) else ""),
           icon="dialog-error-symbolic")


# ── 작업 ─────────────────────────────────────────────────────
class Cancelled(Exception):
    """작업 스레드 안에서 — 사용자가 취소했다"""


class Job:
    """작업 하나. 작업 스레드가 fn(job) 을 돌리고, 진행 창 한 줄·묻는 창은 메인 스레드가 맡는다.

    메인 스레드:  Job(kind, parent, done).start(fn)   kind: copy·move·delete·trash·restore·empty·extract·compress
                  .cancel() · .pause() · .resume()
    작업 스레드 (fn 안):
        job.title = "항목 N개를 …하는 중"          진행 창 맨 위 글
        job.total_items / job.total_bytes          합계 (늘려도 된다). job.scanning = False 로 두면 %를 보인다
        job.item_weight = 0                        항목 수를 미리 모를 때 — 진행률을 바이트로만
        job.item_done(items=1, nbytes=0)           끝난 몫 · job.cur_bytes 지금 파일에서 옮긴 바이트
        job.cur_name                               지금 파일 이름 (자세히)
        job.checkpoint()                           멈춤이면 기다리고, 취소면 Cancelled
        job.hold_if_paused()                       Gio 진행 콜백 안처럼 예외를 던질 수 없는 곳
        job.conflict(name, src_meta, dst_meta, keep_name, nested=False) -> "replace"|"skip"|"keep"|"merge"
            meta = {"dir": bool, "size": int, "mtime": 초, "ctype": str|None, "icon": Gio.Icon|None}
        job.error(err, name, what, reason=None, can_retry=True) -> "retry"|"skip"   (취소면 Cancelled)
        job.ask(build) -> 값      build(reply) 가 메인 스레드에서 창을 띄우고 reply(값) — None 이면 Cancelled
        job.results.append(gfile)                  done 에 넘길 새 자리
        job.notes.append(text)                     끝난 뒤 알릴 것 (작업이 끝나면 한 창으로 보여 준다)
    fn 이 False 를 돌려주면 ok=False 로 끝난다.
    """

    ERR_TITLES = {
        "copy": ("파일을 복사할 수 없습니다", "복사할"), "move": ("항목을 이동할 수 없습니다", "이동할"),
        "delete": ("항목을 삭제할 수 없습니다", "삭제할"), "trash": ("휴지통으로 이동할 수 없습니다", "휴지통으로 이동할"),
        "restore": ("항목을 복원할 수 없습니다", "복원할"), "mkdir": ("폴더를 만들 수 없습니다", "만들"),
        "read": ("폴더를 읽을 수 없습니다", "읽을"), "extract": ("압축을 풀 수 없습니다", "풀"),
        "write": ("파일을 쓸 수 없습니다", "쓸"), "compress": ("압축할 수 없습니다", "압축할"),
    }

    def __init__(self, kind, parent=None, done=None):
        self.kind = kind
        self.parent = _track(parent)
        self.done_cb = done
        self.cancellable = Gio.Cancellable()
        self.title = "준비하는 중…"
        self.total_items = self.total_bytes = 0
        self.done_items = self.done_bytes = 0
        self.cur_bytes = 0
        self.cur_name = ""
        self.scanning = True
        self.item_weight = ITEM_WEIGHT        # 0 이면 진행률은 바이트만 (항목 수를 미리 모를 때 — tar 등)
        self.results = []
        self.notes = []
        self.finished = False
        self.ok = False
        self.row = None
        self._run = threading.Event()
        self._run.set()                       # 풀려 있으면 달린다 (멈춤 = clear)
        self._t0 = time.monotonic()
        self._idle_total = 0.0                # 멈춤·묻는 창에 쓴 시간 (속도·남은 시간에서 뺀다)
        self._idle_from = None
        self._idle_lock = threading.Lock()
        self._samples = deque(maxlen=48)
        self._policy = {}                     # 충돌 "모두 적용": {"file"|"dir"|"mixed": 선택}
        self._err_policy = {}                 # 오류 "모두 건너뛰기": {(what, 코드)}
        self.conflicts_left = {"file": 0, "dir": 0, "mixed": 0}
        self._replies = set()
        self._dialogs = set()

    # ── 메인 스레드 ──
    def start(self, fn):
        def run():
            ok = False
            try:
                ok = fn(self) is not False and not self.cancellable.is_cancelled()
            except Cancelled:
                ok = False
            except Exception:
                traceback.print_exc()
                ok = False
            _idle(self._finish, ok)
        threading.Thread(target=run, daemon=True, name=f"sekai-fileops-{self.kind}").start()
        GLib.timeout_add(SHOW_DELAY, self._maybe_show)
        return self

    def _maybe_show(self):
        if self.finished or self.row is not None:
            return False
        # 묻는 창(충돌·오류)에 답을 기다리는 동안에는 띄우지 않는다 — 나중에 뜬 진행 창이 먼저 뜬
        #   묻는 창을 덮어 단추를 가렸다. 답한 뒤에도 아직 돌고 있으면 다음 차례에 띄운다
        if self._dialogs or self._idle_from is not None:
            return True
        _ProgressWindow.get().add_job(self)
        return False

    def _finish(self, ok):
        self.finished = True
        self.ok = ok
        for d in list(self._dialogs):
            d.destroy()
        if self.row is not None:
            _ProgressWindow.get().remove_job(self)
        if self.notes:
            notice(self.dialog_parent(), "알림", self.notes[0], "\n".join(self.notes[1:]))
        if self.done_cb is not None:
            try:
                self.done_cb(ok, list(self.results))
            except Exception:
                traceback.print_exc()

    def cancel(self):
        self.cancellable.cancel()
        self._run.set()
        for r in list(self._replies):
            r(None)
        for d in list(self._dialogs):
            d.destroy()

    def pause(self):
        if self._run.is_set():
            self._run.clear()
            self._idle_begin()

    def resume(self):
        if not self._run.is_set():
            self._idle_end()
            self._run.set()

    @property
    def paused(self):
        return not self._run.is_set()

    def dialog_parent(self):
        pw = _ProgressWindow.inst
        if self.row is not None and pw is not None and _alive(pw):
            return pw
        return self.parent if _alive(self.parent) else None

    # ── 시간 ──
    def _idle_begin(self):
        with self._idle_lock:
            if self._idle_from is None:
                self._idle_from = time.monotonic()

    def _idle_end(self):
        with self._idle_lock:
            if self._idle_from is not None:
                self._idle_total += time.monotonic() - self._idle_from
                self._idle_from = None

    def active_time(self):
        with self._idle_lock:
            idle = self._idle_total + (time.monotonic() - self._idle_from if self._idle_from else 0)
        return time.monotonic() - self._t0 - idle

    # ── 진행 ──
    def item_done(self, items=1, nbytes=0):
        self.done_items += items
        self.done_bytes += nbytes

    def units(self):
        return self.done_bytes + self.cur_bytes + self.done_items * self.item_weight

    def total_units(self):
        return self.total_bytes + self.total_items * self.item_weight

    def fraction(self):
        tot = self.total_units()
        if tot <= 0:
            return 0.0
        return max(0.0, min(0.99, self.units() / tot))

    def sample(self):
        """진행 창이 틱마다 — 최근 몇 초의 속도를 잰다"""
        if not self.paused:
            self._samples.append((self.active_time(), self.done_bytes + self.cur_bytes, self.units()))

    def rates(self):
        """(바이트/초, 무게/초) — 자료가 모자라면 None"""
        s = self._samples
        if len(s) < 3:
            return None
        t1, b1, u1 = s[-1]
        for t0, b0, u0 in s:
            if t1 - t0 <= 5.0:
                break
        dt = t1 - t0
        if dt < 1.0:
            return None
        return (b1 - b0) / dt, (u1 - u0) / dt

    # ── 작업 스레드 ──
    def checkpoint(self):
        if self.cancellable.is_cancelled():
            raise Cancelled()
        if not self._run.is_set():
            self._run.wait()
            if self.cancellable.is_cancelled():
                raise Cancelled()

    def hold_if_paused(self):
        if not self._run.is_set():
            self._run.wait()

    def ask(self, build):
        """build(reply) 를 메인 스레드에서 부르고 (창을 돌려주면 취소할 때 닫는다) reply(값) 를 기다린다"""
        if self.cancellable.is_cancelled():
            raise Cancelled()
        ev = threading.Event()
        box = []

        def reply(v):
            if not box:
                box.append(v)
                ev.set()

        def show():
            if self.cancellable.is_cancelled():
                reply(None)
                return
            try:
                w = build(reply)
            except Exception:
                traceback.print_exc()
                reply(None)
                return
            if isinstance(w, Gtk.Widget):
                self._dialogs.add(w)
                w.connect("destroy", lambda *_: self._dialogs.discard(w))
        self._replies.add(reply)
        self._idle_begin()
        _idle(show)
        ev.wait()
        self._idle_end()
        self._replies.discard(reply)
        if box[0] is None or self.cancellable.is_cancelled():
            raise Cancelled()
        return box[0]

    def conflict(self, name, src_meta, dst_meta, keep_name, nested=False):
        s_dir, d_dir = src_meta["dir"], dst_meta["dir"]
        kind = "dir" if (s_dir and d_dir) else "file" if not (s_dir or d_dir) else "mixed"
        if kind in self._policy:
            return self._policy[kind]
        left = self.conflicts_left.get(kind, 0)
        choice, for_all = self.ask(lambda reply: _conflict_dialog(self, reply, display_text(name), src_meta,
                                                                  dst_meta, kind, left, nested, keep_name))
        if for_all:
            self._policy[kind] = choice
        if not nested and left:
            self.conflicts_left[kind] = left - 1
        return choice

    def error(self, err, name, what, reason=None, can_retry=True):
        code = getattr(err, "code", None) if isinstance(err, GLib.Error) else getattr(err, "errno", None)
        if (what, code) in self._err_policy:
            return "skip"
        ans, for_all = self.ask(lambda reply: _error_dialog(self, reply, what, display_text(name),
                                                            reason or explain(err), _detail(err), can_retry))
        if for_all and ans == "skip":
            self._err_policy[(what, code)] = True
        return ans


# ── 진행 창 ──────────────────────────────────────────────────
_details_open = [False]          # "자세히" — 윈도우처럼 한 번 펼치면 다음 작업도 펼쳐 둔다


class _ProgressRow(Gtk.Box):
    def __init__(self, job):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_style_context().add_class("fo-prow")
        self.job = job
        self.head = _label("", "fo-head", width_chars=56)
        self.pack_start(self.head, False, False, 0)

        top = Gtk.Box(spacing=6)
        self.pct = _label("", "fo-pct", wrap=False)
        top.pack_start(self.pct, True, True, 0)
        self.pause_btn = Gtk.Button()
        self.pause_btn.get_style_context().add_class("fo-icon")
        self.pause_img = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON)
        self.pause_btn.add(self.pause_img)
        self.pause_btn.set_tooltip_text("일시 중지")
        self.pause_btn.connect("clicked", self._toggle_pause)
        top.pack_start(self.pause_btn, False, False, 0)
        stop = Gtk.Button()
        stop.get_style_context().add_class("fo-icon")
        stop.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON))
        stop.set_tooltip_text("취소")
        stop.connect("clicked", lambda *_: job.cancel())
        top.pack_start(stop, False, False, 0)
        self.pack_start(top, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.pack_start(self.bar, False, False, 2)

        more = Gtk.Button()
        more.get_style_context().add_class("fo-flat")
        mb = Gtk.Box(spacing=6)
        self.more_img = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        self.more_lbl = Gtk.Label(label="자세히")
        mb.pack_start(self.more_img, False, False, 0)
        mb.pack_start(self.more_lbl, False, False, 0)
        more.add(mb)
        more.set_halign(Gtk.Align.START)
        more.connect("clicked", self._toggle_details)
        self.pack_start(more, False, False, 0)

        self.rev = Gtk.Revealer()
        self.rev.set_transition_duration(120)
        grid = Gtk.Grid(column_spacing=14, row_spacing=4)
        grid.set_margin_start(8)
        self.vals = {}
        for i, key in enumerate(("이름", "남은 시간", "남은 항목", "속도")):
            k = _label(key + ":", "fo-key", wrap=False)
            v = _label("", "fo-val", wrap=False)
            v.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            v.set_max_width_chars(44)
            v.set_hexpand(True)
            grid.attach(k, 0, i, 1, 1)
            grid.attach(v, 1, i, 1, 1)
            self.vals[key] = v
        self.rev.add(grid)
        self.pack_start(self.rev, False, False, 0)
        self._sync_details()

    def _toggle_pause(self, *_):
        j = self.job
        if j.paused:
            j.resume()
        else:
            j.pause()
        self.update()

    def _toggle_details(self, *_):
        _details_open[0] = not _details_open[0]
        pw = _ProgressWindow.inst
        for r in (pw.rows if pw else [self]):
            r._sync_details()

    def _sync_details(self):
        on = _details_open[0]
        self.rev.set_reveal_child(on)
        self.more_lbl.set_text("간단히" if on else "자세히")
        self.more_img.set_from_icon_name("pan-up-symbolic" if on else "pan-down-symbolic", Gtk.IconSize.MENU)

    def pct_text(self):
        j = self.job
        if j.scanning:
            return "계산 중…"
        return f"{int(j.fraction() * 100)}% 완료"

    def update(self):
        j = self.job
        j.sample()
        self.head.set_text(j.title)
        paused = j.paused
        self.pause_img.set_from_icon_name("media-playback-start-symbolic" if paused else
                                          "media-playback-pause-symbolic", Gtk.IconSize.BUTTON)
        self.pause_btn.set_tooltip_text("다시 시작" if paused else "일시 중지")
        ctx = self.bar.get_style_context()
        (ctx.add_class if paused else ctx.remove_class)("fo-paused")
        if j.scanning:
            self.pct.set_text("계산 중…")
            self.bar.pulse()
        else:
            self.pct.set_text(self.pct_text() + (" · 일시 중지됨" if paused else ""))
            self.bar.set_fraction(j.fraction())
        v = self.vals
        v["이름"].set_text(display_text(j.cur_name) or "-")
        left_items = max(0, j.total_items - j.done_items)
        left_bytes = max(0, j.total_bytes - j.done_bytes - j.cur_bytes)
        if j.scanning:
            v["남은 항목"].set_text("계산 중…")
        elif j.total_items > 0:
            v["남은 항목"].set_text(f"{left_items:,}개 ({fmt_size(left_bytes)})")
        else:
            v["남은 항목"].set_text(fmt_size(left_bytes))
        r = j.rates()
        if paused:
            v["남은 시간"].set_text("일시 중지됨")
        elif r is None or j.scanning:
            v["남은 시간"].set_text("계산 중…")
            v["속도"].set_text("계산 중…")
        else:
            bps, ups = r
            rest = max(0, j.total_units() - j.units())
            v["남은 시간"].set_text(_fmt_eta(rest / ups) if ups > 0 else "계산 중…")
            v["속도"].set_text(f"{fmt_size(bps)}/초" if bps >= 1 else "-")


class _ProgressWindow(Gtk.Window):
    """진행 중인 작업들 — 한 창에 줄 하나씩 (윈도우처럼). 마지막 작업이 끝나면 닫힌다"""
    inst = None

    @classmethod
    def get(cls):
        if cls.inst is None:
            cls.inst = cls()
        return cls.inst

    def __init__(self):
        super().__init__(title="")
        install_css()
        _track(self)
        self.get_style_context().add_class("sekai-fo")
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_resizable(False)
        self.set_default_size(500, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("system-file-manager")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.box)
        self.box.show()
        self.rows = []
        self._tick = 0
        self.connect("delete-event", self._on_close)

    def add_job(self, job):
        if self.rows:
            sep = Gtk.Separator()
            self.box.pack_start(sep, False, False, 0)
            sep.show()
        row = _ProgressRow(job)
        row.sep = self.box.get_children()[-1] if self.rows else None
        self.box.pack_start(row, False, False, 0)
        row.show_all()
        self.rows.append(row)
        job.row = row
        if not self._tick:
            self._tick = GLib.timeout_add(250, self._update)
        self._update()
        self.show()

    def remove_job(self, job):
        row = job.row
        job.row = None
        if row in self.rows:
            i = self.rows.index(row)
            self.rows.remove(row)
            if row.sep is not None:
                row.sep.destroy()
            elif self.rows and i == 0 and self.rows[0].sep is not None:
                self.rows[0].sep.destroy()               # 맨 위 줄이 빠지면 다음 줄 위의 구분선도
                self.rows[0].sep = None
            row.destroy()
        if not self.rows:
            if self._tick:
                GLib.source_remove(self._tick)
                self._tick = 0
            _ProgressWindow.inst = None
            self.destroy()
        else:
            self.resize(1, 1)                           # 줄이 빠진 만큼 줄인다
            self._update()

    def _update(self):
        for r in self.rows:
            r.update()
        n = len(self.rows)
        self.set_title(self.rows[0].pct_text() if n == 1 else f"작업 {n}개 진행 중")
        return True

    def _on_close(self, *_):
        # 윈도우처럼 진행 창을 닫으면 작업을 취소한다 (끝나면 스스로 사라진다)
        for r in list(self.rows):
            r.job.cancel()
        return True


# ── 묻는 창 (작업 스레드가 job.ask 로) ───────────────────────
def _meta_card(title, name, meta, tags):
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.get_style_context().add_class("fo-card")
    card.pack_start(_label(title, "fo-key", wrap=False), False, False, 0)
    row = Gtk.Box(spacing=10)
    icon = meta.get("icon")
    if icon is None:
        ct = meta.get("ctype") or ("inode/directory" if meta["dir"] else "application/octet-stream")
        icon = Gio.content_type_get_icon(ct)
    row.pack_start(_icon(icon, 40), False, False, 0)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    nl = _label(name, "fo-name", wrap=False)
    nl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    nl.set_max_width_chars(22)
    col.pack_start(nl, False, False, 0)
    if meta["dir"]:
        col.pack_start(_label("파일 폴더", "fo-sub", wrap=False), False, False, 0)
    else:
        col.pack_start(_label(f"크기: {fmt_size(meta.get('size') or 0)}", "fo-sub", wrap=False), False, False, 0)
    if meta.get("mtime"):
        col.pack_start(_label(f"수정한 날짜: {fmt_date(meta['mtime'])}", "fo-sub", wrap=False), False, False, 0)
    for t in tags:
        col.pack_start(_label(t, "fo-tag", wrap=False), False, False, 0)
    row.pack_start(col, True, True, 0)
    card.pack_start(row, False, False, 0)
    return card


def _choice_button(icon, title, sub, cb):
    b = Gtk.Button()
    b.get_style_context().add_class("fo-choice")
    h = Gtk.Box(spacing=12)
    img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    img.set_valign(Gtk.Align.CENTER)
    h.pack_start(img, False, False, 0)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    v.pack_start(_label(title, "fo-name", wrap=False), False, False, 0)
    if sub:
        s = _label(sub, "fo-sub", wrap=True, width_chars=58)
        v.pack_start(s, False, False, 0)
    h.pack_start(v, True, True, 0)
    b.add(h)
    b.connect("clicked", lambda *_: cb())
    return b


def _conflict_dialog(job, reply, name, sm, dm, kind, left, nested, keep_name):
    what = {"dir": "폴더", "file": "파일"}.get(kind, "항목")
    title = "폴더 병합 또는 건너뛰기" if kind == "dir" else "파일 바꾸기 또는 건너뛰기"
    w, body, foot = dialog_window(job.dialog_parent(), title, modal=False, width=560)
    done = []

    def answer(v):
        if done:
            return
        done.append(v)
        w.destroy()
        reply(None if v is None else (v, check.get_active()))

    body.pack_start(_label(job.title, "fo-head", width_chars=64), False, False, 0)
    head = (f"대상 폴더에 이름이 같은 {what}{josa(what, '이')} {left}개 있습니다" if left > 1 else
            f"대상 폴더에 이름이 같은 {what}{josa(what, '이')} 있습니다")
    body.pack_start(_label(head, "fo-title", width_chars=52), False, False, 0)
    s_tags, d_tags = [], []
    if not sm["dir"] and not dm["dir"]:
        if sm.get("mtime") and dm.get("mtime") and sm["mtime"] != dm["mtime"]:
            (s_tags if sm["mtime"] > dm["mtime"] else d_tags).append("더 최신")
        if (sm.get("size") or 0) != (dm.get("size") or 0):
            (s_tags if (sm.get("size") or 0) > (dm.get("size") or 0) else d_tags).append("더 큼")
    cards = Gtk.Box(spacing=10, homogeneous=True)
    cards.pack_start(_meta_card("원본", name, sm, s_tags), True, True, 0)
    cards.pack_start(_meta_card("대상 폴더에 있는 것", name, dm, d_tags), True, True, 0)
    body.pack_start(cards, False, False, 4)

    keep = display_text(keep_name)
    if kind == "file":
        body.pack_start(_choice_button("object-select-symbolic", "파일 바꾸기",
                                       "대상 폴더의 파일을 원본 파일로 바꿉니다.",
                                       lambda: answer("replace")), False, False, 0)
        body.pack_start(_choice_button("go-next-symbolic", "파일 건너뛰기",
                                       "대상 폴더의 파일을 그대로 둡니다.",
                                       lambda: answer("skip")), False, False, 0)
    elif kind == "dir":
        body.pack_start(_choice_button("object-select-symbolic", "폴더 병합",
                                       "두 폴더의 내용을 합칩니다. 이름이 같은 파일이 있으면 따로 묻습니다.",
                                       lambda: answer("merge")), False, False, 0)
        body.pack_start(_choice_button("go-next-symbolic", "폴더 건너뛰기",
                                       "대상 폴더의 폴더를 그대로 둡니다.",
                                       lambda: answer("skip")), False, False, 0)
    else:
        body.pack_start(_choice_button("go-next-symbolic", "건너뛰기", "대상 폴더의 항목을 그대로 둡니다.",
                                       lambda: answer("skip")), False, False, 0)
    body.pack_start(_choice_button("edit-copy-symbolic", "둘 다 유지",
                                   f"옮기는 {what}의 이름을 '{keep}'(으)로 바꿉니다.",
                                   lambda: answer("keep")), False, False, 0)

    check = Gtk.CheckButton(label="나머지 충돌에 모두 적용")
    band = w.foot_band
    band.pack_start(check, False, False, 0)
    band.reorder_child(check, 0)
    check.set_no_show_all(not (left > 1 or nested))
    foot.pack_start(_button("취소", cb=lambda: answer(None)), False, False, 0)
    w.connect("delete-event", lambda *_: (answer(None), True)[1])
    w.connect("key-press-event", lambda _w, ev: (answer(None), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
    w.show_all()
    return w


def _error_dialog(job, reply, what, name, reason, detail, can_retry):
    title, verb = Job.ERR_TITLES.get(what, ("작업을 할 수 없습니다", "처리할"))
    w, body, foot = dialog_window(job.dialog_parent(), title, modal=False, width=540)
    done = []

    def answer(v):
        if done:
            return
        done.append(v)
        w.destroy()
        reply(None if v is None else (v, check.get_active()))

    body.pack_start(_label(job.title, "fo-head", width_chars=60), False, False, 0)
    row = Gtk.Box(spacing=14)
    img = _icon("dialog-error-symbolic", 32)
    img.get_style_context().add_class("fo-dim")
    row.pack_start(img, False, False, 0)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    col.pack_start(_label(f"'{name}'{josa(name, '을')} {verb} 수 없습니다.", "fo-title", width_chars=46),
                   False, False, 0)
    col.pack_start(_label(reason, width_chars=52), False, False, 0)
    if detail:
        col.pack_start(_label(detail, "fo-sub", width_chars=60, selectable=True), False, False, 0)
    row.pack_start(col, True, True, 0)
    body.pack_start(row, False, False, 0)

    check = Gtk.CheckButton(label="같은 오류가 또 나면 모두 건너뛰기")
    band = w.foot_band
    band.pack_start(check, False, False, 0)
    band.reorder_child(check, 0)
    check.set_no_show_all(job.total_items <= 1)
    first = None
    if can_retry:
        first = _button("다시 시도", True, lambda: answer("retry"))
        foot.pack_start(first, False, False, 0)
    skip = _button("건너뛰기", not can_retry, lambda: answer("skip"))
    foot.pack_start(skip, False, False, 0)
    foot.pack_start(_button("취소", cb=lambda: answer(None)), False, False, 0)
    w.connect("delete-event", lambda *_: (answer(None), True)[1])
    w.connect("key-press-event", lambda _w, ev: (answer(None), True)[1] if ev.keyval == Gdk.KEY_Escape else False)
    w.show_all()
    (first or skip).grab_focus()
    return w


# ── 복사·이동 엔진 (작업 스레드) ─────────────────────────────
def _count(job, f, info):
    """폴더 안까지 센다 — (항목 수, 바이트). 읽을 수 없는 폴더는 1개로"""
    items, nbytes = 0, 0
    stack = [(f, info)]
    while stack:
        job.checkpoint()
        cur, ci = stack.pop()
        items += 1
        if ci.get_file_type() == Gio.FileType.REGULAR:
            nbytes += ci.get_size()
        elif ci.get_file_type() == Gio.FileType.DIRECTORY:
            try:
                en = cur.enumerate_children(SCAN_ATTRS, NOFOLLOW, job.cancellable)
                for child in en:
                    stack.append((cur.get_child(child.get_name()), child))
                en.close(None)
            except GLib.Error as e:
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                dbg("세지 못함", cur.get_uri(), e.message)
    return items, nbytes


def _list_children(job, f, name):
    """폴더의 항목들 — 읽지 못하면 묻고, 건너뛰면 None"""
    while True:
        try:
            en = f.enumerate_children(SCAN_ATTRS, NOFOLLOW, job.cancellable)
            kids = list(en)
            en.close(None)
            return kids
        except GLib.Error as e:
            if job.cancellable.is_cancelled():
                raise Cancelled()
            if job.error(e, name, "read") == "skip":
                return None


def _probe(job, f, name, attrs=SCAN_ATTRS):
    """있으면 정보, 없으면 None — 볼 수조차 없으면 묻는다 (건너뛰면 False)"""
    while True:
        try:
            return _qinfo(f, attrs, job.cancellable)
        except GLib.Error as e:
            if job.cancellable.is_cancelled():
                raise Cancelled()
            if job.error(e, name, "read") == "skip":
                return False


def _remove_quietly(f):
    try:
        f.delete(None)
    except GLib.Error:
        pass


FAT_LIMIT = 4 * 1024 ** 3 - 1


class _Mover:
    """복사(move=False)·이동(move=True) — 폴더는 안으로 들어가며, 겹치면 묻는다"""

    def __init__(self, job, move, dest_fs_type=""):
        self.job = job
        self.move = move
        self.fat = dest_fs_type in ("msdos", "vfat", "fat", "fat32")

    def transfer(self, src, info, dst_dir, name, top=False, try_rename=None, defer=False):
        """src → dst_dir/name. (새 자리 또는 None, 원본까지 모두 옮겼나)"""
        job = self.job
        job.checkpoint()
        is_dir = _is_dir(info)
        dname = _info_name(src, info)
        job.cur_name = dname
        if try_rename is None:
            try_rename = self.move
        dst = dst_dir.get_child(name)
        if self.move and dst.equal(src):
            job.item_done(1, 0)
            return dst, False
        overwrite = merge = False
        dinfo = _probe(job, dst, dname, META_ATTRS)
        if dinfo is False:
            self._skip(src, info, top)
            return None, False
        if dinfo is not None:
            s_meta = _meta(src, _qinfo(src, META_ATTRS, job.cancellable) or info)
            d_meta = _meta(dst, dinfo)
            keep = _free_name(dst_dir, name, is_dir)
            choice = job.conflict(dname, s_meta, d_meta, keep, nested=not top)
            if choice == "skip":
                self._skip(src, info, top)
                return None, False
            if choice == "keep":
                name = keep
                dst = dst_dir.get_child(name)
            elif choice == "merge" and is_dir and d_meta["dir"]:
                merge = True
            elif choice == "replace" and not is_dir and not d_meta["dir"]:
                overwrite = True
            else:                                        # 종류가 다른데 바꾸기·병합 — 건너뛴다
                self._skip(src, info, top)
                return None, False
        if try_rename and not merge:
            r = self._rename(src, dst, overwrite, dname)
            if r == "done":
                job.item_done(*(self._top_counts if top and hasattr(self, "_top_counts") else (1, 0)))
                return dst, True
            if r == "skip":
                self._skip(src, info, top)
                return None, False
            # 다른 파일 시스템 — 복사한 뒤 지운다. 합계를 폴더 안까지로 고친다
            if top and getattr(self, "_top_shallow", False):
                items, nbytes = _count(job, src, info)
                job.total_items += items - 1
                job.total_bytes += nbytes
                self._top_shallow = False
            try_rename = False
        if is_dir:
            made, complete = self._dir(src, info, dst, dname, merge, defer, try_rename)
            return (dst if made else None), complete
        if not self._file(src, info, dst, dname, overwrite):
            return None, False
        if self.move and not defer:
            return dst, self._delete_src(src, dname)     # 복사는 됐다 — 원본을 못 지워도 새 자리는 있다
        return dst, True

    def _skip(self, src, info, top):
        job = self.job
        if top and hasattr(self, "_top_counts"):
            job.item_done(*self._top_counts)
        elif _is_dir(info) and not (self.move and getattr(self, "_top_shallow", False)):
            job.item_done(*_count(job, src, info))
        else:
            job.item_done(1, info.get_size() if info.get_file_type() == Gio.FileType.REGULAR else 0)

    def _rename(self, src, dst, overwrite, name):
        job = self.job
        flags = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
        if overwrite:
            flags |= Gio.FileCopyFlags.OVERWRITE
        while True:
            try:
                src.move(dst, flags, job.cancellable, None, None)
                return "done"
            except GLib.Error as e:
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                if _is(e, _IOE.NOT_SUPPORTED, _IOE.WOULD_RECURSE, _IOE.WOULD_MERGE):
                    return "copy"
                if job.error(e, name, "move") == "skip":
                    return "skip"

    def _file(self, src, info, dst, name, overwrite):
        job = self.job
        size = info.get_size() if info.get_file_type() == Gio.FileType.REGULAR else 0
        if self.fat and size > FAT_LIMIT:
            job.error(None, name, "copy", can_retry=False,
                      reason="FAT32 드라이브에는 4GB보다 큰 파일을 넣을 수 없습니다. "
                             "드라이브를 exFAT·NTFS 로 포맷하면 넣을 수 있습니다.")
            job.item_done(1, size)
            return False
        flags = COPY_FLAGS | (Gio.FileCopyFlags.OVERWRITE if overwrite else Gio.FileCopyFlags.NONE)

        def progress(cur, _total, *_):
            job.cur_bytes = cur
            job.hold_if_paused()
        while True:
            try:
                src.copy(dst, flags, job.cancellable, progress, None)
                job.cur_bytes = 0
                job.item_done(1, size)
                return True
            except GLib.Error as e:
                job.cur_bytes = 0
                if not overwrite and not _is(e, _IOE.EXISTS):
                    _remove_quietly(dst)                     # 쓰다 만 파일
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                if job.error(e, name, "move" if self.move else "copy") == "skip":
                    job.item_done(1, size)
                    return False

    def _dir(self, src, info, dst, name, merge, defer, try_rename):
        """(대상 폴더가 생겼나, 원본 안의 것을 모두 옮겼나)"""
        job = self.job
        created = False
        if not merge:
            while True:
                try:
                    dst.make_directory(job.cancellable)
                    created = True
                    break
                except GLib.Error as e:
                    if job.cancellable.is_cancelled():
                        raise Cancelled()
                    if _is(e, _IOE.EXISTS):              # 그사이에 생겼다 — 합친다
                        break
                    if job.error(e, display_text(dst.get_basename()), "mkdir") == "skip":
                        self._skip(src, info, False)
                        return False, False
        kids = _list_children(job, src, name)
        if kids is None:
            job.item_done(1, 0)
            return True, False
        complete = True
        for ci in kids:
            c = src.get_child(ci.get_name())
            _d, ok = self.transfer(c, ci, dst, ci.get_name(), top=False, try_rename=try_rename, defer=defer)
            complete = complete and ok
        if created:
            # 폴더의 수정한 시각·권한은 안의 것을 다 넣은 뒤에 (먼저 하면 넣으면서 시각이 다시 바뀐다)
            try:
                src.copy_attributes(dst, COPY_FLAGS, None)
            except GLib.Error:
                pass
        job.item_done(1, 0)
        if self.move and complete and not defer:
            complete = self._delete_src(src, name)
        return True, complete

    def _delete_src(self, src, name):
        job = self.job
        while True:
            try:
                src.delete(job.cancellable)
                return True
            except GLib.Error as e:
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                if _is(e, _IOE.NOT_FOUND):
                    return True
                if job.error(e, name, "delete") == "skip":
                    return False


def _inside(dest_dir, src):
    """dest_dir 가 src 자신이거나 그 안인가 (링크를 풀어서도 본다)"""
    if dest_dir.equal(src) or dest_dir.has_prefix(src):
        return True
    a, b = _real(dest_dir), _real(src)
    return bool(a and b and (a == b or a.startswith(b.rstrip("/") + "/")))


def _transfer_job(job, sources, dest_dir, move):
    verb = "이동하는 중" if move else "복사하는 중"
    try:
        dinfo = dest_dir.query_info("standard::type,id::filesystem", 0, job.cancellable)
    except GLib.Error as e:
        if job.cancellable.is_cancelled():
            raise Cancelled()
        why = explain(e)
        job.ask(lambda reply: ask_choice(job.dialog_parent(), "대상 폴더를 열 수 없습니다",
                                         "대상 폴더를 열 수 없습니다.", why, [(True, "확인", True)],
                                         reply, default=True, icon="dialog-error-symbolic"))
        return False
    if dinfo.get_file_type() != Gio.FileType.DIRECTORY:
        return False
    dest_fs = dinfo.get_attribute_string("id::filesystem") or ""
    try:
        fs_type = dest_dir.query_filesystem_info("filesystem::type", job.cancellable) \
            .get_attribute_string("filesystem::type") or ""
    except GLib.Error:
        fs_type = ""
    dest_name = _place_name(dest_dir)
    parents = {p.get_uri() if p else "" for p in (s.get_parent() for s in sources)}
    src_name = _place_name(sources[0].get_parent()) if len(parents) == 1 else "여러 위치"

    tops = []
    for src in sources:
        job.checkpoint()
        try:
            info = _qinfo(src, SCAN_ATTRS, job.cancellable)
        except GLib.Error as e:
            if job.cancellable.is_cancelled():
                raise Cancelled()
            info, err = None, e
        else:
            err = None
        name = display_text(src.get_basename() or src.get_uri())
        if info is None:
            why = explain(err) if err is not None else "항목을 찾을 수 없습니다. 이미 옮겨졌거나 삭제되었을 수 있습니다."
            job.ask(lambda reply, e=err, name=name, why=why: _error_dialog(
                job, reply, "move" if move else "copy", name, why, _detail(e), False))
            continue
        if _is_dir(info) and _inside(dest_dir, src):
            ans = job.ask(lambda reply, n=_info_name(src, info): ask_choice(
                job.dialog_parent(), "폴더를 옮길 수 없습니다", "대상 폴더가 원본 폴더의 하위 폴더입니다.",
                f"'{n}' 폴더를 그 안으로 {'이동' if move else '복사'}할 수 없습니다.",
                [("skip", "건너뛰기", True), (None, "취소", False)], reply, default="skip",
                icon="dialog-error-symbolic"))
            if ans == "skip":
                continue
        parent = src.get_parent()
        same_dir = parent is not None and parent.equal(dest_dir)
        if move and same_dir:
            job.results.append(src)                     # 같은 폴더로 옮기기 — 할 일이 없다
            continue
        src_fs = info.get_attribute_string("id::filesystem") or ""
        shallow = move and bool(src_fs) and src_fs == dest_fs
        items, nbytes = (1, 0) if shallow else _count(job, src, info)
        job.total_items += items
        job.total_bytes += nbytes
        tops.append((src, info, same_dir and not move, shallow, (items, nbytes)))
        if not (same_dir and not move):
            try:
                ex = _qinfo(dest_dir.get_child(src.get_basename()), "standard::type", job.cancellable)
            except GLib.Error:
                ex = None
            if ex is not None:
                d = ex.get_file_type() == Gio.FileType.DIRECTORY
                kind = "dir" if (d and _is_dir(info)) else "file" if not (d or _is_dir(info)) else "mixed"
                job.conflicts_left[kind] += 1
    n = job.total_items
    job.title = (f"항목 {n:,}개를 {src_name}에서 {dest_name}{josa(dest_name, '으로')} {verb}")
    job.scanning = False

    mover = _Mover(job, move, fs_type)
    for src, info, same_dir_copy, shallow, counts in tops:
        job.checkpoint()
        name = src.get_basename()
        if same_dir_copy:
            name = _free_name(dest_dir, name, _is_dir(info), copy_name, first=1)
        mover._top_counts = counts
        mover._top_shallow = shallow
        dst, _complete = mover.transfer(src, info, dest_dir, name, top=True,
                                        defer=move and src.get_uri_scheme() == "trash")
        if move and _complete and src.get_uri_scheme() == "trash":
            mover._delete_src(src, _info_name(src, info))
        if dst is not None:
            job.results.append(dst)
    return True


def _start_transfer(sources, dest_dir, parent, done, move):
    sources = [_gfile(s) for s in (sources or [])]
    dest_dir = _gfile(dest_dir)
    if not sources:
        if done:
            _idle(done, True, [])
        return None
    job = Job("move" if move else "copy", parent, None)

    def finished(ok, results):
        if move and ok:
            owned = clipboard_owned()
            if owned and owned[1]:
                moved = {s.get_uri() for s in sources}
                if all(f.get_uri() in moved for f in owned[0]):
                    clipboard_clear()                    # 잘라낸 것을 붙여넣었다 — 윈도우처럼 비운다
        if done:
            done(ok, results)
    job.done_cb = finished
    return job.start(lambda j: _transfer_job(j, sources, dest_dir, move))


def copy(sources, dest_dir, parent, done=None):
    """sources 를 dest_dir 로 복사 (같은 폴더면 "- 복사본")"""
    return _start_transfer(sources, dest_dir, parent, done, move=False)


def move(sources, dest_dir, parent, done=None):
    """sources 를 dest_dir 로 이동 (같은 파일 시스템이면 이름만 바꾼다)"""
    return _start_transfer(sources, dest_dir, parent, done, move=True)


# ── 삭제 ─────────────────────────────────────────────────────
def _delete_tree(job, f, info, counts=None):
    job.checkpoint()
    name = _info_name(f, info)
    job.cur_name = name
    # 휴지통(trash:///) 맨 위 항목은 통째로 지운다 — 안의 항목은 하나씩 지울 수 없다 (gvfs 가 안에서 한꺼번에)
    whole = f.get_uri_scheme() == "trash"
    if _is_dir(info) and not whole:
        kids = _list_children(job, f, name)
        if kids is None:
            job.item_done(1, 0)
            return False
        ok = True
        for ci in kids:
            ok = _delete_tree(job, f.get_child(ci.get_name()), ci) and ok
        if not ok:
            job.item_done(1, 0)
            return False
    while True:
        try:
            f.delete(job.cancellable)
            job.item_done(*(counts or (1, 0)))
            return True
        except GLib.Error as e:
            if job.cancellable.is_cancelled():
                raise Cancelled()
            if _is(e, _IOE.NOT_FOUND):
                job.item_done(*(counts or (1, 0)))
                return True
            if job.error(e, name, "delete") == "skip":
                job.item_done(*(counts or (1, 0)))
                return False


def _delete_items(job, items, title_from=None):
    """items = [(gfile, info)] — 합계를 세고 지운다"""
    plan = []
    for f, info in items:
        if f.get_uri_scheme() == "trash":
            counts = (1, 0)
        else:
            counts = _count(job, f, info)
        job.total_items += counts[0]
        plan.append((f, info, counts))
    where = title_from if title_from is not None else _common_place(items)
    job.title = f"항목 {job.total_items:,}개를 {where}에서 삭제하는 중"
    job.scanning = False
    for f, info, counts in plan:
        _delete_tree(job, f, info, counts if f.get_uri_scheme() == "trash" else None)
    return True


def _common_place(items):
    parents = {p.get_uri() if p else "" for p in (f.get_parent() for f, _ in items)}
    if len(parents) == 1 and items:
        return _place_name(items[0][0].get_parent())
    return "여러 위치"


def _infos(job, files):
    out = []
    for f in files:
        job.checkpoint()
        try:
            info = _qinfo(f, SCAN_ATTRS, job.cancellable)
        except GLib.Error:
            info = None
        if info is not None:
            out.append((f, info))
    return out


def _single_details(gfile, on_info):
    """삭제 확인 창에 보일 한 항목의 정보 (비동기)"""
    def got(f, res):
        try:
            info = f.query_info_finish(res)
        except GLib.Error:
            info = None
        on_info(info)
    gfile.query_info_async(META_ATTRS, NOFOLLOW, GLib.PRIORITY_DEFAULT, None, got)


def _details_box(gfile, info):
    box = Gtk.Box(spacing=12)
    icon = info.get_icon() if info is not None and info.has_attribute("standard::icon") else None
    box.pack_start(_icon(icon or "text-x-generic", 48), False, False, 0)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    nl = _label(_info_name(gfile, info), "fo-name", wrap=True, width_chars=40)
    col.pack_start(nl, False, False, 0)
    if info is not None:
        if _is_dir(info):
            col.pack_start(_label("유형: 파일 폴더", "fo-sub"), False, False, 0)
        else:
            ct = info.get_content_type() if info.has_attribute("standard::content-type") else None
            if ct:
                col.pack_start(_label("유형: " + type_description(ct, info.get_display_name()), "fo-sub"),
                               False, False, 0)
            col.pack_start(_label("크기: " + fmt_size(info.get_size()), "fo-sub"), False, False, 0)
        mt = info.get_attribute_uint64("time::modified") if info.has_attribute("time::modified") else 0
        if mt:
            col.pack_start(_label("수정한 날짜: " + fmt_date(mt), "fo-sub"), False, False, 0)
    box.pack_start(col, True, True, 0)
    return box


def _confirm_delete(files, parent, on_yes, on_no, heading_many=None):
    """"이 파일을 영구적으로 삭제하시겠습니까?" — 예/아니요 (기본 아니요)"""
    def ask(info):
        if len(files) == 1:
            is_dir = _is_dir(info)
            title = "폴더 삭제" if is_dir else "파일 삭제"
            heading = f"이 {'폴더를' if is_dir else '파일을'} 영구적으로 삭제하시겠습니까?"
            extra = _details_box(files[0], info)
        else:
            title = "여러 항목 삭제"
            heading = heading_many or f"이 항목 {len(files):,}개를 영구적으로 삭제하시겠습니까?"
            extra = None
        ask_choice(parent, title, heading, "", [(True, "예", False), (False, "아니요", True)],
                   lambda v: on_yes() if v else on_no(), default=False, extra=extra)
    if len(files) == 1:
        _single_details(files[0], ask)
    else:
        ask(None)


def delete_permanently(files, parent, done=None):
    """확인을 받은 뒤 영구 삭제 (휴지통을 거치지 않는다)"""
    files = [_gfile(f) for f in (files or [])]
    if not files:
        if done:
            _idle(done, True, [])
        return

    def go():
        Job("delete", parent, done).start(lambda j: _delete_items(j, _infos(j, files)))
    _confirm_delete(files, parent, go, lambda: done and done(False, []))


def _trash_job(job, files):
    items = _infos(job, files)
    job.total_items = len(items)
    where = _common_place(items) if items else ""
    job.title = f"항목 {len(items):,}개를 {where}에서 삭제하는 중"
    job.scanning = False
    cannot = []
    for f, info in items:
        job.checkpoint()
        name = _info_name(f, info)
        job.cur_name = name
        while True:
            try:
                f.trash(job.cancellable)
                job.item_done(1, 0)
                break
            except GLib.Error as e:
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                if _is(e, _IOE.NOT_SUPPORTED, _IOE.PERMISSION_DENIED) and _trash_unsupported(e, f):
                    cannot.append((f, info))
                    break
                if _is(e, _IOE.NOT_FOUND):
                    job.item_done(1, 0)
                    break
                if job.error(e, name, "trash") == "skip":
                    job.item_done(1, 0)
                    break
    if not cannot:
        return True
    names = [_info_name(f, i) for f, i in cannot]
    text = (f"'{names[0]}'{josa(names[0], '은')} 휴지통을 쓸 수 없는 위치에 있습니다." if len(names) == 1 else
            f"항목 {len(names):,}개가 휴지통을 쓸 수 없는 위치에 있습니다.")
    yes = job.ask(lambda reply: ask_choice(
        job.dialog_parent(), "휴지통으로 이동할 수 없습니다",
        "휴지통으로 이동할 수 없습니다. 영구적으로 삭제하시겠습니까?", text,
        [(True, "예", False), (False, "아니요", True)], lambda v: reply(bool(v)), default=False))
    if not yes:
        return len(cannot) < len(items)
    job.kind = "delete"
    job.total_items = job.done_items = 0
    job.scanning = True
    return _delete_items(job, cannot)


def _trash_unsupported(err, f):
    """휴지통 폴더를 쓸 수 없어서 난 오류인가 (파일 자체의 권한 문제가 아니라)"""
    if _is(err, _IOE.NOT_SUPPORTED):
        return True
    # 휴지통 폴더를 만들 수 없는 읽기 전용·남의 드라이브 — GLib 는 권한 거부로 알린다
    msg = (err.message or "").lower()
    return "trash" in msg or "휴지통" in msg


def trash(files, parent, done=None):
    """휴지통으로 (확인 없이 — 윈도우 11 처럼)"""
    files = [_gfile(f) for f in (files or [])]
    if not files:
        if done:
            _idle(done, True, [])
        return None
    return Job("trash", parent, lambda ok, _r: done and done(ok, [])).start(lambda j: _trash_job(j, files))


# ── 휴지통 ───────────────────────────────────────────────────
def _home_trash():
    return os.path.join(GLib.get_user_data_dir(), "Trash")


def _local_trash_origin(path):
    """휴지통 폴더(…/Trash/files/이름 · …/.Trash-UID/files/이름)의 실제 파일 → (원래 경로, .trashinfo 경로)"""
    files_dir = os.path.dirname(path)
    tdir = os.path.dirname(files_dir)
    if os.path.basename(files_dir) != "files":
        return None, None
    base = os.path.basename(tdir)
    if not (base == "Trash" or base.startswith(".Trash-") or os.path.basename(os.path.dirname(tdir)) == ".Trash"):
        return None, None
    info = os.path.join(tdir, "info", os.path.basename(path) + ".trashinfo")
    try:
        with open(info, encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                if line.startswith("Path="):
                    p = urllib.parse.unquote(line[5:].rstrip("\n"), errors="surrogateescape")
                    if not p.startswith("/"):
                        # 드라이브의 .Trash-UID — 경로는 드라이브 뿌리 기준
                        top = os.path.dirname(tdir) if base.startswith(".Trash-") else \
                            os.path.dirname(os.path.dirname(tdir))
                        p = os.path.join(top, p)
                    return p, info
    except OSError:
        pass
    return None, None


def _trash_origin(job, f):
    """(원래 자리 Gio.File, 지울 .trashinfo 경로 또는 None)"""
    if f.get_uri_scheme() == "trash":
        try:
            info = f.query_info("trash::orig-path", 0, job.cancellable)
            p = info.get_attribute_byte_string("trash::orig-path")
            if p:
                return Gio.File.new_for_path(p), None
        except GLib.Error:
            pass
        return None, None
    path = f.get_path()
    if path:
        p, info = _local_trash_origin(path)
        if p:
            return Gio.File.new_for_path(p), info
    return None, None


def _restore_job(job, files):
    job.title = f"항목 {len(files):,}개를 휴지통에서 복원하는 중"
    plan = []
    for f in files:
        job.checkpoint()
        try:
            info = _qinfo(f, SCAN_ATTRS, job.cancellable)
        except GLib.Error:
            info = None
        if info is None:
            continue
        orig, infofile = _trash_origin(job, f)
        name = _info_name(f, info)
        if orig is None or orig.get_parent() is None:
            job.ask(lambda reply, n=name: _error_dialog(job, reply, "restore", n,
                                                        "이 항목의 원래 위치를 알 수 없습니다.", "", False))
            continue
        plan.append((f, info, orig, infofile))
        job.total_items += 1
    job.scanning = False
    mover = _Mover(job, True)
    for f, info, orig, infofile in plan:
        job.checkpoint()
        name = _info_name(f, info)
        parent = orig.get_parent()
        # 없어진 상위 폴더는 다시 만든다
        while True:
            try:
                parent.make_directory_with_parents(job.cancellable)
                break
            except GLib.Error as e:
                if job.cancellable.is_cancelled():
                    raise Cancelled()
                if _is(e, _IOE.EXISTS):
                    break
                if job.error(e, display_text(parent.get_basename() or "/"), "mkdir") == "skip":
                    parent = None
                    break
        if parent is None:
            job.item_done(1, 0)
            continue
        mover._top_counts = (1, 0)
        mover._top_shallow = True
        is_trash = f.get_uri_scheme() == "trash"
        dst, complete = mover.transfer(f, info, parent, orig.get_basename(), top=True, defer=is_trash)
        if dst is None:
            continue
        if is_trash and complete and f.query_exists(None):
            mover._delete_src(f, name)                  # 복사로 옮겼다 — 휴지통의 원본을 통째로
        if infofile and complete:
            try:
                os.unlink(infofile)
            except OSError:
                pass
        job.results.append(dst)
    return True


def restore_from_trash(files, parent, done=None):
    """휴지통 항목을 원래 자리로"""
    files = [_gfile(f) for f in (files or [])]
    if not files:
        if done:
            _idle(done, True, [])
        return None
    return Job("restore", parent, done).start(lambda j: _restore_job(j, files))


def _local_trash_dirs():
    return [_home_trash()]


def _count_trash():
    """휴지통의 맨 위 항목 수 (작업 스레드)"""
    try:
        info = Gio.File.new_for_uri("trash:///").query_info("trash::item-count", 0, None)
        if info.has_attribute("trash::item-count"):
            return info.get_attribute_uint32("trash::item-count"), True
    except GLib.Error:
        pass
    n = 0
    for t in _local_trash_dirs():
        try:
            n += len(os.listdir(os.path.join(t, "files")))
        except OSError:
            pass
    return n, False


def _empty_job(job, n, gvfs):
    job.title = f"휴지통에서 항목 {n:,}개를 삭제하는 중"
    if gvfs:
        root = Gio.File.new_for_uri("trash:///")
        kids = _list_children(job, root, "휴지통") or []
        items = [(root.get_child(ci.get_name()), ci) for ci in kids]
        job.total_items = len(items)
        job.scanning = False
        for f, info in items:
            _delete_tree(job, f, info)
        return True
    # gvfs 가 없을 때 — 집 휴지통 폴더를 직접 비운다
    items = []
    for t in _local_trash_dirs():
        fd = Gio.File.new_for_path(os.path.join(t, "files"))
        for ci in _list_children(job, fd, "휴지통") or []:
            items.append((fd.get_child(ci.get_name()), ci))
    for f, info in items:
        job.total_items += _count(job, f, info)[0]
    job.scanning = False
    for f, info in items:
        _delete_tree(job, f, info)
    for t in _local_trash_dirs():
        for sub in ("info", "expunged"):
            d = os.path.join(t, sub)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for nm in names:
                if sub == "info" and os.path.exists(os.path.join(t, "files", nm[:-len(".trashinfo")])):
                    continue                               # 건너뛴 항목의 기록은 남긴다
                try:
                    os.unlink(os.path.join(d, nm))
                except OSError:
                    pass
    return True


def empty_trash(parent, done=None):
    """확인을 받은 뒤 휴지통을 비운다. 비어 있으면 묻지 않고 done(True, [])"""
    def counted(res, exc):
        n, gvfs = res if exc is None and res else (0, False)
        if n <= 0:
            if done:
                done(True, [])
            return

        def go():
            Job("empty", parent, lambda ok, _r: done and done(ok, [])).start(lambda j: _empty_job(j, n, gvfs))
        ask_choice(parent, "여러 항목 삭제" if n > 1 else "파일 삭제",
                   f"휴지통에 있는 항목 {n:,}개를 영구적으로 삭제하시겠습니까?", "",
                   [(True, "예", False), (False, "아니요", True)],
                   lambda v: go() if v else (done and done(False, [])), default=False)
    run_in_thread(_count_trash, counted)


# ── 이름 바꾸기 · 새로 만들기 ────────────────────────────────
def rename(gfile, new_name):
    """(새 Gio.File, None) 또는 (None, 오류 글). 앞뒤 공백은 뗀다 (윈도우처럼)"""
    gfile = _gfile(gfile)
    name = (new_name or "").strip()
    err = validate_name(name)
    if err:
        return None, err
    old = gfile.get_basename() or ""
    if name == old:
        return gfile, None
    path = gfile.get_path() if gfile.is_native() else None
    if path:
        return _rename_local(path, old, name)
    try:
        return gfile.set_display_name(name, None), None
    except GLib.Error as e:
        if _is(e, _IOE.EXISTS):
            return None, "같은 이름의 파일이 이미 있습니다."
        return None, explain(e)


def _rename_local(path, old, name):
    folder = os.path.dirname(path)
    new_path = os.path.join(folder, name)
    try:
        st_old = os.lstat(path)
    except OSError as e:
        return None, _explain_errno(e.errno)
    try:
        st_new = os.lstat(new_path)
    except FileNotFoundError:
        st_new = None
    except OSError as e:
        return None, _explain_errno(e.errno)
    case_only = False
    if st_new is not None:
        same = (st_new.st_dev, st_new.st_ino) == (st_old.st_dev, st_old.st_ino)
        if same and name.casefold() == old.casefold():
            case_only = True                              # 대소문자를 가리지 않는 드라이브의 같은 파일
        else:
            import stat as _st
            return None, ("같은 이름의 폴더가 이미 있습니다." if _st.S_ISDIR(st_new.st_mode) else
                          "같은 이름의 파일이 이미 있습니다.")
    try:
        if case_only:
            # 같은 파일로 풀리는 이름으로는 rename 이 아무것도 하지 않는다 — 잠깐 다른 이름을 거친다
            tmp = os.path.join(folder, f".sekai-rename-{os.getpid()}-{int(time.time() * 1000) % 100000}")
            os.rename(path, tmp)
            try:
                os.rename(tmp, new_path)
            except OSError:
                os.rename(tmp, path)
                raise
        else:
            os.rename(path, new_path)
    except OSError as e:
        if e.errno in (errno.EEXIST, errno.ENOTEMPTY):
            return None, "같은 이름의 파일이 이미 있습니다."
        return None, _explain_errno(e.errno)
    return Gio.File.new_for_path(new_path), None


def _new_item(dir_gfile, parent, base, ext, make, what):
    dir_gfile = _gfile(dir_gfile)
    for n in range(1, 100000):
        name = f"{base}{ext}" if n == 1 else f"{base} ({n}){ext}"
        f = dir_gfile.get_child(name)
        try:
            make(f)
            return f
        except GLib.Error as e:
            if _is(e, _IOE.EXISTS):
                continue
            _error_notice(parent, f"{what} 만들 수 없습니다", f"새 {what} 만들 수 없습니다.", e)
            return None
    return None


def new_folder(dir_gfile, parent=None):
    """dir 안에 "새 폴더" (겹치면 "새 폴더 (2)" …) — 만든 Gio.File 또는 None"""
    return _new_item(dir_gfile, parent, "새 폴더", "", lambda f: f.make_directory(None), "폴더를")


def new_text_file(dir_gfile, parent=None):
    """dir 안에 빈 "새 텍스트 문서.txt" — 만든 Gio.File 또는 None"""
    def make(f):
        f.create(Gio.FileCreateFlags.NONE, None).close(None)
    return _new_item(dir_gfile, parent, "새 텍스트 문서", ".txt", make, "파일을")


# ── 클립보드 ─────────────────────────────────────────────────
# GTK 의 선택 영역 API 로 한꺼번에 여러 형식을 내놓는다 (Gtk.Clipboard.set_with_data 는 파이썬에서 못 쓴다).
#   X11: 이 프로세스가 CLIPBOARD 주인. Wayland: GTK 가 wl_data_source 에 형식마다 offer 한다 — 탐색기 창이
#   키 입력을 받은 직후(Ctrl+C·메뉴)에 부르므로 컴포지터가 받아 준다. wl-copy 는 한 번에 한 형식뿐이라 쓰지 않는다
_CB_GNOME = "x-special/gnome-copied-files"
_CB_URIS = "text/uri-list"
_CB_KDE = "application/x-kde-cutselection"
_I_GNOME, _I_URIS, _I_TEXT, _I_KDE = 1, 2, 3, 4
_CB_TARGETS = ((_CB_GNOME, _I_GNOME), (_CB_URIS, _I_URIS), (_CB_KDE, _I_KDE),
               ("UTF8_STRING", _I_TEXT), ("text/plain;charset=utf-8", _I_TEXT), ("text/plain", _I_TEXT),
               ("STRING", _I_TEXT), ("TEXT", _I_TEXT), ("COMPOUND_TEXT", _I_TEXT))
_SKIP_SCHEMES = {"http", "https", "data", "resource", "about", "mailto", "javascript", "chrome", "blob"}


class _Clip:
    widget = None
    files = []
    cut = False
    owned = False
    held = None


def _clip_widget():
    if _Clip.widget is None:
        w = Gtk.Invisible()
        w.realize()
        w.connect("selection-get", _clip_get)
        w.connect("selection-clear-event", _clip_lost)
        _Clip.widget = w
    return _Clip.widget


def _clip_get(_w, sd, info, _time):
    files = _Clip.files
    uris = [f.get_uri() for f in files]
    if info == _I_GNOME:
        sd.set(sd.get_target(), 8, (("cut" if _Clip.cut else "copy") + "\n" + "\n".join(uris)).encode())
    elif info == _I_URIS:
        sd.set_uris(uris)
    elif info == _I_KDE:
        sd.set(sd.get_target(), 8, b"1" if _Clip.cut else b"0")
    else:
        sd.set_text("\n".join(f.get_path() or f.get_uri() for f in files), -1)


def _release():
    app, _Clip.held = _Clip.held, None
    if app is not None:
        app.release()


def _clip_lost(*_):
    """다른 앱이 클립보드를 가져갔다"""
    _Clip.owned = False
    _Clip.files = []
    _Clip.cut = False
    _release()
    return False


def clipboard_set(files, cut, hold=True):
    """files 를 클립보드에 (cut=True 면 잘라내기)"""
    files = [_gfile(f) for f in (files or [])]
    if not files:
        return False
    w = _clip_widget()
    sel = Gdk.SELECTION_CLIPBOARD
    if not Gtk.selection_owner_set(w, sel, Gtk.get_current_event_time()):
        dbg("클립보드 주인이 되지 못했습니다")
        return False
    Gtk.selection_clear_targets(w, sel)
    for target, info in _CB_TARGETS:
        Gtk.selection_add_target(w, sel, Gdk.Atom.intern(target, False), info)
    _Clip.files, _Clip.cut, _Clip.owned = files, bool(cut), True
    if hold and _Clip.held is None:
        app = Gio.Application.get_default()
        if app is not None:
            app.hold()
            _Clip.held = app
    return True


def clipboard_clear():
    """이 프로세스가 둔 클립보드를 비운다"""
    if _Clip.owned and _Clip.widget is not None:
        Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gtk.get_current_event_time())
    _clip_lost()


def clipboard_owned():
    """이 프로세스가 지금 클립보드에 둔 것 — (files, cut) 또는 None"""
    return (list(_Clip.files), _Clip.cut) if _Clip.owned else None


def _usable_uri(u):
    u = u.strip()
    if not u or u.startswith("#") or "://" not in u and not u.startswith("file:"):
        return None
    scheme = u.split(":", 1)[0].lower()
    if scheme in _SKIP_SCHEMES:
        return None
    return Gio.File.new_for_uri(u)


def parse_gnome_copied(data):
    """x-special/gnome-copied-files ("copy\\nfile:///…") → (files, cut)"""
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else (data or "")
    lines = [ln.strip("\r\0 ") for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or lines[0] not in ("copy", "cut"):
        return [], False
    files = [f for f in (_usable_uri(u) for u in lines[1:]) if f is not None]
    return files, lines[0] == "cut" and bool(files)


def parse_uri_list(text):
    """text/uri-list → [Gio.File] (http 같은 것은 뺀다)"""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    return [f for f in (_usable_uri(u) for u in (text or "").replace("\0", "").split("\n")) if f is not None]


def parse_text(text):
    """글자 → ([Gio.File], cut). 모든 줄이 절대 경로·file:// 여야 파일로 본다.
    예전 노틸러스는 text/plain 에 "x-special/nautilus-clipboard\\ncut\\nfile://…" 를 넣었다"""
    text = (text or "").replace("\0", "")
    if text.startswith("x-special/nautilus-clipboard"):
        return parse_gnome_copied(text.split("\n", 1)[1] if "\n" in text else "")
    lines = [ln.strip("\r") for ln in text.strip("\n").split("\n")]
    if not lines or len(lines) > 10000:
        return [], False
    files = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("/"):
            files.append(Gio.File.new_for_path(s))
        elif s.startswith("file://"):
            files.append(Gio.File.new_for_uri(s))
        elif s.startswith("~/"):
            files.append(Gio.File.new_for_path(os.path.expanduser(s)))
        else:
            return [], False
    return files, False


def clipboard_get(callback):
    """클립보드의 파일들 → callback(files, cut) (메인 스레드, 늘 비동기)"""
    def give(files, cut):
        try:
            callback(list(files), bool(cut and files))
        except Exception:
            traceback.print_exc()

    if _Clip.owned and _Clip.files:
        _idle(give, _Clip.files, _Clip.cut)
        return
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    def on_targets(_c, atoms, *_rest):
        names = set()
        for a in atoms or []:
            try:
                names.add(a.name())
            except Exception:
                pass
        if _CB_GNOME in names:
            cb.request_contents(Gdk.Atom.intern(_CB_GNOME, False), on_gnome)
        elif _CB_URIS in names:
            cut_known = _CB_KDE in names
            cb.request_contents(Gdk.Atom.intern(_CB_URIS, False),
                                lambda c, sd, *_: on_uris(c, sd, cut_known))
        elif names & {"UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "STRING", "TEXT"}:
            cb.request_text(on_text)
        else:
            give([], False)

    def on_gnome(_c, sd, *_rest):
        data = sd.get_data() if sd is not None and sd.get_length() > 0 else b""
        give(*parse_gnome_copied(bytes(data)))

    def on_uris(_c, sd, cut_known):
        data = sd.get_data() if sd is not None and sd.get_length() > 0 else b""
        files = parse_uri_list(bytes(data))
        if files and cut_known:
            cb.request_contents(Gdk.Atom.intern(_CB_KDE, False),
                                lambda _c2, sd2, *_: give(files, sd2 is not None and sd2.get_length() > 0 and
                                                          bytes(sd2.get_data()).strip() == b"1"))
        else:
            give(files, False)

    def on_text(_c, text, *_rest):
        files, cut = parse_text(text)
        if not files:
            give([], False)
            return
        # 경로 글자 — 정말 있는 것만 (없는 경로를 붙여넣기 대상으로 보이지 않게)
        run_in_thread(lambda: [f for f in files if f.query_exists(None)],
                      lambda res, exc: give(res or [] if exc is None else [], cut))
    cb.request_targets(on_targets)
