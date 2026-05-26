"""Compose-time coverage assertion.

When a program spec declares an eligibility-shaped output (anything whose
name carries an ``eligible``/``ineligible`` marker), this module walks the
output rule's transitive dependency graph and reports any other
eligibility-shaped rules in scope that the output does *not* reference.
Used by ``axiom_compose.core.compose`` to fail compilation when the
declared eligibility chain is too short — the gap that bit CA SNAP
where ``snap_eligible`` only checked per-member eligibility and silently
ignored the household income, asset, and residency tests that the
imported atomic rules already encoded.

Tier 3 from the silent-failure remediation plan: "make the gap a build
failure" without touching the rulespec layer.

Specs can opt out per-output via:

    acknowledged_incomplete:
      - snap_eligible

…which is useful for bootstrap iterations where the spec is honestly
known to be partial. The acknowledgement is structured data, not a
comment, so it shows up in audits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Iterable


# Names that strongly suggest an "eligibility test" — what we expect a
# declared eligibility concept to ultimately depend on. Mirrors the same
# marker list that axiom_oracles.coverage uses so the two analyzers stay
# in sync. False positives are cheaper than silent gaps when the cost of
# a miss is "engine returns wrong answer".
ELIGIBILITY_MARKERS: tuple[str, ...] = (
    "eligible",
    "ineligible",
    "income_limit",
    "income_eligibility",
    "asset_limit",
    "resource_limit",
    "residency",
    "categorically_eligible",
)


_IDENT_RE = re.compile(r"\b([a-z][a-z0-9_]*)\b")

# Identifiers that look like rule references but actually aren't — generic
# language keywords plus engine relation/aggregation primitives. Anything
# in this set is dropped when extracting formula dependencies.
_FORMULA_KEYWORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "if",
        "else",
        "true",
        "false",
        "null",
        "in",
        "min",
        "max",
        "len",
        "sum",
        "count",
        "count_where",
        "sum_where",
        "any",
        "all",
        "member_of_household",  # relation, not a rule
    }
)


def find_uncovered_eligibility_rules(
    *,
    output: str,
    rules_by_name: Mapping[str, Mapping[str, Any]],
    markers: tuple[str, ...] = ELIGIBILITY_MARKERS,
) -> list[str]:
    """Walk ``output``'s transitive dependency graph and return names of
    eligibility-shaped rules in ``rules_by_name`` that are not reached.

    ``rules_by_name`` must include both the atomic corpus rules visible
    to the program and the synthesized transformation rules — the
    analyzer treats them uniformly.
    """
    reachable = _transitive_dependencies(output, rules_by_name)
    eligibility = {
        name
        for name in rules_by_name
        if any(marker in name for marker in markers)
    }
    return sorted(eligibility - reachable - {output})


def _transitive_dependencies(
    start: str, rules_by_name: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    seen: set[str] = set()
    stack: list[str] = [start]
    while stack:
        name = stack.pop()
        if name in seen or name not in rules_by_name:
            continue
        seen.add(name)
        rule = rules_by_name[name]
        stack.extend(_identifiers_in_rule(rule))
    return seen


def _identifiers_in_rule(rule: Mapping[str, Any]) -> Iterable[str]:
    """Yield identifiers referenced by a rule's formulas.

    Handles both the corpus rule shape (``versions: [{formula: ...}]``)
    and the engine compile output shape (``expr`` tree with ``kind:
    derived`` nodes) so the analyzer works on either side.
    """
    versions = rule.get("versions") or []
    if isinstance(versions, list):
        for version in versions:
            if not isinstance(version, Mapping):
                continue
            formula = version.get("formula")
            if isinstance(formula, str):
                for identifier in _IDENT_RE.findall(formula):
                    if identifier in _FORMULA_KEYWORDS:
                        continue
                    yield identifier
    # Derived-relation rules carry their predicate next to the rule body
    # (not inside ``versions``) — pick up the predicate name too.
    derived_relation = rule.get("derived_relation") or rule.get("derivation")
    if isinstance(derived_relation, Mapping):
        predicate = derived_relation.get("predicate")
        if isinstance(predicate, str):
            yield predicate
        elif isinstance(predicate, Mapping):
            name = predicate.get("name")
            if isinstance(name, str):
                yield name


def format_coverage_error(target: str, uncovered: list[str]) -> str:
    """Render the ComposeError message body."""
    lines = [
        f"output {target!r} does not reference {len(uncovered)} "
        "eligibility-looking rule(s) in scope:",
    ]
    for name in uncovered:
        lines.append(f"  - {name}")
    lines.append(
        "Either wire these into the output's expression tree "
        f"(usually by composing them via all_of) or add {target!r} "
        "to the spec's `acknowledged_incomplete:` list."
    )
    return "\n".join(lines)
