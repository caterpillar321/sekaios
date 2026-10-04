"""복구 — 시스템 복원 지점 (윈도우 11 의 "시스템 › 복구"와 "시스템 보호"를 합친 자리).

복원 지점은 btrfs 로 설치한 SekaiOS 에서만 (sekai-restore 가 snapper 로 만든다). 관리자 권한이 필요한 일은
/usr/libexec/sekai/sekai-restore 가 한다 (pkexec). 목록·상태는 도우미가 남기는 /var/lib/sekai/restore.json 을
읽는다 — 볼 때마다 암호를 묻지 않게.
되돌리기는 다시 시작할 때 일어난다 (initramfs 가 시스템 하위 볼륨을 바꿔치기) — 개인 파일(/home)은 그대로.
"""
import datetime
import json
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..widgets import Page, button, combo, row, switch

HELPER = "/usr/libexec/sekai/sekai-restore"
CACHE = "/var/lib/sekai/restore.json"
ICON = ["document-revert", "edit-undo", "system-restore", "view-refresh"]


def _load():
    """(상태, 지점들) — 목록 파일이 없으면 권한 없이 상태만 묻는다"""
    try:
        with open(CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("status") or {}, d.get("points") or []
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run([HELPER, "status"], capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout or "{}"), []
    except Exception:
        return {"supported": False, "reason": "시스템 복원 도우미를 실행하지 못했습니다"}, []


def _when(s):
    """snapper 날짜 "2026-10-04 13:20:11" → "10월 4일 (토) 13:20" """
    try:
        t = datetime.datetime.fromisoformat((s or "")[:19])
    except ValueError:
        return s or ""
    wd = "월화수목금토일"[t.weekday()]
    today = datetime.date.today()
    if t.date() == today:
        return f"오늘 {t:%H:%M}"
    if t.date() == today - datetime.timedelta(days=1):
        return f"어제 {t:%H:%M}"
    return f"{t.month}월 {t.day}일 ({wd}) {t:%H:%M}"


def _size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000


LIMITS = [("0.05", "디스크의 5%"), ("0.1", "디스크의 10% (권장)"), ("0.15", "디스크의 15%"), ("0.2", "디스크의 20%")]
# 파일 복사 방식은 첫 지점이 시스템 크기만큼이라 더 넉넉하게
LIMITS_COPY = [("0.1", "디스크의 10%"), ("0.2", "디스크의 20% (권장)"), ("0.3", "디스크의 30%")]


def _log_when(s):
    """initramfs 기록의 시각 — epoch 초 (initramfs 엔 시간대가 없어 그 PC 의 시간대로 여기서 바꾼다)"""
    try:
        return _when(datetime.datetime.fromtimestamp(int(s)).isoformat(sep=" "))
    except (ValueError, OverflowError, OSError):
        return s


class RecoveryPage:
    def __init__(self, store):
        self.p = Page("복구", "시스템에 문제가 생기면 이전 상태로 되돌립니다. 개인 파일(문서·사진 등)은 그대로 둡니다.")
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.p.add_widget(self.body)
        self.msg = Gtk.Label(xalign=0)
        self.msg.get_style_context().add_class("notice")
        self.msg.set_line_wrap(True)
        self.msg.set_no_show_all(True)
        self.p.add_widget(self.msg)
        self.reload()

    @property
    def widget(self):
        return self.p

    # ── 그리기 ──
    def reload(self):
        st, pts = _load()
        for c in self.body.get_children():
            self.body.remove(c)
            c.destroy()
        self._draw(st, pts)
        self.body.show_all()

    def _sect(self, title=None):
        if title:
            l = Gtk.Label(label=title, xalign=0)
            l.get_style_context().add_class("section-title")
            self.body.pack_start(l, False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("section")
        self.body.pack_start(lb, False, False, 0)
        return lb

    def _notice(self, text):
        l = Gtk.Label(label=text, xalign=0)
        l.get_style_context().add_class("notice")
        l.set_line_wrap(True)
        self.body.pack_start(l, False, False, 0)

    def _draw(self, st, pts):
        if not st.get("supported"):
            self._notice(st.get("reason") or "이 PC 에서는 시스템 복원을 쓸 수 없습니다.")
            return

        pend = st.get("pending")
        if pend:
            s = self._sect("예약됨")
            if pend.get("action") == "undo":
                t, sub = "다시 시작하면 마지막 복원을 취소합니다", "복원하기 전 상태로 돌아갑니다"
            else:
                pt = next((p for p in pts if str(p["number"]) == str(pend.get("arg"))), None)
                t = "다시 시작하면 시스템을 되돌립니다"
                sub = (f"{_when(pt['date'])} — {pt['description']}" if pt else f"복원 지점 {pend.get('arg')}")
            box = Gtk.Box(spacing=6)
            box.pack_start(button("지금 다시 시작", lambda: subprocess.Popen(["systemctl", "reboot"])), False, False, 0)
            box.pack_start(button("예약 취소", lambda: self._run(["cancel"], "예약을 취소했습니다")), False, False, 0)
            row(s, t, sub, icon=ICON, control=box)

        last = st.get("last")
        if last and last.get("action") in ("restore", "undo", "fail", "recover"):
            s = self._sect("마지막 복원")
            when = _log_when(last.get("time", ""))
            if last["action"] == "restore":
                ctl = None
                if st.get("undo") and not pend:
                    ctl = button("복원 취소…", self._ask_undo)
                pt = next((p for p in pts if str(p["number"]) == str(last.get("arg"))), None)
                what = f"‘{pt['description'] or pt['kind_text']}’ 시점으로" if pt else f"복원 지점 {last.get('arg')}(으)로"
                row(s, f"{when}에 {what} 되돌렸습니다",
                    "되돌리기 전 상태가 남아 있어 취소할 수 있습니다" if st.get("undo") else None,
                    icon=ICON, control=ctl)
            elif last["action"] == "undo":
                row(s, f"{when}에 복원을 취소했습니다", None, icon=ICON)
            elif last["action"] == "recover":
                row(s, f"{when}에 중간에 끊긴 복원을 마무리했습니다", "되돌리는 도중 전원이 꺼졌던 것을 이어서 끝냈습니다",
                    icon=ICON)
            else:
                row(s, f"{when}에 되돌리지 못했습니다", f"이유: {last.get('arg', '')} {last.get('detail', '')}".strip(),
                    icon=["dialog-warning", "dialog-warning-symbolic"])

        s = self._sect("복원 지점")
        row(s, "복원 지점 만들기", "지금 상태를 남겨 둡니다 — 큰 설정을 바꾸기 전에", icon=["list-add", "list-add-symbolic"],
            control=button("만들기…", self._ask_create))
        if not pts:
            row(s, "복원 지점이 없습니다", "업데이트나 앱 설치 전에 자동으로 만들어집니다", icon=ICON)
        for p in pts:
            ctl = Gtk.Box(spacing=6)
            b = button("복원…", lambda p=p: self._ask_restore(p))
            b.set_sensitive(not pend)
            ctl.pack_start(b, False, False, 0)
            ctl.pack_start(button("삭제", lambda p=p: self._ask_delete(p)), False, False, 0)
            sub = f"{_when(p['date'])} · {p['kind_text']}"
            r = row(s, p["description"] or p["kind_text"], sub, icon=ICON, control=ctl)
            r.title_label.set_ellipsize(Pango.EllipsizeMode.END)

        s = self._sect("설정")
        row(s, "업데이트·설치 전에 자동으로 복원 지점 만들기",
            ("업데이트·드라이버 설치 전, 그리고 1주일에 한 번" if st.get("backend") == "copy"
             else "업데이트·앱 설치·드라이버 설치 전, 그리고 1주일에 한 번"), icon=ICON,
            control=switch(bool(st.get("auto", True)), self._set_auto))
        # 윈도우 "시스템 보호"의 최대 사용량 — 넘으면 오래된 지점부터 지운다
        used, limit, ratio = st.get("used"), st.get("limit"), st.get("limit_ratio")
        sub = (f"지금 {_size(used)} 사용 · 최대 {_size(limit)} — 넘으면 오래된 지점부터 지웁니다"
               if used is not None else "넘으면 오래된 지점부터 지웁니다")
        limits = LIMITS_COPY if st.get("backend") == "copy" else LIMITS
        cur = next((k for k, _ in limits if ratio is not None and abs(float(k) - ratio) < 1e-6), None)
        row(s, "복원 지점이 쓸 수 있는 공간", sub, icon=["drive-harddisk", "drive-harddisk-symbolic"],
            control=combo(limits, cur or (f"{ratio:g}" if ratio else limits[1][0]), self._set_limit))
        if st.get("backend") == "copy":
            self._notice("이 PC 는 ext4 로 설치되어 복원 지점을 파일 복사로 만듭니다. 첫 지점은 시스템 전체를 복사해 "
                         "몇 분 걸리고 디스크를 시스템 크기만큼 씁니다 (그다음부터는 바뀐 파일만). 그래서 자동 지점은 "
                         "업데이트·드라이버 설치 전과 1주일에 한 번만 만듭니다.")
        self._notice("복원 지점은 시스템 파일과 설치된 앱만 담습니다. 디스크 여유 공간이 모자라면 오래된 것부터 "
                     "자동으로 지웁니다. 컴퓨터가 켜지지 않으면 부팅 메뉴의 ‘시스템 복원’에서 되돌릴 수 있습니다.")

    # ── 묻기 ──
    def _parent(self):
        w = self.p.get_toplevel()
        return w if isinstance(w, Gtk.Window) else None

    def _confirm(self, text, sub, ok_label, on_ok, danger=False, cancel_label="취소"):
        d = Gtk.MessageDialog(transient_for=self._parent(), modal=True,
                              message_type=Gtk.MessageType.WARNING if danger else Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text=text)
        d.format_secondary_text(sub)
        d.add_buttons(cancel_label, Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.CANCEL)

        def responded(dlg, resp):
            dlg.destroy()
            if resp == Gtk.ResponseType.OK:
                on_ok()
        d.connect("response", responded)
        d.show_all()

    def _ask_restore(self, p):
        self._confirm(
            "이 복원 지점으로 되돌릴까요?",
            f"{_when(p['date'])} — {p['description'] or p['kind_text']}\n\n"
            "시스템 파일과 설치된 앱이 그때 상태로 돌아갑니다. 그 뒤에 설치한 앱과 업데이트는 사라집니다. "
            "개인 파일(문서·사진·다운로드 등)은 그대로입니다.\n\n"
            "컴퓨터가 다시 시작되며, 되돌린 뒤에도 ‘복원 취소’로 지금 상태로 돌아올 수 있습니다. "
            "열려 있는 파일은 먼저 저장하세요.",
            "되돌리고 다시 시작", lambda: self._run(["restore", str(p["number"])], None, reboot=True), danger=True)

    def _ask_undo(self):
        self._confirm("마지막 복원을 취소할까요?",
                      "복원하기 전 상태로 돌아갑니다. 컴퓨터가 다시 시작됩니다.",
                      "복원 취소하고 다시 시작", lambda: self._run(["undo"], None, reboot=True), danger=True,
                      cancel_label="그대로 두기")

    def _ask_delete(self, p):
        self._confirm("이 복원 지점을 지울까요?", f"{_when(p['date'])} — {p['description'] or p['kind_text']}",
                      "삭제", lambda: self._run(["delete", str(p["number"])], "복원 지점을 지웠습니다"))

    def _ask_create(self):
        d = Gtk.Dialog(title="복원 지점 만들기", transient_for=self._parent(), modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "만들기", Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        box.pack_start(Gtk.Label(label="나중에 알아볼 수 있게 이름을 붙이세요.", xalign=0), False, False, 0)
        e = Gtk.Entry()
        e.set_max_length(80)
        e.set_text(f"{datetime.datetime.now():%m월 %d일} 수동 복원 지점")
        e.set_activates_default(True)
        box.pack_start(e, False, False, 0)

        def responded(dlg, resp):
            name = e.get_text().strip()
            dlg.destroy()
            if resp == Gtk.ResponseType.OK:
                self._run(["create", name or "수동 복원 지점"], "복원 지점을 만들었습니다")
        d.connect("response", responded)
        d.show_all()

    def _set_limit(self, v):
        if v:
            self._run(["limit", v], None)

    def _set_auto(self, on):
        self._run(["auto", "on" if on else "off"], None)

    # ── 실행 ──
    def _say(self, text):
        self.msg.set_text(text or "")
        self.msg.set_visible(bool(text))

    def _run(self, args, ok_text, reboot=False):
        if self.p.busy:
            return
        self.p.busy = True
        self._say("처리하는 중…")

        def work():
            try:
                # 파일 복사 방식(ext4)의 첫 지점은 시스템 전체를 복사해 몇 분 걸린다
                r = subprocess.run(["pkexec", HELPER] + args, capture_output=True, text=True, timeout=3600)
                rc, err = r.returncode, (r.stderr or "").strip()
            except Exception as ex:
                rc, err = 1, str(ex)
            GLib.idle_add(done, rc, err)

        def done(rc, err):
            self.p.busy = False
            if rc in (126, 127):                    # 인증 창을 닫았다
                self._say("")
            elif rc != 0:
                line = next((ln[6:] for ln in err.splitlines() if ln.startswith("ERROR ")), err.splitlines()[-1] if err else "")
                self._say(f"하지 못했습니다: {line}")
            else:
                self._say(ok_text or "")
                if reboot:
                    self._say("다시 시작하는 중…")
                    subprocess.Popen(["systemctl", "reboot"])
            self.reload()
            return False
        threading.Thread(target=work, daemon=True).start()


def build(store):
    return RecoveryPage(store).widget


PAGES = [{"id": "recovery", "title": "복구", "icon": ICON, "build": build}]
