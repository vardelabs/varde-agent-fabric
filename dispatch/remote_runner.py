"""Remote-first review boundary for the public dispatch command."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import ssl

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from contracts.implement_contracts import ImplementRequest, ImplementArtifact, collect_files
from dispatch import adapters
from dispatch.fallback_policy import run_with_remote_fallback
from dispatch.gateway_client import GatewayClient, GatewayError


class LocalFallbackError(RuntimeError):
    """A local fallback ran after the gateway was unavailable and then failed."""


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
    harness: str = "codex"
    target: str = "remote"
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
        worker_kind=metadata.harness,
        correlation_id=metadata.correlation_id,
        packet=packet,
        timeout_seconds=metadata.timeout_seconds,
        model=metadata.model,
    )


def _gateway_client() -> GatewayClient:
    context = None
    ca_file = os.environ.get("DISPATCH_GATEWAY_CA_FILE")
    if ca_file:
        context = ssl.create_default_context(cafile=ca_file)
    return GatewayClient(os.environ["DISPATCH_GATEWAY_URL"], os.environ["DISPATCH_GATEWAY_TOKEN"], ssl_context=context)


def run_remote_review(metadata: RemoteReviewMetadata, *, fallback_policy: str) -> ReviewArtifact:
    request = build_review_request(metadata)
    if metadata.target != "remote":
        raise GatewayError("remote runner requires a remote target")
    client = _gateway_client()
    fallback = adapters.local_review_fallback(metadata.harness)
    local_fallback = None
    if fallback_policy == "remote-unavailable" and fallback is not None:
        def local_fallback() -> ReviewArtifact:
            try:
                return fallback.run(metadata, request)
            except Exception as exc:
                raise LocalFallbackError(f"gateway unavailable; local {metadata.harness} fallback failed: {exc}") from exc
    return run_with_remote_fallback(request, lambda: client.submit(request), local_fallback)


@dataclass(frozen=True)
class RemoteImplementMetadata:
    repository: str
    correlation_id: str
    paths: tuple[str, ...]
    task: str
    workdir: str
    output: str
    harness: str = "codex"
    target: str = "remote"
    timeout_seconds: int = 300
    model: str | None = None


def run_remote_implement(metadata: RemoteImplementMetadata, *, fallback_policy: str) -> ImplementArtifact:
    if metadata.target != "remote" or fallback_policy != "none":
        raise GatewayError("remote implementation requires a remote target and no fallback")
    request = ImplementRequest(metadata.repository, metadata.harness, metadata.correlation_id,
                               metadata.task, collect_files(metadata.workdir, metadata.paths), metadata.timeout_seconds, model=metadata.model)
    artifact = _gateway_client().submit_implementation(request)
    for change in artifact.changes:
        if change["before_sha256"] is None and os.path.lexists(Path(metadata.workdir) / change["path"]):
            raise GatewayError("created path already exists outside the selected snapshot")
    return artifact


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--local-backend", required=True)
    parser.add_argument("--harness", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--fallback-policy", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--diff")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--kind", choices=("review", "implement"), default="review")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.kind == "review" and (args.pull_request is None or not args.base_sha or not args.head_sha):
        parser.error("review requires pull request, base SHA and head SHA")
    if args.kind == "implement" and any(v is not None for v in (args.pull_request, args.base_sha, args.head_sha, args.diff)):
        parser.error("implement rejects review metadata")
    try:
        if args.kind == "implement":
            metadata = RemoteImplementMetadata(args.repository, args.correlation_id, tuple(args.path),
                                               args.task, args.workdir, args.output, args.harness,
                                               args.target, args.timeout_seconds, args.model)
            artifact = run_remote_implement(metadata, fallback_policy=args.fallback_policy)
        else:
            metadata = RemoteReviewMetadata(
                args.repository, args.pull_request, args.base_sha, args.head_sha,
                args.correlation_id, tuple(args.path), args.task, args.diff,
                args.workdir, args.model, args.local_backend, args.output,
                args.harness, args.target, args.timeout_seconds,
            )
            artifact = run_remote_review(metadata, fallback_policy=args.fallback_policy)
        Path(args.output).write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:
        route = "local" if isinstance(exc, LocalFallbackError) else "remote"
        print(f"varde-agent-dispatch: {route} {args.kind} failed: {exc}", file=sys.stderr)
        return 4
    if isinstance(artifact, ImplementArtifact) and not artifact.changes:
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
