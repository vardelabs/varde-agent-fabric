"""The one registry of model backend adapters, shared by every role that calls a model.

To add a public backend, write `dispatch/cli/backends/<name>.sh` and a module here
that defines `ADAPTER`, then add that module to `_REGISTERED`. A deployment adds a
private backend by calling `register()` from its own module.
"""
from __future__ import annotations

import sys

from dispatch.adapters import codex, gemini, opencode
from dispatch.adapters.base import LIMIT_KINDS, Adapter, LocalReviewFallback

_REGISTERED = (codex, opencode, gemini)

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


def detect_limit(harness: str, diagnostic: str) -> str | None:
    """The usage limit a failed run hit, from the backend's own error report, or None."""
    adapter = ADAPTERS.get(harness)
    if adapter is None or adapter.detect_limit is None:
        return None
    limit = adapter.detect_limit(diagnostic)
    return limit if limit in LIMIT_KINDS else None


# The tail holds the CLI's fatal report; the bound keeps a long run's stderr cheap to scan.
_DIAGNOSTIC_TAIL_BYTES = 64 * 1024


def _read_tail(path: str) -> str:
    with open(path, "rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - _DIAGNOSTIC_TAIL_BYTES))
        return stream.read().decode("utf-8", "replace")


def _cli_env(args: list[str]) -> int:
    print("\n".join(forwarded_env(args[1], fallback=args[0] == "fallback-env")))
    return 0


def _cli_role(args: list[str]) -> int:
    try:
        resolve(args[1], args[2])
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 3
    return 0


def _cli_limit(args: list[str]) -> int:
    print(detect_limit(args[1], _read_tail(args[2])) or "")
    return 0


_COMMANDS = {"env": (2, _cli_env), "fallback-env": (2, _cli_env), "role": (3, _cli_role), "limit": (3, _cli_limit)}


def main(argv: list[str]) -> int:
    command = _COMMANDS.get(argv[0]) if argv else None
    if command is None or len(argv) != command[0]:
        print("usage: dispatch/cli/adapter_env.py env|fallback-env HARNESS | role ROLE HARNESS | limit HARNESS FILE",
              file=sys.stderr)
        return 2
    return command[1](argv)
