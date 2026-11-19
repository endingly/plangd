find_package(lsp 2.0 CONFIG REQUIRED)
find_package(ptx_frontend CONFIG REQUIRED COMPONENTS cst syntax binding ptx_spec)

set(codegen_source_dir "${PROJECT_SOURCE_DIR}/python/src")
set(codegen_command
    "${CMAKE_COMMAND}" -E env "PYTHONPATH=${codegen_source_dir}"
    "${Python3_EXECUTABLE}" -m plangd.code_gen
)
execute_process(
    COMMAND ${codegen_command} --help
    RESULT_VARIABLE codegen_status
    OUTPUT_QUIET
    ERROR_VARIABLE codegen_error
)
if(NOT codegen_status STREQUAL "0")
    message(FATAL_ERROR
        "Install the generator dependencies with\n"
        "  \"${Python3_EXECUTABLE}\" -m pip install -e \"${PROJECT_SOURCE_DIR}/python\"\n"
        "${codegen_error}"
    )
endif()

file(GLOB_RECURSE ptx_spec_files CONFIGURE_DEPENDS
    "${ptx_frontend_PTX_SPEC_DIR}/*.yaml"
)
list(FILTER ptx_spec_files EXCLUDE REGEX "\\.schema\\.yaml$")
if(NOT ptx_spec_files)
    message(FATAL_ERROR "No PTX instruction YAML found in ${ptx_frontend_PTX_SPEC_DIR}")
endif()
file(GLOB_RECURSE codegen_inputs CONFIGURE_DEPENDS
    "${codegen_source_dir}/plangd/code_gen/*.py"
    "${codegen_source_dir}/plangd/code_gen/*.j2"
)
set(generated_dir "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(instruction_docs_header "${generated_dir}/instruction_docs.gen.hpp")
set(instruction_docs_source "${generated_dir}/instruction_docs.gen.cpp")
add_custom_command(
    OUTPUT "${instruction_docs_header}" "${instruction_docs_source}"
    COMMAND ${codegen_command} --output "${generated_dir}" ${ptx_spec_files}
    DEPENDS "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/python/pyproject.toml"
        ${codegen_inputs} ${ptx_spec_files}
    VERBATIM
)

add_library(plangd_lsp_impl STATIC
    src/documents.cpp
    src/instruction_docs.cpp
    src/server.cpp
    "${instruction_docs_header}"
    "${instruction_docs_source}"
)
target_include_directories(plangd_lsp_impl
    PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include"
    PRIVATE "${generated_dir}"
)
target_link_libraries(plangd_lsp_impl
    PUBLIC lsp::lsp
    PRIVATE ptx_frontend::cst ptx_frontend::syntax ptx_frontend::binding
)
