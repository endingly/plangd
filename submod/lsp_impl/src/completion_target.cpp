#include "completion_target.hpp"

#include <algorithm>
#include <charconv>
#include <string_view>
#include <utility>
#include <variant>

#include <ptx_frontend/base/ptx_target.hpp>
#include <ptx_frontend/cst/ptx_cst.hpp>

namespace plangd {
namespace {

namespace base = ptx_frontend::base;
namespace checker = ptx_frontend::resolved_ir::checker;
namespace cst = ptx_frontend::syntax_cst;
using ptx_frontend::TokenKind;

/// Decodes the CST's decimal version spelling without a floating-point conversion.
std::optional<checker::PtxVersion> parseVersion(std::string_view text) {
  const auto point = text.find('.');
  if (point == std::string_view::npos) {
    return std::nullopt;
  }
  checker::PtxVersion version;
  const auto separator = text.data() + point;
  const auto end = text.data() + text.size();
  const auto major = std::from_chars(text.data(), separator, version.major);
  const auto minor = std::from_chars(separator + 1, end, version.minor);
  if (major.ec != std::errc{} || major.ptr != separator || version.major == 0 ||
      minor.ec != std::errc{} || minor.ptr != end) {
    return std::nullopt;
  }
  return version;
}

/// Finds the first valid module version; it applies to all target regions.
std::optional<checker::PtxVersion> moduleVersion(
    const cst::CstFile& file, const cst::CstModule& module) {
  for (const auto& item : module.items) {
    const auto* directive = std::get_if<cst::CstModuleDirective>(&item);
    if (directive && file.token(directive->keyword).kind == TokenKind::DotVersion) {
      if (auto version = parseVersion(file.token(directive->arguments.front()).text)) {
        return version;
      }
    }
  }
  return std::nullopt;
}

/// Uses the SDK's target catalog and preserves its source identity and options.
std::optional<checker::TargetInfo> readTarget(
    const cst::CstFile& file, const cst::CstModuleDirective& directive,
    checker::PtxVersion version) {
  auto profile =
      base::find_target_profile(file.token(directive.arguments.front()).text);
  if (!profile) {
    return std::nullopt;
  }
  const bool map_f64_to_f32 =
      std::ranges::any_of(directive.arguments, [&](auto id) {
        return file.token(id).text == "map_f64_to_f32";
      });
  return checker::TargetInfo{
      .ptx_version = version,
      .sm_version = base::target_feature_architecture(profile->identity).number,
      .enabled_family_features = profile->enabled_family_features,
      .identity = std::move(profile->identity),
      .capabilities = profile->capabilities,
      .map_f64_to_f32 = map_f64_to_f32,
  };
}

/// Returns a source position as an ordered line and column pair.
auto coordinates(ptx_frontend::SourcePos position) {
  return std::pair{position.line, position.column};
}

}  // namespace

std::vector<CompletionTargetRegion> indexCompletionTargets(const cst::CstFile& file) {
  const auto* module = file.module();
  if (!module) {
    return {};
  }
  const auto version = moduleVersion(file, *module);
  if (!version) {
    return {};
  }

  std::vector<CompletionTargetRegion> regions;
  for (const auto& item : module->items) {
    if (const auto* directive = std::get_if<cst::CstModuleDirective>(&item)) {
      const auto& token = file.token(directive->keyword);
      if (token.kind == TokenKind::DotTarget) {
        regions.push_back(
            {token.range.start, readTarget(file, *directive, *version)});
      }
    } else if (const auto* recovery = std::get_if<cst::CstRecoveryNode>(&item);
               recovery && recovery->token_range) {
      const auto& token = file.token(recovery->token_range->first);
      if (token.kind == TokenKind::DotTarget) {
        // A malformed directive still ends the preceding target region.
        regions.push_back({token.range.start, std::nullopt});
      }
    }
  }
  return regions;
}

const checker::TargetInfo* completionTargetAt(
    std::span<const CompletionTargetRegion> regions, ptx_frontend::SourcePos cursor) {
  const auto next = std::ranges::upper_bound(
      regions, coordinates(cursor), {},
      [](const auto& region) { return coordinates(region.start); });
  if (next == regions.begin() || !(next - 1)->target) {
    return nullptr;
  }
  return &*(next - 1)->target;
}

}  // namespace plangd
