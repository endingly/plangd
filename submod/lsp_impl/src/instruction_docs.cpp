#include "instruction_docs.hpp"

#include <algorithm>
#include <memory>
#include <variant>

#include <ptx_frontend/cst/ptx_cst.hpp>

namespace plangd {
namespace {

namespace cst = ptx_frontend::syntax_cst;

/// Adds the opcode and its adjacent modifier tokens from one instruction.
void indexInstruction(const cst::CstFile& file,
                      const cst::CstInstruction& instruction,
                      std::vector<InstructionOccurrence>& occurrences) {
  const auto& opcode = file.token(instruction.opcode);
  const auto forms = findInstructionDocs(opcode.text);
  if (forms.empty()) {
    return;
  }
  auto range = opcode.range;
  for (const auto modifier : instruction.modifiers) {
    const auto& modifier_range = file.token(modifier).range;
    if (range.end != modifier_range.start) {
      break;
    }
    range.end = modifier_range.end;
  }
  occurrences.push_back({.range = range, .forms = forms});
}

/// Visits the parser's source-ordered function body, including nested blocks.
void indexBody(const cst::CstFile& file,
               const std::vector<cst::CstFunctionBodyItem>& body,
               std::vector<InstructionOccurrence>& occurrences) {
  for (const auto& item : body) {
    if (const auto* instruction = std::get_if<cst::CstInstruction>(&item)) {
      indexInstruction(file, *instruction, occurrences);
    } else if (const auto* block = std::get_if<std::unique_ptr<cst::CstBlock>>(&item);
               block != nullptr && *block) {
      indexBody(file, (*block)->body, occurrences);
    }
  }
}

}  // namespace

std::span<const InstructionDoc> findInstructionDocs(std::string_view opcode) {
  const auto forms = std::ranges::equal_range(
      instructionDocs(), opcode, {}, &InstructionDoc::opcode);
  return {forms.begin(), forms.end()};
}

std::vector<InstructionOccurrence> indexInstructions(const cst::CstFile& cst) {
  std::vector<InstructionOccurrence> occurrences;
  if (const auto* module = cst.module()) {
    for (const auto& item : module->items) {
      if (const auto* function = std::get_if<cst::CstFunction>(&item)) {
        indexBody(cst, function->body, occurrences);
      }
    }
  }
  return occurrences;
}

}  // namespace plangd
