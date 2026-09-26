#!/usr/bin/env python3
"""Plymouth DRM 렌더러 패치 — 켜 둔 모니터가 잠깐 연결을 끊어도 떼어 내지 않는다 (SEKAI_KEEP_LIT).

깨어나는 모니터는 신호를 받고 조금 뒤 연결(HPD)을 한 번 끊었다 다시 알리곤 한다. 호스트 PC 의 보조
모니터(HDMI)가 부팅이 빠를 때 그랬다: 부팅 화면이 뜬 0.4초 뒤 "출력이 바뀌었다"며 그 모니터를 떼어 냈다.
떼어 내면 버퍼를 지워(drmModeRmFB) 커널이 그 출력을 끄고, 부팅 화면은 모든 화면을 다시 붙인다.
모니터가 돌아올 땐 부팅 화면이 이미 비켜선 뒤라(로그인 화면이 뜨기 전) 콘솔이 화면을 다시 잡아
부팅 화면이 사라지고 모니터가 깜박였다.
고친다: 같은 컨트롤러로 켜져 있는 출력이 끊겼거나(연결 없음) 모드 목록만 바뀌었으면 예전 상태 그대로 둔다
— 출력은 계속 신호를 내고, 돌아온 모니터는 그 신호에 다시 맞출 뿐이다. 새로 나타난 모니터는 원래대로. 멱등.
사용법: patch-plymouth-keeplit.py <src/plugins/renderers/drm/plugin.c>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SEKAI_KEEP_LIT" in s:
    print("    (이미 적용됨)")
    sys.exit(0)
old = '''        if (memcmp (old_output, new_output, sizeof(ply_output_t)) == 0)
                return false;

        ply_trace ("Output for connector %u changed, removing", old_output->connector_id);
'''
assert s.count(old) == 1, "check_if_output_has_changed 기준점 없음"
s = s.replace(old, '''        if (memcmp (old_output, new_output, sizeof(ply_output_t)) == 0)
                return false;

        /* SEKAI_KEEP_LIT: 켜 둔 모니터가 잠깐 끊겼거나 모드 목록만 바뀌었으면 떼어 내지 않는다 —
         * 떼어 내면 출력이 꺼지고, 돌아올 땐 콘솔이 화면을 다시 잡아 부팅 화면이 사라진다 */
        if (!new_output->connected || new_output->controller_id == old_output->controller_id) {
                ply_trace ("Output for connector %u changed (%s), keeping it lit",
                           old_output->connector_id, new_output->connected ? "modes" : "disconnected");
                *new_output = *old_output;
                return false;
        }

        ply_trace ("Output for connector %u changed, removing", old_output->connector_id);
''', 1)
open(p, "w", encoding="utf-8").write(s)
print("    적용: 켜 둔 모니터가 잠깐 끊겨도 그대로")
