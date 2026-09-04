#!/usr/bin/env python3
"""Focused fake-gateway and routing tests for the remote review slice."""
from __future__ import annotations

import json
import http.client
import sys
import unittest
from pathlib import Path
from typing import Callable, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.review_contracts import ContractError, ReviewArtifact, ReviewRequest
from dispatch.cluster_worker import ClusterReviewWorker
from dispatch.gateway_client import GatewayClient, GatewayError
from dispatch.review_supervisor import ReviewSupervisor


BASE = "a" * 40
HEAD = "b" * 40


def request(worker: str = "local-gemma") -> ReviewRequest:
    return ReviewRequest("vardelabs/example", 7, BASE, HEAD, ("src/app.py",), "reviewer", worker, "corr-7", "bounded packet")


def artifact(req: ReviewRequest, **changes: object) -> dict:
    value = ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"severity": "green"},), "completed")
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

    def test_http_and_malformed_responses_fail_closed(self):
        req = request()
        class ErrorResponse(Response): status = 503
        client = GatewayClient("https://gateway.example", "secret", opener=lambda *args, **kwargs: ErrorResponse(b""))
        with self.assertRaises(GatewayError): client.submit(req)
        client = GatewayClient("https://gateway.example", "secret", opener=lambda *args, **kwargs: Response(b"not-json"))
        with self.assertRaisesRegex(GatewayError, "malformed"): client.submit(req)

    def test_incomplete_read_is_unavailable(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=http.client.IncompleteRead(b"partial", 10)))
        with self.assertRaises(GatewayError): client.submit(req)

    def test_codex_without_fallback_fails_closed(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        with self.assertRaises(GatewayError): ReviewSupervisor(client).run(req)

    def test_transport_configuration_is_validated(self):
        with self.assertRaises(ValueError): GatewayClient("http://gateway.example", "secret")
        with self.assertRaises(ValueError): GatewayClient("https://gateway.example", "")

    def test_finding_shape_and_worker_error_are_strict(self):
        req = request()
        with self.assertRaises(ContractError): ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "cluster", "gemma", req.correlation_id, ({"x": 1},), "completed")
        bad_model = cast(Callable[[str], list[dict]], lambda packet: ["bad"])
        with self.assertRaises(ContractError): ClusterReviewWorker(bad_model, provider="gemma").execute(req)

    def test_oversized_request_is_rejected_before_network(self):
        req = request()
        oversized = ReviewRequest(req.repository, req.pull_request, req.base_sha, req.head_sha, req.paths, req.kind, req.worker_kind, req.correlation_id, "x" * (256 * 1024), req.timeout_seconds)
        network = mock.Mock()
        client = GatewayClient("https://gateway.example", "secret", opener=network)
        with self.assertRaises(GatewayError): client.submit(oversized)
        network.assert_not_called()

    def test_oversized_response_is_rejected(self):
        req = request()
        client = GatewayClient("https://gateway.example", "secret", opener=self.opener({"x": "y" * 200}), max_response_bytes=64)
        with self.assertRaises(GatewayError): client.submit(req)

    def test_gemma_does_not_fallback(self):
        req = request()
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        local = mock.Mock()
        with self.assertRaises(GatewayError): ReviewSupervisor(client, local_codex=local).run(req)
        local.assert_not_called()

    def test_codex_falls_back_with_provenance(self):
        req = request("codex")
        client = GatewayClient("https://gateway.example", "secret", opener=mock.Mock(side_effect=OSError("offline")))
        local = ClusterReviewWorker(lambda packet: [{"severity": "yellow"}], provider="codex-local", location="requester")
        result = ReviewSupervisor(client, local_codex=local.execute).run(req)
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")
        self.assertEqual(result.execution_location, "requester")

    def test_worker_emits_cluster_provenance(self):
        result = ClusterReviewWorker(lambda packet: [{"severity": "green"}], provider="gemma-private").execute(request()).artifact
        self.assertEqual((result.execution_location, result.provider), ("cluster", "gemma-private"))


if __name__ == "__main__": unittest.main()
