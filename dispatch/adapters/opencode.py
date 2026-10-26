"""OpenCode backend registration and its local review fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING

from contracts.review_contracts import MAX_TRANSPORT_BYTES, ReviewArtifact
from dispatch.adapters.base import LOCAL_REVIEW_CONTRACT, Adapter, LocalReviewFallback, caller_base_env

if TYPE_CHECKING:
    from contracts.review_contracts import ReviewRequest
    from dispatch.remote_runner import RemoteReviewMetadata


def _text_parts(raw: bytes) -> list[str]:
    text_parts: list[str] = []
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise RuntimeError("local OpenCode output event is not an object")
            if event.get("type") == "text":
                part = event.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local OpenCode output is malformed JSONL") from exc
    if not text_parts:
        raise RuntimeError("local OpenCode returned no final text event")
    return text_parts


def _extract(value: object) -> list[object] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("findings"), list):
        return value["findings"]
    return None


def _final_findings(text_parts: list[str]) -> list[object]:
    try:
        parsed = json.loads(text_parts[-1])
    except json.JSONDecodeError as last_exc:
        try:
            parsed = json.loads("".join(text_parts))
        except json.JSONDecodeError:
            raise RuntimeError("local OpenCode final text is not valid JSON") from last_exc
    final_findings = _extract(parsed)
    if final_findings is None:
        raise RuntimeError("local OpenCode final text is not a findings array")
    return final_findings


def _prior_findings(text_parts: list[str]) -> list[object]:
    prior_findings: list[object] = []
    for text in text_parts[:-1]:
        try:
            prior = _extract(json.loads(text))
        except json.JSONDecodeError:
            continue
        if prior:
            prior_findings.extend(prior)
    return prior_findings


def opencode_findings(raw: bytes) -> tuple[dict[str, object], ...]:
    text_parts = _text_parts(raw)
    final_findings = _final_findings(text_parts)
    prior_findings = _prior_findings(text_parts)
    if prior_findings and not all(item in final_findings for item in prior_findings):
        raise RuntimeError("local OpenCode final snapshot dropped earlier findings")
    findings: list[dict[str, object]] = []
    for item in final_findings:
        if not isinstance(item, dict):
            raise RuntimeError("local OpenCode returned a non-object finding")
        findings.append(item)
    return tuple(findings)


def run_local_review(metadata: RemoteReviewMetadata, request: ReviewRequest) -> ReviewArtifact:
    opencode_home = os.environ.get("DISPATCH_OPENCODE_HOME")
    if not opencode_home:
        raise RuntimeError("local OpenCode fallback requires DISPATCH_OPENCODE_HOME")
    if metadata.diff is None:
        raise RuntimeError("local OpenCode fallback requires a review diff")

    local_output = Path(f"{metadata.output}.local")
    fallback_task = f"{LOCAL_REVIEW_CONTRACT}\n\n{metadata.task}"
    environment = {
        **caller_base_env(metadata.workdir),
        "DISPATCH_TASK": fallback_task,
        "DISPATCH_WORKDIR": metadata.workdir,
        "DISPATCH_OUTPUT": str(local_output),
        "DISPATCH_KIND": "review",
        "DISPATCH_MODEL": metadata.model,
        "DISPATCH_DIFF": metadata.diff,
        "DISPATCH_OPENCODE_HOME": opencode_home,
    }
    if os.environ.get("DISPATCH_OPENCODE_MODELS_PATH"):
        environment["DISPATCH_OPENCODE_MODELS_PATH"] = os.environ["DISPATCH_OPENCODE_MODELS_PATH"]
    try:
        result = subprocess.run(
            [metadata.local_backend],
            cwd=metadata.workdir,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=request.timeout_seconds + 5,
            check=False,
        )
        if result.returncode != 0 or not local_output.is_file():
            raise RuntimeError("local OpenCode fallback backend failed")
        with local_output.open("rb") as stream:
            raw = stream.read(MAX_TRANSPORT_BYTES + 1)
        if len(raw) > MAX_TRANSPORT_BYTES:
            raise RuntimeError("local OpenCode output exceeds the review artifact limit")
        findings = opencode_findings(raw)
        return ReviewArtifact(
            request.repository,
            request.pull_request,
            request.base_sha,
            request.head_sha,
            request.worker_kind,
            "requester",
            request.model or metadata.model,
            request.correlation_id,
            findings,
            "completed",
        )
    finally:
        local_output.unlink(missing_ok=True)


def _json_object(line: str) -> dict | None:
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _error_event_limit(event: dict) -> str | None:
    error = event.get("error")
    if not isinstance(error, dict):
        return None
    # v1 names the error and nests its status under data; v2 gives a dotted type and a top-level status.
    data = error.get("data")
    status = data.get("statusCode") if isinstance(data, dict) else None
    if status is None:
        status = error.get("status")
    name = error.get("name", error.get("type"))
    if not isinstance(name, str):
        return "rate-limit" if status == 429 else None
    name = name.lower().replace("_", "").replace("-", "")
    # A spent quota also answers 429, but waiting will not bring it back.
    if name.endswith(".quota"):
        return "quota"
    return "rate-limit" if status == 429 or "ratelimit" in name else None


def detect_limit(diagnostic: str) -> str | None:
    """Read only OpenCode's own top-level error events: model text can mention limits in passing."""
    for line in (line for line in reversed(diagnostic.strip().splitlines()) if line.startswith("{")):
        event = _json_object(line)
        if event is None:
            # The last event cannot be read, so an earlier limit may not be the final error.
            return None
        if event.get("type") == "error":
            return _error_event_limit(event)
    return None


# The deployment's broker and gateway allowlists grant exact models; this only
# bounds the provider/model shape, so a new OpenCode provider or model needs no
# fabric release. An aggregating provider names a model by its vendor too, as in
# <provider>/<vendor>/<name>.
IMPLEMENT_MODEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}/(?:[a-z0-9][a-z0-9-]{0,31}/)?[a-z0-9][a-z0-9.-]{0,63}")

ADAPTER = Adapter(
    name="opencode",
    roles=frozenset({"review", "implement"}),
    env=("DISPATCH_OPENCODE_HOME", "DISPATCH_OPENCODE_MODELS_PATH", "DISPATCH_OPENCODE_ALLOW_WRITE"),
    local_review_fallback=LocalReviewFallback(
        run=run_local_review,
        env=("DISPATCH_OPENCODE_HOME", "DISPATCH_OPENCODE_MODELS_PATH"),
    ),
    implement_model=IMPLEMENT_MODEL,
    parse_review=opencode_findings,
    detect_limit=detect_limit,
    label="OpenCode",
)
