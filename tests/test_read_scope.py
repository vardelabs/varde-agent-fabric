#!/usr/bin/env python3
"""Focused tests for the deployment-owned read-scope policy."""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dispatch import read_scope
from dispatch.read_scope import ReadScopeError, load_private_roots, systemd_inaccessible_paths


class FifoOpenBlocked(Exception):
    """Test sentinel that must not be converted into ReadScopeError."""


class OsDouble:
    def __init__(self, **overrides: object) -> None:
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(os, name)


class ReadScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        euid = os.geteuid()
        checkout = Path(__file__).resolve()
        for ancestor in checkout.parents:
            info = os.lstat(ancestor)
            if info.st_uid not in (0, euid) or info.st_mode & 0o022:
                raise AssertionError(
                    f"checkout is under an untrusted ancestor: {ancestor}; "
                    "use a checkout whose path is not group/world writable"
                )

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
            systemd_inaccessible_paths((str(self.north), str(self.corvus)), str(self.work)),
            (
                f"--property=InaccessiblePaths={self.corvus}",
                f"--property=InaccessiblePaths={self.north}",
            ),
        )

    def test_emits_one_property_for_nested_roots(self) -> None:
        nested = self.corvus / "nested"
        nested.mkdir(mode=0o700)
        self.assertEqual(
            systemd_inaccessible_paths((str(nested), str(self.corvus)), str(self.work)),
            (f"--property=InaccessiblePaths={self.corvus}",),
        )

    def test_rejects_fifo_without_blocking(self) -> None:
        fifo = self.temp_root / "overlay.fifo"
        os.mkfifo(fifo, 0o600)

        def timeout_handler(signum: int, frame: object) -> None:
            raise FifoOpenBlocked("FIFO open blocked")

        previous = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(2)
        try:
            with self.assertRaises(ReadScopeError):
                load_private_roots(fifo)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

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
        nested_workdir = self.corvus / "review"
        nested_workdir.mkdir(mode=0o700)
        with self.assertRaisesRegex(ReadScopeError, "would hide"):
            systemd_inaccessible_paths((str(self.corvus),), str(nested_workdir))

    def test_rejects_deeply_nested_json_with_read_scope_error(self) -> None:
        document = "[" * 20000 + "]" * 20000
        self.config.write_text(document)
        with self.assertRaisesRegex(ReadScopeError, "valid UTF-8 JSON"):
            load_private_roots(self.config)

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
        for suffix in ("my secret", 'my"quote', "my'apostrophe", "my\ttab", "my\nnewline", "my-newline\n", "my;semicolon", "my`backtick", "my$dollar", "my\\backslash"):
            root = Path(self.temp_root / suffix)
            root.mkdir(mode=0o700)
            self.config.write_text(json.dumps({"private_roots": [str(root)]}))
            with self.subTest(root=root), self.assertRaises(ReadScopeError):
                load_private_roots(self.config)

    def test_rejects_double_leading_slash(self) -> None:
        self.config.write_text(json.dumps({"private_roots": ["//tmp"]}))
        with self.assertRaises(ReadScopeError):
            load_private_roots(self.config)

    def test_rejects_oversized_valid_config(self) -> None:
        document = {"private_roots": [str(self.corvus)], "padding": "x" * (64 * 1024)}
        self.config.write_text(json.dumps(document))
        with self.assertRaisesRegex(ReadScopeError, "exceeds"):
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

        # Replace the module binding, not the process-global os module.
        patched_os = OsDouble(lstat=lstat_with_foreign_leaf)
        with mock.patch.object(read_scope, "os", patched_os):
            properties = systemd_inaccessible_paths((str(foreign_root),), str(self.work))
        self.assertEqual(properties, (f"--property=InaccessiblePaths={foreign_root}",))

    def test_rejects_world_writable_private_root(self) -> None:
        root = self.temp_root / "world-writable"
        root.mkdir(mode=0o700)
        root.chmod(0o777)
        with self.assertRaises(ReadScopeError):
            systemd_inaccessible_paths((str(root),), str(self.work))

    def test_rejects_foreign_owned_ancestor(self) -> None:
        root = self.temp_root / "foreign-ancestor" / "root"
        root.mkdir(mode=0o700, parents=True)
        ancestor = root.parent
        real_lstat = os.lstat

        def lstat_with_foreign_ancestor(path: str | os.PathLike[str]) -> os.stat_result:
            result = real_lstat(path)
            if Path(path) == ancestor:
                values = list(result)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return result

        # Replace the module binding, not the process-global os module.
        patched_os = OsDouble(lstat=lstat_with_foreign_ancestor)
        with mock.patch.object(read_scope, "os", patched_os):
            with self.assertRaisesRegex(ReadScopeError, "not owned"):
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
