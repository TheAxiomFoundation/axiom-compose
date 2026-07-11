"""Country-monorepo seam test: compose the production CA SNAP spec against a
real canonical rulespec-us checkout.

The OASDI test covers single-repo federal composition; this covers the
federal+state seam, so a rulespec-us change that breaks state programs
(renamed rule, dropped import target, changed producer) fails here at merge
time instead of weeks later in microsim or oracles. Assertions are
structural rather than byte-golden so legitimate content evolution in the
rulespec repos does not break CI.
"""

import os
import subprocess
from pathlib import Path

import pytest

from axiom_compose import compose, load_corpus_from_roots, load_spec

ROOT = Path(__file__).parent


def test_real_ca_snap_composition_resolves_across_repos():
    program = _compose_ca_snap()

    assert program.target == "us-ca/snap/2026-01"

    imports = program.payload.get("imports", [])
    federal = [item for item in imports if item.startswith("us:")]
    state = [item for item in imports if item.startswith("us-ca:")]
    assert federal, "composition should pull federal rules from rulespec-us"
    assert state, "composition should pull state rules from rulespec-us/us-ca"

    rule_names = {rule["name"] for rule in program.payload.get("rules", [])}
    for output in ("snap_eligible", "snap_benefit"):
        assert output in rule_names, f"spec output {output} missing from composition"


def test_real_ca_snap_composition_compiles_through_engine(tmp_path):
    engine_root = _external_path("AXIOM_RULES_ENGINE_ROOT")
    rulespec_root = _external_path("AXIOM_RULESPEC_US_ROOT")
    program = _compose_ca_snap()

    program_path = tmp_path / "ca-snap.yaml"
    artifact_path = tmp_path / "ca-snap.compiled.json"
    program_path.write_bytes(program.source)
    subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--",
            "compile-composed",
            "--program",
            str(program_path),
            "--rulespec-root",
            str(rulespec_root),
            "--output",
            str(artifact_path),
        ],
        cwd=engine_root,
        check=True,
    )
    assert artifact_path.exists()


def _compose_ca_snap():
    rulespec_us = _external_path("AXIOM_RULESPEC_US_ROOT")

    spec = load_spec(rulespec_us / "us-ca" / "programs" / "snap" / "fy-2026.yaml")
    corpus = load_corpus_from_roots(
        [rulespec_us],
        corpus_sha="rulespec-us@acceptance",
    )
    return compose(spec, corpus)


def _external_path(env_var: str) -> Path:
    raw = os.environ.get(env_var)
    if not raw:
        pytest.skip(f"{env_var} is not set")
    path = Path(raw)
    if not path.exists():
        raise AssertionError(f"{env_var} points to missing path: {path}")
    return path
