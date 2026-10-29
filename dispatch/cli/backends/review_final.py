"""Read an OpenCode review's event stream for what the final findings turn needs.

Usage: review_final.py session STREAM_FILE
       review_final.py has-findings STREAM_FILE
       review_final.py finalize EXPLORING_STREAM FINAL_STREAM OUTPUT_FILE

`session` prints the run's session id. `has-findings` exits 0 when the stream
parses as findings under the OpenCode review parser. `finalize` takes the
findings from the final turn's own text, its last part or its parts joined,
allowing a fenced clean list only as the review's only text, and refuses a
final list that drops a finding any earlier snapshot held, even one split
across text parts. It then writes the exploring run's non-text events and one
text event holding the findings as plain JSON, which every OpenCode parser
reads alike. None prints any event text, which can quote files the agent read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from dispatch.adapters import opencode
from dispatch.adapters.review_output import extract_findings, loads_model_json

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


# Text parts searched for earlier snapshots, every contiguous run of them, so the
# search stays bounded on a long stream.
MAX_SNAPSHOT_PARTS = 200


def snapshot_findings(texts: list[str]) -> list[object]:
    """Every finding an earlier snapshot held, read from any contiguous run of text parts."""
    parts = texts[-MAX_SNAPSHOT_PARTS:]
    found: list[object] = []
    for start in range(len(parts)):
        for end in range(start + 1, len(parts) + 1):
            try:
                findings = extract_findings(loads_model_json("".join(parts[start:end])))
            except ValueError:
                continue
            found.extend(findings or [])
    return found


def final_findings(exploring_texts: list[str], final_texts: list[str]) -> list[object] | None:
    """The final turn's own findings: its last text part, or its parts joined.

    A fenced clean list counts only as the review's only text, as the review
    parser rules, since earlier text can hold findings in prose.
    """
    written = [text for text in final_texts if text.strip()]
    if not written:
        return None
    only_text = len(written) == 1 and not any(text.strip() for text in exploring_texts)
    for candidate in (written[-1], "".join(written)):
        try:
            findings = extract_findings(loads_model_json(candidate, allow_fenced_clean=only_text))
        except ValueError:
            continue
        if findings is not None:
            return findings
    return None


def finalized(exploring: bytes, final: bytes) -> bytes | None:
    exploring_events = events(exploring)
    exploring_texts = [text for _, event in exploring_events if (text := text_of(event)) is not None]
    final_texts = [text for _, event in events(final) if (text := text_of(event)) is not None]
    if not final_texts:
        return None
    findings = final_findings(exploring_texts, final_texts)
    if findings is None or not all(earlier in findings for earlier in snapshot_findings(exploring_texts)):
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
