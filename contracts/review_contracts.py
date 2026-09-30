"""Strict, transport-neutral contracts for one bounded remote review."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when a review contract is malformed or exceeds a bound."""


_MAX_TEXT = 256
_MAX_PATH_TEXT = 512
MAX_PACKET_BYTES = 256 * 1024
MAX_TRANSPORT_BYTES = MAX_PACKET_BYTES + 64 * 1024
_MAX_FINDINGS = 50
_MAX_FINDING_TEXT = 1024
_ALLOWED_KINDS = {"reviewer"}
_ALLOWED_WORKERS = {"local-gemma", "codex", "opencode"}
_ALLOWED_FINISH = {"completed", "unavailable", "malformed", "timeout", "truncated"}
_ALLOWED_SEVERITIES = {"red", "yellow", "green"}
_ALLOWED_FINDING_KEYS = {"severity", "file", "line", "message", "confidence", "category", "reproduction"}


def _text(value: Any, field: str, *, max_length: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ContractError(f"{field} must be a non-empty string of at most {max_length} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{field} must contain valid UTF-8 text") from exc
    return value


def _model(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ContractError("model must be a non-empty string of at most 128 characters")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise ContractError("model must contain printable non-whitespace characters")
    return value


def _sha(value: Any, field: str) -> str:
    value = _text(value, field, max_length=64)
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ContractError(f"{field} must be a 40-character hexadecimal SHA")
    return value.lower()


def _positive_int(value: Any, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{field} must be a positive integer")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def _keys(data: Mapping[str, Any], expected: set[str], field: str) -> None:
    unknown = set(data) - expected
    missing = expected - set(data)
    if unknown or missing:
        raise ContractError(f"{field} keys invalid, missing={sorted(missing)}, unknown={sorted(unknown)}")


def _finding(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"findings[{index}] must be an object")
    unknown = set(value) - _ALLOWED_FINDING_KEYS
    if unknown:
        raise ContractError(f"findings[{index}] has unknown keys: {sorted(unknown)}")
    if "message" not in value:
        raise ContractError(f"findings[{index}].message is required")
    severity = value.get("severity")
    if severity not in _ALLOWED_SEVERITIES:
        raise ContractError(f"findings[{index}].severity must be red, yellow, or green")
    for key in ("file", "message", "confidence", "category", "reproduction"):
        if key in value:
            _text(value[key], f"findings[{index}].{key}", max_length=_MAX_FINDING_TEXT)
    if "line" in value and (not isinstance(value["line"], int) or isinstance(value["line"], bool) or value["line"] < 1):
        raise ContractError(f"findings[{index}].line must be a positive integer")
    return value


@dataclass(frozen=True)
class ReviewRequest:
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    paths: Any
    kind: str
    worker_kind: str
    correlation_id: str
    packet: str
    timeout_seconds: int = 300
    model: str | None = None

    def __post_init__(self) -> None:
        _text(self.repository, "repository")
        _positive_int(self.pull_request, "pull_request")
        object.__setattr__(self, "base_sha", _sha(self.base_sha, "base_sha"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha, "head_sha"))
        if not isinstance(self.paths, tuple):
            raise ContractError("paths must be a tuple")
        if not self.paths or len(self.paths) > 100:
            raise ContractError("paths must contain between 1 and 100 non-empty strings")
        for index, path in enumerate(self.paths):
            _text(path, f"paths[{index}]", max_length=_MAX_PATH_TEXT)
            if path.startswith("/") or "\x00" in path or any(segment in {"", ".", ".."} for segment in path.split("/")):
                raise ContractError(f"paths[{index}] must be a relative path without empty, '.', '..', or NUL segments")
        if self.kind not in _ALLOWED_KINDS:
            raise ContractError("kind is not approved")
        if self.worker_kind not in _ALLOWED_WORKERS:
            raise ContractError("worker_kind is not approved")
        _text(self.correlation_id, "correlation_id", max_length=128)
        _text(self.packet, "packet", max_length=MAX_PACKET_BYTES)
        if len(self.packet.encode("utf-8")) > MAX_PACKET_BYTES:
            raise ContractError("packet must be non-empty and at most 262144 UTF-8 bytes")
        if not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 900:
            raise ContractError("timeout_seconds must be an integer from 1 to 900")
        if self.model is not None:
            object.__setattr__(self, "model", _model(self.model))
        body = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_TRANSPORT_BYTES:
            raise ContractError("serialized request exceeds transport limit")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema": "review-request.v1",
            "repository": self.repository,
            "pull_request": self.pull_request,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "paths": list(self.paths),
            "kind": self.kind,
            "worker_kind": self.worker_kind,
            "correlation_id": self.correlation_id,
            "packet": self.packet,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.model is not None:
            value["model"] = self.model
        return value

    @classmethod
    def from_dict(cls, data: Any) -> "ReviewRequest":
        data = _mapping(data, "review request")
        required = {"schema", "repository", "pull_request", "base_sha", "head_sha", "paths", "kind", "worker_kind", "correlation_id", "packet", "timeout_seconds"}
        expected = required | ({"model"} if "model" in data else set())
        _keys(data, expected, "review request")
        if data["schema"] != "review-request.v1":
            raise ContractError("unsupported review request schema")
        if not isinstance(data["paths"], list):
            raise ContractError("paths must be a list")
        values = {key: data[key] for key in required - {"schema", "paths"}}
        if "model" in data:
            values["model"] = data["model"]
        return cls(paths=tuple(data["paths"]), **values)


@dataclass(frozen=True)
class ReviewArtifact:
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    worker_kind: str
    execution_location: str
    provider: str
    correlation_id: str
    findings: tuple[dict[str, Any], ...]
    finish_reason: str
    fallback: str | None = None
    latency_ms: int = 0

    def __post_init__(self) -> None:
        _text(self.repository, "repository")
        _positive_int(self.pull_request, "pull_request")
        object.__setattr__(self, "base_sha", _sha(self.base_sha, "base_sha"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha, "head_sha"))
        if self.worker_kind not in _ALLOWED_WORKERS:
            raise ContractError("worker_kind is not approved")
        _text(self.execution_location, "execution_location")
        _text(self.provider, "provider")
        _text(self.correlation_id, "correlation_id", max_length=128)
        if not isinstance(self.findings, tuple) or len(self.findings) > _MAX_FINDINGS:
            raise ContractError("findings must contain at most 50 objects")
        for index, finding in enumerate(self.findings):
            _finding(finding, index)
        if self.finish_reason not in _ALLOWED_FINISH:
            raise ContractError("finish_reason is not supported")
        if self.fallback is not None:
            _text(self.fallback, "fallback")
        if not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise ContractError("latency_ms must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "review-artifact.v1", "repository": self.repository, "pull_request": self.pull_request, "base_sha": self.base_sha, "head_sha": self.head_sha, "worker_kind": self.worker_kind, "execution_location": self.execution_location, "provider": self.provider, "correlation_id": self.correlation_id, "findings": list(self.findings), "finish_reason": self.finish_reason, "fallback": self.fallback, "latency_ms": self.latency_ms}

    @classmethod
    def from_dict(cls, data: Any) -> "ReviewArtifact":
        data = _mapping(data, "review artifact")
        _keys(data, {"schema", "repository", "pull_request", "base_sha", "head_sha", "worker_kind", "execution_location", "provider", "correlation_id", "findings", "finish_reason", "fallback", "latency_ms"}, "review artifact")
        if data["schema"] != "review-artifact.v1":
            raise ContractError("unsupported review artifact schema")
        if not isinstance(data["findings"], list):
            raise ContractError("findings must be a list")
        return cls(findings=tuple(data["findings"]), **{k: data[k] for k in ("repository", "pull_request", "base_sha", "head_sha", "worker_kind", "execution_location", "provider", "correlation_id", "finish_reason", "fallback", "latency_ms")})
