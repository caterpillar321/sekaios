// SekaiDE — Firefox 도 크롬·파일 탐색기처럼 스스로 그린 제목줄(탭이 제목줄 자리에, 최소화·최대화·닫기 단추)을 쓴다.
//   SekaiDE 는 Firefox 창에 제목 표시줄을 그리지 않는다 (hyprland.conf 의 nobar 규칙) — 그래서 "자동"(2)이 아니라
//   켜기(1)로 못 박는다: 자동이 시스템 제목줄을 고르면 제목줄도 단추도 없는 창이 된다.
//   (예전 합성기(SekaiCompose)는 이 모드의 그림자 자리만큼 내용을 밀어 그려 꺼 두었다 — SEKAI_GEOM_SUBSURFACE 로 고침)
pref("browser.tabs.inTitlebar", 1);
