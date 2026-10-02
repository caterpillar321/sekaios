// SekaiDE — Firefox 도 크롬·파일 탐색기처럼 스스로 그린 제목줄(탭이 제목줄 자리에, 최소화·최대화·닫기 단추)을 쓴다.
//   (사용자가 Firefox 설정에서 끄면 Firefox 가 시스템 제목줄을 요청하고, WorldLink 가 그걸 보고 SekaiDE 의 제목줄을 그린다)
//   (예전 합성기(SekaiCompose)는 이 모드의 그림자 자리만큼 내용을 밀어 그려 꺼 두었다 — SEKAI_GEOM_SUBSURFACE 로 고침)
pref("browser.tabs.inTitlebar", 1);
