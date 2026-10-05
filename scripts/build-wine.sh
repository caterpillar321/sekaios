#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  SekaiOS — Wine 엔진을 trixie buildroot 에서 빌드해 .deb 으로 (sekai-wine-<판>)
#
#    sudo scripts/build-wine.sh [--recreate]
#
#  · WoW64 (--enable-archs=i386,x86_64) — 32비트 윈도우 앱도 i386 라이브러리(멀티아키) 없이 돈다
#  · /usr/lib/sekai/wine/wine-<판> 에 — 판마다 따로 깔려 앱마다 엔진을 고를 수 있다 (sekaiwine)
#  · 남의 빌드(Kron4ek 등)를 받아 쓰지 않는 까닭: 옛 배포판에 맞춰 빌드돼 trixie 에 없는 라이브러리
#    (FFmpeg 4 의 libavcodec.so.58 등)를 찾는다. 여기서 빌드하면 dpkg-shlibdeps 가 Depends 를 정확히 단다
#  · 소스는 WineHQ 의 서명(Alexandre Julliard, DA23579A…AF17519D)을 확인한 tarball — SHA-256 을 고정한다
#  · Hyprland buildroot 와 섞이지 않게 buildroot-wine 을 따로 쓴다
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot-wine"
PKGDIR="$P/packages"
CACHE="$P/cache"
MIRROR="http://mirror.kakao.com/debian"

C_B=$'\033[1;36m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }

BUILD_DEPS="
build-essential flex bison gettext curl ca-certificates xz-utils dpkg-dev file
gcc-mingw-w64-i686 gcc-mingw-w64-x86-64
libx11-dev libxext-dev libxcursor-dev libxi-dev libxrandr-dev libxinerama-dev libxcomposite-dev
libxfixes-dev libxrender-dev libxxf86vm-dev libxkbcommon-dev libxkbregistry-dev libwayland-dev
libegl-dev libgl-dev libvulkan-dev libfreetype-dev libfontconfig-dev libgnutls28-dev libdbus-1-dev
libpulse-dev libasound2-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev
libavcodec-dev libavformat-dev libavutil-dev libsdl2-dev libudev-dev libusb-1.0-0-dev
libcups2-dev libkrb5-dev libpcsclite-dev libsane-dev libv4l-dev ocl-icd-opencl-dev opencl-headers
unixodbc-dev
"
DEPS_CSV=$(echo $BUILD_DEPS | tr ' ' ',')

cleanup() {
    for m in /build/deb /var/cache/apt/archives run dev/pts dev sys proc; do
        mountpoint -q "$BR/$m" && umount -l "$BR/$m" 2>/dev/null
    done
    return 0
}
trap 'echo; echo "중단됨"; cleanup; exit 130' INT TERM

if [ "${1:-}" = "--recreate" ]; then
    say "기존 buildroot 삭제"; cleanup; rm -rf "$BR"
fi

if [ ! -x "$BR/bin/bash" ]; then
    say "buildroot 생성 (trixie) — 수 분 소요"
    rm -rf "$BR"; mkdir -p "$BR" "$CACHE"
    mmdebstrap --mode=root --variant=minbase --architectures=amd64 \
        --components="main contrib non-free non-free-firmware" \
        --include="$DEPS_CSV" \
        trixie "$BR" "$MIRROR"
else
    say "기존 buildroot 재사용: $BR"
fi

say "마운트"
mkdir -p "$PKGDIR" "$BR/build/deb" "$BR/var/cache/apt/archives"
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

say "빌드 의존성 확인"
inchroot apt-get update -qq >/dev/null 2>&1 || true
if ! inchroot apt-get install -y --no-install-recommends $BUILD_DEPS > /tmp/.wine-deps.log 2>&1; then
    echo "의존성 설치 실패:"; tail -20 /tmp/.wine-deps.log; cleanup; exit 1
fi
say "의존성 준비 완료"

install -m755 "$SELF/wine/build-inner.sh" "$BR/build/build-inner.sh"

say "빌드 시작"; echo
set +e
inchroot /bin/bash /build/build-inner.sh
RC=$?
set -e

echo
if [ $RC -eq 0 ]; then
    say "성공:"; ls -lh "$PKGDIR"/sekai-wine-*.deb 2>/dev/null | awk '{printf "    %-10s %s\n", $5, $9}'
else
    say "실패 (종료코드 $RC). 로그: $BR/build/log/"
fi
cleanup
exit $RC
