from pathlib import Path

from axiom_compose import CorpusState, RuleSpecModule, compose, load_spec
from axiom_compose.core import dependency_closure

ROOT = Path(__file__).parent


def test_dependency_closure_is_deterministic_depth_first():
    corpus = CorpusState(
        modules={
            "us:a": RuleSpecModule(target="us:a", imports=("us:c", "us:b")),
            "us:b": RuleSpecModule(target="us:b", imports=("us:c",)),
            "us:c": RuleSpecModule(target="us:c"),
        }
    )

    assert dependency_closure(("us:a",), corpus) == ("us:a", "us:c", "us:b")


def test_compose_matches_golden_fixture():
    spec = load_spec(ROOT / "fixtures" / "simple-benefit.yaml")
    program = compose(spec, CorpusState(corpus_sha="test-sha"))
    golden = (ROOT / "golden" / "simple-benefit.rulespec.yaml").read_bytes()

    assert program.source == golden


def test_co_snap_fixture_is_data_only():
    spec = load_spec(ROOT / "fixtures" / "co-snap-spec.yaml")
    program = compose(spec, CorpusState(corpus_sha="local-fixture"))

    assert program.target == "us-co/snap/2026-01"
    assert "us-co:regulations/10-ccr-2506-1/4.207.2" in program.text()
    assert "rules:" not in program.text()


def test_auto_gate_wraps_output_with_uncovered_eligibility_rules():
    """When the spec lists an output under auto_gate_outputs, compose should
    discover eligibility-shaped rules in scope that the output doesn't reach
    and emit a wrapper `<name> = all_of(<name>_core, ...discovered)`. The
    original output rule is renamed to `<name>_core` and remains in the
    program, while the synthesized `<name>` becomes the new top-level
    answer. Without this, programs whose hand-written output transformation
    misses a household-level eligibility test silently return over-permissive
    answers (the CA SNAP "left-only true" pattern surfaced 2026-05-28)."""
    from axiom_compose.spec import ProgramSpec, TransformationSpec

    spec = ProgramSpec(
        program="us-ca/snap",
        period="2026-01",
        outputs=("widget_eligible",),
        scope={"federal": ("statutes/x/1",)},
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "widget_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "source": "us:statutes/x/1",
                    "conditions": ["widget_member_eligible"],
                },
            ),
        ),
        auto_gate_outputs=("widget_eligible",),
    )
    corpus = CorpusState(
        modules={
            "us:statutes/x/1": RuleSpecModule(
                target="us:statutes/x/1",
                payload={
                    "rules": [
                        {
                            "name": "widget_member_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        {
                            "name": "widget_income_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        {
                            "name": "widget_resource_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                    ]
                },
            ),
        },
        corpus_sha="auto-gate-fixture",
    )

    program = compose(spec, corpus)
    rule_names = [rule.get("name") for rule in program.payload.get("rules") or ()]
    assert "widget_eligible" in rule_names
    assert "widget_eligible_core" in rule_names

    rules_by_name = {
        rule["name"]: rule
        for rule in program.payload["rules"]
        if isinstance(rule, dict)
    }
    gate = rules_by_name["widget_eligible"]
    formula = gate["versions"][0]["formula"]
    # All three eligibility tests must appear in the gate's formula
    # (widget_eligible_core wraps the original chain; income + resource
    # eligibility get AND'd in from scope discovery).
    assert "widget_eligible_core" in formula
    assert "widget_income_eligible" in formula
    assert "widget_resource_eligible" in formula


def test_auto_gate_excludes_unrelated_program_eligibility_rules():
    """Auto-gate must only AND-in household-level gates that share the
    output's program prefix. Otherwise gating SNAP would pull in CTC/EITC
    `*_income_eligible` rules that happen to be in the same imported scope
    (since SNAP and tax modules can share rulespec-us files).

    Live failure 2026-05-28: naive auto-gate pulled 9 unrelated eligibility
    rules into snap_eligible. Engine returned None for all 8 ECPS cases
    because the foreign rules required inputs the SNAP program doesn't
    expose, collapsing the AND-chain to None."""
    from axiom_compose.spec import ProgramSpec, TransformationSpec

    spec = ProgramSpec(
        program="us-ca/snap",
        period="2026-01",
        outputs=("snap_eligible",),
        scope={"federal": ("statutes/x/1",)},
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "snap_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "source": "us:statutes/x/1",
                    "conditions": ["snap_member_eligible"],
                },
            ),
        ),
        auto_gate_outputs=("snap_eligible",),
    )
    corpus = CorpusState(
        modules={
            "us:statutes/x/1": RuleSpecModule(
                target="us:statutes/x/1",
                payload={
                    "rules": [
                        {
                            "name": "snap_member_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # SNAP household gate — should be AND-gated.
                        {
                            "name": "snap_resource_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # CTC eligibility rule that shares the imported
                        # scope — must NOT be pulled into snap_eligible.
                        {
                            "name": "ctc_refundable_foreign_income_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # A noncitizen-specific SNAP rule whose name doesn't
                        # match the household-gate patterns — also a
                        # conditional alternative, must NOT be auto-gated.
                        {
                            "name": "legal_noncitizen_eligible_for_cfap",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                    ]
                },
            ),
        },
        corpus_sha="auto-gate-fixture",
    )

    program = compose(spec, corpus)
    rules_by_name = {
        rule["name"]: rule
        for rule in program.payload["rules"]
        if isinstance(rule, dict)
    }
    gate = rules_by_name["snap_eligible"]
    formula = gate["versions"][0]["formula"]

    assert "snap_resource_eligible" in formula  # in-program household gate
    assert "ctc_refundable_foreign_income_eligible" not in formula  # wrong program
    assert "legal_noncitizen_eligible_for_cfap" not in formula  # not a gate pattern


def test_auto_gate_includes_state_namespaced_program_rules():
    """State rulespecs name their gates with the state code prefix
    (e.g. ``ny_snap_categorically_eligible``). Auto-gate must recognize
    those as belonging to the SNAP program so they get AND-gated into
    ``snap_eligible`` — otherwise the dashboard sees the same over-
    permissive failure mode that bit CA SNAP, just at the state layer.

    Live failure 2026-05-28: NY SNAP bootstrap returned 100% eligible
    vs PolicyEngine 29.8% because 9 ``ny_snap_*_eligible`` rules in
    scope were rejected by the prefix-only filter."""
    from axiom_compose.spec import ProgramSpec, TransformationSpec

    spec = ProgramSpec(
        program="us-ny/snap",
        period="2026-01",
        outputs=("snap_eligible",),
        scope={"federal": ("statutes/x/1",)},
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "snap_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "source": "us:statutes/x/1",
                    "conditions": ["snap_member_eligible"],
                },
            ),
        ),
        auto_gate_outputs=("snap_eligible",),
    )
    corpus = CorpusState(
        modules={
            "us:statutes/x/1": RuleSpecModule(
                target="us:statutes/x/1",
                payload={
                    "rules": [
                        {
                            "name": "snap_member_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # Federal gate — same shape as before, must gate.
                        {
                            "name": "snap_income_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # State-namespaced SNAP gate — the new case.
                        {
                            "name": "ny_snap_categorically_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                        # TANF rule that depends on SNAP cross-program —
                        # must NOT be auto-gated into snap_eligible.
                        {
                            "name": "tanf_income_eligible",
                            "kind": "derived",
                            "versions": [{"formula": "true"}],
                        },
                    ]
                },
            ),
        },
        corpus_sha="auto-gate-fixture",
    )

    program = compose(spec, corpus)
    rules_by_name = {
        rule["name"]: rule
        for rule in program.payload["rules"]
        if isinstance(rule, dict)
    }
    gate = rules_by_name["snap_eligible"]
    formula = gate["versions"][0]["formula"]

    assert "snap_income_eligible" in formula
    assert "ny_snap_categorically_eligible" in formula
    assert "tanf_income_eligible" not in formula
