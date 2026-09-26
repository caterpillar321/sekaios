#!/usr/bin/env python3
"""hyprbars 패치 — 누름 표시가 남던 것, 단추가 다른 창에 먹던 것 (SEKAI_BAR_INPUT).

1. 누름 표시(m_bCancelledDown·m_bDragPending) 초기화
   제목줄 누름은 앱에 넘기지 않으려고 표시해 두고 뗌도 삼킨다. 그런데 최소화 단추처럼 누른 사이에 창이
   초점을 잃으면 뗌 처리가 일찍 끝나 표시가 남고, 다음에 그 창 "본문"을 눌렀다 떼면 뗌이 삼켜졌다
   — 앱은 버튼이 눌린 채로 안다(터미널 선택이 계속 따라오고, 단추가 안 눌리고, 크기 조절이 안 끝남).
   누를 때마다 먼저 지우고, 일찍 끝나는 뗌 처리에서도 지운다.
2. 창 조작 단추(sekai:min·max·close)는 그 제목줄의 창에 곧바로
   원래는 exec 로 hyprctl 을 띄워 "초점 창"에 dispatch 했다 — 도착하기 전에 다른 창을 누르면(부하가 큰 때)
   그 창이 닫히거나 최소화됐다. 디스패처를 그 창 주소로 바로 부른다.
멱등. 다른 hyprbars 패치 다음에 적용한다.
사용법: patch-hyprbars-inputfix.py <barDeco.cpp>
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SEKAI_BAR_INPUT" in s:
    print("    (이미 적용됨)")
    sys.exit(0)


def sub(old, new, what):
    global s
    assert s.count(old) == 1, f"기준점 없음/중복: {what} ({s.count(old)})"
    s = s.replace(old, new, 1)


sub('''void CHyprBar::handleDownEvent(SCallbackInfo& info, std::optional<ITouch::SDownEvent> touchEvent) {
    m_bTouchEv = touchEvent.has_value();
''', '''void CHyprBar::handleDownEvent(SCallbackInfo& info, std::optional<ITouch::SDownEvent> touchEvent) {
    m_bTouchEv       = touchEvent.has_value();
    m_bCancelledDown = false; // SEKAI_BAR_INPUT: 지난 누름의 표시가 남아 이번 뗌(본문 클릭)을 삼키지 않게
''', "handleDownEvent 앞")
sub('''            sekaiSnapCancel();
        }
        return;
    }
''', '''            sekaiSnapCancel();
        }
        // SEKAI_BAR_INPUT: 창이 초점을 잃은 사이(최소화 단추·저장할까요 창) 뗌 — 우리가 삼킨 누름의 뗌이면
        //   함께 삼키고, 표시는 모두 지운다
        if (m_bCancelledDown)
            info.cancelled = true;
        m_bCancelledDown = false;
        m_bDragPending   = false;
        m_bTouchEv       = false;
        return;
    }
''', "handleUpEvent 일찍 끝남")
sub('''            // hit on close
            g_pKeybindManager->m_dispatchers["exec"](b.cmd);
            return true;
''', '''            // SEKAI_BAR_INPUT: 창 조작 단추는 이 창에 곧바로 (exec 로 hyprctl 을 띄우면 도착했을 때의 초점 창에 먹었다)
            if (const auto W = m_pWindow.lock(); W && (b.icon == "sekai:close" || b.icon == "sekai:min" || b.icon == "sekai:max")) {
                const auto ADDR = std::format("address:0x{:x}", (uintptr_t)W.get());
                if (b.icon == "sekai:close")
                    g_pKeybindManager->m_dispatchers["closewindow"](ADDR);
                else if (b.icon == "sekai:min")
                    g_pKeybindManager->m_dispatchers["movetoworkspacesilent"]("special:min," + ADDR);
                else {
                    g_pCompositor->focusWindow(W); // fullscreen 은 초점 창에 걸린다 — 방금(누를 때) 준 초점을 확실히
                    if (g_pCompositor->m_lastWindow.lock() == W)
                        g_pKeybindManager->m_dispatchers["fullscreen"]("1");
                }
                return true;
            }
            // hit on close
            g_pKeybindManager->m_dispatchers["exec"](b.cmd);
            return true;
''', "단추 누름")
open(p, "w", encoding="utf-8").write(s)
print("    적용: 누름 표시 초기화·단추는 그 창에 곧바로")
