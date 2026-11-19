#pragma once

#include <span>
#include <string_view>
#include <vector>

#include <ptx_frontend/common/source_loc.hpp>

namespace ptx_frontend::syntax_cst {
struct CstFile;
}

namespace plangd {

/// One documented form of an opcode, stored in the embedded spec index.
struct InstructionDoc {
  std::string_view opcode;
  std::string_view syntax;
  std::string_view description;
};

/// The mnemonic range and its documented forms, independent of CST lifetime.
struct InstructionOccurrence {
  ptx_frontend::SourceRange range;
  std::span<const InstructionDoc> forms;
};

/// Indexes documented instruction mnemonics in lexical source order.
std::vector<InstructionOccurrence> indexInstructions(
    const ptx_frontend::syntax_cst::CstFile& cst);

}  // namespace plangd
