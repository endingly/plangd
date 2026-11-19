#include "documents.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include <lsp/server_endpoint.h>
#include <ptx_frontend/binding/ptx_symbol_table.hpp>
#include <ptx_frontend/cst/ptx_cst_parser.hpp>
#include <ptx_frontend/syntax/ptx_syntax_lower.hpp>

#include "instruction_docs.hpp"

namespace plangd {
namespace {

/// Byte span of one line in the document text, excluding its terminator.
struct SourceLine {
  std::size_t offset;
  std::size_t length;
};

/// A snapshot of the editor buffer at the last accepted document version.
struct Document {
  std::string text;
  lsp::Int version;
  std::vector<SourceLine> lines{};
  std::vector<InstructionOccurrence> instructions{};
  // Retain the CST until the first navigation request builds the symbol table.
  std::optional<ptx_frontend::syntax_cst::CstFile> cst{};
  std::optional<ptx_frontend::binding::SymbolTable> symbols{};
};

/// A bound symbol and the range of the reference under the cursor.
struct ResolvedReference {
  const ptx_frontend::binding::Symbol& symbol;
  ptx_frontend::SourceRange range;
};

/// Indexes the text by line, treating CRLF as one line break.
std::vector<SourceLine> indexLines(std::string_view text) {
  std::vector<SourceLine> lines;
  std::size_t start = 0;
  auto end = text.find_first_of("\r\n", start);
  while (end != std::string_view::npos) {
    lines.push_back({start, end - start});
    start = end + (text.substr(end).starts_with("\r\n") ? 2 : 1);
    end = text.find_first_of("\r\n", start);
  }
  lines.push_back({start, text.size() - start});
  return lines;
}

/// Borrows the bytes of an indexed line, excluding its line break.
std::string_view lineText(const Document& document, std::size_t line_index) {
  const auto& line = document.lines[line_index];
  return std::string_view(document.text).substr(line.offset, line.length);
}

/// Counts the UTF-16 units contributed by one byte of UTF-8 text.
lsp::Uint utf16Width(char value) {
  const auto byte = static_cast<unsigned char>(value);
  if ((byte & 0xc0) == 0x80) {
    return 0;
  }
  return byte >= 0xf0 ? 2 : 1;
}

/// Converts the frontend's one-based byte position into a UTF-16 LSP position.
lsp::Position toLspPosition(const Document& document,
                            ptx_frontend::SourcePos source) {
  const auto line_index = std::min(
      static_cast<std::size_t>(std::max(source.line, 1) - 1),
      document.lines.size() - 1);
  const auto line = lineText(document, line_index);
  auto byte_column = std::min(
      static_cast<std::size_t>(std::max(source.column, 1) - 1), line.size());
  // A lexer error may land inside a code point. Use its starting boundary.
  while (byte_column != 0 && byte_column < line.size() &&
         utf16Width(line[byte_column]) == 0) {
    --byte_column;
  }

  lsp::Uint utf16_column = 0;
  for (char value : line.substr(0, byte_column)) {
    utf16_column += utf16Width(value);
  }
  return {.line = static_cast<lsp::Uint>(line_index), .character = utf16_column};
}

/// Converts a UTF-16 cursor to a one-based byte position in the same buffer.
std::optional<ptx_frontend::SourcePos> toSourcePosition(
    const Document& document, lsp::Position position) {
  if (position.line >= document.lines.size()) {
    return std::nullopt;
  }
  const auto line = lineText(document, position.line);
  std::size_t byte_column = 0;
  lsp::Uint utf16_column = 0;
  for (char value : line) {
    const auto width = utf16Width(value);
    if (utf16_column + width > position.character) {
      break;
    }
    utf16_column += width;
    ++byte_column;
  }
  return ptx_frontend::SourcePos{static_cast<std::int32_t>(position.line) + 1,
                                 static_cast<std::int32_t>(byte_column) + 1};
}

/// Converts a frontend range using the current document snapshot.
lsp::Range toLspRange(const Document& document, ptx_frontend::SourceRange range) {
  return {.start = toLspPosition(document, range.start),
          .end = toLspPosition(document, range.end)};
}

/// Parses the editor snapshot and replaces its diagnostics, even when empty.
void publishDiagnostics(lsp::ServerEndpoint& endpoint,
                        const lsp::DocumentUri& uri, Document& document) {
  document.lines = indexLines(document.text);
  auto result = ptx_frontend::PtxCstParser(document.text).parseModule();
  if (result) {
    document.instructions = indexInstructions(*result);
  }
  document.cst = std::move(result.value);
  lsp::PublishDiagnosticsParams params{
      .uri = uri, .diagnostics = {}, .version = document.version};
  params.diagnostics.reserve(result.diagnostics.size());
  for (auto& diagnostic : result.diagnostics) {
    params.diagnostics.push_back(lsp::Diagnostic{
        .range = toLspRange(document, diagnostic.range),
        .message = std::move(diagnostic.message),
        .severity = lsp::DiagnosticSeverity::Error,
        .source = "plangd",
    });
  }
  endpoint.textDocumentPublishDiagnostics(params);
}

/// Orders frontend positions by line and byte column.
auto coordinates(ptx_frontend::SourcePos point) {
  return std::pair{point.line, point.column};
}

/// Resolves a cursor on a register or branch-label reference in this snapshot.
std::optional<ResolvedReference> findReference(Document& document,
                                               ptx_frontend::SourcePos source) {
  if (document.cst) {
    auto ast = ptx_frontend::lowerSyntaxModule(*document.cst);
    if (ast) {
      document.symbols = ptx_frontend::binding::bindSymbols(*ast).table;
    }
    document.cst.reset();
  }
  if (!document.symbols) {
    return std::nullopt;
  }

  const auto cursor = coordinates(source);
  const auto& references = document.symbols->references();
  const auto reference = std::ranges::find_if(references, [&](const auto& ref) {
    return ref.target && coordinates(ref.range.start) <= cursor &&
           cursor < coordinates(ref.range.end);
  });
  if (reference == references.end()) {
    return std::nullopt;
  }
  const auto& symbol = document.symbols->symbol(reference->target->symbol);
  if (symbol.kind != ptx_frontend::binding::SymbolKind::Label &&
      symbol.state_space != ptx_frontend::base::DeclarationStateSpace::Register) {
    return std::nullopt;
  }
  return ResolvedReference{symbol, reference->range};
}

/// Returns the declaration location of the reference under the cursor.
lsp::TextDocumentDefinitionResult findDefinition(Document& document,
                                                 const lsp::DocumentUri& uri,
                                                 lsp::Position position) {
  const auto source = toSourcePosition(document, position);
  if (!source) {
    return nullptr;
  }
  const auto reference = findReference(document, *source);
  if (!reference) {
    return nullptr;
  }
  return lsp::Definition{lsp::Location{
      .uri = uri,
      .range = toLspRange(document, reference->symbol.declaration_range)}};
}

/// Formats the bound label or register declaration for display.
std::string describeSymbol(const ptx_frontend::binding::Symbol& symbol) {
  if (symbol.kind == ptx_frontend::binding::SymbolKind::Label) {
    return symbol.name + ':';
  }
  std::string declaration = ".reg ";
  if (symbol.vector_width) {
    declaration += ".v" + std::to_string(*symbol.vector_width) + ' ';
  }
  if (symbol.type) {
    declaration += *symbol.type + ' ';
  }
  declaration += symbol.name;
  if (symbol.parameterized_count) {
    declaration += '<' + std::to_string(*symbol.parameterized_count) + '>';
  }
  declaration += ';';
  return declaration;
}

/// Uses PTX code fences when the client requested Markdown hover content.
std::string formatCode(std::string_view text, lsp::MarkupKind kind) {
  if (kind == lsp::MarkupKind::Markdown) {
    return "```ptx\n" + std::string(text) + "\n```";
  }
  return std::string(text);
}

/// Combines documented forms, keeping each syntax next to its description.
std::string describeInstruction(const InstructionOccurrence& instruction,
                                lsp::MarkupKind kind) {
  std::string text;
  const auto append = [&text](std::string_view paragraph) {
    if (paragraph.empty()) {
      return;
    }
    if (!text.empty()) {
      text += "\n\n";
    }
    text += paragraph;
  };
  for (const auto& form : instruction.forms) {
    if (!form.syntax.empty()) {
      append(formatCode(form.syntax, kind));
    }
    append(form.description);
  }
  return text;
}

/// Builds a hover response whose highlight matches the symbol or mnemonic.
lsp::Hover makeHover(const Document& document, ptx_frontend::SourceRange range,
                     std::string text, lsp::MarkupKind kind) {
  return lsp::Hover{
      .contents = lsp::MarkupContent{.kind = kind, .value = std::move(text)},
      .range = toLspRange(document, range),
  };
}

/// Looks up mnemonics first, then bound references in the editor snapshot.
lsp::TextDocumentHoverResult findHover(Document& document, lsp::Position position,
                                      lsp::MarkupKind kind) {
  const auto source = toSourcePosition(document, position);
  if (!source) {
    return nullptr;
  }

  const auto cursor = coordinates(*source);
  const auto instruction = std::ranges::upper_bound(
      document.instructions, cursor, {}, [](const InstructionOccurrence& entry) {
        return coordinates(entry.range.end);
      });
  if (instruction != document.instructions.end() &&
      coordinates(instruction->range.start) <= cursor) {
    return makeHover(document, instruction->range,
                     describeInstruction(*instruction, kind), kind);
  }
  const auto reference = findReference(document, *source);
  if (!reference) {
    return nullptr;
  }
  return makeHover(document, reference->range,
                   formatCode(describeSymbol(reference->symbol), kind), kind);
}

}  // namespace

void registerDocumentHandlers(lsp::ServerEndpoint& endpoint,
                              std::shared_ptr<const lsp::MarkupKind> hover_kind) {
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

  endpoint.onTextDocumentDefinition(
      [documents](lsp::DefinitionParams&& params)
          -> lsp::TextDocumentDefinitionResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return findDefinition(entry->second, entry->first, params.position);
      });

  endpoint.onTextDocumentHover(
      [documents, hover_kind = std::move(hover_kind)](lsp::HoverParams&& params)
          -> lsp::TextDocumentHoverResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return findHover(entry->second, params.position, *hover_kind);
      });
}

}  // namespace plangd
