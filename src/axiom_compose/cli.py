"""Command-line entry point for axiom-compose."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    ComposeError,
    _path_uses_exact_directory_entry_casing,
    compose,
    load_corpus_from_roots,
)
from .spec import ProgramSpec, load_spec


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
        required=True,
        help=(
            "Absolute path to an exact canonical rulespec-<country> checkout. "
            "Repeat for multiple countries."
        ),
    )
    args = parser.parse_args(argv)

    roots = _collect_rulespec_roots(args.rulespec_root)
    spec_path, jurisdiction, program_path = _canonical_program_spec_path(
        args.spec_path,
        roots,
    )
    spec = load_spec(spec_path)
    _validate_program_spec_location(
        spec,
        spec_path=spec_path,
        jurisdiction=jurisdiction,
        program_path=program_path,
    )
    output_path = (
        _canonical_output_path(args.output, roots) if args.output is not None else None
    )
    corpus = load_corpus_from_roots(roots)
    program = compose(spec, corpus)
    if output_path is None:
        sys.stdout.buffer.write(program.source)
    else:
        output_path.write_bytes(program.source)
    return 0


def _collect_rulespec_roots(cli_roots: list[Path]) -> list[Path]:
    """Resolve only explicit existing country-checkout roots."""

    seen: set[Path] = set()
    deduped: list[Path] = []
    for raw_root in cli_roots:
        root = raw_root
        if not root.is_absolute():
            raise ComposeError(f"--rulespec-root must be absolute: {raw_root}")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ComposeError(f"--rulespec-root does not exist: {raw_root}") from exc
        if resolved != root or not _path_uses_exact_directory_entry_casing(root):
            raise ComposeError(f"--rulespec-root must not contain aliases: {raw_root}")
        if resolved in seen:
            raise ComposeError(f"duplicate --rulespec-root: {resolved}")
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _canonical_program_spec_path(
    raw_path: Path,
    roots: list[Path],
) -> tuple[Path, str, Path]:
    """Bind one CLI spec to a canonical programs/ path in an explicit root."""

    path = Path(raw_path)
    if not path.is_absolute():
        raise ComposeError(f"program spec path must be absolute: {raw_path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ComposeError(f"program spec does not exist: {raw_path}") from exc
    if (
        resolved != path
        or path.is_symlink()
        or not _path_uses_exact_directory_entry_casing(path)
    ):
        raise ComposeError(f"program spec path must not contain aliases: {raw_path}")
    if not path.is_file() or path.suffix != ".yaml":
        raise ComposeError(f"program spec must be a regular .yaml file: {raw_path}")

    matches: list[tuple[str, Path]] = []
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) < 4 or relative.parts[1] != "programs":
            continue
        country = root.name.removeprefix("rulespec-")
        jurisdiction = relative.parts[0]
        if not (jurisdiction == country or jurisdiction.startswith(f"{country}-")):
            continue
        matches.append((jurisdiction, Path(*relative.parts[2:-1])))

    if len(matches) != 1:
        raise ComposeError(
            "program spec must be beneath exactly one explicit "
            "rulespec-<country>/<jurisdiction>/programs/ root: "
            f"{raw_path}"
        )
    jurisdiction, program_path = matches[0]
    return path, jurisdiction, program_path


def _validate_program_spec_location(
    spec: ProgramSpec,
    *,
    spec_path: Path,
    jurisdiction: str,
    program_path: Path,
) -> None:
    segments = spec.program.split("/")
    if (
        len(segments) < 2
        or any(not segment for segment in segments)
        or segments[0] != jurisdiction
        or Path(*segments[1:]) != program_path
    ):
        expected = f"{jurisdiction}/{program_path.as_posix()}"
        raise ComposeError(
            f"program spec id/path mismatch at {spec_path}: "
            f"declares {spec.program!r}, expected {expected!r}"
        )


def _canonical_output_path(raw_path: Path, roots: list[Path]) -> Path:
    """Require an exact external .yaml destination for composed output."""

    path = Path(raw_path)
    if not path.is_absolute():
        raise ComposeError(f"composed output path must be absolute: {raw_path}")
    if path.suffix != ".yaml":
        raise ComposeError(
            f"composed output must use the exact .yaml extension: {raw_path}"
        )
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ComposeError(
            f"composed output parent does not exist: {raw_path}"
        ) from exc
    if parent != path.parent or not _path_uses_exact_directory_entry_casing(parent):
        raise ComposeError(f"composed output path must not contain aliases: {raw_path}")
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        raise ComposeError(
            f"composed output must be outside every RuleSpec checkout: {raw_path}"
        )
    if path.exists() or path.is_symlink():
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve(strict=True) != path
            or not _path_uses_exact_directory_entry_casing(path)
        ):
            raise ComposeError(
                f"composed output must be an exact regular file: {raw_path}"
            )
    return path


if __name__ == "__main__":
    raise SystemExit(main())
