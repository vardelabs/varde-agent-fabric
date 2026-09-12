"""Deterministic public-safe evaluation for review artifact outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.review_contracts import ContractError, ReviewArtifact


_EXPECTED = {
    "completed-valid": "clean",
    "malformed-artifact": "rejected",
    "stale-artifact": "rejected",
    "unavailable-worker": "rejected",
}


def evaluate_fixture(path: Path) -> str:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    case = payload["case"]
    expected = _EXPECTED[case]
    try:
        artifact = ReviewArtifact.from_dict(payload["artifact"])
        if case == "completed-valid":
            actual = "clean" if artifact.finish_reason == "completed" else "rejected"
        elif case == "malformed-artifact":
            actual = "clean"
        elif case == "stale-artifact":
            actual = "rejected" if artifact.head_sha != payload["expected_head_sha"] else "clean"
        elif case == "unavailable-worker":
            actual = "clean" if artifact.finish_reason == "completed" else "rejected"
        else:
            raise KeyError(case)
    except (ContractError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        actual = "rejected"
    if actual != expected:
        raise AssertionError(f"{path.name}: expected {expected}, got {actual}")
    return actual


def run(fixtures_dir: Path) -> dict[str, int]:
    counts = {"clean": 0, "rejected": 0}
    for path in sorted(fixtures_dir.glob("*.json")):
        counts[evaluate_fixture(path)] += 1
    return counts


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = run(root / "fixtures" / "review")
    print(json.dumps(result, sort_keys=True))
