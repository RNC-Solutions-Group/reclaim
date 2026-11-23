"""Bounded evidence extraction service, cache, and reporting."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

from ..config import ProjectConfig
from ..errors import ReclaimError
from ..store import Store, utc_now_iso
from .contracts import (
    EVIDENCE_SCHEMA_VERSION,
    FAILURE_STATUSES,
    STATUS_HASH_UNAVAILABLE,
    STATUS_INPUT_TOO_LARGE,
    STATUS_MALFORMED,
    STATUS_SOURCE_CHANGED,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_TIMEOUT,
    STATUS_UNSUPPORTED,
    EvidenceRecord,
    observation,
)
from .registry import (
    EvidenceSettings,
    ExtractorSpec,
    extractor_for_suffix,
)


class EvidenceError(ReclaimError):
    """Evidence extraction cannot be planned or persisted."""


Progress = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class EvidenceTarget:
    entry_id: int
    source_id: str
    rel_path: str
    size_bytes: int
    digest: str
    hash_status: str
    suffix: str
    extractor_id: str
    extractor_version: str
    path: Path


@dataclass
class EvidenceRunResult:
    evidence_run_id: str
    source_run_id: str
    status: str
    counts: dict[str, int]
    total_entries: int
    cache_hits: int
    worker_calls: int
    started_at: str
    finished_at: str = ""


@dataclass
class EvidenceStats:
    evidence_run_id: str
    source_run_id: str
    status: str
    total_entries: int
    covered_entries: int
    outcomes: dict[str, int]
    formats: dict[str, int]
    cache_hits: int
    worker_calls: int
    failures: int
    unsupported: int
    cacheable_success: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _cache_key(
    *,
    content_sha256: str,
    source_format: str,
    extractor_id: str,
    extractor_version: str,
    config_fingerprint: str,
    evidence_schema_version: int = EVIDENCE_SCHEMA_VERSION,
) -> str:
    payload = {
        "content_sha256": content_sha256,
        "source_format": source_format,
        "extractor_id": extractor_id,
        "extractor_version": extractor_version,
        "config_fingerprint": config_fingerprint,
        "evidence_schema_version": evidence_schema_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _evidence_id(cache_key: str) -> str:
    return "ev-" + cache_key[:16]


def _package_root() -> Path:
    try:
        import reclaim

        return Path(reclaim.__file__).resolve().parent.parent
    except Exception:
        return Path(__file__).resolve().parents[2]


class EvidenceService:
    def __init__(
        self,
        store: Store,
        config: ProjectConfig,
        settings: EvidenceSettings | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.settings = settings or EvidenceSettings()

    # -- target discovery -------------------------------------------------

    def _targets(self, run_id: str) -> list[EvidenceTarget]:
        rows = self.store.conn.execute(
            """
            SELECT e.entry_id, e.source_id, e.rel_path, e.size_bytes,
                   h.digest, h.status, h.error_code
            FROM entries e
            LEFT JOIN hashes h ON h.entry_id=e.entry_id
            WHERE e.run_id=? AND e.entry_type='file'
            ORDER BY e.source_id, e.rel_path
            """,
            (run_id,),
        ).fetchall()
        targets: list[EvidenceTarget] = []
        for row in rows:
            suffix = Path(row["rel_path"]).suffix.casefold()
            spec = extractor_for_suffix(suffix)
            source = self.config.source_by_id(row["source_id"])
            path = source.path.joinpath(*row["rel_path"].split("/"))
            targets.append(
                EvidenceTarget(
                    entry_id=int(row["entry_id"]),
                    source_id=str(row["source_id"]),
                    rel_path=str(row["rel_path"]),
                    size_bytes=int(row["size_bytes"] or 0),
                    digest=str(row["digest"] or ""),
                    hash_status=str(row["status"] or "missing"),
                    suffix=suffix,
                    extractor_id=spec.extractor_id if spec else "none",
                    extractor_version=spec.version if spec else "0",
                    path=path,
                )
            )
        return targets

    def _group_key(self, target: EvidenceTarget) -> tuple[str, str, str, str]:
        if target.hash_status != "ok" or not target.digest:
            return ("hash-unavailable", str(target.entry_id), "", "")
        if target.size_bytes > self.settings.limits.max_input_bytes:
            return ("too-large", target.digest, target.suffix, "none")
        if target.extractor_id == "none":
            return ("unsupported", target.digest, target.suffix, "none")
        return (
            "extract",
            target.digest,
            target.suffix,
            f"{target.extractor_id}:{target.extractor_version}",
        )

    def _cache_key_for(self, target: EvidenceTarget) -> str:
        return _cache_key(
            content_sha256=target.digest or f"entry:{target.entry_id}",
            source_format=target.suffix,
            extractor_id=target.extractor_id,
            extractor_version=target.extractor_version,
            config_fingerprint=self.settings.config_fingerprint(),
            evidence_schema_version=self.settings.evidence_schema_version,
        )

    # -- persistent cache --------------------------------------------------

    def _cached(self, cache_key: str) -> EvidenceRecord | None:
        row = self.store.conn.execute(
            "SELECT payload_json, status FROM evidence_records WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        try:
            record = EvidenceRecord.from_dict(json.loads(row["payload_json"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return record

    def _persist(
        self,
        target: EvidenceTarget,
        record: EvidenceRecord,
        *,
        cache_key: str,
        error_code: str = "",
        error_message: str = "",
        duration_ms: int = 0,
    ) -> None:
        now = utc_now_iso()
        evidence_id = _evidence_id(cache_key)
        payload = json.dumps(record.as_dict(), sort_keys=True, ensure_ascii=False)
        existing = self.store.conn.execute(
            "SELECT evidence_id, status FROM evidence_records WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if existing is not None:
            evidence_id = existing["evidence_id"]
            self.store.conn.execute(
                """
                UPDATE evidence_records
                SET status=?, payload_json=?, error_code=?, error_message=?,
                    text_chars=?, duration_ms=?, updated_at=?
                WHERE cache_key=?
                """,
                (
                    record.status,
                    payload,
                    error_code or None,
                    error_message or None,
                    record.text_chars,
                    duration_ms,
                    now,
                    cache_key,
                ),
            )
        else:
            self.store.conn.execute(
                """
                INSERT INTO evidence_records(
                    evidence_id, cache_key, content_sha256,
                    evidence_schema_version, extractor_id, extractor_version,
                    config_fingerprint, source_format, status, payload_json,
                    error_code, error_message, text_chars, duration_ms,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    cache_key,
                    record.content_sha256,
                    record.evidence_schema_version,
                    record.extractor_id,
                    record.extractor_version,
                    record.config_fingerprint,
                    record.source_format,
                    record.status,
                    payload,
                    error_code or None,
                    error_message or None,
                    record.text_chars,
                    duration_ms,
                    now,
                    now,
                ),
            )

    def _link(
        self,
        target: EvidenceTarget,
        cache_key: str,
        run_id: str,
        evidence_run_id: str,
    ) -> None:
        row = self.store.conn.execute(
            "SELECT evidence_id, content_sha256 FROM evidence_records WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return
        self.store.conn.execute(
            """
            INSERT OR IGNORE INTO evidence_occurrences(
                evidence_id, evidence_run_id, run_id, entry_id, source_id,
                source_rel_path, content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["evidence_id"],
                evidence_run_id,
                run_id,
                target.entry_id,
                target.source_id,
                target.rel_path,
                row["content_sha256"],
            ),
        )

    # -- production -------------------------------------------------------

    def _failure_record(
        self,
        target: EvidenceTarget,
        status: str,
        code: str,
        message: str,
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            content_sha256=target.digest,
            extractor_id=target.extractor_id,
            extractor_version=target.extractor_version,
            config_fingerprint=self.settings.config_fingerprint(),
            status=status,
            source_format=target.suffix,
        )
        record.type_hints.append(
            observation("declared_format", target.suffix, "extension", detail="filename suffix")
        )
        record.warnings.append(message)
        return record

    def _produce(
        self,
        targets: list[EvidenceTarget],
        *,
        retry_failures: bool,
    ) -> tuple[EvidenceRecord, str, str, int, bool]:
        target = targets[0]
        if target.hash_status != "ok" or not target.digest:
            return (
                self._failure_record(
                    target,
                    STATUS_HASH_UNAVAILABLE,
                    "HASH_UNAVAILABLE",
                    f"Content SHA-256 is not available (hash status {target.hash_status!r})",
                ),
                "HASH_UNAVAILABLE",
                f"Hash status {target.hash_status!r}",
                0,
                False,
            )
        if target.size_bytes > self.settings.limits.max_input_bytes:
            return (
                self._failure_record(
                    target,
                    STATUS_INPUT_TOO_LARGE,
                    "INPUT_TOO_LARGE",
                    f"Input size {target.size_bytes} exceeds {self.settings.limits.max_input_bytes}",
                ),
                "INPUT_TOO_LARGE",
                "Input exceeds configured limit",
                0,
                False,
            )
        if target.extractor_id == "none":
            return (
                self._failure_record(
                    target,
                    STATUS_UNSUPPORTED,
                    "UNSUPPORTED_FORMAT",
                    f"No M3 extractor for {target.suffix or '(no suffix)'}",
                ),
                "UNSUPPORTED_FORMAT",
                "Unsupported format",
                0,
                False,
            )
        if not target.path.is_file():
            return (
                self._failure_record(
                    target,
                    STATUS_SOURCE_UNAVAILABLE,
                    "SOURCE_UNAVAILABLE",
                    "Source file is missing",
                ),
                "SOURCE_UNAVAILABLE",
                "Source missing",
                0,
                False,
            )
        try:
            actual = _sha256_file(target.path)
        except OSError as exc:
            return (
                self._failure_record(
                    target,
                    STATUS_SOURCE_UNAVAILABLE,
                    "SOURCE_UNREADABLE",
                    f"Source cannot be read: {exc}",
                ),
                "SOURCE_UNREADABLE",
                str(exc),
                0,
                False,
            )
        if actual != target.digest:
            return (
                self._failure_record(
                    target,
                    STATUS_SOURCE_CHANGED,
                    "SOURCE_CHANGED",
                    "Source content no longer matches the inventory hash",
                ),
                "SOURCE_CHANGED",
                "Source content changed",
                0,
                False,
            )
        spec = extractor_for_suffix(target.suffix)
        if spec is None:
            return (
                self._failure_record(
                    target,
                    STATUS_UNSUPPORTED,
                    "UNSUPPORTED_FORMAT",
                    "Extractor no longer supports this suffix",
                ),
                "UNSUPPORTED_FORMAT",
                "Unsupported format",
                0,
                False,
            )
        started = time.perf_counter()
        response = self._run_worker(target, spec)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.get("ok") and isinstance(response.get("evidence"), dict):
            try:
                record = EvidenceRecord.from_dict(response["evidence"])
            except (TypeError, ValueError, KeyError) as exc:
                return (
                    self._failure_record(
                        target, STATUS_MALFORMED, "INVALID_WORKER_EVIDENCE", str(exc)
                    ),
                    "INVALID_WORKER_EVIDENCE",
                    str(exc),
                    duration_ms,
                    True,
                )
            if record.content_sha256 != target.digest:
                return (
                    self._failure_record(
                        target,
                        STATUS_SOURCE_CHANGED,
                        "CONTENT_HASH_MISMATCH",
                        "Extractor content hash does not match inventory hash",
                    ),
                    "CONTENT_HASH_MISMATCH",
                    "Content hash mismatch",
                    duration_ms,
                    True,
                )
            try:
                after = _sha256_file(target.path)
            except OSError as exc:
                return (
                    self._failure_record(
                        target,
                        STATUS_SOURCE_UNAVAILABLE,
                        "SOURCE_UNREADABLE",
                        f"Source cannot be re-read after extraction: {exc}",
                    ),
                    "SOURCE_UNREADABLE",
                    str(exc),
                    duration_ms,
                    True,
                )
            if after != target.digest:
                return (
                    self._failure_record(
                        target,
                        STATUS_SOURCE_CHANGED,
                        "SOURCE_CHANGED",
                        "Source content changed during extraction",
                    ),
                    "SOURCE_CHANGED",
                    "Source changed during extraction",
                    duration_ms,
                    True,
                )
            record.config_fingerprint = self.settings.config_fingerprint()
            return record, "", "", duration_ms, True
        status = str(response.get("status", STATUS_MALFORMED))
        code = str(response.get("error_code", "EXTRACTION_FAILED"))
        message = str(response.get("error_message", "Extraction failed"))
        return (
            self._failure_record(target, status, code, message),
            code,
            message,
            duration_ms,
            True,
        )

    def _run_worker(self, target: EvidenceTarget, spec: ExtractorSpec) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="reclaim-evidence-") as tmp:
            tmp_path = Path(tmp)
            request_path = tmp_path / "request.json"
            response_path = tmp_path / "response.json"
            request = {
                "path": str(target.path),
                "content_sha256": target.digest,
                "extractor_id": spec.extractor_id,
                "limits": self.settings.limits.as_dict(),
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            env = os.environ.copy()
            package_root = _package_root()
            env["PYTHONPATH"] = str(package_root) + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            command = [
                sys.executable,
                "-m",
                "reclaim.evidence.worker",
                str(request_path),
                str(response_path),
            ]
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            preexec_fn = None
            if os.name != "nt":
                try:
                    import resource

                    def _limits() -> None:  # pragma: no cover - POSIX worker
                        resource.setrlimit(
                            resource.RLIMIT_AS,
                            (
                                self.settings.limits.max_memory_bytes,
                                self.settings.limits.max_memory_bytes,
                            ),
                        )
                        resource.setrlimit(
                            resource.RLIMIT_CPU,
                            (int(self.settings.limits.timeout_seconds) + 1,) * 2,
                        )

                    preexec_fn = _limits
                except (ImportError, OSError):
                    preexec_fn = None
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=self.settings.limits.timeout_seconds,
                    env=env,
                    creationflags=creationflags,
                    preexec_fn=preexec_fn,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "status": STATUS_TIMEOUT,
                    "error_code": "TIMEOUT",
                    "error_message": f"Extraction exceeded {self.settings.limits.timeout_seconds} seconds",
                }
            if completed.returncode != 0:
                stderr = (completed.stderr or b"").decode("utf-8", errors="replace")[-2000:]
                return {
                    "ok": False,
                    "status": "crashed",
                    "error_code": "WORKER_CRASHED",
                    "error_message": f"Worker exited {completed.returncode}: {stderr}",
                }
            try:
                return json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {
                    "ok": False,
                    "status": "crashed",
                    "error_code": "NO_WORKER_RESPONSE",
                    "error_message": f"Worker response missing or invalid: {exc}",
                }

    # -- public run -------------------------------------------------------

    def run(
        self,
        run_id: str,
        *,
        progress: Progress | None = None,
        stop: Event | None = None,
        retry_failures: bool = False,
    ) -> EvidenceRunResult:
        self.store.get_run(run_id)
        targets = self._targets(run_id)
        groups: dict[tuple[str, str, str, str], list[EvidenceTarget]] = defaultdict(list)
        for target in targets:
            groups[self._group_key(target)].append(target)
        evidence_run_id = f"evrun-{uuid.uuid4().hex[:16]}"
        started_at = utc_now_iso()
        self.store.conn.execute(
            """
            INSERT INTO evidence_runs(
                evidence_run_id, source_run_id, config_fingerprint, status, started_at
            ) VALUES (?, ?, ?, 'running', ?)
            """,
            (evidence_run_id, run_id, self.settings.config_fingerprint(), started_at),
        )
        self.store.commit()
        counts: Counter[str] = Counter()
        cache_hits = 0
        worker_calls = 0
        processed_entries = 0
        total_entries = len(targets)
        cancelled = False
        try:
            for index, key in enumerate(groups, start=1):
                if stop is not None and stop.is_set():
                    cancelled = True
                    break
                group = groups[key]
                first = group[0]
                cache_key = self._cache_key_for(first)
                cached = self._cached(cache_key)
                if cached is not None and not (
                    retry_failures and cached.status in FAILURE_STATUSES
                ):
                    for target in group:
                        self._link(target, cache_key, run_id, evidence_run_id)
                        counts[cached.status] += 1
                        processed_entries += 1
                    cache_hits += len(group)
                else:
                    record, error_code, error_message, duration_ms, used_worker = self._produce(
                        group, retry_failures=retry_failures
                    )
                    self._persist(
                        first,
                        record,
                        cache_key=cache_key,
                        error_code=error_code,
                        error_message=error_message,
                        duration_ms=duration_ms,
                    )
                    for target in group:
                        self._link(target, cache_key, run_id, evidence_run_id)
                        counts[record.status] += 1
                        processed_entries += 1
                    if used_worker:
                        worker_calls += 1
                if progress is not None:
                    progress(
                        {
                            "phase": "evidence",
                            "processed": len(group) if index == 1 else min(total_entries, processed_entries),
                            "total": total_entries,
                            "entries": processed_entries,
                            "total_entries": total_entries,
                            "cache_hits": cache_hits,
                            "worker_calls": worker_calls,
                            "status": first.source_id,
                            "message": f"Evidence {processed_entries:,}/{total_entries:,}",
                        }
                    )
                if index % 25 == 0:
                    self.store.commit()
            self.store.commit()
        except Exception:
            self.store.conn.execute(
                "UPDATE evidence_runs SET status='failed', finished_at=?, counts_json=? WHERE evidence_run_id=?",
                (
                    utc_now_iso(),
                    json.dumps(dict(counts), sort_keys=True),
                    evidence_run_id,
                ),
            )
            self.store.commit()
            raise
        failures = sum(counts.get(status, 0) for status in FAILURE_STATUSES)
        status = "cancelled" if cancelled else (
            "completed_with_failures" if failures else "completed"
        )
        finished_at = utc_now_iso()
        counts_payload = {
            "total_entries": total_entries,
            "counts": dict(counts),
            "cache_hits": cache_hits,
            "worker_calls": worker_calls,
            "processed_entries": processed_entries,
        }
        self.store.conn.execute(
            """
            UPDATE evidence_runs
            SET status=?, finished_at=?, counts_json=?
            WHERE evidence_run_id=?
            """,
            (status, finished_at, json.dumps(counts_payload, sort_keys=True), evidence_run_id),
        )
        self.store.commit()
        if progress is not None:
            progress(
                {
                    "phase": "evidence",
                    "processed": processed_entries,
                    "total": total_entries,
                    "message": f"Evidence {status}",
                    "counts": dict(counts),
                }
            )
        return EvidenceRunResult(
            evidence_run_id=evidence_run_id,
            source_run_id=run_id,
            status=status,
            counts=dict(counts),
            total_entries=total_entries,
            cache_hits=cache_hits,
            worker_calls=worker_calls,
            started_at=started_at,
            finished_at=finished_at,
        )

    # -- reporting/export -------------------------------------------------

    def latest_run(self) -> dict[str, Any] | None:
        row = self.store.conn.execute(
            "SELECT * FROM evidence_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def stats(self, evidence_run_id: str | None = None) -> EvidenceStats:
        if evidence_run_id:
            run = self.store.conn.execute(
                "SELECT * FROM evidence_runs WHERE evidence_run_id=?",
                (evidence_run_id,),
            ).fetchone()
        else:
            run = self.store.conn.execute(
                "SELECT * FROM evidence_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if run is None:
            raise EvidenceError("No evidence run exists")
        counts_payload = json.loads(run["counts_json"] or "{}")
        outcomes = {
            str(row["status"]): int(row["n"])
            for row in self.store.conn.execute(
                """
                SELECT r.status, COUNT(*) AS n
                FROM evidence_records r
                JOIN evidence_occurrences o ON o.evidence_id=r.evidence_id
                WHERE o.evidence_run_id=?
                GROUP BY r.status
                ORDER BY r.status
                """,
                (run["evidence_run_id"],),
            )
        }
        formats = {
            str(row["source_format"]): int(row["n"])
            for row in self.store.conn.execute(
                """
                SELECT r.source_format, COUNT(*) AS n
                FROM evidence_records r
                JOIN evidence_occurrences o ON o.evidence_id=r.evidence_id
                WHERE o.evidence_run_id=?
                GROUP BY r.source_format
                ORDER BY r.source_format
                """,
                (run["evidence_run_id"],),
            )
        }
        total = int(counts_payload.get("total_entries", 0))
        covered = sum(outcomes.values())
        failures = sum(outcomes.get(status, 0) for status in FAILURE_STATUSES)
        return EvidenceStats(
            evidence_run_id=str(run["evidence_run_id"]),
            source_run_id=str(run["source_run_id"]),
            status=str(run["status"]),
            total_entries=total,
            covered_entries=covered,
            outcomes=outcomes,
            formats=formats,
            cache_hits=int(counts_payload.get("cache_hits", 0)),
            worker_calls=int(counts_payload.get("worker_calls", 0)),
            failures=failures,
            unsupported=outcomes.get(STATUS_UNSUPPORTED, 0),
            cacheable_success=sum(
                outcomes.get(status, 0)
                for status in ("success", "metadata_only", "empty")
            ),
        )

    def rows(self, evidence_run_id: str | None = None) -> list[dict[str, Any]]:
        if evidence_run_id:
            run = self.store.conn.execute(
                "SELECT * FROM evidence_runs WHERE evidence_run_id=?",
                (evidence_run_id,),
            ).fetchone()
        else:
            run = self.store.conn.execute(
                "SELECT * FROM evidence_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if run is None:
            return []
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in self.store.conn.execute(
                """
                SELECT o.entry_id, o.source_id, o.source_rel_path,
                       o.content_sha256, r.evidence_id, r.status, r.source_format,
                       r.extractor_id, r.extractor_version, r.error_code,
                       r.error_message, r.text_chars, r.duration_ms,
                       r.payload_json
                FROM evidence_occurrences o
                JOIN evidence_records r ON r.evidence_id=o.evidence_id
                WHERE o.evidence_run_id=?
                ORDER BY o.source_id, o.source_rel_path
                """,
                (run["evidence_run_id"],),
            )
        ]

    def export(self, out_dir: str | Path, evidence_run_id: str | None = None) -> tuple[Path, Path]:
        rows = self.rows(evidence_run_id)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jsonl_path = out / "evidence.jsonl"
        csv_path = out / "evidence.csv"
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        fields = [
            "entry_id",
            "source_id",
            "source_rel_path",
            "content_sha256",
            "evidence_id",
            "status",
            "source_format",
            "extractor_id",
            "extractor_version",
            "error_code",
            "error_message",
            "text_chars",
            "duration_ms",
            "payload_json",
        ]
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        **{field: row.get(field, "") for field in fields},
                        "payload_json": json.dumps(row.get("payload", {}), sort_keys=True),
                    }
                )
        return jsonl_path, csv_path
