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
