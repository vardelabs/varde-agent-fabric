"""The content-writer contracts: the proposal a writer returns, never applied by the writer,
and the request and artifact that carry a write to a remote worker and back.

This module imports only `contracts/implement_contracts.py`, so an executing side can
load the two files by exact path.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
import stat
import unicodedata
from typing import Any

from contracts.implement_contracts import (MAX_BYTES, MAX_TASK_BYTES, WORKER_KIND, ContractError, _bounded,
                                           _encoded, _files, _keys, _path, _sha, _text)

SCHEMA = "write-proposal.v1"
REQUEST_SCHEMA = "write-request.v1"
ARTIFACT_SCHEMA = "write-artifact.v1"
MAX_PATHS = 50
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 900
_KEYS = {"schema", "worker_kind", "model", "paths", "changes", "response"}
_REQUEST_KEYS = {"schema", "repository", "worker_kind", "model", "correlation_id", "task", "paths", "files",
                 "timeout_seconds"}
_ARTIFACT_KEYS = {"schema", "repository", "worker_kind", "model", "correlation_id", "snapshot_sha256",
                  "execution_location", "latency_ms", "proposal"}


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
        # tuple() would turn a string into its characters and a dict into its keys.
        if not isinstance(data["paths"], list) or not isinstance(data["changes"], list):
            raise ContractError("paths and changes must be arrays")
        return cls(data["worker_kind"], data["model"], tuple(data["paths"]), tuple(data["changes"]), data["response"])


def _worker_kind(value: Any) -> str:
    if not isinstance(value, str) or not WORKER_KIND.fullmatch(value):
        raise ContractError("unsupported write worker")
    return value


def snapshot_sha256(files: tuple[dict, ...]) -> str:
    """The digest of the sent file text, in the same canonical form an implement snapshot uses."""
    return hashlib.sha256(_encoded(sorted(files, key=lambda item: item["path"]))).hexdigest()


@dataclass(frozen=True)
class WriteRequest:
    """A write for a remote worker: every named path, and the text of the named files that exist."""

    repository: str
    worker_kind: str
    model: str
    correlation_id: str
    task: str
    paths: tuple[str, ...]
    files: tuple[dict, ...]
    timeout_seconds: int

    def __post_init__(self) -> None:
        _text(self.repository, "repository", 256)
        _worker_kind(self.worker_kind)
        _text(self.model, "model", 128)
        _text(self.correlation_id, "correlation_id", 128)
        _text(self.task, "task", MAX_TASK_BYTES)
        object.__setattr__(self, "paths", write_paths(self.paths))
        if not isinstance(self.files, tuple):
            raise ContractError("files must be a tuple")
        # A write may name only files to create, so no file is sent.
        files = _files(self.files) if self.files else ()
        if any(item["path"] not in self.paths for item in files):
            raise ContractError("file outside the named paths")
        object.__setattr__(self, "files", files)
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ContractError(f"timeout_seconds must be 1 to {MAX_TIMEOUT_SECONDS}")
        _bounded(self.to_dict())

    @property
    def snapshot_sha256(self) -> str:
        return snapshot_sha256(self.files)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": REQUEST_SCHEMA, "repository": self.repository, "worker_kind": self.worker_kind,
                "model": self.model, "correlation_id": self.correlation_id, "task": self.task,
                "paths": list(self.paths), "files": list(self.files), "timeout_seconds": self.timeout_seconds}

    @classmethod
    def from_dict(cls, data: Any) -> WriteRequest:
        _keys(data, _REQUEST_KEYS)
        if data["schema"] != REQUEST_SCHEMA:
            raise ContractError("unsupported write request schema")
        if not isinstance(data["paths"], list) or not isinstance(data["files"], list):
            raise ContractError("paths and files must be arrays")
        _bounded(data)
        return cls(data["repository"], data["worker_kind"], data["model"], data["correlation_id"], data["task"],
                   tuple(data["paths"]), tuple(data["files"]), data["timeout_seconds"])


@dataclass(frozen=True)
class WriteArtifact:
    """A remote worker's proposal, bound to the identity of the request it answers."""

    repository: str
    worker_kind: str
    model: str
    correlation_id: str
    snapshot_sha256: str
    execution_location: str
    latency_ms: int
    proposal: WriteProposal

    def __post_init__(self) -> None:
        _text(self.repository, "repository", 256)
        _worker_kind(self.worker_kind)
        _text(self.model, "model", 128)
        _text(self.correlation_id, "correlation_id", 128)
        _sha(self.snapshot_sha256)
        _text(self.execution_location, "execution_location", 256)
        if type(self.latency_ms) is not int or self.latency_ms < 0:
            raise ContractError("invalid latency_ms")
        if not isinstance(self.proposal, WriteProposal):
            raise ContractError("proposal must be a write proposal")
        if (self.proposal.worker_kind, self.proposal.model) != (self.worker_kind, self.model):
            raise ContractError("proposal does not match the artifact's worker")
        _bounded(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"schema": ARTIFACT_SCHEMA, "repository": self.repository, "worker_kind": self.worker_kind,
                "model": self.model, "correlation_id": self.correlation_id, "snapshot_sha256": self.snapshot_sha256,
                "execution_location": self.execution_location, "latency_ms": self.latency_ms,
                "proposal": self.proposal.to_dict()}

    @classmethod
    def from_dict(cls, data: Any) -> WriteArtifact:
        _keys(data, _ARTIFACT_KEYS)
        if data["schema"] != ARTIFACT_SCHEMA:
            raise ContractError("unsupported write artifact schema")
        _bounded(data)
        return cls(data["repository"], data["worker_kind"], data["model"], data["correlation_id"],
                   data["snapshot_sha256"], data["execution_location"], data["latency_ms"],
                   WriteProposal.from_dict(data["proposal"]))


def verify_write_artifact(artifact: WriteArtifact, request: WriteRequest) -> None:
    """Refuse an artifact that answers a different request or edits text the request did not send."""
    identity = ("repository", "worker_kind", "model", "correlation_id", "snapshot_sha256")
    if any(getattr(artifact, field) != getattr(request, field) for field in identity):
        raise ContractError("write artifact does not match the request identity")
    if artifact.proposal.paths != request.paths:
        raise ContractError("write proposal names other paths than the request")
    sent = {item["path"]: hashlib.sha256(item["content"].encode("utf-8")).hexdigest() for item in request.files}
    for change in artifact.proposal.changes:
        if change["before_sha256"] != sent.get(change["path"]):
            raise ContractError("write change does not match the sent text")


def open_parent(root_fd: int, path: str) -> int | None:
    """A no-follow descriptor for the path's parent folder, or None when a parent does not exist."""
    fd = os.dup(root_fd)
    for part in path.split("/")[:-1]:
        try:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            os.close(fd)
            return None
        except OSError:
            os.close(fd)
            raise
        os.close(fd)
        fd = next_fd
    return fd


def _read_bounded(parent_fd: int, name: str) -> bytes | None:
    """The file's bytes, or None when it does not exist. Links, specials and oversize files are refused."""
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ContractError("a named path is a link") from exc
        raise
    try:
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ContractError("a named path is not a bounded regular file")
        with os.fdopen(os.dup(file_fd), "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    finally:
        os.close(file_fd)
    if len(data) > MAX_BYTES:
        raise ContractError("a named path is not a bounded regular file")
    data.decode("utf-8")
    return data


def read_named(root: str, path: str) -> bytes | None:
    """One named file's bytes through no-follow descriptors from root, or None when it does not exist."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_fd = open_parent(root_fd, path)
        if parent_fd is None:
            return None
        try:
            return _read_bounded(parent_fd, path.split("/")[-1])
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)


def collect_write_files(root: str, paths: tuple[str, ...]) -> tuple[dict, ...]:
    """The text of each named file that exists under root; a missing one is left for the writer to create."""
    files = []
    for path in write_paths(paths):
        try:
            data = read_named(root, path)
        except (OSError, UnicodeError) as exc:
            raise ContractError("a named path is unreadable or not UTF-8 text") from exc
        if data is not None:
            files.append({"path": path, "content": data.decode("utf-8")})
    return tuple(files)
