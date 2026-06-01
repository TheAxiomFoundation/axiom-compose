"""Generic RuleSpec transformation patterns.

Patterns in this module must be program-agnostic. They may accept declarative
parameters, but they must not name or branch on a benefit, tax, jurisdiction,
or program family.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class TransformationError(ValueError):
    """Raised when a transformation invocation is invalid."""


Rule = dict[str, Any]
Builder = Callable[[Mapping[str, Any]], Rule]


def build_transformation(pattern: str, parameters: Mapping[str, Any]) -> Rule:
    """Build one RuleSpec rule from a named generic pattern."""

    try:
        builder = PATTERNS[pattern]
    except KeyError as exc:
        available = ", ".join(sorted(PATTERNS))
        raise TransformationError(
            f"unknown transformation pattern {pattern!r}; available: {available}"
        ) from exc
    return builder(parameters)


def sum_terms(parameters: Mapping[str, Any]) -> Rule:
    """Create a derived rule that sums declaratively listed terms.

    This is a generic aggregation pattern: taxes, transfers, health programs,
    and eligibility systems all need law-encoded subtotal concepts assembled
    from law-encoded components.
    """

    name = _identifier(parameters, "name")
    terms = _identifiers(parameters, "terms")
    formula = " + ".join(terms) if terms else "0"
    return _base_rule(parameters, name=name, formula=formula)


def all_of(parameters: Mapping[str, Any]) -> Rule:
    """Create a derived judgment requiring every listed condition."""

    name = _identifier(parameters, "name")
    conditions = _identifiers(parameters, "conditions")
    formula = " and ".join(conditions) if conditions else "true"
    return _base_rule(parameters, name=name, dtype="Judgment", formula=formula)


def any_of(parameters: Mapping[str, Any]) -> Rule:
    """Create a derived judgment satisfied by any listed condition."""

    name = _identifier(parameters, "name")
    conditions = _identifiers(parameters, "conditions")
    formula = " or ".join(conditions) if conditions else "false"
    return _base_rule(parameters, name=name, dtype="Judgment", formula=formula)


def any_related(parameters: Mapping[str, Any]) -> Rule:
    """Create a judgment that any related entity satisfies a condition."""

    name = _identifier(parameters, "name")
    relation = _identifier(parameters, "relation")
    condition = _identifier(parameters, "condition")
    formula = f"count_where({relation}, {condition}) > 0"
    return _base_rule(parameters, name=name, dtype="Judgment", formula=formula)


def conditional_value(parameters: Mapping[str, Any]) -> Rule:
    """Create a derived value selected by a judgment condition."""

    name = _identifier(parameters, "name")
    condition = _identifier(parameters, "condition")
    when_true = _identifier(parameters, "when_true")
    when_false = parameters.get("when_false", 0)
    if isinstance(when_false, str):
        else_formula = _identifier({"when_false": when_false}, "when_false")
    elif isinstance(when_false, int | float):
        else_formula = str(when_false)
    else:
        raise TransformationError("when_false must be an identifier or number")
    formula = f"if {condition}:\n    {when_true}\nelse:\n    {else_formula}"
    return _base_rule(parameters, name=name, formula=formula)


def derived_relation(parameters: Mapping[str, Any]) -> Rule:
    """Create a derived_relation rule (filtered membership over a source relation).

    The runtime semantics live in axiom-rules-engine: given a source relation
    (e.g. member_of_household) and a per-member judgment, build a filtered
    relation that downstream rules can scope to via `entity:`. The aggregations
    `len`, `sum`, `count_where`, `sum_where` operate over filtered membership.

    This pattern is program-agnostic: it accepts the source relation, the
    filtered entity name, and the membership predicate as declarative
    parameters. Use cases: SnapUnit filtered from member_of_household by a
    SNAP eligibility predicate; qualifying-child set filtered from
    dependents by a CTC predicate; MAGI household; etc.
    """

    name = _identifier(parameters, "name")
    arity = _positive_int(parameters, "arity")
    # source_relation may be a bare identifier (e.g. `member_of_household`) or
    # a fully-qualified target (e.g. `us:statutes/7/2012/j#relation.member_of_household`);
    # axiom-rules-engine resolves both forms — we accept either here.
    source_relation = _string(parameters, "source_relation")
    entity = _string(parameters, "entity")
    member_relation = _identifier(parameters, "member_relation")
    predicate = _identifier(parameters, "predicate")
    slot_entities = _strings(parameters, "slot_entities")

    effective_from = _string(parameters, "effective_from")
    source = _string(
        parameters,
        "source",
        default=f"axiom-compose:{parameters.get('pattern', 'derived_relation')}",
    )

    rule: Rule = {
        "name": name,
        "kind": "derived_relation",
        "derived_relation": {
            "arity": arity,
            "source_relation": source_relation,
            "entity": entity,
            "member_relation": member_relation,
            "slot_entities": list(slot_entities),
        },
        "source": source,
        "versions": [
            {
                "effective_from": effective_from,
                "formula": predicate,
            }
        ],
    }
    return rule


def table_lookup_with_extension(parameters: Mapping[str, Any]) -> Rule:
    """Create a table lookup with a repeated additional-member extension."""

    name = _identifier(parameters, "name")
    index = _identifier(parameters, "index")
    table = _identifier(parameters, "table")
    extension = _identifier(parameters, "extension")
    minimum_index = _positive_int(parameters, "minimum_index")
    maximum_index = _positive_int(parameters, "maximum_index")
    if maximum_index < minimum_index:
        raise TransformationError("maximum_index must be >= minimum_index")
    formula = (
        f"if {index} <= 0:\n"
        "    0\n"
        "else:\n"
        f"    {table}[max(min({index}, {maximum_index}), {minimum_index})]\n"
        f"    + (max({index} - {maximum_index}, 0) * {extension})"
    )
    return _base_rule(parameters, name=name, formula=formula)


PATTERNS: dict[str, Builder] = {
    "all_of": all_of,
    "any_of": any_of,
    "any_related": any_related,
    "conditional_value": conditional_value,
    "derived_relation": derived_relation,
    "sum_terms": sum_terms,
    "table_lookup_with_extension": table_lookup_with_extension,
}


def _base_rule(
    parameters: Mapping[str, Any],
    *,
    name: str,
    formula: str,
    dtype: str | None = None,
) -> Rule:
    effective_from = _string(parameters, "effective_from")
    source = _string(
        parameters,
        "source",
        default=f"axiom-compose:{parameters.get('pattern', 'transformation')}",
    )
    rule: Rule = {
        "name": name,
        "kind": "derived",
        "entity": _string(parameters, "entity", default="Household"),
        "dtype": dtype or _string(parameters, "dtype", default="Money"),
        "period": _string(parameters, "period", default="Month"),
        "source": source,
        "versions": [
            {
                "effective_from": effective_from,
                "formula": formula,
            }
        ],
    }
    unit = parameters.get("unit")
    if unit is not None:
        rule["unit"] = _string(parameters, "unit")
    return rule


def _string(
    parameters: Mapping[str, Any], key: str, *, default: str | None = None
) -> str:
    value = parameters.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise TransformationError(f"{key} must be a non-empty string")
    return value.strip()


def _identifier(parameters: Mapping[str, Any], key: str) -> str:
    value = _string(parameters, key)
    if not value.replace("_", "a").isalnum() or not value[0].islower():
        raise TransformationError(f"{key} must be a RuleSpec identifier")
    return value


def _identifiers(parameters: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = parameters.get(key)
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise TransformationError(f"{key} must be a list of identifiers")
    return tuple(_identifier({key: item}, key) for item in value)


def _strings(parameters: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """List of non-empty strings (e.g. slot_entities = [Person, Household])."""

    value = parameters.get(key)
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise TransformationError(f"{key} must be a list of strings")
    return tuple(_string({key: item}, key) for item in value)


def _positive_int(parameters: Mapping[str, Any], key: str) -> int:
    value = parameters.get(key)
    if not isinstance(value, int) or value <= 0:
        raise TransformationError(f"{key} must be a positive integer")
    return value
