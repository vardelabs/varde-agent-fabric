#!/usr/bin/env python3
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import time
import json
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.review_contracts import MAX_TRANSPORT_BYTES, ContractError, ReviewArtifact, ReviewRequest
from dispatch.gateway_client import GatewayError, GatewayUnavailable
from tests.adapter_doubles import patch_local_review
from dispatch.remote_runner import RemoteReviewMetadata, build_review_request, run_remote_review

BASE = "a" * 40
HEAD = "b" * 40


class RemoteEntrypointTests(unittest.TestCase):
    def metadata(self, **changes):
        values = dict(
            repository="example-org/example",
            pull_request=19,
            base_sha=BASE,
            head_sha=HEAD,
            correlation_id="corr-19",
            paths=("src/app.py",),
            task="Review this change",
            diff=None,
            workdir="/work",
            model="codex-model",
            local_backend="/bin/false",
            output="/tmp/review.json",
            timeout_seconds=30,
        )
        values.update(changes)
        return RemoteReviewMetadata(**values)

    def test_request_is_constructed_only_from_explicit_review_metadata(self):
        request = build_review_request(self.metadata())
        self.assertEqual(request.to_dict(), {
            "schema": "review-request.v1",
            "repository": "example-org/example",
            "pull_request": 19,
            "base_sha": BASE,
            "head_sha": HEAD,
            "paths": ["src/app.py"],
            "kind": "reviewer",
            "worker_kind": "codex",
            "correlation_id": "corr-19",
            "packet": "Review this change",
            "timeout_seconds": 30,
            "model": "codex-model",
        })

    def test_remote_request_carries_model_separately_from_harness(self):
        model = "opencode-go/kimi-k2.7-code"
        request = build_review_request(self.metadata(model=model, harness="opencode", target="remote"))

        self.assertEqual(request.worker_kind, "opencode")
        self.assertEqual(request.to_dict()["model"], model)
        self.assertEqual(ReviewRequest.from_dict(request.to_dict()).model, model)

    def test_model_field_is_optional_for_legacy_requests_and_validated_when_present(self):
        model = "opencode-go/kimi-k3"
        request = build_review_request(self.metadata(model=model, harness="opencode", target="remote"))
        payload = request.to_dict()
        legacy_payload = dict(payload)
        legacy_payload.pop("model")
        self.assertIsNone(ReviewRequest.from_dict(legacy_payload).model)

        for invalid in ("", "opencode-go/kimi k3", "x" * 129):
            with self.subTest(model=invalid), self.assertRaises(ContractError):
                ReviewRequest.from_dict({**legacy_payload, "model": invalid})

    def test_malformed_harness_is_rejected_by_request_contract(self):
        with self.assertRaises(ContractError):
            build_review_request(self.metadata(harness="Llama Broker", target="remote"))

    def test_remote_gemma_never_falls_back(self):
        metadata = self.metadata(harness="local-gemma", target="remote")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                with patch_local_review("codex") as local:
                    with self.assertRaises(GatewayUnavailable):
                        run_remote_review(metadata, fallback_policy="remote-unavailable")
                    local.assert_not_called()

    def test_local_fallback_preserves_safe_caller_path_and_home(self):
        metadata = self.metadata(local_backend="/bin/local")
        request = build_review_request(metadata)
        payload = json.dumps({"findings": []})
        with tempfile.TemporaryDirectory() as workdir:
            metadata = self.metadata(workdir=workdir, output=f"{workdir}/remote.json")
            with mock.patch.dict(os.environ, {"PATH": "/custom/bin:/usr/bin", "HOME": "/caller-home", "DISPATCH_GATEWAY_TOKEN": "must-not-forward"}, clear=False):
                with mock.patch("subprocess.run") as run:
                    def execute(*args, **kwargs):
                        Path(kwargs["env"]["DISPATCH_OUTPUT"]).write_text(payload, encoding="utf-8")
                        return mock.Mock(returncode=0)
                    run.side_effect = execute
                    from dispatch.adapters.codex import run_local_review as _run_local_codex
                    _run_local_codex(metadata, request)
                    environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["PATH"], "/custom/bin:/usr/bin")
            self.assertEqual(environment["HOME"], "/caller-home")
            self.assertNotIn("DISPATCH_GATEWAY_TOKEN", environment)

    def test_opencode_remote_unavailable_uses_same_model_local_harness(self):
        model = "opencode-go/kimi-k3"
        with tempfile.TemporaryDirectory() as workdir:
            caller_home = Path(workdir) / "caller-home"
            opencode_home = Path(workdir) / "opencode-home"
            caller_home.mkdir()
            opencode_home.mkdir()
            diff = Path(workdir) / "change.diff"
            diff.write_text("diff --git a/app.py b/app.py" + chr(10), encoding="utf-8")
            metadata = self.metadata(
                harness="opencode", model=model, local_backend="/bin/opencode",
                workdir=workdir, diff=str(diff), output=f"{workdir}/review.json",
            )
            stream = json.dumps({"type": "text", "part": {"text": json.dumps([
                {"severity": "red", "message": "verified finding"}
            ])}}) + chr(10) + json.dumps({"type": "step_finish"}) + chr(10)
            with mock.patch.dict(os.environ, {
                "DISPATCH_GATEWAY_URL": "https://private.example",
                "DISPATCH_GATEWAY_TOKEN": "must-not-forward",
                "PATH": "/custom/bin:/usr/bin",
                "HOME": str(caller_home),
                "DISPATCH_OPENCODE_HOME": str(opencode_home),
            }, clear=False):
                with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                    client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                    with mock.patch("subprocess.run") as run:
                        def execute(*args, **kwargs):
                            Path(kwargs["env"]["DISPATCH_OUTPUT"]).write_text(stream, encoding="utf-8")
                            return mock.Mock(returncode=0)
                        run.side_effect = execute
                        try:
                            result = run_remote_review(metadata, fallback_policy="remote-unavailable")
                        except GatewayUnavailable as exc:
                            self.fail(f"expected local OpenCode fallback, got remote failure: {exc}")
            env = run.call_args.kwargs["env"]

        self.assertEqual(result.provider, model)
        self.assertEqual(result.worker_kind, "opencode")
        self.assertEqual(result.execution_location, "requester")
        self.assertEqual(result.findings, ({"severity": "red", "message": "verified finding"},))
        self.assertEqual(result.fallback, "cluster-unavailable-local-opencode")
        self.assertEqual(env["DISPATCH_MODEL"], model)
        self.assertEqual(env["DISPATCH_OPENCODE_HOME"], str(opencode_home))
        self.assertEqual(env["HOME"], str(caller_home))
        self.assertEqual(env["DISPATCH_DIFF"], str(diff))
        deadline = int(env["DISPATCH_DEADLINE_EPOCH"])
        self.assertLessEqual(abs(deadline - (int(time.time()) + run.call_args.kwargs["timeout"] - 5)), 5)
        self.assertNotIn("DISPATCH_GATEWAY_TOKEN", env)

    def _run_real_opencode_fallback(self, prior_events: str = ""):
        model = "opencode-go/kimi-k3"
        with tempfile.TemporaryDirectory() as workdir:
            root = Path(workdir)
            caller_home = root / "caller-home"
            opencode_home = root / "opencode-home"
            fake_bin = root / "bin"
            caller_home.mkdir()
            opencode_home.mkdir()
            fake_bin.mkdir()
            events = prior_events + json.dumps({"type": "text", "part": {"text": json.dumps([
                {"severity": "red", "message": "verified finding"}
            ])}}) + "\n" + json.dumps({"type": "step_finish"}) + "\n"
            (opencode_home / "events.jsonl").write_text(events, encoding="utf-8")
            fake_cli = fake_bin / "opencode"
            fake_cli.write_text(
                f'''#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "--version" ]]; then echo "opencode v2.0.0"; exit 0; fi
[[ "$HOME" == "$DISPATCH_OPENCODE_HOME" ]] || exit 31
[[ "$DISPATCH_MODEL" == "{model}" ]] || exit 32
[[ " $* " == *"--model {model}"* ]] || exit 33
[[ " $* " == *"--file $DISPATCH_DIFF"* ]] || exit 34
[[ " $* " == *"JSON array of findings"* ]] || exit 36
[[ -z "${{DISPATCH_GATEWAY_TOKEN:-}}${{DISPATCH_GATEWAY_URL:-}}${{OPENAI_API_KEY:-}}${{OPENCODE_GO_API_KEY:-}}" ]] || exit 37
[[ -f "$DISPATCH_DIFF" ]] || exit 35
cat "$HOME/events.jsonl"
''',
                encoding="utf-8",
            )
            fake_cli.chmod(0o700)
            diff = root / "change.diff"
            diff.write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
            backend = Path(__file__).resolve().parents[1] / "dispatch" / "cli" / "backends" / "opencode.sh"
            metadata = self.metadata(
                harness="opencode", model=model, local_backend=str(backend),
                workdir=workdir, diff=str(diff), output=f"{workdir}/review.json",
            )
            with mock.patch.dict(os.environ, {
                "DISPATCH_GATEWAY_URL": "https://private.example",
                "DISPATCH_GATEWAY_TOKEN": "must-not-forward",
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "HOME": str(caller_home),
                "DISPATCH_OPENCODE_HOME": str(opencode_home),
            }, clear=False):
                with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                    client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                    try:
                        result = run_remote_review(metadata, fallback_policy="remote-unavailable")
                    except GatewayUnavailable as exc:
                        self.fail(f"expected local OpenCode fallback, got remote failure: {exc}")
        self.assertEqual(result.provider, model)
        return result

    def test_opencode_local_fallback_runs_real_adapter_with_same_model_and_home(self):
        result = self._run_real_opencode_fallback()
        self.assertEqual(result.worker_kind, "opencode")
        self.assertEqual(result.execution_location, "requester")
        self.assertEqual(result.findings, ({"severity": "red", "message": "verified finding"},))
        self.assertEqual(result.fallback, "cluster-unavailable-local-opencode")

    def test_opencode_fallback_reads_a_stream_longer_than_the_transport_limit(self):
        # A review that reads files streams their text as tool events before its small final findings.
        read = json.dumps({"type": "tool_use", "part": {"tool": "read", "state": {"output": "x" * 10240}}}) + "\n"
        reads = read * (MAX_TRANSPORT_BYTES // len(read) + 2)
        result = self._run_real_opencode_fallback(reads)
        self.assertEqual(result.findings, ({"severity": "red", "message": "verified finding"},))

    def test_opencode_fallback_does_not_run_for_http_400(self):
        metadata = self.metadata(harness="opencode", model="opencode-go/qwen3.8-flash")
        with mock.patch.dict(os.environ, {
            "DISPATCH_GATEWAY_URL": "https://private.example",
            "DISPATCH_GATEWAY_TOKEN": "must-not-forward",
        }, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = GatewayError("gateway returned HTTP 400")
                with patch_local_review("opencode") as local:
                    with self.assertRaisesRegex(GatewayError, "HTTP 400"):
                        run_remote_review(metadata, fallback_policy="remote-unavailable")
                    local.assert_not_called()

    def test_opencode_fallback_rejects_missing_final_text_event(self):
        model = "opencode-go/qwen3.8-flash"
        with tempfile.TemporaryDirectory() as workdir:
            root = Path(workdir)
            caller_home = root / "caller-home"
            opencode_home = root / "opencode-home"
            caller_home.mkdir()
            opencode_home.mkdir()
            diff = root / "change.diff"
            diff.write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
            metadata = self.metadata(
                harness="opencode", model=model, workdir=workdir,
                diff=str(diff), output=f"{workdir}/review.json",
            )
            with mock.patch.dict(os.environ, {
                "DISPATCH_GATEWAY_URL": "https://private.example",
                "DISPATCH_GATEWAY_TOKEN": "must-not-forward",
                "DISPATCH_OPENCODE_HOME": str(opencode_home),
                "HOME": str(caller_home),
            }, clear=False):
                with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                    client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                    with mock.patch("subprocess.run") as run:
                        def execute(*args, **kwargs):
                            Path(kwargs["env"]["DISPATCH_OUTPUT"]).write_text(
                                json.dumps({"type": "step_finish"}) + "\n", encoding="utf-8"
                            )
                            return mock.Mock(returncode=0)
                        run.side_effect = execute
                        try:
                            run_remote_review(metadata, fallback_policy="remote-unavailable")
                        except GatewayUnavailable as exc:
                            self.fail(f"expected local artifact validation, got remote failure: {exc}")
                        except RuntimeError as exc:
                            self.assertIn("no final text event", str(exc))
                        else:
                            self.fail("expected incomplete OpenCode output to be rejected")

    def test_remote_route_calls_gateway_without_local_fallback(self):
        req = build_review_request(self.metadata())
        expected = ReviewArtifact("example-org/example", 19, BASE, HEAD, "codex", "cluster", "codex", "corr-19", (), "completed")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.return_value = expected
                result = run_remote_review(self.metadata(), fallback_policy="none")
        self.assertEqual(result, expected)
        client_type.assert_called_once_with("https://private.example", "token", ssl_context=None)
        client_type.return_value.submit.assert_called_once_with(req)

    def test_only_gateway_unavailable_uses_local_codex_and_preserves_identity(self):
        metadata = self.metadata(local_backend="/bin/local")
        expected = ReviewArtifact("example-org/example", 19, BASE, HEAD, "codex", "requester", "codex-model", "corr-19", (), "completed")
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = GatewayUnavailable("offline")
                with patch_local_review("codex", return_value=expected) as local:
                    result = run_remote_review(metadata, fallback_policy="remote-unavailable")
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")
        self.assertEqual((result.repository, result.pull_request, result.base_sha, result.head_sha, result.correlation_id), ("example-org/example", 19, BASE, HEAD, "corr-19"))
        local.assert_called_once()

    def test_http_503_response_status_uses_local_fallback(self):
        metadata = self.metadata(local_backend="/bin/local")
        expected = ReviewArtifact("example-org/example", 19, BASE, HEAD, "codex", "requester", "codex-model", "corr-19", (), "completed")
        class UnavailableResponse:
            status = 503
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, limit=-1): return b""
        with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient", return_value=__import__("dispatch.gateway_client", fromlist=["GatewayClient"]).GatewayClient("https://private.example", "token", opener=lambda *args, **kwargs: UnavailableResponse())):
                with patch_local_review("codex", return_value=expected):
                    result = run_remote_review(metadata, fallback_policy="remote-unavailable")
        self.assertEqual(result.fallback, "cluster-unavailable-local-codex")

    def test_fallback_artifact_provider_must_match_selected_model(self):
        model = "opencode-go/kimi-k3"
        request = build_review_request(self.metadata(harness="opencode", model=model))
        artifact = ReviewArtifact(
            "example-org/example", 19, BASE, HEAD, "opencode", "requester",
            "opencode-go/qwen3.8-flash", "corr-19", (), "completed",
        )
        from dispatch.fallback_policy import validate_fallback_artifact
        with self.assertRaisesRegex(RuntimeError, "selected model"):
            validate_fallback_artifact(artifact, request)

    def test_opencode_worker_errors_never_fall_back(self):
        metadata = self.metadata(harness="opencode", model="opencode-go/qwen3.8-flash")
        error = GatewayError("worker returned a rejected-artifact response")
        with mock.patch.dict(os.environ, {
            "DISPATCH_GATEWAY_URL": "https://private.example",
            "DISPATCH_GATEWAY_TOKEN": "token",
        }, clear=False):
            with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                client_type.return_value.submit.side_effect = error
                with patch_local_review("opencode") as local:
                    with self.assertRaises(GatewayError):
                        run_remote_review(metadata, fallback_policy="remote-unavailable")
                    local.assert_not_called()

    def test_contract_or_worker_errors_never_fallback(self):
        for error in (GatewayError("bad artifact"), RuntimeError("worker failed")):
            with self.subTest(error=error):
                with mock.patch.dict(os.environ, {"DISPATCH_GATEWAY_URL": "https://private.example", "DISPATCH_GATEWAY_TOKEN": "token"}, clear=False):
                    with mock.patch("dispatch.remote_runner.GatewayClient") as client_type:
                        client_type.return_value.submit.side_effect = error
                        with patch_local_review("codex") as local:
                            with self.assertRaises(type(error)):
                                run_remote_review(self.metadata(), fallback_policy="remote-unavailable")
                            local.assert_not_called()

    def run_main_with_local_codex(self, backend_body: str) -> tuple[int, str, Path]:
        from dispatch.remote_runner import main
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir)
        backend = workdir / "backend.sh"
        backend.write_text("#!/bin/sh\n" + backend_body, encoding="utf-8")
        backend.chmod(0o755)
        output = workdir / "review.json"
        argv = ["remote_runner.py", "--output", str(output), "--local-backend", str(backend),
                "--harness", "codex", "--target", "remote", "--fallback-policy", "remote-unavailable",
                "--model", "codex-model", "--workdir", str(workdir), "--repository", "example-org/example",
                "--pull-request", "19", "--base-sha", BASE, "--head-sha", HEAD,
                "--correlation-id", "corr-19", "--path", "src/app.py", "--task", "Review this diff for bugs."]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stderr", stderr):
            with mock.patch("dispatch.remote_runner._gateway_client") as client:
                client.return_value.submit.side_effect = GatewayUnavailable("offline")
                status = main()
        return status, stderr.getvalue(), output

    def test_local_codex_fallback_asks_a_prose_task_for_json_findings(self):
        # The fake model answers in JSON only when told to, as a prose-prompted model does.
        status, stderr, output = self.run_main_with_local_codex(
            'case "$DISPATCH_TASK" in\n'
            '  *"Return only a JSON array of findings"*) printf "[]" > "$DISPATCH_OUTPUT" ;;\n'
            '  *) printf "Looks fine to me." > "$DISPATCH_OUTPUT" ;;\n'
            'esac\n'
        )
        self.assertEqual(status, 0, stderr)
        artifact = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(artifact["fallback"], "cluster-unavailable-local-codex")
        self.assertEqual(artifact["findings"], [])

    def test_local_codex_prose_output_names_the_local_fallback(self):
        status, stderr, output = self.run_main_with_local_codex('printf "Looks fine to me." > "$DISPATCH_OUTPUT"\n')
        self.assertEqual(status, 4)
        self.assertIn("local review failed", stderr)
        self.assertIn("local Codex output is not JSON", stderr)
        self.assertNotIn("remote review failed", stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
