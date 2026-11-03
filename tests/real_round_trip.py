"""Shared harness for the real-corpus engine round-trip tests (#3).

The round trip composes a real program spec, compiles the emitted YAML with
axiom-rules-engine's `compile-composed`, and runs a fixture population
through it. The comparison path is the one that works without axiom-compose:
compiling the program's atomic RuleSpec module with `compile` and running the
same population. The fixture population is the module's own `.test.yaml`
cases from the rulespec checkout, so the expected values in those files come
from the corpus, which cites the law for each one.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

# Known corpus/engine mismatches that block a compile today. Each entry maps
# engine stderr fragments, all of which must appear, to the reason the test
# cites when it xfails. A fixed corpus turns the xfail into a real run, and
# any other failure, including the same error from another module, fails.
KNOWN_COMPILE_BLOCKERS = (
    (
        (
            # The engine names the module by its id (`us:...`), not its path.
            "RuleSpec module `us:policies/irs/rev-proc-2025-32/earned-income-credit`",
            "declares removed plural `corpus_citation_paths`",
        ),
        "us:policies/irs/rev-proc-2025-32/earned-income-credit uses the "
        "plural `corpus_citation_paths` key that the engine removed "
        "(rulespec-us#1312); rulespec-us lists it in "
        "tools/known-engine-load-failures.txt",
    ),
)


def known_blocker(stderr: str) -> str | None:
    """The reason for a known compile blocker in ``stderr``, if any."""

    for fragments, reason in KNOWN_COMPILE_BLOCKERS:
        if all(fragment in stderr for fragment in fragments):
            return reason
    return None


def external_path(env_var: str) -> Path:
    raw = os.environ.get(env_var)
    if not raw:
        pytest.skip(f"{env_var} is not set")
    path = Path(raw).resolve()
    if not path.exists():
        raise AssertionError(f"{env_var} points to missing path: {path}")
    return path


def run_engine(
    engine_root: Path, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["cargo", "run", "--quiet", "--", *args],
        cwd=engine_root,
        env=dict(os.environ),
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )


def compile_or_xfail(
    engine_root: Path,
    command: str,
    program: Path,
    rulespec_root: Path,
    output: Path,
) -> dict[str, Any]:
    completed = run_engine(
        engine_root,
        command,
        "--program",
        str(program),
        "--rulespec-root",
        str(rulespec_root),
        "--output",
        str(output),
    )
    if completed.returncode:
        reason = known_blocker(completed.stderr)
        if reason is not None:
            pytest.xfail(f"{command}: {reason}")
        raise AssertionError(f"{command} failed:\n{completed.stderr[-2000:]}")
    return json.loads(output.read_text())


def run_compiled(
    engine_root: Path, artifact: Path, request: Mapping[str, Any]
) -> list[dict[str, Any]]:
    completed = run_engine(
        engine_root,
        "run-compiled",
        "--artifact",
        str(artifact),
        stdin=json.dumps(request),
    )
    if completed.returncode:
        raise AssertionError(f"run-compiled failed:\n{completed.stderr[-2000:]}")
    return json.loads(completed.stdout)["results"]


@dataclass(frozen=True)
class Case:
    name: str
    period: Mapping[str, str]
    inputs: Mapping[str, Any]
    expected: Mapping[str, Any]


def load_cases(path: Path) -> list[Case]:
    return [
        Case(
            name=raw["name"],
            period=raw["period"],
            inputs=raw.get("input") or {},
            expected=raw.get("output") or {},
        )
        for raw in yaml.safe_load(path.read_text())
    ]


def _interval(period: Mapping[str, str]) -> dict[str, str]:
    # `.test.yaml` periods name the last day; engine intervals are half-open.
    end = datetime.date.fromisoformat(str(period["end"])) + datetime.timedelta(1)
    return {"start": str(period["start"]), "end": end.isoformat()}


def _value(raw: Any, dtype: str | None) -> dict[str, Any]:
    if isinstance(raw, bool):
        return {"kind": "bool", "value": raw}
    if isinstance(raw, int) and dtype == "integer":
        return {"kind": "integer", "value": raw}
    if isinstance(raw, int | float):
        return {"kind": "decimal", "value": str(raw)}
    if isinstance(raw, str):
        return {"kind": "text", "value": raw}
    raise AssertionError(f"unsupported fixture value {raw!r}")


class Catalog:
    """Input and relation shapes from a compiled artifact's interface."""

    def __init__(self, artifact: Mapping[str, Any]):
        interface = artifact["program"]["interface"]
        self.inputs = {item["legal_id"]: item for item in interface["inputs"]}
        self.relations = {item["name"]: item for item in interface["relations"]}

    def input_entity(self, legal_id: str) -> str:
        entities = self.inputs[legal_id]["entities"]
        assert len(entities) == 1, (legal_id, entities)
        return entities[0]

    def input_dtype(self, legal_id: str) -> str | None:
        return self.inputs[legal_id].get("dtype")

    def relation_slots(self, legal_id: str) -> list[str]:
        # `us:statutes/26/32#relation.qualifying_child_of_tax_unit` names the
        # relation by its canonical id; the interface may list it by that id
        # or by its short name.
        short = legal_id.rsplit("#relation.", 1)[-1]
        relation = self.relations.get(legal_id) or self.relations[short]
        return list(relation["slot_entities"])

    def relation_name(self, legal_id: str) -> str:
        if legal_id in self.relations:
            return legal_id
        return legal_id.rsplit("#relation.", 1)[-1]


def build_request(
    cases: Iterable[Case], catalog: Catalog, outputs: Iterable[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """One request holding every case as its own entity graph.

    Returns the request and a map from each case's query entity id to the
    case name. A case's flat inputs belong to one root entity; a
    `#relation.<name>` input lists the related members, each with its own
    inputs, and becomes one tuple per member in the relation's slot order.
    """

    inputs: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    case_by_entity: dict[str, str] = {}
    wanted = list(outputs)
    for index, case in enumerate(cases):
        interval = _interval(case.period)
        flat = {k: v for k, v in case.inputs.items() if "#relation." not in k}
        kinds = {catalog.input_entity(name) for name in flat}
        assert len(kinds) <= 1, (case.name, kinds)
        if not kinds:
            continue
        root_kind = kinds.pop()
        root_id = f"{root_kind}:case{index}"
        for name, raw in flat.items():
            inputs.append(
                {
                    "name": name,
                    "entity": root_kind,
                    "entity_id": root_id,
                    "interval": interval,
                    "value": _value(raw, catalog.input_dtype(name)),
                }
            )
        for name, members in case.inputs.items():
            if "#relation." not in name:
                continue
            slots = catalog.relation_slots(name)
            assert len(slots) == 2 and root_kind in slots, (name, slots)
            member_kind = slots[1 - slots.index(root_kind)]
            for position, member in enumerate(members):
                member_id = f"{member_kind}:case{index}-{position}"
                for member_input, raw in member.items():
                    inputs.append(
                        {
                            "name": member_input,
                            "entity": member_kind,
                            "entity_id": member_id,
                            "interval": interval,
                            "value": _value(raw, catalog.input_dtype(member_input)),
                        }
                    )
                tuple_ids = [
                    root_id if kind == root_kind else member_id for kind in slots
                ]
                relations.append(
                    {
                        "name": catalog.relation_name(name),
                        "tuple": tuple_ids,
                        "interval": interval,
                    }
                )
        queries.append(
            {
                "entity_id": root_id,
                "period": {**case.period, "end": interval["end"]},
                "outputs": wanted,
            }
        )
        case_by_entity[root_id] = case.name
    request = {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": relations},
        "queries": queries,
    }
    return request, case_by_entity


def output_value(output: Mapping[str, Any]) -> Any:
    """The comparable value of one engine output."""

    if output.get("kind") == "judgment":
        return output["outcome"]
    value = output.get("value")
    if isinstance(value, Mapping):
        return value.get("value")
    return value


def outcomes(
    results: Iterable[Mapping[str, Any]], case_by_entity: Mapping[str, str]
) -> dict[str, dict[str, Any]]:
    return {
        case_by_entity[result["entity_id"]]: {
            name: output_value(output) for name, output in result["outputs"].items()
        }
        for result in results
    }


def numeric_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, int | float):
        try:
            return abs(float(actual) - float(expected)) < 1e-9
        except TypeError, ValueError:
            return False
    return actual == expected
