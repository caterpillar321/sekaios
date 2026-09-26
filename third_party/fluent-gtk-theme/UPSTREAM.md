# Fluent-gtk-theme (SekaiOS 가 가져와 고친 것)

SekaiOS 의 GTK 테마 **Sekai-Light · Sekai-Dark** 는 이 폴더의 원본으로 만든다
(`scripts/build-theme.sh` → `src/sekai-desktop/usr/share/themes/Sekai-{Light,Dark}/gtk-{3.0,4.0}`).

## 출처

| | |
|---|---|
| 원본 | Fluent-gtk-theme — https://github.com/vinceliuice/Fluent-gtk-theme |
| 판 | 태그 `2025-04-17` (commit `76f8112ff22d81b372f7081c4fad13e9a08227de`) |
| 저작권 | vinceliuice (Vince Liuice) 와 Fluent-gtk-theme 기여자들 |
| 라이선스 | **GPL-3.0** — 전문은 이 폴더의 `COPYING` |

Fluent 는 다른 저작물을 바탕으로 한다 (각 저작자의 권리를 여기 함께 적는다):

- **Materia theme** — https://github.com/nana-4/materia-theme — nana-4 와 기여자들, GPL-2.0-or-later
- Materia 는 GNOME 의 **Adwaita** 를 바탕으로 한다 — LGPL-2.1-or-later
- 기호 아이콘 일부는 Google 의 **Material Design icons** 바탕 — Apache-2.0
- 색 값 일부는 Material Design 색 팔레트(2014) 바탕 (`src/_sass/_color-palette.scss`)

## 가져온 것

원본 중 GTK 3·4 테마를 만드는 데 필요한 것만:

- `COPYING`, `README.md` — 원본 그대로
- `src/_sass/` — GTK 부분 (`cinnamon/` 은 뺐다)
- `src/gtk/3.0/gtk-{Light,Dark}.scss`, `src/gtk/4.0/gtk-{Light,Dark}.scss`
- `src/gtk/assets.svg`, `src/gtk/assets.txt`, `src/gtk/scalable/`

원본의 다른 부분(GNOME Shell·Cinnamon·Xfwm4·Plank·Firefox 테마, 미리 만든 CSS·PNG, 설치 스크립트)은 가져오지 않았다.

## SekaiOS 가 고친 것

고친 파일은 머리에 `Modified by SekaiOS, <날짜>` 표시가 있다 (GPL-3.0 5조 a).

| 파일 | 날짜 | 내용 |
|---|---|---|
| `src/_sass/_tweaks.scss` | 2026-09-26 | 색 테마를 `'sekai'` 로 |
| `src/_sass/_colors.scss` | 2026-09-26 | `$theme == 'sekai'` 일 때 강조색(#3CC8BE)·창 바탕·면·제목줄을 SekaiOS 셸 팔레트로 |

`scripts/build-theme.sh` 는 원본 `install.sh` 처럼 `_tweaks.scss` 를 `_tweaks-temp.scss` 로 복사해 컴파일하고,
`assets.svg` 의 파란색(#1A73E8 · #3281EA)을 강조색으로 바꿔 PNG 로 굽는다 (원본 `make-assets.sh` · `render-assets.sh` 와 같은 방식).

## 라이선스 경계

SekaiOS 자체 코드는 Apache-2.0 이지만, **이 폴더와 여기서 만든 테마 파일(`Sekai-{Light,Dark}/gtk-*`, `index.theme`)은 GPL-3.0** 이다.
패키지(sekai-desktop)의 `/usr/share/doc/sekai-desktop/copyright` 에도 같은 내용을 적는다.
