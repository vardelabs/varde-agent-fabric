"""Gemini CLI backend registration."""
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(
    name="gemini",
    roles=frozenset({"review"}),
    env=("DISPATCH_GEMINI_HOME",),
    label="Gemini",
)
