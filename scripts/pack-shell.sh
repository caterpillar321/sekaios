#!/bin/bash
# SekaiOS — sekai-shell · sekai-de · sekaios-base · sekai-desktop · sekai-installer 를 .deb 으로 포장
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
STAGE_DE="$(mktemp -d)"; STAGE_B="$(mktemp -d)"; STAGE_M="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$STAGE_DE" "$STAGE_B" "$STAGE_M"' EXIT

# 옛 빌드 정리 — 남겨 두면 finalize 의 *.deb 가 두 버전을 동시에 설치하려 한다
rm -f "$OUT"/sekai-shell_*.deb "$OUT"/sekai-de_*.deb "$OUT"/sekaios-base_*.deb "$OUT"/sekai-desktop_*.deb "$OUT"/sekai-installer_*.deb

echo "==> 스테이징"
install -Dm755 "$SRC/sekai-panel"     "$STAGE/usr/bin/sekai-panel"
install -Dm755 "$SRC/sekai-settings"  "$STAGE/usr/bin/sekai-settings"
install -Dm755 "$SRC/sekai-taskmgr"   "$STAGE/usr/bin/sekai-taskmgr"
install -Dm755 "$SRC/sekai-admin"     "$STAGE/usr/bin/sekai-admin"
install -Dm755 "$SRC/sekai-files"     "$STAGE/usr/bin/sekai-files"
install -Dm755 "$SRC/sekai-notepad"   "$STAGE/usr/bin/sekai-notepad"
install -Dm755 "$SRC/sekai-appinstall" "$STAGE/usr/bin/sekai-appinstall"
install -Dm755 "$SRC/sekai-store"     "$STAGE/usr/bin/sekai-store"
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
install -Dm755 "$SRC/lib/a11yd"        "$STAGE/usr/lib/sekai/a11yd"
install -Dm755 "$SRC/lib/polkit-agent" "$STAGE/usr/lib/sekai/polkit-agent"
install -Dm755 "$SRC/lib/nm-agent"     "$STAGE/usr/lib/sekai/nm-agent"
install -Dm755 "$SRC/lib/keyring-prompter" "$STAGE/usr/lib/sekai/keyring-prompter"
install -Dm755 "$SRC/sekai-terminal"  "$STAGE/usr/bin/sekai-terminal"
install -Dm755 "$SRC/nenerobo-bin"    "$STAGE/usr/bin/nenerobo"
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
# 파일 탐색기 (sekai-files) · 메모장 (sekai-notepad) · 계산기 (sekai-calc) · 사진 (sekai-photos) · 앱 설치 관리자 (sekai-appinstall) · 스토어 (sekai-store)
for pkg in sekaifiles sekainotepad sekaicalc sekaiphotos sekaiappinstall sekaistore nenerobo; do
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
 gir1.2-gtklayershell-0.1, worldlink, kitty, foot, fuzzel,
 adwaita-icon-theme, papirus-icon-theme, swaybg, swayidle,
 gir1.2-gtksessionlock-0.1, libgtk-session-lock0, python3-pampy,
 libglib2.0-bin, sekai-winshot, gir1.2-gudev-1.0, pulseaudio-utils,
 gir1.2-gtksource-4, gir1.2-polkit-1.0, gir1.2-gcr-4,
 appstream, gir1.2-appstream-1.0, gir1.2-gtk-4.0, gir1.2-vte-3.91,
 fonts-jetbrains-mono, fonts-nanum
Recommends: wireplumber, swaylock, gir1.2-flatpak-1.0
Provides: polkit-1-auth-agent, notification-daemon
Description: SekaiOS desktop shell
 Panel, taskbar, start menu and the system settings app for
 SekaiOS, built on gtk-layer-shell and the WorldLink (Hyprland) IPC.
EOF

echo "==> sekai-shell .deb 생성"
mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$STAGE" \
         "$OUT/sekai-shell_${FULL}_all.deb" > /dev/null
ls -lh "$OUT/sekai-shell_${FULL}_all.deb" | awk '{print "    "$5"  "$9}'

# ═══════════════════════════════════════════════════════════
#  SekaiDE 와 SekaiOS 를 나눈다 (2026-10-01)
#    sekai-de      데스크톱 환경 — 세션·로그인 화면·잠금·첫 설정·합성기 설정·테마·앱 메뉴 (src/sekai-de)
#    sekaios-base  배포판 — os-release·부팅(GRUB·Plymouth·initramfs)·업데이트 저장소·그래픽 드라이버 도구 (src/sekaios-base)
#    sekai-desktop 메타패키지 — sekai-de + sekaios-base + 기본 앱. 예전엔 위 둘의 파일을 직접 실었다
#  두 패키지는 옛 sekai-desktop 의 파일을 넘겨받는다 (Replaces/Breaks: sekai-desktop (<< 이번 판)).
#  옛 sekai-desktop 이름으로 걸어 둔 dpkg-divert 는 새 주인으로 옮긴다 (파일은 그 자리에 둔 채 --no-rename).
# ═══════════════════════════════════════════════════════════

# 소스 폴더를 그대로 스테이징 — 실행 파일 권한, /etc 는 conffile (사용자가 고친 건 업그레이드 때 보존된다)
stage_tree() {
    local src="$1" stage="$2"
    ( cd "$src" && find . -type f ) | while read -r f; do
        f="${f#./}"
        mode=644
        case "$f" in usr/bin/*|usr/sbin/*|usr/libexec/*|etc/kernel/postinst.d/*|etc/initramfs/post-update.d/*|etc/grub.d/*|etc/update-motd.d/*|usr/share/initramfs-tools/hooks/*|usr/share/initramfs-tools/scripts/*) mode=755 ;; esac
        install -Dm$mode "$src/$f" "$stage/$f"
    done
    # 심볼릭 링크는 링크 그대로 (커서 테마의 이름 잇기 등)
    ( cd "$src" && find . -type l ) | while read -r f; do
        f="${f#./}"
        mkdir -p "$stage/$(dirname "$f")"
        cp -P "$src/$f" "$stage/$f"
    done
    mkdir -p "$stage/DEBIAN"
    ( cd "$stage" && find etc -type f 2>/dev/null | sed 's|^|/|' ) > "$stage/DEBIAN/conffiles"
    [ -s "$stage/DEBIAN/conffiles" ] || rm -f "$stage/DEBIAN/conffiles"
}

# 떠 있는 합성기 세션(사용자·로그인 화면)마다 hyprctl 을 부른다 — 메인테이너 스크립트에 넣는 조각
HYPR_EACH='
# hypr_each <hyprctl 인자…>
hypr_each() {
    for sock in /run/user/*/hypr/*/.socket.sock; do
        [ -S "$sock" ] || continue
        d=${sock%/.socket.sock}; sig=${d##*/}; rt=${d%/hypr/*}; uid=${rt##*/}
        u=$(getent passwd "$uid" | cut -d: -f1)
        [ -n "$u" ] || continue
        runuser -u "$u" -- env XDG_RUNTIME_DIR="$rt" HYPRLAND_INSTANCE_SIGNATURE="$sig" \
            hyprctl "$@" >/dev/null 2>&1 || true
    done
}'
# 업데이트가 합성기 설정 파일을 지우기 전에 — 떠 있는 합성기는 읽은 설정 파일이 "지워지면"(inotify IN_IGNORED)
#   스스로 다시 읽는다. 옛 판(설정을 싣던 sekai-desktop)에서 올릴 때 새 sekai-desktop 이 먼저 풀리면 그 파일이
#   sekai-de 가 다시 놓을 때까지 없어서, 그 순간 다시 읽다가 "source= … globbing error: found no match" 오류 막대가 떴다
#   (2026-10-02 실기, VM 에서 같은 오류 재현). 하드 링크를 걸어 두면 경로가 지워져도 파일(inode)이 남아 신호가 없다.
#   (hyprctl keyword misc:disable_autoreload 는 다음 reload 까지 감시를 풀지 않아 소용없다)
#   끝에서 hyprctl reload 로 감시를 새 파일로 옮긴 뒤 치운다 (HYPR_KEEP_DROP)
HYPR_KEEP='
K=/usr/share/sekai/hypr/.sekai-keep
if [ -d /usr/share/sekai/hypr ]; then
    mkdir -p "$K"
    for f in /usr/share/sekai/hypr/*; do
        [ -f "$f" ] && ln -f "$f" "$K/" 2>/dev/null || true
    done
fi'
HYPR_KEEP_DROP='rm -rf /usr/share/sekai/hypr/.sekai-keep'

# 옛 sekai-desktop 이 걸어 둔 divert 를 새 주인에게 (파일은 이미 옮겨진 자리에 그대로) — 메인테이너 스크립트에 넣는 조각
DIVERT_TAKEOVER='
# divert_takeover <새 주인> <원래 경로> <옮긴 경로>
divert_takeover() {
    owner=$(dpkg-divert --listpackage "$2" 2>/dev/null || true)
    [ "$owner" = "$1" ] && return 0
    if [ "$owner" = sekai-desktop ]; then
        dpkg-divert --package sekai-desktop --no-rename --quiet --remove "$2"
        dpkg-divert --package "$1" --no-rename --quiet --divert "$3" --add "$2"
    elif [ -z "$owner" ]; then
        dpkg-divert --package "$1" --rename --quiet --divert "$3" --add "$2"
    fi
}'

# ─────────────────────────────────────────────────────────
#  sekai-de — SekaiDE (데스크톱 환경)
# ─────────────────────────────────────────────────────────
echo "==> sekai-de 스테이징"
stage_tree "$P/src/sekai-de" "$STAGE_DE"
# 번역 원본(.po) → .mo (msgfmt 없이) — 데비안에 한국어가 없는 것을 SekaiOS 가 채운다 (postinst 가 이어 붙인다)
find "$STAGE_DE/usr/share/sekai/locale" -name '*.po' 2>/dev/null | while read -r po; do
    python3 "$SELF/po2mo.py" "$po" "${po%.po}.mo" && chmod 644 "${po%.po}.mo" && rm -f "$po"
done
cat > "$STAGE_DE/DEBIAN/preinst" <<PRI
#!/bin/sh
set -e
$HYPR_KEEP
PRI
cat > "$STAGE_DE/DEBIAN/postinst" <<PI
#!/bin/sh
set -e
$DIVERT_TAKEOVER
$HYPR_EACH
PI
cat >> "$STAGE_DE/DEBIAN/postinst" <<'PI'
if [ "$1" = "configure" ] && command -v pam-auth-update >/dev/null; then
    # 로그인 때 GNOME 키링을 풀도록 PAM 에 반영 (안 풀리면 크로미움이 켤 때마다 '키링 암호' 창을 띄운다)
    pam-auth-update --package
fi
if [ "$1" = "configure" ]; then
    # 그래픽 로그인 화면으로 부팅한다 (greetd = display-manager)
    [ -d /run/systemd/system ] && systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable greetd.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    # 스토어 앱 목록 — 없으면 이 업데이트가 끝난 뒤 뒤에서 받는다 (부팅 때도 없을 때만)
    systemctl enable sekai-store-catalog.service >/dev/null 2>&1 || true
    [ -d /run/systemd/system ] && systemctl start --no-block sekai-store-catalog.service >/dev/null 2>&1 || true
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
    # 펌웨어 화면 장치 권한 규칙(70-sekai-fb.rules)을 지금 바로 적용
    if [ -d /run/systemd/system ] && command -v udevadm >/dev/null; then
        udevadm control --reload >/dev/null 2>&1 || true
        udevadm trigger --subsystem-match=graphics >/dev/null 2>&1 || true
    fi
    # 글꼴 — 데비안 fontconfig 의 "서브픽셀 끔"을 "자동"으로 (한 번만 — 사용자가 dpkg-reconfigure 로 다시 끄면 그대로 둔다).
    #   끔이면 fontconfig 가 먼저 rgba=none 을 정해, GSettings 의 ClearType(font-antialiasing=rgba)을 GTK·크로미움이 못 쓴다.
    #   자동이면 앱이 GSettings 대로 한다 (설정 › 개인 설정 › 색 및 모양 › 글꼴 다듬기)
    if [ ! -e /var/lib/sekai/.fontconfig-subpixel-auto ]; then
        if command -v debconf-set-selections >/dev/null; then
            echo "fontconfig-config fontconfig/subpixel_rendering select Automatic" | debconf-set-selections || true
        fi
        [ -L /etc/fonts/conf.d/10-sub-pixel-none.conf ] && rm -f /etc/fonts/conf.d/10-sub-pixel-none.conf
        mkdir -p /var/lib/sekai && : > /var/lib/sekai/.fontconfig-subpixel-auto
    fi
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
    t=/usr/share/dbus-1/services/org.xfce.Thunar.FileManager1.service
    divert_takeover sekai-de "$t" "$t.sekai-off"
    # 암호 저장소(gnome-keyring)·GPG 의 암호 창은 우리 것(/usr/lib/sekai/keyring-prompter)이 맡는다 —
    #   gcr 의 gcr-prompter 서비스 파일을 같은 방법으로 옆 이름으로 옮긴다 (같은 이름을 둘이 주장하지 않게)
    for n in SystemPrompter PrivatePrompter; do
        t=/usr/share/dbus-1/services/org.gnome.keyring.$n.service
        divert_takeover sekai-de "$t" "$t.sekai-off"
    done
    # 떠 있는 합성기 세션(사용자·로그인 화면)마다 설정을 다시 읽게 한다.
    #   업데이트 중 파일이 잠깐 없어지는 틈(옛 sekai-desktop 이 내려놓고 이 패키지가 다시 놓기 전)에 합성기가
    #   스스로 설정을 다시 읽으면 "source= ... found no match" 오류 막대가 뜨고 기본 모양으로 굳었다 (2026-10-01,
    #   0928 설치본을 로그인한 채 업데이트해서 확인). 여기선 파일이 모두 제자리에 있다
    hypr_each reload
    rm -rf /usr/share/sekai/hypr/.sekai-keep   # preinst 가 걸어 둔 하드 링크 (HYPR_KEEP) — reload 로 감시를 옮긴 뒤
    # 데비안에 한국어가 없는 번역을 시스템 자리에 잇는다 (/usr/share/sekai/locale/<언어>/<도메인>.mo) — 그 자리를
    #   다른 패키지가 가지면(데비안이 나중에 넣으면) 건드리지 않는다. 포털 파일 고르기 창이 영어로 보이던 것
    for mo in /usr/share/sekai/locale/*/*.mo; do
        [ -e "$mo" ] || continue
        lang=$(basename "$(dirname "$mo")"); t="/usr/share/locale/$lang/LC_MESSAGES/$(basename "$mo")"
        if [ -L "$t" ] || { [ ! -e "$t" ] && ! dpkg-query -S "$t" >/dev/null 2>&1; }; then
            mkdir -p "$(dirname "$t")" && ln -sfn "$mo" "$t"
        fi
    done
    # foot 패키지가 graphical-session.target 에 걸어 둔 foot 서버 — SekaiOS 세션이 그 타깃을 켜면서부터
    #   쓰지도 않는 서버가 떴다 (터미널은 Nenerobo, foot 은 예비로 그냥 켠다)
    systemctl --global disable foot-server.service foot-server.socket >/dev/null 2>&1 || true
    # 기본 터미널 — "터미널에서 실행"하는 앱(x-terminal-emulator -e …)도 Nenerobo 로 (kitty 는 20 쯤이다)
    if [ -x /usr/bin/nenerobo ]; then
        update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/nenerobo 60 \
            >/dev/null 2>&1 || true
    fi
fi
PI
cat > "$STAGE_DE/DEBIAN/prerm" <<'PR'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && command -v pam-auth-update >/dev/null; then
    pam-auth-update --package --remove sekai-gnome-keyring
fi
if [ "$1" = "remove" ]; then
    update-alternatives --remove x-terminal-emulator /usr/bin/nenerobo >/dev/null 2>&1 || true
fi
PR
cat > "$STAGE_DE/DEBIAN/postrm" <<'PO'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    # postinst 가 이은 번역 링크 (우리 것을 가리키는 것만)
    for t in /usr/share/locale/*/LC_MESSAGES/*.mo; do
        [ -L "$t" ] && case "$(readlink "$t")" in /usr/share/sekai/locale/*) rm -f "$t" ;; esac
    done
    dpkg-divert --package sekai-de --rename --quiet \
        --remove /usr/share/dbus-1/services/org.xfce.Thunar.FileManager1.service 2>/dev/null || true
    for n in SystemPrompter PrivatePrompter; do
        dpkg-divert --package sekai-de --rename --quiet \
            --remove /usr/share/dbus-1/services/org.gnome.keyring.$n.service 2>/dev/null || true
    done
fi
PO
chmod 755 "$STAGE_DE/DEBIAN/preinst" "$STAGE_DE/DEBIAN/postinst" "$STAGE_DE/DEBIAN/prerm" "$STAGE_DE/DEBIAN/postrm"

copyright "$STAGE_DE" sekai-de
# GTK 테마(Sekai-Light · Sekai-Dark)는 Apache 2.0 이 아니라 GPL-3.0 — 원본 저작권·출처·소스 위치를 함께 적는다
#   (third_party/fluent-gtk-theme/UPSTREAM.md 와 같은 내용)
cat >> "$STAGE_DE/usr/share/doc/sekai-de/copyright" <<'THEME'

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

참고:     usr/share/themes/Sekai-Contrast 는 SekaiOS 가 쓴 것(Apache 2.0)으로, GTK 에 들어 있는 고대비 테마를
          불러 쓴다 (GTK 의 파일을 담지 않음). usr/share/icons/Sekai-Cursor-{White,Black} 은 dmz-cursor-theme 의
          커서 그림으로 가는 링크와 index.theme 뿐이다 — 그림은 담지 않으며 그 라이선스는 dmz-cursor-theme 의 것.
THEME
# 이 패키지를 만든 소스의 커밋 — 옛 패키지에 맞는 소스(GPL-3 6조)를 찾을 수 있게
printf '          이 패키지를 만든 소스: 커밋 %s\n          GPL-3.0 전문은 테마 폴더의 COPYING 에도 있다 (usr/share/themes/Sekai-{Light,Dark}/COPYING)\n' \
    "$(git -C "$P" rev-parse HEAD 2>/dev/null || echo 알수없음)" >> "$STAGE_DE/usr/share/doc/sekai-de/copyright"

cat > "$STAGE_DE/DEBIAN/control" <<CTRL
Package: sekai-de
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: x11
Priority: optional
Depends: sekai-shell (= ${FULL}),
 worldlink (>= 0.50.1-sekai34), hyprbars (>= 0.50.0-sekai31), hyprexpo, xwayland,
 xdg-desktop-portal, xdg-desktop-portal-gtk, xdg-desktop-portal-wlr,
 swaybg, swayidle, swaylock, grim, slurp, wl-clipboard, cliphist, libnotify-bin,
 brightnessctl, playerctl, wtype, pkexec,
 xserver-xorg-core, xserver-xorg-video-fbdev, xserver-xorg-input-libinput, xserver-xorg-legacy,
 xinit, xauth, x11-xserver-utils, xfwm4, xfconf, sxhkd, xcape, xsecurelock, xss-lock, maim, slop, xclip,
 xdotool, gir1.2-wnck-3.0,
 greetd, gnome-keyring, libpam-gnome-keyring, libpam-runtime, dbus-user-session,
 ibus, ibus-wayland, ibus-hangul, gir1.2-ibus-1.0, ibus-gtk3, ibus-gtk4,
 fonts-pretendard, fonts-nanum, fonts-jetbrains-mono, fonts-noto-color-emoji,
 papirus-icon-theme, adwaita-icon-theme, dmz-cursor-theme,
 gvfs, gvfs-backends, udisks2, libarchive-tools, xdg-user-dirs, wvkbd,
 orca, speech-dispatcher-espeak-ng
Replaces: sekai-desktop (<< ${FULL})
Breaks: sekai-desktop (<< ${FULL})
Description: SekaiDE - the SekaiOS desktop environment
 The desktop session of SekaiOS: login screen, lock screen, first-boot
 setup, compositor configuration for WorldLink, GTK themes, icons,
 wallpapers, menus and the helpers behind them. Pulls in sekai-shell
 (taskbar, start menu, settings and apps) and the WorldLink compositor.
 Works on its own on Debian 13; SekaiOS adds sekaios-base on top.
CTRL

# ─────────────────────────────────────────────────────────
#  sekaios-base — SekaiOS 배포판 부품
# ─────────────────────────────────────────────────────────
echo "==> sekaios-base 스테이징"
stage_tree "$P/src/sekaios-base" "$STAGE_B"
cat > "$STAGE_B/DEBIAN/preinst" <<PRI
#!/bin/sh
set -e
$DIVERT_TAKEOVER
PRI
cat >> "$STAGE_B/DEBIAN/preinst" <<'PRI'
# /usr/lib/os-release 는 이 패키지가 싣는 SekaiOS 것 — base-files 의 것은 옆 이름으로 옮긴다
#   (풀기 전에 해야 한다: divert 의 주인 패키지 파일만 제자리에 풀린다. 옛 sekai-desktop 이 주인이면 넘겨받는다)
if [ "$1" = "install" ] || [ "$1" = "upgrade" ]; then
    divert_takeover sekaios-base /usr/lib/os-release /usr/lib/os-release.debian
fi
PRI
cat > "$STAGE_B/DEBIAN/postinst" <<'PI'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    # 설치본의 /etc/motd 에 남은 설치 USB 안내("sudo sekai-install")를 머리글만으로 — 안내는 이제
    #   /etc/update-motd.d/20-sekai-live 가 라이브로 켰을 때만 보인다
    if [ ! -d /run/live/medium ] && [ -f /etc/motd ] && grep -q "sudo sekai-install" /etc/motd; then
        printf '  SekaiOS 1.0 (Hatsune)  -  based on Debian 13 (trixie)\n\n' > /etc/motd
    fi
    [ -d /run/systemd/system ] && systemctl daemon-reload >/dev/null 2>&1 || true
    # 부팅 화면·콘솔을 바탕화면과 같은 모니터 모드로 (sekai-bootmode — 모드가 바뀔 때마다 신호가 끊긴다)
    systemctl enable sekai-bootmode.path >/dev/null 2>&1 || true
    # 시스템 복원 — btrfs 로 설치된 PC 에서만 일한다 (ext4 면 조건이 맞지 않아 그냥 지나간다)
    #   snapper 가 스스로 켜 두는 "켤 때마다 지점"·시간별 지점 타이머는 어느 PC 에서나 끈다 — ext4 에선 설정이
    #   없어 켤 때마다 오류를 남기고, btrfs 에선 공간만 붙잡는다 (지점은 sekai-restore 가 만든다)
    systemctl disable --now snapper-boot.timer snapper-timeline.timer >/dev/null 2>&1 || true
    systemctl enable sekai-restore-grub.path sekai-restore-grub.service >/dev/null 2>&1 || true
    # 자동 복구 — 연달아 끝까지 못 켜면 복구 화면 (sekai-rescue). 비상·복구 모드도 그 화면 (service.d 조각)
    systemctl enable sekai-bootcheck.service sekai-bootok.timer >/dev/null 2>&1 || true
    # 드라이브 최적화 — btrfs 면 한 달에 한 번 데이터 검사 (ext4 면 조건이 맞지 않아 지나간다)
    systemctl enable sekai-scrub.timer >/dev/null 2>&1 || true
    # 지난번 커널 패닉 기록 확인 (pstore) — 로그인하면 패널이 알린다. 패닉 뒤 자동 재시작은 sysctl.d/60-sekai-panic.conf
    systemctl enable sekai-crashcheck.service >/dev/null 2>&1 || true
    [ -d /run/systemd/system ] && sysctl -q -p /etc/sysctl.d/60-sekai-panic.conf >/dev/null 2>&1 || true
    if [ ! -d /run/live/medium ] && [ ! -f /etc/snapper/configs/root ] \
       && [ "$(findmnt -no FSTYPE,FSROOT / 2>/dev/null)" = "btrfs /@" ] && mountpoint -q /.snapshots; then
        /usr/libexec/sekai/sekai-restore setup >/dev/null 2>&1 || true
    fi
    # ext4 등 — 파일 복사 방식의 저장소만 만든다 (첫 지점은 주간 타이머가 한가할 때 만든다 — 여기서 복사하면
    #   업데이트가 몇 분 멈춘다). 설치 이미지를 만드는 chroot 에선 하지 않는다
    if [ ! -d /run/live/medium ] && [ ! -d /.sekai-restore ] && [ -d /run/systemd/system ] \
       && [ "$(findmnt -no FSTYPE / 2>/dev/null)" = ext4 ]; then
        /usr/libexec/sekai/sekai-restore setup >/dev/null 2>&1 || true
    fi
    [ -d /run/systemd/system ] && systemctl start sekai-restore-grub.path >/dev/null 2>&1 || true
    # 업데이트로 받은 설치본은 부팅 메뉴(grub.cfg)에 "시스템 복원" 항목이 아직 없다 — 한 번 다시 만든다
    if [ ! -d /run/live/medium ] && [ -s /boot/grub/grub.cfg ] && ! grep -q "41_sekai-restore" /boot/grub/grub.cfg \
       && { [ -d /.sekai-restore/points ] || [ "$(findmnt -no FSTYPE,FSROOT / 2>/dev/null)" = "btrfs /@" ]; }; then
        /usr/libexec/sekai/update-grub-locked >/dev/null 2>&1 || true
        /usr/libexec/sekai/sekai-restore grub >/dev/null 2>&1 || true
    fi
    [ -d /run/systemd/system ] && systemctl start sekai-bootmode.path >/dev/null 2>&1 || true
    # cups-browsed 는 cups 의 권장 패키지라 업데이트 때 같이 깔린다 — 실행 조건 조각(etc/systemd/system/
    #   cups-browsed.service.d)으로 막아 두고, 이미 떠 있으면 멈춘다. (Conflicts 로 막았더니 apt 가
    #   cups-browsed 대신 sekai-desktop 을 지우려 했다)
    systemctl stop cups-browsed.service >/dev/null 2>&1 || true
    # SSH 호스트 키가 없는 설치본(이미지에서 지운 키를 첫 부팅이 못 만든 경우) — 여기서 만든다.
    #   라이브·이미지 만드는 중(chroot)엔 하지 않는다: 키가 이미지에 박히면 모든 설치본이 같은 키를 쓴다
    if [ -x /usr/sbin/sshd ] && [ ! -d /run/live/medium ] && [ -d /run/systemd/system ] \
       && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1; then
        ssh-keygen -A >/dev/null 2>&1 || true
        systemctl restart ssh.socket >/dev/null 2>&1 || true
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
        # 아주 예전 판(sekai-desktop)이 옮겨 둔 것 되돌리기
        if dpkg-divert --listpackage "/etc/grub.d/$f" 2>/dev/null | grep -qx sekai-desktop; then
            dpkg-divert --package sekai-desktop --rename --quiet --remove "/etc/grub.d/$f" || true
        fi
        if ! dpkg-statoverride --list "/etc/grub.d/$f" >/dev/null 2>&1; then
            dpkg-statoverride --update --add root root 0644 "/etc/grub.d/$f" 2>/dev/null || true
        fi
    done
    rmdir /usr/share/sekai/grub 2>/dev/null || true
    # /etc/os-release 는 데비안처럼 /usr/lib/os-release(이 패키지의 SekaiOS 것 — preinst 가 base-files 것을
    #   옮겨 둔다)를 가리키는 링크로. 예전 이미지는 /etc/os-release 를 파일로 고쳐 두어, base-files 가
    #   업데이트되면 링크로 바뀌며 데비안으로 돌아갔다 (fastfetch·설정 › 시스템 정보·lsb_release 가 "Debian")
    if [ ! -L /etc/os-release ] && [ -f /usr/lib/os-release ] && grep -q '^ID=sekai' /usr/lib/os-release; then
        ln -sfn ../usr/lib/os-release /etc/os-release
    fi
    # Flathub — 스토어가 데비안 저장소에 없는 앱(Discord·VS Code·Spotify·Steam 등)을 여기서 받는다.
    #   처음 한 번만 등록한다 (사용자가 지웠으면 업데이트 때 되살리지 않는다). 저장소 파일(서명 키 포함)을
    #   패키지에 실어 두어 인터넷 없이(이미지 만드는 중) 등록된다. 앱 목록은 첫 새로 고침 때 받는다.
    #   번역은 SekaiOS 가 고를 수 있는 언어로 — flatpak 은 시스템 기본 언어(en)만 받아서 앱이 영어로 떴다
    # Flatpak 앱이 사용자 GTK4 CSS(~/.config/gtk-4.0 — SekaiOS 의 윈도우 11 식 창 단추)를 읽게 (읽기만, 한 번만)
    if command -v flatpak >/dev/null && [ ! -e /var/lib/sekai/flatpak-gtk4-css ]; then
        flatpak override --system --filesystem=xdg-config/gtk-4.0:ro >/dev/null 2>&1 \
            && mkdir -p /var/lib/sekai && touch /var/lib/sekai/flatpak-gtk4-css || true
    fi
    if command -v flatpak >/dev/null && [ ! -e /var/lib/sekai/flathub-added ]; then
        flatpak remote-add --system --if-not-exists --from flathub /usr/share/sekaios/flathub.flatpakrepo \
            >/dev/null 2>&1 && mkdir -p /var/lib/sekai && touch /var/lib/sekai/flathub-added || true
        flatpak config --system --set languages "ko;en;ja" >/dev/null 2>&1 || true
    fi
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
cat > "$STAGE_B/DEBIAN/postrm" <<'PO'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    # 데비안 부팅 항목을 되살리고, 우리 항목은 끈다 (conffile 이라 purge 전까진 남는다)
    for f in 10_linux 30_uefi-firmware; do
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
# 데비안의 os-release 를 되돌린다 (이 패키지의 것은 이미 지워졌다)
if [ "$1" = "remove" ] || [ "$1" = "purge" ] || [ "$1" = "abort-install" ]; then
    dpkg-divert --package sekaios-base --rename --quiet --remove /usr/lib/os-release 2>/dev/null || true
fi
PO
# shim·GRUB 이 업데이트되면 ESP 의 복사본도 새것으로 (postinst "triggered")
cat > "$STAGE_B/DEBIAN/triggers" <<'TR'
interest-noawait /usr/lib/shim
interest-noawait /usr/lib/grub/x86_64-efi-signed
TR
chmod 755 "$STAGE_B/DEBIAN/preinst" "$STAGE_B/DEBIAN/postinst" "$STAGE_B/DEBIAN/postrm"
copyright "$STAGE_B" sekaios-base

cat > "$STAGE_B/DEBIAN/control" <<CTRL
Package: sekaios-base
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: admin
Priority: optional
Depends: shim-signed, grub-efi-amd64-signed, grub-efi-amd64-bin, grub2-common, os-prober,
 efibootmgr, mokutil, pciutils, openssl,
 plymouth (>= 24.004.60-5+sekai1), plymouth-themes,
 network-manager, systemd-resolved, flatpak, btrfs-progs, snapper
Replaces: sekai-desktop (<< ${FULL})
Breaks: sekai-desktop (<< ${FULL})
Description: SekaiOS base system
 The distribution side of SekaiOS: os-release and branding, the boot
 chain (shim + GRUB menu, Plymouth boot splash, initramfs and kernel
 hooks), the signed SekaiOS update repository with its key, the update
 helper, the Flathub remote and the NVIDIA driver installer behind
 Settings > Graphics.
CTRL

# ─────────────────────────────────────────────────────────
#  sekai-desktop — 메타패키지 (파일 없음)
#  "SekaiOS 데스크탑이 되려면 무엇이 깔려 있어야 하는가" 의 정의.
#  옛 설치본도  apt install ./sekai-desktop_*.deb  한 번이면 빠진 패키지가 전부 따라 들어온다.
# ─────────────────────────────────────────────────────────
echo "==> sekai-desktop (메타패키지)"
mkdir -p "$STAGE_M/DEBIAN"
copyright "$STAGE_M" sekai-desktop
cat > "$STAGE_M/DEBIAN/control" <<CTRL
Package: sekai-desktop
Version: ${FULL}
Architecture: all
Maintainer: SekaiOS <sekai@localhost>
Section: metapackages
Priority: optional
Depends: sekai-de (= ${FULL}), sekaios-base (= ${FULL}), sekai-shell (= ${FULL}),
 binutils, foot, fuzzel, xterm,
 firmware-amd-graphics, firmware-intel-graphics, firmware-nvidia-graphics, firmware-misc-nonfree,
 firmware-iwlwifi, firmware-realtek, firmware-atheros, firmware-mediatek, firmware-sof-signed,
 open-vm-tools,
 pipewire, pipewire-audio, pipewire-pulse, wireplumber,
 network-manager-l10n, wpasupplicant, wireless-regdb, iw, ntfs-3g, exfatprogs,
 bluez, wlsunset,
 cups, cups-client, cups-ipp-utils, avahi-daemon, libnss-mdns, ipp-usb,
 qt6-wayland, wayland-utils, fonts-dejavu, fonts-symbola, locales,
 libgl1-mesa-dri, libegl-mesa0, mesa-utils
Recommends: htop, tmux, tree, ncdu, vim, nano, git, curl, wget,
 bash-completion, less, man-db,
 chromium, cups-pk-helper,
 webp-pixbuf-loader, heif-gdk-pixbuf, libavif-gdk-pixbuf,
 poppler-utils
Conflicts: fnott
Description: SekaiOS desktop (metapackage)
 Everything that makes up SekaiOS: the SekaiDE desktop environment
 (sekai-de, sekai-shell, WorldLink), the SekaiOS base system
 (sekaios-base) and the default set of drivers, firmware, printing,
 sound, network and applications.
CTRL

# 옛 판(합성기 설정을 싣던 sekai-desktop)에서 올릴 때 이 패키지가 먼저 풀리면 /usr/share/sekai/hypr 의 파일이
#   sekai-de 가 다시 놓을 때까지 잠깐 없다 — 그 전에 떠 있는 세션의 자동 재적용을 끄고, 끝(모든 부품이 제자리)에 다시 읽힌다
cat > "$STAGE_M/DEBIAN/preinst" <<PRI
#!/bin/sh
set -e
$HYPR_KEEP
PRI
cat > "$STAGE_M/DEBIAN/postinst" <<PI
#!/bin/sh
set -e
$HYPR_EACH
if [ "\$1" = configure ]; then
    hypr_each reload
    $HYPR_KEEP_DROP
fi
exit 0
PI
chmod 755 "$STAGE_M/DEBIAN/preinst" "$STAGE_M/DEBIAN/postinst"

for pkg in sekai-de sekaios-base sekai-desktop; do
    case "$pkg" in sekai-de) st="$STAGE_DE" ;; sekaios-base) st="$STAGE_B" ;; *) st="$STAGE_M" ;; esac
    echo "==> $pkg .deb 생성"
    dpkg-deb --root-owner-group --build "$st" "$OUT/${pkg}_${FULL}_all.deb" > /dev/null
    ls -lh "$OUT/${pkg}_${FULL}_all.deb" | awk '{print "    "$5"  "$9}'
done
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
