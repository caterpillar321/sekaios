#!/bin/bash
# buildroot-wine 안에서 돈다 (scripts/build-wine.sh 가 부른다) — Wine 엔진 하나를 빌드해 sekai-wine-<판>.deb 으로
set -euo pipefail

WINE_VER=11.0
WINE_SHA256=c07a6857933c1fc60dff5448d79f39c92481c1e9db5aa628db9d0358446e0701   # 서명 확인한 tarball (2026-10-05)
WINE_URL="https://dl.winehq.org/wine/source/11.0/wine-${WINE_VER}.tar.xz"
REV=sekai1
PKG="sekai-wine-${WINE_VER}"
PREFIX="/usr/lib/sekai/wine/wine-${WINE_VER}"
OUT=/build/deb
LOG=/build/log
mkdir -p /build/src "$LOG" "$OUT/sources"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }

cd /build/src
[ -f "wine-${WINE_VER}.tar.xz" ] || curl -fsSLO "$WINE_URL"
echo "${WINE_SHA256}  wine-${WINE_VER}.tar.xz" | sha256sum -c --quiet
cp -f "wine-${WINE_VER}.tar.xz" "$OUT/sources/"        # LGPL — 빌드한 그 소스를 저장소에 같이 올린다

# 이미 configure 한 빌드가 있으면 이어서 (포장만 고칠 때 40분을 다시 들이지 않게) — 새로: WINE_CLEAN=1
rm -rf stage
if [ -n "${WINE_CLEAN:-}" ] || [ ! -f build/config.status ]; then
    say "풀기"
    rm -rf "wine-${WINE_VER}" build
    tar xf "wine-${WINE_VER}.tar.xz"
    mkdir build
    cd build
    say "configure (WoW64)"
    ../"wine-${WINE_VER}"/configure --prefix="$PREFIX" --enable-archs=i386,x86_64 --disable-tests \
        --without-capi --without-gphoto --without-pcap --without-netapi --without-oss \
        > "$LOG/configure.log" 2>&1 || { tail -40 "$LOG/configure.log"; exit 1; }
    grep -E "^configure: (WARNING|OpenCL|libavcodec|gstreamer)" "$LOG/configure.log" | head -20 || true
else
    say "이전 빌드에 이어서 (새로 하려면 WINE_CLEAN=1)"
    cd build
fi
mkdir -p /build/src/stage

say "make -j$(nproc) — 20~40분"
make -j"$(nproc)" > "$LOG/make.log" 2>&1 || { tail -60 "$LOG/make.log"; exit 1; }
say "install"
make install-lib DESTDIR=/build/src/stage > "$LOG/install.log" 2>&1 || { tail -40 "$LOG/install.log"; exit 1; }

cd /build/src/stage
# 리눅스 쪽 실행 파일만 strip (PE 파일은 그대로)
find ".${PREFIX}/bin" ".${PREFIX}/lib/wine/x86_64-unix" -type f -exec sh -c \
    'file -b "$1" | grep -q "^ELF" && strip --strip-unneeded "$1"' _ {} \; 2>/dev/null || true
# 윈도우 쪽(PE) DLL·EXE 의 디버그 정보 — 빼지 않으면 패키지가 두 배 넘게 크다
find ".${PREFIX}/lib/wine/x86_64-windows" -type f \( -name '*.dll' -o -name '*.exe' -o -name '*.sys' -o -name '*.drv' \
     -o -name '*.ocx' -o -name '*.cpl' -o -name '*.acm' -o -name '*.ax' -o -name '*.tlb' -o -name '*.ds' \) \
     -exec x86_64-w64-mingw32-strip --strip-debug {} + 2>/dev/null || true
find ".${PREFIX}/lib/wine/i386-windows" -type f \( -name '*.dll' -o -name '*.exe' -o -name '*.sys' -o -name '*.drv' \
     -o -name '*.ocx' -o -name '*.cpl' -o -name '*.acm' -o -name '*.ax' -o -name '*.tlb' -o -name '*.ds' \) \
     -exec i686-w64-mingw32-strip --strip-debug {} + 2>/dev/null || true
rm -rf ".${PREFIX}/include" ".${PREFIX}/share/man"
du -sh ".${PREFIX}"

# 문서 — 저작권 · 라이선스 전문 · 소스 위치
DOC="usr/share/doc/${PKG}"
mkdir -p "$DOC"
cat > "$DOC/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Wine
Upstream-Contact: https://www.winehq.org/
Source: ${WINE_URL}
Comment: 고치지 않은 Wine ${WINE_VER} 를 SekaiOS 가 빌드한 것 (WoW64, ${PREFIX}).
 빌드한 소스 tarball 은 SekaiOS apt 저장소의 sources/wine-${WINE_VER}.tar.xz 에도 있다
 (https://caterpillar321.github.io/sekaios-apt/sources/). 빌드 스크립트: SekaiOS 저장소 scripts/wine/.

Files: *
Copyright: 1993-2026 the Wine project authors (see AUTHORS)
License: LGPL-2.1+
EOF
cat "/build/src/wine-${WINE_VER}/LICENSE" >> "$DOC/copyright"
echo >> "$DOC/copyright"
cat "/build/src/wine-${WINE_VER}/COPYING.LIB" >> "$DOC/copyright"
cp "/build/src/wine-${WINE_VER}/AUTHORS" "$DOC/AUTHORS"

# Depends — 링크된 것은 dpkg-shlibdeps 가, dlopen 으로 여는 것은 손으로
mkdir -p /build/src/shl/debian
cd /build/src/shl
printf 'Source: %s\n\nPackage: %s\nArchitecture: amd64\n' "$PKG" "$PKG" > debian/control
ELFS=$(find "/build/src/stage${PREFIX}/bin" "/build/src/stage${PREFIX}/lib/wine/x86_64-unix" -type f \
       -exec sh -c 'file -b "$1" | grep -q "^ELF" && echo "$1"' _ {} \;)
SHL=$(dpkg-shlibdeps -O --ignore-missing-info -e $ELFS 2>/dev/null | sed -n 's/^shlibs:Depends=//p')
DLOPEN="libfreetype6, libfontconfig1, libgnutls30t64, libvulkan1, libgl1, libegl1, libx11-6, libxcursor1, libxi6, libxrandr2, libxinerama1, libxcomposite1, libxfixes3, libxrender1, libxxf86vm1, libdbus-1-3"
RECO="libsdl2-2.0-0, libcups2t64, libkrb5-3, libgssapi-krb5-2, libv4l-0t64, libodbc2, gstreamer1.0-plugins-good, mesa-vulkan-drivers"

cd /build/src/stage
mkdir -p DEBIAN
SIZE=$(du -sk --exclude=DEBIAN . | cut -f1)
cat > DEBIAN/control <<EOF
Package: ${PKG}
Version: ${WINE_VER}-${REV}
Architecture: amd64
Maintainer: SekaiOS <kbktw0705@gmail.com>
Installed-Size: ${SIZE}
Depends: ${SHL}, ${DLOPEN}
Recommends: ${RECO}
Section: otherosfs
Priority: optional
Homepage: https://www.winehq.org/
Description: Wine ${WINE_VER} engine for SekaiOS (WoW64)
 Unmodified Wine ${WINE_VER} built for SekaiOS in WoW64 mode, so 32-bit
 Windows programs run without i386 multiarch libraries. Installed under
 ${PREFIX} so several engine versions can live side by side; the SekaiOS
 Windows app manager picks the engine per app.
EOF
cd /build/src
dpkg-deb --root-owner-group -Zxz -b stage "$OUT/${PKG}_${WINE_VER}-${REV}_amd64.deb" >/dev/null
say "패키지: ${PKG}_${WINE_VER}-${REV}_amd64.deb"
dpkg-deb -f "$OUT/${PKG}_${WINE_VER}-${REV}_amd64.deb" Depends | tr ',' '\n' | sed 's/^ */    /' | head -60
