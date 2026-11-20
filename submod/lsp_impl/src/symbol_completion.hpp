#pragma once

#include <string>
#include <vector>

#include "completion.hpp"

namespace ptx_frontend::binding {
class SymbolTable;
struct Symbol;
}

namespace plangd {

/// A visible spelling and its declaration, borrowed from the snapshot's table.
struct SymbolCandidate {
  std::string spelling;
  const ptx_frontend::binding::Symbol* declaration;
};

/// The matches found within the per-request completion budget.
struct SymbolCandidates {
  std::vector<SymbolCandidate> items;
  bool incomplete = false;
};

/// Finds matching registers or direct-branch labels visible at the instruction.
SymbolCandidates completeSymbols(const ptx_frontend::binding::SymbolTable& table,
                                 const SymbolCompletion& completion);

}  // namespace plangd
