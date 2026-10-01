#!/bin/bash
# SekaiOS — rootfs 를 데비안 저장소에서 처음부터 만든다 (mmdebstrap)
#   sudo scripts/mkrootfs.sh [대상 폴더]     기본: rootfs.new (지금 쓰는 rootfs/ 는 건드리지 않는다)
#   sudo scripts/mkrootfs.sh --replace       rootfs.new → rootfs 로 바꾼다 (옛것은 rootfs.old)
#
#   여기서 만드는 것은 데비안 부분과 이미지 기본 설정(로케일·시간대·apt 소스·root 잠금)뿐이다.
#   SekaiOS 패키지는 다음 단계 finalize.sh 가 packages/*.deb 로 설치한다:
#     sudo scripts/mkrootfs.sh && sudo scripts/mkrootfs.sh --replace && sudo scripts/finalize.sh
#   패키지 목록: config/rootfs-packages.list   이미지에 그대로 넣는 파일: overlay/
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
MIRROR="http://mirror.kakao.com/debian"
SUITE=trixie
LIST="$P/config/rootfs-packages.list"

C_B=$'\033[1;36m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }

# ── mmdebstrap 의 customize-hook 으로 다시 불릴 때: 마운트가 된 chroot 안을 꾸민다 ──
if [ "${1:-}" = --customize ]; then
    R="$2"
    inroot() { chroot "$R" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
                   DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

    say "apt 소스 (deb822 — 데비안 + 보안 업데이트)"
    rm -f "$R/etc/apt/sources.list"
    install -D -m644 "$P/overlay/etc/apt/sources.list.d/debian.sources" "$R/etc/apt/sources.list.d/debian.sources"
    inroot apt-get update -qq

    say "패키지 설치 (권장 패키지 포함 — 설치된 SekaiOS 와 같은 규칙)"
    #   mmdebstrap 은 도는 동안 권장 패키지를 끈 apt 설정을 넣어 두므로 여기서 분명히 켠다.
    #   (빠뜨리면 upower·rtkit·systemd-timesyncd·user-setup·zstd·alsa-ucm-conf 같은 것이 빠진다)
    WORDS=$(sed 's/#.*//' "$LIST" | xargs -n1)
    PKGS=$(grep -v '!$' <<<"$WORDS" | xargs)
    NOREC=$(grep '!$' <<<"$WORDS" | tr -d '!' | xargs)
    inroot apt-get install -y -o APT::Install-Recommends=true -o Dpkg::Options::=--force-confnew $PKGS
    #   이름 뒤 ! 인 것은 권장 패키지 없이 (목록 머리말 참고)
    [ -z "$NOREC" ] || inroot apt-get install -y --no-install-recommends -o Dpkg::Options::=--force-confnew $NOREC

    say "로케일 · 시간대 · 이름"
    printf 'en_US.UTF-8 UTF-8\nko_KR.UTF-8 UTF-8\n' > "$R/etc/locale.gen"
    inroot locale-gen >/dev/null
    echo 'LANG=en_US.UTF-8' > "$R/etc/default/locale"
    echo 'LANG=en_US.UTF-8' > "$R/etc/locale.conf"
    ln -sf /usr/share/zoneinfo/Asia/Seoul "$R/etc/localtime"
    echo Asia/Seoul > "$R/etc/timezone"
    echo sekai > "$R/etc/hostname"
    printf '127.0.0.1\tlocalhost\n127.0.1.1\tsekai\n::1\t\tlocalhost ip6-localhost ip6-loopback\nff02::1\t\tip6-allnodes\nff02::2\t\tip6-allrouters\n' \
        > "$R/etc/hosts"

    say "root 잠금 (root 로는 로그인하지 않는다 — 관리는 sudo 로)"
    inroot usermod -p '*' root

    say "정리"
    inroot apt-get clean
    exit 0
fi

# ── rootfs.new → rootfs 바꾸기 ──
if [ "${1:-}" = --replace ]; then
    [ -x "$P/rootfs.new/bin/bash" ] || { echo "E: rootfs.new 가 없습니다 — 먼저 만드세요"; exit 1; }
    if mount | grep -q " $P/rootfs/"; then echo "E: rootfs 에 마운트가 남아 있습니다 (exit-chroot.sh)"; exit 1; fi
    [ -e "$P/rootfs.old" ] && { echo "E: rootfs.old 가 이미 있습니다 — 확인하고 지운 뒤 다시"; exit 1; }
    [ -d "$P/rootfs" ] && mv "$P/rootfs" "$P/rootfs.old"
    mv "$P/rootfs.new" "$P/rootfs"
    say "바꿈: rootfs.new → rootfs (옛것은 rootfs.old)"
    exit 0
fi

OUT="${1:-$P/rootfs.new}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
[ "$OUT" = "$P/rootfs" ] && { echo "E: 지금 쓰는 rootfs/ 에 바로 만들지 않습니다 — 기본값(rootfs.new) 뒤 --replace"; exit 1; }
[ -e "$OUT" ] && { echo "E: $OUT 이(가) 이미 있습니다 — 지우고 다시"; exit 1; }
command -v mmdebstrap >/dev/null || { echo "E: mmdebstrap 이 없습니다 (apt install mmdebstrap)"; exit 1; }
[ -f /usr/share/keyrings/debian-archive-trixie-stable.gpg ] || [ -f /usr/share/keyrings/debian-archive-keyring.gpg ] \
    || { echo "E: 데비안 키링이 없습니다 (debian-archive-keyring — trixie 키가 든 판)"; exit 1; }

# debconf 미리 답하기 — 설치 중에 묻지 않게, 그리고 옛 rootfs 와 같은 값
PRESEED='
localepurge localepurge/nopurge multiselect en, en_US, en_US.UTF-8, ko, ko_KR, ko_KR.UTF-8, C.UTF-8
localepurge localepurge/use-dpkg-feature boolean true
localepurge localepurge/mandelete boolean true
localepurge localepurge/dontbothernew boolean false
localepurge localepurge/showfreedspace boolean true
localepurge localepurge/quickndirtycalc boolean true
localepurge localepurge/verbose boolean false
tzdata tzdata/Areas select Asia
tzdata tzdata/Zones/Asia select Seoul
keyboard-configuration keyboard-configuration/layoutcode string us
keyboard-configuration keyboard-configuration/modelcode string pc105
keyboard-configuration keyboard-configuration/xkb-keymap select us
locales locales/default_environment_locale select None
'

say "rootfs 만들기 ($SUITE) → $OUT — 수 분 걸린다"
mkdir -p "$P/cache"
mmdebstrap --mode=root --variant=minbase --architectures=amd64 \
    --components="main contrib non-free non-free-firmware" \
    --include=debconf-utils \
    --essential-hook='printf "%s" "'"$PRESEED"'" | chroot "$1" debconf-set-selections' \
    --customize-hook='"'"$SELF/mkrootfs.sh"'" --customize "$1"' \
    "$SUITE" "$OUT" "$MIRROR"

say "완료: $OUT ($(du -sh "$OUT" | cut -f1))"
echo "    다음: sudo scripts/mkrootfs.sh --replace && sudo scripts/finalize.sh"
