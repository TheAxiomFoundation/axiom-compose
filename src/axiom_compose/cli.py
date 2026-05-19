"""Command-line entry point for axiom-compose."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import CorpusState, compose
from .spec import load_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="axiom-compose",
        description="Emit a deterministic RuleSpec composition from a declarative spec.",
    )
    parser.add_argument("spec_path", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)

    spec = load_spec(args.spec_path)
    program = compose(spec, CorpusState())
    if args.output is None:
        sys.stdout.buffer.write(program.source)
    else:
        args.output.write_bytes(program.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
