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
  auto options = std::make_shared<DocumentClientOptions>(
      DocumentClientOptions{.hover_kind = lsp::MarkupKind::PlainText});
  endpoint.onInitialize(
      [version = std::string(version), options](lsp::InitializeParams&& params) {
        options->hover_kind = hoverKind(params.capabilities);
        const auto& workspace = params.capabilities.workspace;
        options->versioned_edits = workspace && workspace->workspaceEdit &&
                                  workspace->workspaceEdit->documentChanges.value_or(false);
        const auto& document = params.capabilities.textDocument;
        options->diagnostic_related_information =
            document && document->publishDiagnostics &&
            document->publishDiagnostics->relatedInformation.value_or(false);
        options->hierarchical_document_symbols =
            document && document->documentSymbol &&
            document->documentSymbol->hierarchicalDocumentSymbolSupport.value_or(false);
        lsp::OneOf<lsp::Bool, lsp::RenameOptions> rename = true;
        if (document && document->rename && document->rename->prepareSupport.value_or(false)) {
          rename = lsp::RenameOptions{.prepareProvider = true};
        }
        return lsp::InitializeResult{
            .capabilities =
                {
                    .positionEncoding = lsp::PositionEncodingKind::UTF16,
                    .textDocumentSync = lsp::TextDocumentSyncOptions{
                        .openClose = true,
                        .change = lsp::TextDocumentSyncKind::Full,
                    },
                    .completionProvider = lsp::CompletionOptions{
                        .triggerCharacters = lsp::Array<lsp::String>{".", "%"},
                    },
                    .hoverProvider = true,
                    .definitionProvider = true,
                    .referencesProvider = true,
                    .documentSymbolProvider = true,
                    .renameProvider = std::move(rename),
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
  registerDocumentHandlers(endpoint, options);
}

}  // namespace plangd
