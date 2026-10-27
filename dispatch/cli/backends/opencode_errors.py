"""Report a failed OpenCode run from its own top-level error events.

Usage: opencode_errors.py STDOUT_FILE

`--format json` reports a failure as error events on stdout. Only top-level
error events are copied, because other events, tool results included, can quote
files the agent read. A 403 means the signed-in plan cannot use the model; it is
named so, and never read as a usage limit.
"""
from __future__ import annotations

import json
import sys

MAX_REPORT_BYTES = 4096
ENTITLEMENT_STATUS = 403


def error_events(lines: list[str]) -> list[dict]:
    """Top-level error events, read the way the adapter's limit detector reads them."""
    events = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "error" and isinstance(event.get("error"), dict):
            events.append(event)
    return events


def status_of(event: dict) -> object:
    error = event["error"]
    data = error.get("data")
    status = data.get("statusCode") if isinstance(data, dict) else None
    return error.get("status") if status is None else status


def report(lines: list[str]) -> str:
    events = error_events(lines)
    if not events:
        return "opencode reported no error event"
    text = "\n".join(json.dumps(event, separators=(",", ":")) for event in events)[-MAX_REPORT_BYTES:]
    if any(status_of(event) == ENTITLEMENT_STATUS for event in events):
        text += "\nthe signed-in plan is not entitled to this model (HTTP 403); this is not a usage limit"
    return text


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8", errors="replace") as stream:
        print(report(stream.read().splitlines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
