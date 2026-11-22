from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from .helpers import make_junction, write_bytes, write_config

from reclaim.config import load_config
from reclaim.scanner import SourceScanner


class ScannerTests(unittest.TestCase):
    def _build(self, root: Path) -> None:
        write_bytes(root / "loose.txt", b"root")
        write_bytes(root / "Documents" / "report.txt", b"alpha")
        write_bytes(root / "Documents" / "nested" / "data.bin", b"beta")
        (root / "Documents" / "empty").mkdir(parents=True)
        write_bytes(root / "Documents" / ".hidden.txt", b"hidden")
        write_bytes(root / "Documents" / "Ünïcode nâme.txt", b"unicode")
        write_bytes(
            root / "Photos" / "iPhoto Library" / "Originals" / "photo.jpg", b"\xff\xd8\xffx"
        )
        write_bytes(
            root / "Photos" / "iPhoto Library" / "Thumbnails" / "thumb.jpg", b"\xff\xd8\xffy"
        )

    def test_enumerates_files_dirs_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            self._build(source)
            config = load_config(write_config(base, {"source-a": source}))
            scanner = SourceScanner(config.sources[0], config.scan)
            entries = list(scanner.walk())
            by_rel = {entry.rel_path: entry for entry in entries}

            self.assertIn(".", by_rel)
            self.assertIn("Documents/report.txt", by_rel)
            self.assertIn("Documents/Ünïcode nâme.txt", by_rel)
            self.assertIn("Documents/empty", by_rel)
            self.assertTrue(by_rel["Documents/empty"].entry_type == "dir")
            self.assertTrue(by_rel["Documents/.hidden.txt"].is_hidden)
            self.assertFalse(by_rel["Documents/report.txt"].is_media_library)
            self.assertTrue(
                by_rel["Photos/iPhoto Library/Originals/photo.jpg"].is_media_library
            )
            self.assertEqual(by_rel["Documents/report.txt"].parent_rel_path, "Documents")
            self.assertEqual(by_rel["loose.txt"].parent_rel_path, ".")

            fingerprint = scanner.fingerprint
            self.assertEqual(fingerprint.file_count, 7)
            self.assertGreater(fingerprint.entry_count, 7)
            self.assertEqual(fingerprint.errors, 0)

            # Determinism: a second scan must produce the same fingerprint.
            second = SourceScanner(config.sources[0], config.scan)
            list(second.walk())
            self.assertEqual(fingerprint.digest, second.fingerprint.digest)

    def test_junction_is_recorded_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "outside"
            write_bytes(target / "outside.txt", b"secret")
            source.mkdir()
            if not make_junction(source / "link", target):
                self.skipTest("junction/symlink creation not permitted")
            config = load_config(write_config(base, {"source-a": source}))
            scanner = SourceScanner(config.sources[0], config.scan)
            entries = list(scanner.walk())
            by_rel = {entry.rel_path: entry for entry in entries}
            self.assertEqual(by_rel["link"].entry_type, "reparse")
            self.assertNotIn("link/outside.txt", by_rel)
            self.assertEqual(scanner.fingerprint.file_count, 0)

    def test_long_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            deep = source
            for _ in range(12):
                deep = deep / ("d" * 25)
            try:
                write_bytes(deep / "deep.txt", b"deep")
            except OSError:
                self.skipTest("long paths not available on this filesystem")
            config = load_config(write_config(base, {"source-a": source}))
            scanner = SourceScanner(config.sources[0], config.scan)
            entries = list(scanner.walk())
            self.assertTrue(any(entry.name == "deep.txt" for entry in entries))
            self.assertEqual(scanner.fingerprint.file_count, 1)

    def test_excluded_entry_is_recorded_but_not_in_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            write_bytes(source / "keep.txt", b"keep")
            write_bytes(source / "transfer.zip", b"x" * 10)
            config_path = write_config(
                base,
                {"source-a": source},
                scan_extra='include_hidden = true\nexclude_names = ["transfer.zip"]',
            )
            config = load_config(config_path)
            first = SourceScanner(config.sources[0], config.scan)
            entries = list(first.walk())
            by_rel = {entry.rel_path: entry for entry in entries}
            self.assertEqual(by_rel["transfer.zip"].entry_type, "excluded")
            self.assertEqual(first.fingerprint.file_count, 1)
            fingerprint_before = first.fingerprint

            write_bytes(source / "transfer.zip", b"x" * 10_000)
            second = SourceScanner(config.sources[0], config.scan)
            list(second.walk())
            self.assertEqual(second.fingerprint.digest, fingerprint_before.digest)
            self.assertEqual(second.fingerprint.total_bytes, fingerprint_before.total_bytes)


if __name__ == "__main__":
    unittest.main()
