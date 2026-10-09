"""점검 결과 한 건"""
import hashlib

LEVELS = ("error", "warn", "info")
LEVEL_KO = {"error": "오류", "warn": "경고", "info": "참고"}


class Finding:
    def __init__(self, check, level, kind, msg, path=None, line=None, key=None):
        assert level in LEVELS
        self.check, self.level, self.kind, self.msg = check, level, kind, msg
        self.path, self.line = path, line
        # 기준선 비교용 — 줄 번호가 바뀌어도 같은 문제는 같은 키 (key 를 주면 그것으로)
        self.key = key or hashlib.sha1(f"{check}|{kind}|{path}|{msg}".encode()).hexdigest()[:12]

    def where(self):
        return f"{self.path}:{self.line}" if self.path and self.line else (self.path or "")

    def as_dict(self):
        return {"check": self.check, "level": self.level, "kind": self.kind, "msg": self.msg,
                "path": self.path, "line": self.line, "key": self.key}
