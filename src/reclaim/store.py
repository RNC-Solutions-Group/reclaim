"""SQLite project store for inventory, hashes, and verification evidence.

Observed facts live in ``entries`` and ``hashes``.  Recommendations are
derived at report time from policy configuration, so a policy change never
rewrites the observed inventory.  Future checkpoints add override, plan, and
execution tables without altering these.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .errors import SchemaError
from .hashing import HashOutcome
from .paths import canonical_path, root_identity
from .scanner import EntryRecord

SCHEMA_VERSION = "4"
ALGORITHM = "sha256"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class HashTarget:
    entry_id: int
    source_id: str
    source_root_identity: str
    rel_path: str
    size_bytes: int
    modified_ns: int
    is_cloud: bool


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    root_path TEXT NOT NULL,
    canonical_root TEXT NOT NULL,
    root_identity TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tool_version TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    source_fingerprints_json TEXT,
    source_identities_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    entry_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    rel_path TEXT NOT NULL,
    parent_rel_path TEXT,
    name TEXT NOT NULL,
    depth INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    size_bytes INTEGER,
    created_ns INTEGER,
    modified_ns INTEGER,
    accessed_ns INTEGER,
    mode INTEGER,
    attributes INTEGER NOT NULL DEFAULT 0,
    is_hidden INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_reparse INTEGER NOT NULL DEFAULT 0,
    is_cloud INTEGER NOT NULL DEFAULT 0,
    is_media_library INTEGER NOT NULL DEFAULT 0,
    link_target TEXT,
    error_code TEXT,
    error_message TEXT,
    note TEXT,
    UNIQUE (run_id, source_id, rel_path)
);

CREATE TABLE IF NOT EXISTS hashes (
    entry_id INTEGER PRIMARY KEY REFERENCES entries(entry_id) ON DELETE CASCADE,
    algorithm TEXT NOT NULL DEFAULT 'sha256',
    digest TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    bytes_read INTEGER,
    signature_hex TEXT,
    hashed_at TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS hash_cache (
    source_id TEXT NOT NULL,
    root_identity TEXT NOT NULL DEFAULT '',
    rel_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    digest TEXT NOT NULL,
    signature_hex TEXT,
    hashed_at TEXT NOT NULL,
    PRIMARY KEY (source_id, rel_path, size_bytes, modified_ns, algorithm)
);

CREATE TABLE IF NOT EXISTS scan_errors (
    error_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    rel_path TEXT,
    phase TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_checks (
    check_id INTEGER PRIMARY KEY,
    run_id TEXT,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_snapshots (
    analysis_id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES scan_runs(run_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    analysis_id TEXT NOT NULL REFERENCES analysis_snapshots(analysis_id) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE,
    disposition TEXT NOT NULL,
    destination_rel TEXT,
    rule_id TEXT NOT NULL,
    rationale TEXT NOT NULL,
    confidence REAL NOT NULL,
    duplicate_role TEXT,
    duplicate_group TEXT,
    canonical_entry_id INTEGER,
    PRIMARY KEY (analysis_id, entry_id)
);

CREATE TABLE IF NOT EXISTS overrides (
    override_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_rel TEXT NOT NULL,
    disposition TEXT NOT NULL,
    destination_rel TEXT,
    reason TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    plan_digest TEXT NOT NULL UNIQUE,
    plan_schema_version INTEGER NOT NULL,
    source_run_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    destination_root TEXT NOT NULL,
    destination_selected TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS plan_items (
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL,
    entry_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_rel_path TEXT NOT NULL,
    source_root_identity TEXT NOT NULL,
    disposition TEXT NOT NULL,
    destination_rel TEXT,
    expected_sha256 TEXT,
    size_bytes INTEGER NOT NULL,
    source_mtime_ns INTEGER,
    canonical_entry_id INTEGER,
    decision_source TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (plan_id, item_id)
);

CREATE TABLE IF NOT EXISTS plan_authorizations (
    authorization_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
    plan_digest TEXT NOT NULL,
    destination_root TEXT NOT NULL,
    authorized_at TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
    plan_digest TEXT NOT NULL,
    destination_root TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS execution_items (
    execution_item_id INTEGER PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    destination_rel TEXT,
    expected_sha256 TEXT,
    actual_sha256 TEXT,
    bytes_copied INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (execution_id, item_id)
);

CREATE TABLE IF NOT EXISTS execution_events (
    event_id INTEGER PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    item_id INTEGER,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_entries_run_source
    ON entries(run_id, source_id);
CREATE INDEX IF NOT EXISTS idx_entries_run_parent
    ON entries(run_id, source_id, parent_rel_path);
CREATE INDEX IF NOT EXISTS idx_entries_run_type
    ON entries(run_id, entry_type);
CREATE INDEX IF NOT EXISTS idx_entries_run_size
    ON entries(run_id, size_bytes);
CREATE INDEX IF NOT EXISTS idx_entries_run_media
    ON entries(run_id, is_media_library);
CREATE INDEX IF NOT EXISTS idx_hashes_digest ON hashes(digest);
CREATE INDEX IF NOT EXISTS idx_hashes_status ON hashes(status);
CREATE INDEX IF NOT EXISTS idx_scan_errors_run ON scan_errors(run_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_entry
    ON recommendations(entry_id);
CREATE INDEX IF NOT EXISTS idx_overrides_target
    ON overrides(active, source_id, scope, target_rel);
CREATE INDEX IF NOT EXISTS idx_plan_items_plan
    ON plan_items(plan_id, item_id);
CREATE INDEX IF NOT EXISTS idx_plan_authorizations_plan
    ON plan_authorizations(plan_id, plan_digest);
CREATE INDEX IF NOT EXISTS idx_executions_plan
    ON executions(plan_id, started_at);
CREATE INDEX IF NOT EXISTS idx_execution_items_execution
    ON execution_items(execution_id, item_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_execution
    ON execution_events(execution_id, sequence);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-131072")
        self._initialize()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.conn.executescript(SCHEMA)
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
        elif row["value"] == "1":
            self._migrate_v1_to_v2()
            self._migrate_v2_to_v3()
            self._migrate_v3_to_v4()
        elif row["value"] == "2":
            self._migrate_v2_to_v3()
            self._migrate_v3_to_v4()
        elif row["value"] == "3":
            self._migrate_v3_to_v4()
        elif row["value"] != SCHEMA_VERSION:
            raise SchemaError(
                f"Database schema {row['value']} is not supported by this build "
                f"(expected {SCHEMA_VERSION})"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_cache_identity "
            "ON hash_cache(source_id, root_identity)"
        )
        self.conn.commit()

    def _migrate_v1_to_v2(self) -> None:
        """Add source-root identity to cache keys.

        Existing cache rows receive an empty identity and are therefore not
        reusable until the source is registered with a real identity.  This is
        deliberately conservative: a v1 database never recorded which root
        produced a cache row, so reusing it could attach a hash to a different
        source that merely shares a relative path, size, and mtime.
        """

        source_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(sources)")
        }
        if "root_identity" not in source_columns:
            self.conn.execute(
                "ALTER TABLE sources ADD COLUMN root_identity TEXT NOT NULL DEFAULT ''"
            )
        cache_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(hash_cache)")
        }
        if "root_identity" not in cache_columns:
            self.conn.execute(
                "ALTER TABLE hash_cache ADD COLUMN root_identity TEXT NOT NULL DEFAULT ''"
            )
        run_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(scan_runs)")
        }
        if "source_identities_json" not in run_columns:
            self.conn.execute(
                "ALTER TABLE scan_runs ADD COLUMN source_identities_json TEXT"
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            ("2",),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('cache_identity_migration', ?)",
            ("v1 cache keys lacked root identity; cache entries invalidated",),
        )
        self.conn.commit()

    def _migrate_v2_to_v3(self) -> None:
        """Add the M1 analysis, override, plan, and execution tables.

        The tables are created by ``SCHEMA`` using ``CREATE TABLE IF NOT
        EXISTS``; this migration records the version and a human-readable note.
        """

        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            ("3",),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('m1_schema_migration', ?)",
            ("M1 planning, override, execution, journal, and reconciliation tables added",),
        )
        self.conn.commit()

    def _migrate_v3_to_v4(self) -> None:
        """Add the raw selected destination path for redirection validation."""

        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(plans)")
        }
        if "destination_selected" not in columns:
            self.conn.execute(
                "ALTER TABLE plans ADD COLUMN destination_selected TEXT NOT NULL DEFAULT ''"
            )
        self.conn.execute(
            "UPDATE plans SET destination_selected=destination_root "
            "WHERE destination_selected=''"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('m1a_destination_selected', ?)",
            ("raw selected destination path recorded separately from canonical root",),
        )
        self.conn.commit()

    # -- sources and runs --------------------------------------------------

    def upsert_source(
        self, source_id: str, label: str, root_path: str, priority: int
    ) -> None:
        canonical_root = str(canonical_path(root_path))
        identity = root_identity(root_path)
        existing = self.conn.execute(
            "SELECT root_identity FROM sources WHERE source_id=?", (source_id,)
        ).fetchone()
        if existing is not None and existing["root_identity"] != identity:
            # The registered root changed (or is a legacy empty identity).
            # Cached digests belong to the old root and must not be reused.
            self.conn.execute(
                "DELETE FROM hash_cache WHERE source_id=?", (source_id,)
            )
        self.conn.execute(
            """
            INSERT INTO sources(source_id, label, root_path, canonical_root,
                                root_identity, priority, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                label=excluded.label,
                root_path=excluded.root_path,
                canonical_root=excluded.canonical_root,
                root_identity=excluded.root_identity,
                priority=excluded.priority
            """,
            (
                source_id,
                label,
                root_path,
                canonical_root,
                identity,
                priority,
                utc_now_iso(),
            ),
        )

    def source_identities(self) -> dict[str, str]:
        return {
            row["source_id"]: row["root_identity"]
            for row in self.conn.execute(
                "SELECT source_id, root_identity FROM sources ORDER BY source_id"
            )
        }

    def start_run(
        self,
        config_sha256: str,
        source_identities: dict[str, str] | None = None,
    ) -> str:
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            """
            INSERT INTO scan_runs(run_id, started_at, tool_version,
                                  config_sha256, status, source_identities_json)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                utc_now_iso(),
                __version__,
                config_sha256,
                json.dumps(source_identities, sort_keys=True)
                if source_identities
                else None,
            ),
        )
        self.conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        fingerprints: dict[str, dict[str, object]],
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE scan_runs
            SET finished_at=?, status=?, source_fingerprints_json=?, notes=?
            WHERE run_id=?
            """,
            (
                utc_now_iso(),
                status,
                json.dumps(fingerprints, sort_keys=True),
                notes,
                run_id,
            ),
        )
        self.conn.commit()

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def set_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE scan_runs SET status=? WHERE run_id=?", (status, run_id)
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM scan_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise SchemaError(f"Unknown scan run: {run_id}")
        return row

    # -- entries -----------------------------------------------------------

    def insert_entries(self, run_id: str, records: Iterable[EntryRecord]) -> int:
        sql = """
        INSERT INTO entries(
            run_id, source_id, rel_path, parent_rel_path, name, depth,
            entry_type, size_bytes, created_ns, modified_ns, accessed_ns,
            mode, attributes, is_hidden, is_system, is_reparse, is_cloud,
            is_media_library, link_target, error_code, error_message, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        batch: list[tuple[object, ...]] = []
        total = 0
        for record in records:
            batch.append(self._entry_params(run_id, record))
            if len(batch) >= 1000:
                self.conn.executemany(sql, batch)
                total += len(batch)
                batch.clear()
        if batch:
            self.conn.executemany(sql, batch)
            total += len(batch)
        self.conn.commit()
        return total

    @staticmethod
    def _entry_params(run_id: str, record: EntryRecord) -> tuple[object, ...]:
        return (
            run_id,
            record.source_id,
            record.rel_path,
            record.parent_rel_path,
            record.name,
            record.depth,
            record.entry_type,
            record.size_bytes,
            record.created_ns,
            record.modified_ns,
            record.accessed_ns,
            record.mode,
            record.attributes,
            int(record.is_hidden),
            int(record.is_system),
            int(record.is_reparse),
            int(record.is_cloud),
            int(record.is_media_library),
            record.link_target,
            record.error_code,
            record.error_message,
            record.note,
        )

    def add_scan_error(
        self,
        run_id: str,
        source_id: str,
        rel_path: str | None,
        phase: str,
        code: str,
        message: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO scan_errors(run_id, source_id, rel_path, phase, code,
                                    message, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, source_id, rel_path, phase, code, message, utc_now_iso()),
        )

    def seed_hashes(self, run_id: str, *, hash_media_libraries: bool) -> None:
        """Populate pending, deferred, cached, and cloud-skipped hash rows."""

        if not hash_media_libraries:
            self.conn.execute(
                """
                INSERT INTO hashes(entry_id, algorithm, status, error_code, error_message)
                SELECT e.entry_id, ?, 'deferred', 'MEDIA_LIBRARY_DEFERRED',
                       'Photo/media library hashing deferred by CP1 scope amendment'
                FROM entries e
                WHERE e.run_id=? AND e.entry_type='file' AND e.is_media_library=1
                  AND NOT EXISTS (SELECT 1 FROM hashes h WHERE h.entry_id=e.entry_id)
                """,
                (ALGORITHM, run_id),
            )

        self.conn.execute(
            """
            INSERT INTO hashes(entry_id, algorithm, status, error_code, error_message)
            SELECT e.entry_id, ?, 'deferred', 'CLOUD_PLACEHOLDER',
                   'Cloud placeholder deferred to avoid hydration/download'
            FROM entries e
            WHERE e.run_id=? AND e.entry_type='file' AND e.is_cloud=1
              AND NOT EXISTS (SELECT 1 FROM hashes h WHERE h.entry_id=e.entry_id)
            """,
            (ALGORITHM, run_id),
        )

        self.conn.execute(
            """
            INSERT INTO hashes(entry_id, algorithm, digest, status, signature_hex,
                               hashed_at, bytes_read)
            SELECT e.entry_id, c.algorithm, c.digest, 'ok', c.signature_hex,
                   c.hashed_at, e.size_bytes
            FROM entries e
            JOIN sources s ON s.source_id = e.source_id
            JOIN hash_cache c
              ON c.source_id = e.source_id
             AND c.root_identity = s.root_identity
             AND c.rel_path = e.rel_path
             AND c.size_bytes = e.size_bytes
             AND c.modified_ns = e.modified_ns
            WHERE e.run_id=? AND e.entry_type='file'
              AND s.root_identity <> ''
              AND c.root_identity <> ''
              AND NOT EXISTS (SELECT 1 FROM hashes h WHERE h.entry_id=e.entry_id)
            """,
            (run_id,),
        )

        self.conn.execute(
            """
            INSERT INTO hashes(entry_id, algorithm, status)
            SELECT e.entry_id, ?, 'pending'
            FROM entries e
            WHERE e.run_id=? AND e.entry_type='file'
              AND NOT EXISTS (SELECT 1 FROM hashes h WHERE h.entry_id=e.entry_id)
            """,
            (ALGORITHM, run_id),
        )
        self.conn.commit()

    def hash_targets(
        self, run_id: str, *, retry_errors: bool = False
    ) -> Iterator[HashTarget]:
        statuses = ("pending", "error") if retry_errors else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        # Materialize before writing hash rows; a live cursor over ``hashes``
        # while updating the same table is avoidable lock risk.
        rows = self.conn.execute(
            f"""
            SELECT e.entry_id, e.source_id, s.root_identity, e.rel_path,
                   e.size_bytes, e.modified_ns, e.is_cloud
            FROM entries e
            JOIN hashes h ON h.entry_id = e.entry_id
            JOIN sources s ON s.source_id = e.source_id
            WHERE e.run_id=? AND h.status IN ({placeholders})
            ORDER BY e.source_id, e.rel_path
            """,
            (run_id, *statuses),
        ).fetchall()
        for row in rows:
            yield HashTarget(
                entry_id=row["entry_id"],
                source_id=row["source_id"],
                source_root_identity=row["root_identity"],
                rel_path=row["rel_path"],
                size_bytes=row["size_bytes"],
                modified_ns=row["modified_ns"],
                is_cloud=bool(row["is_cloud"]),
            )

    def save_hash(self, target: HashTarget, outcome: HashOutcome) -> None:
        self.conn.execute(
            """
            UPDATE hashes
            SET status=?, digest=?, error_code=?, error_message=?, bytes_read=?,
                signature_hex=?, hashed_at=?, duration_ms=?
            WHERE entry_id=?
            """,
            (
                outcome.status,
                outcome.digest,
                outcome.error_code,
                outcome.error_message,
                outcome.bytes_read,
                outcome.signature_hex,
                utc_now_iso(),
                outcome.duration_ms,
                target.entry_id,
            ),
        )
        if outcome.ok:
            self.conn.execute(
                """
                INSERT INTO hash_cache(source_id, root_identity, rel_path,
                                       size_bytes, modified_ns, algorithm,
                                       digest, signature_hex, hashed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, rel_path, size_bytes, modified_ns, algorithm)
                DO UPDATE SET root_identity=excluded.root_identity,
                              digest=excluded.digest,
                              signature_hex=excluded.signature_hex,
                              hashed_at=excluded.hashed_at
                """,
                (
                    target.source_id,
                    target.source_root_identity,
                    target.rel_path,
                    target.size_bytes,
                    target.modified_ns,
                    ALGORITHM,
                    outcome.digest,
                    outcome.signature_hex,
                    utc_now_iso(),
                ),
            )

    def commit(self) -> None:
        self.conn.commit()

    def counts(self, run_id: str) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
              COUNT(*) AS entries,
              SUM(entry_type='file') AS files,
              SUM(entry_type='dir') AS dirs,
              SUM(entry_type='reparse') AS reparse,
              SUM(entry_type='excluded') AS excluded,
              SUM(entry_type='unknown') AS unknown,
              SUM(CASE WHEN entry_type='file' THEN size_bytes ELSE 0 END) AS bytes
            FROM entries WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        hashes = self.conn.execute(
            """
            SELECT
              SUM(h.status='ok') AS ok,
              SUM(h.status='pending') AS pending,
              SUM(h.status='error') AS errors,
              SUM(h.status='deferred') AS deferred
            FROM hashes h JOIN entries e ON e.entry_id=h.entry_id
            WHERE e.run_id=?
            """,
            (run_id,),
        ).fetchone()
        return {
            **{key: int(row[key] or 0) for key in row.keys()},
            **{f"hash_{key}": int(hashes[key] or 0) for key in hashes.keys()},
        }

    def record_check(
        self, run_id: str | None, name: str, status: str, detail: str | None = None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO verification_checks(run_id, check_name, status, detail, checked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, name, status, detail, utc_now_iso()),
        )
        self.conn.commit()

    def add_entries_note(self, run_id: str, source_id: str, count: int) -> None:
        self.conn.execute(
            "UPDATE scan_runs SET notes = COALESCE(notes,'') || ? WHERE run_id=?",
            (f"{source_id}:{count} entries\n", run_id),
        )
        self.conn.commit()
