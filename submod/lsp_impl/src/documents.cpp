#include "documents.hpp"

#include <algorithm>
#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include <lsp/server_endpoint.h>
#include <ptx_frontend/cst/ptx_cst_parser.hpp>

namespace plangd {
namespace {

/// A snapshot of the editor buffer at the last accepted document version.
struct Document {
  std::string text;
  lsp::Int version;
};

/// Splits text into line views, treating CRLF as one line break.
std::vector<std::string_view> splitLines(std::string_view text) {
  std::vector<std::string_view> lines;
  auto end = text.find_first_of("\r\n");
  while (end != std::string_view::npos) {
    lines.push_back(text.substr(0, end));
    text.remove_prefix(end + (text.substr(end).starts_with("\r\n") ? 2 : 1));
    end = text.find_first_of("\r\n");
  }
  lines.push_back(text);
  return lines;
}

/// Converts the frontend's one-based byte position into a UTF-16 LSP position.
lsp::Position toLspPosition(const std::vector<std::string_view>& lines,
                            ptx_frontend::SourcePos source) {
  const auto line_index = std::min(
      static_cast<std::size_t>(std::max(source.line, 1) - 1), lines.size() - 1);
  const auto line = lines[line_index];
  auto byte_column = std::min(
      static_cast<std::size_t>(std::max(source.column, 1) - 1), line.size());
  const auto is_continuation = [](unsigned char byte) {
    return (byte & 0xc0) == 0x80;
  };
  // A lexer error may land inside a code point. Use its starting boundary.
  while (byte_column != 0 && byte_column < line.size() &&
         is_continuation(static_cast<unsigned char>(line[byte_column]))) {
    --byte_column;
  }

  lsp::Uint utf16_column = 0;
  for (char value : line.substr(0, byte_column)) {
    const auto byte = static_cast<unsigned char>(value);
    if (!is_continuation(byte)) {
      utf16_column += byte >= 0xf0 ? 2 : 1;
    }
  }
  return {.line = static_cast<lsp::Uint>(line_index), .character = utf16_column};
}

/// Parses the editor snapshot and replaces its diagnostics, even when empty.
void publishDiagnostics(lsp::ServerEndpoint& endpoint,
                        const lsp::DocumentUri& uri, const Document& document) {
  auto result = ptx_frontend::PtxCstParser(document.text).parseModule();
  lsp::PublishDiagnosticsParams params{
      .uri = uri, .diagnostics = {}, .version = document.version};
  if (!result.diagnostics.empty()) {
    const auto lines = splitLines(document.text);
    params.diagnostics.reserve(result.diagnostics.size());
    for (auto& diagnostic : result.diagnostics) {
      params.diagnostics.push_back(lsp::Diagnostic{
          .range = {.start = toLspPosition(lines, diagnostic.range.start),
                    .end = toLspPosition(lines, diagnostic.range.end)},
          .message = std::move(diagnostic.message),
          .severity = lsp::DiagnosticSeverity::Error,
          .source = "plangd",
      });
    }
  }
  endpoint.textDocumentPublishDiagnostics(params);
}

}  // namespace

void registerDocumentHandlers(lsp::ServerEndpoint& endpoint) {
  using Documents = std::unordered_map<lsp::DocumentUri, Document>;
  auto documents = std::make_shared<Documents>();
  endpoint.onTextDocumentDidOpen(
      [&endpoint, documents](lsp::DidOpenTextDocumentParams&& params) {
        auto& item = params.textDocument;
        if (item.languageId != "ptx" && !item.uri.path().ends_with(".ptx")) {
          return;
        }
        auto [entry, inserted] = documents->try_emplace(
            std::move(item.uri), Document{std::move(item.text), item.version});
        if (inserted) {
          publishDiagnostics(endpoint, entry->first, entry->second);
        }
      });

  endpoint.onTextDocumentDidChange(
      [&endpoint, documents](lsp::DidChangeTextDocumentParams&& params) {
        const auto& item = params.textDocument;
        auto entry = documents->find(item.uri);
        if (entry == documents->end() || item.version <= entry->second.version ||
            params.contentChanges.empty()) {
          return;
        }
        // The final whole-document change supersedes all preceding changes.
        auto* change = std::get_if<lsp::TextDocumentContentChangeWholeDocument>(
            &params.contentChanges.back());
        if (change == nullptr) {
          return;
        }
        entry->second = Document{std::move(change->text), item.version};
        publishDiagnostics(endpoint, entry->first, entry->second);
      });

  endpoint.onTextDocumentDidClose(
      [&endpoint, documents](lsp::DidCloseTextDocumentParams&& params) {
        auto& uri = params.textDocument.uri;
        if (documents->erase(uri) != 0) {
          endpoint.textDocumentPublishDiagnostics(
              {.uri = std::move(uri), .diagnostics = {}});
        }
      });
}

}  // namespace plangd
