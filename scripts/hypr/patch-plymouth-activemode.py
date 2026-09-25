#!/usr/bin/env python3
"""Plymouth DRM 렌더러 패치 — 커널이 켜 둔 화면 모드를 권장 모드보다 먼저 쓴다 (SEKAI_ACTIVE_MODE).

원래는 모니터의 권장 모드(EDID preferred)를 먼저 고르고, 그게 없을 때만 지금 켜진 모드를 쓴다.
커널 옵션 video= 로 콘솔을 바탕화면과 같은 모드(165Hz 등)로 켜 둬도 부팅 화면이 권장 모드로
다시 잡아 모니터 신호가 끊겼다. 켜진 모드가 있으면 그대로 두고, 없을 때만 권장 모드.
(타일 모니터는 원래처럼 켜진 모드만 본다.) 멱등.
사용법: patch-plymouth-activemode.py <src/plugins/renderers/drm/plugin.c>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SEKAI_ACTIVE_MODE" in s:
    print("    (이미 적용됨)")
    sys.exit(0)
old = '''        if (!output->tiled)
                mode = get_preferred_mode (connector);

        if (!mode && output->controller_id)
                mode = get_active_mode (backend, connector, output);
'''
assert s.count(old) == 1, "get_output_info 기준점 없음"
s = s.replace(old, '''        /* SEKAI_ACTIVE_MODE: 커널이 켜 둔 모드를 먼저 — 다시 잡으면 모니터 신호가 끊긴다 */
        if (output->controller_id)
                mode = get_active_mode (backend, connector, output);

        if (!mode && !output->tiled)
                mode = get_preferred_mode (connector);
''', 1)
open(p, "w", encoding="utf-8").write(s)
print("    적용: DRM 렌더러가 켜진 모드를 먼저")
