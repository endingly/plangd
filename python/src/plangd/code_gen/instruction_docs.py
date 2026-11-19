"""Extract the authored instruction documentation from PTX specification YAML."""

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


def load_instruction_docs(spec_files: Iterable[Path]) -> tuple[InstructionDoc, ...]:
    """Read and deduplicate documented instruction forms in stable order."""
    forms = set()
    for path in spec_files:
        if path.name.endswith(".schema.yaml"):
            continue
        instructions = load_yaml(path).get("instructions")
        if not isinstance(instructions, list):
            raise ValueError(f"{path}: expected an instructions sequence")
        for entry in instructions:
            syntax = entry.get("syntax", "").strip(" \t\r\n")
            description = entry.get("doc", "").strip(" \t\r\n")
            if syntax or description:
                forms.add(InstructionDoc(entry["opcode"], syntax, description))
    if not forms:
        raise ValueError("PTX specs contain no documented instructions")
    return tuple(sorted(forms))
