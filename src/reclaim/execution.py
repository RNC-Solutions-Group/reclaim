"""Journaled migration execution, verification, and reconciliation for M1."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .config import ProjectConfig
from .destination import (
    DestinationError,
    DestinationOccupied,
    DestinationPolicy,
    DestinationUnsupported,
    publish_no_overwrite,
    safe_makedirs,
    validate_destination_root,
    validate_relative,
)
from .errors import ReclaimError
from .paths import canonical_path
from .planning import (
    PlanItem,
    get_authorization,
    get_plan_items,
    get_plan_row,
    recompute_plan_digest,
    revalidate_sources,
)
from .store import Store, utc_now_iso

COPY_CHUNK = 4 * 1024 * 1024
TERMINAL_STATES = ("verified", "failed", "conflict")
RUNNABLE_STATES = ("pending", "failed")


class ExecutionError(ReclaimError):
    """Execution cannot start or complete safely."""


@dataclass
class ExecutionResult:
    execution_id: str
    plan_id: str
    status: str
    summary: dict[str, Any]
    dry_run: bool = False


@dataclass(frozen=True)
class Preflight:
    issues: list[str]
    destination_root: Path
    policy: DestinationPolicy
    copy_items: list[PlanItem]
    remaining_bytes: int


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(COPY_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _event(
    store: Store,
    execution_id: str,
    event_type: str,
    *,
    item_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    row = store.conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM execution_events WHERE execution_id=?",
        (execution_id,),
    ).fetchone()
    sequence = int(row[0])
    store.conn.execute(
        """
        INSERT INTO execution_events(
            execution_id, item_id, sequence, event_type, occurred_at, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            item_id,
            sequence,
            event_type,
            utc_now_iso(),
            json.dumps(detail or {}, sort_keys=True),
        ),
    )


def _set_execution_status(store: Store, execution_id: str, status: str) -> None:
    store.conn.execute(
        "UPDATE executions SET status=?, finished_at=? WHERE execution_id=?",
        (status, utc_now_iso() if status not in ("running", "paused") else None, execution_id),
    )


def get_execution_row(store: Store, execution_id: str):
    row = store.conn.execute(
        "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
    ).fetchone()
    if row is None:
        raise ExecutionError(f"Unknown execution: {execution_id}")
    return row


def _execution_items(store: Store, execution_id: str) -> dict[int, Any]:
    return {
        row["item_id"]: row
        for row in store.conn.execute(
            "SELECT * FROM execution_items WHERE execution_id=? ORDER BY item_id",
            (execution_id,),
        )
    }


def _upsert_item(
    store: Store,
    execution_id: str,
    item: PlanItem,
    *,
    state: str | None = None,
    actual_sha256: str | None = None,
    bytes_copied: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
) -> None:
    existing = store.conn.execute(
        "SELECT * FROM execution_items WHERE execution_id=? AND item_id=?",
        (execution_id, item.item_id),
    ).fetchone()
    if existing is None:
        store.conn.execute(
            """
            INSERT INTO execution_items(
                execution_id, item_id, state, destination_rel, expected_sha256,
                actual_sha256, bytes_copied, attempts, error_code, error_message,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                execution_id,
                item.item_id,
                state or "pending",
                item.destination_rel,
                item.expected_sha256,
                actual_sha256,
                bytes_copied,
                error_code,
                error_message,
                utc_now_iso(),
                utc_now_iso() if state in TERMINAL_STATES else None,
            ),
        )
        return
    attempts = existing["attempts"] + (1 if increment_attempt else 0)
    store.conn.execute(
        """
        UPDATE execution_items
        SET state=?, destination_rel=?, expected_sha256=?, actual_sha256=?,
            bytes_copied=?, attempts=?, error_code=?, error_message=?,
            finished_at=?
        WHERE execution_id=? AND item_id=?
        """,
        (
            state if state is not None else existing["state"],
            item.destination_rel,
            item.expected_sha256,
            actual_sha256 if actual_sha256 is not None else existing["actual_sha256"],
            bytes_copied if bytes_copied is not None else existing["bytes_copied"],
            attempts,
            error_code if error_code is not None else existing["error_code"],
            error_message if error_message is not None else existing["error_message"],
            utc_now_iso() if state in TERMINAL_STATES else existing["finished_at"],
            execution_id,
            item.item_id,
        ),
    )


def _source_path(config: ProjectConfig, item: PlanItem) -> Path:
    source = config.source_by_id(item.source_id)
    return source.path.joinpath(*item.source_rel_path.split("/"))


def _destination_path(root: Path, rel_path: str) -> Path:
    return root.joinpath(*rel_path.split("/"))


def _preflight(
    store: Store,
    config: ProjectConfig,
    plan_row: Any,
    items: list[PlanItem],
) -> Preflight:
    policy = DestinationPolicy(
        target_platform=plan_row["target_platform"],
        max_path_length=plan_row["max_path_length"],
    )
    selected = plan_row["destination_selected"] or plan_row["destination_root"]
    root = canonical_path(plan_row["destination_root"])
    root_issues = validate_destination_root(
        selected,
        policy,
        source_roots=[source.path for source in config.sources],
        workspace=config.workspace,
    )
    if root_issues:
        raise ExecutionError(
            "Destination preflight failed: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in root_issues)
        )
    if os.path.normcase(str(canonical_path(selected))) != os.path.normcase(str(root)):
        raise ExecutionError(
            "Destination selected path no longer resolves to the planned root"
        )
    source_issues = revalidate_sources(
        config, json.loads(plan_row["source_snapshot_json"])
    )
    if source_issues:
        raise ExecutionError("Source preflight failed: " + "; ".join(source_issues))

    copy_items = [
        item for item in items if item.disposition == "copy" and item.destination_rel
    ]
    remaining = sum(item.size_bytes for item in copy_items)
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise ExecutionError(f"Cannot inspect destination capacity: {exc}") from exc
    if remaining > free:
        raise ExecutionError(
            f"Destination has {free} bytes free; remaining plan requires {remaining}"
        )
    return Preflight(
        issues=[],
        destination_root=root,
        policy=policy,
        copy_items=copy_items,
        remaining_bytes=remaining,
    )


def dry_run(
    store: Store,
    config: ProjectConfig,
    plan_id: str,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    plan = get_plan_row(store, plan_id)
    items = get_plan_items(store, plan_id)
    policy = DestinationPolicy(
        target_platform=plan["target_platform"],
        max_path_length=plan["max_path_length"],
    )
    selected = plan["destination_selected"] or plan["destination_root"]
    root = canonical_path(plan["destination_root"])
    root_issues = validate_destination_root(
        selected,
        policy,
        source_roots=[source.path for source in config.sources],
        workspace=config.workspace,
    )
    if root_issues:
        raise ExecutionError(
            "Destination dry-run failed: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in root_issues)
        )
    if os.path.normcase(str(canonical_path(selected))) != os.path.normcase(str(root)):
        raise ExecutionError(
            "Destination selected path no longer resolves to the planned root"
        )
    copy_items = [
        item for item in items if item.disposition == "copy" and item.destination_rel
    ]
    remaining = 0
    conflicts = 0
    missing_sources = 0
    previews: list[dict[str, Any]] = []
    for item in copy_items:
        path = _destination_path(root, item.destination_rel or "")
        source = _source_path(config, item)
        if not source.exists():
            missing_sources += 1
        exists = path.exists() or path.is_symlink()
        if exists:
            conflicts += 1
        else:
            remaining += item.size_bytes
        previews.append(
            {
                "item_id": item.item_id,
                "source": f"{item.source_id}:{item.source_rel_path}",
                "destination": item.destination_rel,
                "bytes": item.size_bytes,
                "would_copy": not exists,
                "conflict": exists,
            }
        )
    free = shutil.disk_usage(root).free
    plan_summary = json.loads(plan["summary_json"])
    plan_conflicts = int(plan_summary.get("conflicts", 0))
    global_issues = plan_summary.get("global_issues", [])
    summary = {
        "dry_run": True,
        "plan_id": plan_id,
        "plan_digest": plan["plan_digest"],
        "copy_items": len(copy_items),
        "would_copy": len(copy_items) - conflicts,
        "destination_conflicts": conflicts,
        "missing_sources": missing_sources,
        "plan_conflicts": plan_conflicts,
        "global_issues": global_issues,
        "required_bytes": remaining,
        "free_bytes": free,
        "capacity_ok": remaining <= free,
        "previews": previews[:limit] if limit else previews,
        "preview_truncated": bool(limit and len(previews) > limit),
    }
    if (
        missing_sources
        or conflicts
        or plan_conflicts
        or global_issues
        or remaining > free
    ):
        summary["would_execute"] = False
    else:
        summary["would_execute"] = True
    return summary


def _new_execution(
    store: Store, plan_id: str, digest: str, root: Path
) -> str:
    execution_id = "exec-" + uuid.uuid4().hex[:16]
    store.conn.execute(
        """
        INSERT INTO executions(
            execution_id, plan_id, plan_digest, destination_root, status,
            started_at, finished_at, summary_json
        ) VALUES (?, ?, ?, ?, 'running', ?, NULL, '{}')
        """,
        (execution_id, plan_id, digest, str(root), utc_now_iso()),
    )
    _event(store, execution_id, "EXECUTION_STARTED")
    store.commit()
    return execution_id


def _execution_for_plan(store: Store, plan_id: str):
    return store.conn.execute(
        """
        SELECT * FROM executions
        WHERE plan_id=? AND status IN ('running','paused')
        ORDER BY started_at DESC LIMIT 1
        """,
        (plan_id,),
    ).fetchone()


def execute_plan(
    store: Store,
    config: ProjectConfig,
    plan_id: str,
    digest: str,
    *,
    dry_run_mode: bool = False,
    max_items: int | None = None,
    retry_failed: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
    stop_event: Event | None = None,
) -> ExecutionResult:
    if dry_run_mode:
        summary = dry_run(store, config, plan_id)
        return ExecutionResult(
            execution_id="",
            plan_id=plan_id,
            status="dry_run",
            summary=summary,
            dry_run=True,
        )

    plan = get_plan_row(store, plan_id)
    if plan["plan_digest"] != digest:
        raise ExecutionError("Plan digest does not match the requested digest")
    if recompute_plan_digest(store, plan_id) != digest:
        raise ExecutionError("Plan content no longer matches its digest")
    authorization = get_authorization(store, plan_id, digest)
    if authorization is None:
        raise ExecutionError(
            "Plan is not authorized for this exact digest; run 'reclaim authorize'"
        )
    if plan["status"] not in (
        "authorized",
        "paused",
        "running",
        "complete",
        "complete_with_failures",
        "verification_failed",
        "verification_incomplete",
    ):
        raise ExecutionError(f"Plan status {plan['status']!r} is not executable")
    items = get_plan_items(store, plan_id)
    preflight = _preflight(store, config, plan, items)

    existing = _execution_for_plan(store, plan_id)
    if existing is not None:
        execution_id = existing["execution_id"]
        if existing["plan_digest"] != digest:
            raise ExecutionError("Existing execution belongs to a different plan digest")
        _set_execution_status(store, execution_id, "running")
        _event(store, execution_id, "EXECUTION_RESUMED")
        store.commit()
    else:
        execution_id = _new_execution(
            store, plan_id, digest, preflight.destination_root
        )

    for item in preflight.copy_items:
        current = store.conn.execute(
            "SELECT * FROM execution_items WHERE execution_id=? AND item_id=?",
            (execution_id, item.item_id),
        ).fetchone()
        if current is None:
            _upsert_item(store, execution_id, item, state="pending")
            current = store.conn.execute(
                "SELECT * FROM execution_items WHERE execution_id=? AND item_id=?",
                (execution_id, item.item_id),
            ).fetchone()

    processed = 0
    total_copy_items = len(preflight.copy_items)
    if progress is not None:
        progress(
            {
                "execution_id": execution_id,
                "processed": 0,
                "total": total_copy_items,
                "message": "Execution started",
            }
        )
    for item in preflight.copy_items:
        if stop_event is not None and stop_event.is_set():
            break
        if max_items is not None and processed >= max_items:
            break
        current = store.conn.execute(
            "SELECT * FROM execution_items WHERE execution_id=? AND item_id=?",
            (execution_id, item.item_id),
        ).fetchone()
        if current is None:
            continue
        state = current["state"]
        if state == "verified":
            destination = _destination_path(
                preflight.destination_root, item.destination_rel or ""
            )
            if not destination.exists() or destination.is_symlink():
                _fail_item(
                    store,
                    execution_id,
                    item,
                    "DESTINATION_MISSING",
                    "Verified destination is missing on resume",
                )
                continue
            try:
                actual = _sha256_file(destination)
            except OSError as exc:
                _fail_item(
                    store,
                    execution_id,
                    item,
                    "DESTINATION_UNREADABLE",
                    f"Verified destination cannot be read: {exc}",
                )
                continue
            if actual != item.expected_sha256:
                _fail_item(
                    store,
                    execution_id,
                    item,
                    "DESTINATION_CHANGED",
                    "Verified destination no longer matches the planned digest",
                )
            continue
        if state == "failed" and not retry_failed:
            continue
        processed += 1
        _execute_item(
            store,
            config,
            execution_id,
            item,
            preflight,
            prior=current,
        )
        state_row = store.conn.execute(
            "SELECT state, error_code FROM execution_items WHERE execution_id=? AND item_id=?",
            (execution_id, item.item_id),
        ).fetchone()
        if progress is not None:
            progress(
                {
                    "execution_id": execution_id,
                    "processed": processed,
                    "total": total_copy_items,
                    "source_id": item.source_id,
                    "rel_path": item.source_rel_path,
                    "destination_rel": item.destination_rel,
                    "state": state_row["state"] if state_row else "",
                    "error_code": state_row["error_code"] if state_row else "",
                    "message": "Item processed",
                }
            )

    counts = _execution_counts(store, execution_id)
    failed = counts.get("failed", 0) + counts.get("conflict", 0)
    pending = store.conn.execute(
        """
        SELECT COUNT(*) FROM execution_items
        WHERE execution_id=? AND state NOT IN ('verified','failed','conflict')
        """,
        (execution_id,),
    ).fetchone()[0]
    if pending:
        status = "paused"
        _event(store, execution_id, "EXECUTION_PAUSED")
    elif failed:
        status = "complete_with_failures"
        _event(store, execution_id, "EXECUTION_COMPLETE_WITH_FAILURES")
    else:
        status = "complete"
        _event(store, execution_id, "EXECUTION_COMPLETE")
    summary = {
        "plan_id": plan_id,
        "plan_digest": digest,
        "destination_root": str(preflight.destination_root),
        "copy_items": len(preflight.copy_items),
        "counts": counts,
        "failed_or_conflict": failed,
    }
    _set_execution_status(store, execution_id, status)
    store.conn.execute(
        "UPDATE executions SET summary_json=? WHERE execution_id=?",
        (json.dumps(summary, sort_keys=True), execution_id),
    )
    if status in ("complete", "complete_with_failures"):
        store.conn.execute(
            "UPDATE plans SET status=? WHERE plan_id=?", (status, plan_id)
        )
    store.commit()
    return ExecutionResult(
        execution_id=execution_id,
        plan_id=plan_id,
        status=status,
        summary=summary,
    )


def _execute_item(
    store: Store,
    config: ProjectConfig,
    execution_id: str,
    item: PlanItem,
    preflight: Preflight,
    *,
    prior: Any,
) -> None:
    assert item.destination_rel is not None
    _event(store, execution_id, "ITEM_STARTED", item_id=item.item_id)
    _upsert_item(store, execution_id, item, state="pending", increment_attempt=True)
    root = preflight.destination_root
    rel_issues = validate_relative(item.destination_rel, preflight.policy)
    if rel_issues:
        _fail_item(
            store,
            execution_id,
            item,
            "DESTINATION_INVALID",
            "; ".join(f"{issue.code}: {issue.message}" for issue in rel_issues),
        )
        return
    destination = _destination_path(root, item.destination_rel)
    source = _source_path(config, item)

    if destination.exists() or destination.is_symlink():
        published = store.conn.execute(
            """
            SELECT 1 FROM execution_events
            WHERE execution_id=? AND item_id=? AND event_type='DEST_PUBLISHED'
            LIMIT 1
            """,
            (execution_id, item.item_id),
        ).fetchone()
        provenance = published is not None or prior["state"] == "verified"
        if (
            provenance
            and item.expected_sha256
            and destination.is_file()
            and not destination.is_symlink()
        ):
            try:
                actual = _sha256_file(destination)
            except OSError as exc:
                _fail_item(store, execution_id, item, "DESTINATION_UNREADABLE", str(exc))
                return
            if actual == item.expected_sha256:
                _verify_item(store, execution_id, item, actual)
                return
            if published is not None:
                _fail_item(
                    store,
                    execution_id,
                    item,
                    "DESTINATION_CHANGED",
                    "Previously published destination no longer matches the planned digest",
                )
                return
        _conflict_item(
            store,
            execution_id,
            item,
            "DESTINATION_PREEXISTING" if published is None else "DESTINATION_EXISTS",
            "Destination was present without verified provenance for this execution; "
            "refusing to adopt or overwrite it",
        )
        return

    try:
        source_stat = source.stat()
    except OSError as exc:
        _fail_item(store, execution_id, item, "SOURCE_UNREADABLE", str(exc))
        return
    if item.size_bytes != source_stat.st_size:
        _fail_item(
            store,
            execution_id,
            item,
            "SOURCE_CHANGED",
            f"Source size changed: expected {item.size_bytes}, found {source_stat.st_size}",
        )
        return
    if item.source_mtime_ns is not None and source_stat.st_mtime_ns != item.source_mtime_ns:
        _fail_item(
            store,
            execution_id,
            item,
            "SOURCE_CHANGED",
            "Source modification time changed since planning",
        )
        return

    try:
        source_digest = _sha256_file(source)
    except OSError as exc:
        _fail_item(store, execution_id, item, "SOURCE_UNREADABLE", str(exc))
        return
    if source_digest != item.expected_sha256:
        _fail_item(
            store,
            execution_id,
            item,
            "SOURCE_CONTENT_CHANGED",
            "Source content no longer matches the planned digest",
        )
        return
    _event(store, execution_id, "SOURCE_VERIFIED", item_id=item.item_id)

    try:
        safe_makedirs(root, "/".join(item.destination_rel.split("/")[:-1]))
    except (OSError, DestinationError) as exc:
        _fail_item(store, execution_id, item, "DESTINATION_PARENT_FAILED", str(exc))
        return

    if destination.exists() or destination.is_symlink():
        _conflict_item(
            store,
            execution_id,
            item,
            "DESTINATION_EXISTS",
            "Destination appeared before copy; refusing to overwrite",
        )
        return

    temp = destination.parent / f".reclaim-tmp-{execution_id}-{item.item_id}"
    try:
        if temp.exists() or temp.is_symlink():
            temp.unlink()
        with open(source, "rb") as src, open(temp, "xb") as dst:
            while True:
                block = src.read(COPY_CHUNK)
                if not block:
                    break
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        temp_digest = _sha256_file(temp)
        if temp_digest != item.expected_sha256:
            _fail_item(
                store,
                execution_id,
                item,
                "COPY_VERIFY_FAILED",
                "Temporary copy does not match the planned digest",
            )
            return
        try:
            publish_no_overwrite(temp, destination)
        except DestinationOccupied as exc:
            _conflict_item(
                store, execution_id, item, "DESTINATION_RACE", str(exc)
            )
            return
        except DestinationUnsupported as exc:
            _fail_item(
                store,
                execution_id,
                item,
                "PUBLICATION_UNSUPPORTED",
                str(exc),
            )
            return
        except OSError as exc:
            _fail_item(store, execution_id, item, "PUBLISH_FAILED", str(exc))
            return
        _event(
            store,
            execution_id,
            "DEST_PUBLISHED",
            item_id=item.item_id,
            detail={"sha256": item.expected_sha256},
        )
        store.commit()
        actual = _sha256_file(destination)
        if actual != item.expected_sha256:
            _fail_item(
                store,
                execution_id,
                item,
                "DESTINATION_VERIFY_FAILED",
                "Published destination does not match the planned digest",
            )
            return
        if item.source_mtime_ns:
            try:
                os.utime(
                    destination,
                    ns=(item.source_mtime_ns, item.source_mtime_ns),
                )
            except OSError:
                pass
        _verify_item(store, execution_id, item, actual)
    except OSError as exc:
        _fail_item(store, execution_id, item, "COPY_FAILED", str(exc))
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def _verify_item(
    store: Store, execution_id: str, item: PlanItem, actual: str
) -> None:
    _upsert_item(
        store,
        execution_id,
        item,
        state="verified",
        actual_sha256=actual,
        bytes_copied=item.size_bytes,
    )
    store.conn.execute(
        "UPDATE execution_items SET error_code=NULL, error_message=NULL "
        "WHERE execution_id=? AND item_id=?",
        (execution_id, item.item_id),
    )
    _event(
        store,
        execution_id,
        "ITEM_VERIFIED",
        item_id=item.item_id,
        detail={"sha256": actual, "bytes": item.size_bytes},
    )
    store.commit()


def _fail_item(
    store: Store,
    execution_id: str,
    item: PlanItem,
    code: str,
    message: str,
) -> None:
    _upsert_item(
        store,
        execution_id,
        item,
        state="failed",
        error_code=code,
        error_message=message,
    )
    _event(
        store,
        execution_id,
        "ITEM_FAILED",
        item_id=item.item_id,
        detail={"code": code, "message": message},
    )
    store.commit()


def _conflict_item(
    store: Store,
    execution_id: str,
    item: PlanItem,
    code: str,
    message: str,
) -> None:
    _upsert_item(
        store,
        execution_id,
        item,
        state="conflict",
        error_code=code,
        error_message=message,
    )
    _event(
        store,
        execution_id,
        "ITEM_CONFLICT",
        item_id=item.item_id,
        detail={"code": code, "message": message},
    )
    store.commit()


def _execution_counts(store: Store, execution_id: str) -> dict[str, int]:
    counts = Counter()
    for row in store.conn.execute(
        "SELECT state, COUNT(*) AS n FROM execution_items WHERE execution_id=? GROUP BY state",
        (execution_id,),
    ):
        counts[row["state"]] = row["n"]
    return dict(counts)


def verify_execution(
    store: Store, config: ProjectConfig, execution_id: str
) -> dict[str, Any]:
    execution = get_execution_row(store, execution_id)
    plan = get_plan_row(store, execution["plan_id"])
    if recompute_plan_digest(store, plan["plan_id"]) != plan["plan_digest"]:
        raise ExecutionError("Plan content no longer matches its digest")
    if execution["status"] in ("running", "paused"):
        return {
            "execution_id": execution_id,
            "status": "INCOMPLETE",
            "execution_status": execution["status"],
            "verified": 0,
            "failed": 0,
            "missing": 0,
            "mismatches": [],
            "counts": _execution_counts(store, execution_id),
        }

    root = canonical_path(execution["destination_root"])
    verified = 0
    failed = 0
    missing = 0
    mismatches: list[dict[str, Any]] = []
    verification_codes = (
        "DESTINATION_MISSING",
        "DESTINATION_UNREADABLE",
        "VERIFY_MISMATCH",
    )
    placeholders = ",".join("?" for _ in verification_codes)
    rows = store.conn.execute(
        f"""
        SELECT i.*, p.destination_rel, p.expected_sha256 AS plan_digest
        FROM execution_items i
        JOIN plan_items p ON p.plan_id=? AND p.item_id=i.item_id
        WHERE i.execution_id=? AND p.disposition='copy'
          AND (
            i.state='verified'
            OR (i.state='failed' AND i.error_code IN ({placeholders}))
          )
        ORDER BY i.item_id
        """,
        (plan["plan_id"], execution_id, *verification_codes),
    ).fetchall()

    def mark_failure(row: Any, code: str, message: str, actual: str | None) -> None:
        nonlocal failed, missing
        failed += 1
        if code == "DESTINATION_MISSING":
            missing += 1
        mismatches.append(
            {
                "item_id": row["item_id"],
                "code": code,
                "path": row["destination_rel"],
                "message": message,
            }
        )
        store.conn.execute(
            """
            UPDATE execution_items
            SET state='failed', actual_sha256=?, error_code=?, error_message=?,
                finished_at=?
            WHERE execution_id=? AND item_id=?
            """,
            (
                actual,
                code,
                message,
                utc_now_iso(),
                execution_id,
                row["item_id"],
            ),
        )
        _event(
            store,
            execution_id,
            "VERIFY_FAILED",
            item_id=row["item_id"],
            detail={"code": code, "message": message, "actual": actual},
        )

    for row in rows:
        path = root.joinpath(*(row["destination_rel"] or "").split("/"))
        if not path.exists() or path.is_symlink():
            mark_failure(
                row,
                "DESTINATION_MISSING",
                "Destination file is missing or is a link/reparse point",
                None,
            )
            continue
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            mark_failure(
                row,
                "DESTINATION_UNREADABLE",
                f"Destination cannot be read for verification: {exc}",
                None,
            )
            continue
        if actual != row["plan_digest"]:
            mark_failure(
                row,
                "VERIFY_MISMATCH",
                "Destination no longer matches the planned digest",
                actual,
            )
        else:
            verified += 1
            store.conn.execute(
                """
                UPDATE execution_items
                SET state='verified', actual_sha256=?, error_code=NULL,
                    error_message=NULL, finished_at=?
                WHERE execution_id=? AND item_id=?
                """,
                (actual, utc_now_iso(), execution_id, row["item_id"]),
            )
            _event(
                store,
                execution_id,
                "VERIFY_PASSED",
                item_id=row["item_id"],
                detail={"sha256": actual},
            )

    counts = _execution_counts(store, execution_id)
    blocking = counts.get("failed", 0) + counts.get("conflict", 0)
    pending = counts.get("pending", 0)
    if failed:
        execution_status = "verification_failed"
    elif blocking:
        execution_status = "complete_with_failures"
    elif pending:
        execution_status = "verification_incomplete"
    else:
        execution_status = "complete"

    _set_execution_status(store, execution_id, execution_status)
    if execution_status in (
        "complete",
        "complete_with_failures",
        "verification_failed",
    ):
        store.conn.execute(
            "UPDATE plans SET status=? WHERE plan_id=?",
            (execution_status, plan["plan_id"]),
        )
    _event(
        store,
        execution_id,
        "VERIFICATION_COMPLETED",
        detail={
            "verified": verified,
            "failed": failed,
            "missing": missing,
            "execution_status": execution_status,
        },
    )
    summary = json.loads(execution["summary_json"] or "{}")
    summary["counts"] = counts
    summary["verification"] = {
        "verified": verified,
        "failed": failed,
        "missing": missing,
        "status": "PASS" if failed == 0 else "FAIL",
    }
    store.conn.execute(
        "UPDATE executions SET summary_json=? WHERE execution_id=?",
        (json.dumps(summary, sort_keys=True), execution_id),
    )
    store.conn.execute(
        "INSERT INTO verification_checks(run_id, check_name, status, detail, checked_at) VALUES (?, ?, ?, ?, ?)",
        (
            plan["source_run_id"],
            f"execution_verify:{execution_id}",
            "PASS" if failed == 0 else "FAIL",
            json.dumps(
                {
                    "verified": verified,
                    "failed": failed,
                    "missing": missing,
                    "mismatches": mismatches,
                    "execution_status": execution_status,
                },
                sort_keys=True,
            ),
            utc_now_iso(),
        ),
    )
    store.commit()
    return {
        "execution_id": execution_id,
        "status": "PASS" if failed == 0 else "FAIL",
        "execution_status": execution_status,
        "verified": verified,
        "failed": failed,
        "missing": missing,
        "mismatches": mismatches,
        "counts": counts,
    }

def reconcile_execution(
    store: Store, execution_id: str, out_dir: str | Path
) -> dict[str, Any]:
    execution = get_execution_row(store, execution_id)
    plan = get_plan_row(store, execution["plan_id"])
    rows = store.conn.execute(
        """
        SELECT p.item_id, p.source_id, p.source_rel_path, p.disposition,
               p.destination_rel, p.expected_sha256, p.size_bytes, p.reason,
               i.state, i.actual_sha256, i.error_code, i.error_message
        FROM plan_items p
        LEFT JOIN execution_items i
          ON i.execution_id=? AND i.item_id=p.item_id
        WHERE p.plan_id=?
        ORDER BY p.item_id
        """,
        (execution_id, plan["plan_id"]),
    ).fetchall()
    outcomes = Counter()
    records: list[dict[str, Any]] = []
    for row in rows:
        disposition = row["disposition"]
        state = row["state"]
        if disposition == "copy":
            if state == "verified":
                outcome = "copied_verified"
            elif state == "failed":
                outcome = "failed"
            elif state == "conflict":
                outcome = "conflict"
            else:
                outcome = "incomplete"
        elif disposition == "duplicate_omit":
            outcome = "duplicate_omitted"
        elif disposition == "exclude":
            outcome = "excluded"
        elif disposition == "defer":
            outcome = "deferred"
        elif disposition == "conflict":
            outcome = "plan_conflict"
        else:
            outcome = "unclassified"
        outcomes[outcome] += 1
        records.append(
            {
                "item_id": row["item_id"],
                "source_id": row["source_id"],
                "source_rel_path": row["source_rel_path"],
                "disposition": disposition,
                "destination_rel": row["destination_rel"] or "",
                "size_bytes": row["size_bytes"],
                "expected_sha256": row["expected_sha256"] or "",
                "execution_state": state or "",
                "outcome": outcome,
                "error_code": row["error_code"] or "",
                "error_message": row["error_message"] or "",
                "reason": row["reason"],
            }
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"reconciliation-{execution_id}.csv"
    fieldnames = [
        "item_id",
        "source_id",
        "source_rel_path",
        "disposition",
        "destination_rel",
        "size_bytes",
        "expected_sha256",
        "execution_state",
        "outcome",
        "error_code",
        "error_message",
        "reason",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    total = len(records)
    outcome_total = sum(outcomes.values())
    balanced = outcome_total == total
    required_copy = sum(1 for row in rows if row["disposition"] == "copy")
    verified_copy = outcomes.get("copied_verified", 0)
    authorized_excluded = outcomes.get("excluded", 0)
    authorized_deferred = outcomes.get("deferred", 0)
    duplicate_omitted = outcomes.get("duplicate_omitted", 0)
    blocking_names = (
        "failed",
        "conflict",
        "plan_conflict",
        "incomplete",
        "unclassified",
    )
    blocking = {name: outcomes.get(name, 0) for name in blocking_names if outcomes.get(name)}
    successful = (
        balanced
        and not blocking
        and verified_copy == required_copy
    )
    if not balanced:
        assessment = "incomplete"
    elif successful:
        assessment = "success"
    elif outcomes.get("incomplete"):
        assessment = "incomplete"
    else:
        assessment = "failed"
    summary = {
        "execution_id": execution_id,
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "execution_status": execution["status"],
        "total_plan_items": total,
        "outcomes": dict(outcomes),
        "outcome_total": outcome_total,
        "balanced": balanced,
        "successful": successful,
        "assessment": assessment,
        "required_copy_items": required_copy,
        "verified_copy_items": verified_copy,
        "authorized_excluded": authorized_excluded,
        "authorized_deferred": authorized_deferred,
        "duplicate_omitted": duplicate_omitted,
        "blocking_outcomes": blocking,
        "csv": str(csv_path),
    }
    (out / f"reconciliation-{execution_id}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def execution_preview_lines(store: Store, execution_id: str, limit: int = 40) -> list[str]:
    execution = get_execution_row(store, execution_id)
    rows = store.conn.execute(
        """
        SELECT i.item_id, p.source_id, p.source_rel_path, p.destination_rel,
               i.state, i.error_code
        FROM execution_items i JOIN plan_items p
          ON p.plan_id=? AND p.item_id=i.item_id
        WHERE i.execution_id=? ORDER BY i.item_id
        """,
        (execution["plan_id"], execution_id),
    ).fetchall()
    lines = [
        f"execution: {execution_id}",
        f"plan: {execution['plan_id']}",
        f"status: {execution['status']}",
        f"destination: {execution['destination_root']}",
    ]
    for row in rows[:limit]:
        lines.append(
            f"  {row['state']:10} {row['source_id']}:{row['source_rel_path']}"
            f" -> {row['destination_rel'] or ''}"
            + (f" [{row['error_code']}]" if row["error_code"] else "")
        )
    if len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more")
    return lines
