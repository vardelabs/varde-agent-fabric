"""Read an OpenCode review's event stream for what the final findings turn needs.

Usage: review_final.py session STREAM_FILE
       review_final.py has-findings STREAM_FILE
       review_final.py combine EXPLORING_STREAM FINAL_STREAM OUTPUT_FILE

`session` prints the run's session id. `has-findings` exits 0 when the stream
parses as findings under the OpenCode review parser. `combine` writes the
exploring run's events, less a line cut short and any text that is not a
findings snapshot, then the final turn's events. A summary or other prose
cannot stand as the final text, while an earlier snapshot stays, so the parser
still refuses a final turn that drops a finding it held. None prints any event
text, which can quote files the agent read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from dispatch.adapters import opencode

SESSION_ID = re.compile(r"ses_[A-Za-z0-9]{1,64}")


def session_id(raw: bytes) -> str | None:
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        candidate = event.get("sessionID") or part.get("sessionID")
        if isinstance(candidate, str) and SESSION_ID.fullmatch(candidate):
            return candidate
    return None


def has_findings(raw: bytes) -> bool:
    try:
        opencode.parse_review(raw)
    except (RuntimeError, ValueError, TypeError, KeyError):
        return False
    return True


def combined(exploring: bytes, final: bytes) -> bytes:
    kept = []
    for line in exploring.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(event, dict) and event.get("type") == "text") or has_findings(line):
            kept.append(line)
    return b"".join(line + b"\n" for line in (*kept, *final.splitlines()))


def main(argv: list[str]) -> int:
    if argv[1:2] == ["combine"] and len(argv) == 5:
        Path(argv[4]).write_bytes(combined(Path(argv[2]).read_bytes(), Path(argv[3]).read_bytes()))
        return 0
    if len(argv) != 3 or argv[1] not in ("session", "has-findings"):
        print("usage: review_final.py session|has-findings STREAM_FILE | combine EXPLORING FINAL OUTPUT",
              file=sys.stderr)
        return 2
    raw = Path(argv[2]).read_bytes()
    if argv[1] == "has-findings":
        return 0 if has_findings(raw) else 1
    found = session_id(raw)
    if found is None:
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
