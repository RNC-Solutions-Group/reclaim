from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from .helpers import write_bytes, write_config

from reclaim.analysis import FileRow, build_duplicates, source_overlap
from reclaim.cli import main
from reclaim.config import load_config
from reclaim.scanner import SourceScanner


def file_row(
    entry_id: int,
    source_id: str,
    rel_path: str,
    size: int,
    digest: str | None,
    *,
    priority: int = 100,
    status: str = "ok",
) -> FileRow:
    return FileRow(
        entry_id=entry_id,
        source_id=source_id,
        source_label=source_id,
        priority=priority,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        depth=rel_path.count("/") + 1,
        size_bytes=size,
        modified_ns=None,
        created_ns=None,
        is_media_library=False,
        is_cloud=False,
        is_hidden=False,
        digest=digest,
        hash_status=status,
        signature_hex=None,
        kind="document",
    )


class DuplicateTests(unittest.TestCase):
    def test_cross_source_exact_duplicates(self) -> None:
        rows = [
            file_row(1, "source-a", "Docs/report.docx", 10, "a" * 64, priority=10),
            file_row(2, "source-b", "Docs/report-copy.docx", 10, "a" * 64, priority=50),
            file_row(3, "source-a", "Docs/unique.txt", 5, "b" * 64, priority=10),
            file_row(4, "source-b", "Docs/unique-b.txt", 7, "c" * 64, priority=50),
        ]
        groups = build_duplicates(rows)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.size_bytes, 10)
        self.assertEqual(len(group.occurrences), 2)
        self.assertEqual(group.redundant_bytes, 10)
        self.assertEqual(group.canonical.source_id, "source-a")

    def test_zero_byte_files_are_not_duplicate_groups(self) -> None:
        rows = [
            file_row(1, "source-a", "a.txt", 0, "e" * 64),
            file_row(2, "source-b", "b.txt", 0, "e" * 64),
        ]
        self.assertEqual(build_duplicates(rows), [])

    def test_hashed_error_is_not_a_duplicate(self) -> None:
        rows = [
            file_row(1, "source-a", "a.txt", 10, None, status="error"),
            file_row(2, "source-b", "b.txt", 10, None, status="error"),
        ]
        self.assertEqual(build_duplicates(rows), [])

    def test_source_overlap_distinguishes_unique_and_shared(self) -> None:
        rows = [
            file_row(1, "source-a", "a.txt", 10, "a" * 64),
            file_row(2, "source-b", "b.txt", 10, "a" * 64),
            file_row(3, "source-a", "c.txt", 5, "b" * 64),
            file_row(4, "source-b", "d.txt", 7, "c" * 64),
        ]
        overlap = source_overlap(rows)
        a = overlap["sources"]["source-a"]
        b = overlap["sources"]["source-b"]
        self.assertEqual(a["unique_content_bytes"], 5)
        self.assertEqual(a["shared_content_bytes"], 10)
        self.assertEqual(b["unique_content_bytes"], 7)
        self.assertEqual(overlap["pairs"][("source-a", "source-b")][1], 10)


class EndToEndTests(unittest.TestCase):
    def test_scan_hash_export_and_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            alpha = base / "alpha"
            beta = base / "beta"
            write_bytes(alpha / "Docs" / "report.docx", b"same")
            write_bytes(alpha / "Docs" / "unique.txt", b"alpha-only")
            write_bytes(alpha / "Downloads" / "setup.exe", b"MZ" + b"0" * 10)
            write_bytes(alpha / "empty.txt", b"")
            write_bytes(alpha / "Documents" / ".hidden.txt", b"hidden")
            write_bytes(
                alpha / "Photos" / "iPhoto Library" / "Originals" / "photo.jpg",
                b"\xff\xd8\xff" + b"0" * 10,
            )
            write_bytes(beta / "Docs" / "report-copy.docx", b"same")
            write_bytes(beta / "Docs" / "unique-b.txt", b"beta-only")
            write_bytes(beta / "Downloads" / "archive.zip", b"PK\x03\x04" + b"0" * 10)

            config_path = write_config(base, {"source-a": alpha, "source-b": beta})
            config = load_config(config_path)
            before = {
                (entry.source_id, entry.rel_path): (entry.size_bytes, entry.modified_ns)
                for source in config.sources
                for entry in SourceScanner(source, config.scan).walk()
                if entry.entry_type == "file"
            }

            self.assertEqual(main(["scan", "--config", str(config_path)]), 0)
            self.assertEqual(main(["hash", "--config", str(config_path)]), 0)
            self.assertEqual(main(["export", "--config", str(config_path)]), 0)

            metrics = json.loads(
                (config.reports_dir / "cp1-metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metrics["totals"]["files"], 9)
            self.assertEqual(metrics["totals"]["duplicate_groups"], 1)
            self.assertEqual(metrics["totals"]["redundant_bytes"], 4)
            self.assertEqual(metrics["totals"]["deferred_files"], 1)
            self.assertGreaterEqual(metrics["totals"]["hashed_files"], 8)
            self.assertTrue(metrics["structure"]["media_libraries"])
            self.assertEqual(
                metrics["structure"]["media_libraries"][0]["rel_path"],
                "Photos/iPhoto Library",
            )

            with (config.reports_dir / "cp1-source-inventory.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertGreaterEqual(len(rows), 9)
            photo = next(
                row
                for row in rows
                if row["rel_path"] == "Photos/iPhoto Library/Originals/photo.jpg"
            )
            self.assertEqual(photo["hash_status"], "deferred")
            duplicate = next(
                row for row in rows if row["rel_path"] == "Docs/report-copy.docx"
            )
            canonical = next(
                row for row in rows if row["rel_path"] == "Docs/report.docx"
            )
            self.assertEqual(duplicate["sha256"], canonical["sha256"])
            self.assertEqual(len(duplicate["sha256"]), 64)

            with (config.reports_dir / "cp1-disposition-candidates.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                dispositions = list(csv.DictReader(handle))
            copy_row = next(
                row
                for row in dispositions
                if row["rel_path"] == "Docs/report-copy.docx"
            )
            self.assertEqual(copy_row["recommended_disposition"], "duplicate")
            setup_row = next(
                row for row in dispositions if row["rel_path"] == "Downloads/setup.exe"
            )
            self.assertEqual(setup_row["recommended_disposition"], "needs_review")

            after = {
                (entry.source_id, entry.rel_path): (entry.size_bytes, entry.modified_ns)
                for source in config.sources
                for entry in SourceScanner(source, config.scan).walk()
                if entry.entry_type == "file"
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
