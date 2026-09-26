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
install -Dm755 "$SRC/sekai-taskmgr"   "$STAGE/usr/bin/sekai-taskmgr"
install -Dm755 "$SRC/sekai-admin"     "$STAGE/usr/bin/sekai-admin"
install -Dm755 "$SRC/sekai-files"     "$STAGE/usr/bin/sekai-files"
install -Dm755 "$SRC/sekai-notepad"   "$STAGE/usr/bin/sekai-notepad"
install -Dm755 "$SRC/sekai-calc"      "$STAGE/usr/bin/sekai-calc"
install -Dm755 "$SRC/sekai-photos"    "$STAGE/usr/bin/sekai-photos"
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
install -Dm755 "$SRC/lib/autostart"    "$STAGE/usr/lib/sekai/autostart"
install -Dm755 "$SRC/lib/automount"    "$STAGE/usr/lib/sekai/automount"
install -Dm755 "$SRC/lib/polkit-agent" "$STAGE/usr/lib/sekai/polkit-agent"
install -Dm755 "$SRC/lib/nm-agent"     "$STAGE/usr/lib/sekai/nm-agent"
install -Dm755 "$SRC/lib/keyring-prompter" "$STAGE/usr/lib/sekai/keyring-prompter"
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
# 컴퓨터 관리 (sekai-admin)
mkdir -p "$DIST/sekaiadmin/pages"
for f in "$SRC/sekaiadmin"/*.py;        do install -Dm644 "$f" "$DIST/sekaiadmin/$(basename "$f")"; done
for f in "$SRC/sekaiadmin/pages"/*.py;  do install -Dm644 "$f" "$DIST/sekaiadmin/pages/$(basename "$f")"; done
# 파일 탐색기 (sekai-files) · 메모장 (sekai-notepad) · 계산기 (sekai-calc) · 사진 (sekai-photos)
for pkg in sekaifiles sekainotepad sekaicalc sekaiphotos; do
    mkdir -p "$DIST/$pkg"
    for f in "$SRC/$pkg"/*.py; do install -Dm644 "$f" "$DIST/$pkg/$(basename "$f")"; done
done

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
 libglib2.0-bin, sekai-winshot, gir1.2-gudev-1.0, pulseaudio-utils,
 gir1.2-gtksource-4, gir1.2-polkit-1.0, gir1.2-gcr-4
Recommends: wireplumber, swaylock
Provides: polkit-1-auth-agent
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
    # cups-browsed 는 cups 의 권장 패키지라 업데이트 때 같이 깔린다 — 실행 조건 조각(etc/systemd/system/
    #   cups-browsed.service.d)으로 막아 두고, 이미 떠 있으면 멈춘다. (Conflicts 로 막았더니 apt 가
    #   cups-browsed 대신 sekai-desktop 을 지우려 했다)
    systemctl stop cups-browsed.service >/dev/null 2>&1 || true
    # 관리자(sudo)는 시스템 기록(이벤트 뷰어)·프린터 관리(CUPS)도 — 새 계정은 sekai-users 가 넣는다,
    #   이미 있는 관리자는 여기서 (다음 로그인부터)
    for g in systemd-journal lpadmin; do
        getent group "$g" >/dev/null || continue
        for u in $(getent group sudo | cut -d: -f4 | tr , ' '); do
            usermod -aG "$g" "$u" >/dev/null 2>&1 || true
        done
    done
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
    # 마우스 커서 기본값 — DMZ-White (앱이 따로 고르지 않을 때 쓰는 /usr/share/icons/default).
    #   한 번만 한다 — 관리자가 직접 고른 것(수동)이나 auto 로 되돌린 것을 업데이트마다 덮어쓰지 않게
    if [ ! -e /var/lib/sekai/cursor-default-set ] && [ -e /usr/share/icons/DMZ-White/cursor.theme ]; then
        if update-alternatives --query x-cursor-theme 2>/dev/null | grep -q '^Status: auto'; then
            update-alternatives --set x-cursor-theme /usr/share/icons/DMZ-White/cursor.theme >/dev/null 2>&1 || true
        fi
        mkdir -p /var/lib/sekai && touch /var/lib/sekai/cursor-default-set
    fi
    # "폴더에 표시"(org.freedesktop.FileManager1)는 파일 탐색기(sekai-files)가 맡는다. 예전 설치본에
    #   Thunar 가 남아 있으면 같은 이름을 서비스 파일 둘이 주장해 어느 쪽이 뜰지 모른다 → Thunar 것을
    #   옆 이름으로 옮겨 둔다 (dpkg-divert — Thunar 가 업데이트돼도 그 파일은 옮긴 이름으로 깔린다)
    # /etc/os-release 는 데비안처럼 /usr/lib/os-release(이 패키지의 SekaiOS 것 — preinst 가 base-files 것을
    #   옮겨 둔다)를 가리키는 링크로. 예전 이미지는 /etc/os-release 를 파일로 고쳐 두어, base-files 가
    #   업데이트되면 링크로 바뀌며 데비안으로 돌아갔다 (fastfetch·설정 › 시스템 정보·lsb_release 가 "Debian")
    if [ ! -L /etc/os-release ] && [ -f /usr/lib/os-release ] && grep -q '^ID=sekai' /usr/lib/os-release; then
        ln -sfn ../usr/lib/os-release /etc/os-release
    fi
    t=/usr/share/dbus-1/services/org.xfce.Thunar.FileManager1.service
    if ! dpkg-divert --listpackage "$t" 2>/dev/null | grep -qx sekai-desktop; then
        dpkg-divert --package sekai-desktop --rename --quiet --divert "$t.sekai-off" --add "$t" || true
    fi
    # 암호 저장소(gnome-keyring)·GPG 의 암호 창은 우리 것(/usr/lib/sekai/keyring-prompter)이 맡는다 —
    #   gcr 의 gcr-prompter 서비스 파일을 같은 방법으로 옆 이름으로 옮긴다 (같은 이름을 둘이 주장하지 않게)
    for n in SystemPrompter PrivatePrompter; do
        t=/usr/share/dbus-1/services/org.gnome.keyring.$n.service
        if ! dpkg-divert --listpackage "$t" 2>/dev/null | grep -qx sekai-desktop; then
            dpkg-divert --package sekai-desktop --rename --quiet --divert "$t.sekai-off" --add "$t" || true
        fi
    done
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
cat > "$STAGE_D/DEBIAN/preinst" <<'PRI'
#!/bin/sh
set -e
# /usr/lib/os-release 는 이 패키지가 싣는 SekaiOS 것 — base-files 의 것은 옆 이름으로 옮긴다
#   (풀기 전에 해야 두 패키지가 같은 파일을 다투지 않는다. base-files 가 업데이트돼도 옆 이름으로 깔린다)
if [ "$1" = "install" ] || [ "$1" = "upgrade" ]; then
    if ! dpkg-divert --listpackage /usr/lib/os-release 2>/dev/null | grep -qx sekai-desktop; then
        dpkg-divert --package sekai-desktop --rename --quiet --divert /usr/lib/os-release.debian --add /usr/lib/os-release
    fi
fi
PRI
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
    dpkg-divert --package sekai-desktop --rename --quiet \
        --remove /usr/share/dbus-1/services/org.xfce.Thunar.FileManager1.service 2>/dev/null || true
    for n in SystemPrompter PrivatePrompter; do
        dpkg-divert --package sekai-desktop --rename --quiet \
            --remove /usr/share/dbus-1/services/org.gnome.keyring.$n.service 2>/dev/null || true
    done
    # 설치된 디스크면 부팅 메뉴를 다시 쓴다 (없어진 테마·항목을 가리키지 않게)
    if [ -f /boot/grub/grub.cfg ] && command -v update-grub >/dev/null; then
        update-grub >/dev/null 2>&1 || true
    fi
fi
# 데비안의 os-release 를 되돌린다 (이 패키지의 것은 이미 지워졌다)
if [ "$1" = "remove" ] || [ "$1" = "purge" ] || [ "$1" = "abort-install" ]; then
    dpkg-divert --package sekai-desktop --rename --quiet --remove /usr/lib/os-release 2>/dev/null || true
fi
PO
# shim·GRUB 이 업데이트되면 ESP 의 복사본도 새것으로 (postinst "triggered")
cat > "$STAGE_D/DEBIAN/triggers" <<'TR'
interest-noawait /usr/lib/shim
interest-noawait /usr/lib/grub/x86_64-efi-signed
TR
chmod 755 "$STAGE_D/DEBIAN/preinst" "$STAGE_D/DEBIAN/postinst" "$STAGE_D/DEBIAN/prerm" "$STAGE_D/DEBIAN/postrm"

copyright "$STAGE_D" sekai-desktop
# GTK 테마(Sekai-Light · Sekai-Dark)는 Apache 2.0 이 아니라 GPL-3.0 — 원본 저작권·출처·소스 위치를 함께 적는다
#   (third_party/fluent-gtk-theme/UPSTREAM.md 와 같은 내용)
cat >> "$STAGE_D/usr/share/doc/sekai-desktop/copyright" <<'THEME'

════════════════════════════════════════════════════════════════
다음 파일은 Apache 2.0 이 아니라 GPL-3.0 입니다 — 다른 사람의 저작물을 SekaiOS 가 고친 것.

파일:     usr/share/themes/Sekai-Light/{gtk-3.0,gtk-4.0,index.theme}
          usr/share/themes/Sekai-Dark/{gtk-3.0,gtk-4.0,index.theme}
원본:     Fluent-gtk-theme, 태그 2025-04-17 (commit 76f8112ff22d81b372f7081c4fad13e9a08227de)
          https://github.com/vinceliuice/Fluent-gtk-theme
저작권:   Copyright (C) vinceliuice (Vince Liuice) and Fluent-gtk-theme contributors
          Fluent 은 Materia theme 을 바탕으로 한다:
            Copyright (C) nana-4 and Materia contributors — GPL-2.0-or-later
            https://github.com/nana-4/materia-theme
          Materia 는 GNOME 의 Adwaita 를 바탕으로 한다 (LGPL-2.1-or-later)
          기호 아이콘 일부는 Google 의 Material Design icons 바탕 (Apache-2.0)
          SekaiOS 의 수정: 강조색·창 바탕·면·제목줄 색 (2026, 고친 원본 파일 머리에 표시)
라이선스: GPL-3.0 — 전문은 /usr/share/common-licenses/GPL-3
소스:     이 파일들을 만든 원본(SCSS·SVG)과 스크립트는 SekaiOS 소스 저장소에서 받을 수 있다:
          https://github.com/caterpillar321/sekaios
          (third_party/fluent-gtk-theme, scripts/build-theme.sh)
THEME
# 이 패키지를 만든 소스의 커밋 — 옛 패키지에 맞는 소스(GPL-3 6조)를 찾을 수 있게
printf '          이 패키지를 만든 소스: 커밋 %s\n          GPL-3.0 전문은 테마 폴더의 COPYING 에도 있다 (usr/share/themes/Sekai-*/COPYING)\n' \
    "$(git -C "$P" rev-parse HEAD 2>/dev/null || echo 알수없음)" >> "$STAGE_D/usr/share/doc/sekai-desktop/copyright"

cat > "$STAGE_D/DEBIAN/control" <<CTRL
Package: sekai-desktop
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: metapackages
Priority: optional
Depends: sekai-shell (= ${FULL}),
 hyprland (>= 0.50.1-sekai11), hyprbars (>= 0.50.0-sekai11), hyprexpo, xwayland, binutils,
 xdg-desktop-portal, xdg-desktop-portal-gtk, xdg-desktop-portal-wlr,
 foot, fuzzel, swaybg, swayidle, swaylock, grim, slurp,
 brightnessctl, playerctl, wtype, pkexec, efibootmgr, open-vm-tools, mokutil, pciutils, openssl,
 shim-signed, grub-efi-amd64-signed, grub-efi-amd64-bin, grub2-common, os-prober,
 firmware-amd-graphics, firmware-intel-graphics, firmware-nvidia-graphics, firmware-misc-nonfree,
 firmware-iwlwifi, firmware-realtek, firmware-atheros, firmware-mediatek, firmware-sof-signed,
 xserver-xorg-core, xserver-xorg-video-fbdev, xserver-xorg-input-libinput, xserver-xorg-legacy,
 xinit, x11-xserver-utils, xfwm4, xfconf, sxhkd, xcape, xsecurelock, xss-lock, maim, slop, xclip,
 xdotool, xterm, gir1.2-wnck-3.0,
 wl-clipboard, cliphist, libnotify-bin, wayland-utils,
 pipewire, pipewire-audio, pipewire-pulse, wireplumber,
 network-manager, network-manager-l10n, systemd-resolved,
 wpasupplicant, wireless-regdb, iw, ntfs-3g, exfatprogs,
 bluez, wlsunset,
 cups, cups-client, cups-ipp-utils, avahi-daemon, libnss-mdns, ipp-usb,
 gvfs, gvfs-backends, udisks2, libarchive-tools,
 qt6-wayland, xdg-user-dirs,
 fonts-pretendard, fonts-dejavu, fonts-jetbrains-mono, fonts-nanum,
 fonts-noto-color-emoji, fonts-symbola,
 papirus-icon-theme, adwaita-icon-theme, dmz-cursor-theme,
 gnome-keyring, libpam-gnome-keyring, libpam-runtime,
 ibus, ibus-wayland, ibus-hangul, gir1.2-ibus-1.0, ibus-gtk3, ibus-gtk4, locales, greetd,
 plymouth (>= 24.004.60-5+sekai1), plymouth-themes,
 dbus-user-session,
 libgl1-mesa-dri, libegl-mesa0, mesa-utils
Recommends: htop, tmux, tree, ncdu, vim, nano, git, curl, wget,
 bash-completion, less, man-db,
 chromium, cups-pk-helper,
 webp-pixbuf-loader, heif-gdk-pixbuf, libavif-gdk-pixbuf,
 poppler-utils
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
