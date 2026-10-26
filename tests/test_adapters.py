#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.implement_contracts import ContractError as ImplementContractError, ImplementRequest
from contracts.review_contracts import ContractError, ReviewRequest
from dispatch import adapters
from dispatch.adapters.base import Adapter, LocalReviewFallback


def _no_review(metadata, request):
    raise AssertionError("not called")


def _review_request(worker_kind: str) -> ReviewRequest:
    return ReviewRequest("example-org/example", 1, "a" * 40, "b" * 40, ("src/app.py",), "reviewer",
                         worker_kind, "corr-1", "packet", 60)


def _implement_request(worker_kind: str, model: str | None) -> ImplementRequest:
    return ImplementRequest("example-org/example", worker_kind, "corr-1", "task",
                            ({"path": "app.py", "content": "x\n"},), 60, model=model)


class AdapterRegistrationTests(unittest.TestCase):
    def test_adapter_may_forward_only_its_own_dispatch_variables(self):
        for name in ("PATH", "HOME", "GEMINI_API_KEY", "dispatch_lowercase", "DISPATCH_TASK", "DISPATCH_MODEL"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    Adapter(name="example", env=(name,))
                with self.assertRaises(ValueError):
                    LocalReviewFallback(run=_no_review, env=(name,))

    def test_a_capability_requires_the_role_it_serves(self):
        with self.assertRaisesRegex(ValueError, "review role"):
            Adapter(name="example-backend", local_review_fallback=LocalReviewFallback(run=_no_review))
        with self.assertRaisesRegex(ValueError, "implement role"):
            Adapter(name="example-backend", implement_model=re.compile("x"))
        Adapter(name="example-backend", roles=frozenset({"review"}), local_review_fallback=LocalReviewFallback(run=_no_review))

    def test_roles_and_names_are_plain_names(self):
        for bad in ("Review", "", "re view", "-x"):
            with self.subTest(role=bad):
                with self.assertRaises(ValueError):
                    Adapter(name="example-backend", roles=frozenset({bad}))
        with self.assertRaises(ValueError):
            Adapter(name="Example_Backend")

    def test_existing_fallback_stamps_are_unchanged(self):
        self.assertEqual(adapters.ADAPTERS["codex"].fallback_stamp, "cluster-unavailable-local-codex")
        self.assertEqual(adapters.ADAPTERS["opencode"].fallback_stamp, "cluster-unavailable-local-opencode")

    def test_unknown_harness_forwards_nothing_and_has_no_local_fallback(self):
        self.assertEqual(adapters.forwarded_env("llama-broker"), ())
        self.assertEqual(adapters.forwarded_env("llama-broker", fallback=True), ())
        self.assertIsNone(adapters.local_review_fallback("llama-broker"))

    def test_local_fallback_forwards_a_subset_of_the_direct_run(self):
        for name, adapter in adapters.ADAPTERS.items():
            with self.subTest(harness=name):
                self.assertLessEqual(set(adapters.forwarded_env(name, fallback=True)), set(adapter.env))

    def test_opencode_fallback_review_never_gets_write_permission(self):
        self.assertIn("DISPATCH_OPENCODE_ALLOW_WRITE", adapters.forwarded_env("opencode"))
        self.assertNotIn("DISPATCH_OPENCODE_ALLOW_WRITE", adapters.forwarded_env("opencode", fallback=True))

    def test_each_public_adapter_has_an_executable_backend_script(self):
        backends = Path(__file__).resolve().parents[1] / "dispatch" / "cli" / "backends"
        for name in adapters.ADAPTERS:
            with self.subTest(harness=name):
                script = backends / f"{name}.sh"
                self.assertTrue(script.is_file() and script.stat().st_mode & 0o111, script)


class RegistryTests(unittest.TestCase):
    def test_resolve_returns_the_adapter_only_for_a_role_it_declares(self):
        self.assertIs(adapters.resolve("review", "codex"), adapters.ADAPTERS["codex"])
        with self.assertRaisesRegex(LookupError, "does not serve"):
            adapters.resolve("write", "codex")
        with self.assertRaisesRegex(LookupError, "no adapter"):
            adapters.resolve("review", "llama-broker")

    def test_a_registered_adapter_serves_its_roles_with_no_other_change(self):
        extra = Adapter(name="example-backend", roles=frozenset({"review", "write"}))
        with mock.patch.dict(adapters.ADAPTERS):
            adapters.register(extra)
            self.assertIs(adapters.resolve("write", "example-backend"), extra)
            with self.assertRaisesRegex(ValueError, "already registered"):
                adapters.register(extra)
        self.assertNotIn("example-backend", adapters.ADAPTERS)


class LimitAndParserTests(unittest.TestCase):
    def test_codex_reads_only_its_own_fatal_last_line(self):
        self.assertEqual(adapters.detect_limit("codex", "working\nerror: 429 Too Many Requests\n"), "rate-limit")
        self.assertEqual(adapters.detect_limit("codex", "error: usage limit reached"), "rate-limit")
        self.assertIsNone(adapters.detect_limit("codex", "the model said rate limit\nerror: bad request"))
        self.assertIsNone(adapters.detect_limit("codex", "error: 429\nnote: retried and finished"))

    def test_opencode_reads_its_last_top_level_error_event(self):
        v2_quota = '{"type":"error","error":{"type":"provider.quota","status":429}}'
        v1_rate = '{"type":"error","error":{"name":"APIError","data":{"statusCode":429}}}'
        other = '{"type":"error","error":{"type":"provider.auth","status":401}}'
        self.assertEqual(adapters.detect_limit("opencode", v2_quota), "quota")
        self.assertEqual(adapters.detect_limit("opencode", v1_rate), "rate-limit")
        self.assertIsNone(adapters.detect_limit("opencode", other))
        self.assertIsNone(adapters.detect_limit("opencode", v2_quota + "\n{unreadable"))

    def test_opencode_only_the_last_error_event_decides(self):
        rate = '{"type":"error","error":{"name":"RateLimitError"}}'
        not_found = '{"type":"error","error":{"name":"ProviderModelNotFoundError","data":{"statusCode":404}}}'
        self.assertEqual(adapters.detect_limit("opencode", not_found + "\n" + rate), "rate-limit")
        self.assertIsNone(adapters.detect_limit("opencode", rate + "\n" + not_found))

    def test_opencode_a_rate_limit_name_counts_without_a_429(self):
        for name in ('"name":"RateLimitError"', '"type":"provider.rate-limit"', '"type":"provider.rate_limit"'):
            with self.subTest(name=name):
                event = '{"type":"error","error":{' + name + '}}'
                self.assertEqual(adapters.detect_limit("opencode", event), "rate-limit")

    def test_opencode_an_unknown_type_with_status_429_is_a_rate_limit(self):
        event = '{"type":"error","error":{"type":"provider.unknown","status":429}}'
        self.assertEqual(adapters.detect_limit("opencode", event), "rate-limit")

    def test_opencode_a_spent_quota_needs_no_status(self):
        self.assertEqual(adapters.detect_limit("opencode", '{"type":"error","error":{"type":"provider.quota"}}'), "quota")

    def test_opencode_the_v1_status_under_data_wins_over_a_top_level_status(self):
        event = '{"type":"error","error":{"name":"ProviderAuthError","data":{"statusCode":401},"status":429}}'
        self.assertIsNone(adapters.detect_limit("opencode", event))

    def test_an_adapter_without_a_detector_or_an_unknown_kind_reports_no_limit(self):
        self.assertIsNone(adapters.detect_limit("llama-broker", "error: 429"))
        odd = Adapter(name="example-backend", detect_limit=lambda diagnostic: "maybe")
        with mock.patch.dict(adapters.ADAPTERS, {"example-backend": odd}):
            self.assertIsNone(adapters.detect_limit("example-backend", "x"))

    def test_review_parsers_return_the_findings(self):
        finding = {"severity": "red", "message": "m"}
        self.assertEqual(adapters.resolve("review", "codex").parse_review(json.dumps({"findings": [finding]}).encode()),
                         (finding,))
        event = json.dumps({"type": "text", "part": {"text": json.dumps([finding])}}) + "\n"
        self.assertEqual(adapters.resolve("review", "opencode").parse_review(event.encode()), (finding,))
        with self.assertRaises(RuntimeError):
            adapters.resolve("review", "codex").parse_review(b"not json")


class ContractEligibilityTests(unittest.TestCase):
    def test_review_contract_accepts_any_well_formed_worker(self):
        for worker in ("codex", "opencode", "local-gemma", "example-backend"):
            with self.subTest(worker=worker):
                self.assertEqual(_review_request(worker).worker_kind, worker)
        for worker in ("", "Codex", "a/b", "x" * 65, None):
            with self.subTest(worker=worker):
                with self.assertRaises(ContractError):
                    _review_request(worker)

    def test_implement_model_shape_comes_from_the_adapter_registration(self):
        _implement_request("opencode", "example-provider/example-model")
        with self.assertRaisesRegex(ImplementContractError, "unsupported OpenCode implement model"):
            _implement_request("opencode", "not a model")
        with self.assertRaisesRegex(ImplementContractError, "unsupported OpenCode implement model"):
            _implement_request("opencode", None)
        _implement_request("codex", None)
        _implement_request("example-backend", "anything-shaped")
        with self.assertRaisesRegex(ImplementContractError, "unsupported implement worker"):
            _implement_request("Bad Worker", None)


if __name__ == "__main__":
    unittest.main()
