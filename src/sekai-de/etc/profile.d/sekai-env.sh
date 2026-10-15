# SekaiOS 환경 변수 (설정 → 환경 변수) — 로그인 셸(SSH · 텍스트 콘솔)에도.
#   데비안 /etc/profile 은 PATH 를 새로 정하므로 그 뒤인 여기서 다시 얹는다. 변수를 하나도 안 만들었으면 아무것도 안 한다
if [ -s /etc/environment.d/60-sekai-system.conf ] || [ -s "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/70-sekai-user.conf" ] \
   || [ -n "${SEKAI_ENV_BASE:-}" ]; then
    eval "$(python3 -m sekaishell.userenv sh 2>/dev/null)"
fi
