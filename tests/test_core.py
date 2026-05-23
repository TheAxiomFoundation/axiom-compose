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
