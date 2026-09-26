# SekaiOS

데비안 13 (trixie) 기반 데스크톱 배포판. 코드명 **Hatsune**.
Hyprland 위에 윈도우처럼 쓰는 셸(작업 표시줄·시작 메뉴·알림 센터·설정 앱)을 올렸다.

## 구성

| 폴더 | 내용 |
|---|---|
| `src/sekai-shell/` | 셸 — 작업 표시줄, 바탕화면, 로그인·잠금 화면, 설정 앱 (Python + GTK3 layer-shell) |
| `src/sekai-desktop/` | 배포판 설정 메타패키지 — Hyprland 설정, 테마, 부팅 화면, 기본 앱 목록 |
| `overlay/` | 이미지에 직접 넣는 파일 (설치 프로그램 `sekai-install` 등) |
| `config/` | 라이브 ISO 부트로더 설정 |
| `scripts/` | 빌드 스크립트 |
| `local/` | **git 에 없음** — 이 컴퓨터 전용 설정 (`local/README.md` 참고) |

## SekaiOS 가 만든 것과 함께 쓰는 것

SekaiOS 의 화면은 직접 만들었다. 그 밑의 운영체제 부품(드라이버·네트워크·소리·인쇄·암호 저장 등)은
다른 리눅스 데스크톱들과 같은 공개 부품을 함께 쓴다 — 윈도우에서 보이는 창은 마이크로소프트가 만들어도
그 밑에 여러 회사의 드라이버와 표준 부품이 있는 것과 같다.

**직접 만든 것** (`src/sekai-shell`, Python + GTK3)

- 작업 표시줄 · 시작 메뉴 · 알림 센터 · 빠른 설정 · 바탕화면 · 창 배치(스냅) · 스크린샷
- 로그인 화면 · 잠금 화면 · 첫 설정 · 설치 프로그램
- 설정 · 작업 관리자 · 컴퓨터 관리 · 파일 탐색기 · 메모장 · 계산기 · 사진
- 사용자 계정 컨트롤(관리자 권한 확인 창) · 네트워크 암호 창 · 암호 저장소 창
- 터미널 설정(`sekai-terminal` — kitty 를 윈도우 터미널처럼), 부팅 화면 테마, 이미지·패키지 빌드 도구

**고쳐서 쓰는 것** (원본 라이선스와 출처, 고친 내용을 함께 싣는다)

| 부품 | 쓰임 | 라이선스 · 고친 곳 |
|---|---|---|
| Hyprland 0.50.1, hyprbars, hyprexpo | 창을 그리는 합성기, 창 제목 표시줄 | BSD-3 · `scripts/hypr/patch-*.py` |
| Fluent-gtk-theme (vinceliuice) | GTK 테마 Sekai-Light · Sekai-Dark 의 바탕 | GPL-3.0 · `third_party/fluent-gtk-theme` |
| Plymouth | 부팅 화면 | GPL-2.0 · `scripts/hypr/patch-plymouth-*.py` |

**그대로 쓰는 기반** (데비안 13 패키지 — 화면에는 SekaiOS 창만 보인다)

| 부품 | 하는 일 |
|---|---|
| 데비안 13 · 리눅스 커널 · systemd · GRUB · shim | 운영체제 바탕, 부팅 |
| GTK 3 · gtk-layer-shell · PyGObject | SekaiOS 창을 그리는 도구 |
| NetworkManager · wpa_supplicant | 유선 · Wi-Fi 연결 (창은 설정 › 네트워크) |
| PipeWire · WirePlumber | 소리 (창은 설정 › 소리) |
| CUPS · avahi · ipp-usb | 인쇄 (창은 설정 › 프린터) |
| BlueZ | 블루투스 |
| udisks2 · gvfs | USB · 드라이브 연결 |
| polkit | 관리자 권한 확인 (창은 사용자 계정 컨트롤) |
| gnome-keyring · gcr | 앱이 저장한 암호를 암호화해 보관, 암호를 넘길 때의 암호화 (창은 암호 저장소 창) |
| ibus · ibus-hangul | 한글 입력 |
| greetd | 로그인 화면을 띄우는 관리자 (화면은 SekaiOS 로그인 화면) |
| xfwm4 · Xorg | 그래픽 드라이버가 없을 때의 기본 화면 모드 |
| kitty · foot | 터미널 |
| Papirus 아이콘 · DMZ-White 커서 · Pretendard · Noto 글꼴 | 아이콘 · 커서 · 글꼴 |
| Chromium | 웹 브라우저 |

## 빌드

WSL2 / 데비안 계열 호스트에서:

```sh
sudo scripts/build-hypr.sh        # Hyprland 와 플러그인을 .deb 으로 (packages/)
scripts/pack-shell.sh             # sekai-shell, sekai-desktop .deb
sudo scripts/finalize.sh          # rootfs 에 설치 → squashfs → ISO
scripts/build-repo.sh             # 서명된 apt 저장소 (repo/)
scripts/publish-repo.sh           # 저장소 게시 → https://caterpillar321.github.io/sekaios-apt/
```

- `rootfs/` 를 처음 만드는 과정(debootstrap)은 아직 스크립트로 정리되지 않았다.
- 저장소 서명 키는 `~/.sekai-signing` 에 있고 저장소에 올리지 않는다.

## 개발 VM

`local/env` 에 `SEKAI_VM`, `SEKAI_VM_PW` 를 적고 `scripts/deploy-vm.sh --restart`.
개발 빌드에는 `local/overlay-dev/` 의 파일(개발용 SSH 키)이 들어간다.
기본은 **배포용**이다. 개발용 SSH 키(`local/overlay-dev`)를 넣은 개발 ISO 는 `sudo env SEKAI_DEV=1 scripts/finalize.sh` 로 만들고, 이름에 `-dev` 가 붙는다 (남에게 주지 말 것).

## 라이선스

[Apache License 2.0](LICENSE).
이미지에 들어가는 데비안·Hyprland 등 다른 패키지는 각자의 라이선스를 따른다.

예외 — `third_party/` 의 것과 그것으로 만든 파일은 원래 라이선스를 따른다:

- `third_party/fluent-gtk-theme` → GTK 테마 Sekai-Light · Sekai-Dark: **GPL-3.0**
  ([Fluent-gtk-theme](https://github.com/vinceliuice/Fluent-gtk-theme) 바탕, 자세한 출처는 그 폴더의 `UPSTREAM.md`)
