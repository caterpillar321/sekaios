# SekaiOS — 하드웨어별 보정 (sekai-session 과 sekai-greeter-session 이 함께 읽는다)
if [ "$(systemd-detect-virt 2>/dev/null)" = "vmware" ]; then
    # vmwgfx 가 원자적(atomic) 모드 설정으로 해상도를 키우는 걸 거부한다
    export AQ_NO_ATOMIC=1
    # GTK4 의 GL 렌더러 버퍼를 VMware 가상 GPU 에서 컴포지터가 거부한다
    export GSK_RENDERER=cairo
fi

# ── 그래픽 가속 확인 ─────────────────────────────────────
#   GPU 드라이버가 3D 가속을 못 하면 (기본 드라이버만 있는 NVIDIA, 3D 없는 가상 머신 등)
#   Hyprland 가 그래픽 초기화에서 죽어 까만 화면이 된다.
#   → 미리 확인해서 CPU 렌더링(llvmpipe)으로 띄운다. 느리지만 화면은 나온다.
#   Mesa 는 가속이 없으면 스스로 llvmpipe 로 내려가므로 렌더러 이름으로 알 수 있다.
sekai_gpu_check() {
    [ -n "${LIBGL_ALWAYS_SOFTWARE:-}" ] && return 0      # 이미 정해졌으면 그대로
    ls /dev/dri/card* >/dev/null 2>&1 || return 0        # DRM 장치가 아예 없음 → 기본 화면 모드의 몫
    command -v eglinfo >/dev/null 2>&1 || return 0
    r=$(timeout 8 eglinfo -B -p gbm 2>/dev/null | sed -n 's/^OpenGL ES profile renderer: //p' | head -1)
    case "$r" in
        ""|*llvmpipe*|*softpipe*|*swrast*|*SWR*)
            # 둘 다 있어야 한다: CPU 로 그리고(LIBGL_ALWAYS_SOFTWARE),
            #   화면 장치는 "출력만" 쓰게(kms_swrast — 어떤 모드 설정 드라이버와도 동작).
            #   하나만 주면 Hyprland 가 GPU 드라이버로 EGL 을 열다가 그대로 죽는다.
            export LIBGL_ALWAYS_SOFTWARE=1
            export MESA_LOADER_DRIVER_OVERRIDE=kms_swrast
            export SEKAI_SOFTWARE_RENDER=1
            # GTK4 도 GL 대신 cairo 로 (CPU 렌더링 위에 GL 을 또 돌리면 더 느리다)
            export GSK_RENDERER=cairo
            ;;
    esac
}
sekai_gpu_check

# ── 여러 그래픽 카드 ─────────────────────────────────────
#   Hyprland 는 기본으로 첫 번째 카드(card0)로 그린다. 그런데 card0 가 모니터 없는
#   내장 GPU 나 보조 카드일 수 있다 (예: 내장 AMD + RTX 5090 + Quadro).
#   → 모니터가 꽂힌 카드만, 펌웨어가 화면을 띄운 카드(boot_vga)를 앞에 두어 넘긴다.
#   (카드 목록은 콜론으로 나누므로 by-path 대신 /dev/dri/cardN 을 쓴다)
#   모니터가 아직 없는 카드도 화면 단자가 있으면 뒤에 붙인다 — 로그인 뒤 그 카드에 꽂은 모니터를 합성기가 그 카드를
#   안 보고 있어 못 켰다 (하이브리드 노트북의 HDMI 는 보통 NVIDIA 에 직결 — TUF A15, 2026-10-08. 데스크톱도
#   메인보드 단자·보조 카드에 나중에 꽂으면 같았다). 넘겨도 화면을 안 내보내는 동안 NVIDIA 는 그대로 잠든다(D3, 실기).
#   빈 카드 때문에 합성기가 못 뜨면 세션이 모니터가 꽂힌 카드만(SEKAI_AQ_SAFE)으로 다시 띄운다 (sekai-session ·
#   sekai-greeter-session) — 그래도 안 되면 기본 화면 모드.
sekai_gpu_pick() {
    [ -n "${AQ_DRM_DEVICES:-}" ] && return 0            # 사용자가 정했으면 그대로
    first="" rest="" idle="" inner="" n=0
    for c in /dev/dri/card[0-9]*; do
        [ -e "$c" ] || continue
        n=$((n + 1))
        k=${c##*/}
        if ! grep -qx connected /sys/class/drm/"$k"-*/status 2>/dev/null; then
            # 모니터 없음 — 화면 단자(쓰기 되돌림 Writeback 말고)가 있으면 뒤에
            if ls /sys/class/drm/"$k"-* 2>/dev/null | grep -qv -- '-Writeback-'; then
                idle="$idle${idle:+:}$c"
            fi
            continue
        fi
        if [ -z "$inner" ] && cat /sys/class/drm/"$k"-eDP-*/status /sys/class/drm/"$k"-LVDS-*/status \
                /sys/class/drm/"$k"-DSI-*/status 2>/dev/null | grep -qx connected; then
            inner=$c
        elif [ -z "$first" ] && [ "$(cat /sys/class/drm/"$k"/device/boot_vga 2>/dev/null)" = 1 ]; then
            first=$c
        else
            rest="$rest${rest:+:}$c"
        fi
    done
    # 노트북 내장 화면(eDP·LVDS·DSI)이 달린 카드를 맨 앞에 — GNOME(mutter)·KDE(KWin)·aquamarine 의 기본 규칙과 같게.
    #   목록을 우리가 적어 넘기면 aquamarine 의 내장 화면 우선 규칙이 꺼진다
    if [ -n "$inner" ]; then
        [ -n "$first" ] && rest="$first${rest:+:}$rest"
        first=$inner
    fi
    [ "$n" -gt 1 ] || return 0                           # 카드가 하나면 고를 것이 없다
    list="$first${first:+${rest:+:}}$rest"
    [ -n "$list" ] || return 0
    if [ -n "$idle" ]; then
        export SEKAI_AQ_SAFE="$list"                     # 모니터가 꽂힌 카드만 — 되띄울 때
        list="$list:$idle"
    fi
    export AQ_DRM_DEVICES="$list"
    # 다른 GPU 에 꽂힌 모니터로 넘기는 화면은 일반 배치(LINEAR)로 — 그리는 GPU 의 타일 배치(NVIDIA BLOCK_LINEAR 등)를
    #   받는 GPU 가 못 읽으면 CPU 를 거치는 우회 복사로 빠지는데, aquamarine 0.9.2 의 그 길은 고장이라 모니터가 단색
    #   보라로만 나왔다 (5090 → Quadro RTX 4000, 2026-10-08). aquamarine 은 0.9.2 다음 판부터 이게 기본값이다 (be166e1)
    [ -n "${AQ_FORCE_LINEAR_BLIT:-}" ] || export AQ_FORCE_LINEAR_BLIT=1
}
sekai_gpu_pick

# ── NVIDIA 드라이버 ─────────────────────────────────────
#   주 카드가 NVIDIA 드라이버로 돌면 VA-API·GLX 가 NVIDIA 것을 쓰게 알려 준다
sekai_nvidia_env() {
    c=${AQ_DRM_DEVICES%%:*}
    [ -n "$c" ] || c=$(ls /dev/dri/card[0-9]* 2>/dev/null | head -1)
    [ -n "$c" ] || return 0
    drv=$(basename "$(readlink /sys/class/drm/"${c##*/}"/device/driver 2>/dev/null)" 2>/dev/null)
    [ "$drv" = nvidia ] || return 0
    export LIBVA_DRIVER_NAME=nvidia
    export __GLX_VENDOR_LIBRARY_NAME=nvidia
    export NVD_BACKEND=direct
    export SEKAI_NVIDIA=1
}
sekai_nvidia_env

# ── 기본 화면 모드 (X11) ─────────────────────────────────
#   화면 장치(DRM)가 아예 없으면(드라이버 없는 최신 NVIDIA 등) Wayland 는 뜰 수 없다.
#   데비안 커널은 펌웨어 화면을 DRM 으로 잡아 주지 않아(simpledrm 꺼짐) fbdev 만 남는다
#   → Xorg(fbdev) 위의 기본 화면 모드로 띄운다. 부팅 메뉴 "기본 화면 모드"도 여기로 온다.
if grep -qw 'sekai.basic=1' /proc/cmdline 2>/dev/null || ! ls /dev/dri/card* >/dev/null 2>&1; then
    export SEKAI_BASIC=1
elif ! grep -qx connected /sys/class/drm/card*-*/status 2>/dev/null \
        && grep -qiE 'EFI VGA|VESA VGA|simple' /sys/class/graphics/fb0/name 2>/dev/null; then
    # 화면 장치는 있는데 모니터가 꽂힌 곳이 하나도 없고, 펌웨어 화면은 살아 있다
    #   = 모니터는 드라이버 없는 카드(최신 NVIDIA 등)에, 드라이버 있는 건 내장 GPU 뿐
    #   → Hyprland 는 모니터 없는 카드로 떠서 까만 화면이 된다. 펌웨어 화면으로 간다.
    export SEKAI_BASIC=1
    export SEKAI_BASIC_REASON=nodisplay
fi
