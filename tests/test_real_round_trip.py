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

OASDI runs today. EITC and CTC check nothing yet: both close over
`us:policies/irs/rev-proc-2025-32/earned-income-credit`, which the engine
refuses to load (plural `corpus_citation_paths`, rulespec-us#1312). They
xfail only on that error naming that module, and become real runs once
rulespec-us fixes it.

No program here exercises a composer-generated rule yet: the OASDI, EITC
and CTC specs have no `transformations`, so each composition is an import
list and "composed == atomic" can only catch closure or import bugs. A
pattern-synthesis bug (`any_related`, `derived_relation`, ...) would not
reach this test.

Requires AXIOM_RULESPEC_US_ROOT and AXIOM_RULES_ENGINE_ROOT; skipped
otherwise.
"""

from pathlib import Path

import pytest
from real_corpus import corpus
from real_round_trip import (
    Catalog,
    build_request,
    compile_or_xfail,
    external_path,
    known_blocker,
    load_cases,
    numeric_equal,
    outcomes,
    run_compiled,
)

from axiom_compose import compose, load_spec

FIXTURES = Path(__file__).parent / "fixtures"

# program -> (spec, atomic module the program's outputs come from)
PROGRAMS = {
    "oasdi": (FIXTURES / "oasdi-wage-tax-spec.yaml", "us/statutes/26/3101/a"),
    "eitc": (FIXTURES / "eitc-spec.yaml", "us/statutes/26/32"),
    "ctc": (FIXTURES / "ctc-spec.yaml", "us/statutes/26/24"),
}


EITC_MODULE = "us:policies/irs/rev-proc-2025-32/earned-income-credit"


def _plural_error(module: str) -> str:
    # The message axiom-rules-engine prints for this load failure.
    return (
        "failed to load RuleSpec module `/tmp/composed.yaml`: RuleSpec module "
        f"`{module}` declares removed plural `corpus_citation_paths`; every "
        "source/proof node must declare exactly one singular "
        "`corpus_citation_path`"
    )


def test_known_blocker_matches_the_eitc_module_error():
    assert known_blocker(_plural_error(EITC_MODULE)) is not None


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        # The same error from any other module still fails.
        _plural_error("us:statutes/26/3101/a"),
        _plural_error("us:policies/irs/rev-proc-2025-32/child-tax-credit"),
        # The module named next to some other error still fails.
        f"RuleSpec module `{EITC_MODULE}` declares non-canonical "
        "corpus_citation_path `x`",
        f"failed to parse formula `1 + * )` in `{EITC_MODULE}`",
        # A formula syntax error with no module error fails.
        "expected expression, found `*` at 1:5",
    ],
)
def test_known_blocker_rejects_anything_else(stderr):
    assert known_blocker(stderr) is None


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_composed_program_matches_atomic_module_on_fixture_population(
    program, tmp_path
):
    rulespec_us = external_path("AXIOM_RULESPEC_US_ROOT")
    engine_root = external_path("AXIOM_RULES_ENGINE_ROOT")
    spec_path, module = PROGRAMS[program]

    composed_path = tmp_path / "composed.yaml"
    composed_path.write_bytes(
        compose(
            load_spec(spec_path), corpus(rulespec_us, "rulespec-us@checkout")
        ).source
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
