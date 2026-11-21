import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts.implement_contracts import ContractError
from contracts.write_contracts import (WriteArtifact, WriteProposal, WriteRequest, collect_write_files,
                                       verify_write_artifact)
from dispatch.gateway_client import GatewayClient, GatewayError
from dispatch.remote_runner import RemoteWriteMetadata, run_remote_write

BEFORE = hashlib.sha256(b"before").hexdigest()


class Response:
    status = 200

    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, n=-1): return self.body[:n]


def request():
    return WriteRequest("example-org/example", "agy", "example-model", "corr", "write it",
                        ("docs/a.md", "docs/new.md"), ({"path": "docs/a.md", "content": "before"},), 60)


def artifact(req, **overrides):
    changes = overrides.pop("changes", [{"path": "docs/a.md", "before_sha256": BEFORE, "after_text": "after"},
                                        {"path": "docs/new.md", "before_sha256": None, "after_text": "fresh"}])
    proposal = {"schema": "write-proposal.v1", "worker_kind": req.worker_kind, "model": req.model,
                "paths": list(overrides.pop("paths", req.paths)), "changes": changes, "response": "done"}
    data = {"schema": "write-artifact.v1", "repository": req.repository, "worker_kind": req.worker_kind,
            "model": req.model, "correlation_id": req.correlation_id, "snapshot_sha256": req.snapshot_sha256,
            "execution_location": "remote", "latency_ms": 3, "proposal": proposal}
    data.update(overrides)
    return data


def client_returning(data):
    return GatewayClient("https://gateway.example", "secret",
                         opener=lambda *a, **k: Response(json.dumps(data).encode()))


class WriteContractTests(unittest.TestCase):
    def test_request_round_trip_and_snapshot(self):
        req = request()
        again = WriteRequest.from_dict(req.to_dict())
        self.assertEqual(again, req)
        self.assertEqual(again.snapshot_sha256, req.snapshot_sha256)

    def test_request_may_name_only_files_to_create(self):
        req = WriteRequest("example-org/example", "agy", "m", "corr", "t", ("new.md",), (), 60)
        self.assertEqual(WriteRequest.from_dict(req.to_dict()).files, ())

    def test_request_refusals(self):
        good = request().to_dict()
        bad = (
            {"files": [{"path": "other.md", "content": "x"}]},
            {"paths": "docs/a.md"},
            {"files": {}},
            {"paths": ["../a.md"]},
            {"paths": ["docs/a.md", "docs/a.md"]},
            {"worker_kind": "Agy!"},
            {"model": ""},
            {"timeout_seconds": 901},
            {"timeout_seconds": True},
            {"task": "x" * 16385},
            {"schema": "write-request.v2"},
            {"extra": 1},
        )
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ContractError):
                WriteRequest.from_dict({**good, **change})

    def test_artifact_round_trip_and_worker_binding(self):
        req = request()
        art = WriteArtifact.from_dict(artifact(req))
        self.assertEqual(WriteArtifact.from_dict(art.to_dict()), art)
        self.assertIsInstance(art.proposal, WriteProposal)
        mismatched = artifact(req)
        mismatched["proposal"] = {**mismatched["proposal"], "model": "other"}
        with self.assertRaises(ContractError):
            WriteArtifact.from_dict(mismatched)

    def test_verify_refuses_each_identity_field_and_unsent_text(self):
        req = request()
        verify_write_artifact(WriteArtifact.from_dict(artifact(req)), req)
        for field, value in (("repository", "other/repo"), ("correlation_id", "other"),
                             ("snapshot_sha256", "0" * 64)):
            with self.subTest(field=field), self.assertRaises(ContractError):
                verify_write_artifact(WriteArtifact.from_dict(artifact(req, **{field: value})), req)
        for changes in ([{"path": "docs/a.md", "before_sha256": None, "after_text": "x"}],
                        [{"path": "docs/new.md", "before_sha256": "0" * 64, "after_text": "x"}]):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                verify_write_artifact(WriteArtifact.from_dict(artifact(req, changes=changes)), req)
        with self.assertRaises(ContractError):
            verify_write_artifact(WriteArtifact.from_dict(artifact(req, paths=["docs/a.md"], changes=[])), req)

    def test_collect_skips_missing_and_refuses_links(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs/a.md").write_text("before")
            self.assertEqual(collect_write_files(directory, ("docs/a.md", "docs/new.md", "gone/x.md")),
                             ({"path": "docs/a.md", "content": "before"},))
            (root / "link.md").symlink_to(root / "docs/a.md")
            (root / "dirlink").symlink_to(root / "docs")
            (root / "bin.md").write_bytes(b"\xff\xfe")
            for path in ("link.md", "dirlink/a.md", "bin.md"):
                with self.subTest(path=path), self.assertRaises(ContractError):
                    collect_write_files(directory, (path,))


class WriteClientTests(unittest.TestCase):
    def test_submit_write_posts_and_verifies(self):
        req = request()

        def opener(http_req, timeout):
            self.assertEqual(http_req.full_url, "https://gateway.example/v1/writes")
            self.assertEqual(timeout, req.timeout_seconds + 45)
            self.assertEqual(json.loads(http_req.data), req.to_dict())
            return Response(json.dumps(artifact(req)).encode())

        art = GatewayClient("https://gateway.example", "secret", opener=opener).submit_write(req)
        self.assertEqual([c["after_text"] for c in art.proposal.changes], ["after", "fresh"])

    def test_submit_write_refusals(self):
        req = request()
        for data in (artifact(req, correlation_id="other"), artifact(req, execution_location="laptop"),
                     artifact(req, changes=[{"path": "docs/a.md", "before_sha256": "0" * 64, "after_text": "x"}]),
                     {"schema": "write-proposal.v1"}):
            with self.subTest(data=data), self.assertRaises(GatewayError):
                client_returning(data).submit_write(req)


class RemoteWriteRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "docs/a.md").write_text("before")
        self.meta = RemoteWriteMetadata("example-org/example", "corr", ("docs/a.md", "docs/new.md"), "write it",
                                        self.directory.name, "agy", "example-model", timeout_seconds=60)

    def tearDown(self):
        self.directory.cleanup()

    def test_returns_the_inner_proposal_and_leaves_files_alone(self):
        with mock.patch("dispatch.remote_runner._gateway_client") as client:
            client.return_value.submit_write.side_effect = lambda req: WriteArtifact.from_dict(artifact(req))
            proposal = run_remote_write(self.meta, fallback_policy="none")
        self.assertEqual(proposal.to_dict()["schema"], "write-proposal.v1")
        self.assertEqual((self.root / "docs/a.md").read_text(), "before")
        self.assertFalse((self.root / "docs/new.md").exists())

    def test_refuses_when_a_named_file_changed_during_the_run(self):
        def submit(req):
            (self.root / "docs/a.md").write_text("edited meanwhile")
            return WriteArtifact.from_dict(artifact(req))

        with mock.patch("dispatch.remote_runner._gateway_client") as client:
            client.return_value.submit_write.side_effect = submit
            with self.assertRaisesRegex(GatewayError, "changed during the run"):
                run_remote_write(self.meta, fallback_policy="none")

    def test_refuses_any_fallback(self):
        with mock.patch("dispatch.remote_runner._gateway_client") as client:
            with self.assertRaises(GatewayError):
                run_remote_write(self.meta, fallback_policy="remote-unavailable")
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
