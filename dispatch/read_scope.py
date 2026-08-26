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
from pathlib import Path
from typing import Any


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
    return path


def load_private_roots(config_path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Load and validate private roots from a deployment-owned JSON overlay.

    The file must be a regular, non-symlink file. Its contents are configuration,
    never task input. Roots are sorted and de-duplicated for deterministic
    systemd argv and receipts.
    """
    path = Path(config_path)
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReadScopeError(f"read-scope config is unavailable: {path}") from exc
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ReadScopeError(f"read-scope config must be an absolute regular non-symlink file: {path}")
    if stat.st_mode & 0o022:
        raise ReadScopeError(f"read-scope config must not be group/world writable: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadScopeError(f"read-scope config is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, dict) or set(document) != {"private_roots"}:
        raise ReadScopeError("read-scope config must contain exactly private_roots")
    roots = document["private_roots"]
    if not isinstance(roots, list) or not roots:
        raise ReadScopeError("private_roots must be a non-empty list")
    return tuple(sorted({_absolute_clean_path(root, "private_roots entry") for root in roots}))


def systemd_inaccessible_paths(private_roots: tuple[str, ...], workdir: str) -> tuple[str, ...]:
    """Return deterministic systemd properties for a deny-scoped worker.

    ``workdir`` is checked only to prevent a deployment typo that hides the
    worker's own input. It does not make any claim that all other paths are
    inaccessible. The caller must run this before the backend is spawned.
    """
    work = _absolute_clean_path(workdir, "workdir")
    roots = tuple(sorted(set(private_roots)))
    if not roots:
        raise ReadScopeError("private_roots must not be empty")
    for root in roots:
        if work == root or work.startswith(root + os.sep):
            raise ReadScopeError(f"private root would hide the dispatch workdir: {root}")
    return tuple(f"--property=InaccessiblePaths={root}" for root in roots)
