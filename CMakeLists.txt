cmake_minimum_required(VERSION 3.28)
list(PREPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Select optional manifest dependencies before the vcpkg toolchain is loaded.
option(PLANGD_BUILD_BENCHMARKS "Install optional benchmark dependencies" OFF)
if(PLANGD_BUILD_BENCHMARKS)
    list(APPEND VCPKG_MANIFEST_FEATURES benchmarks)
    list(REMOVE_DUPLICATES VCPKG_MANIFEST_FEATURES)
endif()

project(plangd VERSION 0.0.1 LANGUAGES C CXX)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

option(PLANGD_USE_CCACHE "Use ccache when available" ON)
if(PLANGD_USE_CCACHE)
    find_program(CCACHE_EXECUTABLE ccache)
    if(CCACHE_EXECUTABLE)
        set(CMAKE_C_COMPILER_LAUNCHER "${CCACHE_EXECUTABLE}")
        set(CMAKE_CXX_COMPILER_LAUNCHER "${CCACHE_EXECUTABLE}")
    endif()
endif()

option(BUILD_TESTING "Build plangd tests" OFF)
include(CTest)

add_subdirectory(submod)

include(install_project_targets)
install_project_targets(
    PROJECT "${PROJECT_NAME}"
    VERSION "${PROJECT_VERSION}"
    TARGETS plangd
    COMPONENTS plangd
)
