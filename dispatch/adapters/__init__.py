"""The one registry of model backend adapters, shared by every role that calls a model.

To add a public backend, write `dispatch/cli/backends/<name>.sh` and a module here
that defines `ADAPTER`, then add that module to `_REGISTERED`. A deployment adds a
private backend by calling `register()` from its own module.
"""
from __future__ import annotations

import sys

from dispatch.adapters import codex, opencode
from dispatch.adapters.base import Adapter, LocalReviewFallback

_REGISTERED = (codex, opencode)

ADAPTERS: dict[str, Adapter] = {module.ADAPTER.name: module.ADAPTER for module in _REGISTERED}


def register(adapter: Adapter) -> None:
    if not isinstance(adapter, Adapter):
        raise TypeError("register() takes an Adapter")
    if adapter.name in ADAPTERS:
        raise ValueError(f"adapter {adapter.name!r} is already registered")
    ADAPTERS[adapter.name] = adapter


def resolve(role: str, harness: str) -> Adapter:
    """The adapter that serves this role for this harness; a caller never branches on the harness name."""
    adapter = ADAPTERS.get(harness)
    if adapter is None:
        raise LookupError(f"no adapter is registered for {harness!r}")
    if role not in adapter.roles:
        raise LookupError(f"adapter {harness!r} does not serve the {role!r} role")
    return adapter


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
    print("usage: dispatch/cli/adapter_env.py env|fallback-env HARNESS", file=sys.stderr)
    return 2
