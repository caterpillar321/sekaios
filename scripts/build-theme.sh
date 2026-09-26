#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  SekaiOS — GTK 테마 Sekai-Light · Sekai-Dark 만들기
#
#  원본: third_party/fluent-gtk-theme  (Fluent-gtk-theme by vinceliuice, GPL-3.0
#        — 우리가 고친 파일은 머리에 "Modified by SekaiOS" 표시. 자세한 것은 그 폴더의 UPSTREAM.md)
#  결과: src/sekai-desktop/usr/share/themes/Sekai-{Light,Dark}/gtk-{3.0,4.0}/
#        만든 CSS·그림도 저장소에 넣는다 — 패키지를 만들 때 sassc 가 필요 없게.
#        원본(SCSS·assets.svg)을 고치면 이 스크립트를 다시 돌려 결과를 함께 커밋한다.
#  필요: sassc, rsvg-convert (librsvg2-bin), optipng (있으면 PNG 를 줄인다)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
SRC="$P/third_party/fluent-gtk-theme/src"
OUT="$P/src/sekai-desktop/usr/share/themes"
# 그림(체크 상자·입력 칸 테두리 …)에 칠할 강조색 — _sass/_colors.scss 의 $sekai-accent 와 같게
ACCENT="#3CC8BE"

for t in sassc rsvg-convert; do
    command -v "$t" >/dev/null || { echo "E: $t 가 필요합니다 (sudo apt install sassc librsvg2-bin)"; exit 1; }
done
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -r "$SRC" "$TMP/src"
# 원본 install.sh 와 같은 방식 — 옵션 파일을 _tweaks-temp 로 복사해 읽힌다
cp "$TMP/src/_sass/_tweaks.scss" "$TMP/src/_sass/_tweaks-temp.scss"

say "그림 — assets.svg 의 파랑을 강조색($ACCENT)으로 바꿔 PNG 로 (1x · 2x)"
sed -e "s/#1A73E8/$ACCENT/gI" -e "s/#3281EA/$ACCENT/gI" "$TMP/src/gtk/assets.svg" > "$TMP/assets.svg"
mkdir -p "$TMP/assets"
while read -r id; do
    [ -n "$id" ] || continue
    rsvg-convert -i "$id" -o "$TMP/assets/$id.png" "$TMP/assets.svg"
    rsvg-convert -i "$id" -z 2 -o "$TMP/assets/$id@2.png" "$TMP/assets.svg"
done < "$TMP/src/gtk/assets.txt"
if command -v optipng >/dev/null; then
    optipng -quiet -o2 "$TMP"/assets/*.png
fi
cp -r "$TMP/src/gtk/scalable" "$TMP/assets/scalable"

# 컴파일하고 맨 위에 출처·라이선스를 적는다 (SCSS 의 // 주석은 CSS 에 남지 않는다)
css() {
    { printf '/* %s — SekaiOS GTK 테마. GPL-3.0\n' "$3"
      printf ' * Fluent-gtk-theme (https://github.com/vinceliuice/Fluent-gtk-theme, vinceliuice 와 기여자들,\n'
      printf ' * Materia theme 바탕) 을 SekaiOS 가 고쳐 만든 파일 — 원본 SCSS 와 수정 내역:\n'
      printf ' * https://github.com/caterpillar321/sekaios (third_party/fluent-gtk-theme, scripts/build-theme.sh) */\n\n'
      sassc -M -t expanded "$1"; } > "$2"
}

for variant in Light Dark; do
    name="Sekai-$variant"
    d="$OUT/$name"
    say "$name"
    for g in 3.0 4.0; do
        rm -rf "$d/gtk-$g"
        mkdir -p "$d/gtk-$g"
        css "$TMP/src/gtk/$g/gtk-$variant.scss" "$d/gtk-$g/gtk.css" "$name"
        # 라이트 테마에서 "다크 선호"를 청한 앱(셸 프로그램 등)은 gtk-dark.css 를 읽는다
        if [ "$variant" = Light ]; then
            css "$TMP/src/gtk/$g/gtk-Dark.scss" "$d/gtk-$g/gtk-dark.css" "$name (다크)"
        fi
        cp -r "$TMP/assets" "$d/gtk-$g/assets"
    done
    icons=$([ "$variant" = Dark ] && echo Papirus-Dark || echo Papirus-Light)
    cat > "$d/index.theme" <<EOF
[Desktop Entry]
Type=X-GNOME-Metatheme
Name=$name
Comment=SekaiOS $variant — Fluent-gtk-theme (vinceliuice, GPL-3.0) 바탕
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=$name
IconTheme=$icons
EOF
    du -sh "$d/gtk-3.0" "$d/gtk-4.0" | sed 's/^/    /'
done
say "완료 — 결과를 확인하고 third_party 와 함께 커밋하세요"
