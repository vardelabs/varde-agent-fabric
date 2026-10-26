"""Classify one headless Gemini CLI run: success, usage limit reached, or failure.

Usage: gemini_result.py STDOUT_FILE STDERR_FILE MODEL CLI_STATUS OUTPUT_FILE

On success the final response text is written to OUTPUT_FILE and the exit is 0.
A usage limit exits 75. Any other failure exits non-zero with a short reason.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

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


def _is_quota_error(error: dict) -> bool:
    return error.get("type") in QUOTA_ERROR_TYPES or error.get("code") == QUOTA_HTTP_STATUS


def _model_api(stats: object, model: str) -> dict | None:
    models = stats.get("models") if isinstance(stats, dict) else None
    entry = models.get(model) if isinstance(models, dict) else None
    api = entry.get("api") if isinstance(entry, dict) else None
    return api if isinstance(api, dict) else None


def check_model_answered(result: dict, model: str) -> None:
    """The CLI can switch models on an error; accept only a run the requested model answered."""
    api = _model_api(result.get("stats"), model)
    if api is None:
        raise RunFailed(FAILED, f"the CLI did not use the requested model {model}; pass a full model id this account can use")
    requests, errors = api.get("totalRequests", 0), api.get("totalErrors", 0)
    if isinstance(requests, int) and isinstance(errors, int) and requests > errors:
        return
    # The requested model only failed and another model answered. A quota error
    # is what makes the CLI switch, so report the limit rather than a failure.
    raise RunFailed(LIMIT_REACHED, f"the requested model {model} failed and the CLI switched to another model")


def classify(stdout: str, stderr: str, model: str, cli_status: int) -> str:
    """Return the response text, or raise RunFailed."""
    error = _first_error(_json_objects(stdout) + _json_objects(stderr))
    if error is not None and _is_quota_error(error):
        raise RunFailed(LIMIT_REACHED, f"usage limit reached: {error.get('type', 'quota error')}")
    if cli_status != 0 or error is not None:
        detail = f"{error.get('type', 'error')}: {error.get('message', '')}" if error else "no error report"
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
    target = Path(path)
    temporary = target.with_name(f".{target.name}.gemini-result")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


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
