"""Authenticated, bounded transport for review requests."""
from __future__ import annotations

import json
import hashlib
import http.client
import ssl as _ssl
import urllib.error
import urllib.request
from typing import Callable

from contracts.review_contracts import ContractError, MAX_TRANSPORT_BYTES, ReviewArtifact, ReviewRequest
from contracts.implement_contracts import (ContractError as ImplementContractError, ImplementRequest,
                                           ImplementArtifact, MAX_BYTES as MAX_IMPLEMENT_BYTES)


class GatewayError(RuntimeError):
    """A gateway request failed closed."""


class GatewayUnavailable(GatewayError):
    """The gateway could not be reached or reported temporary unavailability."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class GatewayClient:
    def __init__(self, base_url: str, token: str, *, opener: Callable | None = None,
                 max_response_bytes: int = MAX_TRANSPORT_BYTES, ssl_context: "_ssl.SSLContext | None" = None) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("gateway must use HTTPS")
        if not token or len(token) > 4096:
            raise ValueError("gateway token is required and bounded")
        self.base_url = base_url.rstrip("/")
        self.token = token
        if opener is None:
            handlers: list = []
            if ssl_context is not None:
                handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
            handlers.append(_NoRedirect())
            opener = urllib.request.build_opener(*handlers).open
        self.opener = opener
        self.max_response_bytes = max_response_bytes

    def submit_implementation(self, request: ImplementRequest) -> ImplementArtifact:
        body = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_IMPLEMENT_BYTES:
            raise GatewayError("request exceeds transport limit")
        http_request = urllib.request.Request(
            self.base_url + "/v1/implementations", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "Accept": "application/json", "Content-Length": str(len(body))},
        )
        try:
            with self.opener(http_request, timeout=request.timeout_seconds + 45) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    if status in {502, 503, 504}:
                        raise GatewayUnavailable(f"gateway returned HTTP {status}")
                    raise GatewayError(f"gateway returned HTTP {status}")
                raw = response.read(MAX_IMPLEMENT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {502, 503, 504}:
                raise GatewayUnavailable(f"gateway returned HTTP {exc.code}") from exc
            raise GatewayError(f"gateway returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError, OSError) as exc:
            raise GatewayUnavailable("gateway unavailable") from exc
        if len(raw) > MAX_IMPLEMENT_BYTES:
            raise GatewayError("gateway response exceeds transport limit")
        try:
            artifact = ImplementArtifact.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ImplementContractError) as exc:
            raise GatewayError("gateway returned malformed implementation artifact") from exc
        if any(getattr(artifact, field) != getattr(request, field) for field in
               ("repository", "worker_kind", "correlation_id", "snapshot_sha256")):
            raise GatewayError("gateway implementation artifact does not match request identity")
        source = {item["path"]: item["content"] for item in request.files}
        for change in artifact.changes:
            before = source.get(change["path"])
            expected = hashlib.sha256(before.encode("utf-8")).hexdigest() if before is not None else None
            if change["before_sha256"] != expected or (before is None and change["after_text"] is None):
                raise GatewayError("gateway implementation change does not match snapshot")
        return artifact

    def submit(self, request: ReviewRequest) -> ReviewArtifact:
        body = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_TRANSPORT_BYTES:
            raise GatewayError("request exceeds transport limit")
        http_request = urllib.request.Request(
            self.base_url + "/v1/reviews",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json", "Content-Length": str(len(body))},
        )
        try:
            with self.opener(http_request, timeout=request.timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    status = response.status
                    if status in {502, 503, 504}:
                        raise GatewayUnavailable(f"gateway returned HTTP {status}")
                    raise GatewayError(f"gateway returned HTTP {status}")
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {502, 503, 504}:
                raise GatewayUnavailable(f"gateway returned HTTP {exc.code}") from exc
            raise GatewayError(f"gateway returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError, OSError) as exc:
            raise GatewayUnavailable("gateway unavailable") from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("gateway response exceeds transport limit")
        try:
            artifact = ReviewArtifact.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ContractError) as exc:
            raise GatewayError("gateway returned malformed artifact") from exc
        if (artifact.repository != request.repository or artifact.pull_request != request.pull_request or
                artifact.base_sha != request.base_sha or artifact.head_sha != request.head_sha or
                artifact.correlation_id != request.correlation_id or artifact.worker_kind != request.worker_kind):
            raise GatewayError("gateway artifact does not match request identity")
        return artifact
