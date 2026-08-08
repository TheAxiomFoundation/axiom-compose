"""Regressions confirmed by the pre-merge adversarial review: coverage
must not be silently disabled by dtype aliases, acknowledged siblings, or
module-less corpus states."""

import pytest

from axiom_compose import CorpusState, ProgramSpec, RuleSpecModule, compose
from axiom_compose.core import ComposeError, with_corpus_index


def _module(target, rules, imports=()):
    return RuleSpecModule(
        target=target, imports=tuple(imports), payload={"rules": list(rules)}
    )


def _rule(name, formula="0", dtype=None):
    rule = {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }
    if dtype is not None:
        rule["dtype"] = dtype
    return rule


def test_boolean_dtype_gates_still_count_for_coverage():
    # The engine accepts Bool/Boolean as judgment dtypes; a Boolean gate
    # must not vanish from the required set.
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/eligibility": _module(
                    "us:snap/eligibility",
                    rules=[
                        _rule("snap_eligible", "snap_member_eligible", "Judgment"),
                        _rule("snap_member_eligible", "true", "Judgment"),
                        _rule("snap_residency_eligible", "true", "Boolean"),
                    ],
                )
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_eligible"],
            "scope": {"federal": ["snap/eligibility"]},
        }
    )

    with pytest.raises(ComposeError, match="snap_residency_eligible"):
        compose(spec, corpus)


def test_acknowledged_sibling_does_not_unpolice_the_rollup():
    # Review repro: acknowledging snap_expedited_eligible (which references
    # snap_eligible) must not exempt snap_eligible from coverage.
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/eligibility": _module(
                    "us:snap/eligibility",
                    rules=[
                        _rule("snap_eligible", "snap_member_eligible", "Judgment"),
                        _rule("snap_member_eligible", "true", "Judgment"),
                        _rule("snap_expedited_eligible", "snap_eligible", "Judgment"),
                        _rule("snap_income_eligible", "true", "Judgment"),
                    ],
                )
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_eligible", "snap_expedited_eligible"],
            "acknowledged_incomplete": ["snap_expedited_eligible"],
            "scope": {"federal": ["snap/eligibility"]},
        }
    )

    with pytest.raises(ComposeError, match="snap_income_eligible"):
        compose(spec, corpus)


def test_component_output_reached_by_checked_rollup_is_skipped():
    # The legitimate skip: a leaf the CHECKED rollup references may be
    # exposed as an output without being policed as a top-level gate.
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/eligibility": _module(
                    "us:snap/eligibility",
                    rules=[
                        _rule(
                            "snap_eligible",
                            "snap_income_eligible and snap_resource_eligible",
                            "Judgment",
                        ),
                        _rule("snap_income_eligible", "true", "Judgment"),
                        _rule("snap_resource_eligible", "true", "Judgment"),
                    ],
                )
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_eligible", "snap_income_eligible"],
            "scope": {"federal": ["snap/eligibility"]},
        }
    )

    program = compose(spec, corpus)
    assert program.payload["imports"] == ["us:snap/eligibility"]


def test_module_less_corpus_with_unresolvable_output_composes():
    # CLI no-roots mode: the corpus has no modules, so a corpus-produced
    # output cannot be inspected — coverage must not crash on it (review
    # finding: the removed not-in-rules guard).
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_eligible"],
            "scope": {"federal": ["snap/eligibility"]},
            "transformations": [
                {
                    "pattern": "all_of",
                    "name": "snap_member_eligible",
                    "conditions": ["is_resident"],
                    "effective_from": "2026-01-01",
                }
            ],
        }
    )

    program = compose(spec, CorpusState())
    assert program.payload["imports"] == ["us:snap/eligibility"]
