#include <cstdlib>
#include <exception>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>

#include <yaml-cpp/yaml.h>

namespace {

/// Trims outer whitespace from a YAML documentation field.
std::string trim(std::string_view text) {
  constexpr std::string_view whitespace = " \t\r\n";
  const auto first = text.find_first_not_of(whitespace);
  if (first == std::string_view::npos) {
    return {};
  }
  const auto last = text.find_last_not_of(whitespace);
  return std::string(text.substr(first, last - first + 1));
}

/// Writes a C++ string literal that preserves the bytes of a spec field.
void writeLiteral(std::ostream& out, std::string_view text) {
  out << '"';
  for (const char value : text) {
    const auto byte = static_cast<unsigned char>(value);
    if (value == '"' || value == '\\') {
      out << '\\' << value;
    } else if (byte < 0x20 || byte >= 0x7f) {
      out << '\\' << static_cast<char>('0' + (byte >> 6))
          << static_cast<char>('0' + ((byte >> 3) & 7))
          << static_cast<char>('0' + (byte & 7));
    } else {
      out << value;
    }
  }
  out << '"';
}

}  // namespace

/// Extracts and embeds the opcode, syntax and documentation from installed PTX specs.
int main(int argc, char** argv) {
  if (argc < 3) {
    std::cerr << "Usage: plangd_instruction_doc_generator output spec.yaml...\n";
    return EXIT_FAILURE;
  }

  try {
    // Sorting by opcode groups all forms for a binary search at runtime.
    std::set<std::tuple<std::string, std::string, std::string>> forms;
    for (int index = 2; index < argc; ++index) {
      const auto spec = YAML::LoadFile(argv[index]);
      const auto instructions = spec["instructions"];
      if (!instructions.IsSequence()) {
        throw std::runtime_error(std::string(argv[index]) +
                                 ": expected an instructions sequence");
      }
      for (const auto& instruction : instructions) {
        auto opcode = instruction["opcode"].as<std::string>();
        auto syntax = trim(instruction["syntax"].as<std::string>(""));
        auto doc = trim(instruction["doc"].as<std::string>(""));
        if (!syntax.empty() || !doc.empty()) {
          forms.emplace(std::move(opcode), std::move(syntax), std::move(doc));
        }
      }
    }
    if (forms.empty()) {
      throw std::runtime_error("PTX specs contain no documented instructions");
    }

    std::ofstream out;
    out.exceptions(std::ios::failbit | std::ios::badbit);
    out.open(argv[1]);
    out << "// Generated from the installed PTX specification.\n"
           "constexpr InstructionDoc kInstructionDocs[] = {\n";
    for (const auto& [opcode, syntax, doc] : forms) {
      out << "    {";
      writeLiteral(out, opcode);
      out << ", ";
      writeLiteral(out, syntax);
      out << ", ";
      writeLiteral(out, doc);
      out << "},\n";
    }
    out << "};\n";
    out.close();
  } catch (const std::exception& error) {
    std::cerr << "plangd_instruction_doc_generator: " << error.what() << '\n';
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}
