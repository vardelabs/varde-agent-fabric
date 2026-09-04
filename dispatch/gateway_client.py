"""Authenticated, bounded transport for review requests."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

from contracts.review_contracts import ContractError, ReviewArtifact, ReviewRequest


class GatewayError(RuntimeError):
    """A gateway request failed closed."""


class GatewayClient:
    def __init__(self, base_url: str, token: str, *, opener: Callable = urllib.request.urlopen, max_response_bytes: int = 128 * 1024) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("gateway must use HTTPS")
        if not token or len(token) > 4096:
            raise ValueError("gateway token is required and bounded")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = opener
        self.max_response_bytes = max_response_bytes

    def submit(self, request: ReviewRequest) -> ReviewArtifact:
        body = json.dumps(request.to_dict(), separators=(",", ":")).encode("utf-8")
        if len(body) > 256 * 1024:
            raise GatewayError("request exceeds transport limit")
        http_request = urllib.request.Request(
            self.base_url + "/v1/reviews",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json", "Content-Length": str(len(body))},
        )
        started = time.monotonic()
        try:
            with self.opener(http_request, timeout=request.timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise GatewayError(f"gateway returned HTTP {response.status}")
                raw = response.read(self.max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError("gateway unavailable") from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("gateway response exceeds transport limit")
        try:
            artifact = ReviewArtifact.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ContractError) as exc:
            raise GatewayError("gateway returned malformed artifact") from exc
        if artifact.correlation_id != request.correlation_id or artifact.head_sha != request.head_sha or artifact.base_sha != request.base_sha:
            raise GatewayError("gateway artifact does not match request identity")
        if artifact.worker_kind != request.worker_kind:
            raise GatewayError("gateway artifact worker kind does not match request")
        # Preserve measured transport latency only as a caller-side diagnostic.
        _ = int((time.monotonic() - started) * 1000)
        return artifact
