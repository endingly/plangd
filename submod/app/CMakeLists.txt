configure_file(src/version.hpp.in generated/version.hpp @ONLY)

add_executable(plangd src/main.cpp)
add_executable(plangd::plangd ALIAS plangd)
target_include_directories(plangd PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(plangd PRIVATE plangd_lsp_impl)

if(BUILD_TESTING)
    add_test(NAME plangd.stdio
        COMMAND Python3::Interpreter "${CMAKE_CURRENT_SOURCE_DIR}/test/stdio.py"
            $<TARGET_FILE:plangd> "${PROJECT_VERSION}"
    )
    set_tests_properties(plangd.stdio PROPERTIES TIMEOUT 20)
endif()
