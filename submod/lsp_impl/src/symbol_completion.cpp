#include "symbol_completion.hpp"

#include <algorithm>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <string_view>
#include <utility>

#include <ptx_frontend/binding/ptx_symbol_table.hpp>

namespace plangd {
namespace {

namespace binding = ptx_frontend::binding;
using ptx_frontend::SourcePos;

// Compact register groups can describe billions of members without expanding.
constexpr std::size_t kMaxSymbolCandidates = 256;

/// Orders one-based positions as line and byte column.
auto coordinates(SourcePos position) {
  return std::pair{position.line, position.column};
}

/// Finds the innermost scope at the opcode before any incomplete operand.
const binding::Scope* scopeAt(const binding::SymbolTable& table,
                             SourcePos opcode) {
  const binding::Scope* selected = nullptr;
  const auto point = coordinates(opcode);
  for (const auto& scope : table.scopes()) {
    if (scope.range && coordinates(scope.range->start) <= point &&
        point < coordinates(scope.range->end) &&
        (!selected || coordinates(selected->range->start) <
                          coordinates(scope.range->start))) {
      selected = &scope;
    }
  }
  return selected;
}

/// Enumerates only indices whose decimal spelling matches the typed prefix.
bool completeRegisterGroup(std::string_view name, std::uint32_t count,
                           std::string_view prefix, auto&& append) {
  std::size_t expanded = 0;
  const auto emitRange = [&](std::uint64_t first, std::uint64_t last) {
    for (auto index = first; index < last; ++index) {
      if (expanded == kMaxSymbolCandidates ||
          !append(std::string(name) + std::to_string(index))) {
        return false;
      }
      ++expanded;
    }
    return true;
  };
  if (name.starts_with(prefix)) {
    return emitRange(0, count);
  }
  if (!prefix.starts_with(name)) {
    return true;
  }

  const auto digits = prefix.substr(name.size());
  std::uint32_t value = 0;
  const auto end = digits.data() + digits.size();
  const auto [next, error] = std::from_chars(digits.data(), end, value);
  if (error != std::errc{} || next != end ||
      (digits.size() > 1 && digits.front() == '0')) {
    return true;
  }
  if (value == 0) {
    return emitRange(0, std::min(count, 1u));
  }
  for (std::uint64_t first = value, last = first + 1; first < count;
       first *= 10, last *= 10) {
    if (!emitRange(first, std::min(last, static_cast<std::uint64_t>(count)))) {
      return false;
    }
  }
  return true;
}

}  // namespace

SymbolCandidates completeSymbols(const binding::SymbolTable& table,
                                 const SymbolCompletion& completion) {
  SymbolCandidates result;
  const auto* scope = scopeAt(table, completion.instruction);
  if (!scope) {
    return result;
  }

  std::vector<binding::ScopeId> visible;
  const binding::Scope* function = nullptr;
  for (const auto* current = scope; current;
       current = current->parent ? &table.scope(*current->parent) : nullptr) {
    visible.push_back(current->id);
    if (current->kind == binding::ScopeKind::Function) {
      function = current;
    }
  }
  if (!function) {
    return result;
  }

  for (const auto& declaration : table.symbols()) {
    if (completion.kind == OperandSymbolKind::BranchLabel) {
      if (declaration.kind != binding::SymbolKind::Label ||
          declaration.scope != function->id) {
        continue;
      }
    } else if (declaration.state_space !=
                   ptx_frontend::base::DeclarationStateSpace::Register ||
               !std::ranges::contains(visible, declaration.scope)) {
      continue;
    }

    const auto append = [&](std::string spelling) {
      // The binder owns exact and compact-name shadowing in parent scopes.
      const auto lookup = table.lookup(scope->id, spelling);
      if (lookup && lookup->symbol == declaration.id) {
        if (result.items.size() == kMaxSymbolCandidates) {
          return false;
        }
        result.items.push_back({std::move(spelling), &declaration});
      }
      return true;
    };
    bool finished = true;
    if (declaration.parameterized_count) {
      finished = completeRegisterGroup(
          declaration.name, *declaration.parameterized_count, completion.prefix,
          append);
    } else if (declaration.name.starts_with(completion.prefix)) {
      finished = append(declaration.name);
    }
    if (!finished) {
      result.incomplete = true;
      if (result.items.size() == kMaxSymbolCandidates) {
        break;
      }
    }
  }
  std::ranges::sort(result.items, {}, &SymbolCandidate::spelling);
  return result;
}

}  // namespace plangd
