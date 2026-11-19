"""Extract instruction names and documentation from PTX specification YAML."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ptx_frontend.spec.load_yaml import load_yaml


@dataclass(frozen=True, order=True)
class InstructionDoc:
    """One paired syntax and description, ordered by opcode for lookup."""

    opcode: str
    syntax: str
    description: str


@dataclass(frozen=True)
class InstructionData:
    """All opcodes and their documented forms, each sorted for lookup."""

    opcodes: tuple[str, ...]
    docs: tuple[InstructionDoc, ...]


def load_instruction_data(spec_files: Iterable[Path]) -> InstructionData:
    """Read and deduplicate opcodes and documented forms in stable order."""
    opcodes = set()
    forms = set()
    for path in spec_files:
        if path.name.endswith(".schema.yaml"):
            continue
        instructions = load_yaml(path).get("instructions")
        if not isinstance(instructions, list):
            raise ValueError(f"{path}: expected an instructions sequence")
        for entry in instructions:
            opcode = entry["opcode"]
            opcodes.add(opcode)
            syntax = entry.get("syntax", "").strip(" \t\r\n")
            description = entry.get("doc", "").strip(" \t\r\n")
            if syntax or description:
                forms.add(InstructionDoc(opcode, syntax, description))
    if not opcodes:
        raise ValueError("PTX specs contain no instructions")
    return InstructionData(tuple(sorted(opcodes)), tuple(sorted(forms)))
