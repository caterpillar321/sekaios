# MafuyuMom — SekaiOS 기능 평가 하네스

엄격한 검사관. 서버(KVM)에서 SekaiOS VM 을 띄워 **진짜 사용자처럼** 조작하고, 기능이 제대로 도는지 체계적으로 확인합니다.

## 무엇을 하나

- **VM**: 골든 이미지 위에 실행마다 새 덧씌우기 디스크(qcow2 overlay)를 만들어 띄우고, 끝나면 버립니다. 시험이 무엇을 바꿔도 골든은 그대로라 매번 같은 상태에서 시작합니다.
- **입력**: QMP 로 VM 밖에서 키보드·마우스를 넣습니다 (앱이 보기엔 진짜 장치).
- **UI 찾기**: VM 안의 `mm/agent/mm_agent.py` 가 AT-SPI(접근성 정보)로 단추·항목을 **이름과 역할**로 찾아 화면 좌표를 돌려줍니다. 좌표를 박아 두지 않으므로 레이아웃이 바뀌어도 시험이 깨지지 않습니다. 이름 없는 단추는 "발견"으로 보고합니다 (내레이터가 못 읽는 것).
- **상태 확인**: SSH 로 hyprctl · gsettings · 파일 · 패키지 상태를 봅니다.
- **불변식**: 시험마다 끝나고 합성기·작업 표시줄·바탕화면이 살아 있는지, 크래시 보고·파이썬 Traceback(저널)·합성기 [CRIT] 가 새로 생겼는지 확인합니다. 깨지면 그 시험은 "경고".
- **보고서**: `~/.cache/mafuyumom/reports/<시각>/report.html` — 통과·경고·실패·오류, 실패 때 스크린숏, Traceback, 발견.

## 쓰는 법

```sh
cd tests/mafuyumom
./mafuyumom golden --from ~/.cache/sekai-svm-btrfs   # 골든 만들기 (원본 VM 을 꺼 두고)
./mafuyumom list                                     # 시험 목록
./mafuyumom run                                      # 전부
./mafuyumom run -s session -s windows                # 스위트 골라서
./mafuyumom run -k snap                              # 이름에 snap 이 든 것만
./mafuyumom run --install-latest                     # packages/ 의 최신 빌드를 깔고 다시 부팅한 뒤
./mafuyumom run --keep                               # 끝나도 VM 을 두기 (VNC 127.0.0.1:5909, SSH 2340)
```

## 시험 쓰기

`suites/sN_이름.py` 에 `@test` 로:

```python
from mm.runner import test

@test("시작 메뉴 → 모든 앱에서 계산기 열기", suite="session")
def start_menu_list(t):
    t.key("meta_l")                                              # Win
    t.expect(t.ui.wait(app="sekai-panel", role="text", name="검색"), "검색 칸")
    t.expect(t.ui.click(app="sekai-panel", role="label", name="계산기"), "계산기 항목")
    t.expect(t.window("org.sekaios.Calculator"), "계산기 창이 떴다")
```

`t` 의 도구: `key · type · click · drag`(입력), `ui.find · ui.wait · ui.click · ui.text · ui.unnamed`(AT-SPI),
`sh · root · hypr · clients · window · gone · launch · close · kill`(VM 안), `wait · expect · fail · skip · note · finding · shot`(판정·기록).

## 스위트

| 스위트 | 내용 |
|---|---|
| session | 작업 표시줄, 시작 메뉴(목록·검색), 빠른 설정, 알림·알림 센터, Alt+Tab, 가상 데스크톱, 잠금·해제 |
| windows | 창 단추 취소, 최대화·복원, 최소화(데스크톱 유지)·되살리기, 스냅·레이아웃 바·Esc 취소, 크기 조절, 대화상자, 무작위 조작 |
| settings | 모든 설정 페이지 열기, 라이트·다크, 텍스트 크기, 스위치로 애니메이션 끄기 |

2단계(예정): 앱 설치(스토어·Flathub·.deb)와 실사용(탐색기·메모장·계산기·사진·터미널·브라우저·작업 관리자·컴퓨터 관리),
시스템(업데이트·복원 지점·사용자·재부팅), 접근성, 보안 회귀. 실기(노트북·SFF)용 입력(uinput)도.
