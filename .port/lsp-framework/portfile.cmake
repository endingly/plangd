vcpkg_check_linkage(ONLY_STATIC_LIBRARY)

vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO leon-bckl/lsp-framework
    REF "${VERSION}"
    SHA512 53a5705033c365318c44c920e2077d0359ab7e6ce6aea1fc50333d0d6fb94c0b555fc728afe4abf9689a242a142c3d3434730f807ebcadb54c954938bb04f56b
    USE_TARBALL_API
)

vcpkg_cmake_configure(
    SOURCE_PATH "${SOURCE_PATH}"
    OPTIONS
        -DLSP_INSTALL=ON
        -DLSP_BUILD_EXAMPLES=OFF
        -DLSP_BUILD_TESTS=OFF
)

vcpkg_cmake_install()

vcpkg_cmake_config_fixup(
    PACKAGE_NAME lsp
    CONFIG_PATH lib/cmake/lsp
)

file(REMOVE_RECURSE
    "${CURRENT_PACKAGES_DIR}/debug/include"
    "${CURRENT_PACKAGES_DIR}/debug/share"
)

vcpkg_install_copyright(
    FILE_LIST
        "${SOURCE_PATH}/LICENSE"
        "${SOURCE_PATH}/THIRD_PARTY_NOTICES.md"
)
