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
rm -f "$OUT"/sekai-shell_*.deb "$OUT"/sekai-desktop_*.deb "$OUT"/sekai-installer_*.deb

echo "==> 스테이징"
install -Dm755 "$SRC/sekai-panel"     "$STAGE/usr/bin/sekai-panel"
install -Dm755 "$SRC/sekai-settings"  "$STAGE/usr/bin/sekai-settings"
install -Dm755 "$SRC/sekai-wallpaper" "$STAGE/usr/bin/sekai-wallpaper"
install -Dm755 "$SRC/sekai-desk"      "$STAGE/usr/bin/sekai-desk"
install -Dm755 "$SRC/sekai-lock"      "$STAGE/usr/bin/sekai-lock"
install -Dm755 "$SRC/sekai-idle"      "$STAGE/usr/bin/sekai-idle"
install -Dm755 "$SRC/sekai-ctl"       "$STAGE/usr/bin/sekai-ctl"
install -Dm755 "$SRC/sekai-screenshot" "$STAGE/usr/bin/sekai-screenshot"
install -Dm755 "$SRC/sekai-oobe"      "$STAGE/usr/bin/sekai-oobe"
install -Dm755 "$SRC/sekai-session"   "$STAGE/usr/bin/sekai-session"
install -Dm755 "$SRC/sekai-greeter"   "$STAGE/usr/bin/sekai-greeter"
install -Dm755 "$SRC/sekai-greeter-session" "$STAGE/usr/bin/sekai-greeter-session"
install -Dm644 "$SRC/lib/hw-env.sh"   "$STAGE/usr/lib/sekai/hw-env.sh"
install -Dm755 "$SRC/lib/keep-running" "$STAGE/usr/lib/sekai/keep-running"
install -Dm755 "$SRC/sekai-terminal"  "$STAGE/usr/bin/sekai-terminal"
# 기본 화면 모드 (X11) — 그래픽 드라이버가 없을 때
for f in "$SRC"/lib/x11/*; do
    case "$(basename "$f")" in sxhkdrc) m=644 ;; *) m=755 ;; esac
    install -Dm$m "$f" "$STAGE/usr/lib/sekai/x11/$(basename "$f")"
done
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
 gir1.2-gtklayershell-0.1, hyprland, kitty, foot, fuzzel,
 adwaita-icon-theme, papirus-icon-theme, swaybg, swayidle,
 gir1.2-gtksessionlock-0.1, libgtk-session-lock0, python3-pampy,
 libglib2.0-bin, sekai-winshot
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
    case "$f" in usr/bin/*|usr/sbin/*|usr/libexec/*|etc/kernel/postinst.d/*|etc/initramfs/post-update.d/*|etc/grub.d/*|usr/share/initramfs-tools/hooks/*) mode=755 ;; esac
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
    # 부팅 화면·콘솔을 바탕화면과 같은 모니터 모드로 (sekai-bootmode — 모드가 바뀔 때마다 신호가 끊긴다)
    systemctl enable sekai-bootmode.path >/dev/null 2>&1 || true
    [ -d /run/systemd/system ] && systemctl start sekai-bootmode.path >/dev/null 2>&1 || true
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    # 로그인 화면 해상도 공유 폴더 (usr/lib/tmpfiles.d/sekai.conf)
    systemd-tmpfiles --create /usr/lib/tmpfiles.d/sekai.conf >/dev/null 2>&1 \
        || install -d -m 1777 /var/lib/sekai/displays
    # SSH 호스트 키가 없는 설치본(이미지에서 지운 키를 첫 부팅이 못 만든 경우) — 여기서 만든다.
    #   라이브·이미지 만드는 중(chroot)엔 하지 않는다: 키가 이미지에 박히면 모든 설치본이 같은 키를 쓴다
    if [ -x /usr/sbin/sshd ] && [ ! -d /run/live/medium ] && [ -d /run/systemd/system ] \
       && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1; then
        ssh-keygen -A >/dev/null 2>&1 || true
        systemctl restart ssh.socket >/dev/null 2>&1 || true
    fi
    # 펌웨어 화면 장치 권한 규칙(70-sekai-fb.rules)을 지금 바로 적용
    if [ -d /run/systemd/system ] && command -v udevadm >/dev/null; then
        udevadm control --reload >/dev/null 2>&1 || true
        udevadm trigger --subsystem-match=graphics >/dev/null 2>&1 || true
    fi
    # 옛 설치본이 이미지에 직접 넣었던 커널 훅 → 이제 패키지의 zz-sekai-boot 가 한다
    for f in /etc/kernel/postinst.d/zz-sekai-esp /etc/initramfs/post-update.d/zz-sekai-esp; do
        [ -e "$f" ] && ! dpkg -S "$f" >/dev/null 2>&1 && rm -f "$f"
    done
    # 예전 설치본에 남은 refind 패키지가 업데이트 때 스스로 ESP 에 설치하지 않게
    if command -v debconf-set-selections >/dev/null; then
        echo "refind refind/install_to_esp boolean false" | debconf-set-selections || true
    fi
    # 데비안의 10_linux 대신 09_sekaios 가 부팅 항목을 만든다 (항목이 두 벌이 되지 않게).
    #   파일을 옮기지(dpkg-divert) 않고 실행 권한만 뺀다 — update-grub 은 실행 권한 없는 것을
    #   건너뛴다. 이 파일들은 grub-common 의 conffile 이라 옮기면 grub 업데이트 때 꼬인다.
    #   dpkg-statoverride 는 grub-common 이 업데이트돼도 유지된다.
    for f in 10_linux 30_uefi-firmware; do
        # 예전 판이 옮겨 둔 것 되돌리기
        if dpkg-divert --listpackage "/etc/grub.d/$f" 2>/dev/null | grep -qx sekai-desktop; then
            dpkg-divert --package sekai-desktop --rename --quiet --remove "/etc/grub.d/$f" || true
        fi
        if ! dpkg-statoverride --list "/etc/grub.d/$f" >/dev/null 2>&1; then
            dpkg-statoverride --update --add root root 0644 "/etc/grub.d/$f" 2>/dev/null || true
        fi
    done
    rmdir /usr/share/sekai/grub 2>/dev/null || true
fi
if [ "$1" = "configure" ] || [ "$1" = "triggered" ]; then
    # 부팅 메뉴(shim + GRUB)를 이 패키지 기준으로 다시 쓴다 — 설치된 디스크일 때만.
    #   shim·GRUB 패키지가 업데이트돼도 트리거로 여기가 불려 ESP 의 파일이 새것이 된다.
    #   예전 rEFInd 설치본은 이때 GRUB 으로 옮겨진다. (이미지 만드는 중·라이브면 건너뜀)
    /usr/sbin/sekai-bootloader update || true
fi
if [ "$1" = "configure" ]; then
    # 부팅 화면(Plymouth) — 테마나 그 그림이 바뀔 때만 initramfs 를 다시 만든다 (느리므로).
    #   테마는 initramfs 안에 복사되므로 파일만 바뀌어도 다시 만들어야 새 그림이 뜬다
    if command -v plymouth-set-default-theme >/dev/null; then
        # NVIDIA 를 부팅 초기에 올리는 훅(sekai-nvidia)도 해시에 넣는다 — 처음 받으면 initramfs 를 다시 만든다
        sum=$(cat /usr/share/plymouth/themes/sekai/* /usr/share/initramfs-tools/hooks/sekai-nvidia 2>/dev/null | md5sum | cut -d" " -f1)
        stamp=/var/lib/sekai/plymouth-theme.md5
        if [ "$(plymouth-set-default-theme 2>/dev/null)" != sekai ] || [ "$(cat $stamp 2>/dev/null)" != "$sum" ]; then
            plymouth-set-default-theme sekai
            update-initramfs -u >/dev/null 2>&1 || true
            mkdir -p /var/lib/sekai && echo "$sum" > $stamp
        fi
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
cat > "$STAGE_D/DEBIAN/postrm" <<'PO'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    # 데비안 부팅 항목을 되살리고, 우리 항목은 끈다 (conffile 이라 purge 전까진 남는다)
    for f in 10_linux 30_uefi-firmware; do
        dpkg-divert --package sekai-desktop --rename --quiet --remove "/etc/grub.d/$f" 2>/dev/null || true
        if dpkg-statoverride --list "/etc/grub.d/$f" >/dev/null 2>&1; then
            dpkg-statoverride --remove "/etc/grub.d/$f" || true
            [ -e "/etc/grub.d/$f" ] && chmod 755 "/etc/grub.d/$f"
        fi
    done
    [ -e /etc/grub.d/09_sekaios ] && chmod 644 /etc/grub.d/09_sekaios
    # 설치된 디스크면 부팅 메뉴를 다시 쓴다 (없어진 테마·항목을 가리키지 않게)
    if [ -f /boot/grub/grub.cfg ] && command -v update-grub >/dev/null; then
        update-grub >/dev/null 2>&1 || true
    fi
fi
PO
# shim·GRUB 이 업데이트되면 ESP 의 복사본도 새것으로 (postinst "triggered")
cat > "$STAGE_D/DEBIAN/triggers" <<'TR'
interest-noawait /usr/lib/shim
interest-noawait /usr/lib/grub/x86_64-efi-signed
TR
chmod 755 "$STAGE_D/DEBIAN/postinst" "$STAGE_D/DEBIAN/prerm" "$STAGE_D/DEBIAN/postrm"

copyright "$STAGE_D" sekai-desktop

cat > "$STAGE_D/DEBIAN/control" <<CTRL
Package: sekai-desktop
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: metapackages
Priority: optional
Depends: sekai-shell (= ${FULL}),
 hyprland, hyprbars (>= 0.50.0-sekai8), hyprexpo, xwayland, binutils,
 xdg-desktop-portal, xdg-desktop-portal-gtk, xdg-desktop-portal-wlr,
 foot, fuzzel, swaybg, swayidle, swaylock, grim, slurp,
 brightnessctl, playerctl, wtype, pkexec, efibootmgr, open-vm-tools, mokutil, pciutils, openssl,
 shim-signed, grub-efi-amd64-signed, grub-efi-amd64-bin, grub2-common, os-prober,
 firmware-amd-graphics, firmware-intel-graphics, firmware-nvidia-graphics, firmware-misc-nonfree,
 firmware-iwlwifi, firmware-realtek, firmware-atheros, firmware-mediatek, firmware-sof-signed,
 xserver-xorg-core, xserver-xorg-video-fbdev, xserver-xorg-input-libinput, xserver-xorg-legacy,
 xinit, x11-xserver-utils, xfwm4, xfconf, sxhkd, xcape, xsecurelock, xss-lock, maim, slop, xclip,
 xdotool, xterm, gir1.2-wnck-3.0,
 wl-clipboard, cliphist, lxpolkit, libnotify-bin, wayland-utils,
 pipewire, pipewire-audio, pipewire-pulse, wireplumber,
 network-manager, network-manager-gnome, systemd-resolved,
 tumbler, gvfs, gvfs-backends,
 qt6-wayland, xdg-user-dirs, xdg-user-dirs-gtk,
 fonts-pretendard, fonts-dejavu, fonts-jetbrains-mono, fonts-nanum,
 fonts-noto-color-emoji,
 papirus-icon-theme, adwaita-icon-theme,
 gnome-keyring, libpam-gnome-keyring, libpam-runtime,
 ibus, ibus-wayland, ibus-hangul, ibus-gtk3, ibus-gtk4, locales, greetd,
 plymouth (>= 24.004.60-5+sekai1), plymouth-themes,
 dbus-user-session,
 libgl1-mesa-dri, libegl-mesa0, mesa-utils
Recommends: htop, tmux, tree, ncdu, vim, nano, git, curl, wget,
 bash-completion, less, man-db,
 chromium, thunar, thunar-volman, mousepad, ristretto, evince, xarchiver, galculator,
 xfce4-taskmanager, pavucontrol
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
# ═══════════════════════════════════════════════════════════
#  sekai-installer — 설치 화면 (라이브 이미지에만 들어간다)
#  설치가 끝난 시스템에서는 설치 백엔드가 이 패키지를 지운다.
# ═══════════════════════════════════════════════════════════
echo "==> sekai-installer 스테이징"
STAGE_I="$(mktemp -d)"
ISRC="$P/src/sekai-installer"
install -Dm755 "$ISRC/sekai-installer"         "$STAGE_I/usr/bin/sekai-installer"
install -Dm755 "$ISRC/sekai-install"           "$STAGE_I/usr/sbin/sekai-install"
install -Dm755 "$ISRC/sekai-install-backend"   "$STAGE_I/usr/lib/sekai-installer/sekai-install-backend"
install -Dm644 "$ISRC/sekai-installer.desktop" "$STAGE_I/usr/share/applications/sekai-installer.desktop"
copyright "$STAGE_I" sekai-installer
mkdir -p "$STAGE_I/DEBIAN"
cat > "$STAGE_I/DEBIAN/control" <<CTRL
Package: sekai-installer
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: admin
Priority: optional
Depends: sekai-shell (= ${FULL}), sekai-desktop (= ${FULL}),
 gdisk, parted, dosfstools, e2fsprogs, squashfs-tools, efibootmgr,
 util-linux, whiptail, sudo, python3-gi
Description: SekaiOS installer
 Graphical installer used from the SekaiOS live session. Installs the
 system to a whole disk with its own EFI system partition. Account,
 region and network are configured on first boot (OOBE).
CTRL
dpkg-deb --root-owner-group --build "$STAGE_I" "$OUT/sekai-installer_${FULL}_all.deb" > /dev/null
ls -lh "$OUT/sekai-installer_${FULL}_all.deb" | awk '{print "    "$5"  "$9}'
rm -rf "$STAGE_I"

echo "==> 버전 ${FULL}"
