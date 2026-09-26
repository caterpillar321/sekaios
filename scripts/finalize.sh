#!/bin/bash
# SekaiOS — rootfs 최종 정비 + ISO 빌드까지 한 번에
#   sudo ~/MyOS/scripts/finalize.sh
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
R="$P/rootfs"

C_B=$'\033[1;36m'; C_G=$'\033[1;32m'; C_0=$'\033[0m'
say(){ printf '\n%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok(){  printf '%s  ok%s %s\n' "$C_G" "$C_0" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요"; exit 1; }

# ── 마운트 ───────────────────────────────────────────
MOUNTED=0
mnt() {
    MOUNTED=1
    mkdir -p "$R/mnt/packages" "$P/packages"
    mountpoint -q "$R/proc"    || mount -t proc  proc  "$R/proc"
    mountpoint -q "$R/sys"     || mount -t sysfs sysfs "$R/sys"
    mountpoint -q "$R/dev"     || mount --bind /dev     "$R/dev"
    mountpoint -q "$R/dev/pts" || mount --bind /dev/pts "$R/dev/pts"
    mountpoint -q "$R/run"     || mount -t tmpfs tmpfs "$R/run"
    mountpoint -q "$R/var/cache/apt/archives" || mount --bind "$P/cache" "$R/var/cache/apt/archives"
    mountpoint -q "$R/mnt/packages" || mount --bind "$P/packages" "$R/mnt/packages"
    [ -L "$R/etc/resolv.conf" ] || [ -e "$R/etc/resolv.conf" ] && \
        mv -f "$R/etc/resolv.conf" "$R/etc/resolv.conf.sekai-saved" 2>/dev/null || true
    cp -L /etc/resolv.conf "$R/etc/resolv.conf"
    printf '#!/bin/sh\nexit 101\n' > "$R/usr/sbin/policy-rc.d"; chmod 755 "$R/usr/sbin/policy-rc.d"
}
umnt() {
    [ "$MOUNTED" = 1 ] || return 0          # 두 번 불려도(EXIT 트랩) 되살린 resolv.conf 를 지우지 않게
    MOUNTED=0
    rm -f "$R/usr/sbin/policy-rc.d" "$R/etc/resolv.conf"
    [ -e "$R/etc/resolv.conf.sekai-saved" ] || [ -L "$R/etc/resolv.conf.sekai-saved" ] && \
        mv -f "$R/etc/resolv.conf.sekai-saved" "$R/etc/resolv.conf" 2>/dev/null || true
    for m in mnt/packages var/cache/apt/archives run dev/pts dev sys proc; do
        mountpoint -q "$R/$m" && umount -l "$R/$m" 2>/dev/null
    done
    return 0
}
trap 'echo; echo "중단됨"; umnt; exit 130' INT TERM
# 도중에 실패해도(set -e) 마운트·policy-rc.d·resolv.conf 를 남기지 않는다
trap 'umnt' EXIT

inroot() {
    chroot "$R" /usr/bin/env -i HOME=/root TERM="${TERM:-xterm}" \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 \
        /bin/bash -c "$1"
}

say "마운트"
mnt

say "패키지 인덱스 갱신"
inroot "apt-get update -qq" >/dev/null

say "SekaiOS 데스크탑 설치 (sekai-desktop 메타패키지)"
#   앱 목록은 이제 sekai-desktop 의 Depends 가 정의한다.
#   이미지와 '업그레이드된 옛 설치본'이 같은 목록으로 수렴하게 하려는 것.
#   로컬 .deb 를 넘기면 apt 가 나머지 의존성은 데비안 저장소에서 받는다.
inroot "apt-get install -y --reinstall --allow-downgrades \
        -o Dpkg::Options::=--force-confnew /mnt/packages/*.deb" 2>&1 | tail -3
inroot "apt-mark manual sekai-desktop sekai-shell >/dev/null"
ok "sekai-desktop $(inroot "dpkg-query -W -f='\${Version}' sekai-desktop")"

say "fnott 제거 (알림 데몬은 sekai-panel 이 직접 제공)"
inroot "apt-get purge -y fnott 2>/dev/null | tail -1 || true"

say "대체된 다른 DE 앱 제거 (파일 탐색기·메모장·사진·계산기·사용자 계정 컨트롤·설정 › 소리가 대신한다)"
#   설치된 PC 는 업데이트해도 지우지 않고 메뉴에서만 숨긴다 (/usr/share/sekai/data/applications).
#   이미지에는 처음부터 넣지 않는다. lxpolkit 은 nm-applet 이 요구하는 polkit-1-auth-agent 를
#   sekai-shell 이 대신 제공해야 지울 수 있다 — 무엇이든 SekaiOS 구성 요소를 같이 지우게 되면 멈춘다
OLD_APPS="thunar thunar-volman thunar-data mousepad ristretto galculator xarchiver evince pavucontrol
          lxpolkit xfce4-taskmanager system-config-printer tumbler xdg-user-dirs-gtk"
OLD_APPS=$(echo $OLD_APPS)
#   (결과를 먼저 받아 둔다 — pipefail 에서 grep -q 가 일찍 끝나면 앞 명령이 SIGPIPE 로 실패로 잡혀 검사가 새어 나간다)
SIM=$(inroot "apt-get -s purge $OLD_APPS 2>&1" || true)
if grep -qE '^(Purg|Remv) (sekai-|network-manager|hyprland|nm-)' <<<"$SIM"; then
    echo "E: 옛 앱을 지우면 SekaiOS 구성 요소도 지워집니다 — 멈춥니다 (apt-get -s purge $OLD_APPS)"
    exit 1
fi
inroot "apt-get purge -y $OLD_APPS 2>&1 | tail -1; apt-get autoremove --purge -y 2>&1 | tail -1"

say "NetworkManager 전환"
inroot "systemctl disable systemd-networkd.socket systemd-networkd 2>/dev/null || true
        systemctl mask systemd-networkd 2>/dev/null || true
        systemctl enable NetworkManager systemd-resolved 2>/dev/null || true
        # 데비안 13 은 소켓 활성화가 기본. ssh.service 는 ssh.socket 과 충돌한다.
        systemctl disable ssh.service 2>/dev/null || true
        systemctl enable  ssh.socket  2>/dev/null || true
        rm -f /etc/systemd/network/10-dhcp.network"
ok "NetworkManager 활성화, systemd-networkd 비활성화"

say "언마운트"
umnt

say "squashfs 재생성 (overlay 동기화 포함)"
"$SELF/mksquash.sh" | tail -3

say "ISO 빌드"
sudo -u "${SUDO_USER:-$USER}" "$SELF/build-iso.sh" --win 2>&1 | tail -6

say "완료"
