#include "document_syntax.hpp"

#include <algorithm>
#include <memory>
#include <string>
#include <tuple>
#include <utility>
#include <variant>

#include <ptx_frontend/cst/ptx_cst.hpp>

namespace plangd {
namespace {

namespace binding = ptx_frontend::binding;
namespace cst = ptx_frontend::syntax_cst;
using ptx_frontend::TokenKind;

bool isDeviceFunction(const binding::Symbol& symbol) {
  return symbol.kind == binding::SymbolKind::Function && !symbol.function_is_entry;
}

/// Builds a single-line label from the written declaration of a formal parameter.
std::string parameterLabel(const cst::CstFile& file,
                           const cst::CstFunctionParameter& parameter) {
  std::string label;
  for (auto id = parameter.token_range.first; id < parameter.token_range.last; ++id) {
    const auto& token = file.token(id);
    if (!label.empty() && !token.leading_trivia.empty()) {
      label += ' ';
    }
    label += token.text;
  }
  return label;
}

FunctionDeclaration functionDeclaration(const cst::CstFile& file,
                                        const cst::CstFunction& function) {
  const auto& name = file.token(function.name);
  FunctionDeclaration result{
      .name_range = name.range,
      .signature = {.label = ".func ", .parameters = lsp::Array<lsp::ParameterInformation>{}},
      .return_count = 0,
  };
  auto& label = result.signature.label;
  auto& parameters = *result.signature.parameters;
  const auto append = [&](const cst::CstFunctionParameterList& list) {
    label += '(';
    bool first = true;
    for (const auto& parameter : list.parameters) {
      if (!std::exchange(first, false)) {
        label += ", ";
      }
      auto text = parameterLabel(file, parameter);
      label += text;
      parameters.push_back({.label = std::move(text)});
    }
    label += ')';
  };
  if (function.return_parameters) {
    append(*function.return_parameters);
    result.return_count = static_cast<lsp::Uint>(parameters.size());
    label += ' ';
  }
  label += name.text;
  if (function.parameters) {
    append(*function.parameters);
  } else {
    label += "()";
  }
  return result;
}

/// Uses the CST's direct target and list separators for a complete call.
void indexCall(DocumentSyntax& result, const cst::CstFile& file,
               const cst::CstInstruction& instruction,
               const binding::SymbolTable& symbols, binding::ScopeId scope) {
  if (file.token(instruction.opcode).text != "call" ||
      instruction.modifiers.size() > 1 ||
      (!instruction.modifiers.empty() &&
       file.token(instruction.modifiers.front()).text != ".uni")) {
    return;
  }
  DirectCall call{
      .range = {file.token(instruction.opcode).range.start,
                file.token(instruction.semicolon).range.start},
  };
  const binding::Symbol* callee = nullptr;
  for (const auto& operand : instruction.operands) {
    if (const auto* target = std::get_if<cst::CstCallTarget>(&operand.operand)) {
      if (const auto* name = std::get_if<cst::CstIdentifier>(&target->name)) {
        if (const auto lookup = symbols.lookup(scope, file.token(name->token).text)) {
          callee = deviceFunctionTarget(symbols, symbols.symbol(lookup->symbol));
        }
      }
    } else if (const auto* list = std::get_if<cst::CstCallParameterList>(&operand.operand)) {
      CallArguments arguments{
          {file.token(list->left_paren).range.end, file.token(list->right_paren).range.start},
          {},
      };
      for (const auto comma : list->commas) {
        arguments.comma_ends.push_back(file.token(comma).range.end);
      }
      auto& destination = list->kind == cst::CstCallParameterListKind::Return
                              ? call.returns : call.inputs;
      destination = std::move(arguments);
    } else if (std::holds_alternative<cst::CstCallTargetSet>(operand.operand)) {
      return;
    }
  }
  if (callee) {
    call.callee = callee->id;
    result.calls.push_back(std::move(call));
  }
}

/// Reads the separators of a partial list, stopping at its closing token or the
/// recovery boundary. Nested constant expressions own their own commas.
CallArguments partialArguments(const cst::CstFile& file, cst::TokenId& cursor,
                               cst::TokenId limit) {
  CallArguments result{{file.token(cursor++).range.end, file.token(limit).range.start}, {}};
  unsigned depth = 0;
  for (; cursor < limit; ++cursor) {
    const auto& token = file.token(cursor);
    if (token.kind == TokenKind::RParen && depth == 0) {
      result.contents.end = token.range.start;
      ++cursor;
      break;
    }
    if (token.kind == TokenKind::LParen) {
      ++depth;
    } else if (token.kind == TokenKind::RParen) {
      --depth;
    } else if (token.kind == TokenKind::Comma && depth == 0) {
      result.comma_ends.push_back(token.range.end);
    }
  }
  return result;
}

/// Uses the skipped CST tokens up to the first parse error for a call being typed.
void indexPartialCall(DocumentSyntax& result, const cst::CstFile& file,
                      cst::CstTokenRange span, const binding::SymbolTable& symbols,
                      binding::ScopeId scope) {
  auto [opcode, limit] = span;
  if (opcode < limit && file.token(opcode).kind == TokenKind::At) {
    ++opcode;
    if (opcode < limit && file.token(opcode).kind == TokenKind::Exclamation) {
      ++opcode;
    }
    ++opcode;
  }
  if (opcode >= limit || file.token(opcode).text != "call") {
    return;
  }
  auto cursor = opcode + 1;
  const auto at = [&](TokenKind kind) {
    return cursor < limit && file.token(cursor).kind == kind;
  };
  if (cursor < limit && file.token(cursor).text == ".uni") {
    ++cursor;
  }
  DirectCall call{};
  if (at(TokenKind::LParen)) {
    call.returns = partialArguments(file, cursor, limit);
    if (!at(TokenKind::Comma)) {
      return;
    }
    ++cursor;
  }
  if (!at(TokenKind::Ident)) {
    return;
  }
  const auto lookup = symbols.lookup(scope, file.token(cursor++).text);
  const auto* callee = lookup ? deviceFunctionTarget(symbols, symbols.symbol(lookup->symbol))
                              : nullptr;
  if (!callee) {
    return;
  }
  if (at(TokenKind::Comma)) {
    ++cursor;
    if (at(TokenKind::LParen)) {
      call.inputs = partialArguments(file, cursor, limit);
    }
  }
  if (cursor < limit && !at(TokenKind::Semicolon)) {
    return;
  }
  call.range = {file.token(opcode).range.start, file.token(cursor).range.start};
  call.callee = callee->id;
  result.calls.push_back(std::move(call));
}

void indexFold(DocumentSyntax& result, const cst::CstFile& file,
               std::optional<cst::TokenId> open, std::optional<cst::TokenId> close) {
  if (open && close) {
    const auto start = file.token(*open).range.end;
    const auto end = file.token(*close).range.start;
    if (start.line < end.line) {
      result.folds.emplace_back(start, end);
    }
  }
}

void indexBody(DocumentSyntax& result, const cst::CstFile& file,
               const std::vector<cst::CstFunctionBodyItem>& body,
               const binding::SymbolTable* symbols,
               std::optional<binding::ScopeId> scope) {
  for (const auto& item : body) {
    if (const auto* block = std::get_if<std::unique_ptr<cst::CstBlock>>(&item);
        block && *block) {
      const auto child_scope = scope ? symbols->blockScope(
                                          *scope, file.sourceRange((*block)->token_range))
                                     : std::nullopt;
      indexFold(result, file, (*block)->left_brace, (*block)->right_brace);
      indexBody(result, file, (*block)->body, symbols, child_scope);
    } else if (const auto* instruction = std::get_if<cst::CstInstruction>(&item)) {
      result.tokens.push_back({file.token(instruction->opcode).range, SemanticKind::Opcode});
      for (const auto modifier : instruction->modifiers) {
        result.tokens.push_back({file.token(modifier).range, SemanticKind::Modifier});
      }
      if (scope) {
        indexCall(result, file, *instruction, *symbols, *scope);
      }
    } else if (const auto* recovery = std::get_if<cst::CstRecoveryNode>(&item);
               scope && recovery && recovery->kind == cst::CstRecoveryKind::Skipped &&
               recovery->token_range) {
      indexPartialCall(result, file, *recovery->token_range, *symbols, *scope);
    }
  }
}

std::optional<SemanticKind> semanticKind(const binding::Symbol& symbol) {
  if (symbol.state_space == ptx_frontend::base::DeclarationStateSpace::Register) {
    return SemanticKind::Register;
  }
  switch (symbol.kind) {
    case binding::SymbolKind::Label: return SemanticKind::Label;
    case binding::SymbolKind::Function: return SemanticKind::Function;
    case binding::SymbolKind::Variable:
    case binding::SymbolKind::InputParameter:
    case binding::SymbolKind::ReturnParameter:
    case binding::SymbolKind::CallParameter: return SemanticKind::Data;
    default: return std::nullopt;
  }
}

void indexBoundTokens(DocumentSyntax& result, const binding::SymbolTable& symbols) {
  for (const auto& symbol : symbols.symbols()) {
    if (const auto kind = semanticKind(symbol)) {
      const auto declarations = symbols.declarationOccurrences(symbol.id);
      if (declarations.empty()) {
        result.tokens.push_back({symbol.declaration_range, *kind});
      }
      for (const auto& declaration : declarations) {
        result.tokens.push_back({declaration.range, *kind});
      }
    }
  }
  for (const auto& reference : symbols.references()) {
    if (reference.classification == binding::ReferenceClassification::SpecialRegister) {
      result.tokens.push_back({reference.range, SemanticKind::Register});
    } else if (reference.target) {
      if (const auto kind = semanticKind(symbols.symbol(reference.target->symbol))) {
        result.tokens.push_back({reference.range, *kind});
      }
    }
  }
}

}  // namespace

const binding::Symbol* deviceFunctionTarget(const binding::SymbolTable& symbols,
                                           const binding::Symbol& symbol) {
  if (!isDeviceFunction(symbol)) {
    return nullptr;
  }
  const auto& target = symbols.symbol(symbol.canonical_function.value_or(symbol.id));
  return isDeviceFunction(target) ? &target : nullptr;
}

DocumentSyntax indexDocumentSyntax(const cst::CstFile& file,
                                   const binding::SymbolTable* symbols) {
  DocumentSyntax result;
  const auto* module = file.module();
  if (!module) {
    return result;
  }
  for (const auto& item : module->items) {
    const auto* function = std::get_if<cst::CstFunction>(&item);
    if (!function) {
      continue;
    }
    const auto scope = symbols ? symbols->functionScope(file.sourceRange(function->token_range))
                               : std::nullopt;
    if (scope) {
      const auto& symbol = symbols->symbol(*symbols->scope(*scope).owner);
      // The binder prefers a definition's scope over scopes for its declarations.
      if (isDeviceFunction(symbol) && symbol.owned_scope == scope) {
        result.functions.emplace(symbol.id.value, functionDeclaration(file, *function));
      }
    }
    indexFold(result, file, function->left_brace, function->right_brace);
    indexBody(result, file, function->body, symbols, scope);
  }
  if (symbols) {
    indexBoundTokens(result, *symbols);
  }
  std::ranges::sort(result.tokens, [](const auto& lhs, const auto& rhs) {
    const auto key = [](const auto& token) {
      return std::tuple{token.range.start.line, token.range.start.column,
                        token.range.end.line, token.range.end.column, token.kind};
    };
    return key(lhs) < key(rhs);
  });
  return result;
}

}  // namespace plangd
