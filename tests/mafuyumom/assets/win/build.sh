#!/bin/bash
# 시험용 Windows 프로그램 빌드 — buildroot-wine(trixie, mingw-w64 있음)이 있으면 그 안에서, 없으면 이 PC 의 mingw 로
#   bash tests/mafuyumom/assets/win/build.sh   → bin/mmhello.exe · bin/mmsetup.exe
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$(cd "$HERE/../../../.." && pwd)"
BR="$P/buildroot-wine"
mkdir -p "$HERE/bin"
# 아이콘 — 시작 메뉴 아이콘을 exe 에서 꺼내는지 보려고 (청록 바탕에 흰 M)
python3 - "$HERE/mmhello.ico" <<'PY'
import sys
from PIL import Image, ImageDraw
im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((2, 2, 61, 61), 12, fill=(19, 160, 150, 255))
d.polygon([(14, 50), (14, 14), (32, 34), (50, 14), (50, 50), (42, 50), (42, 30), (32, 42), (22, 30), (22, 50)], fill="white")
im.save(sys.argv[1], sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
PY
CFLAGS="-municode -mwindows -O2 -s"
if command -v x86_64-w64-mingw32-gcc >/dev/null; then
    x86_64-w64-mingw32-windres "$HERE/mmhello.rc" -O coff -o "$HERE/bin/mmhello.res"
    x86_64-w64-mingw32-gcc $CFLAGS -o "$HERE/bin/mmhello.exe" "$HERE/mmhello.c" "$HERE/bin/mmhello.res" -lgdi32
    x86_64-w64-mingw32-gcc $CFLAGS -o "$HERE/bin/mmsetup.exe" "$HERE/mmsetup.c" -lole32 -luuid -lshell32
elif ls "$BR"/usr/bin/x86_64-w64-mingw32-gcc-* >/dev/null 2>&1; then         # alternatives 링크라 밖에서는 깨져 보인다
    T="$BR/tmp/mmwin"; sudo mkdir -p "$T"; sudo cp "$HERE"/*.c "$HERE"/*.rc "$HERE"/*.ico "$T/"
    sudo chroot "$BR" /bin/sh -c "cd /tmp/mmwin && x86_64-w64-mingw32-windres mmhello.rc -O coff -o mmhello.res && \
        x86_64-w64-mingw32-gcc $CFLAGS -o mmhello.exe mmhello.c mmhello.res -lgdi32 && \
        x86_64-w64-mingw32-gcc $CFLAGS -o mmsetup.exe mmsetup.c -lole32 -luuid -lshell32"
    sudo cp "$T/mmhello.exe" "$T/mmsetup.exe" "$HERE/bin/"; sudo chown "$(id -u):$(id -g)" "$HERE"/bin/*.exe
else
    echo "E: mingw-w64 가 없습니다 (sudo scripts/build-wine.sh 로 buildroot-wine 을 먼저)"; exit 1
fi
ls -l "$HERE/bin"
