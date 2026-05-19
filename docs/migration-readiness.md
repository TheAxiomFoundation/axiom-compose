# Migration Readiness

This repo currently proves deterministic, program-agnostic composition and
corpus-index discovery. It does not yet prove end-to-end migration for existing
FIIT or SNAP consumers.

## Tracked Gaps

1. Encode CO SNAP bucket-C rules.

   The classification in `docs/co-snap-synthesis-classification.md` found eight
   synthesis rules that belong in atomic law, not composer code:

   - `other_countable_self_employment_earned_income`
   - `shelter_costs`
   - `total_household_resources_before_exclusions`
   - `snap_student_eligible`
   - `snap_ssn_eligible`
   - `snap_residency_citizenship_eligible`
   - `snap_work_requirement_eligible`
   - `snap_eligible`

   These need source-provenance encoding in `rulespec-us` and/or
   `rulespec-us-co`. Until they land, the composer should not pretend to
   reproduce the current CO SNAP artifact.

2. Add real-corpus golden tests.

   Current golden coverage uses a synthetic fixture. Add tests that pin
   `(real corpus SHA, spec)` to exact emitted YAML, for example EITC and CTC.
   These tests should fail loudly when the rulespec corpus or composer behavior
   drifts.

3. Add engine round-trip tests.

   Current tests prove deterministic emission, not engine readiness. Add an
   integration test that composes a real spec, compiles it with
   `axiom-rules-engine`, runs a small fixture population or ECPS slice, and
   compares to the current working path.

4. Keep CLI thin.

   The stable contract is the Python library API. Consumers such as
   `axiom-oracles` and `dashboard-builder` should import `compose()` directly
   and own their own caching, UX, and engine invocation.

5. Treat `axiom-encode` as optional but explicit.

   `load_default_concept_registry()` requires `axiom-encode`. Install the
   optional `concepts` extra when a consumer wants default concept-registry
   loading from this package.
