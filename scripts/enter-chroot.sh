#!/bin/bash
# MyOS — chroot 진입
# 사용법: sudo ~/MyOS/scripts/enter-chroot.sh
set -euo pipefail

# 스크립트 자신의 위치에서 프로젝트 루트를 역산 ($HOME 에 의존하지 않음)
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$SELF")"
ROOT="${MYOS_ROOT:-$PROJECT/rootfs}"
CACHE="${MYOS_CACHE:-$PROJECT/cache}"

[ "$(id -u)" -eq 0 ]    || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -d "$ROOT" ]          || { echo "E: rootfs 없음: $ROOT"; exit 1; }
[ -x "$ROOT/bin/bash" ] || { echo "E: 유효한 rootfs 가 아님: $ROOT"; exit 1; }

mkdir -p "$CACHE" "$ROOT/var/cache/apt/archives"

# --- 가상 파일시스템 (이미 마운트돼 있으면 건너뜀) ---
mountpoint -q "$ROOT/proc"    || mount -t proc  proc  "$ROOT/proc"
mountpoint -q "$ROOT/sys"     || mount -t sysfs sysfs "$ROOT/sys"
mountpoint -q "$ROOT/dev"     || mount --bind /dev     "$ROOT/dev"
mountpoint -q "$ROOT/dev/pts" || mount --bind /dev/pts "$ROOT/dev/pts"
mountpoint -q "$ROOT/run"     || mount -t tmpfs tmpfs "$ROOT/run"
mountpoint -q "$ROOT/var/cache/apt/archives" \
  || mount --bind "$CACHE" "$ROOT/var/cache/apt/archives"

# 우리가 만든 .deb 을 chroot 안에서 설치할 수 있게
mkdir -p "$PROJECT/packages" "$ROOT/mnt/packages"
mountpoint -q "$ROOT/mnt/packages" || mount --bind "$PROJECT/packages" "$ROOT/mnt/packages"

# --- DNS (chroot 안에서 이름 해석용) ---
# 기존 resolv.conf(심볼릭 링크일 수 있음)를 보관해두고 호스트 것을 넣음
if [ -L "$ROOT/etc/resolv.conf" ] || [ -e "$ROOT/etc/resolv.conf" ]; then
  mv -f "$ROOT/etc/resolv.conf" "$ROOT/etc/resolv.conf.sekai-saved"
fi
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"

# --- 패키지 설치 중 서비스 자동 시작 차단 ---
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"

echo "=============================================="
echo " chroot 진입: $ROOT"
echo " 나올 때: exit  →  그 다음 반드시 exit-chroot.sh"
echo "=============================================="
echo

chroot "$ROOT" /usr/bin/env -i \
  HOME=/root \
  TERM="${TERM:-xterm}" \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  DEBIAN_FRONTEND=noninteractive \
  LC_ALL=C.UTF-8 LANG=C.UTF-8 \
  PS1='(MyOS) \w # ' \
  /bin/bash +h
