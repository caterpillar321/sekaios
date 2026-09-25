#!/bin/bash
# SekaiOS — Hyprland 생태계를 별도 buildroot 에서 빌드하고 .deb 을 수확
# 사용법: sudo ~/MyOS/scripts/build-hypr.sh [--recreate]
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot"
PKGDIR="$P/packages"
CACHE="$P/cache"
MIRROR="http://mirror.kakao.com/debian"

C_B=$'\033[1;36m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }

# ── 빌드 의존성 (공백 구분 — 여기만 고치면 됨) ────────
BUILD_DEPS="
build-essential cmake ninja-build meson pkg-config git ca-certificates
dpkg-dev file xz-utils
libwayland-dev wayland-protocols
libdrm-dev libgbm-dev
libgl-dev libegl-dev libgles-dev libopengl-dev libglvnd-dev libglx-dev
libinput-dev libxkbcommon-dev libseat-dev libdisplay-info-dev libsystemd-dev
libpixman-1-dev libcairo2-dev libpango1.0-dev librsvg2-dev
libjpeg-dev libwebp-dev libspng-dev libmagic-dev
libtomlplusplus-dev libzip-dev libre2-dev uuid-dev
libxcursor-dev libglib2.0-dev libudev-dev libpugixml-dev hwdata
libxcb1-dev libxcb-composite0-dev libxcb-ewmh-dev libxcb-icccm4-dev
libxcb-render0-dev libxcb-res0-dev libxcb-xfixes0-dev libxcb-errors-dev
libudis86-dev xwayland
"
DEPS_CSV=$(echo $BUILD_DEPS | tr ' ' ',')

cleanup() {
    for m in /build/deb /var/cache/apt/archives run dev/pts dev sys proc; do
        mountpoint -q "$BR/$m" && umount -l "$BR/$m" 2>/dev/null
    done
    return 0
}
trap 'echo; echo "중단됨"; cleanup; exit 130' INT TERM

# ── buildroot 생성 ───────────────────────────────────
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

# ── 마운트 ───────────────────────────────────────────
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

# ── ★ 의존성을 매번 확인·설치 (목록만 고치고 재실행하면 됨) ──
say "빌드 의존성 확인"
inchroot apt-get update -qq >/dev/null 2>&1 || true
if ! inchroot apt-get install -y --no-install-recommends $BUILD_DEPS > /tmp/.deps.log 2>&1; then
    echo "의존성 설치 실패:"; tail -20 /tmp/.deps.log; cleanup; exit 1
fi
NEW=$(grep -c '^Setting up' /tmp/.deps.log 2>/dev/null || echo 0)
say "의존성 준비 완료 (이번에 새로 설치: ${NEW}개)"

install -m755 "$SELF/hypr/build-inner.sh"   "$BR/build/build-inner.sh"
install -m644 "$SELF/hypr/cxx26-compat.hpp" "$BR/build/cxx26-compat.hpp"
install -m644 "$SELF/hypr/patch-hyprbars-icons.py" "$BR/build/patch-hyprbars-icons.py"
install -m644 "$SELF/hypr/patch-hyprbars-hover.py" "$BR/build/patch-hyprbars-hover.py"
install -m644 "$SELF/hypr/patch-hyprbars-snap.py"  "$BR/build/patch-hyprbars-snap.py"
install -m644 "$SELF/hypr/patch-hyprbars-theme.py" "$BR/build/patch-hyprbars-theme.py"
install -m644 "$SELF/hypr/patch-hyprland-clientmove.py" "$BR/build/patch-hyprland-clientmove.py"
install -m644 "$SELF/hypr/patch-hyprland-keepoutputs.py" "$BR/build/patch-hyprland-keepoutputs.py"
install -m644 "$SELF/hypr/patch-hyprland-bordergrab.py" "$BR/build/patch-hyprland-bordergrab.py"
install -m644 "$SELF/hypr/patch-hyprland-layerfocus.py" "$BR/build/patch-hyprland-layerfocus.py"
install -m644 "$SELF/hypr/patch-hyprland-dndhotspot.py" "$BR/build/patch-hyprland-dndhotspot.py"
install -m644 "$SELF/hypr/patch-hyprbars-bordergrab.py" "$BR/build/patch-hyprbars-bordergrab.py"
install -m644 "$SELF/hypr/patch-hyprbars-focus.py" "$BR/build/patch-hyprbars-focus.py"

# ── 빌드 ─────────────────────────────────────────────
say "빌드 시작"; echo
set +e
inchroot /bin/bash /build/build-inner.sh
RC=$?
set -e

echo
if [ $RC -eq 0 ]; then
    say "성공 — 생성된 패키지:"
    ls -lh "$PKGDIR"/*.deb 2>/dev/null | awk '{printf "    %-10s %s\n", $5, $9}'
else
    say "실패 (종료코드 $RC). 로그: $BR/build/log/"
fi

cleanup
say "언마운트 완료"
exit $RC
