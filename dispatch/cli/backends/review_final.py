"""Read an OpenCode review's event stream for what the final findings turn needs.

Usage: review_final.py session STREAM_FILE
       review_final.py has-findings STREAM_FILE
       review_final.py finalize EXPLORING_STREAM FINAL_STREAM OUTPUT_FILE

`session` prints the run's session id. `has-findings` exits 0 when the stream
parses as findings under the OpenCode review parser. `finalize` takes the
findings from the final turn's own text, its last part or its parts joined,
allowing a fenced clean list only as the review's only text, and adds back each
finding any snapshot in either run held, even one split across text parts, that
the final list does not cover. It then writes the exploring run's non-text
events and one text event holding the findings as plain JSON, which every
OpenCode parser reads alike. When it refuses, it prints only a case name:
no-text, too-many-parts, or an unparsed-* name for the final text's form
(length when the output limit cut it, then unterminated, invalid, json, fenced
or prose). None prints any event text, which can quote files the agent read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.review_contracts import MAX_FINDINGS
from dispatch.adapters import opencode
from dispatch.adapters.review_output import (
    extract_findings,
    loads_model_json,
    normalize_finding,
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


# Every contiguous run of text parts is searched for snapshots; a stream with
# more parts than this is refused rather than searched only in part.
MAX_SNAPSHOT_PARTS = 500


def snapshot_findings(texts: list[str]) -> list[object] | None:
    """Every finding a snapshot held, read from any contiguous run of text parts."""
    parts = [text for text in texts if text.strip()]
    if len(parts) > MAX_SNAPSHOT_PARTS:
        return None
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


def _location(finding: object) -> tuple[str, int] | None:
    if not isinstance(finding, dict):
        return None
    normal = normalize_finding(finding)
    return (normal["file"], normal["line"]) if "file" in normal and "line" in normal else None


def _is_checked(finding: object) -> bool:
    return isinstance(finding, dict) and normalize_finding(finding).get("category", "").strip().lower() == "checked"


def merged_findings(final: list[object], earlier: list[object]) -> list[object]:
    """The final list, plus each earlier finding it does not cover.

    A model restating an earlier finding rewords it, and the checked-files object
    grows as it reads, so neither an equal object nor a refusal fits: a finding at
    a file and line the final list already reports counts as covered. Earlier
    findings are read newest first, so the latest version of one wins, and only
    one checked-files object is kept. Nothing is added past the review
    contract's limit, which would refuse the whole review.
    """
    merged = list(final)
    covered = {location for location in map(_location, final) if location}
    has_checked = any(map(_is_checked, final))
    for finding in reversed(earlier):
        if len(merged) >= MAX_FINDINGS:
            break
        location = _location(finding)
        checked = _is_checked(finding)
        if finding in merged or location in covered or (checked and has_checked):
            continue
        merged.append(finding)
        has_checked = has_checked or checked
        if location:
            covered.add(location)
    return merged


def finish_reason(final_events: list[dict]) -> str | None:
    """The reason the final turn's last step ended, when the CLI reported one."""
    for event in reversed(final_events):
        part = event.get("part")
        if event.get("type") == "step_finish" and isinstance(part, dict):
            reason = part.get("reason")
            return reason if isinstance(reason, str) else None
    return None


def unparsed_case(final_events: list[dict], final_texts: list[str]) -> str:
    """Why the final text is not a findings list, as a fixed name that holds none of the text."""
    if finish_reason(final_events) == "length":
        return "unparsed-length"
    last = [text for text in final_texts if text.strip()][-1].strip()
    try:
        json.loads(last)
    except ValueError:
        if last.startswith(("[", "{")):
            return "unparsed-invalid" if last.endswith(("]", "}")) else "unparsed-unterminated"
        return "unparsed-fenced" if last.startswith("```") else "unparsed-prose"
    return "unparsed-json"


def finalized(exploring: bytes, final: bytes) -> tuple[bytes | None, str]:
    exploring_events = events(exploring)
    exploring_texts = [text for _, event in exploring_events if (text := text_of(event)) is not None]
    final_events = [event for _, event in events(final)]
    final_texts = [text for event in final_events if (text := text_of(event)) is not None]
    if not any(text.strip() for text in final_texts):
        return None, "no-text"
    findings = final_findings(exploring_texts, final_texts)
    if findings is None:
        return None, unparsed_case(final_events, final_texts)
    snapshots = snapshot_findings(exploring_texts + final_texts)
    if snapshots is None:
        return None, "too-many-parts"
    kept = [line for line, event in exploring_events if text_of(event) is None]
    text = json.dumps(merged_findings(findings, snapshots))
    canonical = json.dumps({"type": "text", "part": {"text": text}}).encode()
    return b"".join(line + b"\n" for line in (*kept, canonical)), "ok"


def main(argv: list[str]) -> int:
    if argv[1:2] == ["finalize"] and len(argv) == 5:
        output, case = finalized(Path(argv[2]).read_bytes(), Path(argv[3]).read_bytes())
        if output is None:
            print(case)
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
