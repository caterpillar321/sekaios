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
**배포용 ISO 는 `SEKAI_RELEASE=1 sudo -E scripts/finalize.sh`** 로 만든다.

## 라이선스

[Apache License 2.0](LICENSE).
이미지에 들어가는 데비안·Hyprland 등 다른 패키지는 각자의 라이선스를 따른다.
