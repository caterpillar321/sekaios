"""시스템 — 복원 지점, 사용자 계정. 관리자 인증 창(사용자 계정 컨트롤)을 거쳐 실제로 바뀌는지 본다.
   복원해서 다시 시작하고 되돌리는 시험은 느려서 slow (--slow 로)."""
import time

from mm import vm
from mm.runner import test

SETTINGS = "sekai-settings"


def open_page(t, page):
    t.kill("sekai-settings")
    t.gone(SETTINGS, 5)
    t.after(lambda: t.kill("sekai-settings"))
    w = t.launch(f"sekai-settings --page={page}", SETTINGS, timeout=20)
    t.expect(w, f"설정 › {page}")
    time.sleep(2)
    return w


def row_button(t, label_re, btn_re, timeout=10):
    """그 줄(제목 글)과 같은 높이의 단추"""
    lab = t.ui.wait(app=SETTINGS, role="label", name_re=label_re, timeout=timeout)
    if not lab:
        return None
    for b in t.ui.find(app=SETTINGS, role="button", name_re=btn_re, all=True) or []:
        if b["cy"] is not None and abs(b["cy"] - lab["cy"]) < 40:
            return b
    return None


def dialog_button(t, name_re, timeout=15):
    """대화상자의 단추 — 본 창("설정")이 아닌 창에서만 찾는다 (뒤 창의 줄마다 같은 이름 단추가 있다). 켜질 때까지"""
    def find():
        for b in t.ui.find(app=SETTINGS, role="button", name_re=name_re, all=True, not_frame="^설정$") or []:
            if b["cy"] is not None and "sensitive" in b["states"]:
                return b
    return t.wait(find, timeout)


def snapshots(t):
    return t.root("snapper --no-dbus -c root list --columns description 2>/dev/null").out


@test("복원 지점 만들기 → 목록에 → 삭제", suite="system", timeout=300)
def restore_point(t):
    name = f"MM point {int(time.time()) % 100000}"
    t.after(lambda: t.root(f"for n in $(snapper --no-dbus -c root list --columns number,description | awk -F'|' '/{name}/{{print $1}}'); "
                           f"do snapper --no-dbus -c root delete $n; done; true"))
    open_page(t, "recovery")
    mk = t.ui.click(app=SETTINGS, role="button", name="만들기…", timeout=10)
    t.expect(mk, "[만들기…]")
    time.sleep(1)
    t.key("ctrl-a")
    t.type(name)
    ok = dialog_button(t, "^만들기$")
    t.expect(ok, "대화상자의 [만들기]")
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(t.wait(lambda: name in snapshots(t), 120, every=3), "snapper 에 복원 지점이 생겼다")
    t.expect(t.ui.wait(app=SETTINGS, role="label", name=name, timeout=20), "설정 목록에 보인다")
    t.shot("만듦")
    rm = row_button(t, f"^{name}$", "^삭제$")
    t.expect(rm, "그 줄의 [삭제]")
    t.click(rm["cx"], rm["cy"])
    ok = dialog_button(t, "^삭제$")
    t.expect(ok, "확인 창의 [삭제]")
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(t.wait(lambda: name not in snapshots(t), 60, every=3), "snapper 에서 지워졌다")


USER = "mmtest"


def user_exists(t):
    return t.sh(f"getent passwd {USER} >/dev/null").ok


@test("계정 추가(표준 사용자) → 생김 → 삭제(파일까지)", suite="system", timeout=300)
def user_add_remove(t):
    t.after(lambda: t.root(f"getent passwd {USER} >/dev/null && deluser --remove-home {USER} >/dev/null 2>&1; true"))
    if user_exists(t):
        t.root(f"deluser --remove-home {USER} >/dev/null 2>&1")
    open_page(t, "users")
    add = t.ui.click(app=SETTINGS, role="button", name="계정 추가…", timeout=10)
    t.expect(add, "[계정 추가…]")
    time.sleep(1)
    # 첫 칸(이름)에 초점 — 이름을 치면 사용자 이름이 저절로 채워진다
    t.type(f"{USER} Kun")
    t.key("tab")
    time.sleep(0.3)
    t.key("tab")
    t.type("mm-pass-1234")
    t.key("tab")
    t.type("mm-pass-1234")
    ok = dialog_button(t, "^만들기$")
    t.shot("계정추가")
    t.expect(ok, "[만들기]가 켜졌다 (사용자 이름이 채워지고 암호가 같다)")
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(t.wait(lambda: user_exists(t), 60, every=2), f"{USER} 계정이 생겼다")
    t.expect(not t.sh(f"id -nG {USER} | grep -qw sudo").ok, "표준 사용자 (sudo 무리 아님)")
    t.expect(t.sh(f"test -d /home/{USER}").ok, "홈 폴더가 생겼다")
    t.expect(t.ui.wait(app=SETTINGS, role="label", name_re=f"{USER} Kun", timeout=10), "목록에 보인다")
    rm = row_button(t, f"{USER} Kun", "^삭제…$")
    t.expect(rm, "그 줄의 [삭제…]")
    t.click(rm["cx"], rm["cy"])
    ok = dialog_button(t, "^파일까지 삭제$")
    t.expect(ok, "[파일까지 삭제]")
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(t.wait(lambda: not user_exists(t), 60, every=2), "계정이 지워졌다")
    t.expect(t.wait(lambda: not t.sh(f"test -e /home/{USER}").ok, 10), "홈 폴더도 지워졌다")


@test("복원 지점으로 되돌리고 다시 시작 → 그 뒤의 변경이 사라짐 → 복원 취소로 돌아옴", suite="system",
      timeout=1500, slow=True)
def restore_roundtrip(t):
    name = f"MM roundtrip {int(time.time()) % 100000}"
    marker = "/etc/mafuyumom-marker"
    t.root(f"rm -f {marker}")
    t.root(f"/usr/libexec/sekai/sekai-restore create '{name}'")
    t.expect(name in snapshots(t), "복원 지점을 만들었다")
    t.root(f"echo after > {marker}")                    # 복원 지점 뒤의 변경
    open_page(t, "recovery")
    b = row_button(t, f"^{name}$", "^복원…$", timeout=15)
    t.expect(b, "그 줄의 [복원…]")
    t.click(b["cx"], b["cy"])
    ok = dialog_button(t, "되돌리고 다시 시작")
    t.expect(ok, "[되돌리고 다시 시작]")
    old = vm.boot_id()
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(vm.wait_rebooted(t.q, old), "다시 시작해 로그인했다")
    t.expect(not t.sh(f"test -e {marker}").ok, "복원 지점 뒤에 만든 파일이 사라졌다")
    open_page(t, "recovery")
    t.shot("복원뒤")
    u = t.ui.click(app=SETTINGS, role="button", name="복원 취소…", timeout=15)
    t.expect(u, "[복원 취소…]")
    ok = dialog_button(t, "복원 취소하고 다시 시작")
    t.expect(ok, "[복원 취소하고 다시 시작]")
    old = vm.boot_id()
    t.click(ok["cx"], ok["cy"])
    t.auth()
    t.expect(vm.wait_rebooted(t.q, old), "다시 시작해 로그인했다")
    t.expect(t.sh(f"cat {marker}").out.strip() == "after", "복원 취소 — 파일이 돌아왔다")
    t.root(f"rm -f {marker}")
