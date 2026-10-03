#!/usr/bin/env python3
"""po → mo (GNU gettext 형식) — msgfmt 없이 패키지를 만들 때. 사용법: po2mo.py 입력.po 출력.mo
msgctxt·복수형은 쓰지 않는 간단한 번역 파일만 (SekaiOS 가 직접 쓴 것)."""
import ast
import struct
import sys


def parse(path):
    out, key, cur, mid, mstr = {}, None, None, None, None
    def flush():
        if mid is not None and mstr is not None:
            out[mid] = mstr
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            flush()
            mid, mstr, cur = ast.literal_eval(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            mstr, cur = ast.literal_eval(line[7:]), "str"
        elif line.startswith('"'):
            s = ast.literal_eval(line)
            if cur == "id":
                mid += s
            else:
                mstr += s
    flush()
    return {k: v for k, v in out.items() if v}


def write(msgs, path):
    keys = sorted(msgs)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(msgs[k].encode() + b"\0" for k in keys)
    n = len(keys)
    o_ids, o_strs = 28, 28 + n * 8
    base = 28 + n * 16
    ki, vi, off = [], [], 0
    for k in keys:
        b = k.encode()
        ki.append((len(b), base + off))
        off += len(b) + 1
    soff = 0
    for k in keys:
        b = msgs[k].encode()
        vi.append((len(b), base + len(ids) + soff))
        soff += len(b) + 1
    with open(path, "wb") as f:
        f.write(struct.pack("<7I", 0x950412de, 0, n, o_ids, o_strs, 0, 0))
        for ln, of in ki:
            f.write(struct.pack("<2I", ln, of))
        for ln, of in vi:
            f.write(struct.pack("<2I", ln, of))
        f.write(ids + strs)


if __name__ == "__main__":
    write(parse(sys.argv[1]), sys.argv[2])
