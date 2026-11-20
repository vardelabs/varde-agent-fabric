"""Antigravity CLI (agy) backend registration: a content writer that proposes file text."""
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(
    name="agy",
    roles=frozenset({"write"}),
    env=("DISPATCH_AGY_HOME", "DISPATCH_AGY_TIMEOUT_SECONDS"),
    label="Antigravity CLI",
)
