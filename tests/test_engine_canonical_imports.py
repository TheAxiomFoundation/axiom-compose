"""Composed output must be engine-canonical: module-only imports, no
fragments, deduped (#29)."""

from axiom_compose import CorpusState, ProgramSpec, compose
from axiom_compose.core import module_from_payload, with_corpus_index


def _rule(name, formula="0"):
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def test_fragment_qualified_imports_are_stripped_and_deduped():
    # A corpus module importing two rules from the same module via
    # fragment-qualified targets must emit ONE module-only import, and the
    # closure walk must still resolve the fragment target to its module.
    tables = module_from_payload(
        "us-az:policies/standards", {"rules": [_rule("payment_standard", "100")]}
    )
    benefit = module_from_payload(
        "us-az:policies/benefit",
        {
            "imports": [
                "us-az:policies/standards#payment_standard",
                "us-az:policies/standards#payment_standard_each_additional",
            ],
            "rules": [_rule("tanf_benefit", "payment_standard")],
        },
    )
    corpus = with_corpus_index(
        CorpusState(modules={module.target: module for module in (tables, benefit)})
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us-az/tanf",
            "period": "2026-01",
            "outputs": ["tanf_benefit"],
            "scope": {"state": ["policies/benefit"]},
        }
    )

    program = compose(spec, corpus)

    imports = program.payload["imports"]
    assert imports == ["us-az:policies/benefit", "us-az:policies/standards"]
    assert not any("#" in item for item in imports)


def test_fragment_qualified_scope_entries_normalize_to_modules():
    module = module_from_payload(
        "us:policies/oasdi", {"rules": [_rule("oasdi_tax", "1")]}
    )
    corpus = with_corpus_index(CorpusState(modules={module.target: module}))
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/oasdi",
            "period": "2026-01",
            "outputs": ["oasdi_tax"],
            "scope": {"federal": ["policies/oasdi#oasdi_tax"]},
        }
    )

    program = compose(spec, corpus)

    assert program.payload["imports"] == ["us:policies/oasdi"]
