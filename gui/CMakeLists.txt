cmake_minimum_required(VERSION 3.19)
project(ryzen-curve-optimizer VERSION 2.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.4 REQUIRED COMPONENTS Widgets Svg)

# Where the root helper lives. Must match the polkit exec.path annotation.
set(RCO_HELPER_DIR "/usr/lib/ryzen-curve-optimizer" CACHE PATH "Installed root helper directory")

add_executable(ryzen-curve-optimizer
    src/main.cpp
    src/theme.cpp      src/theme.h
    src/privileged.cpp src/privileged.h
    src/trialbar.cpp   src/trialbar.h
    src/ryzentab.cpp   src/ryzentab.h
    src/sysfs.h
)

# Theme glyphs (checkbox tick, spin/combo chevrons), @2x for HiDPI. Compiled
# with qt_add_resources, not AUTORCC: CMake's Makefile generator runs the
# AUTORCC step through /bin/sh without escaping '(' — a checkout under a path
# like "files (1)" failed with a shell syntax error.
qt_add_resources(ryzen-curve-optimizer theme
    PREFIX /theme
    BASE data/theme
    FILES
        data/theme/check.png data/theme/check@2x.png
        data/theme/chev-down.png data/theme/chev-down@2x.png
        data/theme/chev-up.png data/theme/chev-up@2x.png
        data/theme/chev-down-off.png data/theme/chev-down-off@2x.png
        data/theme/chev-up-off.png data/theme/chev-up-off@2x.png)
# App icon (window fallback when the icon theme has none): the same SVG that is installed to hicolor.
qt_add_resources(ryzen-curve-optimizer appicon
    PREFIX /
    BASE ${CMAKE_CURRENT_SOURCE_DIR}/..
    FILES ../icon.svg)

target_compile_definitions(ryzen-curve-optimizer PRIVATE
    RCO_VERSION="${PROJECT_VERSION}"
    RCO_HELPER_DIR="${RCO_HELPER_DIR}"
    QT_NO_KEYWORDS)
target_compile_options(ryzen-curve-optimizer PRIVATE -Wall -Wextra -Wpedantic)

# Hardening. PIE via -pie on the link only: POSITION_INDEPENDENT_CODE put -fPIE on the link line,
# and with LTO the final codegen followed it (copy relocations of Qt data, which Qt6 does not
# support). The objects are already -fPIC (Qt6::Core adds it), which a PIE executable accepts.
option(RCO_HARDEN "Stack protector, FORTIFY_SOURCE=3, CET, full RELRO, PIE" ON)
if(RCO_HARDEN)
    target_link_options(ryzen-curve-optimizer PRIVATE -pie -Wl,-z,relro,-z,now -Wl,-z,noexecstack)
    target_compile_options(ryzen-curve-optimizer PRIVATE
        -fstack-protector-strong -fstack-clash-protection
        $<$<NOT:$<CONFIG:Debug>>:-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3>)
    target_compile_definitions(ryzen-curve-optimizer PRIVATE _GLIBCXX_ASSERTIONS)
    include(CheckCXXCompilerFlag)
    check_cxx_compiler_flag(-fcf-protection=full _cet)
    if(_cet)
        target_compile_options(ryzen-curve-optimizer PRIVATE -fcf-protection=full)
    endif()
endif()

target_link_libraries(ryzen-curve-optimizer PRIVATE Qt6::Widgets Qt6::Svg)
