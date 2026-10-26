"""Gemini CLI backend registration."""
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(name="gemini", env=("DISPATCH_GEMINI_HOME", "DISPATCH_GEMINI_ALLOW_WRITE"))
