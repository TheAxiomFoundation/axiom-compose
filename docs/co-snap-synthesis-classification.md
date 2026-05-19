# CO SNAP Synthesis Classification

This is an empirical architecture check against the local file
`~/rulespec-us-co/policies/cdhs/snap/fy-2026-benefit-calculation.yaml` as read
on 2026-05-19. That checkout contains 9 synthesis rules under `rules:`.

The classification is not an architectural special case. CO SNAP is only a
fixture used to test whether the universal composer rule holds.

## Buckets

- A: Already-atomic-elsewhere. Extend or rely on the concept registry; the
  synthesis rule disappears.
- B: Generic pattern. Implement only if the pattern demonstrably applies across
  program families.
- C: Should-be-atomic-law. Encode in the relevant `rulespec-*` repo with source
  provenance.
- D: Spec-parameterizable. Move to declarative spec data.
- E: Resists classification. Escalate; the strict architecture is falsified if
  essential entries remain here.

## Rules

| Rule | Bucket | Action |
| --- | --- | --- |
| `max_allotment_for_number_of_boarders` | B | Use a generic `table_lookup_with_extension` transformation: bounded table lookup plus additional-unit extension. The pattern is not SNAP-specific; the law deciding that boarder count indexes the table must remain in the atomic corpus. |
| `other_countable_self_employment_earned_income` | C | Encode the missing self-employment subtotal in the appropriate federal or Colorado SNAP source module with provenance. The concept registry already names the component concepts, but the inclusion rule is law, not composer logic. |
| `shelter_costs` | C | Encode the allowable shelter-cost subtotal in atomic law. The registry already has `snap_total_allowable_shelter_expenses` as producer-missing; the formula belongs under the shelter-cost source authority, not in composer Python. |
| `total_household_resources_before_exclusions` | C | Encode the pre-exclusion resource subtotal in atomic law, likely in the resource module that already consumes it. The category selection is legal content. |
| `snap_student_eligible` | C | Encode household-level student eligibility aggregation in atomic law. The composer must not decide how member eligibility rolls up. |
| `snap_ssn_eligible` | C | Encode household-level SSN eligibility aggregation in atomic law. The composer must not decide the household roll-up rule. |
| `snap_residency_citizenship_eligible` | C | Encode residency and citizenship/alien-status roll-up in atomic law. This is eligibility law, not a generic composer decision. |
| `snap_work_requirement_eligible` | C | Encode household-level work-requirement roll-up in atomic law. The positive and negative member tests are legal content. |
| `snap_eligible` | C | Encode the final eligibility conjunction in atomic law. A generic `all_of` pattern exists, but the selected eligibility gates are program law and should be sourced. |

## Result

No rule lands in E. The strict architecture remains viable for this checkout,
but CO SNAP is not ready to migrate solely by composer changes: the C entries
must be moved into source-provenance RuleSpec modules first, or explicitly
resolved through the concept registry where the corpus drift work proves an
existing atomic producer.
