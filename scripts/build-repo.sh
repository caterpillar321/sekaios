#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  SekaiOS apt 저장소 만들기 — packages/*.deb → repo/ (서명까지)
#
#  결과 repo/ 폴더를 그대로 웹 서버(정적 호스팅)에 올리면 된다.
#    repo/dists/hatsune/{InRelease,Release,Release.gpg}
#    repo/dists/hatsune/main/binary-amd64/Packages(.gz)
#    repo/pool/main/*.deb
#
#  서명 키: ~/.sekai-signing/gnupg (없으면 새로 만든다)
#    프로젝트 폴더 밖에 둔다 — ISO·저장소에 비밀 키가 섞여 나가지 않게.
#    공개 키는 sekai-desktop 패키지에 들어간다:
#      src/sekai-desktop/usr/share/keyrings/sekaios-archive-keyring.gpg
#    키를 새로 만들면 sekai-desktop 을 다시 빌드·배포해야 기존 설치가 새 서명을 믿는다.
#
#  사용법:  scripts/build-repo.sh            저장소 만들기
#           scripts/build-repo.sh --serve    만든 뒤 http://127.0.0.1:8765 로 띄우기 (시험용)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
P="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$P/repo"
SUITE=hatsune
export GNUPGHOME="$HOME/.sekai-signing/gnupg"
KEYRING_OUT="$P/src/sekai-desktop/usr/share/keyrings/sekaios-archive-keyring.gpg"
UID_STR="SekaiOS Archive Signing Key <archive@sekaios.invalid>"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# ── 서명 키 ─────────────────────────────────────────────
mkdir -p "$GNUPGHOME"; chmod 700 "$HOME/.sekai-signing" "$GNUPGHOME"
if ! gpg --batch --list-secret-keys "$UID_STR" >/dev/null 2>&1; then
    say "서명 키 만들기 (ed25519, 5년)"
    gpg --batch --pinentry-mode loopback --passphrase '' \
        --quick-generate-key "$UID_STR" ed25519 sign 5y
fi
FPR=$(gpg --batch --with-colons --list-secret-keys "$UID_STR" | awk -F: '/^fpr/{print $10; exit}')
mkdir -p "$(dirname "$KEYRING_OUT")"
gpg --batch --export "$FPR" > "$KEYRING_OUT.new"
if ! cmp -s "$KEYRING_OUT.new" "$KEYRING_OUT" 2>/dev/null; then
    mv "$KEYRING_OUT.new" "$KEYRING_OUT"
    say "공개 키 갱신 → sekai-desktop 을 다시 빌드하세요 (scripts/pack-shell.sh)"
else
    rm -f "$KEYRING_OUT.new"
fi
say "서명 키 $FPR"

# ── 저장소 ──────────────────────────────────────────────
say "저장소 만들기 → $REPO"
rm -rf "$REPO.tmp"
mkdir -p "$REPO.tmp/pool/main" "$REPO.tmp/dists/$SUITE/main/binary-amd64"
# 같은 패키지의 여러 버전이 있으면 가장 높은 것만 싣는다
declare -A best
for deb in "$P"/packages/*.deb; do
    name=$(dpkg-deb -f "$deb" Package); ver=$(dpkg-deb -f "$deb" Version)
    if [ -z "${best[$name]:-}" ] || dpkg --compare-versions "$ver" gt "$(dpkg-deb -f "${best[$name]}" Version)"; then
        best[$name]=$deb
    fi
done
for name in "${!best[@]}"; do cp "${best[$name]}" "$REPO.tmp/pool/main/"; done

cd "$REPO.tmp"
apt-ftparchive packages pool/main > "dists/$SUITE/main/binary-amd64/Packages"
gzip -9kn "dists/$SUITE/main/binary-amd64/Packages"
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=SekaiOS \
    -o APT::FTPArchive::Release::Label=SekaiOS \
    -o APT::FTPArchive::Release::Suite=$SUITE \
    -o APT::FTPArchive::Release::Codename=$SUITE \
    -o APT::FTPArchive::Release::Architectures="amd64 all" \
    -o APT::FTPArchive::Release::Components=main \
    -o APT::FTPArchive::Release::Description="SekaiOS packages" \
    release "dists/$SUITE" > "dists/$SUITE/Release"
gpg --batch --yes --local-user "$FPR" --clearsign -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
gpg --batch --yes --local-user "$FPR" -abs -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"
cd "$P"
rm -rf "$REPO"; mv "$REPO.tmp" "$REPO"

say "패키지 ${#best[@]}개:"
for name in $(printf '%s\n' "${!best[@]}" | sort); do
    printf '    %-22s %s\n' "$name" "$(dpkg-deb -f "${best[$name]}" Version)"
done

if [ "${1:-}" = "--serve" ]; then
    say "http://127.0.0.1:8765/ 에서 제공 (Ctrl+C 로 끝)"
    exec python3 -m http.server 8765 --bind 127.0.0.1 --directory "$REPO"
fi
