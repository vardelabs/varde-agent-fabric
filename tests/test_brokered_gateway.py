#!/usr/bin/env python3
"""The brokered gateway client: a job starts through the broker and is polled to its end."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.review_contracts import ReviewArtifact, ReviewRequest
from dispatch.gateway_client import BrokeredGatewayClient, GatewayError, GatewayUnavailable

BASE = "a" * 40
HEAD = "b" * 40
JOB = "0123456789abcdef0123456789abcdef"
URL = "https://gateway.example"


def review_request() -> ReviewRequest:
    return ReviewRequest("example-org/example", 7, BASE, HEAD, ("src/app.py",), "reviewer", "codex",
                         "corr-7", "bounded packet", timeout_seconds=300)


def review_artifact(req: ReviewRequest, **changes: object) -> dict:
    value = ReviewArtifact(req.repository, req.pull_request, req.base_sha, req.head_sha, req.worker_kind, "remote",
                           "codex", req.correlation_id, ({"severity": "green", "message": "no findings"},), "completed")
    return {**value.to_dict(), **changes}


def reply(status: int, body: object = "", returncode: int = 0) -> SimpleNamespace:
    text = body if isinstance(body, str) else json.dumps(body)
    return SimpleNamespace(stdout=json.dumps({"status": status, "body": text}), returncode=returncode)


class Broker:
    """Answers each broker call in turn and records its argv and stdin."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, *, input, capture_output, text, timeout):
        self.calls.append((argv, input))
        answer = self.replies.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def client(broker: Broker, clock: Clock | None = None) -> BrokeredGatewayClient:
    clock = clock or Clock()
    return BrokeredGatewayClient(URL, "/opt/example-broker/broker", "gateway-secret", run=broker, sleep=clock.sleep,
                                 clock=clock)


class BrokeredGatewayTests(unittest.TestCase):
    def test_a_review_starts_a_job_and_returns_the_finished_artifact(self):
        req = review_request()
        broker = Broker(reply(202, {"job_id": JOB, "status": "queued"}),
                        reply(200, {"status": "running"}),
                        reply(200, {"status": "done", "http_status": 200, "response": review_artifact(req)}))
        artifact = client(broker).submit(req)
        self.assertEqual(artifact.correlation_id, "corr-7")
        (start, body), (first_poll, poll_body), _ = broker.calls
        self.assertEqual(start[:5], ["/opt/example-broker/broker", "http", "gateway-secret", "POST", f"{URL}/v1/jobs/reviews"])
        self.assertIn("-d", start)
        self.assertEqual(json.loads(body)["correlation_id"], "corr-7")
        self.assertEqual(first_poll[3:5], ["GET", f"{URL}/v1/jobs/{JOB}"])
        self.assertNotIn("-d", first_poll)
        self.assertIsNone(poll_body)

    def test_an_implementation_starts_on_its_own_job_route(self):
        broker = Broker(reply(400, {"error": "bad"}))
        status, _ = client(broker)._exchange("/v1/implementations", b"{}", 10, 100)
        self.assertEqual(status, 400)
        self.assertEqual(broker.calls[0][0][4], f"{URL}/v1/jobs/implementations")

    def test_a_finished_job_carries_the_synchronous_route_status(self):
        cases = ((503, GatewayUnavailable, "HTTP 503"), (400, GatewayError, "too large"), (401, GatewayError, "HTTP 401"))
        for status, error, message in cases:
            with self.subTest(status=status):
                broker = Broker(reply(202, {"job_id": JOB}),
                                reply(200, {"status": "done", "http_status": status, "response": {"error": "too large"}}))
                with self.assertRaisesRegex(error, message):
                    client(broker).submit(review_request())

    def test_a_refused_start_reports_the_gateway_status(self):
        for status, error in ((503, GatewayUnavailable), (401, GatewayError)):
            with self.subTest(status=status):
                with self.assertRaisesRegex(error, f"HTTP {status}"):
                    client(Broker(reply(status, "", returncode=1))).submit(review_request())

    def test_a_lost_job_is_unavailable_so_the_caller_can_fall_back(self):
        broker = Broker(reply(202, {"job_id": JOB}), reply(404, {"error": "not found"}, returncode=1))
        with self.assertRaisesRegex(GatewayUnavailable, "lost the job"):
            client(broker).submit(review_request())

    def test_a_job_that_never_finishes_stops_after_its_timeout_and_the_queue_allowance(self):
        clock = Clock()
        polls = [reply(200, {"status": "running"}) for _ in range(200)]
        broker = Broker(reply(202, {"job_id": JOB}), *polls)
        with self.assertRaisesRegex(GatewayUnavailable, "did not finish"):
            client(broker, clock).submit(review_request())
        self.assertGreater(clock.now, 300 + 600)
        self.assertLess(clock.now, 300 + 600 + 10)

    def test_a_malformed_job_id_is_never_put_in_a_path(self):
        for job_id in ("../../v1/pace", JOB.upper(), 7, None):
            with self.subTest(job_id=job_id):
                broker = Broker(reply(202, {"job_id": job_id}))
                with self.assertRaises(GatewayError):
                    client(broker).submit(review_request())
                self.assertEqual(len(broker.calls), 1)

    def test_a_broker_that_cannot_run_is_unavailable(self):
        for failure in (OSError("missing"), subprocess.TimeoutExpired("broker", 60)):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaisesRegex(GatewayUnavailable, "broker unavailable"):
                    client(Broker(failure)).submit(review_request())

    def test_a_broker_refusal_without_a_reply_fails_closed(self):
        refusal = SimpleNamespace(stdout="", returncode=2)
        with self.assertRaisesRegex(GatewayError, r"refused the call \(exit 2\)"):
            client(Broker(refusal)).submit(review_request())

    def test_an_artifact_for_another_request_is_refused(self):
        req = review_request()
        broker = Broker(reply(202, {"job_id": JOB}),
                        reply(200, {"status": "done", "http_status": 200,
                                    "response": review_artifact(req, correlation_id="other")}))
        with self.assertRaisesRegex(GatewayError, "does not match request identity"):
            client(broker).submit(req)

    def test_settings_are_checked_up_front(self):
        for url, broker, secret in (("http://gateway.example", "/b", "s"), (URL, "broker", "s"), (URL, "/b", ""),
                                    (URL, "/b", "two words")):
            with self.subTest(url=url, broker=broker, secret=secret), self.assertRaises(ValueError):
                BrokeredGatewayClient(url, broker, secret)


if __name__ == "__main__":
    unittest.main()
