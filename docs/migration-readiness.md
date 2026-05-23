# Migration Readiness

This repo currently proves deterministic, program-agnostic composition,
corpus-index discovery, and one simple real-corpus engine round trip:
`tests/test_real_program_oasdi.py` composes the employee OASDI wage-tax rule
from `rulespec-us`, compiles it with `axiom-rules-engine`, and runs a fixture
case. It does not yet prove end-to-end migration for existing FIIT or SNAP
consumers.

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

2. Broaden real-corpus golden tests.

   Current real-corpus golden coverage is the OASDI wage-tax smoke path. Add
   tests that pin `(real corpus SHA, spec)` to exact emitted YAML for broader
   surfaces, for example EITC and CTC. These tests should fail loudly when the
   rulespec corpus or composer behavior drifts.

3. Broaden engine round-trip tests.

   Current engine round-trip coverage proves only the simplest OASDI path. Add
   integration tests that compose broader real specs, compile them with
   `axiom-rules-engine`, run small fixture populations or ECPS slices, and
   compare to the current working path.

4. Keep CLI thin.

   The stable contract is the Python library API. Consumers such as
   `axiom-oracles` and `dashboard-builder` should import `compose()` directly
   and own their own caching, UX, and engine invocation.

5. Treat `axiom-encode` as optional but explicit.

   `load_default_concept_registry()` requires `axiom-encode`. Install the
   optional `concepts` extra when a consumer wants default concept-registry
   loading from this package.
