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


@pytest.mark.parametrize("forbidden", ("format", "imports", "module", "rules", "extra"))
def test_spec_rejects_every_unknown_or_atomic_top_level_key(forbidden: str):
    with pytest.raises(SpecError, match=rf"unsupported spec keys: {forbidden}"):
        ProgramSpec.from_mapping(
            {
                "program": "us/example",
                "period": "2026-01",
                "outputs": ["benefit"],
                forbidden: {},
            }
        )


def test_to_mapping_preserves_auto_gate_outputs():
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/example",
            "period": "2026-01",
            "outputs": ["eligible"],
            "auto_gate_outputs": ["eligible"],
        }
    )

    assert spec.to_mapping()["auto_gate_outputs"] == ["eligible"]


def test_spec_rejects_non_string_mapping_keys():
    with pytest.raises(SpecError, match="spec keys must be strings: 1"):
        ProgramSpec.from_mapping(
            {
                "program": "us/example",
                "period": "2026-01",
                "outputs": ["benefit"],
                1: "not-a-schema-key",
            }
        )
