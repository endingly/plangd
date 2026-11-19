"""Command-line entry point for the plangd generator."""

import argparse
from importlib.resources import as_file
from pathlib import Path

from ptx_frontend.spec.database import discover_spec_files
from ptx_frontend.spec.resources import packaged_spec_dir

from .emit.instruction_docs import emit_instruction_data
from .instruction_docs import load_instruction_data
from .instruction_modifiers import load_instruction_modifiers


def generate(spec_files: tuple[Path, ...], output: Path) -> None:
    """Generate the instruction documentation and modifier tables together."""
    emit_instruction_data(
        load_instruction_data(spec_files), load_instruction_modifiers(spec_files), output
    )


def main() -> int:
    """Read PTX YAML files and emit the C++ instruction data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="generated C++ directory")
    parser.add_argument(
        "spec_files", type=Path, nargs="*", help="PTX YAML files, or the packaged spec by default"
    )
    args = parser.parse_args()

    if args.spec_files:
        generate(
            tuple(path for path in args.spec_files if not path.name.endswith(".schema.yaml")),
            args.output,
        )
    else:
        with as_file(packaged_spec_dir()) as spec_dir:
            generate(discover_spec_files(spec_dir), args.output)
    return 0
