#!/usr/bin/env python3
"""Tests for the public synthetic evaluation fixtures."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.public_eval import evaluate_fixture, run


class PublicEvaluationTests(unittest.TestCase):
    fixtures = ROOT / "eval" / "fixtures" / "review"

    def test_each_fixture_has_expected_safety_classification(self):
        expected = {
            "completed-valid.json": "clean",
            "malformed-artifact.json": "rejected",
            "stale-artifact.json": "rejected",
            "unavailable-worker.json": "rejected",
        }
        actual = {path.name: evaluate_fixture(path) for path in sorted(self.fixtures.glob("*.json"))}
        self.assertEqual(actual, expected)

    def test_aggregate_report_counts_only_completed_as_clean(self):
        self.assertEqual(run(self.fixtures), {"clean": 1, "rejected": 3})


if __name__ == "__main__":
    unittest.main()
