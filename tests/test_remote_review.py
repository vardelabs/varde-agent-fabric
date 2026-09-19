#!/usr/bin/env python3
"""Focused fake-gateway and routing tests for the remote review slice."""
from __future__ import annotations

import json
import http.client
import multiprocessing
import sys
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Callable, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.review_contracts import ContractError, MAX_PACKET_BYTES, MAX_TRANSPORT_BYTES, ReviewArtifact, ReviewRequest
from dispatch.cluster_worker import ClusterReviewWorker, WorkerResult
from dispatch.gateway_client import GatewayClient, GatewayError, GatewayUnavailable
from dispatch.review_supervisor import ReviewSupervisor


BASE = "a" * 40
HEAD = "b" * 40


def request(worker: str = "local-gemma") -> ReviewRequest:
    return ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("src/app.py",), "reviewer", worker, "corr-7", "bounded packet")


def artifact(req: ReviewRequest, **changes: object) -> dict:
    value = ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"severity": "green", "message": "no findings"},), "completed")
    payload = value.to_dict()
    payload.update(changes)
    return payload


class Response:
    status = 200
    def __init__(self, body: bytes): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, limit: int = -1): return self.body[:limit]


class RemoteReviewTests(unittest.TestCase):
    def opener(self, response: dict):
        def open_request(req, timeout):
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.get_header("Authorization"), "Bearer secret")
            return Response(json.dumps(response).encode())
        return open_request

    def test_contract_rejects_unknown_fields(self):
        payload = request().to_dict()
        payload["unexpected"] = True
        with self.assertRaises(ContractError): ReviewRequest.from_dict(payload)

    def test_contract_accepts_opencode_worker_identity(self):
        value = request("opencode")
        self.assertEqual(value.worker_kind, "opencode")
        self.assertEqual(ReviewArtifact.from_dict(artifact(value)).worker_kind, "opencode")

    def test_contract_rejects_non_integer_pull_request_numbers(self):
        for value in ("7", None, 7.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    ReviewArtifact.from_dict(artifact(request(), pull_request=value))

    def test_contract_normalizes_sha_case(self):
        value = ReviewRequest("vardelabs/example", 7, BASE.upper(), HEAD.upper(), ("src/app.py",), "reviewer", "codex", "corr-7", "bounded packet")
        self.assertEqual((value.base_sha, value.head_sha), (BASE, HEAD))
        response = artifact(value, base_sha=BASE.upper(), head_sha=HEAD.upper())
        normalized = ReviewArtifact.from_dict(response)
        self.assertEqual((normalized.base_sha, normalized.head_sha), (BASE, HEAD))

    def test_contract_rejects_surrogate_text(self):
        with self.assertRaises(ContractError):
            ReviewRequest("vardelabs/\ud800", 7, BASE, HEAD, ("src/app.py",), "reviewer", "codex", "corr-7", "packet")
        with self.assertRaises(ContractError):
            ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("src/\ud800.py",), "reviewer", "codex", "corr-7", "packet")
        with self.assertRaises(ContractError):
            ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("src/app.py",), "reviewer", "codex", "corr-7", "\ud800")
        with self.assertRaises(ContractError):
            ReviewArtifact(request().repository, 7, BASE, HEAD, "codex", "cluster", "codex", "corr-7", ({"severity": "red", "message": "\ud800"},), "completed")

    def test_contract_bounds_path_lengths(self):
        with self.assertRaises(ContractError):
            ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("x" * 513,), "reviewer", "codex", "corr-7", "packet")

    def test_contract_rejects_unsafe_path_shapes(self):
        for path in ("/etc/passwd", "src/../secret", "src//app.py", "src/./app.py", "src\x00app.py"):
            with self.subTest(path=path):
                with self.assertRaises(ContractError):
                    ReviewRequest("vardelabs/example", 7, BASE, HEAD, (path,), "reviewer", "codex", "corr-7", "packet")

    def test_findings_require_messages(self):
        req = request()
        with self.assertRaises(ContractError):
            ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"severity": "green"},), "completed")

    def test_findings_require_allowed_severity(self):
        req = request()
        with self.assertRaises(ContractError):
            ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"message": "missing severity"},), "completed")

    def test_finding_text_limit_is_strict(self):
        req = request()
        with self.assertRaises(ContractError):
            ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"severity": "yellow", "message": "x" * 1025},), "completed")

    def test_multibyte_packet_byte_limit_is_strict(self):
        req = request()
        with self.assertRaises(ContractError):
            ReviewRequest(req.repository, req.pull_request, req.base_sha, req.head_sha, req.paths, req.kind, req.worker_kind, req.correlation_id, "漢" * 100000, req.timeout_seconds)

    def test_fake_gateway_returns_validated_artifact(self):
        req = request()
        client = GatewayClient("https://gateway.example", "secret", opener=self.opener(artifact(req)))
        self.assertEqual(client.submit(req).execution_location, "cluster")

    def test_stale_head_is_rejected(self):
        req = request()
        client = GatewayClient("https://gateway.example", "secret", opener=self.opener(artifact(req, head_sha="c" * 40)))
        with self.assertRaisesRegex(GatewayError, "does not match"):
            client.submit(req)

    def test_all_request_identity_fields_are_bound(self):
        req = request()
        for changes in ({"repository": "other/repo"}, {"pull_request": 99}, {"base_sha": "c" * 40}, {"correlation_id": "other"}, {"worker_kind": "codex"}):
            with self.subTest(changes=changes):
                client = GatewayClient("https://gateway.example", "secret", opener=self.opener(artifact(req, **changes)))
                with self.assertRaises(GatewayError): client.submit(req)

    def test_request_rejects_combined_serialized_transport_overflow(self):
        paths = tuple("漢" * 512 for _ in range(100))
        with self.assertRaisesRegex(ContractError, "transport limit"):
            ReviewRequest(
                "vardelabs/example",
                7,
                BASE,
                HEAD,
                paths,
                "reviewer",
                "codex",
                "corr-7",
                "x" * MAX_PACKET_BYTES,
            )

    def test_request_requires_immutable_paths(self):
        with self.assertRaisesRegex(ContractError, "paths must be a tuple"):
            ReviewRequest(
                "vardelabs/example", 7, BASE, HEAD, ["src/app.py"],
                "reviewer", "codex", "corr-7", "bounded packet",
            )

    def test_non_object_contract_envelopes_raise_contract_error(self):
        for value in ([], "not-an-object", 7, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "must be an object"):
                    ReviewRequest.from_dict(value)
                with self.assertRaisesRegex(ContractError, "must be an object"):
                    ReviewArtifact.from_dict(value)

    def test_non_object_gateway_response_fails_closed(self):
        req = request()
        for value in ([], "not-an-object", 7, None):
            with self.subTest(value=value):
                client = GatewayClient(
                    "https://gateway.example",
                    "secret",
                    opener=lambda *args, value=value, **kwargs: Response(
                        json.dumps(value).encode()
                    ),
                )
                with self.assertRaisesRegex(GatewayError, "malformed"):
                    client.submit(req)

    def test_http_and_malformed_responses_fail_closed(self):
        req = request()
        class ErrorResponse(Response): status = 503
        client = GatewayClient("https://gateway.example", "secret", opener=lambda *args, **kwargs: ErrorResponse(b""))
        with self.assertRaisesRegex(GatewayError, "HTTP 503"): client.submit(req)
        client = GatewayClient("https://gateway.example", "secret", opener=lambda *args, **kwargs: Response(b"not-json"))
        with self.assertRaisesRegex(GatewayError, "malformed"): client.submit(req)

        error = urllib.error.HTTPError("https://gateway.example/v1/reviews", 401, "unauthorized", Message(), None)
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=error))
        with self.assertRaisesRegex(GatewayError, "HTTP 401"): client.submit(req)

    def test_incomplete_read_is_unavailable(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=http.client.IncompleteRead(b"partial", 10)))
        with self.assertRaises(GatewayError): client.submit(req)

    def test_codex_without_fallback_fails_closed(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        with self.assertRaises(GatewayError): ReviewSupervisor(client).run(req)

    def test_gateway_unavailable_is_preserved_for_legacy_callers(self):
        req = request("codex")
        unavailable = GatewayUnavailable("offline")
        gateway = mock.Mock()
        gateway.submit.side_effect = unavailable
        with self.assertRaises(GatewayUnavailable) as raised:
            ReviewSupervisor(gateway).run(req)
        self.assertIs(raised.exception, unavailable)

    def test_http_401_does_not_trigger_local_fallback(self):
        req = request("codex")
        error = urllib.error.HTTPError("https://gateway.example/v1/reviews", 401, "unauthorized", Message(), None)
        local = mock.Mock()
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=error))
        with self.assertRaisesRegex(GatewayError, "HTTP 401"):
            ReviewSupervisor(client, local_codex=local).run(req)
        local.assert_not_called()

    def test_http_503_triggers_local_fallback(self):
        req = request("codex")
        error = urllib.error.HTTPError("https://gateway.example/v1/reviews", 503, "unavailable", Message(), None)
        local = mock.Mock(return_value=WorkerResult(ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, "codex", "requester", "codex-local", req.correlation_id, (), "completed")))
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=error))
        result = ReviewSupervisor(client, local_codex=local).run(req)
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")

    def test_transport_configuration_is_validated(self):
        with self.assertRaises(ValueError): GatewayClient("http://gateway.example", "secret")
        with self.assertRaises(ValueError): GatewayClient("https://gateway.example", "")

    def test_finding_shape_and_worker_error_are_strict(self):
        req = request()
        with self.assertRaises(ContractError): ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"x": 1},), "completed")
        bad_model = cast(Callable[[str], list[dict]], lambda packet: ["bad"])
        with self.assertRaises(ContractError): ClusterReviewWorker(bad_model, provider="gemma").execute(req)

    def test_transport_limit_is_checked_before_network(self):
        req = request()
        oversized = req
        network = mock.Mock()
        client = GatewayClient("https://gateway.example", "secret", opener=network)
        with mock.patch("dispatch.gateway_client.MAX_TRANSPORT_BYTES", 1):
            with self.assertRaisesRegex(GatewayError, "transport limit"): client.submit(oversized)
        network.assert_not_called()

    def test_packet_at_contract_limit_is_sendable_with_transport_allowance(self):
        req = request()
        packet = "x" * MAX_PACKET_BYTES
        paths = tuple(f"packages/service-{i:03d}/src/components/Widget/index.tsx" for i in range(100))
        sized = ReviewRequest(req.repository, req.pull_request, req.base_sha, req.head_sha, paths, req.kind, req.worker_kind, req.correlation_id, packet, req.timeout_seconds)
        network = mock.Mock(return_value=Response(json.dumps(artifact(sized)).encode()))
        result = GatewayClient("https://gateway.example", "secret", opener=network).submit(sized)
        self.assertEqual(result.repository, req.repository)
        self.assertLessEqual(len(network.call_args.args[0].data), MAX_TRANSPORT_BYTES)

    def test_multibyte_packet_uses_utf8_transport_size(self):
        req = request()
        packet = "漢字" * 32768
        sized = ReviewRequest(req.repository, req.pull_request, req.base_sha, req.head_sha, req.paths, req.kind, req.worker_kind, req.correlation_id, packet, req.timeout_seconds)
        network = mock.Mock(return_value=Response(json.dumps(artifact(sized), ensure_ascii=False).encode()))
        result = GatewayClient("https://gateway.example", "secret", opener=network).submit(sized)
        self.assertEqual(result.repository, req.repository)

    def test_default_response_limit_accepts_contract_valid_artifact(self):
        req = request()
        findings = tuple(
            {
                "severity": "yellow",
                "file": f"src/app-{i}.py",
                "message": "m" * 1024,
                "confidence": "c" * 1024,
                "category": "k" * 1024,
                "reproduction": "r" * 1024,
                "line": 1,
            }
            for i in range(50)
        )
        value = ReviewArtifact(
            req.repository, req.pull_request, req.base_sha, req.head_sha,
            req.worker_kind, "cluster", "gemma", req.correlation_id,
            findings, "completed",
        )
        body = json.dumps(value.to_dict()).encode()
        client = GatewayClient(
            "https://gateway.example", "secret",
            opener=lambda *args, **kwargs: Response(body),
        )
        self.assertEqual(client.submit(req), value)

    def test_oversized_response_is_rejected(self):
        req = request()
        body = json.dumps(artifact(req)).encode() + (b" " * 200)
        client = GatewayClient("https://gateway.example", "secret", opener=lambda *args, **kwargs: Response(body), max_response_bytes=64)
        with self.assertRaisesRegex(GatewayError, "response exceeds transport limit"):
            client.submit(req)

    def test_gemma_does_not_fallback(self):
        req = request()
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        local = mock.Mock()
        with self.assertRaises(GatewayError): ReviewSupervisor(client, local_codex=local).run(req)
        local.assert_not_called()

    def test_codex_falls_back_with_provenance(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        local = ClusterReviewWorker(lambda packet: [{"severity": "yellow", "message": "finding"}], provider="codex-local", location="requester")
        result = ReviewSupervisor(client, local_codex=local.execute).run(req)
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")
        self.assertEqual(result.execution_location, "requester")

    def test_codex_fallback_rejects_stale_artifact(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        local = mock.Mock(return_value=WorkerResult(ReviewArtifact("other/repo", 99, "c" * 40, "d" * 40, "codex", "requester", "codex-local", "other-corr", (), "completed")))
        with self.assertRaisesRegex(GatewayError, "does not match"):
            ReviewSupervisor(client, local_codex=local).run(req)

    def test_codex_fallback_converts_worker_failures(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        for failure in (ContractError("bad findings"), OSError("codex binary missing"), AttributeError("bad result")):
            with self.subTest(failure=type(failure).__name__):
                local = mock.Mock(side_effect=failure)
                with self.assertRaisesRegex(GatewayError, f"local Codex fallback failed: {type(failure).__name__}"):
                    ReviewSupervisor(client, local_codex=local).run(req)

    def test_codex_fallback_rejects_wrong_return_types(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        for value in (object(), WorkerResult(cast(ReviewArtifact, "nope"))):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(GatewayError, "invalid worker result"):
                    ReviewSupervisor(client, local_codex=mock.Mock(return_value=value)).run(req)

    def test_codex_fallback_drains_large_worker_results(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        findings = tuple({"severity": "yellow", "file": f"src/app-{i}.py", "message": f"{i:04d}-" + "m" * 1019, "confidence": f"{i:04d}-" + "c" * 1019, "category": f"{i:04d}-" + "k" * 1019, "reproduction": f"{i:04d}-" + "r" * 1019, "line": 1} for i in range(50))
        large = WorkerResult(ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, "codex", "requester", "codex-local", req.correlation_id, findings, "completed"))
        result = ReviewSupervisor(client, local_codex=mock.Mock(return_value=large)).run(req)
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")

    def test_codex_fallback_honors_timeout(self):
        req = ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("src/app.py",), "reviewer", "codex", "corr-7", "bounded packet", timeout_seconds=1)
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        def hung(_request):
            time.sleep(2)
            raise AssertionError("must not finish before timeout")
        with self.assertRaisesRegex(GatewayError, "timed out"):
            ReviewSupervisor(client, local_codex=hung).run(req)
        self.assertEqual(multiprocessing.active_children(), [])

    def test_worker_emits_cluster_provenance(self):
        result = ClusterReviewWorker(lambda packet: [{"severity": "green", "message": "no findings"}], provider="gemma-private").execute(request()).artifact
        self.assertEqual((result.execution_location, result.provider), ("cluster", "gemma-private"))


if __name__ == "__main__": unittest.main()
