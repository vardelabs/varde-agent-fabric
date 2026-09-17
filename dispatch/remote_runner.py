"""Remote-first review boundary for the public dispatch command."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import subprocess

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.gateway_client import GatewayClient, GatewayUnavailable


@dataclass(frozen=True)
class RemoteReviewMetadata:
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    correlation_id: str
    paths: tuple[str, ...]
    task: str
    diff: str | None
    workdir: str
    model: str
    local_backend: str
    output: str
    timeout_seconds: int = 300


def build_review_request(metadata: RemoteReviewMetadata) -> ReviewRequest:
    packet = metadata.task
    if metadata.diff is not None:
        packet += "\n\n--- BEGIN DIFF ---\n"
        packet += Path(metadata.diff).read_text(encoding="utf-8")
        packet += "\n--- END DIFF ---"
    return ReviewRequest(
        repository=metadata.repository,
        pull_request=metadata.pull_request,
        base_sha=metadata.base_sha,
        head_sha=metadata.head_sha,
        paths=metadata.paths,
        kind="reviewer",
        worker_kind="codex",
        correlation_id=metadata.correlation_id,
        packet=packet,
        timeout_seconds=metadata.timeout_seconds,
    )


def _validate_identity(artifact: ReviewArtifact, request: ReviewRequest) -> None:
    if (artifact.repository, artifact.pull_request, artifact.base_sha, artifact.head_sha,
            artifact.correlation_id, artifact.worker_kind) != (
                request.repository, request.pull_request, request.base_sha,
                request.head_sha, request.correlation_id, request.worker_kind):
        raise RuntimeError("local fallback artifact does not match request identity")


def _run_local_codex(metadata: RemoteReviewMetadata, request: ReviewRequest) -> ReviewArtifact:
    local_output = Path(metadata.output + ".local")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("DISPATCH_HOME", ""),
        "TMPDIR": metadata.workdir,
        "DISPATCH_TASK": metadata.task,
        "DISPATCH_WORKDIR": metadata.workdir,
        "DISPATCH_OUTPUT": str(local_output),
        "DISPATCH_MODEL": metadata.model,
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
        payload = json.loads(local_output.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("schema") == "review-artifact.v1":
            artifact = ReviewArtifact.from_dict(payload)
        else:
            findings = payload.get("findings") if isinstance(payload, dict) else payload
            if not isinstance(findings, list):
                raise RuntimeError("local backend returned invalid findings")
            artifact = ReviewArtifact(
                request.repository, request.pull_request, request.base_sha,
                request.head_sha, "codex", "requester", metadata.model,
                request.correlation_id, tuple(findings), "completed",
            )
        _validate_identity(artifact, request)
        return replace(artifact, fallback="cluster-unavailable-local-codex")
    finally:
        local_output.unlink(missing_ok=True)


def run_remote_review(metadata: RemoteReviewMetadata, *, fallback_policy: str) -> ReviewArtifact:
    request = build_review_request(metadata)
    client = GatewayClient(os.environ["DISPATCH_GATEWAY_URL"], os.environ["DISPATCH_GATEWAY_TOKEN"])
    try:
        return client.submit(request)
    except GatewayUnavailable:
        if fallback_policy == "remote-unavailable":
            artifact = _run_local_codex(metadata, request)
            _validate_identity(artifact, request)
            return replace(artifact, fallback="cluster-unavailable-local-codex")
        raise


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--local-backend", required=True)
    parser.add_argument("--fallback-policy", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--diff")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    metadata = RemoteReviewMetadata(
        args.repository, args.pull_request, args.base_sha, args.head_sha,
        args.correlation_id, tuple(args.path), args.task, args.diff,
        args.workdir, args.model, args.local_backend, args.output,
        args.timeout_seconds,
    )
    artifact = run_remote_review(metadata, fallback_policy=args.fallback_policy)
    Path(args.output).write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
