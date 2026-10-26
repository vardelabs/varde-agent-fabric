#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dispatch import adapters
from dispatch.adapters.base import Adapter, LocalReviewFallback


class AdapterRegistrationTests(unittest.TestCase):
    def test_adapter_may_forward_only_its_own_dispatch_variables(self):
        for name in ("PATH", "HOME", "GEMINI_API_KEY", "dispatch_lowercase", "DISPATCH_TASK", "DISPATCH_MODEL"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    Adapter(name="example", env=(name,))
                with self.assertRaises(ValueError):
                    LocalReviewFallback(stamp="example", run=lambda metadata, request: None, env=(name,))

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

    def test_each_registered_adapter_has_an_executable_backend_script(self):
        backends = Path(__file__).resolve().parents[1] / "dispatch" / "cli" / "backends"
        for name in adapters.ADAPTERS:
            with self.subTest(harness=name):
                script = backends / f"{name}.sh"
                self.assertTrue(script.is_file() and script.stat().st_mode & 0o111, script)


if __name__ == "__main__":
    unittest.main()
