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
        transitive_dependencies,
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

    # Auto-gated outputs opt out of strict coverage: the auto-gate already
    # wired in the household-level eligibility gates; any remaining uncovered
    # rules are conditional alternatives or exception clauses the auto-gate
    # deliberately excludes (because AND-gating them would require inputs
    # the program doesn't expose).
    acknowledged = set(spec.acknowledged_incomplete) | set(spec.auto_gate_outputs)
    synthesized_rule_names = {
        name
        for rule in synthesized_rules
        if isinstance((name := rule.get("name")), str) and name
    }
    eligibility_outputs = {
        output
        for output in spec.outputs
        if any(marker in output for marker in ELIGIBILITY_MARKERS)
    }
    nested_eligibility_outputs = {
        nested
        for output in eligibility_outputs
        for nested in transitive_dependencies(output, rules_by_name)
        if nested != output and nested in eligibility_outputs
    }
    for output in spec.outputs:
        if output in acknowledged:
            continue
        if not any(marker in output for marker in ELIGIBILITY_MARKERS):
            continue
        if output not in synthesized_rule_names:
            # Direct atomic outputs expose the meaning of their own provision;
            # they are not program-level conclusions and therefore do not own
            # every sibling eligibility rule imported by the ProgramSpec.
            # Cross-module coverage is a corpus validation concern. This
            # compose-time assertion only polices conclusions synthesized by
            # the ProgramSpec's transformations.
            continue
        if output in nested_eligibility_outputs:
            # A declared sub-gate only owns its own condition family. The
            # terminal eligibility output that consumes it owns whole-program
            # coverage, so checking both would falsely require every sibling
            # gate to depend on every other sibling gate.
            continue
        output_rule = rules_by_name.get(output)
        if output_rule is None:
            # The outputs-against-registry check upstream will already
            # have errored on undefined outputs; skip silently here.
            continue
        output_dtype = str(output_rule.get("dtype") or "").lower()
        if output_dtype and output_dtype not in {"bool", "boolean", "judgment"}:
            # Names such as ``gross_income_limit`` and ``resource_limit`` are
            # monetary/quantity parameters, not eligibility conclusions. They
            # may be dependencies of a Judgment output, but must not themselves
            # trigger the missing-gate assertion.
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


_COUNTRY_CHECKOUT_RE = re.compile(r"^rulespec-(?P<country>[a-z]{2})$")
_JURISDICTION_DIR_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)*$")
_ATOMIC_RULESPEC_ROOTS = ("legislation", "policies", "regulations", "statutes")
_PROGRAM_SPEC_ROOT = "programs"
_FILESYSTEM_ROOTS = (*_ATOMIC_RULESPEC_ROOTS, _PROGRAM_SPEC_ROOT)
_IMPORT_PREFIX_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)*$")
_IMPORT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IMPORT_FRAGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _path_uses_exact_directory_entry_casing(path: Path) -> bool:
    """Return whether every lexical component matches its directory entry.

    ``Path.resolve`` cannot detect case aliases on case-insensitive filesystems.
    Comparing each component to the names actually returned by its parent keeps
    the Python loader aligned with the Rust loader's exact-path contract.
    """

    if not path.is_absolute():
        return False
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        try:
            names = {child.name for child in cursor.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        cursor /= part
    return True


def _canonical_jurisdiction_roots(root: Path) -> list[tuple[str, Path]]:
    """Return direct jurisdiction roots from one exact country checkout."""

    if not root.is_absolute():
        raise ComposeError(f"RuleSpec checkout must be an absolute path: {root}")
    if root.is_symlink() or not root.is_dir():
        raise ComposeError(f"RuleSpec checkout must be a real directory: {root}")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ComposeError(f"RuleSpec checkout does not exist: {root}") from exc
    if resolved != root or not _path_uses_exact_directory_entry_casing(root):
        raise ComposeError(f"RuleSpec checkout path must not contain aliases: {root}")

    match = _COUNTRY_CHECKOUT_RE.fullmatch(root.name)
    if match is None:
        raise ComposeError(
            "expected exact country checkout named rulespec-<country>; "
            f"got {root.name!r}"
        )
    country = match.group("country")

    legacy_roots = [
        root / marker
        for marker in _FILESYSTEM_ROOTS
        if (root / marker).exists() or (root / marker).is_symlink()
    ]
    if legacy_roots:
        rendered = ", ".join(path.name for path in legacy_roots)
        raise ComposeError(
            "repository-root RuleSpec content is not canonical; move it under "
            f"a direct jurisdiction root: {rendered}"
        )

    jurisdictions: list[tuple[str, Path]] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            raise ComposeError(f"RuleSpec checkout contains a symlink: {child}")
        if not child.is_dir():
            continue
        has_content_root = any(
            (child / marker).exists() or (child / marker).is_symlink()
            for marker in _FILESYSTEM_ROOTS
        )
        if not has_content_root:
            continue
        if _JURISDICTION_DIR_RE.fullmatch(child.name) is None or not (
            child.name == country or child.name.startswith(f"{country}-")
        ):
            raise ComposeError(
                f"noncanonical jurisdiction directory in {root.name}: {child.name}"
            )
        _validate_jurisdiction_tree(child)
        jurisdictions.append((child.name, child))

    if not jurisdictions:
        raise ComposeError(f"{root}: no canonical jurisdiction content roots found")
    return jurisdictions


def _validate_jurisdiction_tree(jurisdiction: Path) -> None:
    """Reject aliases, special YAML paths, and the removed .yml spelling."""

    for marker in _FILESYSTEM_ROOTS:
        content_root = jurisdiction / marker
        if not content_root.exists() and not content_root.is_symlink():
            continue
        if content_root.is_symlink() or not content_root.is_dir():
            raise ComposeError(
                f"canonical content root must be a real directory: {content_root}"
            )
        for path in sorted(content_root.rglob("*")):
            if path.is_symlink():
                raise ComposeError(f"RuleSpec content contains a symlink: {path}")
            yaml_like = path.suffix.lower() in {".yaml", ".yml"}
            if yaml_like and not path.is_file():
                raise ComposeError(f"RuleSpec YAML path must be a regular file: {path}")
            if path.is_file() and yaml_like and path.suffix != ".yaml":
                raise ComposeError(
                    f"RuleSpec files must use the exact .yaml extension: {path}"
                )


def load_corpus_from_roots(
    roots: list[Path] | tuple[Path, ...],
    *,
    corpus_sha: str | None = None,
    concept_registry: ConceptRegistryLike | None = None,
) -> CorpusState:
    """Load atomic RuleSpec modules from exact country-monorepo checkouts."""

    if not roots:
        raise ComposeError(
            "at least one explicit rulespec-<country> checkout is required"
        )
    modules: dict[str, RuleSpecModule] = {}
    seen_roots: set[Path] = set()
    seen_countries: set[str] = set()
    for raw_root in roots:
        root = Path(raw_root)
        if root in seen_roots:
            raise ComposeError(f"duplicate RuleSpec checkout: {root}")
        seen_roots.add(root)
        country = root.name.removeprefix("rulespec-")
        if country in seen_countries:
            raise ComposeError(f"duplicate RuleSpec country checkout: {country}")
        seen_countries.add(country)
        for prefix, jurisdiction_root in _canonical_jurisdiction_roots(root):
            for marker in _ATOMIC_RULESPEC_ROOTS:
                content_root = jurisdiction_root / marker
                if not content_root.is_dir():
                    continue
                for path in sorted(content_root.rglob("*.yaml")):
                    if path.name.endswith(".test.yaml"):
                        continue
                    target = _target_for_repo_file(prefix, jurisdiction_root, path)
                    payload = yaml.safe_load(path.read_text()) or {}
                    if not isinstance(payload, Mapping):
                        raise ComposeError(f"{path}: module root must be a mapping")
                    if payload.get("format") != "rulespec/v1":
                        raise ComposeError(
                            f"{path}: atomic module must declare format: rulespec/v1"
                        )
                    module = payload.get("module")
                    if isinstance(module, Mapping) and "kind" in module:
                        raise ComposeError(
                            f"{path}: atomic modules must not declare module.kind; "
                            "composition belongs under programs/"
                        )
                    if target in modules:
                        raise ComposeError(
                            f"duplicate RuleSpec module target: {target}"
                        )
                    modules[target] = module_from_payload(target, payload)
    if not modules:
        raise ComposeError(
            "explicit RuleSpec checkouts contain no atomic rulespec/v1 modules"
        )
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
        _assert_scope_roots_in_corpus(explicit_roots, corpus_state)
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
    program_prefix = program.split("/", 1)[0] if program else ""
    country_prefix = program_prefix.split("-", 1)[0] if program_prefix else ""
    if explicit:
        return _dedupe((*explicit, country_prefix))
    if not program:
        prefixes = tuple(
            _target_prefix(target) for target in sorted(corpus_state.modules)
        )
        return _dedupe(prefixes)
    return _dedupe((program_prefix, country_prefix))


def _assert_scope_roots_in_corpus(
    roots: tuple[str, ...], corpus_state: CorpusState
) -> None:
    # A scope entry that names no module in the corpus would otherwise pass
    # through composition and surface as an engine import error three repos
    # downstream. Module-less corpus states (pattern-synthesis fixtures) are
    # exempt: there is nothing to resolve against.
    if not corpus_state.modules:
        return
    missing = sorted(root for root in roots if root not in corpus_state.modules)
    if missing:
        raise ComposeError(
            "scope entries do not resolve to any module in the corpus: "
            + ", ".join(missing)
        )


def _scope_target(program: str, scope_name: str, path: str) -> str:
    if ":" in path:
        return _normalize_import(path)
    prefix = _scope_prefix(program, scope_name)
    return _normalize_import(f"{prefix}:{path}")


def _scope_prefix(program: str, scope_name: str) -> str:
    normalized = scope_name.strip()
    if normalized == "federal":
        return program.split("/", 1)[0].split("-", 1)[0]
    if normalized == "state":
        return program.split("/", 1)[0]
    return normalized


def _normalize_import(target: str) -> str:
    """Validate an absolute atomic import ref and return its module target.

    Atomic modules may import one exact rule via ``#fragment``. Composition
    indexes and emitted root imports operate on modules, so the validated
    fragment is intentionally removed from the returned lookup target.
    """

    if not isinstance(target, str) or target != target.strip():
        raise ComposeError(f"invalid RuleSpec import target: {target!r}")
    if "\\" in target or target.count("#") > 1:
        raise ComposeError(f"invalid RuleSpec import target: {target!r}")
    module_target, has_fragment, fragment = target.partition("#")
    if has_fragment and _IMPORT_FRAGMENT_RE.fullmatch(fragment) is None:
        raise ComposeError(f"invalid RuleSpec import fragment: {target!r}")
    if module_target.count(":") != 1:
        raise ComposeError(f"invalid RuleSpec import target: {target!r}")
    prefix, path = module_target.split(":", 1)
    segments = path.split("/")
    if (
        _IMPORT_PREFIX_RE.fullmatch(prefix) is None
        or not path
        or path.endswith((".yaml", ".yml"))
        or any(_IMPORT_SEGMENT_RE.fullmatch(segment) is None for segment in segments)
        or segments[0] not in _ATOMIC_RULESPEC_ROOTS
    ):
        raise ComposeError(f"invalid canonical atomic import target: {target!r}")
    return module_target


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


# Name suffixes that identify a household-level eligibility gate.
# Restrict auto-gating to these to avoid pulling in conditional
# alternatives or exception-handling rules that require inputs the
# program doesn't expose.
#
# ``_categorically_eligible`` is intentionally NOT in this list:
# categorical eligibility is an OR-alternative to the income test, not
# an AND-requirement on top of it. Auto-gating it produces wrong
# answers (live failure 2026-05-28 on us-ny/snap: federal
# ``snap_regular_categorically_eligible`` AND-gated alongside NY's BBCE
# excluded NY-BBCE-eligible households that don't also receive
# TANF/SSI). Categorical eligibility belongs inside a rolled-up
# ``*_income_eligible`` rule via an OR, which the rulespec encodes.
#
# Add suffixes here as new gate families emerge across benefit programs.
_HOUSEHOLD_GATE_SUFFIXES: tuple[str, ...] = (
    "_income_eligible",
    "_resource_eligible",
    "_residency_eligible",
)


def _filter_to_household_gate_candidates(
    candidates: list[str], program_token: str
) -> list[str]:
    """Return only names that look like a top-level household eligibility
    gate AND belong to the same program as the output being gated.

    "Belongs to" matches the program token as an underscore-bounded token
    anywhere in the rule name. This handles state-namespaced rules like
    ``ny_snap_categorically_eligible`` for an ``us-ny/snap`` program — the
    older output-prefix-only check rejected those because their leading
    token was the state code, not ``snap``. ``program_token`` comes from
    ``spec.program`` so cross-program rules (e.g. ``ctc_*`` in a SNAP
    program's shared scope) are still rejected.

    ``ProgramSpec.program`` is the sole program identity; output-name
    heuristics are intentionally not accepted as a second namespace."""
    if not program_token:
        return []

    def belongs(name: str) -> bool:
        return (
            name == program_token
            or name.startswith(f"{program_token}_")
            or f"_{program_token}_" in name
        )

    return [
        name
        for name in candidates
        if belongs(name)
        and any(name.endswith(suffix) for suffix in _HOUSEHOLD_GATE_SUFFIXES)
    ]


def _minimal_cover(
    candidates: list[str], rules_by_name: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Drop candidates that are transitively reachable from another candidate.

    The eligibility-marker filter sweeps in both top-level rollups
    (``snap_income_eligible``) AND their constituent leaves
    (``ny_snap_categorically_eligible``, ``snap_standard_income_eligible``).
    AND-gating both is at best redundant and at worst wrong: if the rollup
    expresses an OR over alternatives, gating the leaves forces all the
    alternatives to hold simultaneously, which they're not designed to.
    Keeping only rules that no other candidate reaches preserves the
    rulespec's encoded OR-structure inside each gated rollup."""

    from .coverage import transitive_dependencies

    candidate_set = set(candidates)
    dominated: set[str] = set()
    for cand in candidates:
        deps = transitive_dependencies(cand, rules_by_name)
        for dep in deps:
            if dep != cand and dep in candidate_set:
                dominated.add(dep)
    return [c for c in candidates if c not in dominated]


def _program_token(program: str) -> str:
    """Return the program identifier from a ``spec.program`` path.

    ``us-ny/snap`` → ``snap``; ``co/medicaid`` → ``medicaid``; ``snap`` →
    ``snap``. Used by auto-gate to recognize rules that belong to the
    program regardless of state-namespace prefix."""
    if not program:
        return ""
    tail = program.rsplit("/", 1)[-1].strip()
    return tail.lower()


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
        # Only AND-gate rules that look like household-level eligibility
        # gates (income/resource/residency/categorical) AND share the gated
        # output's program prefix. The broader "any uncovered eligibility
        # rule" set sweeps in conditional alternatives and exception clauses
        # that require inputs not present in every household — AND-gating
        # those collapses the formula to None for the common case (live
        # failure 2026-05-28: 0/8 cases evaluated when 9 unrelated rules
        # were pulled in).
        gate_uncovered = _filter_to_household_gate_candidates(
            uncovered, _program_token(spec.program)
        )
        # Reduce to minimal cover: drop any candidate that's reachable
        # via another candidate's dependency tree. The rulespec already
        # rolls alternative gates up (e.g. snap_income_eligible reaches
        # ny_snap_categorically_eligible via its OR-formula), so gating
        # both the rollup and its leaves redundantly AND-chains an OR
        # alternative — collapsing the formula to False for cases that
        # qualify under one branch but not the other.
        gate_uncovered = _minimal_cover(gate_uncovered, rules_by_name)
        if not gate_uncovered:
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
            "conditions": [core_name, *gate_uncovered],
        }
        effective_from = (rule.get("versions") or [{}])[0].get("effective_from")
        if effective_from:
            wrapper_params["effective_from"] = effective_from
        new_rules.append(build_transformation("all_of", wrapper_params))

    return new_rules


def _dump_yaml(payload: Mapping[str, Any]) -> bytes:
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
        width=4096,
        default_flow_style=False,
    )
    return text.encode("utf-8")
