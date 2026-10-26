"""The registration shape every model backend adapter declares."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.review_contracts import ReviewArtifact, ReviewRequest
    from dispatch.remote_runner import RemoteReviewMetadata


LOCAL_REVIEW_CONTRACT = (
    "Review the supplied pull request packet and diff. Return only a JSON array of findings. "
    "Each finding must be an object with severity red, yellow, or green and a non-empty message string. "
    "Optional keys are file, line, category, confidence, and reproduction. "
    "Return [] when there are no findings. Do not include prose or code fences."
)

ADAPTER_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
# A role is a plain name, so a new role needs no change here.
_ROLE_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
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
ReviewParser = Callable[[bytes], "tuple[dict[str, object], ...]"]
# Returns "quota", "rate-limit", or None from the backend's own error report.
LimitDetector = Callable[[str], "str | None"]
LIMIT_KINDS = frozenset({"quota", "rate-limit"})


@dataclass(frozen=True)
class LocalReviewFallback:
    """How a backend serves a remote review route locally when the gateway is unavailable."""

    run: LocalReviewRunner
    env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_env_names(self.env)


@dataclass(frozen=True)
class Adapter:
    """One model backend and the roles it can serve, for every caller that resolves one."""

    name: str
    roles: frozenset[str] = frozenset()
    env: tuple[str, ...] = ()
    local_review_fallback: LocalReviewFallback | None = None
    # The model shape an implement request must name for this backend, when it constrains one.
    implement_model: re.Pattern[str] | None = None
    # Turns the backend's raw review output into findings, for any caller that runs it.
    parse_review: ReviewParser | None = None
    detect_limit: LimitDetector | None = None
    label: str = ""

    def __post_init__(self) -> None:
        if not ADAPTER_NAME.fullmatch(self.name):
            raise ValueError(f"adapter name {self.name!r} is invalid")
        object.__setattr__(self, "roles", frozenset(self.roles))
        invalid = sorted(role for role in self.roles if not _ROLE_NAME.fullmatch(role))
        if invalid:
            raise ValueError(f"adapter {self.name!r} declares invalid roles {invalid}")
        _check_env_names(self.env)
        if self.local_review_fallback is not None and "review" not in self.roles:
            raise ValueError(f"adapter {self.name!r} has a local review fallback but does not declare the review role")
        if self.implement_model is not None and "implement" not in self.roles:
            raise ValueError(f"adapter {self.name!r} constrains implement models but does not declare the implement role")
        if self.parse_review is not None and "review" not in self.roles:
            raise ValueError(f"adapter {self.name!r} parses review output but does not declare the review role")
        if not self.label:
            object.__setattr__(self, "label", self.name)

    @property
    def fallback_stamp(self) -> str:
        return f"cluster-unavailable-local-{self.name}"


def caller_base_env(workdir: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "TMPDIR": workdir,
    }
