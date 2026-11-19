"""Command-line entry point for the plangd generator."""

import argparse
from importlib.resources import as_file
from pathlib import Path

from ptx_frontend.spec.resources import packaged_spec_dir

from .emit.instruction_docs import emit_instruction_docs
from .instruction_docs import load_instruction_docs


def main() -> int:
    """Read the requested PTX YAML files and emit the C++ documentation table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="generated C++ directory")
    parser.add_argument(
        "spec_files", type=Path, nargs="*", help="PTX YAML files, or the packaged spec by default"
    )
    args = parser.parse_args()

    if args.spec_files:
        docs = load_instruction_docs(args.spec_files)
    else:
        with as_file(packaged_spec_dir()) as spec_dir:
            docs = load_instruction_docs(spec_dir.rglob("*.yaml"))
    emit_instruction_docs(docs, args.output)
    return 0
