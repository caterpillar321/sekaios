#!/bin/bash
# MyOS — chroot 뒷정리 (언마운트)
# 사용법: sudo ~/MyOS/scripts/exit-chroot.sh
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$SELF")"
ROOT="${MYOS_ROOT:-$PROJECT/rootfs}"

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -d "$ROOT" ]       || { echo "E: rootfs 없음: $ROOT"; exit 1; }

rm -f "$ROOT/usr/sbin/policy-rc.d"

# resolv.conf 원복 (호스트 DNS가 이미지에 남지 않게)
rm -f "$ROOT/etc/resolv.conf"
if [ -L "$ROOT/etc/resolv.conf.sekai-saved" ] || [ -e "$ROOT/etc/resolv.conf.sekai-saved" ]; then
  mv -f "$ROOT/etc/resolv.conf.sekai-saved" "$ROOT/etc/resolv.conf"
  echo "  resolv.conf 원복"
fi

# 역순으로 언마운트 (dev/pts 가 dev 보다 먼저)
for m in mnt/packages var/cache/apt/archives run dev/pts dev sys proc; do
  if mountpoint -q "$ROOT/$m"; then
    if umount -l "$ROOT/$m" 2>/dev/null; then
      echo "  언마운트: /$m"
    else
      echo "  !! 실패: /$m"
    fi
  fi
done

echo
echo "=== 남은 마운트 확인 ==="
if mount | grep -q " ${ROOT}/"; then
  mount | grep " ${ROOT}/"
  echo
  echo "!!! 위 항목이 남아있습니다. rootfs 를 삭제하지 마세요."
  exit 1
else
  echo "없음 ✓  rootfs 를 안전하게 삭제/압축할 수 있습니다."
fi
