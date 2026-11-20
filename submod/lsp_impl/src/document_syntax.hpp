#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <unordered_map>
#include <vector>

#include <lsp/types.h>
#include <ptx_frontend/binding/ptx_symbol_table.hpp>

namespace ptx_frontend::syntax_cst {
struct CstFile;
}

namespace plangd {

enum class SemanticKind : lsp::Uint { Opcode, Modifier, Register, Label, Function, Data };

inline constexpr std::array semantic_token_types{
    "keyword", "modifier", "register", "label", "function", "variable"};

struct SemanticOccurrence {
  ptx_frontend::SourceRange range;
  SemanticKind kind;
};

struct FunctionDeclaration {
  ptx_frontend::SourceRange name_range;
  lsp::SignatureInformation signature;
  lsp::Uint return_count;
};

struct CallArguments {
  ptx_frontend::SourceRange contents;
  std::vector<ptx_frontend::SourcePos> comma_ends;
};

struct DirectCall {
  ptx_frontend::SourceRange range;
  ptx_frontend::binding::SymbolId callee;
  std::optional<CallArguments> returns;
  std::optional<CallArguments> inputs;
};

/// Source information copied from the CST and its matching symbol table.
struct DocumentSyntax {
  std::vector<ptx_frontend::SourceRange> folds;
  std::vector<SemanticOccurrence> tokens;
  std::unordered_map<std::uint32_t, FunctionDeclaration> functions;
  std::vector<DirectCall> calls;
};

const ptx_frontend::binding::Symbol* deviceFunctionTarget(
    const ptx_frontend::binding::SymbolTable& symbols,
    const ptx_frontend::binding::Symbol& symbol);

DocumentSyntax indexDocumentSyntax(
    const ptx_frontend::syntax_cst::CstFile& file,
    const ptx_frontend::binding::SymbolTable* symbols);

}  // namespace plangd
