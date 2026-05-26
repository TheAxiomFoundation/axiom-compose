"""Tests for compose-time eligibility coverage assertion."""

from __future__ import annotations

import pytest

from axiom_compose.coverage import (
    ELIGIBILITY_MARKERS,
    find_uncovered_eligibility_rules,
    format_coverage_error,
)
from axiom_compose.core import ComposeError, compose
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
        "snap_income_eligible": _rule(
            "snap_income_eligible", "snap_asset_limit"
        ),
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
    msg = format_coverage_error("snap_eligible", ["snap_income_eligible", "snap_asset_limit"])
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
