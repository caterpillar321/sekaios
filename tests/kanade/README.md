# 카나데 (Kanade) — SekaiOS 코드 검토 하네스

MafuyuMom 이 "기능이 되는가"를 본다면, 카나데는 "코드가 계속 자랄 수 있는가"를 본다.
의존성 트리가 깨지거나, 같은 것을 여러 번 만들거나, 층이 뒤엉켜 고치기 어려워지는 곳을 찾는다.

```sh
./kanade run                 # 전부 → 화면 요약 + ~/.cache/kanade/reports/<시각>/results.json
./kanade run -c deps --all   # 하나만, 참고(info)까지
./kanade list
```

| 점검 | 보는 것 |
|---|---|
| `imports` | 우리 코드끼리 import — 순환(맨 위/함수 안), 층 위반(공용 `sekaishell` → 앱, 앱 → 앱), 패키지에 안 실리는 모듈 |
| `deps` | 부르는 명령·typelib·파이썬 모듈의 주인 패키지가 `sekai-desktop` 의존성(설치기는 `sekai-installer`)으로 깔리는가 — 기준은 저장소의 `rootfs/` dpkg 기록 + 지금의 `scripts/pack-shell.sh` |
| `contracts` | 설정 키·기본값 ↔ 저장소 `DEFAULTS`, D-Bus 정의(XML) ↔ 패널 구현 ↔ `sekai-ctl`, `sekai-ctl` 하위 명령을 부르는 곳 ↔ 실제 갈래 |
| `dead` | 아무도 부르지 않는 함수·클래스(vulture), CSS 에만 있고 코드가 쓰지 않는 클래스 |
| `lint` | pyflakes, shellcheck |
| `dupes` | 똑같은·거의 같은 함수(구문 트리 정규화), 모듈마다 다시 만든 도우미, 손으로 쓴 CSS 에서 같은 선택자를 두 번 따로 정의해 몰래 덮는 것 |

파이썬은 SekaiOS 와 같은 3.13 으로 돈다 (`uv` 가 있으면 저절로). 수준: 오류(새로 깐 PC 에서 깨짐 등) · 경고(고칠 것) · 참고.

## 관문 · 기준선

`scripts/publish-repo.sh` 가 게시 전에 `./kanade gate` 를 돌린다 — 오류가 있거나 `baseline.json` 에 없던 경고가 생기면 게시를 멈춘다.
고쳐서 경고가 줄면 `./kanade baseline` 으로 기준선도 줄인다 (늘리는 데 쓰지 않는다). 급할 때만 `KANADE_SKIP=1`.

알고 남기는 것은 이유와 함께 코드에 적는다 — 층 예외는 `kd/imports.py` 의 `LIBRARIES`, 패키지 경계 때문에 못 합치는 중복은
`kd/dupes.py` 의 `ALLOWED`. 이유가 사라지면 지운다.

## 첫 정리 (2026-10-09) — 경고 68 → 0

- 설정 저장소를 공용으로 (`sekaisettings.store` → `sekaishell.store`, 옛 경로는 다시 내보내기만). 설정 읽기는 모두 `store.read()`
  (기본값까지) — 작업 표시줄의 기본값 사본 없앰
- NetworkManager 처리 하나로 (`sekaishell.nm`, Wi-Fi 창 `sekaishell.wifidialog`) — 빠른 설정이 따로 들던 한 벌 없앰
- 앱 뼈대 `sekaishell.appkit` (AppTheme · JsonState · ToastMixin · shot_when_asked) — 앱 9개. Windows 앱·스토어·앱 설치 관리자도
  이제 설정의 색·모드를 곧바로 따라간다
- 공용 UI `sekaishell.ui` (combo · icon_image · text_label · text_entry · flat_button), 설정 페이지 도우미는 `sekaisettings.widgets`
- 바탕 화면·파일 탐색기 실행기 `sekaishell.launchers`, sysfs 읽기 `sekaishell.sysfs`, 배터리는 `sekaishell.power`
- root 도우미 apt 진행 `sekai_apt.py`(sekaios-base), 설치기 디스크 이름 `sekai_disk.py`
- 죽은 함수 12개·안 쓰는 CSS 3개·안 쓰는 import 정리, shellcheck 경고 0

