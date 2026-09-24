// SekaiOS — C++26 P2591 (std::string + std::string_view) 백포트
//
// Hyprland 는 C++26 의 "문자열 + 문자열뷰" 연결을 사용한다.
// libstdc++ 는 GCC 15 부터 구현했고 trixie 는 GCC 14 이므로 직접 채운다.
//
// ★ 중요: 표준 헤더를 먼저 인클루드해야 _GLIBCXX_RELEASE 가 정의된다.
//    (-include 로 이 파일이 최우선 처리되므로, 가드를 앞에 두면 항상 거짓이 된다)
#pragma once

#include <string>
#include <string_view>
#include <utility>

#if defined(_GLIBCXX_RELEASE) && (_GLIBCXX_RELEASE < 15)

#define SEKAI_CXX26_STRCAT_SHIM 1

// std::string + std::string_view
inline std::string operator+(const std::string& lhs, std::string_view rhs) {
    std::string out;
    out.reserve(lhs.size() + rhs.size());
    out.assign(lhs);
    out.append(rhs);
    return out;
}

inline std::string operator+(std::string&& lhs, std::string_view rhs) {
    lhs.append(rhs);
    return std::move(lhs);
}

// std::string_view + std::string
inline std::string operator+(std::string_view lhs, const std::string& rhs) {
    std::string out;
    out.reserve(lhs.size() + rhs.size());
    out.assign(lhs);
    out.append(rhs);
    return out;
}

inline std::string operator+(std::string_view lhs, std::string&& rhs) {
    rhs.insert(0, lhs);
    return std::move(rhs);
}

#else
#define SEKAI_CXX26_STRCAT_SHIM 0
#endif
