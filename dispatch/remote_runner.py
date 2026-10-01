"""Remote-first review boundary for the public dispatch command."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import ssl

from contracts.review_contracts import MAX_TRANSPORT_BYTES, ReviewArtifact, ReviewRequest
from contracts.implement_contracts import ImplementRequest, ImplementArtifact, collect_files
from dispatch.fallback_policy import run_with_remote_fallback
from dispatch.gateway_client import GatewayClient, GatewayError


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


def _run_local_codex(metadata: RemoteReviewMetadata, request: ReviewRequest) -> ReviewArtifact:
    local_output = Path(metadata.output + ".local")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "TMPDIR": metadata.workdir,
        "DISPATCH_TASK": metadata.task,
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
        payload = json.loads(local_output.read_text(encoding="utf-8"))
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


_LOCAL_OPENCODE_REVIEW_CONTRACT = (
    "Review the supplied pull request packet and diff. Return only a JSON array of findings. "
    "Each finding must be an object with severity red, yellow, or green and a non-empty message string. "
    "Optional keys are file, line, category, confidence, and reproduction. "
    "Return [] when there are no findings. Do not include prose or code fences."
)


def _opencode_findings(raw: bytes) -> tuple[dict[str, object], ...]:
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

    def extract(value: object) -> list[object] | None:
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("findings"), list):
            return value["findings"]
        return None

    candidate = text_parts[-1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as last_exc:
        try:
            parsed = json.loads("".join(text_parts))
        except json.JSONDecodeError:
            raise RuntimeError("local OpenCode final text is not valid JSON") from last_exc
    final_findings = extract(parsed)
    if final_findings is None:
        raise RuntimeError("local OpenCode final text is not a findings array")

    prior_findings: list[object] = []
    for text in text_parts[:-1]:
        try:
            prior = extract(json.loads(text))
        except json.JSONDecodeError:
            continue
        if prior:
            prior_findings.extend(prior)
    if prior_findings and not all(item in final_findings for item in prior_findings):
        raise RuntimeError("local OpenCode final snapshot dropped earlier findings")
    findings: list[dict[str, object]] = []
    for item in final_findings:
        if not isinstance(item, dict):
            raise RuntimeError("local OpenCode returned a non-object finding")
        findings.append(item)
    return tuple(findings)


def _run_local_opencode(metadata: RemoteReviewMetadata, request: ReviewRequest) -> ReviewArtifact:
    opencode_home = os.environ.get("DISPATCH_OPENCODE_HOME")
    if not opencode_home:
        raise RuntimeError("local OpenCode fallback requires DISPATCH_OPENCODE_HOME")
    if metadata.diff is None:
        raise RuntimeError("local OpenCode fallback requires a review diff")

    local_output = Path(f"{metadata.output}.local")
    fallback_task = f"{_LOCAL_OPENCODE_REVIEW_CONTRACT}\n\n{metadata.task}"
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "TMPDIR": metadata.workdir,
        "DISPATCH_TASK": fallback_task,
        "DISPATCH_WORKDIR": metadata.workdir,
        "DISPATCH_OUTPUT": str(local_output),
        "DISPATCH_KIND": "review",
        "DISPATCH_MODEL": metadata.model,
        "DISPATCH_DIFF": metadata.diff,
        "DISPATCH_OPENCODE_HOME": opencode_home,
    }
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
        findings = _opencode_findings(raw)
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
    local_fallback = None
    if fallback_policy == "remote-unavailable":
        if metadata.harness == "codex":
            local_fallback = lambda: _run_local_codex(metadata, request)
        elif metadata.harness == "opencode":
            local_fallback = lambda: _run_local_opencode(metadata, request)
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
    if metadata.target != "remote" or metadata.harness not in {"codex", "opencode"} or fallback_policy != "none":
        raise GatewayError("remote implementation requires a supported worker and no fallback")
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
        print(f"varde-agent-dispatch: remote {args.kind} failed: {exc}", file=sys.stderr)
        return 4
    if isinstance(artifact, ImplementArtifact) and not artifact.changes:
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
