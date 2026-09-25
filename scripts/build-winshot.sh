#!/bin/bash
# SekaiOS — sekai-winshot (작업 표시줄 창 미리보기용 창 캡처 도구) 을 buildroot(trixie) 에서 빌드해 .deb 로
# 사용법: sudo ~/MyOS/scripts/build-winshot.sh     (먼저 build-hypr.sh 로 buildroot 가 있어야 한다)
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot"
SRC="$P/src/sekai-winshot"
PKGDIR="$P/packages"
VER="0.1.0-sekai1"
PROTO=/usr/share/hyprland-protocols/protocols/hyprland-toplevel-export-v1.xml

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -x "$BR/bin/bash" ] || { echo "E: buildroot 없음 — 먼저 scripts/build-hypr.sh"; exit 1; }
[ -f "$BR$PROTO" ] || { echo "E: buildroot 에 hyprland-protocols 가 없음"; exit 1; }

W="$BR/build/winshot"
rm -rf "$W"; mkdir -p "$W"
cp "$SRC/winshot.c" "$W/"
chroot "$BR" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C.UTF-8 sh -euc "
    cd /build/winshot
    wayland-scanner client-header $PROTO hyprland-toplevel-export-v1-client-protocol.h
    wayland-scanner private-code  $PROTO hyprland-toplevel-export-v1-protocol.c
    gcc -O2 -Wall -Wextra -Werror -o sekai-winshot winshot.c hyprland-toplevel-export-v1-protocol.c \
        \$(pkg-config --cflags --libs wayland-client)
    strip sekai-winshot
"

ST="$W/stage"
mkdir -p "$ST/DEBIAN" "$ST/usr/libexec/sekai" "$ST/usr/share/doc/sekai-winshot"
install -m755 "$W/sekai-winshot" "$ST/usr/libexec/sekai/sekai-winshot"
cat > "$ST/usr/share/doc/sekai-winshot/copyright" <<'EOF'
sekai-winshot — part of SekaiOS.
Uses the hyprland-toplevel-export-v1 protocol (hyprland-protocols, BSD-3-Clause).
EOF
cat > "$ST/DEBIAN/control" <<EOF
Package: sekai-winshot
Version: $VER
Architecture: amd64
Maintainer: SekaiOS <sekai@localhost>
Section: x11
Priority: optional
Depends: libwayland-client0
Description: SekaiOS taskbar window preview capture helper
 Captures a single Hyprland window (hyprland-toplevel-export-v1) and prints
 a downscaled RGBA image, for the taskbar window previews.
EOF
mkdir -p "$PKGDIR"
dpkg-deb --root-owner-group -b "$ST" "$PKGDIR/sekai-winshot_${VER}_amd64.deb" >/dev/null
chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$PKGDIR/sekai-winshot_${VER}_amd64.deb"
echo "==> $PKGDIR/sekai-winshot_${VER}_amd64.deb"
