#pragma once

#include <memory>

namespace lsp {
class ServerEndpoint;
enum class MarkupKind;
}

namespace plangd {

/// Client capabilities needed when formatting document responses.
struct DocumentClientOptions {
  lsp::MarkupKind hover_kind;
  bool versioned_edits = false;
  bool diagnostic_related_information = false;
  bool hierarchical_document_symbols = false;
  bool line_folding_only = false;
};

/// Registers the open-document handlers using the negotiated client options.
void registerDocumentHandlers(lsp::ServerEndpoint& endpoint,
                              std::shared_ptr<const DocumentClientOptions> options);

}  // namespace plangd
