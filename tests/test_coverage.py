"""Tests for compose-time eligibility coverage assertion."""

from __future__ import annotations

import pytest

from axiom_compose.core import ComposeError, compose
from axiom_compose.coverage import (
    find_uncovered_eligibility_rules,
    format_coverage_error,
)
from axiom_compose.spec import ProgramSpec, TransformationSpec


def _rule(name: str, formula: str = "") -> dict:
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def test_returns_unreferenced_eligibility_rules() -> None:
    rules_by_name = {
        "target_eligible": _rule("target_eligible", "passes_per_member"),
        "passes_per_member": _rule("passes_per_member", ""),
        "snap_income_eligible": _rule("snap_income_eligible", ""),
        "snap_asset_limit": _rule("snap_asset_limit", ""),
        "unrelated_helper": _rule("unrelated_helper", ""),
    }
    uncovered = find_uncovered_eligibility_rules(
        output="target_eligible", rules_by_name=rules_by_name
    )
    assert "snap_income_eligible" in uncovered
    assert "snap_asset_limit" in uncovered
    assert "unrelated_helper" not in uncovered
    assert "passes_per_member" not in uncovered  # referenced


def test_transitive_references_credit_coverage() -> None:
    rules_by_name = {
        "snap_eligible": _rule(
            "snap_eligible", "snap_member_eligible and snap_income_eligible"
        ),
        "snap_member_eligible": _rule("snap_member_eligible", ""),
        "snap_income_eligible": _rule("snap_income_eligible", "snap_asset_limit"),
        "snap_asset_limit": _rule("snap_asset_limit", ""),
    }
    uncovered = find_uncovered_eligibility_rules(
        output="snap_eligible", rules_by_name=rules_by_name
    )
    assert uncovered == []


def test_derived_relation_predicate_is_followed() -> None:
    rules_by_name = {
        "snap_eligible": _rule(
            "snap_eligible", "count_where(member_of_household, snap_unit_member) > 0"
        ),
        "snap_unit_member": {
            "name": "snap_unit_member",
            "kind": "derived_relation",
            "derived_relation": {"predicate": "snap_member_eligible"},
        },
        "snap_member_eligible": _rule("snap_member_eligible", ""),
        "snap_orphan_eligible": _rule("snap_orphan_eligible", ""),  # unreferenced
    }
    uncovered = find_uncovered_eligibility_rules(
        output="snap_eligible", rules_by_name=rules_by_name
    )
    assert "snap_member_eligible" not in uncovered
    assert "snap_orphan_eligible" in uncovered


def test_format_error_lists_rule_names() -> None:
    msg = format_coverage_error(
        "snap_eligible", ["snap_income_eligible", "snap_asset_limit"]
    )
    assert "snap_income_eligible" in msg
    assert "snap_asset_limit" in msg
    assert "acknowledged_incomplete" in msg


# ---------------------------------------------------------------------------
# Integration: compose() raises on coverage gaps.
# ---------------------------------------------------------------------------


def _module(target: str, rules: list[dict]) -> tuple[str, dict]:
    return target, {"target": target, "imports": (), "payload": {"rules": rules}}


def _make_corpus(modules: dict[str, dict]):
    """Build a CorpusState directly from a dict of {target: module_payload}."""
    from axiom_compose.core import (
        CorpusState,
        RuleSpecModule,
        with_corpus_index,
    )

    parsed = {
        target: RuleSpecModule(
            target=target,
            imports=tuple(payload.get("imports") or ()),
            payload=payload["payload"],
        )
        for target, payload in modules.items()
    }
    return with_corpus_index(
        CorpusState(modules=parsed, corpus_sha="test", concept_registry=None)
    )


def test_compose_raises_on_uncovered_eligibility_rules() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us:regulations/7-cfr/273/4",
                    [
                        _rule(
                            "snap_member_citizenship_eligible",
                            "member_is_us_citizen",
                        ),
                    ],
                ),
                _module(
                    "us:regulations/7-cfr/273/9",
                    [_rule("snap_income_eligible", "household_income < 100")],
                ),
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/snap",
        period="2026-01",
        outputs=("snap_eligible",),
        scope={
            "federal": (
                "regulations/7-cfr/273/4",
                "regulations/7-cfr/273/9",
            ),
        },
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "snap_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "conditions": ["snap_member_citizenship_eligible"],
                },
            ),
        ),
    )
    with pytest.raises(ComposeError) as exc:
        compose(spec, corpus)
    assert "snap_income_eligible" in str(exc.value)


def test_compose_passes_when_eligibility_chain_complete() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us:regulations/7-cfr/273/4",
                    [_rule("snap_member_citizenship_eligible", "")],
                ),
                _module(
                    "us:regulations/7-cfr/273/9",
                    [_rule("snap_income_eligible", "")],
                ),
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/snap",
        period="2026-01",
        outputs=("snap_eligible",),
        scope={
            "federal": (
                "regulations/7-cfr/273/4",
                "regulations/7-cfr/273/9",
            ),
        },
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "snap_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "conditions": [
                        "snap_member_citizenship_eligible",
                        "snap_income_eligible",
                    ],
                },
            ),
        ),
    )
    # No raise — both eligibility rules are AND'd into the output.
    compose(spec, corpus)


def test_declared_eligibility_subgate_is_checked_only_through_terminal_output() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us-test:policies/eligibility",
                    [
                        _rule("program_resources_eligible", "resource_limit_met"),
                        _rule("program_income_eligible", "income_limit_met"),
                    ],
                )
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/demo",
        period="2026-01",
        outputs=(
            "program_eligible",
            "program_resources_eligible",
            "program_income_eligible",
        ),
        scope={"state": ("policies/eligibility",)},
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "program_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "conditions": [
                        "program_resources_eligible",
                        "program_income_eligible",
                    ],
                },
            ),
        ),
    )

    # The sibling sub-gates do not need to reference one another. Whole-program
    # coverage belongs to the terminal output that consumes both of them.
    compose(spec, corpus)


def test_direct_atomic_eligibility_output_does_not_own_program_coverage() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us-test:policies/eligibility",
                    [
                        _rule("program_resources_eligible", "resource_limit_met"),
                        _rule("program_income_eligible", "income_limit_met"),
                    ],
                )
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/demo",
        period="2026-01",
        outputs=("program_resources_eligible",),
        scope={"state": ("policies/eligibility",)},
    )

    # An imported provision-level output is intentionally narrower than the
    # entire program. Only a transformation synthesized by this ProgramSpec can
    # claim, and therefore be checked as, a whole-program eligibility result.
    compose(spec, corpus)


def test_compose_acknowledged_incomplete_suppresses_error() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us:regulations/7-cfr/273/4",
                    [_rule("snap_member_citizenship_eligible", "")],
                ),
                _module(
                    "us:regulations/7-cfr/273/9",
                    [_rule("snap_income_eligible", "")],
                ),
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/snap",
        period="2026-01",
        outputs=("snap_eligible",),
        scope={
            "federal": (
                "regulations/7-cfr/273/4",
                "regulations/7-cfr/273/9",
            ),
        },
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "snap_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "conditions": ["snap_member_citizenship_eligible"],
                },
            ),
        ),
        acknowledged_incomplete=("snap_eligible",),
    )
    # Explicit opt-out: snap_eligible's gap is acknowledged. Compose proceeds.
    compose(spec, corpus)


def test_non_eligibility_outputs_are_not_checked() -> None:
    """`snap_benefit` (amount) doesn't trigger the coverage assertion even
    if eligibility rules sit unreferenced in scope — eligibility rules
    live on a different chain by design."""
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us:regulations/7-cfr/273/9",
                    [_rule("snap_income_eligible", "")],
                ),
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/snap",
        period="2026-01",
        outputs=("snap_benefit",),
        scope={"federal": ("regulations/7-cfr/273/9",)},
        transformations=(
            TransformationSpec(
                pattern="sum_terms",
                parameters={
                    "name": "snap_benefit",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Money",
                    "period": "Month",
                    "unit": "USD",
                    "terms": [],
                },
            ),
        ),
    )
    compose(spec, corpus)


def test_limit_named_money_output_is_not_misclassified_as_eligibility() -> None:
    corpus = _make_corpus(
        dict(
            [
                _module(
                    "us-test:policies/limits",
                    [
                        {
                            **_rule("gross_income_limit", "100"),
                            "dtype": "Money",
                        },
                        {
                            **_rule("income_eligible", "income <= gross_income_limit"),
                            "dtype": "Judgment",
                        },
                    ],
                )
            ]
        )
    )
    spec = ProgramSpec(
        program="us-test/demo",
        period="2026-01",
        outputs=("gross_income_limit",),
        scope={"state": ("policies/limits",)},
    )

    compose(spec, corpus)
