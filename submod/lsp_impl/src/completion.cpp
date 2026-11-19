#include "completion.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <tuple>

#include <ptx_frontend/lexer/ptx_lexer.hpp>

namespace plangd {
namespace {

using ptx_frontend::PtxToken;
using ptx_frontend::SourcePos;
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

/// Tracks statement boundaries and distinguishes blocks from operand braces.
struct CompletionContext {
  BodyPosition body = BodyPosition::Start;
  unsigned blocks = 0;
  unsigned other_braces = 0;
  bool function_header = false;
  bool header_pragma = false;
  std::int32_t directive_line = 0;

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

}  // namespace

std::optional<MnemonicCompletion> mnemonicCompletionAt(std::string_view text,
                                                       SourcePos cursor) {
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
        !token.text.starts_with('%') && !before(token.range.end, cursor)) {
      const auto length = static_cast<std::size_t>(
          cursor.column - token.range.start.column);
      return MnemonicCompletion{token.text.substr(0, length), token.range};
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
  return std::nullopt;
}

}  // namespace plangd
