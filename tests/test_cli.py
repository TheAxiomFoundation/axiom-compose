from pathlib import Path

import pytest

from axiom_compose.cli import _collect_rulespec_roots, main
from axiom_compose.core import ComposeError


def _canonical_root(tmp_path: Path) -> Path:
    root = tmp_path / "rulespec-us"
    (root / "us" / "statutes").mkdir(parents=True)
    return root


def test_cli_requires_explicit_rulespec_root(tmp_path: Path) -> None:
    spec = tmp_path / "program.yaml"
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")

    with pytest.raises(SystemExit, match="2"):
        main([str(spec)])


def test_collect_roots_ignores_ambient_engine_root_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _canonical_root(tmp_path)
    monkeypatch.setenv("AXIOM_RULESPEC_REPO_ROOTS", "/ambient/rulespec-us")

    assert _collect_rulespec_roots([root]) == [root]


def test_collect_roots_rejects_relative_path() -> None:
    with pytest.raises(ComposeError, match="must be absolute"):
        _collect_rulespec_roots([Path("rulespec-us")])


def test_collect_roots_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ComposeError, match="does not exist"):
        _collect_rulespec_roots([tmp_path / "rulespec-us"])


def test_collect_roots_rejects_alias(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)

    with pytest.raises(ComposeError, match="must not contain aliases"):
        _collect_rulespec_roots([alias])


def test_collect_roots_rejects_duplicate(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)

    with pytest.raises(ComposeError, match="duplicate --rulespec-root"):
        _collect_rulespec_roots([root, root])


def test_cli_composes_against_explicit_canonical_root(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    (root / "us" / "statutes" / "demo.yaml").write_text(
        """format: rulespec/v1
rules:
  - name: result
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: 1
"""
    )
    spec = root / "us" / "programs" / "demo" / "fy-2026.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")
    output = tmp_path / "compiled.yaml"

    assert (
        main(
            [
                str(spec),
                "--rulespec-root",
                str(root),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "us:statutes/demo" in output.read_text()


def test_cli_rejects_program_spec_outside_canonical_program_root(
    tmp_path: Path,
) -> None:
    root = _canonical_root(tmp_path)
    spec = tmp_path / "program.yaml"
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")

    with pytest.raises(ComposeError, match="beneath exactly one explicit"):
        main([str(spec), "--rulespec-root", str(root)])


def test_cli_rejects_program_spec_alias(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    target = root / "us" / "programs" / "demo" / "fy-2026.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")
    alias = target.with_name("alias.yaml")
    alias.symlink_to(target)

    with pytest.raises(ComposeError, match="must not contain aliases"):
        main([str(alias), "--rulespec-root", str(root)])


@pytest.mark.parametrize(
    ("output", "message"),
    (
        (Path("composed.yaml"), "must be absolute"),
        (Path("OUTPUT_YML"), "exact .yaml extension"),
    ),
)
def test_cli_rejects_noncanonical_output_spelling(
    tmp_path: Path, output: Path, message: str
) -> None:
    root = _canonical_root(tmp_path)
    spec = root / "us/programs/demo/fy-2026.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")
    output_path = tmp_path / "composed.yml" if output.name == "OUTPUT_YML" else output

    with pytest.raises(ComposeError, match=message):
        main(
            [
                str(spec),
                "--rulespec-root",
                str(root),
                "--output",
                str(output_path),
            ]
        )


def test_cli_rejects_composed_output_inside_rulespec_checkout(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    spec = root / "us/programs/demo/fy-2026.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")

    with pytest.raises(ComposeError, match="outside every RuleSpec checkout"):
        main(
            [
                str(spec),
                "--rulespec-root",
                str(root),
                "--output",
                str(spec.with_name("composed.yaml")),
            ]
        )


def test_cli_rejects_symlinked_composed_output(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    spec = root / "us/programs/demo/fy-2026.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("program: us/demo\nperiod: '2026'\noutputs: [result]\n")
    target = tmp_path / "target.yaml"
    target.write_text("existing")
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(target)

    with pytest.raises(ComposeError, match="exact regular file"):
        main(
            [
                str(spec),
                "--rulespec-root",
                str(root),
                "--output",
                str(alias),
            ]
        )


@pytest.mark.parametrize(
    ("relative", "program"),
    (
        ("us/programs/demo/fy-2026.yaml", "us/other"),
        ("us-ca/programs/demo/fy-2026.yaml", "us/demo"),
    ),
)
def test_cli_rejects_program_id_path_or_jurisdiction_mismatch(
    tmp_path: Path,
    relative: str,
    program: str,
) -> None:
    root = _canonical_root(tmp_path)
    spec = root / relative
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(f"program: {program}\nperiod: '2026'\noutputs: [result]\n")

    with pytest.raises(ComposeError, match="id/path mismatch"):
        main([str(spec), "--rulespec-root", str(root)])
