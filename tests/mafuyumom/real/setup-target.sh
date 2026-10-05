#!/bin/bash
# MafuyuMom 실기 모드 — 시험할 실제 PC 를 한 번 준비한다 (그 PC 에서: sudo bash setup-target.sh)
#   시험 계정 miku (암호 miku1234 — VM 골든과 같다, sudo 는 암호로) · 개발 키로 SSH · 가상 입력(uinput) ·
#   로그인 화면이 miku 를 기억. 사용자 계정과 그 파일은 건드리지 않는다. 시험대 전용 PC 에만 쓸 것.
set -e
[ "$(id -u)" = 0 ] || { echo "sudo bash $0 로 실행하세요"; exit 1; }
if ! id miku >/dev/null 2>&1; then
    useradd -m -s /bin/bash -c "MafuyuMom 시험" miku
    echo "miku:miku1234" | chpasswd
    echo "  · 시험 계정 miku 를 만들었습니다"
fi
for g in sudo audio video plugdev users netdev lpadmin bluetooth input; do
    getent group $g >/dev/null && usermod -aG $g miku
done
install -d -m700 -o miku -g miku /home/miku/.ssh
grep -q "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGHAAEaUDOA/Ix1noJtxvIajRv2DTE5onhCFfU6otHZf sekaios-dev@wsl" /home/miku/.ssh/authorized_keys 2>/dev/null || echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGHAAEaUDOA/Ix1noJtxvIajRv2DTE5onhCFfU6otHZf sekaios-dev@wsl" >> /home/miku/.ssh/authorized_keys
chown miku:miku /home/miku/.ssh/authorized_keys; chmod 600 /home/miku/.ssh/authorized_keys
# 가상 입력 (시험이 마우스·키보드를 움직인다) — 부팅 때마다
echo uinput > /etc/modules-load.d/sekai-mm-uinput.conf
modprobe uinput
# 로그인 화면이 miku 를 먼저 보여 주게 (시험이 암호만 치면 되게)
install -d -o _greetd -g _greetd /var/lib/greetd/.cache/sekai-greeter 2>/dev/null || true
echo miku > /var/lib/greetd/.cache/sekai-greeter/last-user
chown _greetd:_greetd /var/lib/greetd/.cache/sekai-greeter/last-user 2>/dev/null || true
echo "  · 준비 끝 — 로그아웃했다가 miku (암호 miku1234) 로 한 번 로그인해 주세요"
