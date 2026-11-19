#include <plangd/lsp_impl/server.hpp>

#include <memory>
#include <string>

#include <lsp/server_endpoint.h>

#include "documents.hpp"

namespace plangd {
namespace {

/// Picks the client's first supported hover format, with plain text as fallback.
lsp::MarkupKind hoverKind(const lsp::ClientCapabilities& capabilities) {
  const auto& text_document = capabilities.textDocument;
  if (text_document && text_document->hover &&
      text_document->hover->contentFormat) {
    for (const auto& kind : *text_document->hover->contentFormat) {
      if (kind == lsp::MarkupKind::Markdown) {
        return lsp::MarkupKind::Markdown;
      }
      if (kind == lsp::MarkupKind::PlainText) {
        return lsp::MarkupKind::PlainText;
      }
    }
  }
  return lsp::MarkupKind::PlainText;
}

}  // namespace

void registerHandlers(lsp::ServerEndpoint& endpoint, std::string_view version,
                      bool& shutdown_requested) {
  auto hover_kind = std::make_shared<lsp::MarkupKind>(lsp::MarkupKind::PlainText);
  endpoint.onInitialize(
      [version = std::string(version), hover_kind](lsp::InitializeParams&& params) {
        *hover_kind = hoverKind(params.capabilities);
        return lsp::InitializeResult{
            .capabilities =
                {
                    .positionEncoding = lsp::PositionEncodingKind::UTF16,
                    .textDocumentSync = lsp::TextDocumentSyncOptions{
                        .openClose = true,
                        .change = lsp::TextDocumentSyncKind::Full,
                    },
                    .completionProvider = lsp::CompletionOptions{},
                    .hoverProvider = true,
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
  registerDocumentHandlers(endpoint, hover_kind);
}

}  // namespace plangd
