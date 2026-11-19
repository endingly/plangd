#pragma once

#include <memory>

namespace lsp {
class ServerEndpoint;
enum class MarkupKind;
}

namespace plangd {

/// Registers the open-document handlers using the negotiated hover format.
void registerDocumentHandlers(lsp::ServerEndpoint& endpoint,
                              std::shared_ptr<const lsp::MarkupKind> hover_kind);

}  // namespace plangd
