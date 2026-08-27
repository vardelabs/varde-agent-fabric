"""Deployment-owned read-scope policy for dispatch adapters.

This module does not discover private roots and does not accept them from a task
prompt. A deployment supplies a local JSON overlay containing private roots. On
Linux, the systemd adapter can turn those roots into ``InaccessiblePaths``
properties before the worker starts. The policy is deliberately a denylist,
not a claim that a worktree is an allowlist: a complete filesystem allowlist
requires a mount namespace or equivalent host boundary. If the worker runs under
the same uid as the dispatcher, deployment must use root-owned policy files or a
separate worker uid so the worker cannot rewrite policy for a later spawn.
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
    """Normalize and validate an absolute path for systemd compatibility.

    Rejects paths with unsafe characters, normalizes to realpath to eliminate
    aliases like //path, and ensures the path is absolute and not the root.
    """
    if not isinstance(value, str) or not value:
        raise ReadScopeError(f"{field} must be a non-empty absolute path")
    # Normalize first to handle .. and .
    path = os.path.normpath(value)
    # Then resolve to eliminate aliases like //path on POSIX
    # Use realpath to canonicalize, but we don't require it to exist yet
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        resolved = path
    if not os.path.isabs(resolved) or resolved == "/" or resolved != value:
        raise ReadScopeError(
            f"{field} must be normalized, absolute, and must not be filesystem root: {value!r}"
        )
    if not _SYSTEMD_PATH_RE.fullmatch(resolved):
        raise ReadScopeError(
            f"{field} contains characters not safe for systemd path properties: {value!r}"
        )
    return resolved


def _check_no_symlinks(path: Path) -> None:
    """Verify no component of path is a symlink."""
    for component in (path, *path.parents):
        if os.path.islink(component):
            raise ReadScopeError(f"path contains a symlink component: {path}")


def _check_ancestor_trust(path: Path) -> None:
    """Verify all ancestors are owned by root or euid and not group/world writable.

    This establishes a trusted path from root to the leaf, preventing substitution
    attacks by untrusted users who control intermediate directories.
    """
    euid = os.geteuid()
    for component in path.parents:
        try:
            component_stat = os.lstat(component)
        except OSError as exc:
            raise ReadScopeError(f"ancestor is unavailable: {component}") from exc
        if not stat.S_ISDIR(component_stat.st_mode):
            raise ReadScopeError(f"ancestor is not a directory: {component}")
        if component_stat.st_uid not in (0, euid):
            raise ReadScopeError(f"ancestor is not owned by root or euid: {component}")
        if component_stat.st_mode & 0o022:
            raise ReadScopeError(f"ancestor is group/world writable: {component}")


def _validated_private_root(value: Any) -> str:
    """Validate a private root path for systemd InaccessiblePaths.

    The root must be:
    - A normalized, absolute path safe for systemd properties
    - A real directory (not a symlink)
    - Not world-writable (so it cannot be swapped by untrusted users)
    - Reachable only through trusted ancestors (root or euid owned, not writable)

    Note: leaf ownership is not required to support tenant isolation (hiding
    directories owned by other users), but the leaf must not be world-writable
    so that untrusted users cannot substitute it.
    """
    root = _absolute_clean_path(value, "private_roots entry")
    path = Path(root)
    _check_no_symlinks(path)
    _check_ancestor_trust(path)
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

    The config file and all its ancestors must be owned by root or the effective
    user, and not group/world writable, establishing a trusted path from root.
    """
    path = _absolute_clean_path(str(config_path), "config_path")
    path_obj = Path(path)
    _check_no_symlinks(path_obj)
    _check_ancestor_trust(path_obj)
    try:
        fd = os.open(
            path_obj, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    except OSError as exc:
        raise ReadScopeError(f"read-scope config is unavailable: {path}") from exc
    # Transfer ownership immediately to prevent double-close on exception paths
    fd_transferred = False
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
        stream = os.fdopen(fd, encoding="utf-8")
        fd_transferred = True  # fd is now owned by stream
        try:
            document = json.loads(stream.read(_MAX_CONFIG_BYTES + 1))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ReadScopeError(f"read-scope config is not valid UTF-8 JSON: {path}") from exc
        finally:
            stream.close()
    finally:
        if not fd_transferred:
            os.close(fd)
    if not isinstance(document, dict):
        raise ReadScopeError("read-scope config must be a JSON object")
    allowed_keys = {"private_roots", "version"}
    if not set(document).issubset(allowed_keys) or "private_roots" not in document:
        raise ReadScopeError("read-scope config must contain private_roots and optionally version")

    # Validate version field if present
    if "version" in document:
        version = document["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version != 1:
            raise ReadScopeError("version must be the supported version 1")

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
