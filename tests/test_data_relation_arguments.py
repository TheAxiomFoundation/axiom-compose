"""Typed data relations (#33).

axiom-rules-engine refuses to execute an aggregation over a relation that
declares no entity kind per tuple slot (`untyped_relation`). These tests
cover the `data_relation` pattern's `arguments`, the composition-level
requirement that an aggregated relation be typed, the duplicate-relation
check across imported modules, and a hermetic compile-and-run through the
engine (skipped without AXIOM_RULES_ENGINE_ROOT).
"""

import itertools
import json
import os
import random
import string
import subprocess
import warnings
from pathlib import Path

import pytest
import yaml

from axiom_compose import (
    CorpusState,
    RuleSpecModule,
    compose,
    load_corpus_from_roots,
)
from axiom_compose.core import ComposeError, ComposeWarning
from axiom_compose.spec import ProgramSpec
from axiom_compose.transformations import TransformationError, build_transformation


def _relation(**parameters):
    return build_transformation(
        "data_relation", {"pattern": "data_relation", **parameters}
    )


# --- the pattern -------------------------------------------------------------


def test_data_relation_emits_arguments_in_spec_order():
    rule = _relation(
        name="member_of_household", arity=2, arguments=["Person", "Household"]
    )

    assert rule == {
        "name": "member_of_household",
        "kind": "data_relation",
        "data_relation": {"arity": 2, "arguments": ["Person", "Household"]},
        "source": "axiom-compose:data_relation",
    }


def test_data_relation_keeps_container_first_order():
    rule = _relation(name="tax_unit_member", arity=2, arguments=["TaxUnit", "Person"])

    assert rule["data_relation"]["arguments"] == ["TaxUnit", "Person"]


def test_data_relation_without_arguments_stays_arity_only():
    rule = _relation(name="member_of_household", arity=2)

    assert rule["data_relation"] == {"arity": 2}


@pytest.mark.parametrize(
    "arguments",
    [
        ["Person"],
        ["Person", "Household", "TaxUnit"],
    ],
)
def test_data_relation_refuses_argument_count_that_differs_from_arity(arguments):
    with pytest.raises(TransformationError, match="arity 2 but"):
        _relation(name="member_of_household", arity=2, arguments=arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        "Person",
        ["person", "Household"],
        ["Person", "tax_unit"],
        ["Person", "Tax Unit"],
        ["Person", " Household"],
        ["Person\n", "Household"],
        ["Person", "Household\n"],
        ["Person", 3],
        ["Person", {"entity": "Household"}],
    ],
)
def test_data_relation_refuses_non_entity_kind_arguments(arguments):
    with pytest.raises(TransformationError, match="arguments"):
        _relation(name="member_of_household", arity=2, arguments=arguments)


def _random_kind(rng: random.Random) -> str:
    rest = rng.choices(string.ascii_letters + string.digits, k=rng.randint(0, 8))
    return rng.choice(string.ascii_uppercase) + "".join(rest)


@pytest.mark.parametrize("seed", range(200))
def test_property_arguments_keep_order_and_count_mismatch_refuses(seed):
    # Seeded so a failure reproduces from its parameter id.
    rng = random.Random(seed)
    arity = rng.randint(1, 5)
    count = rng.randint(1, 6)
    arguments = [_random_kind(rng) for _ in range(count)]

    if count == arity:
        rule = _relation(name="some_relation", arity=arity, arguments=arguments)
        assert rule["data_relation"] == {"arity": arity, "arguments": arguments}
    else:
        with pytest.raises(TransformationError, match=f"arity {arity} but"):
            _relation(name="some_relation", arity=arity, arguments=arguments)


# --- composition-level requirement ---------------------------------------------


def _spec(relation: dict, *readers: dict) -> ProgramSpec:
    return ProgramSpec.from_mapping(
        {
            "program": "us/example",
            "period": "2026-01",
            "outputs": [reader["name"] for reader in readers],
            # Module-less corpus: scope needs an entry but is not resolved.
            "scope": {"federal": ["policies/elderly"]},
            "transformations": [
                {"pattern": "data_relation", **relation},
                *readers,
            ],
        }
    )


ANY_ELDERLY = {
    "pattern": "any_related",
    "name": "has_elderly_member",
    "relation": "member_of_household",
    "condition": "is_elderly",
    "effective_from": "2026-01-01",
}


def test_compose_refuses_untyped_relation_aggregated_by_composed_rule():
    spec = _spec({"name": "member_of_household", "arity": 2}, ANY_ELDERLY)

    with pytest.raises(ComposeError, match="must declare `arguments`") as error:
        compose(spec, CorpusState())
    assert "member_of_household (read by has_elderly_member)" in str(error.value)


def test_compose_refuses_untyped_relation_filtered_by_derived_relation():
    spec = _spec(
        {"name": "member_of_household", "arity": 2},
        {
            "pattern": "derived_relation",
            "name": "snap_unit_member",
            "arity": 2,
            "source_relation": "member_of_household",
            "entity": "SnapUnit",
            "member_relation": "member_of_snap_unit",
            "predicate": "snap_member_eligible",
            "slot_entities": ["Person", "Household"],
            "effective_from": "2026-01-01",
        },
    )

    with pytest.raises(ComposeError, match="read by snap_unit_member"):
        compose(spec, CorpusState())


def test_compose_accepts_typed_relation_aggregated_by_composed_rule():
    spec = _spec(
        {
            "name": "member_of_household",
            "arity": 2,
            "arguments": ["Person", "Household"],
        },
        ANY_ELDERLY,
    )

    program = compose(spec, CorpusState())

    relation = program.payload["rules"][0]
    assert relation["data_relation"] == {
        "arity": 2,
        "arguments": ["Person", "Household"],
    }


def test_compose_accepts_untyped_relation_no_composed_rule_aggregates():
    # Arity-only declarations stay legal when nothing reads them (e.g. the
    # rulespec-us TANF specs that declare relations they do not yet use).
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/example",
            "period": "2026-01",
            "outputs": ["member_of_household"],
            "scope": {"federal": ["policies/elderly"]},
            "transformations": [
                {"pattern": "data_relation", "name": "member_of_household", "arity": 2}
            ],
        }
    )

    program = compose(spec, CorpusState())

    assert program.payload["rules"][0]["data_relation"] == {"arity": 2}


# --- duplicate declarations across imported modules ----------------------------


def _relation_module(target: str, data_relation: dict | list) -> RuleSpecModule:
    """A module declaring member_of_household once per entry, in order."""

    declared = data_relation if isinstance(data_relation, list) else [data_relation]
    return RuleSpecModule(
        target=target,
        payload={
            "format": "rulespec/v1",
            "rules": [
                {
                    "name": "member_of_household",
                    "kind": "data_relation",
                    "data_relation": body,
                }
                for body in declared
            ],
        },
    )


def _compose_two_declarations(first: dict, second: dict):
    modules = {
        "us:a": _relation_module("us:a", first),
        "us:b": _relation_module("us:b", second),
    }
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/example",
            "period": "2026-01",
            "outputs": ["member_of_household"],
            "scope": {"federal": ["a", "b"]},
        }
    )
    return compose(spec, CorpusState(modules=modules))


@pytest.mark.filterwarnings("error::axiom_compose.core.ComposeWarning")
def test_identical_imported_relations_stay_exempt():
    typed = {"arity": 2, "arguments": ["Person", "Household"]}

    program = _compose_two_declarations(typed, dict(typed))

    assert program.payload["imports"] == ["us:a", "us:b"]


@pytest.mark.filterwarnings("error::axiom_compose.core.ComposeWarning")
def test_identical_untyped_imported_relations_stay_exempt():
    program = _compose_two_declarations({"arity": 2}, {"arity": 2})

    assert program.payload["imports"] == ["us:a", "us:b"]


@pytest.mark.filterwarnings("error::axiom_compose.core.ComposeWarning")
def test_object_form_arguments_match_compact_form():
    # The engine reads `{name, entity}` arguments as their entity; the
    # descriptive name does not change the relation's type.
    program = _compose_two_declarations(
        {"arity": 2, "arguments": ["Person", "Household"]},
        {
            "arity": 2,
            "arguments": [
                {"name": "member", "entity": "Person"},
                {"name": "household", "entity": "Household"},
            ],
        },
    )

    assert program.payload["imports"] == ["us:a", "us:b"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (
            {"arity": 2, "arguments": ["Person", "Household"]},
            {"arity": 2, "arguments": ["Household", "Person"]},
        ),
        (
            {"arity": 2, "arguments": ["Person", "Household"]},
            {"arity": 2, "arguments": ["Person", "TaxUnit"]},
        ),
        ({"arity": 2}, {"arity": 3}),
        ({"arity": 2}, {"arity": 3, "arguments": ["Person", "Household", "Year"]}),
    ],
)
def test_imported_relations_with_different_declarations_refuse(first, second):
    # Two different non-empty slot lists, or a different arity, cannot be one
    # shape for the name, so compose refuses. The refusal is compose's own:
    # namesakes from different modules get different engine relation ids
    # (`<module>#relation.<name>`), so the engine never merges them.
    for a, b in ((first, second), (second, first)):
        with pytest.raises(ComposeError, match="declared differently") as error:
            _compose_two_declarations(a, b)
        assert "us:a" in str(error.value) and "us:b" in str(error.value)


@pytest.mark.parametrize("typed_first", [True, False])
def test_typed_beside_untyped_imported_relation_warns_and_composes(typed_first):
    # The corpus pairs an untyped us:statutes/7/2012/j#member_of_household
    # with typed state declarations. That warns until the corpus types it.
    typed = {"arity": 2, "arguments": ["Person", "Household"]}
    untyped = {"arity": 2}
    first, second = (typed, untyped) if typed_first else (untyped, typed)

    with pytest.warns(ComposeWarning, match="declared differently") as record:
        program = _compose_two_declarations(first, second)

    assert program.payload["imports"] == ["us:a", "us:b"]
    message = str(record[0].message)
    assert "us:a" in message and "us:b" in message
    assert "no arguments" in message and "[Person, Household]" in message


def _random_shape(rng: random.Random) -> dict:
    arity = rng.choice((2, 3))
    shape: dict = {"arity": arity}
    if rng.random() < 0.6:
        shape["arguments"] = [
            rng.choice(("Person", "Household", "TaxUnit")) for _ in range(arity)
        ]
    return shape


@pytest.mark.parametrize("seed", range(200))
def test_property_namesake_relations_refuse_warn_or_pass(seed):
    # For a pair of same-named imported data_relations, with the two shapes
    # assigned to the two modules either way round (scope order fixed):
    # - compose silently when arity and slot kinds are equal;
    # - warn when arity is equal and exactly one side is untyped;
    # - refuse otherwise (different arity, or two different typed lists).
    # Import order over three or more modules is covered below.
    rng = random.Random(seed)
    first, second = _random_shape(rng), _random_shape(rng)
    if rng.random() < 0.25:
        second = dict(first)
    same_arity = first["arity"] == second["arity"]
    kinds = (first.get("arguments", []), second.get("arguments", []))

    if same_arity and kinds[0] == kinds[1]:
        expected = "pass"
    elif same_arity and not (kinds[0] and kinds[1]):
        expected = "warn"
    else:
        expected = "refuse"

    for a, b in ((first, second), (second, first)):
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            try:
                _compose_two_declarations(a, b)
            except ComposeError:
                outcome = "refuse"
            else:
                warned = any(issubclass(w.category, ComposeWarning) for w in record)
                outcome = "warn" if warned else "pass"
        assert outcome == expected, (a, b)


def _compose_declarations(shapes: dict[str, dict | list], order: list[str]):
    """Compose one module per short name in ``shapes``, scoped in ``order``.

    A list value declares the relation once per entry in that module.
    """

    modules = {
        f"us:{short}": _relation_module(f"us:{short}", shape)
        for short, shape in shapes.items()
    }
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/example",
            "period": "2026-01",
            "outputs": ["member_of_household"],
            "scope": {"federal": list(order)},
        }
    )
    return compose(spec, CorpusState(modules=modules))


def _outcome(shapes: dict[str, dict | list], order: list[str]):
    """("refuse", message) or ("compose", imports, warning messages)."""

    with warnings.catch_warnings(record=True) as record:
        # "always": the default filter would drop repeated messages.
        warnings.simplefilter("always")
        try:
            program = _compose_declarations(shapes, order)
        except ComposeError as error:
            return ("refuse", str(error))
    messages = sorted(
        str(w.message) for w in record if issubclass(w.category, ComposeWarning)
    )
    return ("compose", program.payload["imports"], messages)


THREE_MODULES = {
    "a": {"arity": 2, "arguments": ["Person", "Household"]},
    "b": {"arity": 2},
    "c": {"arity": 2, "arguments": ["Household", "Person"]},
}


@pytest.mark.parametrize("order", [["a", "b", "c"], ["a", "c", "b"]])
def test_untyped_namesake_between_conflicting_typed_ones_still_refuses(order):
    # An untyped declaration imported between two conflicting typed ones
    # used to replace the first in scope and hide the conflict.
    with pytest.raises(ComposeError, match="declared differently") as error:
        _compose_declarations(THREE_MODULES, order)
    message = str(error.value)
    assert "us:a declares arity 2 with arguments [Person, Household]" in message
    assert "us:c declares arity 2 with arguments [Household, Person]" in message


def test_untyped_namesake_never_replaces_a_typed_one_in_scope():
    # The rule in scope keeps the slot kinds whatever the import order.
    from axiom_compose.core import _rules_in_scope

    typed = {"arity": 2, "arguments": ["Person", "Household"]}
    modules = {
        "us:a": _relation_module("us:a", typed),
        "us:b": _relation_module("us:b", {"arity": 2}),
    }
    for imports in (("us:a", "us:b"), ("us:b", "us:a")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ComposeWarning)
            scope = _rules_in_scope(imports, CorpusState(modules=modules), [])
        assert scope["member_of_household"]["data_relation"] == typed


@pytest.mark.parametrize("order", [["a", "b"], ["b", "a"]])
def test_a_module_that_declares_a_relation_twice_contributes_both(order):
    # us:a's typed declaration conflicts with us:b's. Keyed by module, the
    # later arity-only declaration in us:a hid it, and the rule left in
    # scope depended on import order.
    shapes = {
        "a": [{"arity": 2, "arguments": ["Person", "Household"]}, {"arity": 2}],
        "b": {"arity": 2, "arguments": ["Household", "Person"]},
    }
    assert _outcome(shapes, order) == (
        "refuse",
        "data relation 'member_of_household' is declared differently by two "
        "imported modules: us:a declares arity 2 with arguments [Person, "
        "Household] and us:b declares arity 2 with arguments [Household, Person]",
    )


@pytest.mark.parametrize("order", list(itertools.permutations("abc")))
def test_a_warning_cites_the_first_typed_declaration_in_target_order(order):
    typed = {"arity": 2, "arguments": ["Person", "Household"]}
    shapes = {"a": typed, "b": {"arity": 2}, "c": dict(typed)}

    assert _outcome(shapes, list(order))[2] == [
        "data relation 'member_of_household' is declared differently by two "
        "imported modules: us:a declares arity 2 with arguments [Person, "
        "Household] and us:b declares arity 2 with no arguments; type the "
        "declaration in us:b with the same `arguments`"
    ]


def _auto_gated(first: dict, second: dict, *, auto_gate: bool):
    from axiom_compose.spec import TransformationSpec

    spec = ProgramSpec(
        program="us/example",
        period="2026-01",
        outputs=("widget_eligible",),
        scope={"federal": ("a", "b")},
        transformations=(
            TransformationSpec(
                pattern="all_of",
                parameters={
                    "name": "widget_eligible",
                    "effective_from": "2026-01-01",
                    "entity": "Household",
                    "dtype": "Judgment",
                    "period": "Month",
                    "source": "us:a",
                    "conditions": ["widget_member_eligible"],
                },
            ),
        ),
        auto_gate_outputs=("widget_eligible",) if auto_gate else (),
    )
    module_a = _relation_module("us:a", first)
    module_a.payload["rules"].append(
        {
            "name": "widget_member_eligible",
            "kind": "derived",
            "versions": [{"formula": "true"}],
        }
    )
    modules = {"us:a": module_a, "us:b": _relation_module("us:b", second)}
    return compose(spec, CorpusState(modules=modules))


def test_auto_gated_compose_warns_once_per_untyped_declaration():
    # compose builds the rule scope twice when it auto-gates outputs; each
    # build used to check namesakes, so each warning was issued twice.
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        program = _auto_gated(
            {"arity": 2, "arguments": ["Person", "Household"]},
            {"arity": 2},
            auto_gate=True,
        )
    warned = [w for w in record if issubclass(w.category, ComposeWarning)]
    assert len(warned) == 1, [str(w.message) for w in warned]
    assert "type the declaration in us:b" in str(warned[0].message)
    assert program.payload["imports"] == ["us:a", "us:b"]


def test_auto_gated_compose_refuses_with_the_same_text():
    first = {"arity": 2, "arguments": ["Person", "Household"]}
    second = {"arity": 2, "arguments": ["Household", "Person"]}
    messages = []
    for auto_gate in (True, False):
        with pytest.raises(ComposeError, match="declared differently") as error:
            _auto_gated(first, second, auto_gate=auto_gate)
        messages.append(str(error.value))
    assert messages[0] == messages[1]


KINDS = ("Person", "Household", "TaxUnit")


def _declaration(rng: random.Random, arity: int, kinds: list[str] | None) -> dict:
    """A data_relation body in one of the forms the corpus uses.

    ``kinds=None`` gives an untyped declaration: ``arguments`` absent or
    ``[]``. Otherwise ``arguments`` is the compact list or the
    ``{name, entity}`` object form, which compare equal.
    """

    if kinds is None:
        return (
            {"arity": arity}
            if rng.random() < 0.5
            else {"arity": arity, "arguments": []}
        )
    if rng.random() < 0.5:
        return {"arity": arity, "arguments": list(kinds)}
    return {
        "arity": arity,
        "arguments": [
            {"name": f"slot{index}", "entity": kind} for index, kind in enumerate(kinds)
        ],
    }


def _normalized(body: dict) -> tuple[int, tuple[str, ...]]:
    return (
        body["arity"],
        tuple(
            item["entity"] if isinstance(item, dict) else item
            for item in body.get("arguments") or ()
        ),
    )


def _declaration_set(seed: int) -> dict[str, list[dict]]:
    """Two to five declarations over one to five modules (a module may hold
    several), in one of three modes by ``seed % 3``:

    - 0: any arity 1-3 and any kinds per declaration;
    - 1: one arity and one kinds list, typed or untyped, so the set never
      refuses and warn and pass cases are common;
    - 2: one arity and two kinds lists (the second reversed or redrawn), so
      typed declarations conflict with an untyped one anywhere in the order.

    Every typed declaration takes the compact or the ``{name, entity}``
    object form, and every untyped one omits ``arguments`` or gives ``[]``.
    """

    rng = random.Random(seed)
    count = rng.randint(2, 5)
    modules = rng.randint(1, count)
    owners = list(range(modules)) + [
        rng.randrange(modules) for _ in range(count - modules)
    ]
    rng.shuffle(owners)
    arity = rng.randint(1, 3)
    kinds = [rng.choice(KINDS) for _ in range(arity)]
    shapes: dict[str, list[dict]] = {f"m{index}": [] for index in range(modules)}
    other = list(reversed(kinds))
    if other == kinds:
        other = [rng.choice(KINDS) for _ in range(arity)]
    for owner in owners:
        chosen = kinds
        if seed % 3 == 0:
            arity = rng.randint(1, 3)
            chosen = [rng.choice(KINDS) for _ in range(arity)]
        elif seed % 3 == 2 and rng.random() < 0.5:
            chosen = other
        typed = rng.random() < 0.6
        shapes[f"m{owner}"].append(_declaration(rng, arity, chosen if typed else None))
    return shapes


def _orders(names: list[str], seed: int) -> list[tuple[str, ...]]:
    # Every order for up to four modules; for five, 24 orders including
    # sorted and reversed.
    if len(names) <= 4:
        return list(itertools.permutations(names))
    rng = random.Random(seed)
    sampled = {tuple(names), tuple(reversed(names))}
    while len(sampled) < 24:
        sampled.add(tuple(rng.sample(names, len(names))))
    return sorted(sampled)


def _format(shape: tuple[int, tuple[str, ...]]) -> str:
    arity, kinds = shape
    typed = f"arguments [{', '.join(kinds)}]" if kinds else "no arguments"
    return f"arity {arity} with {typed}"


@pytest.mark.parametrize("seed", range(300))
def test_property_namesake_outcome_is_a_function_of_the_declaration_set(seed):
    # For a set of same-named declarations, composed in every import order
    # (24 sampled orders for five modules):
    # - refuse iff some pair of declarations differs in arity or declares
    #   different non-empty arguments (compact and object form compare
    #   equal; absent and [] are both untyped);
    # - otherwise compose, issuing exactly one ComposeWarning per untyped
    #   declaration when any declaration is typed, and none otherwise; each
    #   cites the first typed declaration in (module target, position) order
    #   and names the untyped declaration's module;
    # - the rule left in scope is typed iff any declaration is typed;
    # - the composed imports follow the scope order.
    from axiom_compose.core import _rules_in_scope

    shapes = _declaration_set(seed)
    declared = [
        (f"us:{name}", position, _normalized(body))
        for name, bodies in sorted(shapes.items())
        for position, body in enumerate(bodies)
    ]
    refuses = any(
        first[2][0] != second[2][0]
        or (first[2][1] and second[2][1] and first[2][1] != second[2][1])
        for first, second in itertools.combinations(declared, 2)
    )
    typed = [item for item in declared if item[2][1]]
    expected_warnings = []
    if not refuses and typed:
        typed_target, _, typed_shape = typed[0]
        for target, _, shape in declared:
            if shape[1]:
                continue
            pair = sorted(
                [(target, shape), (typed_target, typed_shape)], key=lambda x: x[0]
            )
            expected_warnings.append(
                "data relation 'member_of_household' is declared differently by "
                f"two imported modules: {pair[0][0]} declares {_format(pair[0][1])} "
                f"and {pair[1][0]} declares {_format(pair[1][1])}; type the "
                f"declaration in {target} with the same `arguments`"
            )
    modules = {
        f"us:{name}": _relation_module(f"us:{name}", bodies)
        for name, bodies in shapes.items()
    }

    results = set()
    for order in _orders(sorted(shapes), seed):
        result = _outcome(shapes, list(order))
        if refuses:
            assert result[0] == "refuse", (shapes, order, result)
            results.add(result)
            continue
        assert result[0] == "compose", (shapes, order, result)
        assert result[1] == [f"us:{name}" for name in order]
        assert result[2] == sorted(expected_warnings), (shapes, order)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ComposeWarning)
            scope = _rules_in_scope(
                tuple(f"us:{name}" for name in order),
                CorpusState(modules=modules),
                [],
            )
        in_scope = _normalized(scope["member_of_household"]["data_relation"])
        assert bool(in_scope[1]) is bool(typed), (shapes, order, in_scope)
        if typed:
            assert in_scope[1] == typed[0][2][1]
        results.add(("compose", tuple(result[2])))
    assert len(results) == 1, results


# --- through the engine ---------------------------------------------------------


ELDERLY_MODULE = """\
format: rulespec/v1
rules:
  - name: age_threshold
    kind: parameter
    entity: Person
    dtype: Integer
    period: Month
    source: fixture
    versions:
      - effective_from: '2026-01-01'
        formula: '60'
  - name: is_elderly
    kind: derived
    entity: Person
    dtype: Judgment
    period: Month
    source: fixture
    versions:
      - effective_from: '2026-01-01'
        formula: age >= age_threshold
"""


def _engine(engine_root: Path, *args: str, stdin: str | None = None):
    return subprocess.run(
        ["cargo", "run", "--quiet", "--", *args],
        cwd=engine_root,
        env=dict(os.environ),
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )


def _write_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "rulespec-us"
    module_path = root / "us" / "policies" / "elderly.yaml"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(ELDERLY_MODULE)
    return root


def _household_request() -> dict:
    month = {"start": "2026-01-01", "end": "2026-02-01"}
    ages = {"person:1": 35, "person:2": 70, "person:3": 40}
    members = {"household:1": ["person:1", "person:2"], "household:2": ["person:3"]}
    return {
        "mode": "fast",
        "dataset": {
            "inputs": [
                {
                    "name": "us:policies/elderly#input.age",
                    "entity": "Person",
                    "entity_id": person,
                    "interval": month,
                    "value": {"kind": "integer", "value": age},
                }
                for person, age in ages.items()
            ],
            "relations": [
                {
                    "name": "member_of_household",
                    "tuple": [person, household],
                    "interval": month,
                }
                for household, people in members.items()
                for person in people
            ],
        },
        "queries": [
            {
                "entity_id": household,
                "period": {"period_kind": "month", **month},
                "outputs": ["has_elderly_member"],
            }
            for household in members
        ],
    }


def test_typed_root_relation_compiles_and_runs_through_engine(tmp_path):
    raw = os.environ.get("AXIOM_RULES_ENGINE_ROOT")
    if not raw:
        pytest.skip("AXIOM_RULES_ENGINE_ROOT is not set")
    engine_root = Path(raw)
    root = _write_corpus(tmp_path)
    corpus = load_corpus_from_roots([root], corpus_sha="hermetic")
    spec = ProgramSpec.from_mapping(
        {
            "program": "us/elderly-household",
            "period": "2026-01",
            "outputs": ["has_elderly_member"],
            "scope": {"federal": ["policies/elderly"]},
            "transformations": [
                {
                    "pattern": "data_relation",
                    "name": "member_of_household",
                    "arity": 2,
                    "arguments": ["Person", "Household"],
                },
                {**ANY_ELDERLY, "entity": "Household"},
            ],
        }
    )
    program_path = tmp_path / "composed.yaml"
    artifact_path = tmp_path / "composed.compiled.json"

    # Control: the same composition with the relation left untyped is the
    # shape compose now refuses; the engine refuses it too, with the
    # error #33 reports. Written by hand because compose will not emit it.
    untyped = compose(spec, corpus).payload
    untyped["rules"][0]["data_relation"] = {"arity": 2}
    untyped_path = tmp_path / "untyped.yaml"
    untyped_path.write_text(yaml.safe_dump(dict(untyped), sort_keys=False))
    refused = _engine(
        engine_root,
        "compile-composed",
        "--program",
        str(untyped_path),
        "--rulespec-root",
        str(root),
        "--output",
        str(tmp_path / "untyped.compiled.json"),
    )
    assert refused.returncode != 0
    assert "[untyped_relation]" in refused.stderr

    program_path.write_bytes(compose(spec, corpus).source)
    compiled = _engine(
        engine_root,
        "compile-composed",
        "--program",
        str(program_path),
        "--rulespec-root",
        str(root),
        "--output",
        str(artifact_path),
    )
    assert compiled.returncode == 0, compiled.stderr[-2000:]
    assert "untyped_relation" not in compiled.stderr
    artifact = json.loads(artifact_path.read_text())
    assert {
        "name": "member_of_household",
        "arity": 2,
        "slot_entities": ["Person", "Household"],
    } in artifact["program"]["relations"]

    ran = _engine(
        engine_root,
        "run-compiled",
        "--artifact",
        str(artifact_path),
        stdin=json.dumps(_household_request()),
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    outcomes = {
        result["entity_id"]: result["outputs"]["has_elderly_member"]["outcome"]
        for result in json.loads(ran.stdout)["results"]
    }
    # Expected values follow from the fixture: household:1 has a member
    # aged 70 (>= 60); household:2's only member is 40.
    assert outcomes == {"household:1": "holds", "household:2": "not_holds"}
