"""Strict, transport-neutral contracts for one bounded remote review."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when a review contract is malformed or exceeds a bound."""


_MAX_TEXT = 256
_MAX_PACKET_BYTES = 256 * 1024
_MAX_FINDINGS = 50
_ALLOWED_KINDS = {"reviewer"}
_ALLOWED_WORKERS = {"local-gemma", "codex"}
_ALLOWED_FINISH = {"completed", "unavailable", "malformed", "timeout", "truncated"}


def _text(value: Any, field: str, *, max_length: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ContractError(f"{field} must be a non-empty string of at most {max_length} characters")
    return value


def _sha(value: Any, field: str) -> str:
    value = _text(value, field, max_length=64)
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ContractError(f"{field} must be a 40-character hexadecimal SHA")
    return value.lower()


def _keys(data: Mapping[str, Any], expected: set[str], field: str) -> None:
    unknown = set(data) - expected
    missing = expected - set(data)
    if unknown or missing:
        raise ContractError(f"{field} keys invalid, missing={sorted(missing)}, unknown={sorted(unknown)}")


@dataclass(frozen=True)
class ReviewRequest:
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    paths: tuple[str, ...]
    kind: str
    worker_kind: str
    correlation_id: str
    packet: str
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        _text(self.repository, "repository")
        if self.pull_request < 1:
            raise ContractError("pull_request must be positive")
        _sha(self.base_sha, "base_sha")
        _sha(self.head_sha, "head_sha")
        if not self.paths or len(self.paths) > 100 or any(not isinstance(p, str) or not p for p in self.paths):
            raise ContractError("paths must contain between 1 and 100 non-empty strings")
        if self.kind not in _ALLOWED_KINDS:
            raise ContractError("kind is not approved")
        if self.worker_kind not in _ALLOWED_WORKERS:
            raise ContractError("worker_kind is not approved")
        _text(self.correlation_id, "correlation_id", max_length=128)
        if not isinstance(self.packet, str) or not self.packet or len(self.packet.encode()) > _MAX_PACKET_BYTES:
            raise ContractError("packet must be non-empty and at most 262144 UTF-8 bytes")
        if not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 900:
            raise ContractError("timeout_seconds must be an integer from 1 to 900")

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewRequest":
        _keys(data, {"schema", "repository", "pull_request", "base_sha", "head_sha", "paths", "kind", "worker_kind", "correlation_id", "packet", "timeout_seconds"}, "review request")
        if data["schema"] != "review-request.v1":
            raise ContractError("unsupported review request schema")
        if not isinstance(data["paths"], list):
            raise ContractError("paths must be a list")
        return cls(paths=tuple(data["paths"]), **{k: data[k] for k in ("repository", "pull_request", "base_sha", "head_sha", "kind", "worker_kind", "correlation_id", "packet", "timeout_seconds")})


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
        if self.pull_request < 1:
            raise ContractError("pull_request must be positive")
        _sha(self.base_sha, "base_sha")
        _sha(self.head_sha, "head_sha")
        if self.worker_kind not in _ALLOWED_WORKERS:
            raise ContractError("worker_kind is not approved")
        _text(self.execution_location, "execution_location")
        _text(self.provider, "provider")
        _text(self.correlation_id, "correlation_id", max_length=128)
        if not isinstance(self.findings, tuple) or len(self.findings) > _MAX_FINDINGS or any(not isinstance(f, dict) for f in self.findings):
            raise ContractError("findings must contain at most 50 objects")
        if self.finish_reason not in _ALLOWED_FINISH:
            raise ContractError("finish_reason is not supported")
        if self.fallback is not None:
            _text(self.fallback, "fallback")
        if not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise ContractError("latency_ms must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "review-artifact.v1", "repository": self.repository, "pull_request": self.pull_request, "base_sha": self.base_sha, "head_sha": self.head_sha, "worker_kind": self.worker_kind, "execution_location": self.execution_location, "provider": self.provider, "correlation_id": self.correlation_id, "findings": list(self.findings), "finish_reason": self.finish_reason, "fallback": self.fallback, "latency_ms": self.latency_ms}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewArtifact":
        _keys(data, {"schema", "repository", "pull_request", "base_sha", "head_sha", "worker_kind", "execution_location", "provider", "correlation_id", "findings", "finish_reason", "fallback", "latency_ms"}, "review artifact")
        if data["schema"] != "review-artifact.v1":
            raise ContractError("unsupported review artifact schema")
        if not isinstance(data["findings"], list):
            raise ContractError("findings must be a list")
        return cls(findings=tuple(data["findings"]), **{k: data[k] for k in ("repository", "pull_request", "base_sha", "head_sha", "worker_kind", "execution_location", "provider", "correlation_id", "finish_reason", "fallback", "latency_ms")})
