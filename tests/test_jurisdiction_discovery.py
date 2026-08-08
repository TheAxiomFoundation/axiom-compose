"""Jurisdiction handling in producer discovery: no US leakage into
non-US programs (#21), specificity-ranked producer selection (#23)."""

import pytest

from axiom_compose import CorpusState, ProgramSpec, RuleSpecModule, compose
from axiom_compose.core import ComposeError, with_corpus_index


def _module(target, rules, imports=()):
    return RuleSpecModule(
        target=target, imports=tuple(imports), payload={"rules": list(rules)}
    )


def _rule(name, formula="0"):
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def _spec(program, outputs, jurisdictions=None):
    raw = {"program": program, "period": "2026-01", "outputs": list(outputs)}
    if jurisdictions is not None:
        raw["scope"] = {"jurisdictions": list(jurisdictions)}
    return ProgramSpec.from_mapping(raw)


US_ONLY_CORPUS = {
    "us:statutes/benefit": _module(
        "us:statutes/benefit", rules=[_rule("child_benefit", "100")]
    ),
}


def test_uk_program_does_not_bind_to_us_producer():
    corpus = with_corpus_index(CorpusState(modules=US_ONLY_CORPUS))

    with pytest.raises(ComposeError, match="no producer found.*child_benefit"):
        compose(_spec("uk/child-benefit", ["child_benefit"]), corpus)


def test_uk_program_with_explicit_jurisdictions_does_not_bind_to_us_producer():
    corpus = with_corpus_index(CorpusState(modules=US_ONLY_CORPUS))

    with pytest.raises(ComposeError, match="no producer found.*child_benefit"):
        compose(
            _spec("uk/child-benefit", ["child_benefit"], jurisdictions=["uk"]),
            corpus,
        )


def test_uk_state_program_federal_scope_resolves_to_uk():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "uk:legislation/benefit": _module(
                    "uk:legislation/benefit", rules=[_rule("child_benefit", "100")]
                ),
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "uk-sct/child-benefit",
            "period": "2026-01",
            "outputs": ["child_benefit"],
            "scope": {"federal": ["legislation/benefit"]},
        }
    )

    program = compose(spec, corpus)

    assert program.payload["imports"] == ["uk:legislation/benefit"]


def test_state_producer_outranks_federal_regardless_of_jurisdiction_order():
    modules = {
        "us:snap/benefit": _module(
            "us:snap/benefit", rules=[_rule("snap_allotment", "100")]
        ),
        "us-ny:snap/benefit": _module(
            "us-ny:snap/benefit", rules=[_rule("snap_allotment", "200")]
        ),
    }
    corpus = with_corpus_index(CorpusState(modules=modules))

    for order in (["us", "us-ny"], ["us-ny", "us"]):
        program = compose(
            _spec("us-ny/snap", ["snap_allotment"], jurisdictions=order), corpus
        )
        assert program.payload["imports"] == ["us-ny:snap/benefit"], order


def test_program_own_prefix_outranks_sibling_state():
    modules = {
        "us-ny:snap/benefit": _module(
            "us-ny:snap/benefit", rules=[_rule("snap_allotment", "100")]
        ),
        "us-nj:snap/benefit": _module(
            "us-nj:snap/benefit", rules=[_rule("snap_allotment", "200")]
        ),
    }
    corpus = with_corpus_index(CorpusState(modules=modules))

    program = compose(
        _spec("us-ny/snap", ["snap_allotment"], jurisdictions=["us-ny", "us-nj"]),
        corpus,
    )

    assert program.payload["imports"] == ["us-ny:snap/benefit"]


def test_producers_outside_the_program_family_tie_ambiguously():
    modules = {
        "us-ny:snap/benefit": _module(
            "us-ny:snap/benefit", rules=[_rule("snap_allotment", "100")]
        ),
        "us-nj:snap/benefit": _module(
            "us-nj:snap/benefit", rules=[_rule("snap_allotment", "200")]
        ),
    }
    corpus = with_corpus_index(CorpusState(modules=modules))

    with pytest.raises(ComposeError, match="ambiguous producers"):
        compose(
            _spec("us/snap", ["snap_allotment"], jurisdictions=["us-ny", "us-nj"]),
            corpus,
        )


def test_foreign_state_never_outranks_the_program_country():
    # Review regression: ranking by raw hyphen count let us-ny (2 segments)
    # silently outrank uk (1 segment) for a uk program.
    modules = {
        "uk:benefit/child": _module(
            "uk:benefit/child", rules=[_rule("child_benefit", "100")]
        ),
        "us-ny:benefit/child": _module(
            "us-ny:benefit/child", rules=[_rule("child_benefit", "200")]
        ),
    }
    corpus = with_corpus_index(CorpusState(modules=modules))

    program = compose(
        _spec("uk/child-benefit", ["child_benefit"], jurisdictions=["uk", "us-ny"]),
        corpus,
    )

    assert program.payload["imports"] == ["uk:benefit/child"]
