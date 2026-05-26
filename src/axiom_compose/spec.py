"""Declarative program spec loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


class SpecError(ValueError):
    """Raised when a declarative program spec is invalid."""


@dataclass(frozen=True)
class TransformationSpec:
    """A declarative invocation of a generic transformation pattern."""

    pattern: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class ProgramSpec:
    """Program-agnostic composition request."""

    program: str
    period: str
    outputs: tuple[str, ...]
    scope: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    rounding: str | None = None
    transformations: tuple[TransformationSpec, ...] = field(default_factory=tuple)
    # Outputs whose eligibility-coverage check is intentionally disabled.
    # Useful for bootstrap iterations that ship a deliberately partial
    # eligibility chain. Each acknowledged output gets a compose warning
    # but no error. Listed by output rule name (matches `outputs`).
    acknowledged_incomplete: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProgramSpec":
        required = ("program", "period", "outputs")
        missing = [key for key in required if key not in raw]
        if missing:
            raise SpecError(f"missing required spec keys: {', '.join(missing)}")

        program = _non_empty_string(raw["program"], "program")
        period = _non_empty_string(raw["period"], "period")
        outputs = _string_tuple(raw["outputs"], "outputs")
        if not outputs:
            raise SpecError("outputs must contain at least one output name")

        scope_raw = raw.get("scope") or {}
        if not isinstance(scope_raw, Mapping):
            raise SpecError("scope must be a mapping")
        scope = {
            _non_empty_string(key, "scope key"): _string_tuple(value, f"scope.{key}")
            for key, value in scope_raw.items()
        }
        transformations = tuple(
            _transformation_from_mapping(item, index)
            for index, item in enumerate(raw.get("transformations") or [])
        )
        rounding = raw.get("rounding")
        if rounding is not None:
            rounding = _non_empty_string(rounding, "rounding")

        acknowledged_incomplete = ()
        if "acknowledged_incomplete" in raw:
            acknowledged_incomplete = _string_tuple(
                raw["acknowledged_incomplete"], "acknowledged_incomplete"
            )

        return cls(
            program=program,
            period=period,
            outputs=outputs,
            scope=scope,
            rounding=rounding,
            transformations=transformations,
            acknowledged_incomplete=acknowledged_incomplete,
        )

    def import_scopes(self) -> Mapping[str, tuple[str, ...]]:
        """Scope entries that name RuleSpec import anchors."""

        return {
            key: value
            for key, value in self.scope.items()
            if key not in {"jurisdictions", "include", "exclude"}
        }

    def jurisdictions(self) -> tuple[str, ...]:
        """Allowed jurisdiction prefixes for output discovery."""

        return self.scope.get("jurisdictions", ())

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "program": self.program,
            "period": self.period,
            "outputs": list(self.outputs),
            "scope": {key: list(value) for key, value in self.scope.items()},
        }
        if self.rounding is not None:
            payload["rounding"] = self.rounding
        if self.transformations:
            payload["transformations"] = [
                {"pattern": item.pattern, **dict(item.parameters)}
                for item in self.transformations
            ]
        if self.acknowledged_incomplete:
            payload["acknowledged_incomplete"] = list(self.acknowledged_incomplete)
        return payload


def load_spec(path: str | Path) -> ProgramSpec:
    """Load a program spec from YAML."""

    payload = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(payload, Mapping):
        raise SpecError("spec root must be a mapping")
    return ProgramSpec.from_mapping(payload)


def _transformation_from_mapping(raw: Any, index: int) -> TransformationSpec:
    if not isinstance(raw, Mapping):
        raise SpecError(f"transformations[{index}] must be a mapping")
    pattern = _non_empty_string(raw.get("pattern"), f"transformations[{index}].pattern")
    parameters = {str(key): value for key, value in raw.items() if key != "pattern"}
    return TransformationSpec(pattern=pattern, parameters=parameters)


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise SpecError(f"{name} must be a list of strings")
    result = tuple(_non_empty_string(item, f"{name}[]") for item in value)
    return result
