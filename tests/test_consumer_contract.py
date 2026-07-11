from axiom_compose import compose, load_corpus_from_roots, load_spec


def test_consumer_builds_index_once_then_composes_minimal_spec(tmp_path):
    rules_us = tmp_path / "rulespec-us"
    spec_path = tmp_path / "us-or-snap.yaml"
    (rules_us / "us" / "statutes/snap").mkdir(parents=True)
    (rules_us / "us-or" / "policies/snap").mkdir(parents=True)
    (rules_us / "us" / "statutes/snap/federal.yaml").write_text(
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
    (rules_us / "us-or" / "policies/snap/allotment.yaml").write_text(
        """
format: rulespec/v1
imports:
  - us:statutes/snap/federal
rules:
  - name: snap_allotment
    kind: derived
    versions:
      - effective_from: '2026-01-01'
        formula: federal_amount
""".strip()
    )
    spec_path.write_text(
        """
program: us-or/snap
period: 2026-01
outputs:
  - snap_allotment
""".strip()
    )

    corpus = load_corpus_from_roots([rules_us], corpus_sha="sha-for-cache")
    program = compose(load_spec(spec_path), corpus)

    assert corpus.index is not None
    assert program.target == "us-or/snap/2026-01"
    assert program.source == compose(load_spec(spec_path), corpus).source
    assert program.payload["imports"] == [
        "us-or:policies/snap/allotment",
        "us:statutes/snap/federal",
    ]
