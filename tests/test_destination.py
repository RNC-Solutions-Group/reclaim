from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import make_junction, write_bytes

from reclaim.destination import (
    DestinationItem,
    DestinationOccupied,
    DestinationPolicy,
    name_keys,
    normalize_relative,
    publish_no_overwrite,
    validate_plan,
    validate_relative,
)


class DestinationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "destination"
        self.root.mkdir()

    def test_normalize_rejects_traversal_and_absolute(self) -> None:
        for value in ("../escape", "/absolute", "a/../b", "a//b", "C:/x", "a\\b"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_relative(value)
        self.assertEqual(normalize_relative("a/b.txt"), "a/b.txt")

    def test_windows_reserved_and_invalid_names(self) -> None:
        policy = DestinationPolicy("windows")
        for name in ("CON.txt", "aux", "LPT9.log", "a:b.txt", "trail. ", "bad?.txt"):
            with self.subTest(name=name):
                issues = validate_relative(f"Docs/{name}", policy)
                self.assertTrue(issues, name)
        self.assertEqual(validate_relative("Docs/normal.txt", policy), [])

    def test_windows_path_length(self) -> None:
        policy = DestinationPolicy("windows", max_path_length=60)
        result = validate_plan(
            self.root,
            [DestinationItem(1, "a/" + ("x" * 80) + ".txt", 1)],
            policy,
        )
        self.assertTrue(any(issue.code == "PATH_TOO_LONG" for issue in result.issues))

    def test_linux_allows_windows_invalid_characters(self) -> None:
        policy = DestinationPolicy("linux")
        self.assertEqual(validate_relative("Docs/a:b.txt", policy), [])
        self.assertTrue(validate_relative("CON.txt", DestinationPolicy("windows")))

    def test_case_and_unicode_keys(self) -> None:
        windows = DestinationPolicy("windows")
        mac = DestinationPolicy("macos")
        linux = DestinationPolicy("linux")
        composed = "Résumé.txt"
        decomposed = "Re\u0301sume\u0301.txt"
        self.assertTrue(name_keys(composed, windows) & name_keys(decomposed, windows))
        self.assertTrue(name_keys(composed, mac) & name_keys(decomposed, mac))
        self.assertFalse(name_keys(composed, linux) & name_keys(decomposed, linux))
        self.assertTrue(name_keys("A.txt", windows) & name_keys("a.txt", windows))
        self.assertFalse(name_keys("A.txt", linux) & name_keys("a.txt", linux))

    def test_duplicate_targets_and_case_collisions(self) -> None:
        policy = DestinationPolicy("windows")
        items = [
            DestinationItem(1, "Docs/A.txt", 1),
            DestinationItem(2, "Docs/a.txt", 1),
        ]
        result = validate_plan(self.root, items, policy)
        self.assertFalse(result.ok)
        codes = {issue.code for issue in result.issues}
        self.assertIn("DUPLICATE_DESTINATION", codes)

    def test_prefix_conflict(self) -> None:
        policy = DestinationPolicy("linux")
        items = [
            DestinationItem(1, "Docs/a", 1),
            DestinationItem(2, "Docs/a/b.txt", 1),
        ]
        result = validate_plan(self.root, items, policy)
        self.assertIn("PATH_PREFIX_CONFLICT", {i.code for i in result.issues})

    def test_existing_destination_is_a_conflict(self) -> None:
        write_bytes(self.root / "Docs" / "a.txt", b"existing")
        policy = DestinationPolicy("linux")
        result = validate_plan(
            self.root, [DestinationItem(1, "Docs/a.txt", 1)], policy
        )
        self.assertIn("DESTINATION_EXISTS", {i.code for i in result.issues})
        self.assertEqual((self.root / "Docs" / "a.txt").read_bytes(), b"existing")

    def test_missing_root_is_rejected(self) -> None:
        policy = DestinationPolicy("linux")
        result = validate_plan(self.root / "missing", [], policy)
        self.assertIn("DESTINATION_MISSING", {i.code for i in result.issues})

    def test_unwritable_root_is_rejected(self) -> None:
        policy = DestinationPolicy("linux")
        with mock.patch("os.access", return_value=False):
            result = validate_plan(self.root, [], policy)
        self.assertIn("DESTINATION_NOT_WRITABLE", {i.code for i in result.issues})

    def test_capacity_failure(self) -> None:
        policy = DestinationPolicy("linux")
        with mock.patch("shutil.disk_usage") as usage:
            usage.return_value = mock.Mock(free=1)
            result = validate_plan(
                self.root, [DestinationItem(1, "a.txt", 100)], policy
            )
        self.assertIn("INSUFFICIENT_SPACE", {i.code for i in result.issues})

    def test_junction_root_is_rejected(self) -> None:
        target = Path(self.tmp.name) / "real"
        target.mkdir()
        link = Path(self.tmp.name) / "linked"
        if not make_junction(link, target):
            self.skipTest("junction/symlink creation not permitted")
        policy = DestinationPolicy("linux")
        result = validate_plan(link, [], policy)
        codes = {i.code for i in result.issues}
        self.assertTrue(
            {"DESTINATION_REPARSE", "DESTINATION_REDIRECTED"} & codes, codes
        )

    def test_publish_does_not_overwrite(self) -> None:
        temp = Path(self.tmp.name) / "temp.bin"
        dest = self.root / "dest.bin"
        write_bytes(temp, b"new")
        publish_no_overwrite(temp, dest)
        self.assertEqual(dest.read_bytes(), b"new")
        self.assertFalse(temp.exists())
        write_bytes(temp, b"replacement")
        with self.assertRaises(DestinationOccupied):
            publish_no_overwrite(temp, dest)
        self.assertEqual(dest.read_bytes(), b"new")

    def test_publish_reports_existing_target(self) -> None:
        temp = Path(self.tmp.name) / "temp2.bin"
        dest = self.root / "existing.bin"
        write_bytes(temp, b"new")
        write_bytes(dest, b"old")
        with self.assertRaises(DestinationOccupied):
            publish_no_overwrite(temp, dest)
        self.assertEqual(dest.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
