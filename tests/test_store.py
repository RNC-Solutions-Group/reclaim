from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from .helpers import write_bytes, write_config

from reclaim.config import SourceSpec, load_config
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

    def test_v3_database_adds_selected_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "v3.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE plans (
                    plan_id TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL UNIQUE,
                    plan_schema_version INTEGER NOT NULL,
                    source_run_id TEXT NOT NULL,
                    analysis_id TEXT NOT NULL,
                    destination_root TEXT NOT NULL,
                    destination_template TEXT NOT NULL,
                    duplicate_policy TEXT NOT NULL,
                    target_platform TEXT NOT NULL,
                    max_path_length INTEGER NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    source_snapshot_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', '3')"
            )
            connection.execute(
                """
                INSERT INTO plans(
                    plan_id, plan_digest, plan_schema_version, source_run_id,
                    analysis_id, destination_root, destination_template,
                    duplicate_policy, target_platform, max_path_length,
                    policy_fingerprint, source_snapshot_json, summary_json,
                    status, created_at
                ) VALUES (?, ?, 1, 'run', 'analysis', ?, '{rel_path}',
                          'prefer_one', 'linux', 240, 'fp', '{}', '{}',
                          'draft', '2026-01-01')
                """,
                ("plan-old", "a" * 64, str(Path(tmp) / "destination")),
            )
            connection.commit()
            connection.close()
            with Store(database) as store:
                version = store.conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "5")
                row = store.conn.execute(
                    "SELECT destination_selected, destination_root FROM plans"
                ).fetchone()
                self.assertEqual(row["destination_selected"], row["destination_root"])

    def test_v4_database_adds_evidence_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "v4.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', '4')"
            )
            connection.commit()
            connection.close()
            with Store(database) as store:
                version = store.conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "5")
                tables = {
                    row["name"]
                    for row in store.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {"evidence_records", "evidence_occurrences", "evidence_runs"}
                    <= tables
                )

    def test_cache_is_invalidated_when_source_root_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "first"
            second = base / "second"
            mtime = 1_700_000_000_000_000_000
            write_bytes(first / "same.txt", b"AAAA", mtime_ns=mtime)
            write_bytes(second / "same.txt", b"BBBB", mtime_ns=mtime)
            config = load_config(write_config(base, {"source-a": first}))
            with Store(config.database_path) as store:
                store.upsert_source(
                    "source-a", "source-a", str(first), config.sources[0].priority
                )
                first_identity = store.source_identities()["source-a"]
                self.assertTrue(first_identity)
                run_id = store.start_run(config.fingerprint(), store.source_identities())
                store.insert_entries(
                    run_id, SourceScanner(config.sources[0], config.scan).walk()
                )
                store.seed_hashes(run_id, hash_media_libraries=False)
                target = list(store.hash_targets(run_id))[0]
                outcome = hash_file(
                    first / "same.txt",
                    expected_size=target.size_bytes,
                    expected_mtime_ns=target.modified_ns,
                )
                store.save_hash(target, outcome)
                store.commit()
                self.assertEqual(store.counts(run_id)["hash_ok"], 1)

                # Re-registering the same root preserves the cache.
                store.upsert_source("source-a", "source-a", str(first), 100)
                self.assertEqual(
                    store.conn.execute("SELECT COUNT(*) FROM hash_cache").fetchone()[0],
                    1,
                )

                # A different root with the same relative path, size, and mtime
                # must not inherit the cached digest.
                store.upsert_source("source-a", "source-a", str(second), 100)
                second_identity = store.source_identities()["source-a"]
                self.assertNotEqual(first_identity, second_identity)
                self.assertEqual(
                    store.conn.execute("SELECT COUNT(*) FROM hash_cache").fetchone()[0],
                    0,
                )

                second_run = store.start_run(
                    config.fingerprint(), store.source_identities()
                )
                spec = SourceSpec("source-a", "source-a", second, 100)
                store.insert_entries(
                    second_run, SourceScanner(spec, config.scan).walk()
                )
                store.seed_hashes(second_run, hash_media_libraries=False)
                counts = store.counts(second_run)
                self.assertEqual(counts["hash_pending"], 1)
                self.assertEqual(counts["hash_ok"], 0)

                target2 = list(store.hash_targets(second_run))[0]
                self.assertEqual(target2.source_root_identity, second_identity)
                outcome2 = hash_file(
                    second / "same.txt",
                    expected_size=target2.size_bytes,
                    expected_mtime_ns=target2.modified_ns,
                )
                store.save_hash(target2, outcome2)
                self.assertEqual(
                    outcome2.digest, hashlib.sha256(b"BBBB").hexdigest()
                )

    def test_v1_database_is_migrated_and_legacy_cache_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            database = base / "v1.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE sources (
                    source_id TEXT PRIMARY KEY, label TEXT NOT NULL,
                    root_path TEXT NOT NULL, canonical_root TEXT NOT NULL,
                    priority INTEGER NOT NULL, registered_at TEXT NOT NULL
                );
                CREATE TABLE scan_runs (
                    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    finished_at TEXT, tool_version TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    source_fingerprints_json TEXT, notes TEXT
                );
                CREATE TABLE hash_cache (
                    source_id TEXT NOT NULL, rel_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
                    algorithm TEXT NOT NULL, digest TEXT NOT NULL,
                    signature_hex TEXT, hashed_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, rel_path, size_bytes, modified_ns, algorithm)
                );
                """
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', '1')"
            )
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                ("source-a", "source-a", str(source), str(source), 100, "2026-01-01"),
            )
            connection.execute(
                """
                INSERT INTO hash_cache(source_id, rel_path, size_bytes, modified_ns,
                                       algorithm, digest, signature_hex, hashed_at)
                VALUES ('source-a', 'a.txt', 4, 1, 'sha256', ?, NULL, '2026-01-01')
                """,
                ("a" * 64,),
            )
            connection.commit()
            connection.close()

            with Store(database) as store:
                version = store.conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "5")
                columns = {
                    row["name"] for row in store.conn.execute("PRAGMA table_info(hash_cache)")
                }
                self.assertIn("root_identity", columns)
                store.upsert_source("source-a", "source-a", str(source), 100)
                # Legacy rows have no root identity and must not be reused.
                self.assertEqual(
                    store.conn.execute("SELECT COUNT(*) FROM hash_cache").fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
