"""SekaiDE 가 SekaiOS(sekaios-base) 위에서 도는지 — 배포판 부품에 기대는 기능을 숨길 때 쓴다.

SekaiDE(sekai-de · sekai-shell)는 데비안 13 에 따로 깔 수도 있다. 그때는 SekaiOS 의 배포판 도우미
(그래픽 드라이버 설치 sekai-gpu, SekaiOS 업데이트 sekai-update)가 없으므로 그 설정 페이지를 숨긴다.
GTK 를 가져오지 않는다 (시작 메뉴 검색도 쓴다).
"""
import os

# 설정 페이지 id → 있어야 하는 배포판 도우미 (sekaios-base 가 싣는다)
PAGE_NEEDS = {
    "graphics": "/usr/libexec/sekai/sekai-gpu",
    "update": "/usr/libexec/sekai/sekai-update",
    "recovery": "/usr/libexec/sekai/sekai-restore",
}


def page_available(page_id):
    need = PAGE_NEEDS.get(page_id)
    return need is None or os.path.exists(need)
