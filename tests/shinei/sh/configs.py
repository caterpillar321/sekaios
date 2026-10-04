"""화면 환경 — 테마 × 텍스트 크기 × 해상도·배율. 기본은 윈도우 사용자가 실제로 겪는 조합 몇 개 (전부 곱하면 너무 많다).

해상도를 낮추는 환경(노트북)은 맨 뒤에 — VM 의 virtio-gpu 는 한 번 낮춘 뒤 1920×1080 으로 되돌리면 커널이
모드를 거절한다(atomic test EINVAL). 그래서 한 판에서 높은 해상도 → 낮은 해상도 순으로만 간다."""

CONFIGS = [
    # id, 설명, 모드, 대비, 텍스트 크기, 해상도, 배율
    {"id": "dark",       "desc": "다크 · 100% · 1920×1080",          "mode": "dark",  "contrast": False, "text": 1.0, "res": (1920, 1080), "scale": 1.0},
    {"id": "light",      "desc": "라이트 · 100% · 1920×1080",        "mode": "light", "contrast": False, "text": 1.0, "res": (1920, 1080), "scale": 1.0},
    {"id": "contrast",   "desc": "대비 테마(야간 하늘) · 100%",       "mode": "dark",  "contrast": True,  "text": 1.0, "res": (1920, 1080), "scale": 1.0},
    {"id": "text150",    "desc": "다크 · 텍스트 150% · 1920×1080",   "mode": "dark",  "contrast": False, "text": 1.5, "res": (1920, 1080), "scale": 1.0},
    {"id": "hidpi125",   "desc": "다크 · 100% · 1920×1080 배율 125%", "mode": "dark",  "contrast": False, "text": 1.0, "res": (1920, 1080), "scale": 1.25},
    {"id": "laptop",     "desc": "다크 · 100% · 1366×768 (노트북)",  "mode": "dark",  "contrast": False, "text": 1.0, "res": (1366, 768),  "scale": 1.0},
    {"id": "laptop200",  "desc": "다크 · 텍스트 200% · 1366×768",    "mode": "dark",  "contrast": False, "text": 2.0, "res": (1366, 768),  "scale": 1.0},
]
DEFAULT = CONFIGS[0]


def by_id(ids):
    if not ids:
        return CONFIGS
    sel = [c for c in CONFIGS if c["id"] in ids]
    missing = set(ids) - {c["id"] for c in sel}
    if missing:
        raise SystemExit(f"모르는 환경: {', '.join(sorted(missing))} (있는 것: {', '.join(c['id'] for c in CONFIGS)})")
    return sel
