#include "completion.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <span>
#include <tuple>
#include <utility>

#include <ptx_frontend/lexer/ptx_lexer.hpp>
#include <ptx_frontend/resolved_ir/ptx_resolved_ir_checker_support.hpp>

#include "instruction_docs.gen.hpp"
#include "instruction_modifiers.gen.hpp"

namespace plangd {
namespace {

namespace checker = ptx_frontend::resolved_ir::checker;

using ptx_frontend::PtxToken;
using ptx_frontend::SourcePos;
using ptx_frontend::SourceRange;
using ptx_frontend::TokenKind;
using ptx_frontend::TriviaKind;

/// A lexical cursor can locate an opcode without a complete PTX statement.
enum class BodyPosition {
  Start,
  LabelColon,
  Predicate,
  NegatedPredicate,
  Opcode,
  Statement,
};

/// A recognized instruction whose operands may still be under the cursor.
struct OperandContext {
  SourcePos instruction;
  OperandSymbolKind kind;
  std::optional<TokenKind> previous;

  /// A direct branch accepts only its first operand as a label.
  bool acceptsIdentifier() const {
    return kind == OperandSymbolKind::Register || !previous;
  }

  /// A fresh operand can be completed after separators or a direct branch mnemonic.
  std::optional<SymbolCompletion> emptyOperandAt(SourcePos cursor) const {
    if ((kind == OperandSymbolKind::BranchLabel && !previous) ||
        (kind == OperandSymbolKind::Register &&
         (previous == TokenKind::Comma || previous == TokenKind::LBracket ||
          previous == TokenKind::LBrace || previous == TokenKind::LParen))) {
      return SymbolCompletion{kind, instruction, {}, {cursor, cursor}};
    }
    return std::nullopt;
  }
};

/// Tracks statement boundaries and distinguishes blocks from operand braces.
struct CompletionContext {
  BodyPosition body = BodyPosition::Start;
  unsigned blocks = 0;
  unsigned other_braces = 0;
  bool function_header = false;
  bool header_pragma = false;
  std::int32_t directive_line = 0;
  std::optional<OperandContext> operands;

  /// A statement head can also be a label or a predicate guard.
  bool atStatementStart() const {
    return blocks != 0 && other_braces == 0 && body == BodyPosition::Start;
  }

  /// The predicate identifier follows `@` or `@!`.
  bool expectsPredicate() const {
    return body == BodyPosition::Predicate ||
           body == BodyPosition::NegatedPredicate;
  }

  /// An opcode can begin a statement or follow a complete predicate guard.
  bool expectsOpcode() const {
    return blocks != 0 && other_braces == 0 &&
           (body == BodyPosition::Start || body == BodyPosition::Opcode);
  }

  /// Line-oriented debug metadata does not require a semicolon.
  void advanceTo(std::int32_t line) {
    if (directive_line != 0 && directive_line < line) {
      body = BodyPosition::Start;
      directive_line = 0;
    }
  }

  /// Updates the lexical context after a token wholly before the cursor.
  void consume(TokenKind kind, std::int32_t line, bool label) {
    if (operands) {
      if (other_braces == 0 &&
          (kind == TokenKind::Semicolon || kind == TokenKind::RBrace)) {
        operands.reset();
      } else {
        operands->previous = kind;
      }
    }
    if (other_braces != 0) {
      if (kind == TokenKind::LBrace) {
        ++other_braces;
      } else if (kind == TokenKind::RBrace) {
        --other_braces;
      }
      return;
    }

    if (blocks == 0) {
      if (kind == TokenKind::DotEntry || kind == TokenKind::DotFunc) {
        function_header = true;
        header_pragma = false;
      } else if (kind == TokenKind::DotPragma && function_header) {
        header_pragma = true;
      } else if (kind == TokenKind::Semicolon) {
        function_header = header_pragma;
        header_pragma = false;
      } else if (kind == TokenKind::LBrace) {
        if (function_header) {
          ++blocks;
          body = BodyPosition::Start;
          function_header = false;
          header_pragma = false;
        } else {
          ++other_braces;
        }
      }
      return;
    }

    if (directive_line != 0 && kind != TokenKind::Semicolon &&
        kind != TokenKind::RBrace) {
      return;
    }

    if (kind == TokenKind::LBrace) {
      if (body == BodyPosition::Start) {
        ++blocks;
      } else {
        ++other_braces;
        body = BodyPosition::Statement;
      }
    } else if (kind == TokenKind::RBrace || kind == TokenKind::Semicolon) {
      if (kind == TokenKind::RBrace) {
        --blocks;
      }
      body = BodyPosition::Start;
      directive_line = 0;
    } else if (label) {
      body = BodyPosition::LabelColon;
    } else if (body == BodyPosition::LabelColon && kind == TokenKind::Colon) {
      body = BodyPosition::Start;
    } else if (body == BodyPosition::Start && kind == TokenKind::At) {
      body = BodyPosition::Predicate;
    } else if (body == BodyPosition::Predicate &&
               kind == TokenKind::Exclamation) {
      body = BodyPosition::NegatedPredicate;
    } else if (expectsPredicate() && kind == TokenKind::Ident) {
      body = BodyPosition::Opcode;
    } else {
      if (body == BodyPosition::Start &&
          (kind == TokenKind::DotLoc || kind == TokenKind::AtAtDwarf)) {
        directive_line = line;
      }
      body = BodyPosition::Statement;
    }
  }
};

/// Compares one-based line and byte column in source order.
bool before(SourcePos lhs, SourcePos rhs) {
  return std::tie(lhs.line, lhs.column) < std::tie(rhs.line, rhs.column);
}

/// Detects comments in a token's trivia, including EOF in a line comment.
bool inComment(const PtxToken& token, SourcePos cursor) {
  return std::ranges::any_of(token.leading_trivia, [&](const auto& trivia) {
    const bool at_line_comment_end =
        trivia.kind == TriviaKind::LineComment && cursor == trivia.range.end;
    return trivia.kind != TriviaKind::Whitespace &&
           !before(cursor, trivia.range.start) &&
           (before(cursor, trivia.range.end) || at_line_comment_end);
  });
}

/// The lexer gives some modifier spellings their own directive token kind.
bool isModifierFragment(TokenKind kind) {
  switch (kind) {
    case TokenKind::Dot:
    case TokenKind::DotIdent:
    case TokenKind::DotGlobal:
    case TokenKind::DotConst:
    case TokenKind::DotShared:
    case TokenKind::DotLocal:
    case TokenKind::DotParam:
    case TokenKind::DotWeak:
      return true;
    default:
      return false;
  }
}

/// Scans the modifiers after an opcode, leaving the next other token unconsumed.
std::optional<ModifierCompletion> readModifierCompletion(
    ptx_frontend::PtxLexer& lexer, std::string_view opcode,
    std::string_view cursor_line, SourcePos cursor) {
  std::vector<WrittenModifier> preceding;
  for (auto token = lexer.peek(); isModifierFragment(token.kind) &&
       before(token.range.start, cursor); token = lexer.peek()) {
    lexer.consume();
    auto range = token.range;
    if (range.start.line == cursor.line) {
      auto end = static_cast<std::size_t>(range.end.column - 1);
      // A partial namespace such as `.param::` has separate colon tokens.
      while (end < cursor_line.size() && cursor_line[end] == ':') {
        ++end;
      }
      range.end.column = static_cast<std::int32_t>(end + 1);
      if (!before(range.end, cursor)) {
        const auto start = static_cast<std::size_t>(range.start.column - 1);
        const auto length =
            static_cast<std::size_t>(cursor.column - range.start.column);
        return ModifierCompletion{
            std::string(opcode), std::move(preceding),
            std::string(cursor_line.substr(start, length)), range};
      }
    }
    preceding.push_back({std::move(token.text), token.range});
  }
  return std::nullopt;
}

/// Evaluates shared ISA requirements only when a document target is known.
struct AvailabilityFilter {
  const checker::TargetInfo* target;
  std::span<const checker::AvailabilityDescriptor> requirements =
      instructionAvailability();

  /// The generator reserves condition zero for unrestricted spellings.
  bool allows(AvailabilityId id) const {
    return !target || id == 0 || checker::is_available(requirements[id], *target);
  }

  /// A viable order must have at least one available spelling per required slot.
  bool allowsOrder(ModifierOrder order, std::span<const ModifierSlot> slots) const {
    if (!allows(order.availability)) {
      return false;
    }
    return !target || std::ranges::all_of(order.slots, [&](ModifierSlotId id) {
      const auto& slot = slots[id];
      return slot.optional || std::ranges::any_of(slot.spellings, [&](const auto& spelling) {
        return allows(spelling.availability);
      });
    });
  }
};

/// Tries one slot order, skipping only optional slots when a spelling mismatches.
void collectModifierCandidates(ModifierOrder order,
                               std::span<const ModifierSlot> slots,
                               std::span<const WrittenModifier> preceding,
                               std::string_view prefix, SourceRange range,
                               const AvailabilityFilter& available,
                               std::map<std::string_view, SourceRange>& candidates) {
  if (!available.allowsOrder(order, slots)) {
    return;
  }
  for (const auto id : order.slots) {
    const auto& slot = slots[id];
    if (preceding.empty()) {
      for (const auto& spelling : slot.spellings) {
        if (spelling.text.starts_with(prefix) &&
            available.allows(spelling.availability)) {
          candidates.try_emplace(spelling.text, range);
        }
      }
    } else if (std::ranges::any_of(slot.spellings, [&](const auto& spelling) {
                 return spelling.text == preceding.front().spelling &&
                        available.allows(spelling.availability);
               })) {
      preceding = preceding.subspan(1);
      continue;
    }
    if (!slot.optional) {
      break;
    }
  }
}

/// Includes any partial compound segments to the right of the cursor in the edit.
SourceRange compoundEditRange(std::string_view spelling, std::string_view line,
                              SourceRange range) {
  auto segment = spelling.find('.', 1);
  if (segment == std::string_view::npos) {
    return range;
  }

  const auto start = static_cast<std::size_t>(range.start.column - 1);
  auto end = static_cast<std::size_t>(range.end.column - 1);
  const auto fragment = line.substr(start, end - start);
  for (auto dot = fragment.find('.', 1);
       dot != std::string_view::npos && segment != std::string_view::npos;
       dot = fragment.find('.', dot + 1)) {
    segment = spelling.find('.', segment + 1);
  }

  constexpr std::string_view atom =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$%:";
  while (segment != std::string_view::npos && end < line.size() &&
         line[end] == '.') {
    const auto following = line.substr(end);
    const auto length = following.find_first_not_of(atom, 1);
    const auto written = following.substr(0, length);
    const auto separator = spelling.find('.', segment + 1);
    if (written.size() == 1 ||
        !spelling.substr(segment, separator == std::string_view::npos
                                      ? separator : separator - segment)
             .starts_with(written)) {
      break;
    }
    end += written.size();
    segment = separator;
  }
  range.end.column = static_cast<std::int32_t>(end + 1);
  return range;
}

}  // namespace

std::optional<InstructionCompletion> instructionCompletionAt(
    std::string_view text, std::string_view cursor_line, SourcePos cursor) {
  ptx_frontend::PtxLexer lexer(text);
  CompletionContext context;
  for (auto token = lexer.consume();; token = lexer.consume()) {
    if (inComment(token, cursor)) {
      return std::nullopt;
    }
    if (token.kind == TokenKind::Eof || before(cursor, token.range.start) ||
        (token.kind == TokenKind::RBrace && cursor == token.range.start)) {
      break;
    }

    context.advanceTo(token.range.start.line);
    const bool identifier = token.kind == TokenKind::Ident;
    const bool label = identifier && context.atStatementStart() &&
                       lexer.peek().kind == TokenKind::Colon;
    if (context.expectsOpcode() && identifier && !label &&
        !token.text.starts_with('%')) {
      if (!before(token.range.end, cursor)) {
        const auto length = static_cast<std::size_t>(
            cursor.column - token.range.start.column);
        return MnemonicCompletion{token.text.substr(0, length), token.range};
      }
      if (auto modifier =
              readModifierCompletion(lexer, token.text, cursor_line, cursor)) {
        return std::move(*modifier);
      }
      if (std::ranges::binary_search(instructionOpcodes(), token.text)) {
        context.operands = OperandContext{
            token.range.start, token.text == "bra" ? OperandSymbolKind::BranchLabel
                                                   : OperandSymbolKind::Register,
            std::nullopt};
        context.body = BodyPosition::Statement;
        continue;
      }
    }
    if (const auto& operands = context.operands) {
      // A lone '%' has its own lexer kind until the register name arrives.
      const bool register_prefix = token.kind == TokenKind::Percent &&
                                   operands->kind == OperandSymbolKind::Register;
      const bool operand_name = identifier || register_prefix;
      if (operand_name && operands->acceptsIdentifier() &&
          !before(token.range.end, cursor) && lexer.peek().kind != TokenKind::Colon) {
        const auto length = static_cast<std::size_t>(
            cursor.column - token.range.start.column);
        return SymbolCompletion{operands->kind, operands->instruction,
                                token.text.substr(0, length), token.range};
      }
      if (token.kind == TokenKind::Semicolon && token.range.start == cursor) {
        if (auto empty = operands->emptyOperandAt(cursor)) {
          return std::move(*empty);
        }
      }
    }
    const bool predicate = identifier && context.expectsPredicate();
    const bool unterminated_comment =
        token.kind == TokenKind::Error && token.text.starts_with("/*");
    if (before(cursor, token.range.end) ||
        (cursor == token.range.end && (predicate || unterminated_comment))) {
      return std::nullopt;
    }
    context.consume(token.kind, token.range.start.line, label);
  }

  context.advanceTo(cursor.line);
  if (context.expectsOpcode()) {
    return MnemonicCompletion{{}, {cursor, cursor}};
  }
  if (context.operands) {
    if (auto empty = context.operands->emptyOperandAt(cursor)) {
      return std::move(*empty);
    }
  }
  return std::nullopt;
}

bool mnemonicAvailable(std::string_view opcode, const checker::TargetInfo* target) {
  const auto orders = modifierOrders(opcode);
  if (!target || orders.empty()) {
    return true;
  }
  const AvailabilityFilter available{target};
  const auto slots = modifierSlots();
  return std::ranges::any_of(orders, [&](const auto& order) {
    return available.allowsOrder(order, slots);
  });
}

std::vector<ModifierCandidate> modifierCandidates(
    const ModifierCompletion& context, std::string_view cursor_line,
    const checker::TargetInfo* target) {
  const auto orders = modifierOrders(context.opcode);
  const auto slots = modifierSlots();
  const AvailabilityFilter available{target};
  std::span<const WrittenModifier> preceding = context.preceding;
  auto prefix = context.prefix;
  auto range = context.range;
  std::map<std::string_view, SourceRange> candidates;
  for (;;) {
    for (const auto order : orders) {
      collectModifierCandidates(order, slots, preceding, prefix, range, available,
                                candidates);
    }
    if (preceding.empty() || preceding.back().range.end != range.start) {
      break;
    }
    // The lexer recognizes compound spellings once complete. Allow partial
    // ones such as `.xorsign.a` to span adjacent tokens while typing.
    prefix.insert(0, preceding.back().spelling);
    range.start = preceding.back().range.start;
    preceding = preceding.first(preceding.size() - 1);
  }

  std::vector<ModifierCandidate> result;
  result.reserve(candidates.size());
  for (const auto& [spelling, edit_range] : candidates) {
    result.push_back({spelling, compoundEditRange(spelling, cursor_line, edit_range)});
  }
  return result;
}

}  // namespace plangd
