"""Canonical country-monorepo loading has no flat-layout fallback."""

from pathlib import Path

import pytest

from axiom_compose import compose, load_spec
from axiom_compose.core import (
    ComposeError,
    _path_uses_exact_directory_entry_casing,
    load_corpus_from_roots,
)

FEDERAL = """
format: rulespec/v1
rules:
  - name: base_amount
    kind: parameter
    dtype: Money
    unit: USD
    versions:
      - effective_from: 2026-01-01
        formula: "10"
"""

STATE = """
format: rulespec/v1
imports:
  - us:policies/base
rules:
  - name: state_amount
    kind: derived
    entity: Household
    dtype: Money
    period: Month
    unit: USD
    versions:
      - effective_from: 2026-01-01
        formula: base_amount
"""

SPEC = """
program: us-co/demo
period: 2026-01
outputs:
  - state_amount
scope:
  federal:
    - policies/base
  state:
    - policies/state
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _monorepo(tmp_path: Path) -> Path:
    root = tmp_path / "rulespec-us"
    _write(root / "us" / "policies" / "base.yaml", FEDERAL)
    _write(root / "us-co" / "policies" / "state.yaml", STATE)
    return root


def test_monorepo_root_yields_jurisdiction_prefixed_targets(tmp_path: Path) -> None:
    corpus = load_corpus_from_roots([_monorepo(tmp_path)], corpus_sha="t")
    assert set(corpus.modules) == {
        "us:policies/base",
        "us-co:policies/state",
    }


def test_composition_uses_exact_country_monorepo_closure(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(SPEC)
    program = compose(
        load_spec(spec_path),
        load_corpus_from_roots([_monorepo(tmp_path / "mono")], corpus_sha="t"),
    )
    assert program.payload["imports"] == [
        "us:policies/base",
        "us-co:policies/state",
    ]


def test_fragmented_atomic_import_builds_fragmentless_module_closure(
    tmp_path: Path,
) -> None:
    root = _monorepo(tmp_path)
    _write(
        root / "us-co" / "policies" / "state.yaml",
        STATE.replace("us:policies/base", "us:policies/base#base_amount"),
    )
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(SPEC)

    corpus = load_corpus_from_roots([root], corpus_sha="t")
    assert corpus.modules["us-co:policies/state"].imports == ("us:policies/base",)

    program = compose(load_spec(spec_path), corpus)
    assert program.payload["imports"] == [
        "us:policies/base",
        "us-co:policies/state",
    ]


@pytest.mark.parametrize(
    "bad_import",
    (
        "policies/base",
        " us:policies/base",
        "us:policies/base.yaml",
        "us:programs/demo",
        "us:policies//base",
        "us:policies/base#Bad",
        "us:policies\\base",
        "us:policies/base:alias",
    ),
)
def test_atomic_import_aliases_are_rejected(
    tmp_path: Path,
    bad_import: str,
) -> None:
    root = _monorepo(tmp_path)
    _write(
        root / "us-co" / "policies" / "state.yaml",
        STATE.replace("  - us:policies/base", f"  - {bad_import!r}"),
    )

    with pytest.raises(ComposeError, match="invalid RuleSpec|invalid canonical"):
        load_corpus_from_roots([root])


def test_program_specs_are_canonical_but_not_atomic_modules(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(root / "us-co" / "programs" / "demo" / "fy-2026.yaml", SPEC)
    corpus = load_corpus_from_roots([root], corpus_sha="t")
    assert set(corpus.modules) == {
        "us:policies/base",
        "us-co:policies/state",
    }


@pytest.mark.parametrize(
    "legacy_root",
    ("legislation", "policies", "programs", "regulations", "statutes"),
)
def test_repository_root_content_is_rejected(
    tmp_path: Path,
    legacy_root: str,
) -> None:
    root = _monorepo(tmp_path)
    (root / legacy_root).mkdir()
    with pytest.raises(ComposeError, match="repository-root RuleSpec content"):
        load_corpus_from_roots([root])


def test_standalone_jurisdiction_repo_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "rulespec-us-co"
    _write(root / "policies" / "state.yaml", STATE)
    with pytest.raises(ComposeError, match="exact country checkout"):
        load_corpus_from_roots([root])


def test_empty_root_list_is_rejected() -> None:
    with pytest.raises(ComposeError, match="at least one explicit"):
        load_corpus_from_roots([])


@pytest.mark.parametrize(
    ("marker", "extension"),
    (("policies", ".yml"), ("programs", ".yml"), ("programs", ".YAML")),
)
def test_legacy_yml_is_rejected_in_atomic_or_program_root(
    tmp_path: Path,
    marker: str,
    extension: str,
) -> None:
    root = _monorepo(tmp_path)
    _write(root / "us-co" / marker / "demo" / f"fy-2026{extension}", SPEC)
    with pytest.raises(ComposeError, match="exact .yaml extension"):
        load_corpus_from_roots([root])


def test_non_rulespec_yaml_in_atomic_root_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(root / "us-co" / "policies" / "not-a-module.yaml", "program: demo\n")
    with pytest.raises(ComposeError, match="must declare format: rulespec/v1"):
        load_corpus_from_roots([root])


def test_composition_module_in_atomic_root_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(
        root / "us-co" / "policies" / "legacy-composition.yaml",
        FEDERAL.replace(
            "format: rulespec/v1\n",
            "format: rulespec/v1\nmodule:\n  kind: composition\n",
        ),
    )

    with pytest.raises(ComposeError, match="must not declare module.kind"):
        load_corpus_from_roots([root])


def test_symlinked_content_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    target = tmp_path / "target.yaml"
    target.write_text(FEDERAL)
    (root / "us" / "policies" / "linked.yaml").symlink_to(target)
    with pytest.raises(ComposeError, match="symlink"):
        load_corpus_from_roots([root])


def test_checkout_alias_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    alias = tmp_path / "rulespec-alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ComposeError, match="real directory"):
        load_corpus_from_roots([alias])


def test_directory_entry_casing_is_exact(tmp_path: Path) -> None:
    actual = tmp_path / "RuleSpec-US"
    actual.mkdir()

    assert _path_uses_exact_directory_entry_casing(actual)
    assert not _path_uses_exact_directory_entry_casing(tmp_path / "rulespec-us")


def test_wrong_country_jurisdiction_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(root / "ca-on" / "policies" / "demo.yaml", FEDERAL)
    with pytest.raises(ComposeError, match="noncanonical jurisdiction"):
        load_corpus_from_roots([root])


def test_duplicate_checkout_is_rejected(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    with pytest.raises(ComposeError, match="duplicate RuleSpec checkout"):
        load_corpus_from_roots([root, root])


def test_duplicate_country_checkout_is_rejected(tmp_path: Path) -> None:
    first = _monorepo(tmp_path / "first")
    second = _monorepo(tmp_path / "second")
    with pytest.raises(ComposeError, match="duplicate RuleSpec country"):
        load_corpus_from_roots([first, second])


def test_program_only_checkout_is_not_an_atomic_corpus(tmp_path: Path) -> None:
    root = tmp_path / "rulespec-us"
    _write(root / "us" / "programs" / "demo.yaml", SPEC)
    with pytest.raises(ComposeError, match="no atomic rulespec/v1 modules"):
        load_corpus_from_roots([root])


def test_non_us_program_derives_national_and_subnational_prefixes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rulespec-ca"
    _write(
        root / "ca" / "legislation" / "base.yaml",
        FEDERAL.replace("base_amount", "national_amount"),
    )
    _write(
        root / "ca-on" / "policies" / "state.yaml",
        STATE.replace("us:policies/base", "ca:legislation/base")
        .replace("base_amount", "national_amount")
        .replace("state_amount", "provincial_amount"),
    )
    spec_path = tmp_path / "ca-on-program.yaml"
    spec_path.write_text(
        """program: ca-on/demo
period: 2026-01
outputs: [provincial_amount]
scope:
  federal: [legislation/base]
  state: [policies/state]
"""
    )

    program = compose(
        load_spec(spec_path),
        load_corpus_from_roots([root], corpus_sha="ca-test"),
    )

    assert program.payload["imports"] == [
        "ca:legislation/base",
        "ca-on:policies/state",
    ]
