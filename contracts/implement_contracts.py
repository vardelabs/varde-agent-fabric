"""Bounded, transport neutral source snapshot and implementation artifact contracts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Any


class ContractError(ValueError):
    """Invalid implementation wire contract or unsafe source path."""


MAX_BYTES = 320 * 1024
MAX_TASK_BYTES = 16 * 1024
# The deployment's broker and gateway allowlists grant exact models; the contract only
# bounds the shape, so a new OpenCode Go model needs no fabric release.
OPENCODE_IMPLEMENT_MODEL = re.compile(r"opencode-go/[a-z0-9][a-z0-9.-]{0,63}")

def _worker_model(worker_kind, model):
    if worker_kind not in {"codex", "opencode"}:
        raise ContractError("unsupported implement worker")
    if model is not None:
        _text(model, "model", 256)
    if worker_kind == "opencode" and (not isinstance(model, str) or not OPENCODE_IMPLEMENT_MODEL.fullmatch(model)):
        raise ContractError("unsupported OpenCode implement model")


def _encoded(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContractError("invalid UTF-8 JSON") from exc


def _bounded(value: Any) -> None:
    if len(_encoded(value)) > MAX_BYTES:
        raise ContractError("aggregate JSON exceeds 320 KiB")


def _text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not empty) or "\x00" in value:
        raise ContractError(f"{name} must be UTF-8 text without NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ContractError(f"{name} must be UTF-8 text") from exc
    if size > maximum:
        raise ContractError(f"{name} exceeds byte limit")
    return value


def _path(value: Any) -> str:
    value = _text(value, "path", 512)
    if (value.startswith("/") or "\\" in value
            or any(unicodedata.category(char) == "Cc" for char in value)
            or any(part in ("", ".", "..") or part.casefold() == ".git" for part in value.split("/"))):
        raise ContractError("unsafe relative path")
    return value


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ContractError("invalid object keys")


def _sha(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("invalid sha256")
    return value


def _files(value: Any, *, changes: bool = False) -> tuple[dict, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 50:
        raise ContractError("files must contain 1 to 50 entries")
    result = []
    seen = set()
    for item in value:
        _keys(item, {"path", "before_sha256", "after_text"} if changes else {"path", "content"})
        path = _path(item["path"])
        key = unicodedata.normalize("NFC", path)
        if key in seen:
            raise ContractError("duplicate path")
        seen.add(key)
        before = after = None
        if changes:
            before, after = item["before_sha256"], item["after_text"]
            if before is not None: _sha(before)
            if after is not None: _text(after, "after_text", MAX_BYTES, empty=True)
            if before is None and after is None:
                raise ContractError("change must create, modify, or delete a file")
        else:
            _text(item["content"], "content", MAX_BYTES, empty=True)
        if changes:
            result.append({"path": path, "before_sha256": before, "after_text": after})
        else:
            result.append({"path": path, "content": item["content"]})
    return tuple(result)


@dataclass(frozen=True)
class ImplementRequest:
    repository: str
    worker_kind: str
    correlation_id: str
    task: str
    files: tuple[dict, ...]
    timeout_seconds: int
    model: str | None = None

    def __post_init__(self):
        _text(self.repository, "repository", 256)
        _text(self.correlation_id, "correlation_id", 128)
        _worker_model(self.worker_kind, self.model)
        _text(self.task, "task", MAX_TASK_BYTES)
        if not isinstance(self.files, tuple): raise ContractError("files must be a tuple")
        object.__setattr__(self, "files", _files(self.files))
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 900:
            raise ContractError("timeout_seconds must be 1 to 900")
        _bounded(self.to_dict())

    @property
    def snapshot_sha256(self) -> str:
        canonical = sorted(self.files, key=lambda item: item["path"])
        return hashlib.sha256(_encoded(canonical)).hexdigest()

    def to_dict(self) -> dict:
        return dict(**({"model": self.model} if self.model is not None else {}), schema="implement-request.v2" if self.model is not None else "implement-request.v1", repository=self.repository, worker_kind=self.worker_kind,
                    correlation_id=self.correlation_id, task=self.task, files=list(self.files), timeout_seconds=self.timeout_seconds)

    @classmethod
    def from_dict(cls, data: Any) -> ImplementRequest:
        if not isinstance(data, dict): raise ContractError("invalid object")
        _keys(data, {"schema", "repository", "worker_kind", "correlation_id", "task", "files", "timeout_seconds"} | ({"model"} if data.get("schema") == "implement-request.v2" else set()))
        if data["schema"] not in {"implement-request.v1", "implement-request.v2"} or not isinstance(data["files"], list) or (data["schema"] == "implement-request.v2" and not isinstance(data.get("model"), str)):
            raise ContractError("invalid implement request schema")
        _bounded(data)
        return cls(data["repository"], data["worker_kind"], data["correlation_id"], data["task"], tuple(data["files"]), data["timeout_seconds"], data.get("model"))


@dataclass(frozen=True)
class ImplementArtifact:
    repository: str
    worker_kind: str
    correlation_id: str
    snapshot_sha256: str
    provider: str
    execution_location: str
    changes: tuple[dict, ...]
    model_output: str
    finish_reason: str
    fallback: None
    latency_ms: int
    model: str | None = None

    def __post_init__(self):
        _text(self.repository, "repository", 256)
        _text(self.correlation_id, "correlation_id", 128)
        _worker_model(self.worker_kind, self.model)
        _sha(self.snapshot_sha256)
        _text(self.provider, "provider", 256)
        if self.model is not None and self.provider != self.model:
            raise ContractError("provider does not match requested model")
        _text(self.execution_location, "execution_location", 256)
        if not isinstance(self.changes, tuple): raise ContractError("changes must be tuple")
        if self.changes:
            object.__setattr__(self, "changes", _files(self.changes, changes=True))
        _text(self.model_output, "model_output", MAX_BYTES, empty=True)
        if self.finish_reason != "completed" or self.fallback is not None:
            raise ContractError("only completed without fallback is supported")
        if type(self.latency_ms) is not int or self.latency_ms < 0: raise ContractError("invalid latency_ms")
        _bounded(self.to_dict())

    def to_dict(self) -> dict:
        return dict(**({"model": self.model} if self.model is not None else {}), schema="implement-artifact.v2" if self.model is not None else "implement-artifact.v1", repository=self.repository, worker_kind=self.worker_kind,
                    correlation_id=self.correlation_id, snapshot_sha256=self.snapshot_sha256, provider=self.provider,
                    execution_location=self.execution_location, changes=list(self.changes), model_output=self.model_output,
                    finish_reason=self.finish_reason, fallback=self.fallback, latency_ms=self.latency_ms)

    @classmethod
    def from_dict(cls, data: Any) -> ImplementArtifact:
        if not isinstance(data, dict): raise ContractError("invalid object")
        _keys(data, {"schema", "repository", "worker_kind", "correlation_id", "snapshot_sha256", "provider", "execution_location", "changes", "model_output", "finish_reason", "fallback", "latency_ms"} | ({"model"} if data.get("schema") == "implement-artifact.v2" else set()))
        if data["schema"] not in {"implement-artifact.v1", "implement-artifact.v2"} or not isinstance(data["changes"], list) or (data["schema"] == "implement-artifact.v2" and not isinstance(data.get("model"), str)):
            raise ContractError("invalid implement artifact schema")
        _bounded(data)
        return cls(data["repository"], data["worker_kind"], data["correlation_id"], data["snapshot_sha256"], data["provider"], data["execution_location"], tuple(data["changes"]), data["model_output"], data["finish_reason"], data["fallback"], data["latency_ms"], data.get("model"))


def collect_files(workdir: str | Path, paths: tuple[str, ...]) -> tuple[dict, ...]:
    """Read only explicit regular files through no-follow descriptors."""
    if not 1 <= len(paths) <= 50 or len({unicodedata.normalize("NFC", path) for path in paths}) != len(paths):
        raise ContractError("paths must contain 1 to 50 unique entries")
    root = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    files = []
    try:
        for path in paths:
            _path(path)
            fd = os.dup(root)
            try:
                for part in path.split("/")[:-1]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = next_fd
                file_fd = os.open(path.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    size = os.fstat(file_fd)
                    if not stat.S_ISREG(size.st_mode) or size.st_nlink != 1 or size.st_size > MAX_BYTES:
                        raise ContractError("source must be a bounded regular file")
                    chunks = []
                    remaining = MAX_BYTES + 1
                    while remaining:
                        chunk = os.read(file_fd, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    content = b"".join(chunks).decode("utf-8")
                    files.append({"path":path, "content":content})
                finally:
                    os.close(file_fd)
            except (OSError, UnicodeError) as exc:
                raise ContractError("unsafe or unreadable source file") from exc
            finally:
                os.close(fd)
    finally:
        os.close(root)
    return _files(tuple(files))
