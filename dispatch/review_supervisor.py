"""Policy-controlled worker routing for the requester-side supervisor."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.gateway_client import GatewayClient, GatewayError
from dispatch.cluster_worker import WorkerResult


class ReviewSupervisor:
    def __init__(self, gateway: GatewayClient, *, local_codex: Callable[[ReviewRequest], WorkerResult] | None = None) -> None:
        self.gateway = gateway
        self.local_codex = local_codex

    def run(self, request: ReviewRequest) -> ReviewArtifact:
        try:
            return self.gateway.submit(request)
        except GatewayError:
            if request.worker_kind == "local-gemma":
                raise
            if request.worker_kind == "codex" and self.local_codex is not None:
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(self.local_codex, request)
                try:
                    result = future.result(timeout=request.timeout_seconds)
                except FutureTimeoutError as exc:
                    future.cancel()
                    raise GatewayError("local Codex fallback timed out") from exc
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
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
