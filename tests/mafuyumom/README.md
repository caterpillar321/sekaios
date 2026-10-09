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
./mafuyumom list                                     # 시험 목록 (Q = quick, S = 느린 시험)
./mafuyumom quick                                    # 중간 점검 (~1분 반) — 켜진 VM 그대로, 영역마다 대표 26개
./mafuyumom full                                     # 마지막 검사 (~20분) — 새 VM + 최신 패키지 + 느린 시험까지 전부
./mafuyumom run                                      # 느린 것 빼고 전부
./mafuyumom run --slow                               # 느린 시험(재부팅이 드는 복원 왕복, 무작위 60번)까지
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

`@test(…, quick=True)` 는 `quick` 에, `slow=True` 는 `full`·`--slow` 에서만.

`t` 의 도구: `key · type · click · drag`(입력), `ui.find · ui.wait · ui.click · ui.text · ui.unnamed`(AT-SPI — `frame`/`not_frame` 로 창 제목 거르기),
`sh · root · hypr · clients · window · gone · launch · close · kill · auth`(VM 안 — `auth` 는 관리자 인증 창에 암호),
`after`(실패해도 끝에 할 정리), `wait · expect · fail · skip · note · finding · shot`(판정·기록).

## 알아 둘 함정

- **AT-SPI 에이전트는 하나를 계속 띄워 쓴다** (`mm_agent.py --serve`). 요청마다 새 클라이언트를 띄우면 GLib 2.84 가 앱마다
  직접 연결의 pidfd 를 놓지 않아, 오래 켜 둔 앱(인증 창·바탕화면·포털)이 파일 1024개에 막혀 CPU 100% 로 멈춘다.
- **QMP 키 입력은 비동기** — `send-key` 는 줄에 넣고 바로 돌아온다(실제로는 한 글자 ~0.1초). `qmp.type` 이 끝까지 기다린다.
- **관리자 인증 창은 미끄러져 들어온다** — 멈춘 뒤 치고, 친 글자 수를 확인한다(`t.auth`).
- **snapper 목록은 `--no-dbus`** — 복원 도우미가 D-Bus 없이 만들어 snapperd 의 목록 캐시가 모른다.
- **`pgrep -f` 패턴은 `[x]` 대괄호로** — 안 그러면 원격 셸 자신의 명령줄에 걸린다.
- `--reuse` 는 다시 부팅하지 않는다 — 시작할 때 열린 창을 모두 닫는다.

## 스위트

| 스위트 | 내용 |
|---|---|
| session | 작업 표시줄, 시작 메뉴(목록·검색), 빠른 설정, 알림·알림 센터, Alt+Tab, 가상 데스크톱, 잠금·해제 |
| windows | 창 단추 취소, 최대화·복원, 최소화(데스크톱 유지)·되살리기, 스냅·레이아웃 바·Esc 취소, 크기 조절, 대화상자, 무작위 조작 |
| settings | 모든 설정 페이지 열기, 라이트·다크, 텍스트 크기, 스위치로 애니메이션 끄기 |
| apps | 스토어에서 검색·설치 → 시작 메뉴로 열어 계산 → 설정 › 설치된 앱에서 제거, 앱 설치 관리자로 .deb 설치 |
| use | 메모장 저장·다시 열기, 계산기, 탐색기(새 폴더·이름 바꾸기·휴지통), 사진, 터미널, Chromium, 작업 관리자(끝내기), 컴퓨터 관리 |
| system | 복원 지점 만들기·삭제, 계정 추가·삭제, (느림) 복원하고 다시 시작 → 복원 취소 |

다음: 업데이트, 사용자 전환, Flathub(느림), 접근성·보안 회귀.

## 실기 모드 (MM_REAL)

VM 대신 실제 PC 를 시험한다. 입력은 그 PC 의 가상 키보드·태블릿(`real/mm-uinput.py`, root), 화면은 `grim`.

```sh
# 시험할 PC 에서 한 번 (miku 계정·개발 키·uinput 모듈)
sudo sh real/setup-target.sh
# 서버에서 — 집 공유기 안이면 거쳐 갈 곳을 MM_REAL_JUMP 로
MM_REAL=miku@192.168.0.56 MM_REAL_JUMP=homedesktop ./mafuyumom run -s session -s windows --reuse --keep
```

- 화면 크기가 1920x1080 이 아니면 `MM_REAL_SCREEN=2560x1440`.
- VM 에만 있는 동작(QEMU 의 전원 단추·절전 확인)을 쓰는 시험은 건너뜀으로 나온다.
- 방화벽 시험의 "밖에서 들어오는 연결"은 거쳐 갈 PC 에서 LAN 으로 붙는다 (`ssh -L`, 빈 포트를 따로 잡는다).
- 시험이 계정·방화벽·복원 지점을 바꾸니 쓰는 PC 에는 돌리지 말 것.
