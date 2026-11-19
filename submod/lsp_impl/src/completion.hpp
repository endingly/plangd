#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include <ptx_frontend/common/source_loc.hpp>

namespace plangd {

/// An opcode prefix and the full single-line mnemonic range it can replace.
struct MnemonicCompletion {
  std::string prefix;
  ptx_frontend::SourceRange range;
};

/// A written modifier before the fragment under the cursor.
struct WrittenModifier {
  std::string spelling;
  ptx_frontend::SourceRange range;
};

/// The instruction and modifier fragment at the cursor in the current text.
struct ModifierCompletion {
  std::string opcode;
  std::vector<WrittenModifier> preceding;
  std::string prefix;
  ptx_frontend::SourceRange range;
};

/// A modifier spelling from the generated table and the source it replaces.
struct ModifierCandidate {
  std::string_view spelling;
  ptx_frontend::SourceRange range;
};

using InstructionCompletion = std::variant<MnemonicCompletion, ModifierCompletion>;

/// Finds a position to complete in the instruction head, even in unfinished code.
std::optional<InstructionCompletion> instructionCompletionAt(
    std::string_view text, std::string_view cursor_line,
    ptx_frontend::SourcePos cursor);

/// Returns the sorted, distinct next spellings accepted by the modifier orders.
std::vector<ModifierCandidate> modifierCandidates(const ModifierCompletion& context,
                                                  std::string_view cursor_line);

}  // namespace plangd
