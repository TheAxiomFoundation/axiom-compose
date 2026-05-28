"""Pure composition core."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml

from .spec import ProgramSpec
from .transformations import build_transformation


class ComposeError(ValueError):
    """Raised when a composition request cannot be satisfied."""


class ConceptRegistryLike(Protocol):
    """Subset of axiom_encode.concepts.registry.ConceptRegistry used here."""

    def concept_for_name(self, name: str) -> Any: ...


IDENT_RE = re.compile(r"\b([a-z][a-z0-9_]*)\b")
BUILTIN_IDENTIFIERS = frozenset(
    {
        "and",
        "ceil",
        "count_where",
        "else",
        "false",
        "floor",
        "if",
        "match",
        "max",
        "min",
        "not",
        "or",
        "true",
    }
)


@dataclass(frozen=True)
class RuleSpecModule:
    """A parsed RuleSpec module in an explicit corpus state."""

    target: str
    imports: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Producer:
    """One rule producer in a RuleSpec module."""

    name: str
    target: str
    kind: str


@dataclass(frozen=True)
class CorpusIndex:
    """Cacheable graph index over a loaded RuleSpec corpus."""

    producers_by_name: Mapping[str, tuple[Producer, ...]]
    consumed_by_target: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CorpusState:
    """Explicit, immutable input describing the available RuleSpec corpus."""

    modules: Mapping[str, RuleSpecModule] = field(default_factory=dict)
    corpus_sha: str | None = None
    concept_registry: ConceptRegistryLike | None = None
    index: CorpusIndex | None = None


@dataclass(frozen=True)
class RunnableProgram:
    """Deterministic emitted RuleSpec program."""

    target: str
    payload: Mapping[str, Any]
    source: bytes

    def text(self) -> str:
        return self.source.decode("utf-8")


def compose(spec: ProgramSpec, corpus_state: CorpusState) -> RunnableProgram:
    """Compose a runnable RuleSpec program.

    This function is pure: callers must pass all corpus and registry state in.
    """

    _validate_outputs_against_registry(spec, corpus_state.concept_registry)
    allowed_prefixes = _allowed_prefixes_for_program(
        spec.program, corpus_state, explicit=spec.jurisdictions()
    )
    root_imports = _root_imports(spec, corpus_state)
    imports = dependency_closure(
        root_imports, corpus_state, allowed_prefixes=allowed_prefixes
    )
    rules = [
        build_transformation(item.pattern, {"pattern": item.pattern, **item.parameters})
        for item in spec.transformations
    ]
    rules = _apply_auto_gate_outputs(spec, corpus_state, imports, rules)

    # Coverage assertion: walk each eligibility-shaped output and refuse to
    # compose if there are atomic eligibility rules in scope the output
    # silently ignores. Closes the "compose succeeds but engine returns
    # over-permissive answer" trap that bit CA SNAP. Specs can opt out per
    # output via `acknowledged_incomplete:` for honest bootstrap states.
    _assert_eligibility_coverage(spec, corpus_state, imports, rules)

    payload: dict[str, Any] = {
        "format": "rulespec/v1",
        "module": {
            "kind": "composition",
            "summary": _summary(spec, corpus_state),
        },
    }
    if imports:
        payload["imports"] = list(imports)
    if rules:
        payload["rules"] = rules

    target = _program_target(spec.program, spec.period)
    source = _dump_yaml(payload)
    return RunnableProgram(target=target, payload=payload, source=source)


def _assert_eligibility_coverage(
    spec: ProgramSpec,
    corpus_state: CorpusState,
    imports: tuple[str, ...],
    synthesized_rules: list[Mapping[str, Any]],
) -> None:
    """Raise ComposeError if any eligibility-shaped output silently drops
    atomic eligibility rules that the imported corpus exposes."""
    from .coverage import (
        ELIGIBILITY_MARKERS,
        find_uncovered_eligibility_rules,
        format_coverage_error,
    )

    # Build the unified rule map: atomic rules from imported modules plus
    # synthesized transformation rules. Both sides expose `versions`-with-
    # formulas the analyzer understands.
    rules_by_name: dict[str, Mapping[str, Any]] = {}
    for target in imports:
        module = corpus_state.modules.get(target)
        if module is None:
            continue
        for rule in module.payload.get("rules") or ():
            if not isinstance(rule, Mapping):
                continue
            name = rule.get("name")
            if isinstance(name, str) and name and name not in rules_by_name:
                rules_by_name[name] = rule
    for rule in synthesized_rules:
        name = rule.get("name")
        if isinstance(name, str) and name:
            # Synthesized rules win over corpus rules of the same name —
            # they're the program-level override.
            rules_by_name[name] = rule

    acknowledged = set(spec.acknowledged_incomplete)
    for output in spec.outputs:
        if output in acknowledged:
            continue
        if not any(marker in output for marker in ELIGIBILITY_MARKERS):
            continue
        if output not in rules_by_name:
            # The outputs-against-registry check upstream will already
            # have errored on undefined outputs; skip silently here.
            continue
        uncovered = find_uncovered_eligibility_rules(
            output=output, rules_by_name=rules_by_name
        )
        if uncovered:
            raise ComposeError(format_coverage_error(output, uncovered))


def dependency_closure(
    roots: tuple[str, ...] | list[str],
    corpus_state: CorpusState,
    *,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return deterministic import and formula-dependency closure."""

    seen: set[str] = set()
    ordered: list[str] = []

    def visit(target: str) -> None:
        normalized = _normalize_import(target)
        if normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)
        module = corpus_state.modules.get(normalized)
        if module is None:
            return
        for child in module.imports:
            visit(child)
        if corpus_state.index is None:
            return
        reachable = (
            normalized,
            *_explicit_import_closure(module.imports, corpus_state),
        )
        for identifier in corpus_state.index.consumed_by_target.get(normalized, ()):
            producer = _resolve_producer_in_context(
                identifier,
                corpus_state,
                reachable_targets=reachable,
            )
            if producer is not None:
                visit(producer.target)

    for root in roots:
        visit(root)
    return tuple(ordered)


def _explicit_import_closure(
    roots: tuple[str, ...] | list[str], corpus_state: CorpusState
) -> tuple[str, ...]:
    """Return closure from encoded imports only, without formula inference."""

    seen: set[str] = set()
    ordered: list[str] = []

    def visit(target: str) -> None:
        normalized = _normalize_import(target)
        if normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)
        module = corpus_state.modules.get(normalized)
        if module is None:
            return
        for child in module.imports:
            visit(child)

    for root in roots:
        visit(root)
    return tuple(ordered)


def build_corpus_index(corpus_state: CorpusState) -> CorpusIndex:
    """Build a reusable graph index from already-loaded RuleSpec modules."""

    producers: dict[str, list[Producer]] = defaultdict(list)
    consumed_by_target: dict[str, list[str]] = defaultdict(list)

    for target in sorted(corpus_state.modules):
        module = corpus_state.modules[target]
        rules = module.payload.get("rules") or []
        if not isinstance(rules, list):
            continue
        module_producers: set[str] = set()
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            name = rule.get("name")
            if not isinstance(name, str) or not name:
                continue
            kind = rule.get("kind")
            producers[name].append(
                Producer(name=name, target=target, kind=str(kind or ""))
            )
            module_producers.add(name)
            for identifier in _rule_consumed_identifiers(rule):
                if identifier not in consumed_by_target[target]:
                    consumed_by_target[target].append(identifier)
        consumed_by_target[target] = [
            identifier
            for identifier in consumed_by_target[target]
            if identifier not in module_producers
        ]

    return CorpusIndex(
        producers_by_name={
            name: tuple(sorted(values, key=lambda item: item.target))
            for name, values in sorted(producers.items())
        },
        consumed_by_target={
            target: tuple(values)
            for target, values in sorted(consumed_by_target.items())
        },
    )


def with_corpus_index(corpus_state: CorpusState) -> CorpusState:
    """Return corpus state with a built index, preserving explicit inputs."""

    return CorpusState(
        modules=corpus_state.modules,
        corpus_sha=corpus_state.corpus_sha,
        concept_registry=corpus_state.concept_registry,
        index=build_corpus_index(corpus_state),
    )


def module_from_payload(target: str, payload: Mapping[str, Any]) -> RuleSpecModule:
    """Create a corpus module from parsed RuleSpec YAML."""

    imports = payload.get("imports") or ()
    if isinstance(imports, str) or not isinstance(imports, list | tuple):
        raise ComposeError(f"{target}: imports must be a list")
    return RuleSpecModule(
        target=_normalize_import(target),
        imports=tuple(_normalize_import(str(item)) for item in imports),
        payload=payload,
    )


def load_corpus_state(
    modules: Mapping[str, Path],
    *,
    corpus_sha: str | None = None,
    concept_registry: ConceptRegistryLike | None = None,
) -> CorpusState:
    """I/O helper for callers and tests. The pure core does not call this."""

    parsed = {}
    for target, path in modules.items():
        payload = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(payload, Mapping):
            raise ComposeError(f"{path}: module root must be a mapping")
        parsed[_normalize_import(target)] = module_from_payload(target, payload)
    return CorpusState(
        modules=parsed,
        corpus_sha=corpus_sha,
        concept_registry=concept_registry,
    )


def load_corpus_from_roots(
    roots: list[Path] | tuple[Path, ...],
    *,
    corpus_sha: str | None = None,
    concept_registry: ConceptRegistryLike | None = None,
) -> CorpusState:
    """Load and index all RuleSpec modules under rulespec-style repo roots.

    This is an I/O helper for startup/cache-building code. The pure composition
    function does not call it.
    """

    modules: dict[str, RuleSpecModule] = {}
    for root in roots:
        root = Path(root)
        prefix = _repo_prefix(root)
        for path in sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")):
            if path.name.endswith(".test.yaml") or path.name.endswith(".test.yml"):
                continue
            target = _target_for_repo_file(prefix, root, path)
            payload = yaml.safe_load(path.read_text()) or {}
            if not isinstance(payload, Mapping):
                raise ComposeError(f"{path}: module root must be a mapping")
            modules[target] = module_from_payload(target, payload)
    return with_corpus_index(
        CorpusState(
            modules=modules,
            corpus_sha=corpus_sha,
            concept_registry=concept_registry,
        )
    )


def load_default_concept_registry() -> ConceptRegistryLike:
    """Load the canonical registry from axiom-encode when available."""

    try:
        from axiom_encode.concepts import load_concept_registry
    except ImportError as exc:
        raise ComposeError(
            "axiom-encode is required to load the default concept registry"
        ) from exc
    return load_concept_registry()


def _validate_outputs_against_registry(
    spec: ProgramSpec, registry: ConceptRegistryLike | None
) -> None:
    if registry is None:
        return
    missing = [name for name in spec.outputs if registry.concept_for_name(name) is None]
    if missing:
        raise ComposeError(
            "outputs are not registered canonical concepts or synonyms: "
            + ", ".join(sorted(missing))
        )


def _root_imports(spec: ProgramSpec, corpus_state: CorpusState) -> tuple[str, ...]:
    explicit_roots = tuple(
        _scope_target(spec.program, scope_name, path)
        for scope_name, paths in spec.import_scopes().items()
        for path in paths
    )
    if explicit_roots:
        return explicit_roots
    if corpus_state.index is None:
        raise ComposeError(
            "spec does not declare explicit scope imports and CorpusState has no index"
        )
    allowed = _allowed_prefixes_for_program(
        spec.program, corpus_state, explicit=spec.jurisdictions()
    )
    roots: list[str] = []
    for output in spec.outputs:
        producer = _resolve_producer(
            output,
            corpus_state,
            allowed_prefixes=allowed,
            required=True,
        )
        if producer is not None:
            roots.append(producer.target)
    return tuple(roots)


def _resolve_producer(
    name: str,
    corpus_state: CorpusState,
    *,
    allowed_prefixes: tuple[str, ...],
    required: bool,
) -> Producer | None:
    if corpus_state.index is None:
        if required:
            raise ComposeError("CorpusState has no index for producer discovery")
        return None
    candidates = tuple(
        candidate
        for candidate in corpus_state.index.producers_by_name.get(name, ())
        if _target_prefix(candidate.target) in allowed_prefixes
    )
    if not candidates:
        if required:
            allowed = ", ".join(allowed_prefixes)
            raise ComposeError(f"no producer found for {name!r} in scope: {allowed}")
        return None

    ranked: dict[int, list[Producer]] = defaultdict(list)
    for candidate in candidates:
        ranked[allowed_prefixes.index(_target_prefix(candidate.target))].append(
            candidate
        )
    best = tuple(sorted(ranked[min(ranked)], key=lambda item: item.target))
    if len(best) > 1:
        targets = ", ".join(item.target for item in best)
        raise ComposeError(f"ambiguous producers for {name!r}: {targets}")
    return best[0]


def _resolve_producer_in_context(
    name: str,
    corpus_state: CorpusState,
    *,
    reachable_targets: tuple[str, ...],
) -> Producer | None:
    if corpus_state.index is None:
        return None
    reachable = set(reachable_targets)
    candidates = tuple(
        candidate
        for candidate in corpus_state.index.producers_by_name.get(name, ())
        if candidate.target in reachable
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        targets = ", ".join(
            item.target for item in sorted(candidates, key=lambda x: x.target)
        )
        raise ComposeError(
            f"ambiguous producers for {name!r} in import context: {targets}"
        )
    return candidates[0]


def _allowed_prefixes_for_program(
    program: str, corpus_state: CorpusState, *, explicit: tuple[str, ...]
) -> tuple[str, ...]:
    if explicit:
        return _dedupe((*explicit, "us"))
    if not program:
        prefixes = tuple(
            _target_prefix(target) for target in sorted(corpus_state.modules)
        )
        return _dedupe(("us", *prefixes))
    program_prefix = program.split("/", 1)[0]
    return _dedupe((program_prefix, "us"))


def _scope_target(program: str, scope_name: str, path: str) -> str:
    if ":" in path:
        return _normalize_import(path)
    prefix = _scope_prefix(program, scope_name)
    return _normalize_import(f"{prefix}:{path}")


def _scope_prefix(program: str, scope_name: str) -> str:
    normalized = scope_name.strip()
    if normalized == "federal":
        return "us"
    if normalized == "state":
        return program.split("/", 1)[0]
    return normalized


def _normalize_import(target: str) -> str:
    prefix, separator, path = target.strip().partition(":")
    if not separator or not prefix or not path:
        raise ComposeError(f"invalid RuleSpec import target: {target!r}")
    return f"{prefix}:{path.strip().strip('/')}"


def _repo_prefix(root: Path) -> str:
    name = root.name
    if name.startswith("rulespec-"):
        return name.removeprefix("rulespec-")
    if name.startswith("rules-"):
        return name.removeprefix("rules-")
    raise ComposeError(f"{root}: expected repo name rulespec-<prefix>")


def _target_for_repo_file(prefix: str, root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return _normalize_import(f"{prefix}:{relative.as_posix()}")


def _target_prefix(target: str) -> str:
    prefix, _, _ = _normalize_import(target).partition(":")
    return prefix


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _rule_consumed_identifiers(rule: Mapping[str, Any]) -> tuple[str, ...]:
    identifiers: list[str] = []
    versions = rule.get("versions") or []
    if not isinstance(versions, list):
        return ()
    for version in versions:
        if not isinstance(version, Mapping):
            continue
        formula = version.get("formula")
        if not isinstance(formula, str):
            continue
        for identifier in IDENT_RE.findall(formula):
            if identifier in BUILTIN_IDENTIFIERS or identifier in identifiers:
                continue
            identifiers.append(identifier)
    return tuple(identifiers)


def _program_target(program: str, period: str) -> str:
    return f"{program.strip().strip('/')}/{period.strip()}"


def _summary(spec: ProgramSpec, corpus_state: CorpusState) -> str:
    sha = corpus_state.corpus_sha or "unversioned corpus"
    outputs = ", ".join(spec.outputs)
    return (
        f"Deterministic composition for {spec.program} at {spec.period}. "
        f"Outputs: {outputs}. Corpus: {sha}."
    )


def _apply_auto_gate_outputs(
    spec: ProgramSpec,
    corpus_state: CorpusState,
    imports: tuple[str, ...],
    rules: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """For each output in ``spec.auto_gate_outputs``, AND-gate the existing
    rule with eligibility-shaped rules from scope that the output doesn't
    reach. Rename the original rule to ``<name>_core`` and synthesize a new
    ``<name> = all_of(<name>_core, ...discovered)`` wrapping it.

    The discovered conditions come from a transitive-dependency walk over
    the existing rule plus all imported corpus rules — only eligibility-
    shaped rules that the original output does not already reach get
    AND-gated in. Programs without ``auto_gate_outputs`` are unchanged.
    """
    if not spec.auto_gate_outputs:
        return rules

    rules_by_name: dict[str, Mapping[str, Any]] = {}
    for target in imports:
        module = corpus_state.modules.get(target)
        if module is None:
            continue
        for rule in module.payload.get("rules") or ():
            if not isinstance(rule, Mapping):
                continue
            name = rule.get("name")
            if isinstance(name, str) and name and name not in rules_by_name:
                rules_by_name[name] = rule
    for rule in rules:
        name = rule.get("name") if isinstance(rule, Mapping) else None
        if isinstance(name, str) and name:
            rules_by_name[name] = rule

    new_rules: list[Mapping[str, Any]] = []
    for rule in rules:
        name = rule.get("name") if isinstance(rule, Mapping) else None
        if not isinstance(name, str) or name not in spec.auto_gate_outputs:
            new_rules.append(rule)
            continue

        from .coverage import find_uncovered_eligibility_rules

        uncovered = find_uncovered_eligibility_rules(
            output=name, rules_by_name=rules_by_name
        )
        # Restrict to rules sharing the original output's name prefix so we
        # don't pull in unrelated programs' eligibility rules that happen
        # to share the corpus root (e.g. CTC/EITC eligibility rules when
        # gating SNAP). Prefix derived from the longest leading run of
        # underscore-delimited segments shared with the output name.
        prefix = _shared_name_prefix(name)
        program_uncovered = [n for n in uncovered if prefix and n.startswith(prefix)]
        if not program_uncovered:
            new_rules.append(rule)
            continue

        core_name = f"{name}_core"
        renamed_core = dict(rule)
        renamed_core["name"] = core_name
        new_rules.append(renamed_core)

        # Synthesize the wrapper via the all_of pattern so the formula and
        # metadata stay consistent with hand-written all_of transformations.
        wrapper_params = {
            "pattern": "all_of",
            "name": name,
            "entity": rule.get("entity", "Household"),
            "dtype": rule.get("dtype", "Judgment"),
            "period": rule.get("period", "Month"),
            "source": rule.get("source", ""),
            "conditions": [core_name, *program_uncovered],
        }
        effective_from = (rule.get("versions") or [{}])[0].get("effective_from")
        if effective_from:
            wrapper_params["effective_from"] = effective_from
        new_rules.append(build_transformation("all_of", wrapper_params))

    return new_rules


def _shared_name_prefix(name: str) -> str:
    """Return ``"<root>_"`` from a snake_case name (e.g. snap_eligible -> snap_)."""
    if "_" not in name:
        return ""
    return name.split("_", 1)[0] + "_"


def _dump_yaml(payload: Mapping[str, Any]) -> bytes:
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
        width=4096,
        default_flow_style=False,
    )
    return text.encode("utf-8")
