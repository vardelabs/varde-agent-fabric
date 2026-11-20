"""The proposal a content writer returns: changed text files, never applied by the writer."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Any

from contracts.implement_contracts import MAX_BYTES, ContractError, _bounded, _keys, _path, _sha, _text

SCHEMA = "write-proposal.v1"
MAX_PATHS = 50
MAX_RESPONSE_BYTES = 64 * 1024
_KEYS = {"schema", "worker_kind", "model", "paths", "changes", "response"}


def write_paths(value: Any) -> tuple[str, ...]:
    """The caller's named paths: unique, safe, relative files the writer may change."""
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_PATHS:
        raise ContractError(f"paths must contain 1 to {MAX_PATHS} entries")
    paths = tuple(_path(item) for item in value)
    if len({unicodedata.normalize("NFC", path) for path in paths}) != len(paths):
        raise ContractError("duplicate path")
    return paths


def _change(item: Any, allowed: frozenset[str]) -> dict[str, Any]:
    _keys(item, {"path", "before_sha256", "after_text"})
    path = _path(item["path"])
    if path not in allowed:
        raise ContractError("change outside the named paths")
    before, after = item["before_sha256"], item["after_text"]
    if before is not None:
        _sha(before)
    if after is not None:
        _text(after, "after_text", MAX_BYTES, empty=True)
    if before is None and after is None:
        raise ContractError("change must create, modify, or delete a file")
    return {"path": path, "before_sha256": before, "after_text": after}


@dataclass(frozen=True)
class WriteProposal:
    worker_kind: str
    model: str
    paths: tuple[str, ...]
    changes: tuple[dict[str, Any], ...]
    response: str

    def __post_init__(self) -> None:
        _text(self.worker_kind, "worker_kind", 64)
        _text(self.model, "model", 128)
        object.__setattr__(self, "paths", write_paths(self.paths))
        if not isinstance(self.changes, (list, tuple)):
            raise ContractError("changes must be a list")
        allowed = frozenset(self.paths)
        changes = tuple(_change(item, allowed) for item in self.changes)
        if len({change["path"] for change in changes}) != len(changes):
            raise ContractError("duplicate change")
        object.__setattr__(self, "changes", changes)
        _text(self.response, "response", MAX_RESPONSE_BYTES, empty=True)
        _bounded(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "worker_kind": self.worker_kind, "model": self.model,
                "paths": list(self.paths), "changes": list(self.changes), "response": self.response}

    @classmethod
    def from_dict(cls, data: Any) -> WriteProposal:
        _keys(data, _KEYS)
        if data["schema"] != SCHEMA:
            raise ContractError("unsupported write proposal schema")
        return cls(data["worker_kind"], data["model"], tuple(data["paths"]), tuple(data["changes"]), data["response"])
