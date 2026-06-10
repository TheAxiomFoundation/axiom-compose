"""Country-monorepo checkouts load with the same targets as legacy
sibling repos: rulespec-us/us-co/… and rulespec-us-co/… both yield
us-co:<path> modules."""

from axiom_compose import compose, load_spec
from axiom_compose.core import load_corpus_from_roots

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


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _monorepo(tmp_path):
    root = tmp_path / "rulespec-us"
    _write(root / "us" / "policies" / "base.yaml", FEDERAL)
    _write(root / "us-co" / "policies" / "state.yaml", STATE)
    return root


def _legacy(tmp_path):
    us = tmp_path / "rulespec-us"
    co = tmp_path / "rulespec-us-co"
    _write(us / "policies" / "base.yaml", FEDERAL)
    _write(co / "policies" / "state.yaml", STATE)
    return us, co


def test_monorepo_root_yields_prefixed_targets(tmp_path):
    corpus = load_corpus_from_roots([_monorepo(tmp_path)], corpus_sha="t")
    assert "us:policies/base" in corpus.modules
    assert "us-co:policies/state" in corpus.modules


def test_monorepo_and_legacy_layouts_load_identical_targets(tmp_path):
    mono = load_corpus_from_roots([_monorepo(tmp_path / "mono")], corpus_sha="t")
    legacy = load_corpus_from_roots(list(_legacy(tmp_path / "legacy")), corpus_sha="t")
    assert set(mono.modules) == set(legacy.modules)


def test_composition_from_monorepo_matches_legacy(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(SPEC)
    spec = load_spec(spec_path)

    mono = compose(
        spec, load_corpus_from_roots([_monorepo(tmp_path / "mono")], corpus_sha="t")
    )
    legacy = compose(
        spec,
        load_corpus_from_roots(list(_legacy(tmp_path / "legacy")), corpus_sha="t"),
    )
    assert mono.text() == legacy.text()
    assert "us-co:policies/state" in mono.payload["imports"]


def test_programs_dir_in_monorepo_is_not_a_jurisdiction(tmp_path):
    root = _monorepo(tmp_path)
    _write(root / "programs" / "us-co" / "demo" / "fy-2026.yaml", SPEC)
    corpus = load_corpus_from_roots([root], corpus_sha="t")
    assert not any(target.startswith("programs") for target in corpus.modules)
    assert "us:policies/base" in corpus.modules
