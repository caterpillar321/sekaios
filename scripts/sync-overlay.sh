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
#   명시적으로 SEKAI_DEV=1 일 때만 넣는다. 기본은 배포용 — 환경 변수를 빠뜨려도
#   (sudo 가 지우는 등) 키가 새지 않는 쪽으로 틀린다.
#   배포용일 땐 지난번에 넣은 것도 지운다 (manifest 가 없어도 overlay-dev 목록으로 지운다).
SRCS=("$P/overlay")
KIND=release
if [ "${SEKAI_DEV:-0}" = 1 ] && [ -d "$P/local/overlay-dev" ]; then
    SRCS+=("$P/local/overlay-dev")
    KIND=dev
    echo "==> 개발 빌드: local/overlay-dev 포함 (SSH 키가 들어갑니다 — 남에게 주지 말 것)"
elif [ -d "$P/local/overlay-dev" ]; then
    (cd "$P/local/overlay-dev" && find . -type f -printf '%P\n') | while read -r f; do
        [ -e "$R/$f" ] || continue
        dpkg --admindir="$R/var/lib/dpkg" -S "/$f" >/dev/null 2>&1 && continue
        rm -f "$R/$f"; echo "    삭제 (개발 전용): /$f"
    done
fi
mkdir -p "$P/build"; echo "$KIND" > "$P/build/overlay.kind"

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
