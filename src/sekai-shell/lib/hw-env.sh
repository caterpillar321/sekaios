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
