#!/bin/bash
# SekaiOS — OpenRGB 포크(~/Project/openrgb, sekai 브랜치)를 데비안 패키지로 빌드
#   sudo ~/Project/SekaiOS/scripts/build-openrgb.sh      (buildroot 가 있어야 한다 — scripts/build-hypr.sh)
#
# 왜 포크: OpenRGB 1.0 명령줄은 장치 스레드가 모드를 쓰는 동안 메인 스레드에서 색을 바로 써서, ENE 칩
#   (ASUS 램·ROG Astral 그래픽카드)의 "포인터 → 값" 두 단계가 섞였다 — 색이 0x8020~0x8025 에 떨어져
#   LED 가 꺼지거나 엉뚱한 색이 됐다(2026-10-15, smbus ftrace 로 확인). 포크: 색도 장치 스레드로,
#   ENE 레지스터 접근은 한 쌍씩 잠금. 데비안 의존성은 shlibs 에 맡김(원본 control 의 libmbedtls14 는 trixie 에 없다).
# OpenRGB 는 GPL-2.0-or-later — 포크 소스는 공개 저장소에, .deb 에는 원본 copyright·라이선스가 그대로 들어간다.
# 의존하는 hidapi-hotplug(OpenRGB 가 hidapi 를 고친 것)은 데비안에 없어 원본 릴리스 .deb 를 그대로 싣는다.
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot"
PKGDIR="$P/packages"
FORK="${OPENRGB_FORK:-/home/caterpillar/Project/openrgb}"
OPENRGB_REF="a677b523"          # sekai 브랜치의 고정 커밋 — 올릴 때 SUFFIX 도 올린다
OPENRGB_VER="1.0.0"             # 원본 릴리스 .deb 과 같은 꼴 (qmake 의 VERSION_DEB 는 git 태그 없이 "1.0." 이 된다)
SUFFIX="+sekai1"
HIDAPI_URL="https://codeberg.org/OpenRGB/hidapi-hotplug/releases/download/hidapi-hotplug-0.15.0"
HIDAPI_DEBS="libhidapi-hotplug-hidraw0_0.15.0-1_trixie_amd64.deb libhidapi-hotplug-libusb0_0.15.0-1_trixie_amd64.deb libhidapi-hotplug-dev_0.15.0-1_trixie_amd64.deb"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -x "$BR/bin/bash" ] || { echo "E: buildroot 없음 — 먼저 scripts/build-hypr.sh"; exit 1; }

cleanup() {
    for m in run dev/pts dev sys proc; do
        mountpoint -q "$BR/$m" && umount -l "$BR/$m" 2>/dev/null
    done
    return 0
}
trap cleanup EXIT
mountpoint -q "$BR/proc"    || mount -t proc  proc  "$BR/proc"
mountpoint -q "$BR/sys"     || mount -t sysfs sysfs "$BR/sys"
mountpoint -q "$BR/dev"     || mount --bind /dev     "$BR/dev"
mountpoint -q "$BR/dev/pts" || mount --bind /dev/pts "$BR/dev/pts"
mountpoint -q "$BR/run"     || mount -t tmpfs tmpfs "$BR/run"
cp -L /etc/resolv.conf "$BR/etc/resolv.conf"

say "포크 소스 ($OPENRGB_REF)"
rm -rf "$BR/build/openrgb" && mkdir -p "$BR/build/openrgb/hidapi"
git -C "$FORK" archive --prefix=openrgb/ "$OPENRGB_REF" | tar -x -C "$BR/build/openrgb"
say "hidapi-hotplug .deb"
for d in $HIDAPI_DEBS; do curl -fsSL -o "$BR/build/openrgb/hidapi/$d" "$HIDAPI_URL/$d"; done

chroot "$BR" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 SUFFIX="$SUFFIX" ver="$OPENRGB_VER" /bin/bash -euo pipefail -c '
    apt-get update -qq
    apt-get install -y -qq devscripts dpkg-dev debhelper pkg-config qt6-base-dev qt6-base-dev-tools \
        qt6-tools-dev-tools qmake6 libusb-1.0-0-dev libmbedtls-dev >/dev/null
    apt-get install -y -qq /build/openrgb/hidapi/*.deb >/dev/null
    cd /build/openrgb/openrgb
    sed -e "s/__VERSION__/${ver}${SUFFIX}/" debian/changelog.in > debian/changelog
    DEBFULLNAME="SekaiOS" DEBEMAIL="sekai@localhost" dch -b -v "${ver}${SUFFIX}" -D trixie \
        "SekaiOS fork: serialize ENE register access, queue CLI per-LED colors on the device thread"
    dpkg-buildpackage -b -uc -us -j"$(nproc)" >/build/openrgb/build.log 2>&1 || { tail -40 /build/openrgb/build.log; exit 1; }
    ls /build/openrgb/*.deb
'
mkdir -p "$PKGDIR"
rm -f "$PKGDIR"/openrgb_*.deb "$PKGDIR"/libhidapi-hotplug-*.deb
for d in "$BR"/build/openrgb/*.deb "$BR"/build/openrgb/hidapi/libhidapi-hotplug-hidraw0_*.deb "$BR"/build/openrgb/hidapi/libhidapi-hotplug-libusb0_*.deb; do
    case "$(basename "$d")" in *-dbgsym_*) continue ;; esac
    cp "$d" "$PKGDIR/"
    chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$PKGDIR/$(basename "$d")"
done
# GPL 소스 — 고친 OpenRGB 와 그대로 실은 hidapi-hotplug 의 소스를 apt 저장소 sources/ 에 같이 (build-repo.sh)
mkdir -p "$PKGDIR/sources"
rm -f "$PKGDIR"/sources/openrgb-*.tar.xz "$PKGDIR"/sources/hidapi-hotplug-*.tar.gz
git -C "$FORK" archive --prefix="openrgb-${OPENRGB_VER}${SUFFIX}/" "$OPENRGB_REF" | xz -9 > "$PKGDIR/sources/openrgb-${OPENRGB_VER}${SUFFIX}.tar.xz"
curl -fsSL -o "$PKGDIR/sources/hidapi-hotplug-0.15.0.tar.gz" \
    "https://codeberg.org/OpenRGB/hidapi-hotplug/archive/hidapi-hotplug-0.15.0.tar.gz"
chown -R "${SUDO_UID:-0}:${SUDO_GID:-0}" "$PKGDIR/sources"
say "끝: $(ls "$PKGDIR" | grep -E '^(openrgb|libhidapi-hotplug)' | tr '\n' ' ')"
