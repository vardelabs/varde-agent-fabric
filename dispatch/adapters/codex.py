"""Codex backend registration, its review output parser, and its local review fallback."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any

from contracts.review_contracts import ReviewArtifact
from dispatch.adapters.base import LOCAL_REVIEW_CONTRACT, Adapter, LocalReviewFallback, caller_base_env
from dispatch.adapters.review_output import normalized_findings

if TYPE_CHECKING:
    from contracts.review_contracts import ReviewRequest
    from dispatch.remote_runner import RemoteReviewMetadata

# Matched only on Codex's own fatal line: model text on stderr can mention limits in passing.
RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "usage limit", "quota")


def _load(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local Codex output is not JSON") from exc


def _findings(payload: Any) -> tuple[Any, ...]:
    findings = payload.get("findings") if isinstance(payload, dict) else payload
    if not isinstance(findings, list):
        raise RuntimeError("local backend returned invalid findings")
    return tuple(findings)


def parse_review(raw: bytes) -> tuple[Any, ...]:
    """Codex writes only its final message, so it is parsed as strict JSON, then normalised."""
    return normalized_findings(_load(raw))


def detect_limit(diagnostic: str) -> str | None:
    """Codex prints its fatal error as the last line, starting with "error"."""
    lines = diagnostic.strip().splitlines()
    last = lines[-1].strip().lower() if lines else ""
    return "rate-limit" if last.startswith("error") and any(marker in last for marker in RATE_LIMIT_MARKERS) else None


def run_local_review(metadata: RemoteReviewMetadata, request: ReviewRequest) -> ReviewArtifact:
    local_output = Path(metadata.output + ".local")
    env = {
        **caller_base_env(metadata.workdir),
        "DISPATCH_TASK": f"{LOCAL_REVIEW_CONTRACT}\n\n{metadata.task}",
        "DISPATCH_WORKDIR": metadata.workdir,
        "DISPATCH_OUTPUT": str(local_output),
        "DISPATCH_MODEL": metadata.model,
        "DISPATCH_KIND": "review",
        "DISPATCH_CODEX_SANDBOX": "read-only",
    }
    if metadata.diff is not None:
        env["DISPATCH_DIFF"] = metadata.diff
    try:
        result = subprocess.run(
            [metadata.local_backend], cwd=metadata.workdir, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=request.timeout_seconds + 5,
        )
        if result.returncode != 0 or not local_output.is_file():
            raise RuntimeError("local backend failed")
        payload = _load(local_output.read_bytes())
        if isinstance(payload, dict) and payload.get("schema") == "review-artifact.v1":
            return ReviewArtifact.from_dict(payload)
        return ReviewArtifact(
            request.repository, request.pull_request, request.base_sha,
            request.head_sha, request.worker_kind, "requester", metadata.model,
            request.correlation_id, _findings(payload), "completed",
        )
    finally:
        local_output.unlink(missing_ok=True)


ADAPTER = Adapter(
    name="codex",
    roles=frozenset({"review", "implement"}),
    env=("DISPATCH_CODEX_SANDBOX",),
    local_review_fallback=LocalReviewFallback(run=run_local_review),
    parse_review=parse_review,
    detect_limit=detect_limit,
    label="Codex",
)
