"""Corpus loader hygiene: collision detection, root validation, and
ingestion restricted to module-bearing directories (#19, #24)."""

import pytest

from axiom_compose.core import ComposeError, load_corpus_from_roots

MODULE = """
format: rulespec/v1
rules:
  - name: {name}
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: "100"
""".strip()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_yml_yaml_collision_is_an_error(tmp_path):
    root = tmp_path / "rulespec-us"
    _write(root / "policies/a.yml", MODULE.format(name="amount"))
    _write(root / "policies/a.yaml", MODULE.format(name="other_amount"))

    with pytest.raises(ComposeError, match="us:policies/a.*two files"):
        load_corpus_from_roots([root])


def test_duplicate_prefix_roots_collide_instead_of_overwriting(tmp_path):
    first = tmp_path / "one" / "rulespec-us"
    second = tmp_path / "two" / "rulespec-us"
    _write(first / "policies/a.yaml", MODULE.format(name="amount"))
    _write(second / "policies/a.yaml", MODULE.format(name="other_amount"))

    with pytest.raises(ComposeError, match="us:policies/a.*two files"):
        load_corpus_from_roots([first, second])


def test_nonexistent_root_is_an_error(tmp_path):
    with pytest.raises(ComposeError, match="does not exist"):
        load_corpus_from_roots([tmp_path / "rulespec-us"])


def test_non_module_directories_are_never_ingested(tmp_path):
    root = tmp_path / "rulespec-us"
    _write(root / "policies/a.yaml", MODULE.format(name="amount"))
    _write(root / "_compose/ca-snap.yaml", MODULE.format(name="snap_eligible"))
    _write(root / ".github/workflows/ci.yaml", "name: CI\non: push")
    _write(root / ".axiom/repository-structure.yaml", MODULE.format(name="junk"))
    _write(root / "programs/us-co/snap/fy-2026.yaml", MODULE.format(name="snap"))
    _write(root / "policies/_scratch/b.yaml", MODULE.format(name="scratch"))

    corpus = load_corpus_from_roots([root])

    assert set(corpus.modules) == {"us:policies/a"}


def test_monorepo_root_sweep_does_not_ingest_state_dirs_as_federal(tmp_path):
    # rulespec-us with BOTH root-level federal content and us-xx state dirs:
    # the federal sweep must not re-index state law under the us: prefix
    # (#19 — wrong-state law composed silently as federal).
    root = tmp_path / "rulespec-us"
    _write(root / "policies/base.yaml", MODULE.format(name="base_amount"))
    _write(root / "us-ca/policies/snap.yaml", MODULE.format(name="snap_benefit"))

    corpus = load_corpus_from_roots([root])

    assert "us:policies/base" in corpus.modules
    assert "us-ca:policies/snap" in corpus.modules
    assert "us:us-ca/policies/snap" not in corpus.modules
