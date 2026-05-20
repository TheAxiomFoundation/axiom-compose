from pathlib import Path

import pytest

from axiom_compose import CorpusState, compose, load_spec

ROOT = Path(__file__).parent
SRC = ROOT.parent / "src" / "axiom_compose"


@pytest.mark.parametrize(
    ("fixture", "expected_target", "expected_imports"),
    [
        (
            "co-snap-spec.yaml",
            "us-co/snap/2026-01",
            {
                "us:regulations/7-cfr/273/10",
                "us-co:regulations/10-ccr-2506-1/4.207.2",
            },
        ),
        (
            "ny-snap-spec.yaml",
            "us-ny/snap/2026-01",
            {
                "us:regulations/7-cfr/273/10",
                "us-ny:regulations/18-nycrr/387/14/a/1",
            },
        ),
        (
            "fiit-spec.yaml",
            "us/fiit/2026",
            {
                "us:statutes/26/1/j",
                "us:statutes/26/24",
                "us:statutes/26/32",
                "us:policies/irs/rev-proc-2025-32/income-tax-brackets",
            },
        ),
        (
            "eitc-spec.yaml",
            "us/fiit/eitc/2026",
            {
                "us:statutes/26/32",
                "us:statutes/26/32/c/2",
                "us:policies/irs/rev-proc-2025-32/earned-income-credit",
            },
        ),
        (
            "ctc-spec.yaml",
            "us/fiit/ctc/2026",
            {
                "us:statutes/26/24",
                "us:statutes/26/24/d",
                "us:policies/irs/rev-proc-2025-32/child-tax-credit",
            },
        ),
    ],
)
def test_program_families_share_the_same_composition_path(
    fixture, expected_target, expected_imports
):
    spec = load_spec(ROOT / "fixtures" / fixture)
    program = compose(spec, CorpusState(corpus_sha="fixture-sha"))

    assert program.target == expected_target
    assert set(program.payload["imports"]) >= expected_imports
    assert "programs/" not in program.text()
    assert "snap.py" not in program.text()
    assert "federal_income_tax.py" not in program.text()


def test_state_scope_is_derived_from_program_prefix():
    co = compose(load_spec(ROOT / "fixtures" / "co-snap-spec.yaml"), CorpusState())
    ny = compose(load_spec(ROOT / "fixtures" / "ny-snap-spec.yaml"), CorpusState())

    assert "us-co:regulations/10-ccr-2506-1/4.207.2" in co.payload["imports"]
    assert "us-ny:regulations/18-nycrr/387/14/a/1" in ny.payload["imports"]


def test_source_tree_has_no_program_specific_modules():
    forbidden_paths = {
        "programs",
        "snap.py",
        "federal_income_tax.py",
        "fiit.py",
        "eitc.py",
        "ctc.py",
        "medicaid.py",
    }
    paths = {part for path in SRC.rglob("*") for part in path.relative_to(SRC).parts}

    assert paths.isdisjoint(forbidden_paths)
