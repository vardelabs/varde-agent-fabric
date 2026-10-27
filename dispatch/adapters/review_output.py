"""Review output normalisation shared by the CLI adapters' parse_review.

Models name severities and keys in their own words and often fence their JSON.
These rules map that onto the review contract. Anything they cannot map errs
toward blocking: an unknown severity is red, and a finding with no recognised
text keeps its raw JSON as the message, so nothing is dropped silently.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Matches the review contract's per-field text limit.
FINDING_TEXT_LIMIT = 1024

_SEVERITY_ALIASES = {
    **dict.fromkeys(("critical", "high", "blocker", "blocking", "major", "red"), "red"),
    **dict.fromkeys(("medium", "moderate", "minor", "low", "warning", "yellow"), "yellow"),
    **dict.fromkeys(("info", "informational", "nit", "note", "green"), "green"),
}
_TEXT_KEYS = ("category", "confidence", "reproduction")
_FILE_KEYS = ("file", "path", "filename")
_BODY_KEYS = ("body", "description", "detail")
_WHOLE_FENCE = re.compile(r"\s*```[A-Za-z]*\n(.*)\n```\s*", re.DOTALL)


def extract_findings(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("findings"), list):
        return value["findings"]
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value)[:FINDING_TEXT_LIMIT]
    return text if text.strip() else None


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return next((text for text in (_text(item.get(key)) for key in keys) if text), None)


def _severity(value: Any) -> str:
    return _SEVERITY_ALIASES.get(value.strip().lower(), "red") if isinstance(value, str) else "red"


def _line(value: Any) -> int | None:
    if isinstance(value, str) and value.strip().isdecimal() and len(value.strip()) <= 9:
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _message(item: dict[str, Any]) -> str:
    message = _text(item.get("message"))
    if message is not None:
        return message
    joined = _text(": ".join(part for part in (_text(item.get("title")), _first_text(item, _BODY_KEYS)) if part))
    return joined or json.dumps(item, sort_keys=True, default=str)[:FINDING_TEXT_LIMIT]


def normalize_finding(item: dict[str, Any]) -> dict[str, Any]:
    """Map one model finding onto the review contract's finding shape."""
    finding: dict[str, Any] = {"severity": _severity(item.get("severity")), "message": _message(item)}
    file = _first_text(item, _FILE_KEYS)
    if file is not None:
        finding["file"] = file
    line = _line(item.get("line"))
    if line is not None:
        finding["line"] = line
    for key in _TEXT_KEYS:
        text = _text(item.get(key))
        if text is not None:
            finding[key] = text
    return finding


def normalized_findings(payload: Any) -> tuple[dict[str, Any], ...]:
    findings = extract_findings(payload)
    if findings is None or any(not isinstance(item, dict) for item in findings):
        raise RuntimeError("review output is not a findings array of objects")
    return tuple(normalize_finding(item) for item in findings)


def loads_model_json(text: str, *, allow_fenced_clean: bool = False) -> Any:
    """Parse model text as JSON, unwrapping a fence that wraps the whole text.

    A fenced empty findings list is refused unless it is the model's only text:
    earlier text can hold findings in prose that this parser cannot see, so after
    any earlier text a clean review must be strict JSON. Nothing else is rescued:
    scanning prose for a JSON value lets a quoted "[]" read as a clean review, or
    a later value replace an earlier one's findings.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        fenced = _WHOLE_FENCE.fullmatch(text)
        if fenced is None:
            raise
        try:
            value = json.loads(fenced.group(1))
        except json.JSONDecodeError:
            raise exc from None
        if extract_findings(value) == [] and not allow_fenced_clean:
            raise exc
        return value


def text_shape(text: str) -> str:
    """A content-free label for an error message, which must not echo model text."""
    if not text.strip():
        return "empty"
    return "fenced" if text.lstrip().startswith("```") else "prose"


def _prior_findings(prefix_parts: list[str]) -> list[Any]:
    prior: list[Any] = []
    for text in prefix_parts:
        try:
            findings = extract_findings(loads_model_json(text))
        except json.JSONDecodeError:
            continue
        if findings:
            prior.extend(findings)
    if not prior and prefix_parts:
        try:
            findings = extract_findings(loads_model_json("".join(prefix_parts)))
        except json.JSONDecodeError:
            findings = None
        prior.extend(findings or ())
    return prior


def streamed_findings_payload(text_parts: list[str]) -> Any:
    """The final findings value of a streamed answer, refusing one that dropped earlier findings."""
    final_text = text_parts[-1]
    only_text = sum(1 for text in text_parts if text.strip()) == 1
    try:
        parsed = loads_model_json(final_text, allow_fenced_clean=only_text)
    except json.JSONDecodeError as last_exc:
        try:
            parsed = json.loads("".join(text_parts))
        except json.JSONDecodeError:
            raise RuntimeError(f"final text is not valid JSON ({text_shape(final_text)})") from last_exc
    prior = _prior_findings(text_parts[:-1])
    final = extract_findings(parsed)
    if prior and isinstance(final, list) and not all(finding in final for finding in prior):
        raise RuntimeError("final snapshot dropped earlier findings")
    return parsed
