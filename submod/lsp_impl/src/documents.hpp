#pragma once

namespace lsp {
class ServerEndpoint;
}

namespace plangd {

/// Registers full synchronization and diagnostics for open PTX documents.
void registerDocumentHandlers(lsp::ServerEndpoint& endpoint);

}  // namespace plangd
