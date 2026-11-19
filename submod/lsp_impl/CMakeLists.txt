find_package(lsp 2.0 CONFIG REQUIRED)
find_package(ptx_frontend CONFIG REQUIRED COMPONENTS cst)

add_library(plangd_lsp_impl STATIC src/server.cpp)
target_include_directories(plangd_lsp_impl PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(plangd_lsp_impl
    PUBLIC lsp::lsp
    PRIVATE ptx_frontend::cst
)
