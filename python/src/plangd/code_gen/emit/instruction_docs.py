"""Render the generated instruction tables used by the language server."""

from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from ..instruction_docs import InstructionData
from ..instruction_modifiers import InstructionModifiers


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


def emit_instruction_data(
    data: InstructionData, modifiers: InstructionModifiers, output: Path
) -> None:
    """Render the C++ tables and reuse existing output when its bytes match."""
    environment = Environment(
        loader=PackageLoader("plangd.code_gen"),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters["cpp_string"] = cpp_string
    output.mkdir(parents=True, exist_ok=True)
    for table, table_data in (("instruction_docs", data), ("instruction_modifiers", modifiers)):
        for extension in ("hpp", "cpp"):
            name = f"{table}.gen.{extension}"
            target = output / name
            rendered = environment.get_template(f"{name}.j2").render(data=table_data).encode("utf-8")
            if not target.is_file() or target.read_bytes() != rendered:
                target.write_bytes(rendered)
