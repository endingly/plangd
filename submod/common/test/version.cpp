#include <plangd/common/version.hpp>

static_assert(plangd::version == PLANGD_EXPECTED_VERSION);

/// Entry point for the public version header smoke test.
int main() { return 0; }
