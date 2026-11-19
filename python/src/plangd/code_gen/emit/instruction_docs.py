"""Render the instruction documentation declaration and implementation."""

from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from ..instruction_docs import InstructionDoc


def cpp_string(text: str) -> str:
    """Escape UTF-8 bytes as a C++ string literal."""
    escaped = []
    for byte in text.encode("utf-8"):
        if byte in (ord('"'), ord("\\")):
            escaped.append("\\" + chr(byte))
        elif 0x20 <= byte < 0x7F:
            escaped.append(chr(byte))
        else:
            escaped.append(f"\\{byte:03o}")
    return '"' + "".join(escaped) + '"'


def emit_instruction_docs(docs: tuple[InstructionDoc, ...], output: Path) -> None:
    """Render both C++ files and reuse existing output when its bytes match."""
    environment = Environment(
        loader=PackageLoader("plangd.code_gen"),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters["cpp_string"] = cpp_string
    output.mkdir(parents=True, exist_ok=True)
    for name in ("instruction_docs.gen.hpp", "instruction_docs.gen.cpp"):
        target = output / name
        rendered = environment.get_template(f"{name}.j2").render(docs=docs).encode("utf-8")
        if not target.is_file() or target.read_bytes() != rendered:
            target.write_bytes(rendered)
