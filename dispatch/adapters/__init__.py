"""Registry of dispatch backend adapters.

To add a backend, write `dispatch/cli/backends/<name>.sh` and a module here that
defines `ADAPTER`, then add that module to `_REGISTERED`.
"""
from __future__ import annotations

import sys

from dispatch.adapters import codex, opencode
from dispatch.adapters.base import Adapter, LocalReviewFallback

_REGISTERED = (codex, opencode)

ADAPTERS: dict[str, Adapter] = {module.ADAPTER.name: module.ADAPTER for module in _REGISTERED}


def local_review_fallback(harness: str) -> LocalReviewFallback | None:
    adapter = ADAPTERS.get(harness)
    return adapter.local_review_fallback if adapter else None


def forwarded_env(harness: str, *, fallback: bool = False) -> tuple[str, ...]:
    """Caller variables the dispatcher may pass to this backend, for a direct run or a local fallback."""
    if fallback:
        policy = local_review_fallback(harness)
        return policy.env if policy else ()
    adapter = ADAPTERS.get(harness)
    return adapter.env if adapter else ()


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] in {"env", "fallback-env"}:
        print("\n".join(forwarded_env(argv[1], fallback=argv[0] == "fallback-env")))
        return 0
    print("usage: dispatch/adapters/__main__.py env|fallback-env HARNESS", file=sys.stderr)
    return 2
