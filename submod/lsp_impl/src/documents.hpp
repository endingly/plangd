#pragma once

namespace lsp {
class ServerEndpoint;
}

namespace plangd {

/// Registers synchronization, diagnostics and navigation for open PTX documents.
void registerDocumentHandlers(lsp::ServerEndpoint& endpoint);

}  // namespace plangd
