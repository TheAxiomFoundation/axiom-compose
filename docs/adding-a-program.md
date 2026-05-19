# Adding A Program

Add a declarative YAML spec. Do not add Python.

```yaml
program: us/example-benefit
period: 2026-01
outputs: [example_benefit, example_eligible]
```

If the corpus is indexed, that is enough for the composer to discover producer
modules and dependencies. Optional scope constraints can limit discovery:

```yaml
program: us-or/snap
period: 2026-01
outputs: [snap_allotment, snap_eligible]
scope:
  jurisdictions: [us-or, us]
```

Explicit import scopes are still supported for fixtures, migrations, and cases
where a caller wants to pin entry modules:

```yaml
program: us/example-benefit
period: 2026-01
outputs: [example_benefit, example_eligible]
scope:
  federal:
    - statutes/example/1
    - regulations/example/2
  state:
    - regulations/example-state/3
rounding: half-even
```

`scope` entries become RuleSpec imports. `federal` resolves to `us:` and
`state` resolves to the jurisdiction prefix before the slash in `program`.
Direct prefixes such as `us-ny:` may also be used.

If composition needs a derived value, first decide which bucket it belongs to:

- Existing concept drift: extend `axiom-encode`'s concept registry.
- Generic transformation: add or reuse a pattern in `transformations.py`.
- Legal rule: encode it in `rulespec-*` with source provenance.
- Pure parameter: add data to the spec schema.

Do not create a program module in this repository.
