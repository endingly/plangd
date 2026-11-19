#pragma once

#include <span>
#include <vector>

#include <ptx_frontend/common/source_loc.hpp>

#include "instruction_docs.gen.hpp"

namespace ptx_frontend::syntax_cst {
struct CstFile;
}

namespace plangd {

/// The mnemonic range and its documented forms, independent of CST lifetime.
struct InstructionOccurrence {
  ptx_frontend::SourceRange range;
  std::span<const InstructionDoc> forms;
};

/// Indexes documented instruction mnemonics in lexical source order.
std::vector<InstructionOccurrence> indexInstructions(
    const ptx_frontend::syntax_cst::CstFile& cst);

}  // namespace plangd
