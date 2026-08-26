#!/usr/bin/env python3
"""Focused tests for the deployment-owned read-scope policy."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dispatch.read_scope import ReadScopeError, load_private_roots, systemd_inaccessible_paths


class ReadScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "private-overlay.json"
        self.corvus = self.root / "corvus"
        self.north = self.root / "north"
        self.corvus.mkdir()
        self.north.mkdir()
        self.config.write_text(
            json.dumps({"private_roots": [str(self.north), str(self.corvus), str(self.north)]})
        )
        self.config.chmod(0o600)
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_loads_deduplicated_sorted_roots(self) -> None:
        self.assertEqual(load_private_roots(self.config), (str(self.corvus), str(self.north)))

    def test_emits_systemd_properties_deterministically(self) -> None:
        self.assertEqual(
            systemd_inaccessible_paths((str(self.corvus), str(self.north)), str(self.work)),
            (
                f"--property=InaccessiblePaths={self.corvus}",
                f"--property=InaccessiblePaths={self.north}",
            ),
        )

    def test_config_symlinked_ancestor_is_rejected(self) -> None:
        target = self.root / "config-target"
        target.mkdir()
        target_config = target / "overlay.json"
        target_config.write_text(json.dumps({"private_roots": [str(self.corvus)]}))
        target_config.chmod(0o600)
        link = self.root / "config-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ReadScopeError):
            load_private_roots(link / "overlay.json")

    def test_task_cannot_supply_relative_or_non_normalized_root(self) -> None:
        for root in ("relative", "/data/../corvus", "/"):
            self.config.write_text(json.dumps({"private_roots": [root]}))
            with self.subTest(root=root), self.assertRaises(ReadScopeError):
                load_private_roots(self.config)

    def test_rejects_world_writable_overlay(self) -> None:
        self.config.chmod(0o666)
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_rejects_invalid_roots_at_property_boundary(self) -> None:
        for root in ("relative", "/data/../corvus", "/", str(self.root / "missing")):
            with self.subTest(root=root), self.assertRaises(ReadScopeError):
                systemd_inaccessible_paths((root,), str(self.work))

    def test_rejects_symlinked_private_root(self) -> None:
        link = self.root / "link"
        link.symlink_to(self.corvus, target_is_directory=True)
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(link),), str(self.work))

    def test_rejects_symlinked_workdir(self) -> None:
        link = self.root / "work-link"
        link.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(self.corvus),), str(link))

    def test_rejects_workdir_inside_private_root(self) -> None:
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(self.corvus),), str(self.corvus / "review"))

    def test_rejects_empty_or_malformed_policy(self) -> None:
        for document in ({}, {"private_roots": []}, {"private_roots": "/data/corvus"}):
            self.config.write_text(json.dumps(document))
            with self.subTest(document=document), self.assertRaises(ReadScopeError):
                load_private_roots(self.config)

    def test_config_symlink_is_rejected(self) -> None:
        target = self.root / "real.json"
        target.write_text(json.dumps({"private_roots": ["/data/corvus"]}))
        target.chmod(0o600)
        self.config.unlink()
        self.config.symlink_to(target)
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)


if __name__ == "__main__":
    unittest.main()
