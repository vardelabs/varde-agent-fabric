"""Policy-controlled worker routing for the requester-side supervisor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from contracts.review_contracts import ReviewRequest
from dispatch.gateway_client import GatewayClient, GatewayError
from dispatch.cluster_worker import WorkerResult


class ReviewSupervisor:
    def __init__(self, gateway: GatewayClient, *, local_codex: Callable[[ReviewRequest], WorkerResult] | None = None) -> None:
        self.gateway = gateway
        self.local_codex = local_codex

    def run(self, request: ReviewRequest):
        try:
            return self.gateway.submit(request)
        except GatewayError:
            if request.worker_kind == "local-gemma":
                raise
            if request.worker_kind == "codex" and self.local_codex is not None:
                result = self.local_codex(request).artifact
                return replace(result, fallback="cluster-unavailable-local-codex")
            raise
