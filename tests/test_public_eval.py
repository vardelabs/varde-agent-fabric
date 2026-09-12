#!/usr/bin/env python3
"""Tests for the public synthetic evaluation fixtures."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.public_eval import evaluate_fixture, run


class PublicEvaluationTests(unittest.TestCase):
    fixtures = ROOT / "eval" / "fixtures" / "review"

    def test_all_expected_fixture_files_are_present(self):
        self.assertEqual(
            {path.name for path in self.fixtures.glob("*.json")},
            {
                "completed-valid.json",
                "malformed-artifact.json",
                "stale-artifact.json",
                "unavailable-worker.json",
            },
        )

    def test_aggregate_report_counts_only_completed_as_clean(self):
        self.assertEqual(run(self.fixtures), {"clean": 1, "rejected": 3})

    def test_unavailable_completed_mutation_fails_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for source in self.fixtures.glob("*.json"):
                destination.joinpath(source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            path = destination / "unavailable-worker.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["artifact"]["finish_reason"] = "completed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(AssertionError):
                run(destination)

    def test_valid_malformed_mutation_fails_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for source in self.fixtures.glob("*.json"):
                destination.joinpath(source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            valid = json.loads((destination / "completed-valid.json").read_text(encoding="utf-8"))
            malformed = json.loads((destination / "malformed-artifact.json").read_text(encoding="utf-8"))
            malformed["artifact"] = valid["artifact"]
            (destination / "malformed-artifact.json").write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(AssertionError):
                run(destination)


if __name__ == "__main__":
    unittest.main()
