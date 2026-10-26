#!/usr/bin/env python3
from __future__ import annotations

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
