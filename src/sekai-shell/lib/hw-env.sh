# SekaiOS — 하드웨어별 보정 (sekai-session 과 sekai-greeter-session 이 함께 읽는다)
if [ "$(systemd-detect-virt 2>/dev/null)" = "vmware" ]; then
    # vmwgfx 가 원자적(atomic) 모드 설정으로 해상도를 키우는 걸 거부한다
    export AQ_NO_ATOMIC=1
    # GTK4 의 GL 렌더러 버퍼를 VMware 가상 GPU 에서 컴포지터가 거부한다
    export GSK_RENDERER=cairo
fi
