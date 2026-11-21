"""CSV and JSON report writers.

Reports are written to the private project workspace, never into the
repository or a source tree.  CSV files are UTF-8 with a BOM so spreadsheet
applications open Unicode paths correctly while remaining machine-readable.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis import (
    DirRow,
    DispositionRow,
    DuplicateGroup,
    FileRow,
    build_metrics,
)
from .classify import TypeProfile
from .store import Store

INVENTORY_COLUMNS = [
    "source_id",
    "source_label",
    "rel_path",
    "entry_type",
    "depth",
    "name",
    "extension",
    "kind",
    "size_bytes",
    "created_utc",
    "modified_utc",
    "accessed_utc",
    "sha256",
    "hash_status",
    "hash_error_code",
    "is_hidden",
    "is_system",
    "is_reparse",
    "is_cloud",
    "is_media_library",
    "link_target",
    "scan_error_code",
    "scan_error_message",
    "note",
]


def format_ts(ns: int | None) -> str:
    if not ns:
        return ""
    try:
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return ""


def extension_of(name: str) -> str:
    if "." in name.strip("."):
        return name.rsplit(".", 1)[-1].casefold()
    return ""


class _KindEntry:
    __slots__ = ("rel_path", "name", "entry_type")

    def __init__(self, rel_path: str, name: str, entry_type: str) -> None:
        self.rel_path = rel_path
        self.name = name
        self.entry_type = entry_type


def write_inventory(
    store: Store, run_id: str, profile: TypeProfile, path: Path
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    cursor = store.conn.execute(
        """
        SELECT e.source_id, s.label, e.rel_path, e.entry_type, e.depth, e.name,
               e.size_bytes, e.created_ns, e.modified_ns, e.accessed_ns,
               e.is_hidden, e.is_system, e.is_reparse, e.is_cloud,
               e.is_media_library, e.link_target, e.error_code, e.error_message,
               e.note, h.digest, h.status, h.error_code AS hash_error
        FROM entries e
        JOIN sources s ON s.source_id = e.source_id
        LEFT JOIN hashes h ON h.entry_id = e.entry_id
        WHERE e.run_id=?
        ORDER BY e.source_id, e.rel_path
        """,
        (run_id,),
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(INVENTORY_COLUMNS)
        for row in cursor:
            entry = _KindEntry(row["rel_path"], row["name"], row["entry_type"])
            kind = (
                "directory"
                if row["entry_type"] == "dir"
                else profile.kind_for(entry)  # type: ignore[arg-type]
            )
            writer.writerow(
                [
                    row["source_id"],
                    row["label"],
                    row["rel_path"],
                    row["entry_type"],
                    row["depth"],
                    row["name"],
                    extension_of(row["name"]),
                    kind,
                    row["size_bytes"],
                    format_ts(row["created_ns"]),
                    format_ts(row["modified_ns"]),
                    format_ts(row["accessed_ns"]),
                    row["digest"] or "",
                    row["status"] or "",
                    row["hash_error"] or "",
                    int(bool(row["is_hidden"])),
                    int(bool(row["is_system"])),
                    int(bool(row["is_reparse"])),
                    int(bool(row["is_cloud"])),
                    int(bool(row["is_media_library"])),
                    row["link_target"] or "",
                    row["error_code"] or "",
                    row["error_message"] or "",
                    row["note"] or "",
                ]
            )
            count += 1
    return count


def write_duplicates(
    groups: list[DuplicateGroup], groups_path: Path, occurrences_path: Path
) -> None:
    groups_path.parent.mkdir(parents=True, exist_ok=True)
    with groups_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "group_id",
                "sha256",
                "size_bytes",
                "occurrences",
                "source_count",
                "sources",
                "redundant_bytes",
                "filename_variants",
                "extension_variants",
                "canonical_source",
                "canonical_path",
                "locations_json",
                "earliest_modified_utc",
                "latest_modified_utc",
            ]
        )
        for group in groups:
            writer.writerow(
                [
                    group.group_id,
                    group.digest,
                    group.size_bytes,
                    len(group.occurrences),
                    len(group.sources),
                    ",".join(group.sources),
                    group.redundant_bytes,
                    group.filename_variants,
                    group.extension_variants,
                    group.canonical.source_id,
                    group.canonical.rel_path,
                    json.dumps(
                        [
                            {"source_id": row.source_id, "rel_path": row.rel_path}
                            for row in group.occurrences
                        ],
                        ensure_ascii=False,
                    ),
                    format_ts(group.earliest_modified_ns),
                    format_ts(group.latest_modified_ns),
                ]
            )

    with occurrences_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "group_id",
                "sha256",
                "size_bytes",
                "role",
                "source_id",
                "rel_path",
                "modified_utc",
                "kind",
            ]
        )
        for group in groups:
            for row in group.occurrences:
                writer.writerow(
                    [
                        group.group_id,
                        group.digest,
                        group.size_bytes,
                        "canonical"
                        if row.entry_id == group.canonical.entry_id
                        else "duplicate",
                        row.source_id,
                        row.rel_path,
                        format_ts(row.modified_ns),
                        row.kind,
                    ]
                )


def write_filetypes(metrics: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    totals = {
        source: sum(bucket["bytes"] for bucket in kinds.values())
        for source, kinds in metrics["kinds"].items()
    }
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_id",
                "kind",
                "files",
                "bytes",
                "pct_files_of_source",
                "pct_bytes_of_source",
            ]
        )
        for source in sorted(metrics["kinds"]):
            source_total_bytes = totals.get(source, 0) or 1
            source_files = sum(
                bucket["files"] for bucket in metrics["kinds"][source].values()
            ) or 1
            for kind, values in sorted(
                metrics["kinds"][source].items(), key=lambda item: -item[1]["bytes"]
            ):
                writer.writerow(
                    [
                        source,
                        kind,
                        values["files"],
                        values["bytes"],
                        round(values["files"] / source_files, 6),
                        round(values["bytes"] / source_total_bytes, 6),
                    ]
                )


def write_extensions(metrics: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_id", "extension", "files", "bytes"])
        for row in metrics["extensions"]:
            writer.writerow(
                [row["source_id"], row["extension"], row["files"], row["bytes"]]
            )


def write_dispositions(dispositions: list[DispositionRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_id",
                "rel_path",
                "size_bytes",
                "kind",
                "sha256",
                "hash_status",
                "recommended_disposition",
                "effective_disposition",
                "decision_source",
                "rule_id",
                "rationale",
                "confidence",
                "duplicate_role",
                "duplicate_group",
                "is_media_library",
            ]
        )
        for item in dispositions:
            writer.writerow(
                [
                    item.row.source_id,
                    item.row.rel_path,
                    item.row.size_bytes,
                    item.row.kind,
                    item.row.digest or "",
                    item.row.hash_status,
                    item.recommended,
                    item.effective,
                    item.decision_source,
                    item.rule_id,
                    item.rationale,
                    item.confidence,
                    item.duplicate_role,
                    item.duplicate_group,
                    int(item.row.is_media_library),
                ]
            )


def write_signature_mismatches(metrics: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_id",
                "rel_path",
                "size_bytes",
                "extension_kind",
                "signature_family",
                "signature_hex",
            ]
        )
        for row in metrics["signature_mismatches"]:
            writer.writerow(
                [
                    row["source_id"],
                    row["rel_path"],
                    row["size_bytes"],
                    row["extension_kind"],
                    row["signature_family"],
                    row["signature_hex"],
                ]
            )


def write_errors(store: Store, run_id: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = store.conn.execute(
        """
        SELECT phase, source_id, rel_path, code, message, occurred_at
        FROM scan_errors WHERE run_id=?
        UNION ALL
        SELECT 'hash' AS phase, e.source_id, e.rel_path, h.error_code AS code,
               h.error_message AS message, h.hashed_at AS occurred_at
        FROM hashes h JOIN entries e ON e.entry_id = h.entry_id
        WHERE e.run_id=? AND h.status='error'
        ORDER BY phase, source_id, rel_path
        """,
        (run_id, run_id),
    ).fetchall()
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["phase", "source_id", "rel_path", "code", "message", "occurred_at"])
        for row in rows:
            writer.writerow(
                [
                    row["phase"],
                    row["source_id"],
                    row["rel_path"] or "",
                    row["code"],
                    row["message"],
                    row["occurred_at"] or "",
                ]
            )
    return len(rows)


def write_metrics(metrics: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def write_manifest(
    store: Store,
    run_id: str,
    path: Path,
    *,
    tool_version: str,
    config_path: str,
    config_sha256: str,
    sources: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    configuration: dict[str, Any] | None = None,
) -> None:
    run = store.get_run(run_id)
    manifest = {
        "tool": "reclaim",
        "tool_version": tool_version,
        "run_id": run_id,
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "status": run["status"],
        "config_path": config_path,
        "config_sha256": config_sha256,
        "configuration": configuration or {},
        "sources": sources,
        "fingerprints": json.loads(run["source_fingerprints_json"] or "{}"),
        "checks": checks,
        "counts": store.counts(run_id),
        "database": str(store.path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def build_all(
    store: Store,
    run_id: str,
    rows: list[FileRow],
    dirs: list[DirRow],
    profile: TypeProfile,
    policy: Any,
) -> tuple[list[DuplicateGroup], list[DispositionRow], dict[str, Any]]:
    from .analysis import build_dispositions, build_duplicates, build_metrics

    groups = build_duplicates(rows)
    dispositions = build_dispositions(rows, groups, policy)
    metrics = build_metrics(store, run_id, rows, dirs, groups, dispositions, profile)
    return groups, dispositions, metrics
