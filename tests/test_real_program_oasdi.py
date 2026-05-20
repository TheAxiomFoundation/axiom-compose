import json
import os
import subprocess
from pathlib import Path

import pytest

from axiom_compose import compose, load_corpus_from_roots, load_spec

ROOT = Path(__file__).parent


def test_real_rulespec_us_oasdi_composition_is_stable():
    rulespec_us = _external_path("AXIOM_RULESPEC_US_ROOT")
    spec = load_spec(ROOT / "fixtures" / "oasdi-wage-tax-spec.yaml")
    corpus = load_corpus_from_roots(
        [rulespec_us],
        corpus_sha="rulespec-us@acceptance",
    )

    program = compose(spec, corpus)

    assert program.target == "us/payroll/oasdi-wage-tax/2026"
    assert program.text() == (
        "format: rulespec/v1\n"
        "module:\n"
        "  kind: composition\n"
        "  summary: 'Deterministic composition for us/payroll/oasdi-wage-tax at 2026. Outputs: oasdi_wage_tax. Corpus: rulespec-us@acceptance.'\n"
        "imports:\n"
        "- us:statutes/26/3101/a\n"
    )


def test_real_rulespec_us_oasdi_composition_round_trips_through_engine(tmp_path):
    rulespec_us = _external_path("AXIOM_RULESPEC_US_ROOT")
    engine_root = _external_path("AXIOM_RULES_ENGINE_ROOT")
    spec = load_spec(ROOT / "fixtures" / "oasdi-wage-tax-spec.yaml")
    corpus = load_corpus_from_roots(
        [rulespec_us],
        corpus_sha="rulespec-us@acceptance",
    )
    program = compose(spec, corpus)
    program_path = tmp_path / "oasdi-wage-tax.yaml"
    artifact_path = tmp_path / "oasdi-wage-tax.compiled.json"
    request_path = tmp_path / "request.json"
    program_path.write_bytes(program.source)
    request_path.write_text(
        json.dumps(
            {
                "mode": "fast",
                "dataset": {
                    "inputs": [
                        {
                            "name": "us:statutes/26/3101/a#input.wages",
                            "entity": "TaxUnit",
                            "entity_id": "tax_unit:1",
                            "interval": {
                                "start": "2026-01-01",
                                "end": "2027-01-01",
                            },
                            "value": {"kind": "decimal", "value": "1000"},
                        }
                    ],
                    "relations": [],
                },
                "queries": [
                    {
                        "entity_id": "tax_unit:1",
                        "period": {
                            "period_kind": "tax_year",
                            "start": "2026-01-01",
                            "end": "2027-01-01",
                        },
                        "outputs": ["us:statutes/26/3101/a#oasdi_wage_tax"],
                    }
                ],
            }
        )
    )
    env = {
        **os.environ,
        "AXIOM_RULESPEC_REPO_ROOTS": str(rulespec_us.parent),
    }

    subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--",
            "compile",
            "--program",
            str(program_path),
            "--output",
            str(artifact_path),
        ],
        cwd=engine_root,
        env=env,
        check=True,
    )
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--",
            "run-compiled",
            "--artifact",
            str(artifact_path),
        ],
        cwd=engine_root,
        env=env,
        check=True,
        input=request_path.read_text(),
        text=True,
        capture_output=True,
    )

    response = json.loads(completed.stdout)
    output = response["results"][0]["outputs"]["us:statutes/26/3101/a#oasdi_wage_tax"]
    assert response["metadata"]["actual_mode"] == "fast"
    assert output["value"] == {"kind": "decimal", "value": "62"}


def _external_path(env_var: str) -> Path:
    raw = os.environ.get(env_var)
    if not raw:
        pytest.skip(f"{env_var} is not set")
    path = Path(raw)
    if not path.exists():
        raise AssertionError(f"{env_var} points to missing path: {path}")
    return path
