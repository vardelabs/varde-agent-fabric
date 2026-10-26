"""Canonical remote-unavailable fallback policy and artifact validation."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch import adapters
from dispatch.gateway_client import GatewayUnavailable


def validate_fallback_artifact(artifact: ReviewArtifact, request: ReviewRequest) -> None:
    identity = ("repository", "pull_request", "base_sha", "head_sha", "correlation_id", "worker_kind")
    if any(getattr(artifact, field) != getattr(request, field) for field in identity):
        raise RuntimeError("local fallback artifact does not match request identity")
    if request.model is not None and artifact.provider != request.model:
        raise RuntimeError("local fallback artifact provider does not match selected model")


def run_with_remote_fallback(
    request: ReviewRequest,
    submit: Callable[[], ReviewArtifact],
    local_fallback: Callable[[], ReviewArtifact] | None = None,
) -> ReviewArtifact:
    try:
        return submit()
    except GatewayUnavailable:
        adapter = adapters.ADAPTERS.get(request.worker_kind)
        if adapter is None or adapter.local_review_fallback is None or local_fallback is None:
            raise
        artifact = local_fallback()
        validate_fallback_artifact(artifact, request)
        return replace(artifact, fallback=adapter.fallback_stamp)
