from pathlib import Path

import pytest

from axiom_compose.spec import ProgramSpec, SpecError, load_spec


FIXTURES = Path(__file__).with_name("fixtures")


def test_load_spec_normalizes_lists_to_tuples():
    spec = load_spec(FIXTURES / "simple-benefit.yaml")

    assert spec.program == "us/example-benefit"
    assert spec.outputs == ("example_benefit",)
    assert spec.scope["federal"] == (
        "statutes/example/1",
        "regulations/example/2",
    )
    assert spec.transformations[0].pattern == "sum_terms"


def test_spec_requires_outputs():
    with pytest.raises(SpecError, match="outputs"):
        ProgramSpec.from_mapping(
            {
                "program": "us/example",
                "period": "2026-01",
                "scope": {"federal": ["statutes/example"]},
            }
        )
