#include "documents.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include <lsp/error.h>
#include <lsp/server_endpoint.h>
#include <ptx_frontend/binding/ptx_symbol_table.hpp>
#include <ptx_frontend/cst/ptx_cst_parser.hpp>
#include <ptx_frontend/syntax/ptx_syntax_lower.hpp>

#include "completion.hpp"
#include "completion_target.hpp"
#include "instruction_docs.hpp"
#include "symbol_completion.hpp"
#include "symbol_rename.hpp"

namespace plangd {
namespace {

namespace binding = ptx_frontend::binding;

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
  std::vector<CompletionTargetRegion> completion_targets{};
  // Retain the CST until the first symbol request builds the symbol table.
  std::optional<ptx_frontend::syntax_cst::CstFile> cst{};
  std::optional<ptx_frontend::binding::SymbolTable> symbols{};
};

/// A bound symbol and the range of the reference under the cursor.
struct ResolvedReference {
  const ptx_frontend::binding::Symbol& symbol;
  ptx_frontend::SourceRange range;
};

/// A local symbol identity and the exact token selected under the cursor.
struct SymbolSelection {
  binding::SymbolLookup lookup;
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
    document.completion_targets = indexCompletionTargets(*result);
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

/// Tests a frontend range with an exclusive end position.
bool contains(ptx_frontend::SourceRange range, ptx_frontend::SourcePos point) {
  const auto cursor = coordinates(point);
  return coordinates(range.start) <= cursor && cursor < coordinates(range.end);
}

/// Identifies the symbol kinds covered by document-local navigation.
bool isNavigable(const binding::Symbol& symbol) {
  return symbol.kind == binding::SymbolKind::Label ||
         symbol.state_space == ptx_frontend::base::DeclarationStateSpace::Register;
}

/// Binds the snapshot lazily and shares the same table across symbol features.
const ptx_frontend::binding::SymbolTable* documentSymbols(Document& document) {
  if (document.cst) {
    auto ast = ptx_frontend::lowerSyntaxModule(*document.cst);
    if (ast) {
      document.symbols = ptx_frontend::binding::bindSymbols(*ast).table;
    }
    document.cst.reset();
  }
  return document.symbols ? &*document.symbols : nullptr;
}

/// Borrows the bound reference covering the cursor, if one exists.
const binding::SymbolReference* referenceAt(const binding::SymbolTable& symbols,
                                            ptx_frontend::SourcePos source) {
  const auto& references = symbols.references();
  const auto reference = std::ranges::find_if(references, [&](const auto& ref) {
    return ref.target && contains(ref.range, source);
  });
  return reference == references.end() ? nullptr : &*reference;
}

/// Resolves a cursor on a register or branch-label reference in this snapshot.
std::optional<ResolvedReference> findReference(Document& document,
                                               ptx_frontend::SourcePos source) {
  const auto* symbols = documentSymbols(document);
  if (!symbols) {
    return std::nullopt;
  }

  const auto* reference = referenceAt(*symbols, source);
  if (!reference) {
    return std::nullopt;
  }
  const auto& symbol = symbols->symbol(reference->target->symbol);
  if (!isNavigable(symbol)) {
    return std::nullopt;
  }
  return ResolvedReference{symbol, reference->range};
}

/// Selects a bound reference or a declaration at the cursor.
std::optional<SymbolSelection> symbolAt(
    const binding::SymbolTable& symbols, ptx_frontend::SourcePos source) {
  if (const auto* reference = referenceAt(symbols, source)) {
    const auto target = *reference->target;
    if (isNavigable(symbols.symbol(target.symbol))) {
      return SymbolSelection{target, reference->range};
    }
    return std::nullopt;
  }
  for (const auto& symbol : symbols.symbols()) {
    if (!isNavigable(symbol)) {
      continue;
    }
    // Selecting a group declaration covers all of its numbered members.
    const binding::SymbolLookup lookup{symbol.id, std::nullopt};
    if (contains(symbol.declaration_range, source)) {
      return SymbolSelection{lookup, symbol.declaration_range};
    }
    for (const auto& declaration : symbols.declarationOccurrences(symbol.id)) {
      if (contains(declaration.range, source)) {
        return SymbolSelection{lookup, declaration.range};
      }
    }
  }
  return std::nullopt;
}

/// Collects the selected declaration and use sites in source order.
std::vector<ptx_frontend::SourceRange> symbolOccurrences(
    const binding::SymbolTable& symbols, binding::SymbolLookup target,
    bool include_declaration) {
  std::vector<ptx_frontend::SourceRange> ranges;
  if (include_declaration) {
    const auto declarations = symbols.declarationOccurrences(target.symbol);
    if (declarations.empty()) {
      ranges.push_back(symbols.symbol(target.symbol).declaration_range);
    }
    for (const auto& declaration : declarations) {
      ranges.push_back(declaration.range);
    }
  }
  for (const auto& reference : symbols.references()) {
    if (reference.target && reference.target->symbol == target.symbol &&
        (!target.parameterized_index ||
         reference.target->parameterized_index == target.parameterized_index)) {
      ranges.push_back(reference.range);
    }
  }
  std::ranges::sort(ranges, {}, [](const auto& range) {
    return std::pair{coordinates(range.start), coordinates(range.end)};
  });
  const auto duplicates = std::ranges::unique(ranges);
  ranges.erase(duplicates.begin(), duplicates.end());
  return ranges;
}

/// Finds bound references in the current snapshot, including declarations on request.
lsp::TextDocumentReferencesResult findReferences(
    Document& document, const lsp::DocumentUri& uri, lsp::Position position,
    bool include_declaration) {
  const auto source = toSourcePosition(document, position);
  if (!source) {
    return nullptr;
  }
  const auto* symbols = documentSymbols(document);
  if (!symbols) {
    return nullptr;
  }
  const auto target = symbolAt(*symbols, *source);
  if (!target) {
    return nullptr;
  }

  const auto ranges = symbolOccurrences(*symbols, target->lookup, include_declaration);
  lsp::Array<lsp::Location> locations;
  locations.reserve(ranges.size());
  for (const auto& range : ranges) {
    locations.push_back({.uri = uri, .range = toLspRange(document, range)});
  }
  return locations;
}

/// A selected ordinary register or label in the current snapshot.
struct RenameTarget {
  const binding::SymbolTable& symbols;
  SymbolSelection selection;
};

/// Resolves rename requests to local declarations that have individual names.
std::optional<RenameTarget> renameTargetAt(Document& document,
                                          lsp::Position position) {
  const auto source = toSourcePosition(document, position);
  if (!source) {
    return std::nullopt;
  }
  const auto* symbols = documentSymbols(document);
  if (!symbols) {
    return std::nullopt;
  }
  const auto selection = symbolAt(*symbols, *source);
  if (!selection) {
    return std::nullopt;
  }
  const auto& symbol = symbols->symbol(selection->lookup.symbol);
  if (symbol.parameterized_count || symbol.linkage != binding::SymbolLinkage::None ||
      binding::isSpecialRegister(symbol.name)) {
    return std::nullopt;
  }
  return RenameTarget{*symbols, *selection};
}

/// Reports the selected spelling and its exact range for the editor input box.
lsp::TextDocumentPrepareRenameResult prepareRename(Document& document,
                                                   lsp::Position position) {
  const auto target = renameTargetAt(document, position);
  if (!target) {
    return nullptr;
  }
  return lsp::PrepareRenameResult{lsp::PrepareRenamePlaceholder{
      .range = toLspRange(document, target->selection.range),
      .placeholder = target->symbols.symbol(target->selection.lookup.symbol).name,
  }};
}

/// Computes a complete rename edit from the current buffer without mutating it.
lsp::TextDocumentRenameResult renameSymbol(Document& document,
                                          const lsp::DocumentUri& uri,
                                          lsp::Position position,
                                          const std::string& new_name,
                                          bool versioned_edits) {
  const auto target = renameTargetAt(document, position);
  if (!target) {
    throw lsp::RequestError(lsp::MessageError::RequestFailed,
                            "Select an ordinary PTX register or branch label to rename.");
  }
  const auto& symbol = target->symbols.symbol(target->selection.lookup.symbol);
  if (symbol.name == new_name) {
    return nullptr;
  }

  const auto ranges = symbolOccurrences(target->symbols, target->selection.lookup, true);
  std::vector<RenameOccurrence> occurrences;
  occurrences.reserve(ranges.size());
  for (const auto& range : ranges) {
    const auto& line = document.lines.at(static_cast<std::size_t>(range.start.line - 1));
    occurrences.push_back(
        {range, line.offset + static_cast<std::size_t>(range.start.column - 1)});
  }
  if (const auto error = validateSymbolRename(document.text, target->symbols, symbol,
                                               occurrences, new_name)) {
    throw lsp::RequestError(lsp::MessageError::RequestFailed, std::string(*error));
  }

  lsp::WorkspaceEdit result;
  if (versioned_edits) {
    lsp::TextDocumentEdit edit;
    edit.textDocument.uri = uri;
    edit.textDocument.version = document.version;
    edit.edits.reserve(ranges.size());
    for (const auto& range : ranges) {
      edit.edits.emplace_back(lsp::TextEdit{toLspRange(document, range), new_name});
    }
    result.documentChanges.emplace().emplace_back(std::move(edit));
  } else {
    auto& edits = result.changes.emplace()[uri];
    edits.reserve(ranges.size());
    for (const auto& range : ranges) {
      edits.push_back({toLspRange(document, range), new_name});
    }
  }
  return result;
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
std::string describeInstruction(std::span<const InstructionDoc> forms,
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
  for (const auto& form : forms) {
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
                     describeInstruction(instruction->forms, kind), kind);
  }
  const auto reference = findReference(document, *source);
  if (!reference) {
    return nullptr;
  }
  return makeHover(document, reference->range,
                   formatCode(describeSymbol(reference->symbol), kind), kind);
}

/// Builds a completion item that replaces the requested single-line range.
lsp::CompletionItem completionItem(
    std::string_view spelling, lsp::Range range,
    lsp::CompletionItemKind kind = lsp::CompletionItemKind::Keyword) {
  return {
      .label = std::string(spelling),
      .kind = kind,
      .textEdit = lsp::TextEdit{.range = range, .newText = std::string(spelling)},
  };
}

/// Completes instruction heads and symbolic operands in the current snapshot.
lsp::TextDocumentCompletionResult completeInstruction(Document& document,
                                                      lsp::Position position) {
  const auto source = toSourcePosition(document, position);
  if (!source) {
    return nullptr;
  }
  const auto line = lineText(document, position.line);
  const auto context = instructionCompletionAt(document.text, line, *source);
  if (!context) {
    return nullptr;
  }

  lsp::Array<lsp::CompletionItem> items;
  if (const auto* symbol = std::get_if<SymbolCompletion>(&*context)) {
    const auto* table = documentSymbols(document);
    if (!table) {
      return nullptr;
    }
    const auto candidates = completeSymbols(*table, *symbol);
    const auto range = toLspRange(document, symbol->range);
    const auto kind = symbol->kind == OperandSymbolKind::Register
                          ? lsp::CompletionItemKind::Variable
                          : lsp::CompletionItemKind::Reference;
    for (const auto& candidate : candidates.items) {
      auto item = completionItem(candidate.spelling, range, kind);
      item.detail = describeSymbol(*candidate.declaration);
      items.push_back(std::move(item));
    }
    if (candidates.incomplete) {
      return lsp::CompletionList{.isIncomplete = true, .items = std::move(items)};
    }
    if (items.empty()) {
      return nullptr;
    }
    return items;
  }

  const auto* target = completionTargetAt(document.completion_targets, *source);
  if (const auto* modifier = std::get_if<ModifierCompletion>(&*context)) {
    for (const auto& candidate : modifierCandidates(*modifier, line, target)) {
      items.push_back(completionItem(candidate.spelling,
                                     toLspRange(document, candidate.range)));
    }
    return items;
  }

  const auto& mnemonic = std::get<MnemonicCompletion>(*context);
  const auto range = toLspRange(document, mnemonic.range);
  const auto opcodes = instructionOpcodes();
  const std::string_view prefix = mnemonic.prefix;
  for (auto it = std::ranges::lower_bound(opcodes, prefix);
       it != opcodes.end() && it->starts_with(prefix); ++it) {
    if (!mnemonicAvailable(*it, target)) {
      continue;
    }
    auto item = completionItem(*it, range);
    const auto forms = findInstructionDocs(*it);
    if (!forms.empty()) {
      item.documentation = describeInstruction(forms, lsp::MarkupKind::PlainText);
    }
    items.push_back(std::move(item));
  }
  return items;
}

}  // namespace

void registerDocumentHandlers(lsp::ServerEndpoint& endpoint,
                              std::shared_ptr<const DocumentClientOptions> options) {
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

  endpoint.onTextDocumentReferences(
      [documents](lsp::ReferenceParams&& params)
          -> lsp::TextDocumentReferencesResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return findReferences(entry->second, entry->first, params.position,
                              params.context.includeDeclaration);
      });

  endpoint.onTextDocumentPrepareRename(
      [documents](lsp::PrepareRenameParams&& params)
          -> lsp::TextDocumentPrepareRenameResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return prepareRename(entry->second, params.position);
      });

  endpoint.onTextDocumentRename(
      [documents, options](lsp::RenameParams&& params)
          -> lsp::TextDocumentRenameResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          throw lsp::RequestError(lsp::MessageError::RequestFailed,
                                  "The PTX document is not open.");
        }
        return renameSymbol(entry->second, entry->first, params.position,
                            params.newName, options->versioned_edits);
      });

  endpoint.onTextDocumentCompletion(
      [documents](lsp::CompletionParams&& params)
          -> lsp::TextDocumentCompletionResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return completeInstruction(entry->second, params.position);
      });

  endpoint.onTextDocumentHover(
      [documents, options = std::move(options)](lsp::HoverParams&& params)
          -> lsp::TextDocumentHoverResult {
        const auto entry = documents->find(params.textDocument.uri);
        if (entry == documents->end()) {
          return nullptr;
        }
        return findHover(entry->second, params.position, options->hover_kind);
      });
}

}  // namespace plangd
