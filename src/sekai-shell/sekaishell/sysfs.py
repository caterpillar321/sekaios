"""/sys · /proc 의 작은 값 읽기 — 배터리·rfkill·블루투스 등 셸 곳곳이 같이 쓴다 (예전엔 모듈마다 따로 만들었다)."""
import os


def read(path, name=None):
    """파일 한 줄(앞뒤 공백 없이) — 없거나 못 읽으면 "". read(d, "type") 처럼 폴더와 이름을 따로 줘도 된다"""
    try:
        with open(os.path.join(path, name) if name else path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def num(path, name=None, default=0.0):
    """숫자 값 — 없거나 숫자가 아니면 default"""
    try:
        return float(read(path, name))
    except ValueError:
        return default
