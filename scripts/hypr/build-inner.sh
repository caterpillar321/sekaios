#!/bin/bash
# buildroot 내부에서 실행됨 — hyprland 생태계를 빌드하고 .deb 으로 포장
set -euo pipefail

SRC=/build/src
OUT=/build/deb
# SekaiCompose — Hyprland v0.50.1 에서 갈라진 SekaiOS 의 합성기 (hyprbars 포함).
#   예전엔 여기서 원본에 patch-*.py 를 적용했다. 이제 그 고친 것들은 포크의 커밋이다 (git log v0.50.1..sekai).
#   재현할 수 있게 커밋을 고정한다 — 합성기를 고치면 포크에 커밋·푸시하고 여기 REF 와 rev_for 를 올린다.
SEKAICOMP_URL="https://github.com/caterpillar321/sekaicompose.git"
SEKAICOMP_REF="5bf2e478567fd46d5634a8bdd34e475b513ca206"
MAINT="SekaiOS <sekai@localhost>"
REV="sekai2"
# 패키지별 리비전 (고친 패키지만 올린다 → apt 가 그 패키지만 업그레이드)
#   hyprbars sekai2: 창 조작 버튼 벡터 아이콘 패치
#   hyprbars sekai3: 버튼 마우스 올림 배경 (닫기 빨강)
#   전체 sekai2 / hyprbars sekai4: 패키지에 저작권·라이선스 고지(/usr/share/doc/*/copyright) 추가
#   hyprbars sekai5: 끌어서 스냅 — 제목줄 끌기를 셸에 IPC 이벤트로 알림 (patch-hyprbars-snap.py)
#   hyprbars sekai6: 스냅 레이아웃 — 최대화 버튼에 마우스 올림/벗어남 알림
#   hyprbars sekai7: 끄는 중 손을 떼면 커서 밑이 레이어여도 끌기를 끝냄 (위쪽 레이아웃 바)
#   hyprbars sekai8: 버튼 마우스 올림 배경을 글자색에서 (라이트 모드)
#   hyprland sekai3: 창이 그린 제목줄(CSD — Chromium 탭 줄 등)을 끌어서 옮기기 (patch-hyprland-clientmove.py)
#   hyprland sekai4: 끝날 때 모니터 출력을 끄지 않는다 — 로그인 때 화면이 꺼지지 않게 (patch-hyprland-keepoutputs.py)
#   hyprland sekai5: 창 테두리 크기 조절을 윈도우처럼 — 커서, 안쪽 가장자리, 위쪽, 한 방향 (patch-hyprland-bordergrab.py)
#   hyprbars sekai9: 제목줄 가장자리 4px 누름은 넘긴다 — 위쪽으로 크기 조절 (patch-hyprbars-bordergrab.py)
#   hyprland sekai6: 바탕화면 레이어가 창의 키보드 초점을 가로채지 않게 (patch-hyprland-layerfocus.py)
#   hyprland sekai7: follow_mouse=2 에서 바탕화면 레이어가 커서 올림만으로 키보드를 가져가지 않게
#   hyprland sekai8: 메뉴가 닫힐 때(다시 잡기)도 바탕화면이 키보드를 가져가지 않게, 테두리 조절 부작용
#                    (갇힌 포인터·끝난 뒤 커서·앱 커서 요청), 버튼 없이 온 move 요청·끄던 창이 닫힐 때 놓음 알림
#   hyprbars sekai10: 제목줄을 누르면 키보드가 레이어에 있어도 초점, 최대화 창 제목줄 가장자리는 넘기지 않음,
#                     끄던 창이 닫히면 끌기를 끝내고 알림
#   hyprland sekai9: 끌기 아이콘의 기준점(누른 점)을 지킨다 (patch-hyprland-dndhotspot.py)
#   hyprbars sekai11: 대화상자(부모 창이 있는 창)에는 닫기 단추만, 두 번 눌러 최대화 안 함 (patch-hyprbars-dialog.py)
#   hyprland sekai10: 창의 메뉴(팝업)는 작업 표시줄 같은 예약 영역을 피해 맞춘다 (patch-hyprland-popupreserved.py)
#   hyprland sekai11: 나타나기 전에 청한 최대화를 지킨다 — 최대화한 채 닫은 크롬 (patch-hyprland-initialmax.py)
#   hyprland sekai12: 창을 고르면 맨 앞으로, 최대화한 창도 보통 창과 같은 쌓임 순서 (patch-hyprland-raise.py)
#   hyprland sekai13: 최대화한 창을 끌어 내리면 커서가 제목줄 같은 자리에 (patch-hyprland-dragrestore.py)
#   hyprland sekai14: 창이 제목줄 높이만큼 밀려 그려진 채 굳던 것 — 남은 그리기 오프셋 버리기 (patch-hyprland-floatoffset.py)
#   hyprland sekai15: 바탕화면 누르면 활성 창 없음, 최대화해도 대화상자 위, X11 부모, 창 제목줄 끌기 보강 (patch-hyprland-misclick.py)
#   hyprbars sekai12: 누름 표시가 남아 다음 뗌을 삼키던 것, 창 조작 단추는 그 창에 곧바로 (patch-hyprbars-inputfix.py)
#   hyprland sekai16: 화면 끝에 붙은 변은 크기 조절 안쪽 띠 없음(bordergrab), 새 창을 작업 영역 안으로(fitnew),
#                     한 데스크톱에 최대화 창 여럿 (patch-hyprland-multimax.py)
#   hyprbars sekai13: 화면 끝에 붙은 제목줄 가장자리는 넘기지 않고 그쪽 테두리 픽셀도 제목줄 — 화면 맨 위에서 잡으면 끌기
#                     (patch-hyprbars-bordergrab.py)
#   hyprland sekai17: 앱이 스스로 청한 최소화(크롬 최소화 단추·X11 WM_CHANGE_STATE)·X11 최대화 요청을 받고, 최소화하면 그 데스크톱의
#                     맨 위 창에 초점 (patch-hyprland-minimize.py). 안쪽 테두리 띠는 위쪽만 (bordergrab SEKAI_BORDER_TOPONLY)
#   hyprbars sekai14: 막대를 숨긴 창(크롬·탐색기)의 누름은 받지 않는다 — sekai13 이 최대화한 크롬의 탭 줄·단추를 가로챘다.
#                     창 단추의 판정 칸 = 그려지는 칸 (patch-hyprbars-slots.py)
#   ── 2026-10-01: 위의 patch-*.py 는 전부 SekaiCompose 포크의 커밋으로 옮겼다 (SekaiOS 에선 지움).
#      hyprland sekai17 · hyprbars sekai14 는 포크 0d093f0e 와 소스가 같다 — 다음 변경부터 포크에 커밋하고 여기 리비전을 올린다.
#   ── 2026-10-01: 합성기 패키지 이름을 sekaicomp 로 (sekai18). hyprland 는 sekaicomp 를 요구하는 빈 전환 패키지 —
#      설치된 PC 의 sekai-update 는 hyprland 를 지우는 업데이트를 막으므로 지우지 않고 넘어오게 한다.
#      hyprbars sekai15: 포크(plugins/hyprbars)에서 빌드, sekaicomp 를 요구.
#      hyprexpo sekai3: 포크 헤더로 다시 빌드 — 플러그인은 합성기와 같은 커밋 해시로 빌드돼야 로드된다 ("Version mismatch").
#      그래서 플러그인은 sekaicomp 의 "정확한" 버전을 요구한다. SEKAICOMP_REF 를 올리면 플러그인 리비전도 함께 올릴 것.
#   ── 2026-10-01 오조작 시험: sekai19 — 창 단추는 같은 단추 위에서 뗄 때, 놓는 순간 커서 자리 반영·제목줄을 화면 안으로,
#      대화상자도 부모와 같은 데스크톱으로 (포크 1ea3c19 · 5859a9c · ea3003f). 플러그인도 같은 커밋으로 (hyprbars sekai16 · hyprexpo sekai4)
#      sekai20: 끌기 기준점을 누른 자리로 (포크 723858e) — hyprbars sekai17 · hyprexpo sekai5
#      sekai21: 대화상자를 되살리면 부모도, hyprctl clients 에 sekaiParent (포크 b226561) — hyprbars sekai18 · hyprexpo sekai6
#      sekai22: 크기를 바꿀 수 없는 창은 최대화·스냅하지 않는다, hyprctl clients 에 sekaiFixed (포크 e74f689) — hyprbars sekai19 · hyprexpo sekai7
#      sekai23: 하위 면·입력도 xdg 창 영역 기준 — 스스로 그림자를 두는 앱(Firefox)이 밀려 잘리던 것 (포크 5bf2e47) — hyprbars sekai20 · hyprexpo sekai8
rev_for() { case "$1" in hyprbars) echo sekai20 ;; hyprexpo) echo sekai8 ;; sekaicomp|hyprland) echo sekai23 ;; *) echo "$REV" ;; esac; }

mkdir -p "$SRC" "$OUT"
export CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)"
export MAKEFLAGS="-j$(nproc)"

C_B=$'\033[1;36m'; C_G=$'\033[1;32m'; C_R=$'\033[1;31m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok(){  printf '%s  ok%s %s\n' "$C_G" "$C_0" "$*"; }
die(){ printf '%s  ERROR: %s%s\n' "$C_R" "$*" "$C_0"; exit 1; }
warn(){ printf '%s  경고:%s %s\n' "$C_R" "$C_0" "$*"; }

# 로그에서 실제 에러 줄만 추려서 보여줌 (템플릿 에러 덤프에 묻히지 않게)
show_errors() {
    local log="$1"
    echo "─── error: 줄 요약 ───"
    grep -E '(^|[/ ])[^ ]*:[0-9]+:[0-9]+: (error|fatal error):' "$log" \
      | sed 's|/build/src/||' | sort -u | head -25 \
      || echo "  (error: 패턴 없음)"
    echo "─── 로그 마지막 15줄 ───"
    tail -15 "$log"
    echo "─── 전체 로그: $log ───"
}

# C++26 호환 shim 이 실제로 동작하는지 빌드 전에 검증
verify_shim() {
    local shim=/build/cxx26-compat.hpp
    [ -f "$shim" ] || die "shim 파일 없음: $shim"
    say "C++26 호환 shim 검증"
    cat > /tmp/shimtest.cpp <<'TESTEOF'
#include <string>
#include <string_view>
#include <cassert>
#include <cstdio>

// shim 이 실제로 활성화됐는지 컴파일 타임에 확인
#ifndef SEKAI_CXX26_STRCAT_SHIM
#error "shim 헤더가 주입되지 않았습니다 (-include 확인 필요)"
#endif

int main() {
    std::printf("  libstdc++ _GLIBCXX_RELEASE=%d, shim=%d\n",
                (int)_GLIBCXX_RELEASE, (int)SEKAI_CXX26_STRCAT_SHIM);
    std::string s = "abc"; std::string_view sv = "def"; const char* cp = "ghi";
    assert((s + sv)  == "abcdef");
    assert((sv + s)  == "defabc");
    assert((s + cp)  == "abcghi");
    assert((s + s)   == "abcabc");
    assert((s + "L") == "abcL");
    assert((std::string("x") + sv) == "xdef");
    return 0;
}
TESTEOF
    g++ -std=c++26 -include "$shim" /tmp/shimtest.cpp -o /tmp/shimtest 2> /tmp/shimtest.log \
      || { echo "─── shim 컴파일 실패 ───"; head -30 /tmp/shimtest.log; die "shim 검증 실패"; }
    /tmp/shimtest || die "shim 동작 검증 실패"
    ok "shim 정상 (모호성 없음)"
}

# ── git clone (태그 고정) ────────────────────────────
fetch() {
    local repo="$1" tag="$2" dir="$SRC/$1"
    if [ "$repo" = sekaicompose ]; then
        # 고정 커밋으로 맞춘다 (REF 를 올리면 이미 받은 소스도 그 커밋으로 옮겨 간다)
        if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$SEKAICOMP_REF" ]; then
            say "이미 있음: sekaicompose ${SEKAICOMP_REF:0:12}"
            return 0
        fi
        say "SekaiCompose 받기: ${SEKAICOMP_REF:0:12}"
        [ -d "$dir/.git" ] || git clone -q --filter=blob:none "$SEKAICOMP_URL" "$dir" || die "clone 실패: sekaicompose"
        git -C "$dir" fetch -q origin || die "fetch 실패: sekaicompose"
        git -C "$dir" checkout -q --force "$SEKAICOMP_REF" || die "커밋 없음: $SEKAICOMP_REF (포크에 푸시했나?)"
        git -C "$dir" submodule update -q --init --recursive || die "submodule 실패: sekaicompose"
        rm -rf "$dir/build" "$dir/plugins"/*/build      # 다른 커밋의 증분 빌드가 섞이지 않게
        return 0
    fi
    if [ -d "$dir/.git" ]; then
        say "이미 있음: $repo $tag"
    else
        say "clone: $repo $tag"
        git clone --depth 1 --branch "$tag" --recurse-submodules \
            "https://github.com/hyprwm/$repo.git" "$dir" >/dev/null 2>&1 \
          || die "clone 실패: $repo $tag"
    fi
}

# ── 패키지별 후처리 (build_cmake 가 post_<pkg> 를 자동 호출) ──
post_sekaicomp() {
    local stage="$1"
    # Hyprland 기본 배경화면 46MB — SekaiOS 는 자체 배경을 쓰므로 제거
    local before=$(du -sm "$stage" | cut -f1)
    rm -f "$stage"/usr/share/hypr/wall*.png
    # uwsm 은 데비안에 없다. 세션 목록에 뜨면 로그인이 실패하므로 제거
    rm -f "$stage"/usr/share/wayland-sessions/hyprland-uwsm.desktop
    local after=$(du -sm "$stage" | cut -f1)
    ok "후처리: ${before}MB -> ${after}MB (배경화면·uwsm 세션 제거)"
}

# ── .deb 생성 ────────────────────────────────────────
# 우리 패키지끼리의 런타임 의존성 (shlibs 로 자동 해결되지만 안전망으로 명시)
extra_deps_for() {
    case "$1" in
        hyprlang)     echo "hyprutils" ;;
        hyprcursor)   echo "hyprlang" ;;
        hyprgraphics) echo "hyprutils" ;;
        aquamarine)   echo "hyprutils" ;;
        sekaicomp)    echo "hyprutils, hyprlang, hyprcursor, hyprgraphics, aquamarine, xwayland, binutils" ;;
        hyprbars|hyprexpo) echo "sekaicomp (= 0.50.1-$(rev_for sekaicomp))" ;;
        *)            echo "" ;;
    esac
}

# control 에 더 적을 줄 — 이름을 바꾼 합성기가 옛 hyprland 의 파일(/usr/bin/Hyprland·hyprctl·헤더 등)을 넘겨받는다
extra_control_for() {
    case "$1" in
        sekaicomp) printf 'Replaces: hyprland (<< 0.50.1-sekai18)\nBreaks: hyprland (<< 0.50.1-sekai18)\n' ;;
    esac
}

# 설치 후 shlibs 등록 — 이게 있어야 다음 패키지의 dpkg-shlibdeps 가
# "libhyprutils.so.8 은 hyprutils 패키지 것" 이라고 알 수 있다
register_shlibs() {
    local pkg="$1" ver="$2" stage="$3"
    local shl="/var/lib/dpkg/info/${pkg}.shlibs"
    : > "$shl"
    while IFS= read -r so; do
        local soname name maj
        soname=$(readelf -d "$so" 2>/dev/null | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p')
        [ -n "$soname" ] || continue
        case "$soname" in lib*.so.*) ;; *) continue ;; esac
        name="${soname%%.so.*}"
        maj="${soname##*.so.}"
        echo "$name $maj $pkg (>= $ver)" >> "$shl"
    done < <(find "$stage" -name '*.so.*' -type f 2>/dev/null)
    if [ -s "$shl" ]; then
        sort -u -o "$shl" "$shl"
        ldconfig
        printf '     shlibs: %s\n' "$(tr '\n' ' ' < "$shl")"
    else
        rm -f "$shl"
    fi
}

# ── 저작권·라이선스 고지 ─────────────────────────────
#   BSD-3 / LGPL 은 바이너리를 배포할 때 저작권 고지와 라이선스 전문을 함께 넣어야 한다.
add_copyright() {
    local pkg="$1" stage="$2" src="$3" tag="$4" repo lic
    repo=$(basename "$src")
    [ "$pkg" = hyprbars ] && src="$src/plugins/hyprbars"     # hyprbars 는 hyprland-plugins 의 라이선스
    lic="$src/LICENSE"; [ -f "$lic" ] || lic="$src/COPYING"
    [ -f "$lic" ] || die "라이선스 파일 없음: $src"
    local doc="$stage/usr/share/doc/$pkg"
    mkdir -p "$doc"
    {
        echo "패키지:  $pkg (SekaiOS 빌드)"
        case "$pkg" in
            sekaicomp|hyprbars)
                echo "소스:    SekaiCompose — https://github.com/caterpillar321/sekaicompose  (커밋 $SEKAICOMP_REF)"
                if [ "$pkg" = sekaicomp ]; then
                    echo "원본:    Hyprland — https://github.com/hyprwm/Hyprland  (태그 v0.50.1 에서 갈라진 포크)"
                else
                    echo "원본:    hyprbars — https://github.com/hyprwm/hyprland-plugins  (태그 v0.50.0, plugins/hyprbars 로 가져옴)"
                fi
                echo "수정:    SekaiOS 의 창 동작(최대화·최소화·스냅·끌기·테두리·제목줄 단추 등) — 포크의 git log v0.50.1..sekai" ;;
            *)  echo "원본:    https://github.com/hyprwm/$repo  (태그 $tag)"
                echo "수정:    없음 (원본 그대로 빌드)" ;;
        esac
        echo
        echo "── 원본 라이선스 ($(basename "$lic")) ──"
        echo
        cat "$lic"
    } > "$doc/copyright"
    chmod 644 "$doc/copyright"
}

mkdeb() {
    local pkg="$1" ver="$2" stage="$3" desc="$4"
    [ -n "${5:-}" ] && add_copyright "$pkg" "$stage" "$5" "$6"
    local deps="" extra sdlog=/build/log/${pkg}.shlibdeps.log

    # 실제 ELF 바이너리·라이브러리 수집
    local bins=()
    while IFS= read -r f; do bins+=("$f"); done < <(
        find "$stage" -type f \( -name '*.so*' -o -perm -u+x \) \
             ! -path "*/DEBIAN/*" ! -path "*/debian/*" 2>/dev/null \
        | while read -r x; do file -b "$x" 2>/dev/null | grep -qi '^ELF' && echo "$x"; done)

    if [ ${#bins[@]} -gt 0 ]; then
        local rc=0                      # set -e 여도 경고만 하고 계속한다
        ( cd "$stage" && mkdir -p debian && : > debian/control
          dpkg-shlibdeps -O --ignore-missing-info "${bins[@]}" ) > /tmp/.sd 2> "$sdlog" || rc=$?
        deps=$(sed -n 's/^shlibs:Depends=//p' /tmp/.sd | head -1)
        rm -rf "$stage/debian" /tmp/.sd
        if [ $rc -ne 0 ] || [ -z "$deps" ]; then
            warn "dpkg-shlibdeps 결과 이상 (rc=$rc) — $sdlog"
            head -5 "$sdlog" | sed 's/^/     /'
        fi
    fi

    # 안전망: shlibs 로 이미 잡힌 건 빼고 없는 것만 추가 (중복 방지)
    #   (쉼표로 나눈다 — "sekaicomp (>= 0.50.1-sekai18)" 처럼 버전 조건이 붙은 것도 한 덩어리로)
    extra=$(extra_deps_for "$pkg")
    local extras e name
    IFS=',' read -ra extras <<< "$extra"
    for e in "${extras[@]}"; do
        e="$(echo "$e" | sed 's/^ *//; s/ *$//')"; [ -n "$e" ] || continue
        name="${e%% *}"
        echo "$deps" | grep -qE "(^|, )$name( |,|\(|$)" && continue
        deps="${deps:+$deps, }$e"
    done

    mkdir -p "$stage/DEBIAN"
    {
        echo "Package: $pkg"
        echo "Version: ${ver}-$(rev_for "$pkg")"
        echo "Architecture: amd64"
        echo "Maintainer: $MAINT"
        echo "Section: x11"
        echo "Priority: optional"
        [ -n "$deps" ] && echo "Depends: $deps"
        extra_control_for "$pkg"
        echo "Description: $desc"
        echo " Built from upstream source for SekaiOS."
    } > "$stage/DEBIAN/control"

    # 옛 판(다른 버전·리비전)은 지운다 — 두 판이 함께 있으면 finalize 의 *.deb 설치가 꼬인다
    rm -f "$OUT/${pkg}"_*_amd64.deb
    dpkg-deb --root-owner-group --build "$stage" "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb" >/dev/null \
      || die ".deb 생성 실패: $pkg"
    ok "$(basename "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb")"
    printf '     Depends: %s\n' "${deps:-(없음)}" | cut -c1-140

    dpkg -i --force-depends --force-breaks "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb" >/dev/null 2>&1 \
      || die ".deb 설치 실패: $pkg"
    register_shlibs "$pkg" "$ver" "$stage"
    ldconfig
}

# ── 전환용 빈 패키지 (이름을 바꾼 패키지의 옛 이름) ──
build_transitional() {
    local pkg="$1" ver="$2" dep="$3" desc="$4" stage="/build/stage/$1"
    already_built "$pkg" "$ver" && return 0
    say "전환 패키지: $pkg → $dep"
    rm -rf "$stage"; mkdir -p "$stage/DEBIAN" "$stage/usr/share/doc/$pkg"
    printf '패키지:  %s (SekaiOS 전환용 빈 패키지)\n%s 의 새 이름으로 넘어가게 하는 것뿐이다 — 지워도 된다.\n' "$pkg" "$dep" \
        > "$stage/usr/share/doc/$pkg/copyright"
    {
        echo "Package: $pkg"
        echo "Version: ${ver}-$(rev_for "$pkg")"
        echo "Architecture: all"
        echo "Maintainer: $MAINT"
        echo "Section: oldlibs"
        echo "Priority: optional"
        echo "Depends: $dep"
        echo "Description: $desc"
    } > "$stage/DEBIAN/control"
    rm -f "$OUT/${pkg}"_*.deb
    dpkg-deb --root-owner-group --build "$stage" "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_all.deb" >/dev/null \
      || die ".deb 생성 실패: $pkg"
    ok "$(basename "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_all.deb")"
    # 빌드 환경에도 깐다 — 이 이름을 요구하는 플러그인(hyprexpo 등)의 의존성이 맞아야 다음 빌드의 apt 가 멈추지 않는다
    dpkg -i --force-depends "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_all.deb" >/dev/null 2>&1 || die ".deb 설치 실패: $pkg"
}

# ── 이미 만든 .deb 이 있으면 설치만 하고 생략 ────────
# SKIP_BUILT=0 으로 두면 항상 다시 빌드
already_built() {
    local pkg="$1" ver="$2"
    [ "${SKIP_BUILT:-1}" = "1" ] || return 1
    local deb="$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb"
    [ -f "$deb" ] || return 1
    say "생략(이미 빌드됨): $pkg $ver"
    dpkg -i --force-depends --force-breaks "$deb" >/dev/null 2>&1 || die "기존 .deb 설치 실패: $pkg"
    ldconfig
    return 0
}

# ── CMake 프로젝트 빌드 ──────────────────────────────
build_cmake() {
    local repo="$1" tag="$2" pkg="$3" desc="$4"; shift 4
    local ver="${tag#v}"
    local dir="$SRC/$repo" stage="/build/stage/$pkg"

    already_built "$pkg" "$ver" && return 0
    fetch "$repo" "$tag"
    say "빌드(cmake): $pkg $ver"
    rm -rf "$stage"   # 빌드 디렉터리는 유지 (증분 빌드)
    cmake -S "$dir" -B "$dir/build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib/x86_64-linux-gnu \
        "$@" > "/build/log/$pkg.configure.log" 2>&1 \
      || { tail -30 "/build/log/$pkg.configure.log"; die "configure 실패: $pkg"; }
    cmake --build "$dir/build" > "/build/log/$pkg.build.log" 2>&1 \
      || { show_errors "/build/log/$pkg.build.log"; die "컴파일 실패: $pkg"; }
    DESTDIR="$stage" cmake --install "$dir/build" > "/build/log/$pkg.install.log" 2>&1 \
      || { tail -20 "/build/log/$pkg.install.log"; die "설치 실패: $pkg"; }
    declare -F "post_$pkg" >/dev/null && "post_$pkg" "$stage"
    mkdeb "$pkg" "$ver" "$stage" "$desc" "$dir" "$tag"
}

# ── Meson 프로젝트 빌드 ──────────────────────────────
build_meson() {
    local repo="$1" tag="$2" pkg="$3" desc="$4"; shift 4
    local ver="${tag#v}"
    local dir="$SRC/$repo" stage="/build/stage/$pkg"

    already_built "$pkg" "$ver" && return 0
    fetch "$repo" "$tag"
    say "빌드(meson): $pkg $ver"
    rm -rf "$stage"   # 빌드 디렉터리는 유지 (증분 빌드)
    meson setup "$dir/build" "$dir" --prefix=/usr --buildtype=release "$@" \
        > "/build/log/$pkg.configure.log" 2>&1 \
      || { tail -30 "/build/log/$pkg.configure.log"; die "configure 실패: $pkg"; }
    ninja -C "$dir/build" > "/build/log/$pkg.build.log" 2>&1 \
      || { show_errors "/build/log/$pkg.build.log"; die "컴파일 실패: $pkg"; }
    DESTDIR="$stage" ninja -C "$dir/build" install > "/build/log/$pkg.install.log" 2>&1 \
      || { tail -20 "/build/log/$pkg.install.log"; die "설치 실패: $pkg"; }
    mkdeb "$pkg" "$ver" "$stage" "$desc" "$dir" "$tag"
}

# ── Hyprland 플러그인 (저장소 하위 디렉터리를 빌드) ──
#   플러그인은 Hyprland 헤더를 include 하므로 본체와 같은 C++ 표준·shim 이 필요하다
build_plugin() {
    local repo="$1" tag="$2" sub="$3" pkg="$4" ver="$5" desc="$6"
    local dir="$SRC/$repo/$sub" stage="/build/stage/$pkg"

    already_built "$pkg" "$ver" && return 0
    fetch "$repo" "$tag"
    [ -d "$dir" ] || die "플러그인 소스 없음: $dir"
    say "빌드(plugin): $pkg $ver"
    rm -rf "$stage"
    cmake -S "$dir" -B "$dir/build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib/x86_64-linux-gnu \
        -DCMAKE_CXX_STANDARD=26 \
        -DCMAKE_CXX_FLAGS="-include /build/cxx26-compat.hpp" \
        > "/build/log/$pkg.configure.log" 2>&1 \
      || { show_errors "/build/log/$pkg.configure.log"; die "configure 실패: $pkg"; }
    cmake --build "$dir/build" > "/build/log/$pkg.build.log" 2>&1 \
      || { show_errors "/build/log/$pkg.build.log"; die "컴파일 실패: $pkg"; }
    DESTDIR="$stage" cmake --install "$dir/build" > "/build/log/$pkg.install.log" 2>&1 \
      || { tail -20 "/build/log/$pkg.install.log"; die "설치 실패: $pkg"; }

    # 플러그인은 전용 디렉터리로 모아둔다
    mkdir -p "$stage/usr/lib/x86_64-linux-gnu/hyprland"
    find "$stage/usr/lib" -maxdepth 2 -name "${pkg}.so" -not -path "*/hyprland/*" \
        -exec mv {} "$stage/usr/lib/x86_64-linux-gnu/hyprland/" \; 2>/dev/null || true
    mkdeb "$pkg" "$ver" "$stage" "$desc" "$SRC/$repo" "$tag"
}

mkdir -p /build/log /build/stage

# ═══ 빌드 순서 (의존성 순) ═══════════════════════════
build_cmake hyprutils            v0.8.1  hyprutils            "Hyprland utility library"
build_cmake hyprwayland-scanner  v0.4.5  hyprwayland-scanner  "Hyprland Wayland protocol C++ generator"
build_cmake hyprlang             v0.6.3  hyprlang             "Hyprland configuration language parser"
build_cmake hyprcursor           v0.1.12 hyprcursor           "Hyprland cursor theme format library"
build_cmake hyprgraphics         v0.1.5  hyprgraphics         "Hyprland graphics resource library"
build_meson hyprland-protocols   v0.6.4  hyprland-protocols   "Hyprland-specific Wayland protocols"
build_cmake aquamarine           v0.9.2  aquamarine           "Hyprland rendering and backend library"
# C++26 shim 은 포크에 있다 (sekai/cxx26-compat.hpp) — 플러그인 빌드도 같은 것을 쓴다
fetch sekaicompose v0.50.1
install -m644 "$SRC/sekaicompose/sekai/cxx26-compat.hpp" /build/cxx26-compat.hpp
verify_shim
# 이름을 바꾸기 전의 hyprland(합성기 본체, sekai17 이하)가 빌드 환경에 깔려 있으면 지운다 —
#   sekaicomp 가 그 파일을 넘겨받는데, 둘이 함께 남아 있으면 apt 가 빌드 환경을 "깨짐"으로 보고 멈춘다
if v=$(dpkg-query -W -f='${Version}' hyprland 2>/dev/null) && dpkg --compare-versions "$v" lt 0.50.1-sekai18; then
    say "빌드 환경의 옛 hyprland ($v) 지움 — sekaicomp 로 바뀐다"
    dpkg --purge --force-depends hyprland >/dev/null 2>&1 || true
fi
build_cmake sekaicompose         v0.50.1 sekaicomp            "SekaiCompose - the Wayland compositor of SekaiOS (a fork of Hyprland)" \
            -DNO_XWAYLAND=false -DNO_SYSTEMD=false \
            -DCMAKE_CXX_FLAGS="-include /build/cxx26-compat.hpp"

# 전환 패키지: 옛 이름 hyprland → sekaicomp (빈 패키지, 지우지 않고 넘어오게)
build_transitional hyprland 0.50.1 "sekaicomp (>= 0.50.1-sekai18)" \
    "transitional package - the compositor is now sekaicomp (SekaiCompose)"

# Hyprland 플러그인
build_plugin sekaicompose     v0.50.1 plugins/hyprbars hyprbars 0.50.0 \
             "Window title bars for SekaiCompose (from hyprland-plugins)"
build_plugin hyprland-plugins v0.50.0 hyprexpo  hyprexpo  0.50.0 \
             "Hyprland plugin: workspace overview (task view)"

echo
say "전부 완료"
ls -lh "$OUT"/*.deb | awk '{printf "    %-10s %s\n", $5, $9}'
