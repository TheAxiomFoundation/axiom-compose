"""Real compose → engine round trip against a fixture population (#3).

For each program this composes the real spec against the rulespec-us
checkout, compiles the emitted YAML with `compile-composed`, and runs the
fixture population through it. It compares two ways:

- with the current working path: the program's atomic module compiled with
  `compile` and run on the same population (expected values come from
  running the code);
- with the expected values in the module's `.test.yaml`, which come from the
  corpus and cite the law.

The population is every `.test.yaml` case that supplies inputs; cases with
no inputs (parameter-only checks) have no entity to query and are left out.

OASDI runs today. EITC and CTC both close over
`us/policies/irs/rev-proc-2025-32/earned-income-credit`, which the engine
refuses to load (plural `corpus_citation_paths`); they xfail on exactly that
message and become real runs once rulespec-us fixes the module.

Requires AXIOM_RULESPEC_US_ROOT and AXIOM_RULES_ENGINE_ROOT; skipped
otherwise.
"""

import functools
from pathlib import Path

import pytest
from real_round_trip import (
    Catalog,
    build_request,
    compile_or_xfail,
    external_path,
    load_cases,
    numeric_equal,
    outcomes,
    run_compiled,
)

from axiom_compose import compose, load_corpus_from_roots, load_spec

FIXTURES = Path(__file__).parent / "fixtures"

# program -> (spec, atomic module the program's outputs come from)
PROGRAMS = {
    "oasdi": (FIXTURES / "oasdi-wage-tax-spec.yaml", "us/statutes/26/3101/a"),
    "eitc": (FIXTURES / "eitc-spec.yaml", "us/statutes/26/32"),
    "ctc": (FIXTURES / "ctc-spec.yaml", "us/statutes/26/24"),
}


@functools.cache
def _corpus(root: Path):
    return load_corpus_from_roots([root], corpus_sha="rulespec-us@checkout")


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_composed_program_matches_atomic_module_on_fixture_population(
    program, tmp_path
):
    rulespec_us = external_path("AXIOM_RULESPEC_US_ROOT")
    engine_root = external_path("AXIOM_RULES_ENGINE_ROOT")
    spec_path, module = PROGRAMS[program]

    composed_path = tmp_path / "composed.yaml"
    composed_path.write_bytes(
        compose(load_spec(spec_path), _corpus(rulespec_us)).source
    )
    composed_artifact = tmp_path / "composed.compiled.json"
    artifact = compile_or_xfail(
        engine_root,
        "compile-composed",
        composed_path,
        rulespec_us,
        composed_artifact,
    )
    atomic_artifact = tmp_path / "atomic.compiled.json"
    compile_or_xfail(
        engine_root,
        "compile",
        rulespec_us / f"{module}.yaml",
        rulespec_us,
        atomic_artifact,
    )

    cases = [
        case for case in load_cases(rulespec_us / f"{module}.test.yaml") if case.inputs
    ]
    assert cases, f"{module}.test.yaml has no cases with inputs"
    outputs = sorted({name for case in cases for name in case.expected})
    request, case_by_entity = build_request(cases, Catalog(artifact), outputs)

    composed = outcomes(
        run_compiled(engine_root, composed_artifact, request), case_by_entity
    )
    atomic = outcomes(
        run_compiled(engine_root, atomic_artifact, request), case_by_entity
    )

    assert set(composed) == {case.name for case in cases}
    assert composed == atomic
    mismatches = {
        (case.name, name): (composed[case.name].get(name), expected)
        for case in cases
        for name, expected in case.expected.items()
        if not numeric_equal(composed[case.name].get(name), expected)
    }
    assert not mismatches, mismatches
