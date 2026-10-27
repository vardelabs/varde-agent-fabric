#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import review_contracts
from contracts.review_contracts import ReviewArtifact
from dispatch import adapters
from dispatch.adapters import review_output

RED = '[{"severity":"red","message":"auth bypass"}]'
GREEN = '[{"severity":"green","message":"nit"}]'


def _codex(payload: object) -> tuple:
    return adapters.resolve("review", "codex").parse_review(json.dumps(payload).encode())


def _jsonl(*texts: str) -> bytes:
    return "".join(json.dumps({"type": "text", "part": {"text": text}}) + "\n" for text in texts).encode()


def _opencode(*texts: str) -> tuple:
    return adapters.resolve("review", "opencode").parse_review(_jsonl(*texts))


def _artifact(findings: tuple) -> ReviewArtifact:
    return ReviewArtifact("example-org/example", 1, "a" * 40, "b" * 40, "codex", "cluster", "example/model",
                          "c1", findings, "completed")


class FindingNormalisationTests(unittest.TestCase):
    def test_text_limit_matches_the_review_contract(self):
        self.assertEqual(review_output.FINDING_TEXT_LIMIT, review_contracts._MAX_FINDING_TEXT)

    def test_title_and_body_become_one_message_that_satisfies_the_contract(self):
        findings = _codex([
            {"file": "service/app.py", "line": 2, "severity": "high",
             "title": "Zero divisor still raises an exception", "body": "`safe_div` evaluates first"},
            {"severity": "critical", "title": "Auth bypass", "body": "token check skipped"},
        ])
        self.assertEqual(findings, (
            {"severity": "red", "message": "Zero divisor still raises an exception: `safe_div` evaluates first",
             "line": 2, "file": "service/app.py"},
            {"severity": "red", "message": "Auth bypass: token check skipped"},
        ))
        self.assertEqual(_artifact(findings).findings, findings)

    def test_each_severity_family_maps_to_a_contract_colour(self):
        families = {
            "red": ("critical", "HIGH", "blocker", "blocking", "major", "red"),
            "yellow": ("medium", "moderate", "minor", "Low", "warning", "yellow"),
            "green": ("info", "informational", "nit", "note", "green"),
        }
        for expected, aliases in families.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(_codex([{"severity": alias, "message": "m"}])[0]["severity"], expected)

    def test_unknown_or_missing_severity_blocks(self):
        findings = _codex([{"severity": "catastrophic", "message": "m"}, {"message": "m"},
                           {"severity": 3, "message": "m"}, {"priority": "P0", "message": "m"}])
        self.assertEqual([finding["severity"] for finding in findings], ["red"] * 4)

    def test_unknown_keys_are_dropped(self):
        findings = _codex([{"severity": "red", "message": "m", "id": "F1", "suggestion": "x",
                            "title": "ignored when message present"}])
        self.assertEqual(findings, ({"severity": "red", "message": "m"},))

    def test_overlong_text_is_cut_to_the_contract_limit(self):
        finding = _codex([{"severity": "low", "title": "T", "body": "x" * 5000, "reproduction": "y" * 2000}])[0]
        self.assertEqual(finding["message"], ("T: " + "x" * 5000)[:1024])
        self.assertEqual(finding["reproduction"], "y" * 1024)

    def test_a_numeric_string_line_is_kept_and_bad_lines_are_dropped(self):
        lines = ("5", 0, "L12", True, 3.5, "9" * 5000)
        findings = _codex([{"severity": "red", "message": "m", "line": line} for line in lines])
        self.assertEqual([finding.get("line") for finding in findings], [5, None, None, None, None, None])

    def test_a_finding_without_recognised_text_keeps_its_raw_json(self):
        findings = _codex([{"severity": "critical", "summary": "auth bypass"}, {"severity": "red", "title": "kept"}])
        self.assertEqual(findings[0]["severity"], "red")
        self.assertIn("auth bypass", findings[0]["message"])
        self.assertEqual(findings[1]["message"], "kept")

    def test_path_or_filename_becomes_file(self):
        findings = _codex([{"severity": "red", "message": "m", "path": "a.py"},
                           {"severity": "red", "message": "m", "filename": "b.py"}])
        self.assertEqual([finding["file"] for finding in findings], ["a.py", "b.py"])

    def test_a_body_without_a_title_becomes_the_message(self):
        self.assertEqual(_codex([{"severity": "red", "description": "only a description"}])[0]["message"],
                         "only a description")

    def test_numeric_confidence_becomes_contract_text(self):
        self.assertEqual(_codex([{"severity": "red", "message": "m", "confidence": 0.8}])[0]["confidence"], "0.8")

    def test_empty_findings_stay_empty(self):
        self.assertEqual(_codex([]), ())
        self.assertEqual(_codex({"findings": []}), ())

    def test_a_conforming_finding_passes_through_unchanged(self):
        finding = {"severity": "yellow", "file": "a.py", "line": 7, "message": "m",
                   "confidence": "high", "category": "correctness", "reproduction": "run it"}
        self.assertEqual(_codex([dict(finding)]), (finding,))

    def test_a_non_object_finding_or_payload_is_refused(self):
        for payload in (["not an object"], {"no": "findings"}, "text"):
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    _codex(payload)

    def test_codex_output_is_strict_json(self):
        with self.assertRaises(RuntimeError):
            adapters.resolve("review", "codex").parse_review(("```json\n" + RED + "\n```").encode())


class OpenCodeStreamTests(unittest.TestCase):
    def test_findings_before_an_empty_trailing_text_are_kept(self):
        self.assertEqual(_opencode(RED, ""), ({"severity": "red", "message": "auth bypass"},))

    def test_a_fence_around_the_whole_final_text_is_unwrapped(self):
        for final in ("```json\n" + RED + "\n```", "\n```\n" + RED + "\n```\n"):
            with self.subTest(final=final[:8]):
                self.assertEqual(_opencode(final), ({"severity": "red", "message": "auth bypass"},))

    def test_a_fenced_clean_review_that_is_the_only_text_is_accepted(self):
        for final in ("```json\n[]\n```", '```\n{"findings": []}\n```'):
            with self.subTest(final=final):
                self.assertEqual(_opencode(final), ())
                self.assertEqual(_opencode("", "\n", final), ())

    def test_a_fenced_clean_review_after_earlier_text_is_refused(self):
        for final in ("```json\n[]\n```", '```\n{"findings": []}\n```'):
            for earlier in ("I found " + RED + " so far.", "Reviewing the packet."):
                with self.subTest(final=final, earlier=earlier[:9]):
                    with self.assertRaisesRegex(RuntimeError, r"not valid JSON \(fenced\)"):
                        _opencode(earlier, final)

    def test_an_unparsed_final_text_is_named_by_its_shape_only(self):
        for parts, shape in ((("  ",), "empty"), (("Found an auth bypass.\n[]",), "prose"),
                             (("```json\n[\n```",), "fenced")):
            with self.subTest(shape=shape):
                with self.assertRaisesRegex(RuntimeError, rf"^final text is not valid JSON \({shape}\)$"):
                    _opencode(*parts)

    def test_json_mixed_with_prose_is_never_rescued(self):
        for final in ("Here are the findings:\n" + RED, "An empty list [] means clean. Findings:\n" + RED,
                      "I found: " + RED + "\nRevised:\n" + GREEN, "Draft: " + RED + '\n{"findings": ' + GREEN + "}",
                      "```json\n" + RED + "\n```\n```json\n" + GREEN + "\n```", RED + "\n" + GREEN):
            with self.subTest(final=final[:12]):
                with self.assertRaisesRegex(RuntimeError, "final text is not valid JSON"):
                    _opencode(final)

    def test_a_final_snapshot_that_drops_earlier_findings_is_refused(self):
        for parts in (("[", '{"severity":"red","message":"auth bypass"}', "]", "[]"),
                      ("```json\n" + RED + "\n```", "```json\n[]\n```"),
                      ("```json\n[{\"severity\":\"red\",", '"message":"auth bypass"}]\n```', "[]")):
            with self.subTest(parts=parts[0][:10]):
                with self.assertRaises(RuntimeError):
                    _opencode(*parts)

    def test_an_empty_string_final_after_findings_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "final text is not valid JSON"):
            _opencode(RED, "[]", "")

    def test_streamed_findings_are_normalised(self):
        self.assertEqual(_opencode('[{"severity":"major","title":"T","path":"a.py","line":"3"}]'),
                         ({"severity": "red", "message": "T", "file": "a.py", "line": 3},))


if __name__ == "__main__":
    unittest.main()
