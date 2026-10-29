"""Read an OpenCode review's event stream for what the final findings turn needs.

Usage: review_final.py session STREAM_FILE
       review_final.py has-findings STREAM_FILE
       review_final.py finalize EXPLORING_STREAM FINAL_STREAM OUTPUT_FILE

`session` prints the run's session id. `has-findings` exits 0 when the stream
parses as findings under the OpenCode review parser. `finalize` judges the
final turn's text after the exploring run's, by the parser's own rules, and
also refuses a final list that drops a finding the exploring text held split
across parts. It then writes the exploring run's non-text events and one text
event holding the findings as plain JSON, which every OpenCode parser reads
alike. None prints any event text, which can quote files the agent read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from dispatch.adapters import opencode
from dispatch.adapters.review_output import (
    extract_findings,
    loads_model_json,
    streamed_findings_payload,
)

SESSION_ID = re.compile(r"ses_[A-Za-z0-9]{1,64}")


def events(raw: bytes) -> list[tuple[bytes, dict]]:
    """Whole JSON object lines; a line cut short when a run was stopped is skipped."""
    found = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append((line, event))
    return found


def text_of(event: dict) -> str | None:
    part = event.get("part")
    if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
        return part["text"]
    return None


def session_id(raw: bytes) -> str | None:
    for _, event in events(raw):
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


def split_snapshot(texts: list[str]) -> list[object]:
    """Findings the exploring text holds only when its parts are read together."""
    try:
        joined = extract_findings(loads_model_json("".join(texts)))
    except ValueError:
        return []
    return joined or []


def finalized(exploring: bytes, final: bytes) -> bytes | None:
    exploring_events = events(exploring)
    exploring_texts = [text for _, event in exploring_events if (text := text_of(event)) is not None]
    final_texts = [text for _, event in events(final) if (text := text_of(event)) is not None]
    if not final_texts:
        return None
    try:
        value = streamed_findings_payload(exploring_texts + final_texts)
    except (RuntimeError, ValueError):
        return None
    findings = extract_findings(value)
    if findings is None or not all(earlier in findings for earlier in split_snapshot(exploring_texts)):
        return None
    kept = [line for line, event in exploring_events if text_of(event) is None]
    canonical = json.dumps({"type": "text", "part": {"text": json.dumps(findings)}}).encode()
    return b"".join(line + b"\n" for line in (*kept, canonical))


def main(argv: list[str]) -> int:
    if argv[1:2] == ["finalize"] and len(argv) == 5:
        output = finalized(Path(argv[2]).read_bytes(), Path(argv[3]).read_bytes())
        if output is None:
            return 1
        Path(argv[4]).write_bytes(output)
        return 0
    if len(argv) != 3 or argv[1] not in ("session", "has-findings"):
        print("usage: review_final.py session|has-findings STREAM_FILE | finalize EXPLORING FINAL OUTPUT",
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
