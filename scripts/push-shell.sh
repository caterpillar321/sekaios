#!/bin/bash
# 개발 중인 셸을 실행 중인 SekaiOS VM 으로 전송하고 재시작
# 사용법: ~/MyOS/scripts/push-shell.sh <VM_IP> [사용자]
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"
IP="${1:?사용법: push-shell.sh <VM_IP> [user]}"
USER_="${2:-sekai}"

echo "==> 전송: $P/src/sekai-shell -> $USER_@$IP:~/sekai-shell"
scp -q -o StrictHostKeyChecking=no -r "$P/src/sekai-shell" "$USER_@$IP:~/"

echo "==> 패널 재시작"
ssh -o StrictHostKeyChecking=no "$USER_@$IP" bash -lc '
  pkill -f sekai-panel 2>/dev/null || true
  sleep 0.3
  chmod +x ~/sekai-shell/sekai-panel
  # Hyprland 세션의 환경변수를 가져와서 실행
  HIS=$(ls ~/.local/share/hyprland 2>/dev/null | head -1)
  export HYPRLAND_INSTANCE_SIGNATURE=$(ls "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | head -1)
  export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-1}
  setsid nohup ~/sekai-shell/sekai-panel > /tmp/sekai-panel.log 2>&1 &
  sleep 1
  echo "--- 로그 ---"
  tail -20 /tmp/sekai-panel.log 2>/dev/null || echo "(로그 없음 = 정상 실행 중)"
'
