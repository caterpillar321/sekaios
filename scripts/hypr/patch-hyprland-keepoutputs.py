#!/usr/bin/env python3
"""Hyprland 패치 — 끝날 때 모니터 출력을 끄지 않는다 (마지막 화면을 띄운 채 넘긴다).

Hyprland 0.50.1 은 종료하며 모니터마다 setEnabled(false) 로 출력을 꺼 버린다.
로그인 화면(작은 Hyprland)이 끝나고 사용자 Hyprland 가 뜨는 1~2초 동안 신호가 없으니
LCD 가 "신호 없음"으로 보고 백라이트까지 끈다 — 비밀번호를 넣으면 화면이 완전히 꺼졌다 켜졌다.
출력을 켜 둔 채 끝내면 마지막 화면이 남고(aquamarine 은 drmModeCloseFB 로 버퍼를 닫아
화면에서 내려가지 않는다), 다음 Hyprland 가 같은 모드로 넘겨받으면 신호가 끊기지 않는다.
wlroots(Sway)도 이렇게 한다. 멱등 (SEKAI_KEEP_OUTPUTS 표시).

사용법: patch-hyprland-keepoutputs.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

f = pathlib.Path(sys.argv[1]) / "src/Compositor.cpp"
t = f.read_text()
if "SEKAI_KEEP_OUTPUTS" in t:
    print("    (Compositor.cpp 이미 적용됨)")
    sys.exit(0)
old = '''    for (auto const& m : m_monitors) {
        g_pHyprOpenGL->destroyMonitorResources(m);

        m->m_output->state->setEnabled(false);
        m->m_state.commit();
    }
'''
assert t.count(old) == 1, "cleanup 기준점 없음"
t = t.replace(old, '''    // SEKAI_KEEP_OUTPUTS: 출력을 끄지 않는다 — 다음 화면(사용자 세션·로그인 화면)이 끊김 없이 넘겨받게
    for (auto const& m : m_monitors) {
        g_pHyprOpenGL->destroyMonitorResources(m);
    }
''', 1)
f.write_text(t)
print("    적용: Compositor.cpp 종료 때 출력 유지")
