#pragma once

#include <optional>
#include <span>
#include <vector>

#include <ptx_frontend/resolved_ir/ptx_resolved_ir_foundation.hpp>

namespace ptx_frontend::syntax_cst {
struct CstFile;
}

namespace plangd {

/// A source target directive and its completion context until the next directive.
struct CompletionTargetRegion {
  ptx_frontend::SourcePos start;
  std::optional<ptx_frontend::resolved_ir::checker::TargetInfo> target;
};

/// Indexes version and target directives from the recovered document CST.
std::vector<CompletionTargetRegion> indexCompletionTargets(
    const ptx_frontend::syntax_cst::CstFile& cst);

/// Borrows the active target, or returns null for an incomplete configuration.
const ptx_frontend::resolved_ir::checker::TargetInfo* completionTargetAt(
    std::span<const CompletionTargetRegion> regions, ptx_frontend::SourcePos cursor);

}  // namespace plangd
