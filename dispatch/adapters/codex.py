"""Codex backend registration and its local review fallback."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

from contracts.review_contracts import ReviewArtifact
from dispatch.adapters.base import LOCAL_REVIEW_CONTRACT, Adapter, LocalReviewFallback, caller_base_env

if TYPE_CHECKING:
    from contracts.review_contracts import ReviewRequest
    from dispatch.remote_runner import RemoteReviewMetadata


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
        try:
            payload = json.loads(local_output.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("local Codex output is not JSON") from exc
        if isinstance(payload, dict) and payload.get("schema") == "review-artifact.v1":
            artifact = ReviewArtifact.from_dict(payload)
        else:
            findings = payload.get("findings") if isinstance(payload, dict) else payload
            if not isinstance(findings, list):
                raise RuntimeError("local backend returned invalid findings")
            artifact = ReviewArtifact(
                request.repository, request.pull_request, request.base_sha,
                request.head_sha, request.worker_kind, "requester", metadata.model,
                request.correlation_id, tuple(findings), "completed",
            )
        return artifact
    finally:
        local_output.unlink(missing_ok=True)


ADAPTER = Adapter(
    name="codex",
    env=("DISPATCH_CODEX_SANDBOX",),
    local_review_fallback=LocalReviewFallback(stamp="cluster-unavailable-local-codex", run=run_local_review),
)
