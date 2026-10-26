"""Classify one headless Gemini CLI run: success, usage limit reached, or failure.

Usage: gemini_result.py STDOUT_FILE STDERR_FILE MODEL CLI_STATUS OUTPUT_FILE

On success the final response text is written to OUTPUT_FILE and the exit is 0.
A usage limit exits 75. Any other failure exits non-zero with a short reason.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import sys
import tempfile

LIMIT_REACHED = 75
FAILED = 1
# The CLI raises these for a 429 from the model API: TerminalQuotaError for an
# exhausted quota, RetryableQuotaError once its own retries run out.
QUOTA_ERROR_TYPES = frozenset({"TerminalQuotaError", "RetryableQuotaError"})
QUOTA_HTTP_STATUS = 429
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_REASON_CHARS = 2000


class RunFailed(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def _json_objects(text: str) -> list[dict]:
    """Every top-level JSON object in text, which may also hold plain log lines."""
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            objects.append(value)
        index = text.find("{", end)
    return objects


def _first_error(objects: list[dict]) -> dict | None:
    for value in objects:
        error = value.get("error")
        if isinstance(error, dict):
            return error
    return None


def _final_error(objects: list[dict]) -> dict | None:
    """The CLI's own error report, printed last, never a retry it logged.

    The report carries a session_id; a logged retry does not. Text the model
    steers, such as a tool error echoing a path, can print an object shaped like
    a report earlier, so only the last one counts. A run killed before it could
    report has none, and fails without a class.
    """
    reports = [value for value in objects if "session_id" in value]
    return _first_error(reports[::-1])


# Some fatal errors, such as a missing sign-in, are printed as text, not JSON.
# Only the CLI's own Fatal...Error line is quoted, never other stderr text.
_FATAL_LINE = re.compile(r"^.*\bFatal\w*Error\b.*$", re.MULTILINE)


def _fatal_line(stderr: str) -> str:
    match = _FATAL_LINE.search(stderr)
    return match.group(0).strip() if match else "no error report"


def _is_quota_error(error: dict) -> bool:
    return error.get("type") in QUOTA_ERROR_TYPES or error.get("code") == QUOTA_HTTP_STATUS


def _models(stats: object) -> dict:
    models = stats.get("models") if isinstance(stats, dict) else None
    return models if isinstance(models, dict) else {}


# A subagent does substantive work that the main turn may only relay, so it
# counts as answering. Subagents are also off in the adapter's settings.
ANSWERING_ROLES = ("main", "subagent")


def _answered_role(roles: dict, role: str) -> bool:
    stats = roles.get(role)
    requests = stats.get("totalRequests", 0) if isinstance(stats, dict) else 0
    errors = stats.get("totalErrors", 0) if isinstance(stats, dict) else 0
    return isinstance(requests, int) and isinstance(errors, int) and requests > errors


def _answered_main_turns(entry: object, roles_counted: tuple[str, ...] = ("main",)) -> bool:
    roles = entry.get("roles") if isinstance(entry, dict) else None
    return isinstance(roles, dict) and any(_answered_role(roles, role) for role in roles_counted)


def check_model_answered(result: dict, model: str) -> None:
    """Accept only a run whose every main turn the requested model answered.

    With an explicit model, and outside plan mode, the CLI reports a quota error
    instead of switching, so a quota never reaches this check. A switch here has
    another cause, and its answer came from a model nobody asked for, so it fails
    loudly and is never reported as a limit. Utility calls run on other models by
    design and do not count; subagent turns do.
    """
    models = _models(result.get("stats"))
    others = sorted(name for name, entry in models.items()
                    if name != model and _answered_main_turns(entry, ANSWERING_ROLES))
    if others:
        raise RunFailed(FAILED, f"the CLI switched main turns from {model} to {', '.join(others)}; "
                                "its answer is refused")
    if not _answered_main_turns(models.get(model)):
        raise RunFailed(FAILED, f"the requested model {model} answered no main turn; "
                                "pass a full model id this account can use")


def classify(stdout: str, stderr: str, model: str, cli_status: int) -> str:
    """Return the response text, or raise RunFailed."""
    # A run that exits 0 may still log an error its own retry cleared, so stderr
    # is read for an error report only when the run failed.
    error = _first_error(_json_objects(stdout))
    if error is None and cli_status != 0:
        error = _final_error(_json_objects(stderr))
    if error is not None and _is_quota_error(error):
        raise RunFailed(LIMIT_REACHED, f"usage limit reached: {error.get('type', 'quota error')}")
    if cli_status != 0 or error is not None:
        detail = f"{error.get('type', 'error')}: {error.get('message', '')}" if error else _fatal_line(stderr)
        raise RunFailed(cli_status or FAILED, f"gemini exited with status {cli_status}: {detail}"[:MAX_REASON_CHARS])
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RunFailed(FAILED, "gemini output is not a JSON result") from exc
    if not isinstance(result, dict) or not isinstance(result.get("response"), str) or not result["response"].strip():
        raise RunFailed(FAILED, "gemini returned no response text")
    check_model_answered(result, model)
    return result["response"]


def _read(path: str) -> str:
    with open(path, "rb") as stream:
        return stream.read(MAX_READ_BYTES).decode("utf-8", errors="replace")


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
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    stdout_path, stderr_path, model, status, output_path = argv
    try:
        response = classify(_read(stdout_path), _read(stderr_path), model, int(status))
    except RunFailed as failure:
        print(f"gemini: {failure}", file=sys.stderr)
        return failure.status if 0 < failure.status < 256 else FAILED
    _write_atomic(output_path, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
