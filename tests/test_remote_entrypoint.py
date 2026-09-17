#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import json
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

    def test_resolved_harness_sets_request_worker_kind(self):
        self.assertEqual(
            build_review_request(self.metadata(harness="llama-broker", target="remote")).worker_kind,
            "local-gemma",
        )

    def test_remote_gemma_never_falls_back(self):
        metadata = self.metadata(harness="llama-broker", target="remote")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                with mock.patch("dispatch.remote_runner._run_local_codex") as local:
                    with self.assertRaises(GatewayUnavailable):
                        run_remote_review(metadata, fallback_policy="remote-unavailable")
                    local.assert_not_called()

    def test_local_fallback_preserves_safe_caller_path_and_home(self):
        metadata = self.metadata(local_backend="/bin/local")
        request = build_review_request(metadata)
        payload = json.dumps({"findings": []})
        with tempfile.TemporaryDirectory() as workdir:
            metadata = self.metadata(workdir=workdir, output=f"{workdir}/remote.json")
            with mock.patch.dict(os.environ, {"PATH": "/custom/bin:/usr/bin", "HOME": "/caller-home", "DISPATCH_GATEWAY_TOKEN": "must-not-forward"}, clear=False):
                with mock.patch("dispatch.remote_runner.subprocess.run") as run:
                    def execute(*args, **kwargs):
                        Path(kwargs["env"]["DISPATCH_OUTPUT"]).write_text(payload, encoding="utf-8")
                        return mock.Mock(returncode=0)
                    run.side_effect = execute
                    from dispatch.remote_runner import _run_local_codex
                    _run_local_codex(metadata, request)
                    environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["PATH"], "/custom/bin:/usr/bin")
            self.assertEqual(environment["HOME"], "/caller-home")
            self.assertNotIn("DISPATCH_GATEWAY_TOKEN", environment)

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
