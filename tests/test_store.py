from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .helpers import write_bytes, write_config

from reclaim.config import load_config
from reclaim.hashing import hash_file
from reclaim.scanner import SourceScanner
from reclaim.store import Store


class StoreTests(unittest.TestCase):
    def test_scan_seed_hash_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            write_bytes(source / "a.txt", b"alpha")
            write_bytes(source / "b.txt", b"beta")
            config = load_config(write_config(base, {"source-a": source}))
            with Store(config.database_path) as store:
                store.upsert_source(
                    "source-a", "source-a", str(source), config.sources[0].priority
                )
                run_id = store.start_run(config.fingerprint())
                scanner = SourceScanner(config.sources[0], config.scan)
                store.insert_entries(run_id, scanner.walk())
                store.seed_hashes(run_id, hash_media_libraries=False)
                counts = store.counts(run_id)
                self.assertEqual(counts["files"], 2)
                self.assertEqual(counts["hash_pending"], 2)

                targets = list(store.hash_targets(run_id))
                self.assertEqual(len(targets), 2)
                for target in targets:
                    path = source.joinpath(*target.rel_path.split("/"))
                    outcome = hash_file(
                        path,
                        expected_size=target.size_bytes,
                        expected_mtime_ns=target.modified_ns,
                    )
                    store.save_hash(target, outcome)
                store.commit()
                counts = store.counts(run_id)
                self.assertEqual(counts["hash_ok"], 2)
                cache_rows = store.conn.execute(
                    "SELECT COUNT(*) AS n FROM hash_cache"
                ).fetchone()["n"]
                self.assertEqual(cache_rows, 2)

                # A fresh run must reuse cached hashes for unchanged files.
                second_run = store.start_run(config.fingerprint())
                scanner2 = SourceScanner(config.sources[0], config.scan)
                store.insert_entries(second_run, scanner2.walk())
                store.seed_hashes(second_run, hash_media_libraries=False)
                counts2 = store.counts(second_run)
                self.assertEqual(counts2["hash_ok"], 2)
                self.assertEqual(counts2["hash_pending"], 0)

    def test_counts_on_empty_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            config = load_config(write_config(base, {"source-a": source}))
            with Store(config.database_path) as store:
                store.upsert_source("source-a", "source-a", str(source), 100)
                run_id = store.start_run("abc")
                counts = store.counts(run_id)
                self.assertEqual(counts["entries"], 0)
                self.assertEqual(counts["hash_pending"], 0)


if __name__ == "__main__":
    unittest.main()
