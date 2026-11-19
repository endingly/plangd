#include <plangd/lsp_impl/server.hpp>

#include <string>

#include <lsp/server_endpoint.h>

namespace plangd {

void registerLifecycleHandlers(lsp::ServerEndpoint& endpoint,
                               std::string_view version,
                               bool& shutdown_requested) {
  endpoint.onInitialize([version = std::string(version)](lsp::InitializeParams&&) {
    return lsp::InitializeResult{
        .capabilities = {},
        .serverInfo = lsp::ServerInfo{.name = "plangd", .version = version},
    };
  });
  endpoint.onInitialized([](lsp::InitializedParams&&) {});
  endpoint.onShutdown([&shutdown_requested]() -> lsp::ShutdownResult {
    shutdown_requested = true;
    return {};
  });
  endpoint.onExit([] {});
}

}  // namespace plangd
