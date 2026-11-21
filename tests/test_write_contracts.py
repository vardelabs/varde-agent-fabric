#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.implement_contracts import ContractError, MAX_BYTES
from contracts.write_contracts import WriteProposal, write_paths

SHA = "a" * 64


def _proposal(changes, paths=("docs/intro.md",)):
    return {"schema": "write-proposal.v1", "worker_kind": "agy", "model": "example-model", "paths": list(paths),
            "changes": changes, "response": ""}


class WritePathTests(unittest.TestCase):
    def test_unsafe_or_duplicate_paths_are_refused(self):
        for paths in ([], ["/etc/passwd"], ["../x"], ["a//b"], [".git/config"], ["a\nb"], ["x", "x"],
                      ["p"] * 51):
            with self.subTest(paths=paths), self.assertRaises(ContractError):
                write_paths(paths)


class WriteProposalTests(unittest.TestCase):
    def test_round_trip_keeps_each_kind_of_change(self):
        changes = [{"path": "docs/intro.md", "before_sha256": SHA, "after_text": "new\n"},
                   {"path": "docs/new.md", "before_sha256": None, "after_text": ""},
                   {"path": "docs/old.md", "before_sha256": SHA, "after_text": None}]
        data = _proposal(changes, paths=("docs/intro.md", "docs/new.md", "docs/old.md"))
        self.assertEqual(WriteProposal.from_dict(data).to_dict(), data)

    def test_a_change_outside_the_named_paths_is_refused(self):
        with self.assertRaisesRegex(ContractError, "outside the named paths"):
            WriteProposal.from_dict(_proposal([{"path": "other.md", "before_sha256": None, "after_text": "x"}]))

    def test_malformed_changes_are_refused(self):
        bad = {
            "neither side": {"path": "docs/intro.md", "before_sha256": None, "after_text": None},
            "bad sha": {"path": "docs/intro.md", "before_sha256": "A" * 64, "after_text": "x"},
            "extra key": {"path": "docs/intro.md", "before_sha256": None, "after_text": "x", "mode": "755"},
            "nul": {"path": "docs/intro.md", "before_sha256": None, "after_text": "a\x00b"},
        }
        for case, change in bad.items():
            with self.subTest(case=case), self.assertRaises(ContractError):
                WriteProposal.from_dict(_proposal([change]))
        twice = {"path": "docs/intro.md", "before_sha256": None, "after_text": "x"}
        with self.assertRaisesRegex(ContractError, "duplicate change"):
            WriteProposal.from_dict(_proposal([twice, twice]))

    def test_unknown_schema_or_keys_are_refused(self):
        with self.assertRaises(ContractError):
            WriteProposal.from_dict({**_proposal([]), "schema": "write-proposal.v2"})
        with self.assertRaises(ContractError):
            WriteProposal.from_dict({**_proposal([]), "applied": True})

    def test_paths_and_changes_must_be_arrays(self):
        for field, value in (("paths", "docs/intro.md"), ("paths", None), ("changes", {}), ("changes", None)):
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                WriteProposal.from_dict({**_proposal([]), field: value})

    def test_the_whole_proposal_is_bounded(self):
        paths = ("a.md", "b.md")
        changes = [{"path": path, "before_sha256": None, "after_text": "x" * (MAX_BYTES // 2 + 1)} for path in paths]
        with self.assertRaisesRegex(ContractError, "aggregate"):
            WriteProposal.from_dict(_proposal(changes, paths=paths))


if __name__ == "__main__":
    unittest.main()
