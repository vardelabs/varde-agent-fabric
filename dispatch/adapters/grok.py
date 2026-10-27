"""Grok backend registration: OpenCode's xAI provider under a subscription sign-in.

The backend's output is OpenCode's event stream, so OpenCode's review parser and
limit detector read it.
"""
from dispatch.adapters import opencode
from dispatch.adapters.base import Adapter

ADAPTER = Adapter(
    name="grok",
    roles=frozenset({"review"}),
    env=("DISPATCH_GROK_HOME",),
    parse_review=opencode.parse_review,
    detect_limit=opencode.detect_limit,
    label="Grok",
)
