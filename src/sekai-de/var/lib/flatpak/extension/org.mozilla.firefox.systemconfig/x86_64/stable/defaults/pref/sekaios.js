// SekaiDE — Flathub 의 Firefox(org.mozilla.firefox)용 기본값. Firefox 가 이 확장 자리(systemconfig)를 읽는다.
//   (사용자가 Firefox 설정에서 끄면 Firefox 가 시스템 제목줄을 요청하고, WorldLink 가 그걸 보고 SekaiDE 의 제목줄을 그린다)
//   (예전 합성기(SekaiCompose)는 이 모드의 그림자 자리만큼 내용을 밀어 그려 꺼 두었다 — SEKAI_GEOM_SUBSURFACE 로 고침)
pref("browser.tabs.inTitlebar", 1);
