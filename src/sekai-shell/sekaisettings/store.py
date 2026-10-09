"""옛 경로 — 설정 저장소는 sekaishell.store 로 옮겼다 (작업 표시줄·빠른 설정·첫 부팅도 쓰는 공용이라).
저장소 밖의 스크립트(python3 -c 'from sekaisettings.store import Store …')가 깨지지 않게 남겨 둔다."""
from sekaishell.store import CFG_FILE, DEFAULTS, Store, display_key, read

__all__ = ["CFG_FILE", "DEFAULTS", "Store", "display_key", "read"]
