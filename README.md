# axiom-compose

`axiom-compose` is a deterministic, program-agnostic composer for Axiom RuleSpec programs.

It takes a declarative spec plus an explicit corpus state and emits a runnable RuleSpec composition module. The core function is pure:

```python
from pathlib import Path

from axiom_compose import compose, load_corpus_from_roots

corpus_state = load_corpus_from_roots(
    [Path("/srv/rulespec-us")],
    corpus_sha="rulespec-us-commit-sha",
)
program = compose(spec, corpus_state)
```

Each root must be an explicit, absolute, unaliased checkout named exactly
`rulespec-<country>`. Jurisdictions live directly below it. The loader indexes
only atomic `.yaml` modules below `legislation/`, `policies/`, `regulations/`,
and `statutes/`; declarative ProgramSpecs below `programs/` are intentionally
outside the atomic corpus. Flat jurisdiction repositories, `.yml`, symlinked
content, ambient root discovery, and an empty corpus fail closed.

The CLI likewise accepts only a ProgramSpec at its canonical
`<jurisdiction>/programs/<program>/<file>.yaml` path. `--output`, when used,
must be an absolute, unaliased `.yaml` path outside every RuleSpec checkout;
omitting it writes the composition to stdout.

The same `ProgramSpec` and `CorpusState` always produce byte-identical output. Runtime tools can load files, build corpus state, cache artifacts, and invoke `axiom-rules-engine`, but composition itself does not read the environment, wall clock, network, or filesystem.

Corpus scanning is not per-household work. Consumers should build or load a `CorpusState`
with its `CorpusIndex` once per corpus SHA, then reuse that indexed state across many
`compose()` calls and engine executions.

Consumers should import the library API directly rather than shelling out to the
CLI:

```python
from axiom_compose import compose, load_corpus_from_roots, load_spec
```

The optional concept-registry integration uses `axiom-encode`:

```bash
python -m pip install ".[concepts]"
```

## Architecture Rule

No program-specific Python belongs in this repository. A composition decision must come from exactly one of:

1. Atomic encoded law in `rulespec-*`
2. A generic transformation pattern that applies across program families
3. A declarative spec parameter

If a needed rule does not fit, it belongs in the appropriate rulespec corpus with source provenance, or the program does not ship.

See [docs/architecture.md](docs/architecture.md), [docs/adding-a-program.md](docs/adding-a-program.md), and [docs/migration-readiness.md](docs/migration-readiness.md).
