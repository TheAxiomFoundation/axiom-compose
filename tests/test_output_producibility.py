"""Composed programs must actually produce their declared outputs, and
emitted imports must resolve in the loaded corpus (#22)."""

import pytest

from axiom_compose import CorpusState, ProgramSpec, RuleSpecModule, compose
from axiom_compose.core import ComposeError, with_corpus_index


def _module(target, rules=(), imports=()):
    return RuleSpecModule(
        target=target, imports=tuple(imports), payload={"rules": list(rules)}
    )


def _rule(name, formula="0"):
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def test_typoed_output_with_explicit_scope_fails_to_compose():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/benefit": _module(
                    "us:snap/benefit", rules=[_rule("snap_allotment")]
                )
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_allotmnet_typo"],
            "scope": {"federal": ["snap/benefit"]},
        }
    )

    with pytest.raises(ComposeError, match="not produced.*snap_allotmnet_typo"):
        compose(spec, corpus)


def test_unresolved_transitive_import_fails_instead_of_surfacing_in_engine():
    # us:snap/benefit imports a module absent from the loaded corpus (it
    # lives in a repo the caller did not load). Composition must fail here,
    # not three repos downstream at engine load time.
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/benefit": _module(
                    "us:snap/benefit",
                    rules=[_rule("snap_allotment")],
                    imports=("us:snap/tables",),
                )
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/snap",
            "period": "2026-01",
            "outputs": ["snap_allotment"],
            "scope": {"federal": ["snap/benefit"]},
        }
    )

    with pytest.raises(ComposeError, match="do not resolve.*us:snap/tables"):
        compose(spec, corpus)


def test_auto_gating_a_corpus_produced_output_gets_no_coverage_exemption():
    # #20: previously a silent no-op that ALSO exempted the output from
    # the coverage assertion — the exact trap auto-gate was built to
    # close. The gate cannot rewrite corpus-produced rules, so the output
    # keeps its full coverage obligation: uncovered gates fail loudly.
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us:snap/eligibility": _module(
                    "us:snap/eligibility",
                    rules=[
                        _rule("snap_eligible", "snap_member_eligible"),
                        _rule("snap_member_eligible", "true"),
                        _rule("snap_income_eligible", "true"),
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
            "auto_gate_outputs": ["snap_eligible"],
            "scope": {"federal": ["snap/eligibility"]},
        }
    )

    with pytest.raises(ComposeError, match="does not reference.*eligibility"):
        compose(spec, corpus)
