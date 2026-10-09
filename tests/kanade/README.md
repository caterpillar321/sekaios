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
