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


def _relation_module(target: str, data_relation: dict) -> RuleSpecModule:
    return RuleSpecModule(
        target=target,
        payload={
            "format": "rulespec/v1",
            "rules": [
                {
                    "name": "member_of_household",
                    "kind": "data_relation",
                    "data_relation": data_relation,
                }
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


def _compose_declarations(shapes: dict[str, dict], order: list[str]):
    """Compose one module per short name in ``shapes``, scoped in ``order``."""

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


def _outcome(shapes: dict[str, dict], order: list[str]):
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


NAMESAKE_SHAPES = (
    {"arity": 2, "arguments": ["Person", "Household"]},
    {"arity": 2, "arguments": ["Household", "Person"]},
    {"arity": 2},
    {"arity": 3},
    {"arity": 3, "arguments": ["Person", "Household", "TaxUnit"]},
)


COMPATIBLE_SHAPES = (
    (NAMESAKE_SHAPES[0], NAMESAKE_SHAPES[2]),
    (NAMESAKE_SHAPES[3], NAMESAKE_SHAPES[4]),
)


def _refuses_as_pair(first: dict, second: dict) -> bool:
    first_kinds = first.get("arguments", [])
    second_kinds = second.get("arguments", [])
    return first["arity"] != second["arity"] or (
        bool(first_kinds and second_kinds) and first_kinds != second_kinds
    )


@pytest.mark.parametrize("seed", range(150))
def test_property_namesake_outcome_is_a_function_of_the_declaration_set(seed):
    # For three or four same-named declarations, composed in every import
    # order:
    # - refuse iff some pair of declarations refuses as a pair;
    # - otherwise compose, with exactly one ComposeWarning per untyped
    #   declaration when any declaration is typed, and none otherwise;
    # - the refusal message, or the set of warning messages, is the same for
    #   every order, and the composed imports follow the scope order.
    rng = random.Random(seed)
    count = 3 + seed % 2
    # Two seeds in three draw from one arity's typed and untyped shape, which
    # never refuse, so warn and pass cases are common, not just refusals.
    pool = COMPATIBLE_SHAPES[seed % 2] if seed % 3 else NAMESAKE_SHAPES
    shapes = {f"m{index}": dict(rng.choice(pool)) for index in range(count)}
    declared = list(shapes.values())
    refuses = any(
        _refuses_as_pair(first, second)
        for first, second in itertools.combinations(declared, 2)
    )
    untyped = sorted(name for name, shape in shapes.items() if "arguments" not in shape)
    any_typed = len(untyped) < count

    results = set()
    for order in itertools.permutations(sorted(shapes)):
        result = _outcome(shapes, list(order))
        if refuses:
            assert result[0] == "refuse", (shapes, order)
            results.add(result)
            continue
        assert result[0] == "compose", (shapes, order, result)
        assert result[1] == [f"us:{name}" for name in order]
        expected_warned = untyped if any_typed else []
        assert len(result[2]) == len(expected_warned), (shapes, order, result[2])
        for name, message in zip(
            expected_warned,
            sorted(result[2], key=lambda m: m.rsplit(" in ", 1)[-1]),
            strict=True,
        ):
            assert message.endswith(f"in us:{name} with the same `arguments`")
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
