#!/bin/bash
# SekaiOS — Plymouth 를 데비안 소스로 다시 빌드 (패치 하나: 커널이 잡아 둔 화면 모드를 그대로 쓴다)
#   sudo ~/MyOS/scripts/build-plymouth.sh      (buildroot 가 있어야 한다 — scripts/build-hypr.sh)
#
# 왜: Plymouth 24 의 DRM 렌더러는 커널(fbdev, 커널 옵션 video=)이 이미 켜 둔 모드가 있어도
#   모니터의 "권장 모드"(EDID preferred)를 먼저 골라 다시 잡는다. 권장 모드가 바탕화면 모드(165Hz 등)와
#   다르면 부팅 화면이 뜰 때 한 번, 로그인 화면이 뜰 때 또 한 번 모니터 신호가 끊겼다.
#   패치: 켜져 있는 모드가 있으면 그것을 먼저 (없을 때만 권장 모드). 데비안 패키징은 그대로.
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
BR="$P/buildroot"
PKGDIR="$P/packages"
SUFFIX="+sekai1"

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
install -m644 "$SELF/hypr/patch-plymouth-activemode.py" "$BR/build/patch-plymouth-activemode.py"

chroot "$BR" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 SUFFIX="$SUFFIX" /bin/bash -euo pipefail -c '
    echo "deb-src http://mirror.kakao.com/debian trixie main" > /etc/apt/sources.list.d/sekai-src.list
    apt-get update -qq
    apt-get install -y -qq devscripts dpkg-dev >/dev/null
    apt-get build-dep -y -qq plymouth >/dev/null
    rm -rf /build/plymouth && mkdir -p /build/plymouth && cd /build/plymouth
    apt-get source -qq plymouth >/dev/null 2>&1
    cd plymouth-*/
    python3 /build/patch-plymouth-activemode.py src/plugins/renderers/drm/plugin.c
    ver=$(dpkg-parsechangelog -S Version)
    DEBFULLNAME="SekaiOS" DEBEMAIL="sekai@localhost" dch -b -v "${ver}${SUFFIX}" -D trixie \
        "SekaiOS: DRM 렌더러가 커널이 켜 둔 화면 모드를 먼저 쓴다 (부팅 때 모니터 신호가 끊기지 않게)"
    dpkg-buildpackage -b -uc -us -j"$(nproc)" >/build/plymouth/build.log 2>&1 || { tail -30 /build/plymouth/build.log; exit 1; }
    ls /build/plymouth/*.deb
'
mkdir -p "$PKGDIR"
rm -f "$PKGDIR"/plymouth*_*.deb "$PKGDIR"/libplymouth*_*.deb
for d in "$BR"/build/plymouth/*.deb; do
    case "$(basename "$d")" in *-dbgsym_*|*-dev_*) continue ;; esac
    cp "$d" "$PKGDIR/"
    chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$PKGDIR/$(basename "$d")"
done
say "완료:"; ls -1 "$PKGDIR" | grep -i plymouth
