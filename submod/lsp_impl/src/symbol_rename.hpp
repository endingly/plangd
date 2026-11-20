#pragma once

#include <cstddef>
#include <optional>
#include <span>
#include <string_view>

#include <ptx_frontend/common/source_loc.hpp>

namespace ptx_frontend::binding {
class SymbolTable;
struct Symbol;
}

namespace plangd {

/// A name token in the original snapshot, with its byte offset into that text.
struct RenameOccurrence {
  ptx_frontend::SourceRange range;
  std::size_t offset;
};

/// Checks a proposed rename by binding an edited copy of the original snapshot.
std::optional<std::string_view> validateSymbolRename(
    std::string_view text, const ptx_frontend::binding::SymbolTable& symbols,
    const ptx_frontend::binding::Symbol& symbol,
    std::span<const RenameOccurrence> occurrences, std::string_view new_name);

}  // namespace plangd
