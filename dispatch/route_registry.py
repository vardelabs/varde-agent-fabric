"""Portable, strict model-first route configuration."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class RouteConfigError(ValueError):
    """Raised when route configuration or resolution is unsafe or ambiguous."""


_MAX_MODEL = 128
_MAX_NAME = 64
_ALLOWED_POLICIES = {"none", "remote-unavailable"}
_ALLOWED_TARGETS = {"local", "remote"}
_ALLOWED_ROUTE_KEYS = {"default_harness", "harnesses", "target", "kinds", "fallback"}
_ALLOWED_FALLBACK_KEYS = {"policy", "harness", "target"}


def _name(value: Any, field: str, *, max_length: int = _MAX_NAME) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise RouteConfigError(f"{field} must be a non-empty string of at most {max_length} characters")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise RouteConfigError(f"{field} must contain printable non-whitespace characters and no NUL")
    return value


def _model(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_MODEL:
        raise RouteConfigError(f"model must be a non-empty string of at most {_MAX_MODEL} characters")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise RouteConfigError("model must contain printable non-whitespace characters and no NUL")
    return value


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RouteConfigError(f"{field} must be a non-empty list")
    result = tuple(_name(item, f"{field}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise RouteConfigError(f"{field} must not contain duplicates")
    return result


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RouteConfigError(f"{field} must be an object")
    return value


@dataclass(frozen=True)
class FallbackPolicy:
    policy: str
    harness: str | None = None
    target: str | None = None

    @classmethod
    def from_dict(cls, value: Any, field: str = "fallback") -> "FallbackPolicy":
        data = _mapping(value, field)
        unknown = set(data) - _ALLOWED_FALLBACK_KEYS
        if unknown:
            raise RouteConfigError(f"{field} has unknown keys: {sorted(unknown)}")
        policy = data.get("policy")
        if policy not in _ALLOWED_POLICIES:
            raise RouteConfigError(f"{field}.policy must be none or remote-unavailable")
        harness = data.get("harness")
        target = data.get("target")
        if policy == "none":
            if harness is not None or target is not None:
                raise RouteConfigError(f"{field} with policy none cannot define a fallback route")
        elif harness is None or target is None:
            raise RouteConfigError(f"{field} requires an explicit fallback harness and target")
        else:
            harness = _name(harness, f"{field}.harness")
            target = _name(target, f"{field}.target")
            if target not in _ALLOWED_TARGETS:
                raise RouteConfigError(f"{field}.target must be local or remote")
        return cls(policy=policy, harness=harness, target=target)

    def to_dict(self) -> dict[str, str]:
        value = {"policy": self.policy}
        if self.harness is not None:
            value["harness"] = self.harness
        if self.target is not None:
            value["target"] = self.target
        return value


@dataclass(frozen=True)
class RouteDefinition:
    model: str
    default_harness: str
    harnesses: tuple[str, ...]
    target: str
    kinds: tuple[str, ...]
    fallback: FallbackPolicy

    @classmethod
    def from_dict(cls, model: str, value: Any) -> "RouteDefinition":
        model = _model(model)
        data = _mapping(value, f"routes[{model!r}]")
        unknown = set(data) - _ALLOWED_ROUTE_KEYS
        missing = {"default_harness", "target", "kinds", "fallback"} - set(data)
        if unknown or missing:
            raise RouteConfigError(
                f"routes[{model!r}] keys invalid, missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        default_harness = _name(data["default_harness"], "default_harness")
        harnesses = _string_list(data.get("harnesses", [default_harness]), "harnesses")
        if default_harness not in harnesses:
            raise RouteConfigError("default_harness must be listed in harnesses")
        target = _name(data["target"], "target")
        if target not in _ALLOWED_TARGETS:
            raise RouteConfigError("target must be local or remote")
        kinds = _string_list(data["kinds"], "kinds")
        fallback = FallbackPolicy.from_dict(data["fallback"])
        if fallback.harness is not None and fallback.harness not in harnesses:
            raise RouteConfigError("fallback harness must be listed in harnesses")
        if (
            fallback.policy == "remote-unavailable"
            and fallback.target == target
        ):
            raise RouteConfigError("remote-unavailable fallback must change target")
        return cls(model, default_harness, harnesses, target, kinds, fallback)


@dataclass(frozen=True)
class ResolvedRoute:
    model: str
    kind: str
    harness: str
    target: str
    harness_overridden: bool
    fallback: FallbackPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "kind": self.kind,
            "harness": self.harness,
            "target": self.target,
            "harness_overridden": self.harness_overridden,
            "fallback": self.fallback.to_dict(),
        }


@dataclass(frozen=True)
class RouteRegistry:
    routes: Mapping[str, RouteDefinition]

    @classmethod
    def from_dict(cls, value: Any) -> "RouteRegistry":
        data = _mapping(value, "route configuration")
        if set(data) != {"schema", "routes"}:
            raise RouteConfigError("route configuration must contain only schema and routes")
        if data["schema"] != "dispatch-routes.v1":
            raise RouteConfigError("unsupported route configuration schema")
        routes_data = _mapping(data["routes"], "routes")
        if not routes_data:
            raise RouteConfigError("routes must contain at least one model")
        routes = {
            _model(model): RouteDefinition.from_dict(model, route)
            for model, route in routes_data.items()
        }
        return cls(MappingProxyType(routes))

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.routes.items())))

    def resolve(self, model: str, kind: str, *, harness: str | None = None) -> ResolvedRoute:
        model = _model(model)
        kind = _name(kind, "kind")
        try:
            definition = self.routes[model]
        except KeyError as exc:
            raise RouteConfigError(f"unknown model: {model}") from exc
        if kind not in definition.kinds:
            raise RouteConfigError(f"kind {kind!r} is not approved for model {model!r}")
        effective_harness = definition.default_harness if harness is None else _name(harness, "harness")
        if effective_harness not in definition.harnesses:
            raise RouteConfigError(
                f"harness {effective_harness!r} is not approved for model {model!r}"
            )
        return ResolvedRoute(
            model=model,
            kind=kind,
            harness=effective_harness,
            target=definition.target,
            harness_overridden=harness is not None and harness != definition.default_harness,
            fallback=definition.fallback,
        )
