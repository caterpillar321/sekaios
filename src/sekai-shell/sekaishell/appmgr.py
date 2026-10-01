"""앱 설치·제거 — 앱 설치 관리자(sekai-appinstall)와 설정 › 설치된 앱이 같이 쓴다.

실제 일은 /usr/libexec/sekai/sekai-apps 가 한다 (설치·제거는 pkexec 로, 미리 보기는 보통 권한으로).
여기서는 그 도우미를 돌리고 출력 줄(INFO/STATE/ADD/UPG/DEL/BLOCK/PROGRESS/APP/DONE/ERROR)을 읽는다.
"""
import configparser
import os
import re
import subprocess
import tarfile
import threading

from gi.repository import GLib

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = next((p for p in ("/usr/libexec/sekai/sekai-apps",
                           os.path.join(HERE, "..", "..", "sekai-de", "usr", "libexec", "sekai", "sekai-apps"))
               if os.path.exists(p)), "/usr/libexec/sekai/sekai-apps")
REPO_HELPER = "/usr/libexec/sekai/sekai-apps-repo"   # 스토어·제거 — 저장소 패키지만, 잠시 인증을 기억
CANCELLED = (126, 127)          # pkexec: 인증 창을 닫았거나 인증에 실패


def run_helper(args, on_line, on_done, root=False, stdin_path=None, repo=False):
    """도우미를 작업 스레드에서 돌린다. on_line(종류, 나머지)·on_done(종료 코드) 은 GTK 스레드에서 불린다.
    root: pkexec 로 (sekai-apps — 매번 인증), repo: 저장소 입구 sekai-apps-repo 로 (install·remove·refresh)"""
    def work():
        stdin = None
        try:
            if stdin_path is not None:
                stdin = open(stdin_path, "rb")
            if repo:
                cmd = ["pkexec", REPO_HELPER] + list(args)
            else:
                cmd = (["pkexec", HELPER] if root else [HELPER]) + list(args)
            p = subprocess.Popen(cmd, stdin=stdin or subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace")
        except OSError as e:
            GLib.idle_add(on_line, "ERROR", f"실행하지 못했습니다: {e}")
            GLib.idle_add(on_done, 1)
            return
        finally:
            if stdin is not None:
                stdin.close()                   # 자식이 물려받았다 — 우리 쪽은 닫는다
        for line in p.stdout:
            kind, _, rest = line.rstrip("\n").partition(" ")
            if kind.isupper():
                GLib.idle_add(on_line, kind, rest)
        GLib.idle_add(on_done, p.wait())
    threading.Thread(target=work, daemon=True).start()


class Plan:
    """inspect · plan-remove 의 결과"""

    def __init__(self):
        self.info = {}
        self.desc = []
        self.state, self.installed = None, None
        self.add, self.upg, self.dele = [], [], []
        self.blocks, self.error = [], None
        self.done = False

    def feed(self, kind, rest):
        if kind == "INFO":
            k, _, v = rest.partition(" ")
            self.info[k] = v
        elif kind == "DESC":
            self.desc.append(rest)
        elif kind == "STATE":
            parts = rest.split()
            self.state = parts[0] if parts else None
            self.installed = parts[1] if len(parts) > 1 else None
        elif kind == "ADD":
            self.add.append(rest.split()[0])
        elif kind == "UPG":
            self.upg.append(rest.split()[0])
        elif kind == "DEL":
            self.dele.append(rest.split()[0])
        elif kind == "BLOCK":
            self.blocks.append(rest)
        elif kind == "ERROR":
            self.error = rest
        elif kind == "DONE":
            self.done = True


def human_size(kb):
    try:
        kb = int(kb)
    except (TypeError, ValueError):
        return None
    if kb < 1024:
        return f"{kb} KB"
    if kb < 1024 * 1024:
        return f"{kb / 1024:.1f} MB".replace(".0 MB", " MB")
    return f"{kb / 1024 / 1024:.1f} GB"


def publisher(maintainer):
    """'이름 <메일>' → 이름"""
    m = re.match(r"^\s*(.*?)\s*<[^>]*>\s*$", maintainer or "")
    return (m.group(1) if m else (maintainer or "")).strip() or None


# ── .deb 안의 앱 이름·아이콘 (설치 전에 보여 주려고) ──
def _lang_keys():
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").split(".")[0]
    keys = []
    if lang and lang not in ("C", "POSIX"):
        keys.append(f"Name[{lang}]")
        if "_" in lang:
            keys.append(f"Name[{lang.split('_')[0]}]")
    return keys + ["Name"]


def _members(deb):
    """dpkg-deb 로 꾸러미 안의 파일을 차례로 (멤버, 파일 객체) — 스트림이라 한 번만 지나간다."""
    p = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", deb], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|") as tf:
            for m in tf:
                yield m, tf
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def _norm(name):
    return "/" + name.lstrip("./")


ICON_RANK = ["scalable", "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48"]


def deb_appinfo(deb, icon_dir):
    """(앱 이름, 아이콘 파일 경로 또는 아이콘 이름) — 꾸러미에 보이는 앱(.desktop)이 없으면 (None, None).
    큰 꾸러미도 두 번 훑을 뿐이다 (앱 목록 → 고른 아이콘 하나 꺼내기)."""
    entries = []
    try:
        for m, tf in _members(deb):
            path = _norm(m.name)
            if m.isfile() and re.match(r"^/usr/(local/)?share/applications/[^/]+\.desktop$", path) and m.size < 65536:
                cp = configparser.RawConfigParser(strict=False, interpolation=None)
                cp.optionxform = str
                try:
                    cp.read_string(tf.extractfile(m).read().decode("utf-8", "replace"))
                    e = dict(cp["Desktop Entry"])
                except (configparser.Error, KeyError, UnicodeError):
                    continue
                if e.get("NoDisplay", "").lower() == "true" or e.get("Type", "Application") != "Application":
                    continue
                entries.append(e)
    except (tarfile.TarError, OSError, EOFError):
        return None, None
    if not entries:
        return None, None
    e = entries[0]
    name = next((e[k] for k in _lang_keys() if e.get(k)), None)
    icon = e.get("Icon") or ""
    if not icon:
        return name, None
    # 아이콘 — 절대 경로면 그 파일, 이름이면 꾸러미 안의 hicolor·pixmaps 에서 가장 큰 것
    want, best = None, 99
    try:
        for m, tf in _members(deb):
            if not m.isfile() or m.size > 4 * 1024 * 1024:
                continue
            path = _norm(m.name)
            if os.path.isabs(icon):
                if path == icon:
                    want = (m.name, tf.extractfile(m).read())
                    break
                continue
            base = os.path.basename(path)
            stem, ext = os.path.splitext(base)
            if stem != icon or ext not in (".png", ".svg"):
                continue
            rank = 50
            mm = re.match(r"^/usr/share/icons/[^/]+/([^/]+)/apps/", path)
            if mm:
                rank = ICON_RANK.index(mm.group(1)) if mm.group(1) in ICON_RANK else 40
            elif path.startswith("/usr/share/pixmaps/"):
                rank = 45
            if rank < best:
                best = rank
                want = (m.name, tf.extractfile(m).read())
    except (tarfile.TarError, OSError, EOFError):
        pass
    if want is None:
        return name, (icon if not os.path.isabs(icon) else None)      # 설치된 테마에 같은 이름이 있으면 그걸로
    ext = os.path.splitext(want[0])[1] or ".png"
    os.makedirs(icon_dir, exist_ok=True)
    out = os.path.join(icon_dir, "icon" + ext)
    with open(out, "wb") as f:
        f.write(want[1])
    return name, out


# ── 설치된 앱 → 패키지 ──
def packages_of(paths):
    """{.desktop 경로: 패키지 이름} — 패키지가 아닌 것(사용자가 만든 것·flatpak 등)은 빠진다."""
    paths = [p for p in paths if p]
    if not paths:
        return {}
    res = subprocess.run(["dpkg-query", "-S"] + paths, capture_output=True, text=True,
                         env=dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8", LANGUAGE=""))
    found = {}
    for line in res.stdout.splitlines():
        if line.startswith(("diversion by", "local diversion")):
            continue
        pkgs, sep, path = line.partition(": ")
        if sep and path in paths:
            found[path] = pkgs.split(",")[0].strip().split(":")[0]
    return found


# ── Flathub 앱 (flatpak, 시스템 설치) ──
#   설치·제거는 libflatpak 으로 — 시스템 설치는 flatpak 의 system-helper 가 polkit 으로 권한을 받는다
#   (데비안 기본 규칙: 관리자(sudo) 계정은 암호 없이, 아니면 사용자 계정 컨트롤). 진행은 run_helper 와 같은 줄 형식.
def flatpak_available():
    try:
        gi_require("Flatpak", "1.0")
        from gi.repository import Flatpak  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


def gi_require(ns, ver):
    import gi
    gi.require_version(ns, ver)


def _flatpak():
    gi_require("Flatpak", "1.0")
    from gi.repository import Flatpak
    return Flatpak


def flatpak_installation():
    return _flatpak().Installation.new_system(None)


def flatpak_installed_apps():
    """{앱 id: 브랜치} — 시스템에 설치된 flatpak 앱"""
    try:
        Flatpak = _flatpak()
        inst = flatpak_installation()
        return {r.get_name(): r.get_branch() for r in inst.list_installed_refs_by_kind(Flatpak.RefKind.APP, None)}
    except Exception:
        return {}


def flatpak_remote(inst=None):
    """앱을 받을 원격 — flathub, 없으면 처음 것"""
    inst = inst or flatpak_installation()
    names = [r.get_name() for r in inst.list_remotes(None) if not r.get_disabled()]
    return "flathub" if "flathub" in names else (names[0] if names else None)


def _flatpak_error(e):
    msg = getattr(e, "message", str(e))
    low = msg.lower()
    if "not authorized" in low or "authentication" in low or "dismissed" in low:
        return "인증이 취소되었습니다"
    if "no space" in low:
        return "디스크 공간이 부족합니다"
    if "could not resolve" in low or "unable to connect" in low or "timeout" in low or "network" in low:
        return "Flathub 에 연결하지 못했습니다 — 인터넷 연결을 확인해 주세요"
    if "already installed" in low:
        return "이미 설치되어 있습니다"
    if "not installed" in low:
        return "설치되어 있지 않습니다"
    return msg


def flatpak_plan(ref):
    """설치 전 크기 — {"dl": 바이트, "inst": 바이트, "runtime": 이름 또는 None, "rt_dl": 바이트, "rt_inst": 바이트,
    "version": 문자열 또는 None} 또는 {"error": ...}. 인터넷으로 묻는다 — 작업 스레드에서."""
    try:
        Flatpak = _flatpak()
        from gi.repository import GLib as _G
        inst = flatpak_installation()
        remote = flatpak_remote(inst)
        _kind, name, arch, branch = ref.split("/")
        rr = inst.fetch_remote_ref_sync(remote, Flatpak.RefKind.APP, name, arch, branch, None)
        out = {"dl": rr.get_download_size(), "inst": rr.get_installed_size(), "runtime": None,
               "rt_dl": 0, "rt_inst": 0, "version": None}
        md = (rr.get_metadata().get_data() or b"").decode("utf-8", "replace") if rr.get_metadata() else ""
        rt = next((l.split("=", 1)[1] for l in md.splitlines() if l.startswith("runtime=")), None)
        if rt and rt.count("/") == 2:
            n, a, b = rt.split("/")
            try:
                inst.get_installed_ref(Flatpak.RefKind.RUNTIME, n, a, b, None)
            except _G.Error:
                r2 = inst.fetch_remote_ref_sync(remote, Flatpak.RefKind.RUNTIME, n, a, b, None)
                out.update(runtime=f"{n} {b}", rt_dl=r2.get_download_size(), rt_inst=r2.get_installed_size())
        return out
    except Exception as e:
        return {"error": _flatpak_error(e)}


def run_flatpak(op, ref, on_line, on_done):
    """op: install · remove. 줄(PROGRESS·ERROR·DONE)과 끝(코드)은 GTK 스레드에서. 제거한 뒤엔 아무도 안 쓰는
    런타임도 정리한다 (flatpak uninstall --unused 처럼)."""
    def work():
        try:
            Flatpak = _flatpak()
            inst = flatpak_installation()
            t = Flatpak.Transaction.new_for_installation(inst, None)
            t.set_no_interaction(True)
            if op == "install":
                t.add_install(flatpak_remote(inst), ref, None)
            else:
                t.add_uninstall(ref)
            st = {"n": 0, "total": 1, "err": None}

            def ready(tr):
                st["total"] = max(1, len(tr.get_operations()))
                return True

            def new_op(tr, o, progress):
                st["n"] += 1
                parts = (o.get_ref() or "").split("/")
                what = parts[1] if len(parts) > 1 else o.get_ref()
                verb = "제거하는 중" if op == "remove" else "내려받는 중"
                progress.set_update_frequency(400)

                def changed(p):
                    pct = int(((st["n"] - 1) + p.get_progress() / 100) / st["total"] * 100)
                    GLib.idle_add(on_line, "PROGRESS", f"{min(99, pct)} {verb} {what}")
                progress.connect("changed", changed)
                GLib.idle_add(on_line, "PROGRESS",
                              f"{int((st['n'] - 1) / st['total'] * 100)} {verb} {what}")

            def op_error(tr, o, err, details):
                st["err"] = _flatpak_error(err)
                return False                    # 멈춘다

            t.connect("ready", ready)
            t.connect("new-operation", new_op)
            t.connect("operation-error", op_error)
            t.run(None)
            if op == "remove":
                try:
                    unused = inst.list_unused_refs(None, None)
                    if unused:
                        GLib.idle_add(on_line, "PROGRESS", "99 쓰지 않는 런타임을 정리하는 중")
                        t2 = Flatpak.Transaction.new_for_installation(inst, None)
                        t2.set_no_interaction(True)
                        for r in unused:
                            t2.add_uninstall(r.format_ref())
                        t2.run(None)
                except Exception:
                    pass                        # 정리는 덤 — 실패해도 앱은 지워졌다
            GLib.idle_add(on_line, "PROGRESS", "100 완료")
            GLib.idle_add(on_line, "DONE", "")
            GLib.idle_add(on_done, 0)
        except Exception as e:
            msg = st["err"] if "st" in locals() and st.get("err") else _flatpak_error(e)
            GLib.idle_add(on_line, "ERROR", msg)
            GLib.idle_add(on_done, CANCELLED[0] if msg == "인증이 취소되었습니다" else 1)
    threading.Thread(target=work, daemon=True).start()
