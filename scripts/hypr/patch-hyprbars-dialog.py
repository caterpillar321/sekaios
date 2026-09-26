#!/usr/bin/env python3
"""hyprbars 패치 — 대화상자(부모 창이 있는 창)에는 닫기 단추만 (윈도우처럼).

파일 복사 충돌 창·압축 풀기 창·속성 창·프린터 추가 창에도 최소화·최대화 단추가 붙어 있었다.
부모 창이 있는 창(xdg 의 parent, X11 의 transient-for — Hyprland 의 CWindow::parent())이면
sekai:min · sekai:max 단추를 그리지도, 누름을 받지도, 자리를 차지하지도 않게 한다.
제목줄 두 번 누르기(on_double_click = 최대화)도 대화상자에서는 하지 않는다.
단추를 도는 곳(누름 판정 · 제목 폭 · 보이는 개수 · 배경 · 아이콘 · 마우스 올림)이 모두 같은 규칙으로
건너뛰어야 자리가 어긋나지 않는다. 다른 hyprbars 패치(icons·hover·snap·…) 다음에 적용한다. 멱등.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="utf-8").read()
MARK = "SEKAI_DIALOG_BUTTONS"
if MARK in s:
    print("이미 적용됨"); sys.exit(0)


def replace(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, f"기준점 {n}개 (기대 {count}): {old[:60]!r}"
    s = s.replace(old, new)


# 1. 도움 함수 — 끌어서 스냅 부분(patch-hyprbars-snap.py) 앞에
replace('''// ── SEKAI_SNAP: 끌어서 스냅 ─────────────────────────────────────
''', '''// ── SEKAI_DIALOG_BUTTONS: 대화상자에는 닫기 단추만 ─────────────────
static bool sekaiDialogSkip(const PHLWINDOW& w, const std::string& icon) {
    return (icon == "sekai:min" || icon == "sekai:max") && w && w->parent();
}

// ── SEKAI_SNAP: 끌어서 스냅 ─────────────────────────────────────
''')

SKIP_B = '''        if (sekaiDialogSkip(m_pWindow.lock(), b.icon)) // SEKAI_DIALOG_BUTTONS
            continue;
'''
# 2. 누름 판정 · 제목 폭 · 마우스 올림 — 세 곳 모두 같은 줄로 시작한다
replace('''    for (auto& b : g_pGlobalState->buttons) {
''', '''    for (auto& b : g_pGlobalState->buttons) {
''' + SKIP_B, count=3)

# 3. 보이는 단추 개수
replace('''    for (const auto& button : g_pGlobalState->buttons) {
        const float buttonSpace''', '''    for (const auto& button : g_pGlobalState->buttons) {
        if (sekaiDialogSkip(m_pWindow.lock(), button.icon)) // SEKAI_DIALOG_BUTTONS
            continue;
        const float buttonSpace''')

# 4. 단추 배경 (renderBarButtons) — 보이는 개수만큼, 건너뛴 것은 세지 않고
replace('''    for (size_t i = 0; i < visibleCount; ++i) {
        const auto& button           = g_pGlobalState->buttons[i];
''', '''    for (size_t i = 0, sekaiShown = 0; i < g_pGlobalState->buttons.size() && sekaiShown < visibleCount; ++i) {
        const auto& button           = g_pGlobalState->buttons[i];
        if (sekaiDialogSkip(m_pWindow.lock(), button.icon)) // SEKAI_DIALOG_BUTTONS
            continue;
        ++sekaiShown;
''')

# 5. 단추 아이콘 (renderBarButtonsText)
replace('''    for (size_t i = 0; i < visibleCount; ++i) {
        auto&      button           = g_pGlobalState->buttons[i];
''', '''    for (size_t i = 0, sekaiShown = 0; i < g_pGlobalState->buttons.size() && sekaiShown < visibleCount; ++i) {
        auto&      button           = g_pGlobalState->buttons[i];
        if (sekaiDialogSkip(m_pWindow.lock(), button.icon)) // SEKAI_DIALOG_BUTTONS
            continue;
        ++sekaiShown;
''')

# 6. 제목줄 두 번 누르기 — 대화상자는 최대화하지 않는다
replace('''    if (!ON_DOUBLE_CLICK.empty() &&
''', '''    if (!ON_DOUBLE_CLICK.empty() && !(PWINDOW && PWINDOW->parent()) /* SEKAI_DIALOG_BUTTONS */ &&
''')

open(path, "w", encoding="utf-8").write(s)
print("적용")
