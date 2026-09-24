#!/usr/bin/env python3
"""hyprbars 패치 — 버튼에 마우스를 올렸을 때의 옅은 배경을 버튼 글자색에서 만든다.

patch-hyprbars-hover.py 는 흰색 10% 로 고정했다 → 라이트 모드(밝은 제목줄)에선 안 보인다.
글자색 10% 면 다크(밝은 글자)·라이트(어두운 글자) 모두 알맞게 보인다. 닫기 빨강은 그대로.
patch-hyprbars-hover.py 다음에 적용. 멱등.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK = "SEKAI_THEME"
if MARK in s:
    print("이미 적용됨"); sys.exit(0)
old = 'CHyprColor(0xc4 / 255.0, 0x2b / 255.0, 0x1c / 255.0, 1.0) : CHyprColor(1.0, 1.0, 1.0, 0.10);'
assert old in s, "hover 색 기준점 없음 (patch-hyprbars-hover.py 먼저)"
s = s.replace(old, 'CHyprColor(0xc4 / 255.0, 0x2b / 255.0, 0x1c / 255.0, 1.0) :\n'
              '                                                              CHyprColor(button.fgcol.r, button.fgcol.g, button.fgcol.b, 0.12); // SEKAI_THEME', 1)
open(path, "w", encoding="utf-8").write(s)
print("적용함")
