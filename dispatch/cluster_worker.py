"""Cluster worker boundary for approved review workers.

The worker receives a validated packet and returns an artifact. It has no
GitHub client, repository credentials, or outward-action methods by design.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from contracts.review_contracts import ReviewArtifact, ReviewRequest


@dataclass(frozen=True)
class WorkerResult:
    artifact: ReviewArtifact


class ClusterReviewWorker:
    """Adapt a private model callable to the public review contract."""

    def __init__(self, model: Callable[[str], list[dict]], *, provider: str, location: str = "cluster") -> None:
        self.model = model
        self.provider = provider
        self.location = location

    def execute(self, request: ReviewRequest) -> WorkerResult:
        started = monotonic()
        findings = self.model(request.packet)
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise ValueError("model returned findings outside the bounded shape")
        artifact = ReviewArtifact(
            repository=request.repository,
            pull_request=request.pull_request,
            base_sha=request.base_sha,
            head_sha=request.head_sha,
            worker_kind=request.worker_kind,
            execution_location=self.location,
            provider=self.provider,
            correlation_id=request.correlation_id,
            findings=tuple(findings),
            finish_reason="completed",
            latency_ms=max(0, int((monotonic() - started) * 1000)),
        )
        return WorkerResult(artifact)
