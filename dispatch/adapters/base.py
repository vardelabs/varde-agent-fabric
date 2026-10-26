"""The registration shape every dispatch backend adapter declares."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import re
from typing import TYPE_CHECKING

from contracts.review_contracts import REVIEW_WORKER_KINDS

if TYPE_CHECKING:
    from contracts.review_contracts import ReviewArtifact, ReviewRequest
    from dispatch.remote_runner import RemoteReviewMetadata


LOCAL_REVIEW_CONTRACT = (
    "Review the supplied pull request packet and diff. Return only a JSON array of findings. "
    "Each finding must be an object with severity red, yellow, or green and a non-empty message string. "
    "Optional keys are file, line, category, confidence, and reproduction. "
    "Return [] when there are no findings. Do not include prose or code fences."
)

# The prefix keeps an adapter from asking for PATH, HOME, or a provider key; the
# reserved names are the core contract, which an adapter must not override.
_FORWARDABLE_NAME = re.compile(r"DISPATCH_[A-Z0-9_]+")
_CORE_CONTRACT_NAMES = frozenset({
    "DISPATCH_TASK", "DISPATCH_WORKDIR", "DISPATCH_OUTPUT", "DISPATCH_KIND", "DISPATCH_DIFF",
    "DISPATCH_MODEL", "DISPATCH_TARGET", "DISPATCH_HARNESS_OVERRIDDEN", "DISPATCH_FALLBACK_POLICY",
    "DISPATCH_FALLBACK_HARNESS", "DISPATCH_FALLBACK_TARGET",
})


def _check_env_names(names: tuple[str, ...]) -> None:
    for name in names:
        if not _FORWARDABLE_NAME.fullmatch(name) or name in _CORE_CONTRACT_NAMES:
            raise ValueError(f"adapter cannot forward environment variable {name!r}")


LocalReviewRunner = Callable[["RemoteReviewMetadata", "ReviewRequest"], "ReviewArtifact"]


@dataclass(frozen=True)
class LocalReviewFallback:
    """How a backend serves a remote review route locally when the gateway is unavailable."""

    stamp: str
    run: LocalReviewRunner
    env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_env_names(self.env)


@dataclass(frozen=True)
class Adapter:
    """One backend: `dispatch/cli/backends/<name>.sh` plus what the dispatcher forwards to it."""

    name: str
    env: tuple[str, ...] = ()
    local_review_fallback: LocalReviewFallback | None = None

    def __post_init__(self) -> None:
        _check_env_names(self.env)
        # The fallback builds a review request named after this backend, which the
        # wire contract refuses unless it lists the worker.
        if self.local_review_fallback is not None and self.name not in REVIEW_WORKER_KINDS:
            raise ValueError(
                f"adapter {self.name!r} has a local review fallback but contracts/review_contracts.py "
                "does not accept it as a review worker"
            )


def caller_base_env(workdir: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "TMPDIR": workdir,
    }
