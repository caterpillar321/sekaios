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
mnt() {
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
    rm -f "$R/usr/sbin/policy-rc.d" "$R/etc/resolv.conf"
    [ -e "$R/etc/resolv.conf.sekai-saved" ] || [ -L "$R/etc/resolv.conf.sekai-saved" ] && \
        mv -f "$R/etc/resolv.conf.sekai-saved" "$R/etc/resolv.conf" 2>/dev/null || true
    for m in mnt/packages var/cache/apt/archives run dev/pts dev sys proc; do
        mountpoint -q "$R/$m" && umount -l "$R/$m" 2>/dev/null
    done
    return 0
}
trap 'echo; echo "중단됨"; umnt; exit 130' INT TERM

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
