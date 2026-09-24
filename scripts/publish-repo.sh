#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  SekaiOS apt 저장소 게시 — repo/ 를 GitHub 의 공개 저장소(Pages)로 올린다
#
#    https://github.com/caterpillar321/sekaios-apt
#    → https://caterpillar321.github.io/sekaios-apt/
#
#  매번 커밋 하나로 새로 만들어 강제 푸시한다 (옛 .deb 가 기록에 쌓여
#  저장소가 커지지 않게 — 저장소에는 늘 최신본만 있다).
#
#  사용법:  scripts/build-repo.sh && scripts/publish-repo.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
P="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${SEKAI_APT_REMOTE:-https://github.com/caterpillar321/sekaios-apt.git}"
URL="${SEKAI_APT_URL:-https://caterpillar321.github.io/sekaios-apt/}"
SITE="$P/build/apt-site"
KEY="$P/src/sekai-desktop/usr/share/keyrings/sekaios-archive-keyring.gpg"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }

[ -f "$P/repo/dists/hatsune/InRelease" ] || { echo "E: repo/ 가 없습니다 — scripts/build-repo.sh 먼저"; exit 1; }

# 올라가면 안 되는 것이 섞였는지 마지막으로 확인
if find "$P/repo" -type f \( -name '*.key' -o -name '*secring*' -o -name 'private-keys*' \) | grep -q .; then
    echo "E: repo/ 에 비밀 키로 보이는 파일이 있습니다"; exit 1
fi

say "게시할 파일 준비 → $SITE"
rm -rf "$SITE"; mkdir -p "$SITE"
cp -a "$P/repo/." "$SITE/"
cp "$KEY" "$SITE/sekaios-archive-keyring.gpg"
touch "$SITE/.nojekyll"             # GitHub Pages 가 파일을 가공하지 않고 그대로 내보내게

FPR=$(gpg --show-keys --with-colons "$KEY" 2>/dev/null | awk -F: '/^fpr/{print $10; exit}')
PKGS=$(grep -c '^Package:' "$SITE/dists/hatsune/main/binary-amd64/Packages")
cat > "$SITE/index.html" <<EOF
<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SekaiOS 업데이트 저장소</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 16px;
       background:#151517;color:#f1f1f3;line-height:1.6}
  code,pre{background:#1e1e22;border-radius:6px;padding:2px 6px}
  pre{padding:12px;overflow-x:auto} a{color:#39c5bb} h1{font-weight:700}
</style></head><body>
<h1>SekaiOS 업데이트 저장소</h1>
<p>SekaiOS(데비안 13 기반) 설치본이 업데이트를 받는 apt 저장소입니다.
SekaiOS 에는 이미 설정되어 있어 따로 할 일이 없습니다.</p>
<p>패키지 ${PKGS}개 · 모두 SekaiOS 서명 키로 서명됨</p>
<pre>Types: deb
URIs: ${URL}
Suites: hatsune
Components: main
Signed-By: /usr/share/keyrings/sekaios-archive-keyring.gpg</pre>
<p>서명 키: <a href="sekaios-archive-keyring.gpg">sekaios-archive-keyring.gpg</a><br>
지문: <code>${FPR}</code></p>
</body></html>
EOF

say "커밋 하나로 강제 푸시 → $REMOTE"
cd "$SITE"
git init -q -b main
git config user.name "$(git -C "$P" config user.name)"
git config user.email "$(git -C "$P" config user.email)"
git add -A
git commit -q -m "SekaiOS 저장소 $(date +%Y-%m-%d\ %H:%M) — 패키지 ${PKGS}개"
git push -q --force "$REMOTE" main
say "완료 — 몇 분 뒤 $URL 에 반영됩니다"
