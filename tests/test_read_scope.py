#!/usr/bin/env python3
"""Focused tests for the deployment-owned read-scope policy."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dispatch.read_scope import ReadScopeError, load_private_roots, systemd_inaccessible_paths


class ReadScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep all fixture ancestors inside the checkout, independent of ambient HOME/TMPDIR.
        self.control_base = Path(__file__).resolve().parent / ".fabric-test"
        self.control_base.mkdir(mode=0o700, parents=True, exist_ok=True)

        self.temp_root = Path(tempfile.mkdtemp(dir=self.control_base, prefix="test-"))
        self.temp_root.chmod(0o700)

        # Set up test fixtures
        self.config = self.temp_root / "private-overlay.json"
        self.corvus = self.temp_root / "corvus"
        self.north = self.temp_root / "north"
        self.work = self.temp_root / "work"

        self.corvus.mkdir(mode=0o700)
        self.north.mkdir(mode=0o700)
        self.work.mkdir(mode=0o700)

        self.config.write_text(
            json.dumps({"private_roots": [str(self.north), str(self.corvus), str(self.north)]})
        )
        self.config.chmod(0o600)

    def tearDown(self) -> None:
        if hasattr(self, "temp_root") and self.temp_root.exists():
            shutil.rmtree(self.temp_root, ignore_errors=True)
        if hasattr(self, "control_base"):
            try:
                self.control_base.rmdir()
            except OSError:
                pass

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

    def test_rejects_fifo_without_blocking(self) -> None:
        fifo = self.temp_root / "overlay.fifo"
        fifo_mode = 0o600
        os.mkfifo(fifo, fifo_mode)
        with self.assertRaises(ReadScopeError):
            load_private_roots(fifo)

    def test_config_symlinked_ancestor_is_rejected(self) -> None:
        target = self.temp_root / "config-target"
        target.mkdir()
        target_config = target / "overlay.json"
        target_config.write_text(json.dumps({"private_roots": [str(self.corvus)]}))
        target_config.chmod(0o600)
        link = self.temp_root / "config-link"
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
        for root in ("relative", "/data/../corvus", "/", str(self.temp_root / "missing")):
            with self.subTest(root=root), self.assertRaises(ReadScopeError):
                systemd_inaccessible_paths((root,), str(self.work))

    def test_rejects_symlinked_private_root(self) -> None:
        link = self.temp_root / "link"
        link.symlink_to(self.corvus, target_is_directory=True)
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(link),), str(self.work))

    def test_rejects_symlinked_workdir(self) -> None:
        link = self.temp_root / "work-link"
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
        target = self.temp_root / "real.json"
        target.write_text(json.dumps({"private_roots": [str(self.corvus)]}))
        target.chmod(0o600)
        self.config.unlink()
        self.config.symlink_to(target)
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_rejects_paths_with_unsafe_characters(self) -> None:
        for suffix in ("my secret", 'my"quote', "my'apostrophe", "my\ttab", "my\nnewline", "my;semicolon", "my`backtick", "my$dollar", "my\\backslash"):
            root = str(self.temp_root / suffix)
            self.config.write_text(json.dumps({"private_roots": [root]}))
            with self.subTest(root=root), self.assertRaises(ReadScopeError):
                load_private_roots(self.config)

    def test_rejects_double_leading_slash(self) -> None:
        self.config.write_text(json.dumps({"private_roots": ["//tmp"]}))
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_rejects_oversized_valid_config(self) -> None:
        document = {"private_roots": [str(self.corvus)], "padding": "x" * (64 * 1024)}
        self.config.write_text(json.dumps(document))
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_allows_private_root_owned_by_another_tenant(self) -> None:
        foreign_root = self.temp_root / "foreign-tenant"
        foreign_root.mkdir(mode=0o700)
        real_lstat = os.lstat
        foreign_uid = os.geteuid() + 1

        def lstat_with_foreign_leaf(path: str | os.PathLike[str]) -> os.stat_result:
            result = real_lstat(path)
            if Path(path) == foreign_root:
                values = list(result)
                values[4] = foreign_uid
                return os.stat_result(values)
            return result

        with mock.patch("dispatch.read_scope.os.lstat", side_effect=lstat_with_foreign_leaf):
            properties = systemd_inaccessible_paths((str(foreign_root),), str(self.work))
        self.assertEqual(properties, (f"--property=InaccessiblePaths={foreign_root}",))

    def test_rejects_world_writable_private_root(self) -> None:
        root = self.temp_root / "world-writable"
        root.mkdir(mode=0o700)
        root.chmod(0o777)
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(root),), str(self.work))

    def test_rejects_group_writable_ancestor(self) -> None:
        """Ancestor trust: all ancestors must not be group/world writable."""
        # Create a world-writable ancestor
        untrusted = self.temp_root / "untrusted"
        untrusted.mkdir(mode=0o755)
        untrusted.chmod(0o777)  # Make it world-writable

        root = untrusted / "root"
        root.mkdir(mode=0o700)

        self.config.write_text(json.dumps({"private_roots": [str(root)]}))
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_accepts_version_key(self) -> None:
        """Forward compatibility: version key should be accepted."""
        self.config.write_text(json.dumps({
            "private_roots": [str(self.corvus)],
            "version": 1
        }))
        # Should not raise
        result = load_private_roots(self.config)
        self.assertEqual(result, (str(self.corvus),))

    def test_rejects_invalid_version_type(self) -> None:
        """Version must be the supported version 1 if present."""
        for version in (0, -1, 2, 99, "1", 1.5, None, True):
            self.config.write_text(json.dumps({
                "private_roots": [str(self.corvus)],
                "version": version
            }))
            with self.subTest(version=version), self.assertRaises(ReadScopeError):
                load_private_roots(self.config)


if __name__ == "__main__":
    unittest.main()
