"""불변식 — 시험마다 끝나고 확인한다. 하나라도 깨지면 그 시험은 "경고"(통과여도)."""
from . import remote

PROBE = r'''
set +e
comp=$(pgrep -x worldlink >/dev/null && echo 1 || echo 0)
panel=$(pgrep -f "/usr/bin/sekai-pane[l]" >/dev/null && echo 1 || echo 0)
desk=$(pgrep -f "/usr/bin/sekai-des[k]" >/dev/null && echo 1 || echo 0)
cores=$( (ls ${XDG_CACHE_HOME:-$HOME/.cache}/hyprland/hyprlandCrashReport*.txt 2>/dev/null; command -v coredumpctl >/dev/null && coredumpctl list --no-legend 2>/dev/null) | wc -l)
tb=$(journalctl --user --since "@SINCE" --no-pager -q 2>/dev/null | grep -c "Traceback (most recent call last)")
L=$(ls -t /run/user/$(id -u)/hypr/*/hyprland.log 2>/dev/null | head -1)
crit=$( [ -n "$L" ] && grep -ac "\[CRIT\]" "$L" || echo 0)
echo "comp=$comp panel=$panel desk=$desk cores=$cores tb=$tb crit=$crit"
'''


def probe(since):
    r = remote.run(PROBE.replace("@SINCE", f"@{int(since)}"), timeout=30)
    vals = {}
    for kv in r.out.split():
        k, _, v = kv.partition("=")
        try:
            vals[k] = int(v)
        except ValueError:
            pass
    return vals


def problems(before, after):
    """앞뒤 비교 — 깨진 것 목록 (빈 목록이면 괜찮다)"""
    out = []
    if after.get("comp") == 0:
        out.append("합성기(worldlink)가 죽었다")
    if after.get("panel") == 0:
        out.append("작업 표시줄(sekai-panel)이 없다")
    if after.get("desk") == 0:
        out.append("바탕화면(sekai-desk)이 없다")
    if after.get("cores", 0) > before.get("cores", 0):
        out.append(f"크래시 보고 {after['cores'] - before.get('cores', 0)}개 (~/.cache/hyprland · coredumpctl)")
    if after.get("tb", 0) > before.get("tb", 0):
        out.append(f"파이썬 Traceback {after['tb'] - before.get('tb', 0)}개 (journalctl --user)")
    if after.get("crit", 0) > before.get("crit", 0):
        out.append("합성기 [CRIT] 기록")
    return out


def tracebacks(since, limit=3):
    r = remote.run(f'journalctl --user --since "@{int(since)}" --no-pager -q -o cat 2>/dev/null | '
                   f'grep -A12 "Traceback (most recent call last)" | head -60', timeout=30)
    return r.out.strip()
