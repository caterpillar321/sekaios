"""SekaiOS 파일 탐색기 — 드라이브를 연결(마운트)하지 못했을 때 이유를 알아내고 바로 할 수 있는 일을 준다.

커널이 마운트를 거절하면 udisks 는 거의 늘 같은 문장("wrong fs type, bad option, bad superblock … or other error")을
돌려준다 — 진짜 이유는 커널 기록에만 있다. 2026-11-26: 롱혼을 깔았던 MBR SSD 를 꽂자 그 영어 문장만 떴는데, 이유는
NTFS 볼륨의 "깨끗하게 닫히지 않음(dirty)" 표시 하나였다(ntfs3 는 이 표시가 있으면 쓰기로 연결하지 않는다).

이유를 가리는 순서 (관리자 권한 없이):
  1. udisks 의 블록 정보 — 파일 시스템이 아님(비었거나 알 수 없음), SekaiOS 가 읽지 못하는 형식
  2. NTFS 면 커널 기록 — "volume is dirty" · 최대 절전. 관리자(systemd-journal 그룹)만 읽을 수 있어,
     못 읽으면 "제대로 닫히지 않았거나 오류" 로 묶어 말한다
할 수 있는 일:
  - 읽기 전용으로 열기 — udisks Filesystem.Mount(options=ro). ntfs3 는 dirty 볼륨도 읽기 전용이면 연결한다
  - 고치고 열기 — udisks Filesystem.Repair (NTFS 는 ntfsfix: dirty 표시를 지우고 기록 파일을 비운다 — chkdsk 만큼
    꼼꼼하지는 않아 안내에 적는다). 관리자 인증은 udisks 가 묻는다
"""
import os
import subprocess
import threading

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

UD = "org.freedesktop.UDisks2"
UD_MGR = "/org/freedesktop/UDisks2/Manager"
FS_NAMES = {"ntfs": "NTFS", "exfat": "exFAT", "vfat": "FAT32", "ext4": "ext4", "btrfs": "btrfs", "xfs": "XFS",
            "apfs": "APFS(맥)", "hfsplus": "HFS+(맥)", "zfs_member": "ZFS", "ReFS": "ReFS", "refs": "ReFS",
            "linux_raid_member": "RAID 구성원", "LVM2_member": "LVM 구성원", "swap": "스왑"}


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _call(obj, iface, method, args, rtype, timeout=-1):
    return _bus().call_sync(UD, obj, iface, method, args, GLib.VariantType(rtype) if rtype else None,
                            Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, None)


def _block_obj(dev):
    """/dev/sdb1 → udisks 객체 경로 (없으면 None)"""
    try:
        r = _call(UD_MGR, f"{UD}.Manager", "ResolveDevice",
                  GLib.Variant("(a{sv}a{sv})", ({"path": GLib.Variant("s", dev)}, {})), "(ao)")
        objs = r.unpack()[0]
        return objs[0] if objs else None
    except GLib.Error:
        return None


def _prop(obj, iface, name):
    try:
        r = _call(obj, "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", (iface, name)), "(v)")
        return r.unpack()[0]
    except GLib.Error:
        return None


def _supported():
    try:
        r = _call(UD_MGR, "org.freedesktop.DBus.Properties", "Get",
                  GLib.Variant("(ss)", (f"{UD}.Manager", "SupportedFilesystems")), "(v)")
        return set(r.unpack()[0])
    except GLib.Error:
        return None


def _kernel_reason(dev):
    """NTFS 를 커널이 거절한 이유 — 이번 부팅의 커널 기록에서 이 장치의 마지막 ntfs3 줄. 못 읽으면 None"""
    name = os.path.basename(os.path.realpath(dev))
    try:
        r = subprocess.run(["journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat", "-n", "400",
                            "-g", f"ntfs3\\({name}\\)"], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1) or "No journal files" in r.stderr:
        return None
    if r.returncode == 1 and not r.stdout.strip():
        # 줄이 없다 — 읽을 권한이 없는 것과 구별이 안 된다 (권한이 없으면 journalctl 이 경고를 낸다)
        return None if "insufficient permissions" in r.stderr.lower() or "not seeing" in r.stderr.lower() else "none"
    for line in reversed(r.stdout.splitlines()):
        low = line.lower()
        if "dirty" in low:
            return "dirty"
        if "hibernat" in low:
            return "hibernated"
        if "logfile" in low or "journal" in low:
            return "unclean"
    return "none"


def diagnose(dev):
    """{reason, fs, fsname} — reason: nofs · unsupported · dirty · hibernated · unclean · ntfs-unknown · other"""
    obj = _block_obj(dev)
    usage = _prop(obj, f"{UD}.Block", "IdUsage") if obj else None
    fs = (_prop(obj, f"{UD}.Block", "IdType") if obj else None) or ""
    res = {"reason": "other", "fs": fs, "fsname": FS_NAMES.get(fs, fs or "알 수 없음")}
    if obj is None:
        return res
    if usage in ("", None) or (usage == "other" and fs in ("", "swap")):
        res["reason"] = "nofs"
        return res
    if usage == "raid" or fs in ("linux_raid_member", "LVM2_member", "zfs_member"):
        res["reason"] = "unsupported"
        return res
    sup = _supported()
    if usage == "filesystem" and sup is not None and fs and fs not in sup:
        res["reason"] = "unsupported"
        return res
    if fs == "ntfs":
        k = _kernel_reason(dev)
        res["reason"] = k if k in ("dirty", "hibernated", "unclean") else "ntfs-unknown"
    return res


def _async(fn, cb):
    """fn() 을 따로 돌리고 cb(결과, 오류글) 을 화면 스레드에서"""
    def work():
        try:
            out, err = fn(), None
        except GLib.Error as e:
            out, err = None, e
        GLib.idle_add(lambda: cb(out, err) and False)
    threading.Thread(target=work, daemon=True).start()


def mount_ro(dev, cb):
    """읽기 전용으로 연결 — cb(연결 위치 | None, GLib.Error | None)"""
    def fn():
        obj = _block_obj(dev)
        if obj is None:
            raise GLib.Error("장치를 찾지 못했습니다")
        r = _call(obj, f"{UD}.Filesystem", "Mount",
                  GLib.Variant("(a{sv})", ({"options": GLib.Variant("s", "ro")},)), "(s)", 60000)
        return r.unpack()[0]
    _async(fn, cb)


def repair(dev, cb):
    """고치기 (NTFS 는 ntfsfix) — cb(성공 bool | None, GLib.Error | None). 관리자 인증은 udisks 가 묻는다"""
    def fn():
        obj = _block_obj(dev)
        if obj is None:
            raise GLib.Error("장치를 찾지 못했습니다")
        r = _call(obj, f"{UD}.Filesystem", "Repair", GLib.Variant("(a{sv})", ({},)), "(b)", 600000)
        return r.unpack()[0]
    _async(fn, cb)


VOLCHECK = "/usr/libexec/sekai/sekai-volcheck"


def admin_diagnose(dev, diag, cb):
    """관리자 인증(pkexec) 뒤 sekai-volcheck 로 정확한 원인 — cb(새 diag | None, 오류글 | None).
    인증 창을 닫으면 (None, None)"""
    def work():
        try:
            r = subprocess.run(["pkexec", VOLCHECK, dev], capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            GLib.idle_add(lambda: cb(None, str(e)) and False)
            return
        if r.returncode in (126, 127):              # 인증 취소 · 거부
            GLib.idle_add(lambda: cb(None, None) and False)
            return
        d = dict(diag, logs=[])
        err = None
        for line in r.stdout.splitlines():
            kind, _, rest = line.partition(" ")
            if kind == "REASON":
                d["reason"] = {"unknown": d["reason"] if d["reason"] != "ntfs-unknown" else "other"}.get(rest, rest)
            elif kind == "LOG":
                d["logs"].append(rest)
            elif kind == "ERROR":
                err = rest
        d["checked"] = True
        GLib.idle_add(lambda: cb(None if err else d, err) and False)
    threading.Thread(target=work, daemon=True).start()


def explain(name, diag, raw):
    """(제목, 설명, 할 수 있는 일[(글, 행동)]) — 행동: ro · repair · diag"""
    title, text, acts = _explain(name, diag, raw)
    if diag.get("logs"):
        text += "\n\n시스템 기록:\n" + "\n".join("  " + l for l in diag["logs"])
    if not diag.get("checked") and diag["reason"] in ("ntfs-unknown", "other") and os.path.exists(VOLCHECK):
        acts = [("원인 자세히 확인", "diag")] + acts
    if diag["reason"] == "corrupt":
        acts = [a for a in acts if a[1] != "repair"]
    return title, text, acts


def _explain(name, diag, raw):
    r, fsname = diag["reason"], diag["fsname"]
    if r == "dirty":
        return (f"‘{name}’이(가) 제대로 닫히지 않은 채 분리되었습니다",
                "윈도우에서 쓰던 중 전원이 꺼졌거나, 안전하게 제거하지 않고 뽑으면 이렇게 표시됩니다. "
                "파일이 깨지지 않게 그대로는 열지 않습니다.\n\n"
                "읽기 전용으로 열어 파일을 꺼낼 수 있고, 표시를 지운 뒤 평소처럼 열 수도 있습니다. "
                "중요한 자료가 있다면 윈도우에서 디스크 검사(chkdsk)를 먼저 하는 것이 가장 안전합니다.",
                [("읽기 전용으로 열기", "ro"), ("고치고 열기", "repair")])
    if r in ("hibernated", "unclean"):
        return (f"‘{name}’은(는) 윈도우가 아직 쓰고 있는 상태로 꺼져 있습니다",
                "윈도우의 빠른 시작(또는 최대 절전)으로 꺼져, 윈도우가 이 드라이브의 내용을 기억하고 있습니다. "
                "지금 쓰면 다음에 윈도우를 켤 때 파일이 깨질 수 있어 읽기 전용으로만 열 수 있습니다.\n\n"
                "쓰려면 윈도우를 켜서 '다시 시작'으로 끄거나, 윈도우 설정에서 빠른 시작을 끄세요.",
                [("읽기 전용으로 열기", "ro")])
    if r == "ntfs-unknown":
        return (f"‘{name}’을(를) 열지 못했습니다",
                "이 NTFS 드라이브는 제대로 닫히지 않았거나(안전하게 제거하지 않고 뽑음, 갑자기 꺼짐) "
                "오류가 있어 그대로는 열지 않았습니다.\n\n"
                "읽기 전용으로 열어 파일을 꺼낼 수 있고, 고친 뒤 평소처럼 열 수도 있습니다. "
                "중요한 자료가 있다면 윈도우에서 디스크 검사(chkdsk)를 먼저 하는 것이 가장 안전합니다.",
                [("읽기 전용으로 열기", "ro"), ("고치고 열기", "repair")])
    if r == "corrupt":
        return (f"‘{name}’의 파일 시스템이 손상되었습니다",
                "이 NTFS 드라이브는 구조가 손상되어 그대로 열면 더 망가질 수 있습니다. 윈도우에서 디스크 검사(chkdsk /f)로 "
                "고치는 것이 가장 안전합니다. 급하면 읽기 전용으로 열어 파일을 먼저 꺼내세요.",
                [("읽기 전용으로 열기", "ro")])
    if r == "unsupported":
        return (f"‘{name}’은(는) SekaiOS 가 읽을 수 없는 형식입니다",
                f"이 드라이브는 {fsname} 형식입니다. 그 형식을 쓰는 운영체제에서 파일을 옮기거나, "
                "필요 없는 드라이브라면 포맷해서 쓸 수 있습니다.", [])
    if r == "nofs":
        return (f"‘{name}’에는 열 수 있는 내용이 없습니다",
                "이 영역은 비어 있거나(포맷되지 않음) 알 수 없는 형식입니다. "
                "새로 쓰려면 컴퓨터 관리 › 디스크 관리에서 포맷하세요.", [])
    return (f"‘{name}’에 연결하지 못했습니다",
            "드라이브에 오류가 있거나 연결이 불안정할 수 있습니다. 다시 꽂아 보거나, 다른 컴퓨터에서 검사해 보세요."
            + (f"\n\n자세한 내용: {raw}" if raw else ""),
            [("읽기 전용으로 열기", "ro")] if diag.get("fs") else [])
