#!/usr/bin/env python3
"""CSS 의 글자 크기 px → pt (1px = 0.75pt — 96 dpi 에서 크기는 똑같다).

GTK 는 px 글자 크기를 그대로 두고 pt 만 시스템의 글자 배율(GSettings text-scaling-factor)을 따라 키운다.
설정 › 접근성 › 텍스트 크기가 SekaiOS 앱·테마 글자에도 듣게 하려고 font-size 를 pt 로 쓴다.
    scripts/px2pt.py 파일…   (그 자리에서 고친다 — CSS 파일이든 CSS 를 품은 파이썬이든)
build-theme.sh 도 만든 테마 CSS 에 이것을 돌린다.
"""
import re
import sys

PAT = re.compile(r"(font-size:\s*)(\d+(?:\.\d+)?)px")


def conv(m):
    pt = float(m.group(2)) * 0.75
    s = f"{pt:.2f}".rstrip("0").rstrip(".")
    return f"{m.group(1)}{s}pt"


n_total = 0
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new, n = PAT.subn(conv, text)
    if n:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        n_total += n
        print(f"  {path}: {n}")
print(f"합계 {n_total}")
