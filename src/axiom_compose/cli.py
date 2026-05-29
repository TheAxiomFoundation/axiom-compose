"""Command-line entry point for axiom-compose."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import CorpusState, compose, load_corpus_from_roots
from .spec import load_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="axiom-compose",
        description="Emit a deterministic RuleSpec composition from a declarative spec.",
    )
    parser.add_argument("spec_path", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--rulespec-root",
        type=Path,
        action="append",
        default=[],
        help="Rulespec repo root to load corpus from. Repeatable. "
        "If omitted, AXIOM_RULESPEC_REPO_ROOTS is consulted. With no "
        "roots compose runs against an empty corpus: auto-gate becomes "
        "a no-op so only the spec's hand-written transformations apply.",
    )
    args = parser.parse_args(argv)

    spec = load_spec(args.spec_path)
    roots = _collect_rulespec_roots(args.rulespec_root)
    corpus = load_corpus_from_roots(roots) if roots else CorpusState()
    program = compose(spec, corpus)
    if args.output is None:
        sys.stdout.buffer.write(program.source)
    else:
        args.output.write_bytes(program.source)
    return 0


def _collect_rulespec_roots(cli_roots: list[Path]) -> list[Path]:
    """Merge --rulespec-root flags with AXIOM_RULESPEC_REPO_ROOTS env var.

    Env var follows PATH conventions (colon-separated on Unix) — same as
    what the rust engine consults at compile time, so the two tools
    resolve against the same set of repos."""

    roots: list[Path] = list(cli_roots)
    env_value = os.environ.get("AXIOM_RULESPEC_REPO_ROOTS")
    if env_value:
        for raw in env_value.split(os.pathsep):
            raw = raw.strip()
            if raw:
                roots.append(Path(raw))
    seen: set[Path] = set()
    deduped: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
