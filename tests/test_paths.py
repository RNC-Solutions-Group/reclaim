from __future__ import annotations

import os
import unittest
from pathlib import Path

from .helpers import SRC  # noqa: F401  (ensures sys.path)

from reclaim.errors import SafetyError
from reclaim.paths import (
    assert_disjoint,
    assert_safe_relative,
    canonical_path,
    is_within,
    long_path,
    parent_rel,
    paths_overlap,
)


class PathTests(unittest.TestCase):
    def test_within_and_overlap(self) -> None:
        base = Path(os.path.abspath(os.sep + "tmp" if os.name != "nt" else "C:\\tmp"))
        self.assertTrue(is_within(base / "a" / "b", base))
        self.assertFalse(is_within(base, base / "a"))
        self.assertTrue(paths_overlap(base / "a", base / "a" / "b"))
        self.assertFalse(paths_overlap(base / "a", base / "b"))

    def test_assert_disjoint(self) -> None:
        with self.assertRaises(SafetyError):
            assert_disjoint({"source": "/data/src", "work": "/data/src/work"})
        assert_disjoint({"source": "/data/src", "work": "/data/work"})

    def test_safe_relative(self) -> None:
        assert_safe_relative("Documents/report.pdf")
        assert_safe_relative(".")
        for unsafe in ("../escape", "/absolute", "a/../b", "C:/windows"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(SafetyError):
                    assert_safe_relative(unsafe)

    def test_parent_rel(self) -> None:
        self.assertIsNone(parent_rel("."))
        self.assertEqual(parent_rel("a"), ".")
        self.assertEqual(parent_rel("a/b/c"), "a/b")

    def test_long_path_prefix(self) -> None:
        deep = "C:\\" + ("x" * 300)
        result = long_path(deep)
        if os.name == "nt":
            self.assertTrue(result.startswith("\\\\?\\"))
        else:
            self.assertEqual(result, deep)
        self.assertEqual(long_path("C:\\short"), "C:\\short")

    def test_canonical_is_absolute(self) -> None:
        self.assertTrue(canonical_path(".").is_absolute())


if __name__ == "__main__":
    unittest.main()
