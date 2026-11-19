find_package(lsp 2.0 CONFIG REQUIRED)
find_package(ptx_frontend CONFIG REQUIRED COMPONENTS cst syntax binding ptx_spec)
find_package(yaml-cpp CONFIG REQUIRED)

add_executable(plangd_instruction_doc_generator
    tools/generate_instruction_docs.cpp
)
target_link_libraries(plangd_instruction_doc_generator PRIVATE yaml-cpp::yaml-cpp)

file(GLOB_RECURSE ptx_spec_files CONFIGURE_DEPENDS
    "${ptx_frontend_PTX_SPEC_DIR}/*.yaml"
)
set(instruction_docs "${CMAKE_CURRENT_BINARY_DIR}/instruction_docs.inc")
add_custom_command(
    OUTPUT "${instruction_docs}"
    COMMAND plangd_instruction_doc_generator "${instruction_docs}" ${ptx_spec_files}
    DEPENDS plangd_instruction_doc_generator ${ptx_spec_files}
    VERBATIM
)

add_library(plangd_lsp_impl STATIC
    src/documents.cpp
    src/instruction_docs.cpp
    src/server.cpp
    "${instruction_docs}"
)
target_include_directories(plangd_lsp_impl
    PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include"
    PRIVATE "${CMAKE_CURRENT_BINARY_DIR}"
)
target_link_libraries(plangd_lsp_impl
    PUBLIC lsp::lsp
    PRIVATE ptx_frontend::cst ptx_frontend::syntax ptx_frontend::binding
)
