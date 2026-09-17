"""Compatibility supervisor using the canonical review fallback policy."""
from __future__ import annotations

from collections.abc import Callable
import multiprocessing
import threading

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.cluster_worker import WorkerResult
from dispatch.fallback_policy import run_with_remote_fallback
from dispatch.gateway_client import GatewayClient, GatewayError, GatewayUnavailable


class ReviewSupervisor:
    """Legacy library boundary retained for callers of the original API."""

    def __init__(self, gateway: GatewayClient, *, local_codex: Callable[[ReviewRequest], WorkerResult] | None = None) -> None:
        self.gateway = gateway
        self.local_codex = local_codex

    def run(self, request: ReviewRequest) -> ReviewArtifact:
        def fallback() -> ReviewArtifact:
            result = _run_local_codex_with_timeout(self.local_codex, request)  # type: ignore[arg-type]
            if not isinstance(result, WorkerResult) or not isinstance(result.artifact, ReviewArtifact):
                raise GatewayError("local fallback returned an invalid worker result")
            return result.artifact

        try:
            return run_with_remote_fallback(
                request,
                lambda: self.gateway.submit(request),
                fallback if self.local_codex is not None else None,
            )
        except RuntimeError as exc:
            if isinstance(exc, GatewayUnavailable):
                raise
            raise GatewayError(str(exc)) from exc


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
