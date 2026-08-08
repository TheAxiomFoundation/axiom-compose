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


def test_to_mapping_round_trips_auto_gate_outputs():
    spec = ProgramSpec.from_mapping(
        {
            "program": "us-ny/snap",
            "period": "2026-01",
            "outputs": ["snap_eligible"],
            "auto_gate_outputs": ["snap_eligible"],
        }
    )
    round_tripped = ProgramSpec.from_mapping(spec.to_mapping())
    assert round_tripped == spec
    assert round_tripped.auto_gate_outputs == ("snap_eligible",)


def test_unknown_top_level_keys_are_rejected():
    with pytest.raises(SpecError, match="unknown spec keys: auto_gate_output"):
        ProgramSpec.from_mapping(
            {
                "program": "us/snap",
                "period": "2026-01",
                "outputs": ["snap_eligible"],
                "auto_gate_output": ["snap_eligible"],
            }
        )


def test_reserved_scope_filters_are_rejected_until_implemented():
    with pytest.raises(SpecError, match="reserved but not implemented: exclude"):
        ProgramSpec.from_mapping(
            {
                "program": "us/snap",
                "period": "2026-01",
                "outputs": ["snap_eligible"],
                "scope": {"exclude": ["policies/old"]},
            }
        )


def test_acknowledged_incomplete_must_reference_declared_outputs():
    with pytest.raises(SpecError, match="acknowledged_incomplete not in outputs"):
        ProgramSpec.from_mapping(
            {
                "program": "us/snap",
                "period": "2026-01",
                "outputs": ["snap_eligible"],
                "acknowledged_incomplete": ["snap_eligibile_typo"],
            }
        )
