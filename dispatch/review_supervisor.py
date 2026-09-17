"""Policy-controlled worker routing for the requester-side supervisor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import multiprocessing
import threading

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.cluster_worker import WorkerResult
from dispatch.gateway_client import GatewayClient, GatewayError, GatewayUnavailable


class ReviewSupervisor:
    def __init__(self, gateway: GatewayClient, *, local_codex: Callable[[ReviewRequest], WorkerResult] | None = None) -> None:
        self.gateway = gateway
        self.local_codex = local_codex

    def run(self, request: ReviewRequest) -> ReviewArtifact:
        try:
            return self.gateway.submit(request)
        except GatewayUnavailable:
            if request.worker_kind == "local-gemma":
                raise
            if request.worker_kind == "codex" and self.local_codex is not None:
                result = _run_local_codex_with_timeout(self.local_codex, request)
                if not isinstance(result, WorkerResult) or not isinstance(result.artifact, ReviewArtifact):
                    raise GatewayError("local fallback returned an invalid worker result")
                artifact = result.artifact
                if (artifact.repository != request.repository or
                        artifact.pull_request != request.pull_request or
                        artifact.base_sha != request.base_sha or
                        artifact.head_sha != request.head_sha or
                        artifact.correlation_id != request.correlation_id or
                        artifact.worker_kind != request.worker_kind):
                    raise GatewayError("local fallback artifact does not match request identity")
                return replace(artifact, fallback="cluster-unavailable-local-codex")
            raise


def _local_codex_child(local_codex: Callable[[ReviewRequest], WorkerResult], request: ReviewRequest, output) -> None:
    try:
        output.send(("ok", local_codex(request)))
    except BaseException as exc:
        try:
            output.send(("error", type(exc).__name__, str(exc)[:1024]))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        output.close()


def _run_local_codex_with_timeout(local_codex: Callable[[ReviewRequest], WorkerResult], request: ReviewRequest) -> WorkerResult:
    context = multiprocessing.get_context("spawn" if threading.active_count() > 1 else "fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_local_codex_child, args=(local_codex, request, child), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(request.timeout_seconds):
            raise GatewayError("local Codex fallback timed out")
        message = parent.recv()
    except (EOFError, OSError) as exc:
        raise GatewayError("local Codex fallback failed without a result") from exc
    finally:
        parent.close()
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join()
    if message[0] == "error":
        raise GatewayError(f"local Codex fallback failed: {message[1]}: {message[2]}")
    return message[1]
