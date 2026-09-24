#!/bin/bash
# rootfs 정리 + squashfs 재생성
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
R="$P/rootfs"
OUT="$P/build/filesystem.zstd.squashfs"

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }

if mount | grep -q " $R/"; then
    echo "E: rootfs 에 마운트가 남아있습니다. exit-chroot.sh 를 먼저 실행하세요."
    mount | grep " $R/"
    exit 1
fi

# overlay 를 먼저 반영한다.
#   예전에 sync-overlay 를 빠뜨린 채 squashfs 를 만들어서
#   설정 변경이 ISO 에 안 들어간 적이 있다. 여기서 항상 보장한다.
echo "==> overlay 동기화"
"$SELF/sync-overlay.sh" | tail -2

echo "==> 정리"
rm -rf  "$R"/var/cache/apt/archives/*.deb "$R"/var/cache/apt/archives/partial/*
rm -rf  "$R"/var/lib/apt/lists/*;  mkdir -p "$R/var/lib/apt/lists/partial"
rm -rf  "$R"/tmp/* "$R"/var/tmp/*
rm -f   "$R"/root/.bash_history "$R"/etc/debian_chroot
find    "$R/var/log" -type f -delete 2>/dev/null || true
: >     "$R/etc/machine-id"
rm -f   "$R/var/lib/dbus/machine-id"
# SSH 호스트 키 — 이미지에 박히면 모든 설치본이 같은 키를 쓰게 된다.
# 지우면 첫 부팅 때 ssh-keygen -A 로 각자 새로 생성된다.
rm -f   "$R"/etc/ssh/ssh_host_*
# 호스트 DNS 가 남지 않게
if [ ! -L "$R/etc/resolv.conf" ]; then
    rm -f "$R/etc/resolv.conf"
    ln -sf ../run/systemd/resolve/stub-resolv.conf "$R/etc/resolv.conf"
fi
# 사용하지 않는 번역·문서 제거 (매 빌드마다 적용)
#   localepurge 가 USE_DPKG 모드로 향후 설치분은 막아주지만,
#   이미 설치된 파일과 man/doc 은 여기서 정리한다.
python3 - "$R" <<'PURGE'
import os, shutil, sys
R = sys.argv[1]
KEEP = {"en", "en_US", "en_GB", "ko", "ko_KR", "C"}
def keep(n): return n.split('.')[0].split('@')[0] in KEEP

loc = os.path.join(R, "usr/share/locale")
if os.path.isdir(loc):
    for e in os.listdir(loc):
        p = os.path.join(loc, e)
        if os.path.isdir(p) and not os.path.islink(p) and not keep(e):
            shutil.rmtree(p, ignore_errors=True)

man = os.path.join(R, "usr/share/man")
if os.path.isdir(man):
    for e in os.listdir(man):
        p = os.path.join(man, e)
        if os.path.isdir(p) and not os.path.islink(p) and not e.startswith("man") and not keep(e):
            shutil.rmtree(p, ignore_errors=True)

doc = os.path.join(R, "usr/share/doc")
for root, _d, files in os.walk(doc):
    for f in files:
        if f != "copyright":
            try: os.remove(os.path.join(root, f))
            except OSError: pass
PURGE

echo "    rootfs: $(du -sh "$R" | cut -f1)"

echo "==> squashfs 생성"
rm -f "$OUT"
mksquashfs "$R" "$OUT" \
  -comp zstd -Xcompression-level 19 \
  -b 1M -noappend -no-progress \
  -processors "$(nproc)" | tail -4

echo "==> 완료"
ls -lh "$OUT" | awk '{print "    "$5"  "$9}'
