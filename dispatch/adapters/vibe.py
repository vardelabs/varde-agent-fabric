"""Mistral Vibe CLI backend registration."""
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(
    name="vibe",
    roles=frozenset({"review"}),
    env=("DISPATCH_VIBE_HOME", "DISPATCH_DEADLINE_EPOCH"),
    label="Vibe",
)
