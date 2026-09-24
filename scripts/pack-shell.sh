#!/bin/bash
# SekaiOS — sekai-shell 을 .deb 으로 포장
#   순수 파이썬이라 buildroot 없이 호스트에서 바로 만들 수 있다.
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
SRC="$P/src/sekai-shell"
OUT="$P/packages"
# 버전 — 빌드마다 올라가야 apt 가 '업그레이드'로 인식한다.
#   전에는 늘 0.1.0-sekai1 이라 같은 버전으로 보고 설치를 건너뛰었다.
#   0.2.0+20260924.1530 처럼 빌드 시각을 붙인다.
BASE_VER="${SEKAI_VERSION:-0.2.0}"
VER="${BASE_VER}+$(date +%Y%m%d.%H%M)"
REV="sekai1"
FULL="${VER}-${REV}"
STAGE="$(mktemp -d)"
STAGE_D="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$STAGE_D"' EXIT

# 옛 빌드 정리 — 남겨 두면 finalize 의 *.deb 가 두 버전을 동시에 설치하려 한다
rm -f "$OUT"/sekai-shell_*.deb "$OUT"/sekai-desktop_*.deb

echo "==> 스테이징"
install -Dm755 "$SRC/sekai-panel"     "$STAGE/usr/bin/sekai-panel"
install -Dm755 "$SRC/sekai-settings"  "$STAGE/usr/bin/sekai-settings"
install -Dm755 "$SRC/sekai-wallpaper" "$STAGE/usr/bin/sekai-wallpaper"
install -Dm755 "$SRC/sekai-desk"      "$STAGE/usr/bin/sekai-desk"
install -Dm755 "$SRC/sekai-lock"      "$STAGE/usr/bin/sekai-lock"
install -Dm755 "$SRC/sekai-idle"      "$STAGE/usr/bin/sekai-idle"
install -Dm755 "$SRC/sekai-ctl"       "$STAGE/usr/bin/sekai-ctl"
install -Dm755 "$SRC/sekai-screenshot" "$STAGE/usr/bin/sekai-screenshot"
install -Dm755 "$SRC/sekai-session"   "$STAGE/usr/bin/sekai-session"
install -Dm755 "$SRC/sekai-greeter"   "$STAGE/usr/bin/sekai-greeter"
install -Dm755 "$SRC/sekai-greeter-session" "$STAGE/usr/bin/sekai-greeter-session"
install -Dm644 "$SRC/lib/hw-env.sh"   "$STAGE/usr/lib/sekai/hw-env.sh"
install -Dm644 "$SRC/style.css"       "$STAGE/usr/share/sekai-shell/style.css"
install -Dm644 "$SRC/settings.css"    "$STAGE/usr/share/sekai-shell/settings.css"

# 파이썬 패키지들
#   sekaishell    — 패널이 쓰는 공용 모듈 (팝업 / 트레이 / 알림)
#   sekaisettings — 설정 프로그램 본체
DIST="$STAGE/usr/lib/python3/dist-packages"
mkdir -p "$DIST/sekaishell" "$DIST/sekaisettings/pages"
for f in "$SRC/sekaishell"/*.py;          do install -Dm644 "$f" "$DIST/sekaishell/$(basename "$f")"; done
for f in "$SRC/sekaisettings"/*.py;       do install -Dm644 "$f" "$DIST/sekaisettings/$(basename "$f")"; done
for f in "$SRC/sekaisettings/pages"/*.py; do install -Dm644 "$f" "$DIST/sekaisettings/pages/$(basename "$f")"; done

# settings.css 는 패키지 옆이 아니라 /usr/share 에 있으므로 app.py 의 폴백 경로가 찾는다

# .desktop 항목
install -Dm644 /dev/stdin "$STAGE/usr/share/applications/sekai-settings.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Settings
Name[ko]=설정
GenericName=System Settings
GenericName[ko]=시스템 설정
Comment=Configure SekaiOS
Comment[ko]=SekaiOS 를 설정합니다
Exec=sekai-settings
Icon=preferences-system
Terminal=false
Categories=Settings;DesktopSettings;GTK;
Keywords=settings;preferences;configuration;설정;환경설정;
StartupNotify=true
DESK

# 셸 소스가 /usr/share 의 스타일을 찾도록 경로 확인 (main() 에 이미 폴백 있음)
mkdir -p "$STAGE/DEBIAN"
# 저작권·라이선스 고지 (Apache 2.0)
copyright() {
    install -d "$1/usr/share/doc/$2"
    { echo "패키지:  $2 (SekaiOS)"
      echo "저작권:  SekaiOS 개발자"
      echo "라이선스: Apache License 2.0 — 아래 전문"
      echo; cat "$P/LICENSE"; } > "$1/usr/share/doc/$2/copyright"
    chmod 644 "$1/usr/share/doc/$2/copyright"
}
copyright "$STAGE" sekai-shell

cat > "$STAGE/DEBIAN/control" <<EOF
Package: sekai-shell
Version: ${VER}-${REV}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: x11
Priority: optional
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0,
 gir1.2-gtklayershell-0.1, hyprland, foot, fuzzel,
 adwaita-icon-theme, papirus-icon-theme, swaybg, swayidle,
 gir1.2-gtksessionlock-0.1, libgtk-session-lock0, python3-pampy,
 libglib2.0-bin
Recommends: wireplumber, pavucontrol, swaylock,
 network-manager-gnome, xfce4-taskmanager
Description: SekaiOS desktop shell
 Panel, taskbar, start menu and the system settings app for
 SekaiOS, built on gtk-layer-shell and the Hyprland IPC.
EOF

echo "==> sekai-shell .deb 생성"
mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$STAGE" \
         "$OUT/sekai-shell_${FULL}_all.deb" > /dev/null
ls -lh "$OUT/sekai-shell_${FULL}_all.deb" | awk '{print "    "$5"  "$9}'

# ═══════════════════════════════════════════════════════════
#  sekai-desktop — 메타패키지 + 데스크탑 시스템 설정
#
#  "SekaiOS 데스크탑이 되려면 무엇이 깔려 있어야 하는가" 의 정의.
#  옛 설치본도  apt install ./sekai-desktop_*.deb  한 번이면
#  빠진 패키지가 전부 따라 들어온다. (이미지를 만들 때도 같은 목록을 쓴다)
# ═══════════════════════════════════════════════════════════
echo "==> sekai-desktop 스테이징"
DSRC="$P/src/sekai-desktop"
( cd "$DSRC" && find . -type f ) | while read -r f; do
    f="${f#./}"
    mode=644
    case "$f" in usr/bin/*|usr/libexec/*) mode=755 ;; esac
    install -Dm$mode "$DSRC/$f" "$STAGE_D/$f"
done

mkdir -p "$STAGE_D/DEBIAN"
# /etc 아래 파일은 conffile — 사용자가 고친 건 업그레이드 때 보존된다
( cd "$STAGE_D" && find etc -type f 2>/dev/null | sed 's|^|/|' ) > "$STAGE_D/DEBIAN/conffiles"

# 설치/제거 스크립트
#   · pam-auth-update: 로그인 때 GNOME 키링을 풀도록 PAM 에 반영
#     (안 풀리면 크로미움이 켤 때마다 '키링 암호' 창을 띄운다)
#   · gschema override 는 libglib2.0 의 dpkg 트리거가 알아서 컴파일한다
cat > "$STAGE_D/DEBIAN/postinst" <<'PI'
#!/bin/sh
set -e
if [ "$1" = "configure" ] && command -v pam-auth-update >/dev/null; then
    pam-auth-update --package
fi
if [ "$1" = "configure" ]; then
    # 그래픽 로그인 화면으로 부팅한다 (greetd = display-manager)
    [ -d /run/systemd/system ] && systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable greetd.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    # 로그인 화면 해상도 공유 폴더 (usr/lib/tmpfiles.d/sekai.conf)
    systemd-tmpfiles --create /usr/lib/tmpfiles.d/sekai.conf >/dev/null 2>&1 \
        || install -d -m 1777 /var/lib/sekai/displays
    # 부팅 화면(Plymouth) — 테마가 바뀔 때만 initramfs 를 다시 만든다 (느리므로)
    if command -v plymouth-set-default-theme >/dev/null \
       && [ "$(plymouth-set-default-theme 2>/dev/null)" != sekai ]; then
        plymouth-set-default-theme sekai
        update-initramfs -u >/dev/null 2>&1 || true
    fi
fi
PI
cat > "$STAGE_D/DEBIAN/prerm" <<'PR'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && command -v pam-auth-update >/dev/null; then
    pam-auth-update --package --remove sekai-gnome-keyring
fi
PR
chmod 755 "$STAGE_D/DEBIAN/postinst" "$STAGE_D/DEBIAN/prerm"

copyright "$STAGE_D" sekai-desktop

cat > "$STAGE_D/DEBIAN/control" <<CTRL
Package: sekai-desktop
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: metapackages
Priority: optional
Depends: sekai-shell (= ${FULL}),
 hyprland, hyprbars (>= 0.50.0-sekai4), hyprexpo, xwayland, binutils,
 xdg-desktop-portal, xdg-desktop-portal-gtk, xdg-desktop-portal-wlr,
 foot, fuzzel, swaybg, swayidle, swaylock, grim, slurp,
 brightnessctl, playerctl, wtype, pkexec,
 wl-clipboard, cliphist, lxpolkit, libnotify-bin, wayland-utils,
 pipewire, pipewire-audio, pipewire-pulse, wireplumber, pavucontrol,
 network-manager, network-manager-gnome, systemd-resolved,
 thunar, thunar-volman, tumbler, gvfs, gvfs-backends,
 mousepad, ristretto, evince, xarchiver, galculator, xfce4-taskmanager,
 chromium, qt6-wayland, xdg-user-dirs, xdg-user-dirs-gtk,
 fonts-pretendard, fonts-dejavu, fonts-jetbrains-mono, fonts-nanum,
 fonts-noto-color-emoji,
 papirus-icon-theme, adwaita-icon-theme,
 gnome-keyring, libpam-gnome-keyring, libpam-runtime,
 ibus, ibus-wayland, ibus-hangul, ibus-gtk3, ibus-gtk4, locales, greetd,
 plymouth,
 dbus-user-session,
 libgl1-mesa-dri, libegl-mesa0, mesa-utils
Recommends: htop, tmux, tree, ncdu, vim, nano, git, curl, wget,
 bash-completion, less, man-db
Conflicts: fnott
Description: SekaiOS desktop (metapackage)
 Pulls in everything that makes up the SekaiOS desktop: the Hyprland
 compositor with title-bar and overview plugins, the sekai-shell panel,
 settings app, tray and notification center, and the default set of
 applications. Also ships the distribution Hyprland configuration
 (/usr/share/sekai/hypr/hyprland.conf), NetworkManager defaults,
 Chromium Wayland flags and the default wallpaper.
CTRL

echo "==> sekai-desktop .deb 생성"
dpkg-deb --root-owner-group --build "$STAGE_D" \
         "$OUT/sekai-desktop_${FULL}_all.deb" > /dev/null
ls -lh "$OUT/sekai-desktop_${FULL}_all.deb" | awk '{print "    "$5"  "$9}'
echo "    conffiles:"; sed 's/^/      /' "$STAGE_D/DEBIAN/conffiles"
echo "==> 버전 ${FULL}"
