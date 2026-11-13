"""Authenticated, bounded transport for review requests."""
from __future__ import annotations

import json
import hashlib
import http.client
import re
import ssl as _ssl
import subprocess
import time
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


HTTP_400_DIAGNOSTIC_BYTES = 512
UNAVAILABLE_STATUSES = frozenset({502, 503, 504})
# "cluster" is the old name, accepted until every gateway writes "remote".
REMOTE_EXECUTION_LOCATIONS = frozenset({"remote", "cluster"})
REVIEWS_PATH = "/v1/reviews"
IMPLEMENTATIONS_PATH = "/v1/implementations"


def _bad_request_error(body: bytes) -> GatewayError:
    excerpt = body[:HTTP_400_DIAGNOSTIC_BYTES].decode("utf-8", "replace")
    excerpt = " ".join(
        "".join(char if char.isprintable() else " " for char in excerpt).split()
    )
    suffix = "…" if len(body) > HTTP_400_DIAGNOSTIC_BYTES else ""
    detail = f"; gateway diagnostic: {excerpt[:HTTP_400_DIAGNOSTIC_BYTES]}{suffix}" if excerpt else ""
    return GatewayError(f"gateway rejected review request (HTTP 400){detail}")


def _status_error(status: int, body: bytes, *, diagnose_bad_request: bool) -> GatewayError:
    if status in UNAVAILABLE_STATUSES:
        return GatewayUnavailable(f"gateway returned HTTP {status}")
    if status == 400 and diagnose_bad_request:
        return _bad_request_error(body)
    return GatewayError(f"gateway returned HTTP {status}")


class _GatewayRequests:
    """The request contracts both transports share; a transport supplies `_exchange`."""

    max_response_bytes: int

    def _exchange(self, path: str, body: bytes, timeout: float, limit: int) -> tuple[int, bytes]:
        raise NotImplementedError

    def submit_implementation(self, request: ImplementRequest) -> ImplementArtifact:
        body = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_IMPLEMENT_BYTES:
            raise GatewayError("request exceeds transport limit")
        status, raw = self._exchange(IMPLEMENTATIONS_PATH, body, request.timeout_seconds + 45, MAX_IMPLEMENT_BYTES)
        if status != 200:
            raise _status_error(status, raw, diagnose_bad_request=False)
        if len(raw) > MAX_IMPLEMENT_BYTES:
            raise GatewayError("gateway response exceeds transport limit")
        try:
            artifact = ImplementArtifact.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ImplementContractError) as exc:
            raise GatewayError("gateway returned malformed implementation artifact") from exc
        if any(getattr(artifact, field) != getattr(request, field) for field in
               ("repository", "worker_kind", "correlation_id", "snapshot_sha256", "model")):
            raise GatewayError("gateway implementation artifact does not match request identity")
        if artifact.execution_location not in REMOTE_EXECUTION_LOCATIONS:
            raise GatewayError("gateway implementation execution location mismatch")
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
        status, raw = self._exchange(REVIEWS_PATH, body, request.timeout_seconds, self.max_response_bytes)
        if status != 200:
            raise _status_error(status, raw, diagnose_bad_request=True)
        if len(raw) > self.max_response_bytes:
            raise GatewayError("gateway response exceeds transport limit")
        try:
            artifact = ReviewArtifact.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ContractError) as exc:
            raise GatewayError("gateway returned malformed artifact") from exc
        if (artifact.repository != request.repository or artifact.pull_request != request.pull_request or
                artifact.base_sha != request.base_sha or artifact.head_sha != request.head_sha or
                artifact.correlation_id != request.correlation_id or artifact.worker_kind != request.worker_kind or
                (request.model is not None and artifact.provider != request.model)):
            raise GatewayError("gateway artifact does not match request identity")
        return artifact


def _require_https(base_url: str) -> str:
    if not base_url.startswith("https://"):
        raise ValueError("gateway must use HTTPS")
    return base_url.rstrip("/")


class GatewayClient(_GatewayRequests):
    """Sends each request straight to the gateway with the caller's own bearer token."""

    def __init__(self, base_url: str, token: str, *, opener: Callable | None = None,
                 max_response_bytes: int = MAX_TRANSPORT_BYTES, ssl_context: "_ssl.SSLContext | None" = None) -> None:
        self.base_url = _require_https(base_url)
        if not token or len(token) > 4096:
            raise ValueError("gateway token is required and bounded")
        self.token = token
        if opener is None:
            handlers: list = []
            if ssl_context is not None:
                handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
            handlers.append(_NoRedirect())
            opener = urllib.request.build_opener(*handlers).open
        self.opener = opener
        self.max_response_bytes = max_response_bytes

    def _exchange(self, path: str, body: bytes, timeout: float, limit: int) -> tuple[int, bytes]:
        http_request = urllib.request.Request(
            self.base_url + path, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "Accept": "application/json", "Content-Length": str(len(body))},
        )
        try:
            with self.opener(http_request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read((limit if status == 200 else HTTP_400_DIAGNOSTIC_BYTES) + 1)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(HTTP_400_DIAGNOSTIC_BYTES + 1)
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError, OSError) as exc:
            raise GatewayUnavailable("gateway unavailable") from exc
        return status, raw


JOB_PATHS = {REVIEWS_PATH: "/v1/jobs/reviews", IMPLEMENTATIONS_PATH: "/v1/jobs/implementations"}
JOB_ID = re.compile(r"[0-9a-f]{32}")
# One broker call must outlast the broker's own upstream timeout, so its refusal reaches us as a reply.
BROKER_CALL_SECONDS = 60
POLL_SECONDS = 5
# A job can wait behind the gateway's other running jobs before its own timeout starts.
JOB_QUEUE_ALLOWANCE_SECONDS = 600


class BrokeredGatewayClient(_GatewayRequests):
    """Reaches the gateway through a credential broker that holds the bearer token.

    The broker command takes `http <secret> <METHOD> <url> [-H header] [-d -]` and prints
    `{"status": <int>, "body": <text>}`. A broker bounds each call to a short upstream
    timeout, so a request starts as a gateway job and the client polls it to the end.
    """

    def __init__(self, base_url: str, broker: str, secret: str, *, run: Callable = subprocess.run,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 max_response_bytes: int = MAX_TRANSPORT_BYTES) -> None:
        self.base_url = _require_https(base_url)
        if not broker.startswith("/"):
            raise ValueError("gateway broker must be an absolute path")
        if not secret or any(char.isspace() for char in secret):
            raise ValueError("gateway broker secret is required and has no whitespace")
        self.broker, self.secret = broker, secret
        self.run, self.sleep, self.clock = run, sleep, clock
        self.max_response_bytes = max_response_bytes

    def _call(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        argv = [self.broker, "http", self.secret, method, self.base_url + path, "-H", "Accept: application/json"]
        if body is not None:
            argv += ["-H", "Content-Type: application/json", "-d", "-"]
        try:
            completed = self.run(argv, input=None if body is None else body.decode("utf-8"),
                                 capture_output=True, text=True, timeout=BROKER_CALL_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GatewayUnavailable("gateway broker unavailable") from exc
        try:
            reply = json.loads(completed.stdout)
            status = reply["status"]
            text = reply.get("body", "")
        except (ValueError, KeyError, TypeError) as exc:
            raise GatewayError(f"gateway broker refused the call (exit {completed.returncode})") from exc
        if not isinstance(status, int) or isinstance(status, bool) or not isinstance(text, str):
            raise GatewayError("gateway broker reply has an unexpected shape")
        return status, text.encode("utf-8")

    def _start_job(self, path: str, body: bytes) -> tuple[int, bytes] | str:
        status, raw = self._call("POST", JOB_PATHS[path], body)
        if status != 202:
            return status, raw
        try:
            job_id = json.loads(raw)["job_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GatewayError("gateway job reply has no job id") from exc
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise GatewayError("gateway job id is malformed")
        return job_id

    def _exchange(self, path: str, body: bytes, timeout: float, limit: int) -> tuple[int, bytes]:
        started = self._start_job(path, body)
        if not isinstance(started, str):
            return started
        deadline = self.clock() + timeout + JOB_QUEUE_ALLOWANCE_SECONDS
        while True:
            self.sleep(POLL_SECONDS)
            status, raw = self._call("GET", f"/v1/jobs/{started}")
            if status == 404:
                raise GatewayUnavailable("gateway lost the job")
            if status != 200:
                return status, raw
            try:
                job = json.loads(raw)
                finished = job["status"] == "done"
                if finished:
                    return int(job["http_status"]), json.dumps(job["response"]).encode("utf-8")
            except (ValueError, KeyError, TypeError) as exc:
                raise GatewayError("gateway job reply has an unexpected shape") from exc
            if self.clock() > deadline:
                raise GatewayUnavailable("gateway job did not finish in time")
