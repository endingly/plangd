#pragma once

#include <string_view>

namespace lsp {
class ServerEndpoint;
}

namespace plangd {

/// Registers the LSP lifecycle handlers before the endpoint starts its loop.
/// `shutdown_requested` starts false and must outlive the endpoint.
void registerLifecycleHandlers(lsp::ServerEndpoint& endpoint,
                               std::string_view version,
                               bool& shutdown_requested);

}  // namespace plangd
