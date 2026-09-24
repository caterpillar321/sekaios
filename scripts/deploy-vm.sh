#!/bin/bash
# SekaiOS — 새로 만든 sekai-* 패키지를 개발 VM 에 올리고 설치 결과를 '검증'한다.
#   전에는 apt 출력에서 "upgraded" 한 줄만 보다가 파일 충돌로 설치가 실패한 걸 놓쳤다.
#   이제는 apt 종료 코드 + dpkg --audit + 설치된 버전을 모두 확인한다.
#
# 사용법: deploy-vm.sh [--restart]    (--restart: Hyprland 세션 재시작)
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; P="$(dirname "$SELF")"
# VM 주소·비밀번호는 저장소 밖 local/env 에 둔다 (git 에 올리지 않음)
[ -f "$P/local/env" ] && . "$P/local/env"
VM="${SEKAI_VM:?local/env 에 SEKAI_VM=사용자@주소 를 적으세요}"
PW="${SEKAI_VM_PW:?local/env 에 SEKAI_VM_PW=비밀번호 를 적으세요}"

debs=( "$P"/packages/sekai-shell_*.deb "$P"/packages/sekai-desktop_*.deb "$P"/packages/hyprbars_*.deb )
want=$(basename "${debs[0]}" | sed -E 's/^sekai-shell_(.*)_all\.deb$/\1/')

ssh -o BatchMode=yes "$VM" 'rm -rf /tmp/sekai-deploy && mkdir /tmp/sekai-deploy'
scp -q "${debs[@]}" "$VM":/tmp/sekai-deploy/

ssh -o BatchMode=yes "$VM" "PW='$PW' WANT='$want' RESTART='${1:-}' bash -s" <<'REMOTE'
set -u
S() { echo "$PW" | sudo -S -p '' "$@"; }
S env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confnew /tmp/sekai-deploy/*.deb > /tmp/sekai-deploy/apt.log 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    # 이전 실패로 '반쯤 설치된' 상태면 apt 가 의존성 핑계로 거부한다 → dpkg 로 함께 풀고 마무리
    S env DEBIAN_FRONTEND=noninteractive dpkg -i --force-confnew /tmp/sekai-deploy/*.deb >> /tmp/sekai-deploy/apt.log 2>&1
    S env DEBIAN_FRONTEND=noninteractive apt-get -f install -y >> /tmp/sekai-deploy/apt.log 2>&1
    rc=$?
fi
audit=$(S dpkg --audit 2>&1)
vs=$(dpkg-query -W -f='${Version}' sekai-shell)
vd=$(dpkg-query -W -f='${Version}' sekai-desktop)
if [ $rc -ne 0 ] || [ -n "$audit" ] || [ "$vs" != "$WANT" ] || [ "$vd" != "$WANT" ]; then
    echo "✗ 설치 실패 (apt=$rc, shell=$vs, desktop=$vd, 목표=$WANT)"
    grep -E "^E:|dpkg: error|trying to overwrite" /tmp/sekai-deploy/apt.log | head -8
    [ -n "$audit" ] && echo "$audit" | head -8
    exit 1
fi
echo "✓ 설치 확인: sekai-shell / sekai-desktop $WANT"
if [ "$RESTART" = "--restart" ]; then
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    export HYPRLAND_INSTANCE_SIGNATURE=$(ls -t $XDG_RUNTIME_DIR/hypr/ | head -1)
    hyprctl dispatch exit >/dev/null 2>&1 && echo "  세션 재시작"
fi
REMOTE
