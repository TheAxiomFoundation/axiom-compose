# Architecture

`axiom-compose` is a lower-layer library. It assembles RuleSpec programs but
does not know any program's policy.

## Hard Rule

No program-specific code is allowed. The source tree must not contain
`programs/snap.py`, `programs/federal_income_tax.py`, or equivalent dispatch.

Every composition decision must reduce to one of:

1. Atomic encoded law in `rulespec-*`
2. A generic transformation pattern that applies to at least two program
   families
3. A declarative spec parameter

Anything else is a blocker. The fix is to encode the missing law with source
provenance, extend the concept registry in `axiom-encode`, or prove and add a
generic pattern.

## Purity

`axiom_compose.compose(spec, corpus_state)` is pure. It does not read files,
environment variables, time, or network. Callers can build `CorpusState` from
their environment, but the resulting state is explicit input.

## Corpus Index

Producer discovery is a startup/cache concern, not per-query work. A consumer
loads `rulespec-*` roots with `load_corpus_from_roots(...)` or otherwise builds
a `CorpusState`, then attaches a `CorpusIndex` with `with_corpus_index(...)`.

The index records:

- rule producers by name
- explicit RuleSpec imports by module
- formula identifiers consumed by each module

Minimal specs can then name outputs only. The composer resolves each output to
the selected producer and recursively expands explicit imports plus formula
dependencies.

Formula dependency resolution is import-bounded. If a module consumes an
unanchored identifier, the composer only resolves it to a producer in that
module's explicit import closure. It does not guess dependencies from
jurisdiction defaults such as "state plus federal." Cross-jurisdiction
dependencies must be encoded as imports or otherwise anchored in the corpus. If
a reachable producer is ambiguous, composition fails before engine execution.

## Registry

The composer consumes `axiom_encode.concepts` for canonical concept names. It
does not duplicate the registry or introduce a new identifier scheme.

## Output

The output is ordinary RuleSpec YAML with canonical `prefix:path` imports. The
engine remains responsible for resolving those imports against `rulespec-*`
checkouts.
