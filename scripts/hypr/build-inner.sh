#!/bin/bash
# buildroot 내부에서 실행됨 — hyprland 생태계를 빌드하고 .deb 으로 포장
set -euo pipefail

SRC=/build/src
OUT=/build/deb
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
rev_for() { case "$1" in hyprbars) echo sekai9 ;; hyprland) echo sekai6 ;; *) echo "$REV" ;; esac; }

mkdir -p "$SRC" "$OUT"
export CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)"
export MAKEFLAGS="-j$(nproc)"

C_B=$'\033[1;36m'; C_G=$'\033[1;32m'; C_R=$'\033[1;31m'; C_0=$'\033[0m'
say(){ printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok(){  printf '%s  ok%s %s\n' "$C_G" "$C_0" "$*"; }
die(){ printf '%s  ERROR: %s%s\n' "$C_R" "$*" "$C_0"; exit 1; }

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

# ── Hyprland 소스 패치 (GCC 14 호환) ─────────────────
# 업스트림은 GCC 15 / 롤링 배포판을 전제로 개발한다.
# trixie(GCC 14)에 없는 기능 3가지를 동등한 코드로 치환한다. 모두 멱등.
patch_hyprland() {
    local dir="$SRC/Hyprland"
    [ -d "$dir" ] || die "Hyprland 소스 없음"
    say "GCC 14 호환 패치"

    python3 - "$dir" <<'PYEOF'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
changed = []

# ① #embed (C++26 전처리기, GCC 15+) → 바이트 목록을 직접 생성해서 #include
hdr  = root / "src/config/defaultConfig.hpp"
conf = root / "example/hyprland.conf"
inc  = root / "src/config/example_config_bytes.inc"
t = hdr.read_text()
if "#embed" in t:
    data = conf.read_bytes()
    vals = [str(b if b < 128 else b - 256) for b in data]           # char 는 signed
    rows = [",".join(vals[i:i+20]) for i in range(0, len(vals), 20)]
    inc.write_text("// SekaiOS: #embed 대체 (자동 생성)\n" + ",\n".join(rows) + "\n")
    t = t.replace('#embed "../../example/hyprland.conf"',
                  '#include "example_config_bytes.inc"')
    hdr.write_text(t)
    changed.append(f"#embed -> #include ({len(data)} bytes)")

# ② 삼항 연산자에서 사용자 정의 변환을 GCC14 가 못 찾음 → 명시적 캐스트
xwm = root / "src/xwayland/XWM.hpp"
t = xwm.read_text()
old = "return m_connection ? *m_connection : nullptr;"
new = "return m_connection ? static_cast<xcb_connection_t*>(*m_connection) : nullptr;"
if old in t:
    xwm.write_text(t.replace(old, new))
    changed.append("XWM.hpp ?: 명시적 캐스트")

# ③ std::vector::insert_range (C++23, libstdc++ GCC 15+) → 동등한 insert
mon = root / "src/helpers/Monitor.cpp"
t = mon.read_text()
old = "requestedModes.insert_range(requestedModes.end(), sortedModes | std::views::reverse);"
new = "requestedModes.insert(requestedModes.end(), sortedModes.rbegin(), sortedModes.rend());"
if old in t:
    mon.write_text(t.replace(old, new))
    changed.append("Monitor.cpp insert_range -> insert")

if changed:
    for c in changed: print(f"    적용: {c}")
else:
    print("    (이미 전부 적용됨)")

# 잔존 확인
import subprocess
leftover = []
if "#embed" in hdr.read_text(): leftover.append("#embed")
if "insert_range" in mon.read_text(): leftover.append("insert_range")
if leftover:
    print("    !! 잔존:", ", ".join(leftover)); sys.exit(1)
PYEOF
    [ $? -eq 0 ] || die "패치 실패"
    say "SekaiOS 패치: 창이 그린 제목줄 끌기"
    python3 /build/patch-hyprland-clientmove.py "$dir" || die "패치 실패 (clientmove)"
    python3 /build/patch-hyprland-keepoutputs.py "$dir" || die "패치 실패 (keepoutputs)"
    python3 /build/patch-hyprland-bordergrab.py "$dir" || die "패치 실패 (bordergrab)"
    python3 /build/patch-hyprland-layerfocus.py "$dir" || die "패치 실패 (layerfocus)"
    ok "패치 완료"
}

# ── git clone (태그 고정) ────────────────────────────
fetch() {
    local repo="$1" tag="$2" dir="$SRC/$1"
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
post_hyprland() {
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
        hyprland)     echo "hyprutils, hyprlang, hyprcursor, hyprgraphics, aquamarine, xwayland, binutils" ;;
        hyprbars|hyprexpo) echo "hyprland" ;;
        *)            echo "" ;;
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
    lic="$src/LICENSE"; [ -f "$lic" ] || lic="$src/COPYING"
    [ -f "$lic" ] || die "라이선스 파일 없음: $src"
    local doc="$stage/usr/share/doc/$pkg"
    mkdir -p "$doc"
    {
        echo "패키지:  $pkg (SekaiOS 빌드)"
        echo "원본:    https://github.com/hyprwm/$repo  (태그 $tag)"
        case "$pkg" in
            hyprland) echo "수정:    GCC 14 빌드 호환 패치, 창이 그린 제목줄 끌기 (scripts/hypr/build-inner.sh 의 patch_hyprland, patch-hyprland-*.py)" ;;
            hyprbars) echo "수정:    창 조작 버튼 벡터 아이콘·마우스 올림 배경·끌어서 스냅 알림 (scripts/hypr/patch-hyprbars-*.py)" ;;
            *)        echo "수정:    없음 (원본 그대로 빌드)" ;;
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
        ( cd "$stage" && mkdir -p debian && : > debian/control
          dpkg-shlibdeps -O --ignore-missing-info "${bins[@]}" ) > /tmp/.sd 2> "$sdlog"
        local rc=$?
        deps=$(sed -n 's/^shlibs:Depends=//p' /tmp/.sd | head -1)
        rm -rf "$stage/debian" /tmp/.sd
        if [ $rc -ne 0 ] || [ -z "$deps" ]; then
            warn "dpkg-shlibdeps 결과 이상 (rc=$rc) — $sdlog"
            head -5 "$sdlog" | sed 's/^/     /'
        fi
    fi

    # 안전망: shlibs 로 이미 잡힌 건 빼고 없는 것만 추가 (중복 방지)
    extra=$(extra_deps_for "$pkg")
    for e in ${extra//,/ }; do
        case ",${deps// /}," in
            *",$e,"*|*",$e("*) continue ;;
        esac
        echo "$deps" | grep -qE "(^|, )$e( |,|\()" && continue
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
        echo "Description: $desc"
        echo " Built from upstream source for SekaiOS."
    } > "$stage/DEBIAN/control"

    # 옛 판(다른 버전·리비전)은 지운다 — 두 판이 함께 있으면 finalize 의 *.deb 설치가 꼬인다
    rm -f "$OUT/${pkg}"_*_amd64.deb
    dpkg-deb --root-owner-group --build "$stage" "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb" >/dev/null \
      || die ".deb 생성 실패: $pkg"
    ok "$(basename "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb")"
    printf '     Depends: %s\n' "${deps:-(없음)}" | cut -c1-140

    dpkg -i --force-depends "$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb" >/dev/null 2>&1 \
      || die ".deb 설치 실패: $pkg"
    register_shlibs "$pkg" "$ver" "$stage"
    ldconfig
}

# ── 이미 만든 .deb 이 있으면 설치만 하고 생략 ────────
# SKIP_BUILT=0 으로 두면 항상 다시 빌드
already_built() {
    local pkg="$1" ver="$2"
    [ "${SKIP_BUILT:-1}" = "1" ] || return 1
    local deb="$OUT/${pkg}_${ver}-$(rev_for "$pkg")_amd64.deb"
    [ -f "$deb" ] || return 1
    say "생략(이미 빌드됨): $pkg $ver"
    dpkg -i "$deb" >/dev/null 2>&1 || die "기존 .deb 설치 실패: $pkg"
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
    if [ "$pkg" = hyprbars ]; then
        python3 /build/patch-hyprbars-icons.py "$dir/barDeco.cpp" || die "hyprbars 패치 실패"
        python3 /build/patch-hyprbars-hover.py "$dir/barDeco.cpp" || die "hyprbars 패치 실패 (hover)"
        python3 /build/patch-hyprbars-snap.py "$dir/barDeco.cpp" || die "hyprbars 패치 실패 (snap)"
        python3 /build/patch-hyprbars-theme.py "$dir/barDeco.cpp" || die "hyprbars 패치 실패 (theme)"
        python3 /build/patch-hyprbars-bordergrab.py "$dir/barDeco.cpp" || die "hyprbars 패치 실패 (bordergrab)"
    fi

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
verify_shim
if [ ! -f "$OUT/hyprland_0.50.1-$(rev_for hyprland)_amd64.deb" ] || [ "${SKIP_BUILT:-1}" != "1" ]; then
    fetch Hyprland v0.50.1
    patch_hyprland
fi
build_cmake Hyprland             v0.50.1 hyprland             "Dynamic tiling Wayland compositor" \
            -DNO_XWAYLAND=false -DNO_SYSTEMD=false \
            -DCMAKE_CXX_FLAGS="-include /build/cxx26-compat.hpp"

# Hyprland 플러그인
build_plugin hyprland-plugins v0.50.0 hyprbars  hyprbars  0.50.0 \
             "Hyprland plugin: window title bars"
build_plugin hyprland-plugins v0.50.0 hyprexpo  hyprexpo  0.50.0 \
             "Hyprland plugin: workspace overview (task view)"

echo
say "전부 완료"
ls -lh "$OUT"/*.deb | awk '{printf "    %-10s %s\n", $5, $9}'
