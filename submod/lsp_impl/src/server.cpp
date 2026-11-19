#include <plangd/lsp_impl/server.hpp>

#include <string>

#include <lsp/server_endpoint.h>

#include "documents.hpp"

namespace plangd {

void registerHandlers(lsp::ServerEndpoint& endpoint, std::string_view version,
                      bool& shutdown_requested) {
  endpoint.onInitialize([version = std::string(version)](lsp::InitializeParams&&) {
    return lsp::InitializeResult{
        .capabilities =
            {
                .positionEncoding = lsp::PositionEncodingKind::UTF16,
                .textDocumentSync = lsp::TextDocumentSyncOptions{
                    .openClose = true, .change = lsp::TextDocumentSyncKind::Full},
                .definitionProvider = true,
            },
        .serverInfo = lsp::ServerInfo{.name = "plangd", .version = version},
    };
  });
  endpoint.onInitialized([](lsp::InitializedParams&&) {});
  endpoint.onShutdown([&shutdown_requested]() -> lsp::ShutdownResult {
    shutdown_requested = true;
    return {};
  });
  endpoint.onExit([] {});
  registerDocumentHandlers(endpoint);
}

}  // namespace plangd
