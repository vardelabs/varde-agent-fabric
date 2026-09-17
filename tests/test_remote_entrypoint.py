#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.review_contracts import ReviewArtifact
from dispatch.gateway_client import GatewayError, GatewayUnavailable
from dispatch.remote_runner import RemoteReviewMetadata, build_review_request, run_remote_review

BASE = "a" * 40
HEAD = "b" * 40


class RemoteEntrypointTests(unittest.TestCase):
    def metadata(self, **changes):
        values = dict(
            repository="vardelabs/example",
            pull_request=19,
            base_sha=BASE,
            head_sha=HEAD,
            correlation_id="corr-19",
            paths=("src/app.py",),
            task="Review this change",
            diff=None,
            workdir="/work",
            model="codex-model",
            local_backend="/bin/false",
            output="/tmp/review.json",
            timeout_seconds=30,
        )
        values.update(changes)
        return RemoteReviewMetadata(**values)

    def test_request_is_constructed_only_from_explicit_review_metadata(self):
        request = build_review_request(self.metadata())
        self.assertEqual(request.to_dict(), {
            "schema": "review-request.v1",
            "repository": "vardelabs/example",
            "pull_request": 19,
            "base_sha": BASE,
            "head_sha": HEAD,
            "paths": ["src/app.py"],
            "kind": "reviewer",
            "worker_kind": "codex",
            "correlation_id": "corr-19",
            "packet": "Review this change",
            "timeout_seconds": 30,
        })

    def test_remote_route_calls_gateway_without_local_fallback(self):
        req = build_review_request(self.metadata())
        expected = ReviewArtifact("vardelabs/example", 19, BASE, HEAD, "codex", "cluster", "codex", "corr-19", (), "completed")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.return_value = expected
                result = run_remote_review(self.metadata(), fallback_policy="none")
        self.assertEqual(result, expected)
        client_type.assert_called_once_with("https://private.example", "token")
        client_type.return_value.submit.assert_called_once_with(req)

    def test_only_gateway_unavailable_uses_local_codex_and_preserves_identity(self):
        metadata = self.metadata(local_backend="/bin/local")
        expected = ReviewArtifact("vardelabs/example", 19, BASE, HEAD, "codex", "requester", "codex", "corr-19", (), "completed")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                with mock.patch("dispatch.remote_runner._run_local_codex", return_value=expected) as local:
                    result = run_remote_review(metadata, fallback_policy="remote-unavailable")
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")
        self.assertEqual((result.repository, result.pull_request, result.base_sha, result.head_sha, result.correlation_id), ("vardelabs/example", 19, BASE, HEAD, "corr-19"))
        local.assert_called_once()

    def test_http_503_response_status_uses_local_fallback(self):
        metadata = self.metadata(local_backend="/bin/local")
        expected = ReviewArtifact("vardelabs/example", 19, BASE, HEAD, "codex", "requester", "codex", "corr-19", (), "completed")
        class UnavailableResponse:
            status = 503
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, limit=-1): return b""
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient", return_value=__import__("dispatch.gateway_client", fromlist=["GatewayClient"]).GatewayClient("https://private.example", "token", opener=lambda *args, **kwargs: UnavailableResponse())):
                with mock.patch("dispatch.remote_runner._run_local_codex", return_value=expected):
                    result = run_remote_review(metadata, fallback_policy="remote-unavailable")
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")

    def test_contract_or_worker_errors_never_fallback(self):
        for error in (GatewayError("bad artifact"), RuntimeError("worker failed")):
            with self.subTest(error=error):
                with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
                    with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                        client_type.return_value.submit.side_effect = error
                        with mock.patch("dispatch.remote_runner._run_local_codex") as local:
                            with self.assertRaises(type(error)):
                                run_remote_review(self.metadata(), fallback_policy="remote-unavailable")
                            local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
