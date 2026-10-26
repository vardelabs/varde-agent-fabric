#!/usr/bin/env python3
"""The local review fallback artifact of each existing adapter, byte for byte."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dispatch.gateway_client import GatewayUnavailable
from dispatch.remote_runner import RemoteReviewMetadata, run_remote_review

FINDING = {"severity": "red", "message": "golden finding", "file": "src/app.py", "line": 3}
BACKEND_OUTPUT = {
    "codex": json.dumps([FINDING]),
    "opencode": json.dumps({"type": "text", "part": {"text": json.dumps([FINDING])}}) + "\n",
}
# Captured from main at the adapter seam (22710f9), before roles were added.
GOLDEN = {
    "codex": '{"schema":"review-artifact.v1","repository":"example-org/example","pull_request":7,"base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","head_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","worker_kind":"codex","execution_location":"requester","provider":"example/model","correlation_id":"golden-1","findings":[{"severity":"red","message":"golden finding","file":"src/app.py","line":3}],"finish_reason":"completed","fallback":"cluster-unavailable-local-codex","latency_ms":0}',
    "opencode": '{"schema":"review-artifact.v1","repository":"example-org/example","pull_request":7,"base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","head_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","worker_kind":"opencode","execution_location":"requester","provider":"example/model","correlation_id":"golden-1","findings":[{"severity":"red","message":"golden finding","file":"src/app.py","line":3}],"finish_reason":"completed","fallback":"cluster-unavailable-local-opencode","latency_ms":0}',
}


def fallback_artifact(harness: str) -> str:
    with tempfile.TemporaryDirectory() as workdir:
        diff = Path(workdir) / "change.diff"
        diff.write_text("diff --git a/src/app.py b/src/app.py\n", encoding="utf-8")
        home = Path(workdir) / "opencode-home"
        home.mkdir()
        metadata = RemoteReviewMetadata(
            "example-org/example", 7, "a" * 40, "b" * 40, "golden-1", ("src/app.py",), "Review it",
            str(diff), workdir, "example/model", "/bin/backend", f"{workdir}/out.json", harness,
        )

        def backend(*args, **kwargs):
            Path(kwargs["env"]["DISPATCH_OUTPUT"]).write_text(BACKEND_OUTPUT[harness], encoding="utf-8")
            return mock.Mock(returncode=0)

        environment = {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token",
                       "DISPATCH_OPENCODE_HOME": str(home)}
        with mock.patch.dict(os.environ, environment), mock.patch("dispatch.remote_runner.GatewayClient") as client, \
                mock.patch("subprocess.run", side_effect=backend):
            client.return_value.submit.side_effect = GatewayUnavailable("offline")
            artifact = run_remote_review(metadata, fallback_policy="remote-unavailable")
    return json.dumps(artifact.to_dict(), ensure_ascii=False, separators=(",", ":"))


class FallbackGoldenTests(unittest.TestCase):
    def test_existing_adapters_produce_the_same_fallback_artifact_bytes(self):
        for harness, expected in GOLDEN.items():
            with self.subTest(harness=harness):
                self.assertEqual(fallback_artifact(harness), expected)


if __name__ == "__main__":
    if sys.argv[1:] == ["--print"]:
        for name in GOLDEN:
            print(fallback_artifact(name))
    else:
        unittest.main()
