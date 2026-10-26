"""Gemini CLI backend registration."""
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(
    name="gemini",
    roles=frozenset({"review", "implement"}),
    env=("DISPATCH_GEMINI_HOME", "DISPATCH_GEMINI_ALLOW_WRITE"),
    label="Gemini",
)
