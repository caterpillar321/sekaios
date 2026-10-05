#!/bin/bash
# SekaiOS — xdg-desktop-portal-wlr(화면 찍기·나누기 포털)를 데비안 소스 + 우리 패치로 다시 빌드해 .deb 을 수확
#   패치: scripts/xdpw/*.patch — 합성기가 dmabuf 피드백을 다시 보내면(모니터가 꺼졌다 켜질 때) 포털이 죽던 것
#   판: 데비안 판 + "sekai1" (예: 0.7.1-2sekai1) — 데비안 것보다 높아 업데이트로 바뀐다
# 사용법: sudo scripts/build-xdpw.sh   (buildroot 는 build-hypr.sh 가 만든 trixie 것을 쓴다)
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot"
PKGDIR="$P/packages"
CACHE="$P/cache"
MIRROR="http://mirror.kakao.com/debian"
SRCPKG=xdg-desktop-portal-wlr
SUFFIX=sekai1
C_B=$'\033[1;36m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -x "$BR/bin/bash" ] || { echo "E: $BR 가 없습니다 — 먼저 sudo scripts/build-hypr.sh"; exit 1; }

cleanup() {
    rm -f "$BR/etc/apt/sources.list.d/sekai-src.sources"
    for m in /build/deb /var/cache/apt/archives run dev/pts dev sys proc; do
        mountpoint -q "$BR/$m" && umount -l "$BR/$m" 2>/dev/null
    done
    return 0
}
trap 'echo; echo "중단됨"; cleanup; exit 130' INT TERM
trap cleanup EXIT

say "마운트"
mkdir -p "$PKGDIR" "$CACHE" "$BR/build/deb" "$BR/var/cache/apt/archives"
mountpoint -q "$BR/proc"    || mount -t proc  proc  "$BR/proc"
mountpoint -q "$BR/sys"     || mount -t sysfs sysfs "$BR/sys"
mountpoint -q "$BR/dev"     || mount --bind /dev     "$BR/dev"
mountpoint -q "$BR/dev/pts" || mount --bind /dev/pts "$BR/dev/pts"
mountpoint -q "$BR/run"     || mount -t tmpfs tmpfs "$BR/run"
mountpoint -q "$BR/var/cache/apt/archives" || mount --bind "$CACHE" "$BR/var/cache/apt/archives"
mountpoint -q "$BR/build/deb" || mount --bind "$PKGDIR" "$BR/build/deb"
cp -L /etc/resolv.conf "$BR/etc/resolv.conf"

inchroot() {
    chroot "$BR" /usr/bin/env -i \
        HOME=/root TERM="${TERM:-xterm}" \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 \
        "$@"
}

say "데비안 소스 받기"
cat > "$BR/etc/apt/sources.list.d/sekai-src.sources" <<EOF
Types: deb-src
URIs: $MIRROR
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
inchroot apt-get update -qq
inchroot apt-get install -y -qq --no-install-recommends dpkg-dev devscripts quilt >/dev/null
inchroot apt-get build-dep -y -qq "$SRCPKG" >/dev/null
rm -rf "$BR/build/xdpw"; mkdir -p "$BR/build/xdpw"
inchroot sh -c "cd /build/xdpw && apt-get source -qq $SRCPKG >/dev/null"
SRC=$(cd "$BR/build/xdpw" && ls -d ${SRCPKG}-*/ | head -1); SRC="${SRC%/}"
VER=$(inchroot sh -c "cd /build/xdpw/$SRC && dpkg-parsechangelog -S Version")
NEW="${VER}${SUFFIX}"
say "패치 ($VER → $NEW)"
mkdir -p "$BR/build/xdpw/$SRC/debian/patches"
for p in "$SELF"/xdpw/*.patch; do
    cp "$p" "$BR/build/xdpw/$SRC/debian/patches/sekai-$(basename "$p")"
    echo "sekai-$(basename "$p")" >> "$BR/build/xdpw/$SRC/debian/patches/series"
done
inchroot sh -c "cd /build/xdpw/$SRC && QUILT_PATCHES=debian/patches quilt push -a -q >/dev/null && QUILT_PATCHES=debian/patches quilt pop -a -q >/dev/null"
inchroot sh -c "cd /build/xdpw/$SRC && DEBFULLNAME='SekaiOS' DEBEMAIL='sekaios@users.noreply.github.com' \
    dch -v '$NEW' -D trixie --force-distribution 'SekaiOS: 합성기가 dmabuf 피드백을 다시 보내면 죽던 것 고침 (모니터 끄기·켜기)'"

say "빌드"
inchroot sh -c "cd /build/xdpw/$SRC && dpkg-buildpackage -b -us -uc -j\$(nproc)" > /tmp/.xdpw-build.log 2>&1 \
    || { echo "E: 빌드 실패"; tail -30 /tmp/.xdpw-build.log; exit 1; }
rm -f "$PKGDIR"/${SRCPKG}_*.deb
cp "$BR"/build/xdpw/${SRCPKG}_${NEW#*:}_*.deb "$PKGDIR"/
ls -la "$PKGDIR"/${SRCPKG}_*.deb
say "완료 — packages/ 에 넣었습니다 (scripts/build-repo.sh 로 저장소에)"
