"""Scope entries must name modules the corpus actually contains.

A dangling entry used to pass through composition and only fail at engine
compile, three repos downstream (see axiom-programs#14). With a loaded
corpus, compose now refuses and names the offending targets; module-less
corpus states (pattern-synthesis fixtures) stay exempt.
"""

import pytest

from axiom_compose import CorpusState, ProgramSpec, RuleSpecModule, compose
from axiom_compose.core import ComposeError, with_corpus_index


def _module(target, rules=()):
    return RuleSpecModule(target=target, imports=(), payload={"rules": list(rules)})


def _rule(name, formula="0"):
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def _spec(scope_federal):
    return ProgramSpec.from_mapping(
        {
            "program": "us-co/snap",
            "period": "2026-01",
            "outputs": ["snap_allotment"],
            "scope": {"federal": list(scope_federal)},
        }
    )


def _corpus():
    return with_corpus_index(
        CorpusState(
            modules={
                "us:statutes/7/2017/a": _module(
                    "us:statutes/7/2017/a",
                    rules=[_rule("snap_allotment")],
                )
            }
        )
    )


def test_compose_fails_fast_on_scope_entry_missing_from_corpus():
    spec = _spec(["statutes/7/2017/a", "regulations/7-cfr/273/9/d/6/iii"])
    with pytest.raises(ComposeError, match=r"273/9/d/6/iii"):
        compose(spec, _corpus())


def test_compose_accepts_scope_when_every_entry_resolves():
    program = compose(_spec(["statutes/7/2017/a"]), _corpus())
    assert "us:statutes/7/2017/a" in program.payload["imports"]


def test_module_less_corpus_state_skips_scope_existence_check():
    spec = _spec(["statutes/7/2017/a", "regulations/7-cfr/273/9/d/6/iii"])
    program = compose(spec, CorpusState(corpus_sha="fixture-sha"))
    assert "us:regulations/7-cfr/273/9/d/6/iii" in program.payload["imports"]
