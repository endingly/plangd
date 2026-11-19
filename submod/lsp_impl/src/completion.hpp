#pragma once

#include <optional>
#include <string>
#include <string_view>

#include <ptx_frontend/common/source_loc.hpp>

namespace plangd {

/// An opcode prefix and the full single-line mnemonic range it can replace.
struct MnemonicCompletion {
  std::string prefix;
  ptx_frontend::SourceRange range;
};

/// Finds a mnemonic position in the current text, including unfinished code.
std::optional<MnemonicCompletion> mnemonicCompletionAt(
    std::string_view text, ptx_frontend::SourcePos cursor);

}  // namespace plangd
