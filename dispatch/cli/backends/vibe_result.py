"""Classify one headless Vibe CLI run: success, usage limit reached, out of time, or failure.

Usage: vibe_result.py STDOUT_FILE EXPORT_FILE CLI_STATUS OUTPUT_FILE

On success the final assistant text is written to OUTPUT_FILE and the exit is 0.
A usage limit exits 75 and a run stopped at its time limit exits 124. Any other
failure exits 1 with a short reason.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

LIMIT_REACHED = 75
OUT_OF_TIME = 124
FAILED = 1
MAX_READ_BYTES = 8 * 1024 * 1024
LIMIT_ERROR_CODES = frozenset({"rate_limit"})
# Only these tokens from the export reach a failure reason: its error message
# can quote a provider response.
_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


class RunFailed(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def _token(value: object) -> str:
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= _TOKEN_CHARS:
        return value
    return "unlisted"


def _load_json(path: str) -> object:
    with open(path, "rb") as stream:
        return json.loads(stream.read(MAX_READ_BYTES).decode("utf-8", errors="replace"))


def _export(path: str) -> dict:
    """The CLI's own run record, which it writes on every exit; model text cannot shape it."""
    try:
        export = _load_json(path)
    except (OSError, ValueError) as exc:
        raise RunFailed(FAILED, "vibe wrote no readable run export") from exc
    if not isinstance(export, dict):
        raise RunFailed(FAILED, "vibe run export is malformed")
    return export


def check_finished(export: dict, cli_status: int) -> None:
    outcome = export.get("outcome")
    error = export.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if outcome == "deadline":
        raise RunFailed(OUT_OF_TIME, "vibe stopped at its time limit")
    if code in LIMIT_ERROR_CODES:
        raise RunFailed(LIMIT_REACHED, f"usage limit reached: {code}")
    if outcome != "finished" or error is not None or cli_status != 0 or export.get("exit_code") != 0:
        raise RunFailed(FAILED, f"vibe exited with status {cli_status}: outcome={_token(outcome)} "
                                f"error={_token(code) if error is not None else 'none'}")


def final_text(stdout_path: str) -> str:
    """The text of the transcript's last entry, which must be an assistant message."""
    try:
        transcript = _load_json(stdout_path)
    except (OSError, ValueError) as exc:
        raise RunFailed(FAILED, "vibe output is not a JSON transcript") from exc
    last = transcript[-1] if isinstance(transcript, list) and transcript else None
    if not isinstance(last, dict) or last.get("role") != "assistant" or not isinstance(last.get("content"), list):
        raise RunFailed(FAILED, "vibe transcript does not end with an assistant message")
    text = "".join(part["text"] for part in last["content"]
                   if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
    if not text.strip():
        raise RunFailed(FAILED, "vibe returned no response text")
    return text


def _write_atomic(path: str, text: str) -> None:
    # mkstemp gives an unpredictable name, created exclusively at 0600, so the
    # output is never readable by other users and no planted link is followed.
    target = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    stdout_path, export_path, status, output_path = argv
    try:
        check_finished(_export(export_path), int(status))
        response = final_text(stdout_path)
    except RunFailed as failure:
        print(f"vibe: {failure}", file=sys.stderr)
        return failure.status
    _write_atomic(output_path, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
