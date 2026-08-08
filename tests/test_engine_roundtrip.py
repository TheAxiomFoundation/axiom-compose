"""Hermetic compose→engine round-trip (#29).

Builds a minimal canonical rulespec checkout in tmp_path, composes a
program whose scope uses a fragment-qualified entry, and compiles the
output through engine main's `compile-composed`. Unlike the real-corpus
tests this does not depend on rulespec-us passing the engine's root scan,
so it guards the seam contract itself: module-only imports in, artifact
out. Requires AXIOM_RULES_ENGINE_ROOT; skipped otherwise.
"""

import os
import subprocess
from pathlib import Path

import pytest

from axiom_compose import compose, load_corpus_from_roots
from axiom_compose.spec import ProgramSpec

MODULE = """\
format: rulespec/v1
rules:
  - name: wage_tax_rate
    kind: parameter
    entity: Household
    dtype: Rate
    period: Month
    source: fixture
    versions:
      - effective_from: '2026-01-01'
        formula: '0.062'
  - name: wage_tax
    kind: derived
    entity: Household
    dtype: Money
    period: Month
    unit: USD
    source: fixture
    versions:
      - effective_from: '2026-01-01'
        formula: wages * wage_tax_rate
"""


def test_composed_output_compiles_through_engine_main(tmp_path):
    engine_root = os.environ.get("AXIOM_RULES_ENGINE_ROOT")
    if not engine_root:
        pytest.skip("AXIOM_RULES_ENGINE_ROOT is not set")

    root = tmp_path / "rulespec-us"
    module_path = root / "us" / "policies" / "wage-tax.yaml"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(MODULE)

    corpus = load_corpus_from_roots([root], corpus_sha="hermetic")
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/wage-tax",
            "period": "2026-01",
            "outputs": ["wage_tax"],
            # Fragment-qualified on purpose: the emitted import must be
            # the module-only target or engine main rejects it (#29).
            "scope": {"federal": ["policies/wage-tax#wage_tax"]},
        }
    )
    program = compose(spec, corpus)
    assert program.payload["imports"] == ["us:policies/wage-tax"]

    program_path = tmp_path / "composed.yaml"
    artifact_path = tmp_path / "composed.compiled.json"
    program_path.write_bytes(program.source)

    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--",
            "compile-composed",
            "--program",
            str(program_path),
            "--rulespec-root",
            str(root),
            "--output",
            str(artifact_path),
        ],
        cwd=Path(engine_root),
        env=dict(os.environ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert artifact_path.exists()
