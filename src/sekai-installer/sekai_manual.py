"""SekaiOS 설치 — "직접 나누기 (고급)" (우분투의 "기타"와 같은 것)

디스크마다 파티션·빈 공간을 표로 보여 주고, 만들기·바꾸기·지우기·새 파티션 표를 고른다.
고른 것은 머릿속(Model)에만 두다가, 설치를 누르면 계획(plan, sekai-partition 형식)으로 바뀌어 백엔드가 한 번에 쓴다.
디스크에 쓰기 전까지는 아무것도 바뀌지 않는다.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from sekaishell import wizard as W  # noqa: E402

MIB = 1024 * 1024
GIB = 1024 * MIB
ROOT_MIN = 10 * GIB

# 쓰임새 — (키, 이름, 연결할 곳, 고를 수 있는 파일 시스템, GPT 종류)
USES = [
    ("root", "SekaiOS 시스템 (/)", "/", ["btrfs", "ext4"], "8304"),
    ("boot", "부팅 — 커널 (/boot)", "/boot", ["ext4"], "ea00"),
    ("esp", "EFI 시스템 파티션 (/boot/efi)", "/boot/efi", ["vfat"], "ef00"),
    ("home", "사용자 폴더 (/home)", "/home", ["btrfs", "ext4", "xfs"], "8302"),
    ("swap", "스왑", "swap", ["swap"], "8200"),
    ("data", "데이터 — 직접 위치 지정", "", ["ext4", "btrfs", "xfs", "ntfs", "exfat", "vfat"], "8300"),
    ("none", "쓰지 않음 (빈 파티션)", None, [], "8300"),
]
USE = {u[0]: u for u in USES}
FS_NAME = {"btrfs": "btrfs (시스템 복원 지점)", "ext4": "ext4", "xfs": "XFS", "vfat": "FAT32", "swap": "스왑",
           "ntfs": "NTFS (Windows 와 같이)", "exfat": "exFAT (USB·카메라와 같이)"}


def human(b):
    if b >= 1000 ** 4:
        return f"{b / 1000 ** 4:.1f} TB"
    if b >= 1000 ** 3:
        return f"{b / 1000 ** 3:.1f} GB" if b < 10 * 1000 ** 3 else f"{b / 1000 ** 3:.0f} GB"
    return f"{b // MIB} MB"


def free_regions(d, parts):
    """sekai-partition 과 같은 계산 — parts([start, sectors]) 사이의 1MiB 맞춘 빈 공간, 8MiB 이상"""
    align = max(1, MIB // d["sector"])
    used = sorted((p["start"], p["start"] + p["sectors"] - 1) for p in parts if p["sectors"])
    out, cur = [], max(d["first"], align)
    for s, e in used + [(d["last"] + 1, d["last"] + 1)]:
        a = -(-cur // align) * align
        b = ((s // align) * align) - 1
        if b - a + 1 >= 8 * MIB // d["sector"]:
            out.append({"start": a, "end": b, "bytes": (b - a + 1) * d["sector"]})
        cur = max(cur, e + 1)
    return out


class Model:
    """디스크마다: newtable · 있는 파티션의 처리(keep/format/delete + use/fs) · 새 파티션"""

    def __init__(self, layout):
        self.layout = layout
        self.reset()

    def reset(self):
        self.disks = {}
        self.seq = 0
        for d in self.layout.get("disks", []):
            if d.get("live"):
                continue
            self.disks[d["path"]] = {
                "d": d, "newtable": False,
                "parts": [dict(p, act="keep", use=None, fs_new=None)
                          for p in d["parts"]],
                "new": []}

    # ── 바꾸기 ──
    def new_table(self, path):
        m = self.disks[path]
        m["newtable"] = True
        m["new"] = []

    def delete(self, path, number):
        for p in self.disks[path]["parts"]:
            if p["number"] == number:
                p["act"], p["use"] = "delete", None

    def remove_new(self, path, nid):
        self.disks[path]["new"] = [n for n in self.disks[path]["new"] if n["id"] != nid]

    def create(self, path, region, size, use, fs, target, label):
        d = self.disks[path]["d"]
        spm = MIB // d["sector"]
        sectors = min(region["end"] - region["start"] + 1, max(spm, (size // MIB) * spm))
        self.seq += 1
        n = {"id": f"n{self.seq}", "start": region["start"], "end": region["start"] + sectors - 1,
             "bytes": sectors * d["sector"], "use": use, "fs": fs, "target": target, "label": label}
        self.disks[path]["new"].append(n)
        return n

    def set_existing(self, path, number, use, fs_new, target, label=None):
        for p in self.disks[path]["parts"]:
            if p["number"] == number:
                p["use"], p["fs_new"], p["target"] = use, fs_new, target
                p["act"] = "format" if fs_new else "keep"

    # ── 보기 ──
    def live_parts(self, path):
        m = self.disks[path]
        return [] if m["newtable"] else [p for p in m["parts"] if p["act"] != "delete"]

    def free(self, path):
        m = self.disks[path]
        parts = self.live_parts(path) + [{"start": n["start"], "sectors": n["end"] - n["start"] + 1} for n in m["new"]]
        d = dict(m["d"])
        if m["newtable"]:
            d["first"] = max(d["first"], 2048)
        return free_regions(d, parts) if (m["newtable"] or d["table"] == "gpt") else []

    def rows(self, path):
        """화면 줄 — (종류, 시작, 객체) 를 디스크 순서대로. 종류 = part · new · free"""
        out = [("part", p["start"], p) for p in (self.disks[path]["parts"] if not self.disks[path]["newtable"] else [])]
        out += [("new", n["start"], n) for n in self.disks[path]["new"]]
        out += [("free", f["start"], f) for f in self.free(path)]
        return sorted(out, key=lambda x: x[1])

    def target_of(self, use, target):
        return USE[use][2] if USE[use][2] is not None and use != "data" else target

    def auto_esp(self, explicit):
        """/boot/efi 를 고르지 않았으면 같이 쓸 ESP — / 와 같은 디스크 것이 먼저, 남은 공간 16MB 이상"""
        if any(t == "/boot/efi" for t, *_ in explicit):
            return None
        root = next((x for x in explicit if x[0] == "/"), None)
        order = sorted(self.disks, key=lambda pth: 0 if root and pth == root[1] else 1)
        for path in order:
            for p in self.live_parts(path):
                if p.get("esp") and p.get("fs") == "vfat" and not p.get("use") and (p.get("esp_free") or 0) >= 16 * MIB:
                    return ("/boot/efi", path, "part", p)
        return None

    def assignments(self):
        """[(target, path, kind, obj)] — 연결할 곳이 있는 것 전부 (고르지 않은 ESP 는 자동으로 하나)"""
        out = self._explicit()
        a = self.auto_esp(out)
        return out + [a] if a else out

    def _explicit(self):
        out = []
        for path, m in self.disks.items():
            for p in self.live_parts(path):
                if p.get("use") and p["use"] != "none":
                    out.append((self.target_of(p["use"], p.get("target")), path, "part", p))
            for n in m["new"]:
                if n["use"] != "none":
                    out.append((self.target_of(n["use"], n.get("target")), path, "new", n))
        return out

    def problems(self, encrypt):
        """설치를 막는 문제들 (빈 목록이면 됨)"""
        errs = []
        asg = self.assignments()
        tg = [t for t, *_ in asg if t != "swap"]
        for t in sorted(set(tg)):
            if tg.count(t) > 1:
                errs.append(f"{t} 가 두 번 나옵니다")
        roots = [x for x in asg if x[0] == "/"]
        if not roots:
            errs.append("SekaiOS 시스템(/) 을 둘 파티션을 정해 주세요")
        else:
            _, path, kind, o = roots[0]
            size = o["bytes"] if kind == "new" else o["size"]
            if size < ROOT_MIN:
                errs.append(f"/ 는 {ROOT_MIN // GIB}GB 이상이어야 합니다 (지금 {human(size)})")
            if kind == "part" and not o.get("fs_new"):
                errs.append("/ 로 쓸 파티션은 포맷해야 합니다")
        esps = [x for x in asg if x[0] == "/boot/efi"]
        if not esps:
            errs.append("EFI 시스템 파티션(/boot/efi) 이 필요합니다 — 있는 것을 쓰거나 새로 만드세요 (FAT32, 300MB 이상)")
        else:
            _, path, kind, o = esps[0]
            if kind == "part" and not o.get("fs_new") and (o.get("esp_free") or 0) < 16 * MIB:
                errs.append("고른 EFI 시스템 파티션의 남은 공간이 16MB 보다 작습니다")
        boots = [x for x in asg if x[0] == "/boot"]
        if boots and boots[0][2] == "part" and not boots[0][3].get("fs_new"):
            errs.append("/boot 로 쓸 파티션은 포맷해야 합니다 (다른 리눅스의 커널과 섞이지 않게)")
        if encrypt and not boots:
            errs.append("드라이브 암호화를 하려면 /boot 를 따로 두어야 합니다 (1GB, ext4)")
        if encrypt and roots and (roots[0][3].get("fs") if roots[0][2] == "new" else roots[0][3].get("fs_new")) != "btrfs":
            errs.append("드라이브 암호화는 / 가 btrfs 일 때만 됩니다")
        for path, m in self.disks.items():
            d = m["d"]
            if not m["newtable"] and d["table"] != "gpt" and (m["new"] or any(p["act"] == "delete" for p in m["parts"])):
                errs.append(f"{path} 는 MBR 파티션 표라 바꿀 수 없습니다 — 새 파티션 표(GPT)를 만들어 주세요")
        return errs

    def changes(self):
        """확인 화면에 보일 바뀌는 것들 — [(무엇, 설명)]"""
        out = []
        for path, m in self.disks.items():
            name = f"{m['d'].get('model') or path} ({path})"
            if m["newtable"]:
                out.append(("새 파티션 표 (GPT)", f"{name} — 이 디스크의 모든 파티션이 지워집니다"))
            else:
                for p in m["parts"]:
                    if p["act"] == "delete":
                        out.append(("지움", f"{p['path']}  {human(p['size'])}  {p.get('fs') or ''}  {p.get('label') or ''}"))
                    elif p["act"] == "format":
                        out.append(("포맷", f"{p['path']} → {FS_NAME.get(p['fs_new'], p['fs_new'])}"))
            for n in m["new"]:
                out.append(("만듦", f"{path} 에 {human(n['bytes'])}  {FS_NAME.get(n['fs'], n['fs'] or '빈 파티션')}"))
        for t, path, kind, o in sorted(self.assignments(), key=lambda x: x[0]):
            dev = o["path"] if kind == "part" else f"새 파티션 ({path})"
            out.append(("연결", f"{t}  ←  {dev}"))
        return out

    def plan(self, encrypt):
        disks, delete, create, mounts = [], [], [], []
        for path, m in self.disks.items():
            d = m["d"]
            touched = m["newtable"] or m["new"] or any(p["act"] != "keep" or p.get("use") for p in m["parts"])
            if not touched:
                continue
            disks.append({"path": path, "size": d["size"], "serial": d["serial"], "ptuuid": d["ptuuid"],
                          "newtable": m["newtable"]})
            if not m["newtable"]:
                delete += [{"disk": path, "number": p["number"]} for p in m["parts"] if p["act"] == "delete"]
            for n in m["new"]:
                create.append({"id": n["id"], "disk": path, "start": n["start"], "end": n["end"],
                               "type": USE[n["use"]][4] if n["use"] != "data" or n["fs"] != "ntfs" else "0700",
                               "name": {"root": "SEKAI-ROOT", "boot": "SEKAI-BOOT", "esp": "SEKAI-ESP", "home": "SEKAI-HOME",
                                        "swap": "SEKAI-SWAP"}.get(n["use"], (n.get("label") or "SEKAI-DATA")[:30])})
        for t, path, kind, o in self.assignments():
            if kind == "new":
                fs = o["fs"] if o["use"] != "swap" else "swap"
                mounts.append({"target": t, "dev": "@" + o["id"], "fs": fs, "format": True,
                               "encrypt": bool(encrypt and t == "/"), "label": o.get("label") or ""})
            else:
                fs = o.get("fs_new") or o.get("fs") or ""
                mounts.append({"target": t, "dev": o["path"], "fs": fs, "format": bool(o.get("fs_new")),
                               "encrypt": bool(encrypt and t == "/"), "label": o.get("label") or ""})
        return {"version": 1, "disks": disks, "delete": delete, "create": create, "mounts": mounts}


class UseDialog(Gtk.Dialog):
    """만들기·바꾸기 대화 상자 — 크기(만들 때만), 쓰임새, 파일 시스템, 위치, 이름, 포맷(있는 것일 때)"""

    def __init__(self, parent, title, max_bytes=None, existing=None):
        super().__init__(title=title, transient_for=parent, modal=True)
        self.add_button("취소", Gtk.ResponseType.CANCEL)
        self.ok = self.add_button("확인", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.existing = existing
        g = Gtk.Grid(column_spacing=12, row_spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(g, f"set_margin_{m}")(16)
        r = 0
        self.size = None
        if max_bytes is not None:
            g.attach(W.label("크기 (GB)", "row-title"), 0, r, 1, 1)
            mx = max_bytes / 1000 ** 3
            self.size = Gtk.SpinButton.new_with_range(0.1, max(0.1, mx), 0.1)
            self.size.set_digits(1)
            self.size.set_value(mx)
            g.attach(self.size, 1, r, 1, 1)
            g.attach(W.label(f"최대 {human(max_bytes)}", "row-sub"), 2, r, 1, 1)
            r += 1
        g.attach(W.label("쓰임새", "row-title"), 0, r, 1, 1)
        self.use = Gtk.ComboBoxText()
        for k, name, *_ in USES:
            if existing is not None and k == "none":
                self.use.append(k, "쓰지 않음 (그대로 둠)")
            else:
                self.use.append(k, name)
        g.attach(self.use, 1, r, 2, 1)
        r += 1
        self.fmt = Gtk.CheckButton(label="포맷 (안의 파일이 모두 지워짐)")
        if existing is not None:
            g.attach(self.fmt, 1, r, 2, 1)
            r += 1
        g.attach(W.label("파일 시스템", "row-title"), 0, r, 1, 1)
        self.fs = Gtk.ComboBoxText()
        g.attach(self.fs, 1, r, 2, 1)
        r += 1
        g.attach(W.label("위치", "row-title"), 0, r, 1, 1)
        self.target = Gtk.Entry(placeholder_text="/data")
        g.attach(self.target, 1, r, 2, 1)
        r += 1
        g.attach(W.label("이름 (선택)", "row-title"), 0, r, 1, 1)
        self.label = Gtk.Entry(max_length=16)
        g.attach(self.label, 1, r, 2, 1)
        r += 1
        self.err = W.label("", "row-sub warn", wrap=True)
        g.attach(self.err, 0, r, 3, 1)
        self.get_content_area().add(g)
        self.use.connect("changed", lambda *_: self._sync())
        self.fmt.connect("toggled", lambda *_: self._sync())
        self.fs.connect("changed", lambda *_: self._check())
        self.target.connect("changed", lambda *_: self._check())
        self.show_all()

    def set_values(self, use, fs=None, fmt=False, target="", label=""):
        self.use.set_active_id(use)
        self.fmt.set_active(fmt)
        self._sync()
        if fs:
            self.fs.set_active_id(fs)
        self.target.set_text(target or "")
        self.label.set_text(label or "")

    def _sync(self):
        use = self.use.get_active_id() or "none"
        ex = self.existing
        need_fmt = use in ("root", "boot") or (ex is not None and use == "swap" and ex.get("fs") != "swap")
        if ex is not None:
            if need_fmt:
                self.fmt.set_active(True)
            self.fmt.set_sensitive(use != "none" and not need_fmt)
            if use == "none":
                self.fmt.set_active(False)
        cur = self.fs.get_active_id()
        self.fs.remove_all()
        for f in USE[use][3]:
            self.fs.append(f, FS_NAME.get(f, f))
        if cur in USE[use][3]:
            self.fs.set_active_id(cur)
        elif USE[use][3]:
            self.fs.set_active(0)
        formatting = ex is None or self.fmt.get_active()
        self.fs.set_sensitive(formatting and len(USE[use][3]) > 1)
        self.target.set_sensitive(use == "data")
        self._check()

    def _check(self):
        use = self.use.get_active_id() or "none"
        err = ""
        if use == "data":
            t = self.target.get_text().strip()
            if not t.startswith("/") or ".." in t or t in ("/", "/boot", "/boot/efi", "/home") or " " in t:
                err = "위치는 /로 시작하는 폴더 (예: /data, /var, /srv)"
        ex = self.existing
        if ex is not None and use == "esp" and not self.fmt.get_active() and (ex.get("fs") or "") != "vfat":
            err = "EFI 시스템 파티션은 FAT32 여야 합니다 — 포맷하거나 다른 파티션을 고르세요"
        self.err.set_text(err)
        self.ok.set_sensitive(not err)

    def values(self):
        use = self.use.get_active_id() or "none"
        size = int(self.size.get_value() * 1000 ** 3) if self.size else None
        fmt = self.existing is None or self.fmt.get_active()
        fs = self.fs.get_active_id() if fmt and use != "none" else None
        return {"size": size, "use": use, "fs": fs, "target": self.target.get_text().strip(),
                "label": self.label.get_text().strip()}


class ManualPage(Gtk.Box):
    """편집기 화면 — 위: 디스크별 표, 아래: 단추·문제 목록. on_change() 로 다음 단추를 고친다"""

    def __init__(self, window, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.win = window
        self.on_change = on_change
        self.model = None
        self.sel = None                     # (path, kind, obj)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.get_style_context().add_class("part-table")
        self.list.connect("row-selected", self._selected)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_min_content_height(250)
        sc.add(self.list)
        self.pack_start(sc, True, True, 0)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.b_new = Gtk.Button(label="＋ 만들기")
        self.b_edit = Gtk.Button(label="바꾸기")
        self.b_del = Gtk.Button(label="－ 지우기")
        self.b_table = Gtk.Button(label="새 파티션 표")
        self.b_reset = Gtk.Button(label="처음대로")
        for b in (self.b_new, self.b_edit, self.b_del, self.b_table):
            bar.pack_start(b, False, False, 0)
        bar.pack_end(self.b_reset, False, False, 0)
        self.b_new.connect("clicked", lambda *_: self._create())
        self.b_edit.connect("clicked", lambda *_: self._edit())
        self.b_del.connect("clicked", lambda *_: self._delete())
        self.b_table.connect("clicked", lambda *_: self._new_table())
        self.b_reset.connect("clicked", lambda *_: (self.model.reset(), self.refresh()))
        self.pack_start(bar, False, False, 0)
        self.probs = W.label("", "row-sub warn", wrap=True)
        self.pack_start(self.probs, False, False, 0)
        self.enc_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(self.enc_slot, False, False, 6)

    def load(self, layout):
        if self.model is None or self.model.layout is not layout:
            self.model = Model(layout)
        self.refresh()

    # ── 표 ──
    def _row(self, path, kind, o):
        m = self.model
        d = m.disks[path]["d"]
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h.set_margin_start(18)

        def col(text, w, cls="row-sub"):
            lb = W.label(text, cls)
            lb.set_size_request(w, -1)
            lb.set_ellipsize(3)
            lb.set_width_chars(max(4, w // 8))          # 글이 길어도 칸이 밀리지 않게 (열 맞춤)
            lb.set_max_width_chars(max(4, w // 8))
            h.pack_start(lb, False, False, 0)
        if kind == "free":
            col("빈 공간", 150, "row-title")
            col("", 150)
            col("", 170)
            col(human(o["bytes"]), 90)
            col("", 110)
        elif kind == "new":
            col("새 파티션", 150, "row-title")
            col(FS_NAME.get(o["fs"], o["fs"] or "—"), 150)
            col(m.target_of(o["use"], o.get("target")) or "—", 170)
            col(human(o["bytes"]), 90)
            col("새로 만듦", 110, "row-sub ok")
        else:
            col(o["path"].replace("/dev/", ""), 150, "row-title")
            fs = o.get("fs_new") or o.get("fs") or "—"
            col(FS_NAME.get(fs, fs) + (f"  · {o.get('label')}" if o.get("label") else ""), 150)
            tgt = m.target_of(o["use"], o.get("target")) if o.get("use") and o["use"] != "none" else ""
            auto = m.auto_esp(m._explicit())
            if not tgt and auto and auto[3] is o:
                tgt = "/boot/efi (자동 · 같이 씀)"
            col(tgt or ("(Windows 복구)" if o.get("winre") else "(Windows)" if o.get("windows") else ""), 170)
            col(human(o["size"]), 90)
            col({"keep": "그대로" if not tgt else "그대로 · 연결", "format": "포맷",
                 "delete": "지움"}[o["act"]], 110, "row-sub warn" if o["act"] in ("delete", "format") else "row-sub")
        r = Gtk.ListBoxRow()
        r.add(h)
        r.sel = (path, kind, o)
        return r

    def refresh(self):
        for c in self.list.get_children():
            self.list.remove(c)
        for path, m in self.model.disks.items():
            d = m["d"]
            hr = Gtk.ListBoxRow()
            hr.set_selectable(True)
            hr.get_style_context().add_class("disk-head")
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            hb.pack_start(W.label(f"{d.get('model') or path}", "choice-title"), False, False, 0)
            hb.pack_start(W.label(f"{path} · {human(d['size'])} · "
                                  + ("새 GPT" if m["newtable"] else (d["table"] or "파티션 표 없음").upper()),
                                  "choice-sub"), False, False, 0)
            hr.add(hb)
            hr.sel = (path, "disk", d)
            self.list.add(hr)
            for kind, _, o in self.model.rows(path):
                self.list.add(self._row(path, kind, o))
        self.list.show_all()
        self.sel = None
        self._buttons()
        self._problems()

    def _selected(self, _lb, row):
        self.sel = getattr(row, "sel", None) if row else None
        self._buttons()

    def _buttons(self):
        k = self.sel[1] if self.sel else None
        o = self.sel[2] if self.sel else None
        self.b_new.set_sensitive(k == "free")
        self.b_edit.set_sensitive(k in ("part", "new") and (k == "new" or o["act"] != "delete"))
        self.b_del.set_sensitive(k == "new" or (k == "part" and o["act"] != "delete"))
        self.b_table.set_sensitive(k == "disk")

    def _problems(self):
        enc = getattr(self.win, "encrypt", False)
        errs = self.model.problems(enc)
        self.probs.set_text("\n".join("• " + e for e in errs))
        self.probs.set_visible(bool(errs))
        self.on_change(not errs)

    # ── 단추 ──
    def _create(self):
        path, _, region = self.sel
        dlg = UseDialog(self.win, "새 파티션", max_bytes=region["bytes"])
        asg = {t for t, *_ in self.model.assignments()}
        dlg.set_values("root" if "/" not in asg else "home" if "/home" not in asg else "data",
                       target="/data")
        if dlg.run() == Gtk.ResponseType.OK:
            v = dlg.values()
            target = v["target"] if v["use"] == "data" else None
            self.model.create(path, region, v["size"], v["use"], v["fs"], target, v["label"])
        dlg.destroy()
        self.refresh()

    def _edit(self):
        path, kind, o = self.sel
        if kind == "new":
            dlg = UseDialog(self.win, "새 파티션 바꾸기")
            dlg.set_values(o["use"], o["fs"], target=o.get("target") or "", label=o.get("label") or "")
            if dlg.run() == Gtk.ResponseType.OK:
                v = dlg.values()
                o.update(use=v["use"], fs=v["fs"], target=v["target"] if v["use"] == "data" else None, label=v["label"])
        else:
            dlg = UseDialog(self.win, f"{o['path']} 바꾸기", existing=o)
            dlg.set_values(o.get("use") or "none", o.get("fs_new") or o.get("fs"), fmt=bool(o.get("fs_new")),
                           target=o.get("target") or "", label=o.get("label") or "")
            if dlg.run() == Gtk.ResponseType.OK:
                v = dlg.values()
                self.model.set_existing(path, o["number"], v["use"] if v["use"] != "none" else None, v["fs"],
                                        v["target"] if v["use"] == "data" else None)
        dlg.destroy()
        self.refresh()

    def _delete(self):
        path, kind, o = self.sel
        if kind == "new":
            self.model.remove_new(path, o["id"])
        else:
            self.model.delete(path, o["number"])
        self.refresh()

    def _new_table(self):
        path, _, d = self.sel
        dlg = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK_CANCEL,
                                text=f"{d.get('model') or path} 에 새 파티션 표를 만들까요?")
        dlg.format_secondary_text("이 디스크의 모든 파티션이 지워집니다 (실제로는 설치를 누를 때). "
                                  "Windows 나 파일이 있다면 모두 사라집니다.")
        if dlg.run() == Gtk.ResponseType.OK:
            self.model.new_table(path)
        dlg.destroy()
        self.refresh()
