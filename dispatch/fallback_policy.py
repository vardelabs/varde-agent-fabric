"""Canonical remote-unavailable fallback policy and artifact validation."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.gateway_client import GatewayUnavailable

FALLBACK_STAMP = "cluster-unavailable-local-codex"


def validate_fallback_artifact(artifact: ReviewArtifact, request: ReviewRequest) -> None:
    identity = ("repository", "pull_request", "base_sha", "head_sha", "correlation_id", "worker_kind")
    if any(getattr(artifact, field) != getattr(request, field) for field in identity):
        raise RuntimeError("local fallback artifact does not match request identity")


def run_with_remote_fallback(
    request: ReviewRequest,
    submit: Callable[[], ReviewArtifact],
    local_fallback: Callable[[], ReviewArtifact] | None = None,
) -> ReviewArtifact:
    try:
        return submit()
    except GatewayUnavailable:
        if request.worker_kind != "codex" or local_fallback is None:
            raise
        artifact = local_fallback()
        validate_fallback_artifact(artifact, request)
        return replace(artifact, fallback=FALLBACK_STAMP)
