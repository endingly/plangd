#include "symbol_rename.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <utility>

#include <ptx_frontend/binding/ptx_symbol_table.hpp>
#include <ptx_frontend/cst/ptx_cst_parser.hpp>
#include <ptx_frontend/lexer/ptx_lexer.hpp>
#include <ptx_frontend/syntax/ptx_syntax_lower.hpp>

namespace plangd {
namespace {

namespace binding = ptx_frontend::binding;
using ptx_frontend::SourcePos;
using ptx_frontend::SourceRange;

/// Recognizes a single PTX identifier that is free of reserved register names.
bool validIdentifier(std::string_view name) {
  const auto token = ptx_frontend::PtxLexer(name).consume();
  return token.kind == ptx_frontend::TokenKind::Ident && token.text == name &&
         !binding::isSpecialRegister(name);
}

/// Orders points in the frontend's one-based byte coordinates.
auto coordinates(SourcePos point) {
  return std::pair{point.line, point.column};
}

/// Translates original token boundaries after same-line identifier replacements.
struct RenameCoordinates {
  std::span<const RenameOccurrence> occurrences;
  std::int64_t width_change;

  SourcePos translate(SourcePos point) const {
    const auto first = std::ranges::lower_bound(
        occurrences, point.line, {}, [](const auto& edit) { return edit.range.end.line; });
    const auto end = std::ranges::upper_bound(
        first, occurrences.end(), coordinates(point), {},
        [](const auto& edit) { return coordinates(edit.range.end); });
    point.column = static_cast<std::int32_t>(point.column + (end - first) * width_change);
    return point;
  }

  SourceRange translate(SourceRange range) const {
    return {translate(range.start), translate(range.end)};
  }
};

/// Identifies the same binding, including a compact register's member index.
bool sameTarget(const std::optional<binding::SymbolLookup>& before,
                const std::optional<binding::SymbolLookup>& after) {
  return before && after ? before->symbol == after->symbol &&
                               before->parameterized_index == after->parameterized_index
                         : before.has_value() == after.has_value();
}

/// Compares binding tables in source traversal order with adjusted locations.
bool preservesBindings(const binding::SymbolTable& before,
                       const binding::SymbolTable& after, binding::SymbolId renamed,
                       std::string_view new_name, const RenameCoordinates& positions) {
  // Matching declarations establish a correspondence between the two local ID sets.
  const bool same_declarations = std::ranges::equal(
      before.symbols(), after.symbols(), [&](const auto& old, const auto& edited) {
        const auto name = old.id == renamed ? new_name : std::string_view(old.name);
        return old.id == edited.id && old.scope == edited.scope &&
               old.kind == edited.kind && old.state_space == edited.state_space &&
               old.parameterized_count == edited.parameterized_count &&
               name == edited.name &&
               positions.translate(old.declaration_range) == edited.declaration_range;
      });
  return same_declarations && std::ranges::equal(
      before.references(), after.references(), [&](const auto& old, const auto& edited) {
        const auto spelling = old.target && old.target->symbol == renamed
                                  ? new_name : std::string_view(old.spelling);
        return old.kind == edited.kind && old.scope == edited.scope &&
               old.classification == edited.classification &&
               spelling == edited.spelling && positions.translate(old.range) == edited.range &&
               sameTarget(old.target, edited.target);
      });
}

}  // namespace

std::optional<std::string_view> validateSymbolRename(
    std::string_view text, const binding::SymbolTable& symbols,
    const binding::Symbol& symbol, std::span<const RenameOccurrence> occurrences,
    std::string_view new_name) {
  if (!validIdentifier(new_name)) {
    return "The new name must be a PTX identifier and cannot name a special register.";
  }

  std::string edited;
  edited.reserve(text.size());
  std::size_t cursor = 0;
  for (const auto& occurrence : occurrences) {
    const auto& range = occurrence.range;
    if (occurrence.offset < cursor || occurrence.offset > text.size() ||
        range.start.line != range.end.line ||
        range.end.column - range.start.column != static_cast<std::int64_t>(symbol.name.size()) ||
        text.substr(occurrence.offset, symbol.name.size()) != symbol.name) {
      return "Could not identify all name tokens for this PTX symbol.";
    }
    edited.append(text.substr(cursor, occurrence.offset - cursor));
    edited.append(new_name);
    cursor = occurrence.offset + symbol.name.size();
  }
  edited.append(text.substr(cursor));

  const auto cst = ptx_frontend::PtxCstParser(edited).parseModule();
  if (!cst) {
    return "The renamed document could not be parsed.";
  }
  const auto ast = ptx_frontend::lowerSyntaxModule(*cst);
  if (!ast) {
    return "The renamed document could not be analyzed.";
  }
  const auto rebound = binding::bindSymbols(*ast);
  const RenameCoordinates positions{
      occurrences, static_cast<std::int64_t>(new_name.size()) -
                       static_cast<std::int64_t>(symbol.name.size())};
  const auto declaration = positions.translate(symbol.declaration_range);
  const bool duplicate = std::ranges::any_of(rebound.diagnostics, [&](const auto& diagnostic) {
    return diagnostic.kind == binding::BindDiagnosticKind::DuplicateSymbol &&
           (diagnostic.range == declaration || diagnostic.previous_range == declaration);
  });
  if (duplicate) {
    return "The new name conflicts with another declaration in the same scope.";
  }
  if (!preservesBindings(symbols, rebound.table, symbol.id, new_name, positions)) {
    return "The new name would change the binding of a PTX symbol or reference.";
  }
  return std::nullopt;
}

}  // namespace plangd
