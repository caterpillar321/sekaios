#!/bin/bash
# overlay/ 의 내용을 rootfs/ 에 복사하고 소유권을 root:root 로 정규화
#
# 지난번에 넣었는데 이번 overlay 에는 없는 파일은 rootfs 에서 지운다.
#   전에는 추가만 해서, overlay 에서 뺀 파일이 rootfs 에 영원히 남았다
#   (예: /usr/local/bin/sekai-install-chrome 이 패키지의 /usr/bin 판을 가림).
# 단, 그 사이 패키지가 소유하게 된 파일은 건드리지 않는다.
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
R="$P/rootfs"
MANIFEST="$P/build/overlay.manifest"     # 이미지 밖에 둔다 (빌드 메타데이터)

[ "$(id -u)" -eq 0 ] || { echo "E: sudo 로 실행하세요"; exit 1; }
[ -d "$P/overlay" ]  || { echo "E: overlay 없음"; exit 1; }

# 개발 빌드에만 넣는 파일 (local/overlay-dev: 개발용 SSH 키 등, git 에 없음)
#   배포용으로 만들 땐 SEKAI_RELEASE=1 → 넣지 않고, 지난번에 넣은 것도 지운다
SRCS=("$P/overlay")
if [ "${SEKAI_RELEASE:-0}" != 1 ] && [ -d "$P/local/overlay-dev" ]; then
    SRCS+=("$P/local/overlay-dev")
    echo "==> 개발 빌드: local/overlay-dev 포함 (배포용은 SEKAI_RELEASE=1)"
fi

NEW="$(mktemp)"; trap 'rm -f "$NEW"' EXIT
declare -A FROM                          # 파일 → 원본 폴더 (뒤의 것이 앞의 것을 덮는다)
for src in "${SRCS[@]}"; do
    while read -r f; do FROM["$f"]="$src"; done \
        < <(cd "$src" && find . -type f -o -type l | sed 's|^\./||')
done
printf '%s\n' "${!FROM[@]}" | LC_ALL=C sort > "$NEW"

echo "==> overlay → rootfs 동기화"
while read -r f; do
    install -D -m "$(stat -c%a "${FROM[$f]}/$f")" -o root -g root \
            "${FROM[$f]}/$f" "$R/$f"
    echo "    /$f"
done < "$NEW"

if [ -f "$MANIFEST" ]; then
    LC_ALL=C comm -23 <(LC_ALL=C sort "$MANIFEST") "$NEW" | while read -r f; do
        [ -e "$R/$f" ] || continue
        if dpkg --admindir="$R/var/lib/dpkg" -S "/$f" >/dev/null 2>&1; then
            echo "    (패키지 소유로 넘어감) /$f"
        else
            rm -f "$R/$f"
            echo "    삭제: /$f"
        fi
    done
fi

install -D -m644 "$NEW" "$MANIFEST"
echo "==> 완료"
