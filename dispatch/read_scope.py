"""Deployment-owned read-scope policy for dispatch adapters.

This module does not discover private roots and does not accept them from a task
prompt. A deployment supplies a local JSON overlay containing private roots. On
Linux, the systemd adapter can turn those roots into ``InaccessiblePaths``
properties before the worker starts. The policy is deliberately a denylist,
not a claim that a worktree is an allowlist: a complete filesystem allowlist
requires a mount namespace or equivalent host boundary.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any


# systemd InaccessiblePaths is a space-separated list property; paths must not
# contain whitespace, control characters, quotes, or backslash to avoid silent
# fragment drops or directive injection.
_SYSTEMD_PATH_RE = re.compile(r'^[A-Za-z0-9._+/:-]+$')

# Bounded config read to prevent unbounded memory consumption.
_MAX_CONFIG_BYTES = 64 * 1024


class ReadScopeError(ValueError):
    """The deployment-owned read-scope policy is unsafe or malformed."""


def _absolute_clean_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReadScopeError(f"{field} must be a non-empty absolute path")
    path = os.path.normpath(value)
    if not os.path.isabs(path) or path == "/" or path != value:
        raise ReadScopeError(
            f"{field} must be normalized, absolute, and must not be filesystem root: {value!r}"
        )
    if not _SYSTEMD_PATH_RE.match(path):
        raise ReadScopeError(
            f"{field} contains characters not safe for systemd path properties: {value!r}"
        )
    return path


def _check_no_symlinks(path: Path) -> None:
    """Verify no component of path is a symlink."""
    for component in (path, *path.parents):
        if component == Path("/"):
            continue
        try:
            if os.path.islink(component):
                raise ReadScopeError(f"path contains a symlink component: {path}")
        except OSError as exc:
            raise ReadScopeError(f"path component is unavailable: {component}") from exc


def _validated_private_root(value: Any) -> str:
    root = _absolute_clean_path(value, "private_roots entry")
    path = Path(root)
    _check_no_symlinks(path)
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise ReadScopeError(f"private root is unavailable: {root}") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ReadScopeError(f"private root must be a directory: {root}")
    if root_stat.st_mode & 0o002:
        raise ReadScopeError(f"private root must not be world writable: {root}")
    return root


def load_private_roots(config_path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Load and validate private roots from a deployment-owned JSON overlay.

    The file must be a regular, non-symlink file. Its contents are configuration,
    never task input. Roots are sorted and de-duplicated for deterministic
    systemd argv and receipts.
    """
    path = _absolute_clean_path(str(config_path), "config_path")
    if not path:
        raise ReadScopeError("config_path must be a non-empty absolute path")
    path_obj = Path(path)
    _check_no_symlinks(path_obj)
    try:
        fd = os.open(
            path_obj, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    except OSError as exc:
        raise ReadScopeError(f"read-scope config is unavailable: {path}") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ReadScopeError(f"read-scope config must be a regular file: {path}")
        if file_stat.st_size > _MAX_CONFIG_BYTES:
            raise ReadScopeError(f"read-scope config exceeds {_MAX_CONFIG_BYTES} bytes: {path}")
        if file_stat.st_mode & 0o022:
            raise ReadScopeError(f"read-scope config must not be group/world writable: {path}")
        if file_stat.st_uid not in (0, os.geteuid()):
            raise ReadScopeError(f"read-scope config is not owned by euid or root: {path}")
        try:
            with os.fdopen(fd, encoding="utf-8") as stream:
                fd = -1
                document = json.loads(stream.read(_MAX_CONFIG_BYTES + 1))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReadScopeError(f"read-scope config is not valid UTF-8 JSON: {path}") from exc
        fd = -1
    finally:
        if fd != -1:
            os.close(fd)
    if not isinstance(document, dict):
        raise ReadScopeError("read-scope config must be a JSON object")
    allowed_keys = {"private_roots", "version"}
    if not set(document).issubset(allowed_keys) or "private_roots" not in document:
        raise ReadScopeError("read-scope config must contain private_roots and optionally version")
    roots = document["private_roots"]
    if not isinstance(roots, list) or not roots:
        raise ReadScopeError("private_roots must be a non-empty list")
    return tuple(sorted({_validated_private_root(root) for root in roots}))


def systemd_inaccessible_paths(private_roots: tuple[str, ...], workdir: str) -> tuple[str, ...]:
    """Return deterministic systemd properties for a deny-scoped worker.

    ``workdir`` is checked only to prevent a deployment typo that hides the
    worker's own input. It does not make any claim that all other paths are
    inaccessible. The caller must run this before the backend is spawned.
    """
    work = _absolute_clean_path(workdir, "workdir")
    if not os.path.isdir(work) or os.path.realpath(work) != work:
        raise ReadScopeError("workdir must be an existing directory without symlink components")
    roots = tuple(sorted({_validated_private_root(root) for root in private_roots}))
    if not roots:
        raise ReadScopeError("private_roots must not be empty")
    for root in roots:
        if work == root or work.startswith(root + os.sep):
            raise ReadScopeError(f"private root would hide the dispatch workdir: {root}")
    return tuple(f"--property=InaccessiblePaths={root}" for root in roots)
