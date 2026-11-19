add_library(plangd_common INTERFACE)
add_library(plangd::common ALIAS plangd_common)
set_property(TARGET plangd_common PROPERTY EXPORT_NAME common)
target_compile_features(plangd_common INTERFACE cxx_std_23)

configure_file(include/version.hpp.in include/version.hpp @ONLY)
register_headers(
    TARGET plangd_common
    MODULE_NAME common
    INTERFACE
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${CMAKE_CURRENT_BINARY_DIR}/include"
)

if(BUILD_TESTING)
    add_executable(plangd_common_version_test test/version.cpp)
    target_link_libraries(plangd_common_version_test PRIVATE plangd::common)
    target_compile_definitions(plangd_common_version_test PRIVATE
        PLANGD_EXPECTED_VERSION="${PROJECT_VERSION}")
    add_test(NAME plangd.common.version COMMAND plangd_common_version_test)
endif()
