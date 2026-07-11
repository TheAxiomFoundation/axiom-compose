import pytest

from axiom_compose import CorpusState, ProgramSpec, RuleSpecModule, compose
from axiom_compose.core import (
    ComposeError,
    build_corpus_index,
    load_corpus_from_roots,
    with_corpus_index,
)


def _module(target, imports=(), rules=()):
    return RuleSpecModule(
        target=target,
        imports=tuple(imports),
        payload={"rules": list(rules)},
    )


def _rule(name, formula="0"):
    return {
        "name": name,
        "kind": "derived",
        "versions": [{"effective_from": "2026-01-01", "formula": formula}],
    }


def test_minimal_spec_discovers_output_producer_and_formula_dependencies():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us-or:policies/snap/allotment": _module(
                    "us-or:policies/snap/allotment",
                    imports=(
                        "us-or:policies/snap/eligibility",
                        "us:policies/snap/federal",
                    ),
                    rules=[
                        _rule(
                            "snap_allotment",
                            "if snap_eligible: federal_amount else: 0",
                        )
                    ],
                ),
                "us-or:policies/snap/eligibility": _module(
                    "us-or:policies/snap/eligibility",
                    imports=("us-or:policies/snap/income", "us:policies/snap/federal"),
                    rules=[
                        _rule(
                            "snap_eligible",
                            "oregon_income_eligible and federal_eligible",
                        )
                    ],
                ),
                "us-or:policies/snap/income": _module(
                    "us-or:policies/snap/income",
                    rules=[_rule("oregon_income_eligible", "true")],
                ),
                "us:policies/snap/federal": _module(
                    "us:policies/snap/federal",
                    imports=("us:policies/snap/base",),
                    rules=[
                        _rule("federal_amount", "base_amount"),
                        _rule("federal_eligible", "true"),
                    ],
                ),
                "us:policies/snap/base": _module(
                    "us:policies/snap/base",
                    rules=[_rule("base_amount", "100")],
                ),
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {
            "program": "us-or/snap",
            "period": "2026-01",
            "outputs": ["snap_allotment"],
        }
    )

    program = compose(spec, corpus)

    assert program.payload["imports"] == [
        "us-or:policies/snap/allotment",
        "us-or:policies/snap/eligibility",
        "us-or:policies/snap/income",
        "us:policies/snap/federal",
        "us:policies/snap/base",
    ]


def test_formula_dependency_resolution_is_bounded_by_explicit_import_context():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us-or:policies/snap/allotment": _module(
                    "us-or:policies/snap/allotment",
                    imports=("us:policies/snap/federal",),
                    rules=[_rule("snap_allotment", "shared_amount")],
                ),
                "us:policies/snap/federal": _module(
                    "us:policies/snap/federal",
                    rules=[_rule("shared_amount", "100")],
                ),
                "us-wa:policies/snap/amount": _module(
                    "us-wa:policies/snap/amount",
                    rules=[_rule("shared_amount", "200")],
                ),
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {"program": "us-or/snap", "period": "2026-01", "outputs": ["snap_allotment"]}
    )

    program = compose(spec, corpus)

    assert program.payload["imports"] == [
        "us-or:policies/snap/allotment",
        "us:policies/snap/federal",
    ]


def test_formula_dependency_is_not_guessed_from_jurisdiction_defaults():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us-or:policies/snap/allotment": _module(
                    "us-or:policies/snap/allotment",
                    rules=[_rule("snap_allotment", "shared_amount")],
                ),
                "us:policies/snap/federal": _module(
                    "us:policies/snap/federal",
                    rules=[_rule("shared_amount", "100")],
                ),
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {"program": "us-or/snap", "period": "2026-01", "outputs": ["snap_allotment"]}
    )

    program = compose(spec, corpus)

    assert program.payload["imports"] == ["us-or:policies/snap/allotment"]


def test_index_is_built_once_and_reused_by_compose():
    base = CorpusState(
        modules={
            "us:policies/tax/eitc": _module(
                "us:policies/tax/eitc", rules=[_rule("eitc", "0")]
            ),
        }
    )
    indexed = with_corpus_index(base)
    spec = ProgramSpec.from_mapping(
        {"program": "us/fiit/eitc", "period": "2026", "outputs": ["eitc"]}
    )

    first = compose(spec, indexed)
    second = compose(spec, indexed)

    assert indexed.index is not None
    assert first.source == second.source
    assert first.payload["imports"] == ["us:policies/tax/eitc"]


def test_missing_output_producer_fails_with_scope_diagnostic():
    corpus = with_corpus_index(CorpusState(modules={}))
    spec = ProgramSpec.from_mapping(
        {"program": "us-or/snap", "period": "2026-01", "outputs": ["snap_allotment"]}
    )

    with pytest.raises(ComposeError, match="no producer found.*snap_allotment.*us-or"):
        compose(spec, corpus)


def test_ambiguous_same_jurisdiction_producers_fail():
    corpus = with_corpus_index(
        CorpusState(
            modules={
                "us-or:policies/snap/a": _module(
                    "us-or:policies/snap/a", rules=[_rule("snap_allotment", "0")]
                ),
                "us-or:policies/snap/b": _module(
                    "us-or:policies/snap/b", rules=[_rule("snap_allotment", "0")]
                ),
            }
        )
    )
    spec = ProgramSpec.from_mapping(
        {"program": "us-or/snap", "period": "2026-01", "outputs": ["snap_allotment"]}
    )

    with pytest.raises(ComposeError, match="ambiguous producers.*snap_allotment"):
        compose(spec, corpus)


def test_corpus_index_records_producers_and_consumed_identifiers():
    corpus = CorpusState(
        modules={
            "us:policies/a": _module(
                "us:policies/a",
                rules=[_rule("output", "if input_value > 0: min(input_value, cap)")],
            ),
            "us:policies/b": _module("us:policies/b", rules=[_rule("cap", "100")]),
        }
    )

    index = build_corpus_index(corpus)

    assert index.producers_by_name["output"][0].target == "us:policies/a"
    assert index.consumed_by_target["us:policies/a"] == ("input_value", "cap")


def test_repo_loader_builds_cacheable_index_once_from_rulespec_roots(tmp_path):
    us_root = tmp_path / "rulespec-us"
    (us_root / "us" / "statutes/example").mkdir(parents=True)
    (us_root / "us-or" / "policies/snap").mkdir(parents=True)
    (us_root / "us" / "statutes/example/amount.yaml").write_text(
        """
format: rulespec/v1
rules:
  - name: federal_amount
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: 100
""".strip()
    )
    (us_root / "us-or" / "policies/snap/allotment.yaml").write_text(
        """
format: rulespec/v1
imports:
  - us:statutes/example/amount
rules:
  - name: snap_allotment
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: federal_amount
""".strip()
    )
    (us_root / "us-or" / "policies/snap/allotment.test.yaml").write_text("rules: []")

    corpus = load_corpus_from_roots([us_root], corpus_sha="repo-sha")
    spec = ProgramSpec.from_mapping(
        {"program": "us-or/snap", "period": "2026-01", "outputs": ["snap_allotment"]}
    )
    program = compose(spec, corpus)

    assert corpus.corpus_sha == "repo-sha"
    assert corpus.index is not None
    assert "us-or:policies/snap/allotment.test" not in corpus.modules
    assert program.payload["imports"] == [
        "us-or:policies/snap/allotment",
        "us:statutes/example/amount",
    ]


def test_country_monorepo_loader_rejects_repository_root_content_markers(tmp_path):
    us_root = tmp_path / "rulespec-us"
    (us_root / "statutes").mkdir(parents=True)
    (us_root / "us" / "statutes/example").mkdir(parents=True)
    (us_root / "us" / "statutes/example/amount.yaml").write_text(
        """
format: rulespec/v1
rules:
  - name: federal_amount
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: 100
""".strip()
    )
    with pytest.raises(ComposeError, match="repository-root RuleSpec content"):
        load_corpus_from_roots([us_root])
