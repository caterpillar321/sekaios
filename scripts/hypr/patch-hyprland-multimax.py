#!/usr/bin/env python3
"""Hyprland 패치 — 한 데스크톱에 최대화 창을 여럿 (SEKAI_MULTIMAX).

Hyprland 는 최대화를 "데스크톱의 전체 화면 창 하나"(워크스페이스의 m_hasFullscreenWindow·m_fullscreenMode)로
다뤄서, 한 창을 최대화하면 같은 데스크톱에 이미 최대화돼 있던 창이 풀렸다 — 최대화한 크롬 위에서 탐색기를
최대화하면 크롬이 원래 크기로 돌아가고, 최대화 창 둘을 최소화했다 되살리면 하나가 풀렸다.
윈도우에선 최대화는 창마다의 상태라 몇 개든 최대화한 채로 쌓이고, 보이는 것은 쌓임 순서가 정한다.

고친다:
  - 떠 있는 창끼리는 새로 최대화해도 옛 최대화 창을 풀지 않는다 (setWindowFullscreenState · 새 창의 최대화 요청)
  - 데스크톱의 "전체 화면 창"은 최대화·전체 화면 창 가운데 맨 위 창 (getFullscreenWindow 가 위에서부터 찾는다),
    데스크톱의 전체 화면 방식(작업 표시줄을 숨기나)도 그 창의 것
  - 그 창보다 위에 쌓인 창만 보인다(m_createdOverFullscreen) — 창을 올리거나 최대화·복원·닫기·옮기기 할 때
    쌓임 순서에서 다시 맞춘다 (sekaiFullscreenSync). 맨 위 최대화 창을 최소화하면 그 밑 창들이 쌓인 순서대로 보인다
  - 모니터·작업 표시줄이 바뀌면 모든 최대화 창의 크기를 다시 맞춘다 (전엔 맨 위 창만)
  - 시작 메뉴가 키보드를 쥐고 있는 동안 뜬 창도 최대화 요청이면 최대화로 연다 (전엔 요청이 버려졌다)
패치 순서: raise · misclick 다음. 헤더는 그대로 (플러그인 ABI). 멱등.
사용법: patch-hyprland-multimax.py <Hyprland 소스 디렉터리>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
MARK = "SEKAI_MULTIMAX"


def sub(text, old, new, what):
    assert text.count(old) == 1, f"기준점 없음/중복: {what} ({text.count(old)})"
    return text.replace(old, new, 1)


DECL = "void sekaiFullscreenSync(PHLWORKSPACE ws, PHLWINDOW gone = nullptr); // SEKAI_MULTIMAX (Compositor.cpp)\n"

out = {}  # 경로 → 고친 글 (모두 고친 뒤 한꺼번에 쓴다 — 중간에 기준점이 없으면 아무것도 안 쓴다)

# ── Compositor.cpp ──
p = root / "src/Compositor.cpp"
t = p.read_text()
if MARK in t:
    print("    (이미 적용됨)")
    sys.exit(0)

t = sub(t, '''void CCompositor::changeWindowZOrder(PHLWINDOW pWindow, bool top) {
''', '''// SEKAI_MULTIMAX: 한 데스크톱에 최대화(·전체 화면) 창이 여럿일 수 있다. 데스크톱의 "전체 화면 창"은 그중 맨 위 창이고,
//   그 창보다 위에 쌓인 창만 보인다(m_createdOverFullscreen) — 쌓임 순서에서 다시 맞춘다.
//   gone: 닫히는 중이라 빼고 볼 창
void sekaiFullscreenSync(PHLWORKSPACE ws, PHLWINDOW gone = nullptr) {
    if (!ws)
        return;
    PHLWINDOW top;
    for (auto const& w : g_pCompositor->m_windows)
        if (w != gone && w->m_workspace == ws && w->m_isMapped && w->isFullscreen())
            top = w; // 뒤로 갈수록 위
    ws->m_hasFullscreenWindow = top != nullptr;
    ws->m_fullscreenMode      = !top ? FSMODE_NONE : (top->m_fullscreenState.internal & FSMODE_FULLSCREEN) ? FSMODE_FULLSCREEN : FSMODE_MAXIMIZED;
    if (top) {
        bool above = false;
        for (auto const& w : g_pCompositor->m_windows) {
            if (w == top) {
                above                      = true;
                w->m_createdOverFullscreen = true;
                continue;
            }
            if (w == gone || w->m_workspace != ws || w->m_fadingOut || w->m_pinned)
                continue;
            w->m_createdOverFullscreen = above; // 밑의 최대화 창도 false — 가려진 창이 입력을 받지 않게
        }
    }
    if (ws->m_monitor.lock())
        g_pCompositor->updateFullscreenFadeOnWorkspace(ws);
}

void CCompositor::changeWindowZOrder(PHLWINDOW pWindow, bool top) {
''', "동기화 함수")

t = sub(t, '''        for (auto const& w : kids)
            changeWindowZOrder(w, true);
        if (WS && WS->m_hasFullscreenWindow)
            updateFullscreenFadeOnWorkspace(WS); // 아래로 간 창은 흐리게 숨기고, 올린 창은 보이게
''', '''        for (auto const& w : kids)
            changeWindowZOrder(w, true);
        if (WS && WS->m_hasFullscreenWindow)
            sekaiFullscreenSync(WS); // SEKAI_MULTIMAX: 맨 위 최대화 창이 바뀌었을 수 있다 — 쌓임 순서대로 보이고 숨긴다
''', "올린 뒤 동기화")

t = sub(t, '''    if (PWORKSPACE->m_hasFullscreenWindow && !PWINDOW->isFullscreen())
        setWindowFullscreenInternal(PWORKSPACE->getFullscreenWindow(), FSMODE_NONE);
''', '''    // SEKAI_MULTIMAX: 떠 있는 창끼리는 옛 최대화 창을 풀지 않는다 (윈도우처럼 최대화 창이 여럿)
    if (const auto SEKAIFS = PWORKSPACE->getFullscreenWindow();
        PWORKSPACE->m_hasFullscreenWindow && !PWINDOW->isFullscreen() && SEKAIFS && !(PWINDOW->m_isFloating && SEKAIFS->m_isFloating))
        setWindowFullscreenInternal(SEKAIFS, FSMODE_NONE);
''', "옛 최대화 창 풀기")

t = sub(t, '''    PWINDOW->m_fullscreenState.internal = state.internal;
    PWORKSPACE->m_fullscreenMode        = EFFECTIVE_MODE;
    PWORKSPACE->m_hasFullscreenWindow   = EFFECTIVE_MODE != FSMODE_NONE;
''', '''    PWINDOW->m_fullscreenState.internal = state.internal;
    sekaiFullscreenSync(PWORKSPACE); // SEKAI_MULTIMAX: 데스크톱 상태는 맨 위 최대화 창에서 (다른 최대화 창이 남아 있을 수 있다)
''', "데스크톱 상태")

t = sub(t, '''    // make all windows on the same workspace under the fullscreen window
    for (auto const& w : m_windows) {
        if (w->m_workspace == PWORKSPACE && !w->isFullscreen() && !w->m_fadingOut && !w->m_pinned)
            w->m_createdOverFullscreen = false;
    }

    updateFullscreenFadeOnWorkspace(PWORKSPACE);
''', '''    // SEKAI_MULTIMAX: 위아래 창 정리는 sekaiFullscreenSync 가 쌓임 순서로 했다 (최대화한 창은 레이아웃이 맨 위로 올렸다)
''', "창들을 아래로")
out[p] = t

# ── Workspace.cpp ──
p = root / "src/desktop/Workspace.cpp"
t = p.read_text()
t = sub(t, '''PHLWINDOW CWorkspace::getFullscreenWindow() {
    for (auto const& w : g_pCompositor->m_windows) {
        if (w->m_workspace == m_self && w->isFullscreen())
            return w;
    }
''', '''PHLWINDOW CWorkspace::getFullscreenWindow() {
    // SEKAI_MULTIMAX: 최대화 창이 여럿이면 맨 위 창 (m_windows 는 아래→위)
    for (auto const& w : g_pCompositor->m_windows | std::views::reverse) {
        if (w->m_workspace == m_self && w->isFullscreen())
            return w;
    }
''', "맨 위 최대화 창")
t = sub(t, '''void CWorkspace::updateWindows() {
    m_hasFullscreenWindow = std::ranges::any_of(g_pCompositor->m_windows, [this](const auto& w) { return w->m_isMapped && w->m_workspace == m_self && w->isFullscreen(); });
''', '''void CWorkspace::updateWindows() {
    sekaiFullscreenSync(m_self.lock()); // SEKAI_MULTIMAX: 있나 없나에 더해 방식·위아래 창까지 (창을 옮기고 닫을 때 온다)
''', "창 목록 갱신")
t = sub(t, "using namespace Hyprutils::String;\n", "using namespace Hyprutils::String;\n#include <ranges>\n" + DECL, "선언 자리")
out[p] = t

# ── events/Windows.cpp ──
p = root / "src/events/Windows.cpp"
t = p.read_text()
t = sub(t, '''    if (!PWINDOW->m_noInitialFocus && (requestedInternalFSMode.has_value() || requestedClientFSMode.has_value() || requestedFSState.has_value())) {
        // fix fullscreen on requested (basically do a switcheroo)
        if (PWINDOW->m_workspace->m_hasFullscreenWindow)
            g_pCompositor->setWindowFullscreenInternal(PWINDOW->m_workspace->getFullscreenWindow(), FSMODE_NONE);
''', '''    // SEKAI_MULTIMAX: 최대화만 청한 떠 있는 창은 초점을 받지 못해도(시작 메뉴가 키보드를 쥐고 있을 때) 최대화로 연다
    const bool SEKAIMAXONLY = PWINDOW->m_isFloating && !requestedInternalFSMode.has_value() && !requestedFSState.has_value() && requestedClientFSMode == FSMODE_MAXIMIZED;
    if ((!PWINDOW->m_noInitialFocus || SEKAIMAXONLY) && (requestedInternalFSMode.has_value() || requestedClientFSMode.has_value() || requestedFSState.has_value())) {
        // fix fullscreen on requested (basically do a switcheroo)
        //   SEKAI_MULTIMAX: 떠 있는 창끼리는 옛 최대화 창을 풀지 않는다
        if (const auto SEKAIFS = PWINDOW->m_workspace->getFullscreenWindow();
            PWINDOW->m_workspace->m_hasFullscreenWindow && SEKAIFS && !(PWINDOW->m_isFloating && SEKAIFS->m_isFloating))
            g_pCompositor->setWindowFullscreenInternal(SEKAIFS, FSMODE_NONE);
''', "새 창의 최대화 요청")
t = sub(t, '''    if (PWORKSPACE->m_hasFullscreenWindow && PWINDOW->isFullscreen())
        PWORKSPACE->m_hasFullscreenWindow = false;
''', '''    if (PWORKSPACE->m_hasFullscreenWindow && PWINDOW->isFullscreen())
        sekaiFullscreenSync(PWORKSPACE, PWINDOW); // SEKAI_MULTIMAX: 다른 최대화 창이 남아 있으면 그 창이 맨 위
''', "닫힐 때")
t = sub(t, "bool sekaiTakeInitialMaximize(PHLWINDOW w); // SEKAI_INITIAL_MAXIMIZE (desktop/Window.cpp)\n",
        "bool sekaiTakeInitialMaximize(PHLWINDOW w); // SEKAI_INITIAL_MAXIMIZE (desktop/Window.cpp)\n" + DECL, "선언 자리")
out[p] = t

# ── 레이아웃: 모든 최대화 창의 크기 ──
for name, node in (("DwindleLayout", "SDwindleNodeData"), ("MasterLayout", "SMasterNodeData")):
    p = root / f"src/layout/{name}.cpp"
    t = p.read_text()
    t = sub(t, '''        // massive hack from the fullscreen func
        const auto PFULLWINDOW = pWorkspace->getFullscreenWindow();

        if (pWorkspace->m_fullscreenMode == FSMODE_FULLSCREEN) {''', '''        // massive hack from the fullscreen func
        // SEKAI_MULTIMAX: 최대화·전체 화면 창이 여럿일 수 있다 — 모두 맞춘다 (전엔 맨 위 창만)
        for (auto const& PFULLWINDOW : g_pCompositor->m_windows) {
        if (PFULLWINDOW->m_workspace != pWorkspace || !PFULLWINDOW->m_isMapped || !PFULLWINDOW->isFullscreen())
            continue;
        const auto SEKAIMODE = (PFULLWINDOW->m_fullscreenState.internal & FSMODE_FULLSCREEN) ? FSMODE_FULLSCREEN : FSMODE_MAXIMIZED;

        if (SEKAIMODE == FSMODE_FULLSCREEN) {''', f"{name} 전체 화면")
    t = sub(t, '''        } else if (pWorkspace->m_fullscreenMode == FSMODE_MAXIMIZED) {
            ''' + node, '''        } else if (SEKAIMODE == FSMODE_MAXIMIZED) {
            ''' + node, f"{name} 최대화")
    t = sub(t, '''            applyNodeDataToWindow(&fakeNode);
        }

        // if has fullscreen, don't calculate the rest''', '''            applyNodeDataToWindow(&fakeNode);
        }
        } // SEKAI_MULTIMAX

        // if has fullscreen, don't calculate the rest''', f"{name} 끝")
    out[p] = t

for p, t in out.items():
    p.write_text(t)
print("    적용: 최대화 창 여럿 (Compositor · Workspace · Windows · 레이아웃)")
