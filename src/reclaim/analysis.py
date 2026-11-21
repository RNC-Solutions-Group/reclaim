"""Deterministic analysis over an inventory: duplicates, overlap, structure.

Analysis is read-only and reproducible.  Exact-duplicate claims are based on
complete SHA-256 digests, never on names, sizes, or timestamps alone.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classify import PolicyEngine, TypeProfile
from .store import Store

CONTEXT_SEGMENTS = {
    "desktop",
    "documents",
    "downloads",
    "download",
    "pictures",
    "photos",
    "movies",
    "videos",
    "onedrive",
    "users",
    "documents and settings",
}


@dataclass(frozen=True, slots=True)
class FileRow:
    entry_id: int
    source_id: str
    source_label: str
    priority: int
    rel_path: str
    name: str
    depth: int
    size_bytes: int
    modified_ns: int | None
    created_ns: int | None
    is_media_library: bool
    is_cloud: bool
    is_hidden: bool
    digest: str | None
    hash_status: str
    signature_hex: str | None
    kind: str


@dataclass(frozen=True, slots=True)
class DirRow:
    source_id: str
    rel_path: str
    name: str
    depth: int
    is_media_library: bool
    error_code: str | None


def load_files(store: Store, run_id: str, profile: TypeProfile) -> list[FileRow]:
    rows: list[FileRow] = []
    cursor = store.conn.execute(
        """
        SELECT e.entry_id, e.source_id, s.label, s.priority, e.rel_path, e.name,
               e.depth, e.size_bytes, e.modified_ns, e.created_ns,
               e.is_media_library, e.is_cloud, e.is_hidden,
               h.digest, h.status, h.signature_hex
        FROM entries e
        JOIN sources s ON s.source_id = e.source_id
        LEFT JOIN hashes h ON h.entry_id = e.entry_id
        WHERE e.run_id = ? AND e.entry_type = 'file'
        ORDER BY e.source_id, e.rel_path
        """,
        (run_id,),
    )
    for row in cursor:
        entry = _light_entry(row)
        rows.append(
            FileRow(
                entry_id=row["entry_id"],
                source_id=row["source_id"],
                source_label=row["label"],
                priority=row["priority"],
                rel_path=row["rel_path"],
                name=row["name"],
                depth=row["depth"],
                size_bytes=row["size_bytes"] or 0,
                modified_ns=row["modified_ns"],
                created_ns=row["created_ns"],
                is_media_library=bool(row["is_media_library"]),
                is_cloud=bool(row["is_cloud"]),
                is_hidden=bool(row["is_hidden"]),
                digest=row["digest"],
                hash_status=row["status"] or "missing",
                signature_hex=row["signature_hex"],
                kind=profile.kind_for(entry),
            )
        )
    return rows


def load_dirs(store: Store, run_id: str) -> list[DirRow]:
    return [
        DirRow(
            source_id=row["source_id"],
            rel_path=row["rel_path"],
            name=row["name"],
            depth=row["depth"],
            is_media_library=bool(row["is_media_library"]),
            error_code=row["error_code"],
        )
        for row in store.conn.execute(
            """
            SELECT source_id, rel_path, name, depth, is_media_library, error_code
            FROM entries WHERE run_id=? AND entry_type='dir'
            ORDER BY source_id, rel_path
            """,
            (run_id,),
        )
    ]


class _LightEntry:
    """Minimal attribute bag accepted by TypeProfile.kind_for."""

    __slots__ = ("rel_path", "name", "entry_type")

    def __init__(self, row: Any) -> None:
        self.rel_path = row["rel_path"]
        self.name = row["name"]
        self.entry_type = "file"


def _light_entry(row: Any) -> _LightEntry:
    return _LightEntry(row)


# -- duplicate analysis ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class DuplicateOccurrence:
    row: FileRow
    role: str  # canonical | duplicate


@dataclass
class DuplicateGroup:
    group_id: str
    digest: str
    size_bytes: int
    occurrences: list[FileRow]
    canonical: FileRow
    filename_variants: int
    extension_variants: int
    earliest_modified_ns: int | None
    latest_modified_ns: int | None

    @property
    def redundant_bytes(self) -> int:
        return self.size_bytes * (len(self.occurrences) - 1)

    @property
    def sources(self) -> list[str]:
        return sorted({row.source_id for row in self.occurrences})


def build_duplicates(rows: list[FileRow]) -> list[DuplicateGroup]:
    by_digest: dict[str, list[FileRow]] = defaultdict(list)
    for row in rows:
        if row.hash_status != "ok" or row.digest is None or row.size_bytes <= 0:
            continue
        by_digest[row.digest].append(row)

    groups: list[DuplicateGroup] = []
    for digest in sorted(by_digest):
        occurrences = by_digest[digest]
        if len(occurrences) < 2:
            continue
        canonical = min(
            occurrences,
            key=lambda row: (row.priority, row.depth, len(row.rel_path), row.rel_path),
        )
        timestamps = [row.modified_ns for row in occurrences if row.modified_ns]
        groups.append(
            DuplicateGroup(
                group_id="",
                digest=digest,
                size_bytes=occurrences[0].size_bytes,
                occurrences=occurrences,
                canonical=canonical,
                filename_variants=len({row.name.casefold() for row in occurrences}),
                extension_variants=len(
                    {
                        row.name.rsplit(".", 1)[-1].casefold()
                        if "." in row.name
                        else ""
                        for row in occurrences
                    }
                ),
                earliest_modified_ns=min(timestamps) if timestamps else None,
                latest_modified_ns=max(timestamps) if timestamps else None,
            )
        )
    groups.sort(key=lambda group: (-group.redundant_bytes, group.digest))
    for index, group in enumerate(groups, start=1):
        group.group_id = f"dup-{index:06d}"
    return groups


def duplicate_role_map(groups: list[DuplicateGroup]) -> dict[int, tuple[str, str]]:
    """Map entry_id -> (role, group_id)."""

    roles: dict[int, tuple[str, str]] = {}
    for group in groups:
        for row in group.occurrences:
            role = "canonical" if row.entry_id == group.canonical.entry_id else "duplicate"
            roles[row.entry_id] = (role, group.group_id)
    return roles


# -- source and content overlap -------------------------------------------


def source_overlap(rows: list[FileRow]) -> dict[str, Any]:
    sources = sorted({row.source_id for row in rows})
    file_totals: dict[str, dict[str, int]] = {
        source: {"files": 0, "bytes": 0, "hashed_files": 0, "hashed_bytes": 0}
        for source in sources
    }
    digest_sources: dict[str, set[str]] = defaultdict(set)
    digest_size: dict[str, int] = {}
    for row in rows:
        stats = file_totals[row.source_id]
        stats["files"] += 1
        stats["bytes"] += row.size_bytes
        if row.hash_status == "ok" and row.digest:
            stats["hashed_files"] += 1
            stats["hashed_bytes"] += row.size_bytes
            digest_sources[row.digest].add(row.source_id)
            digest_size[row.digest] = row.size_bytes

    per_source: dict[str, dict[str, Any]] = {}
    for source in sources:
        distinct = sum(
            digest_size[digest]
            for digest, owners in digest_sources.items()
            if source in owners
        )
        unique = sum(
            digest_size[digest]
            for digest, owners in digest_sources.items()
            if owners == {source}
        )
        per_source[source] = {
            **file_totals[source],
            "distinct_content_bytes": distinct,
            "unique_content_bytes": unique,
            "shared_content_bytes": distinct - unique,
            "intra_source_redundant_bytes": file_totals[source]["hashed_bytes"] - distinct,
            "unresolved_bytes": file_totals[source]["bytes"]
            - file_totals[source]["hashed_bytes"],
        }

    pair_shared: dict[tuple[str, str], tuple[int, int]] = {}
    for i, left in enumerate(sources):
        for right in sources[i + 1 :]:
            shared_digests = [
                digest
                for digest, owners in digest_sources.items()
                if left in owners and right in owners
            ]
            shared_bytes = sum(digest_size[digest] for digest in shared_digests)
            pair_shared[(left, right)] = (len(shared_digests), shared_bytes)

    return {"sources": per_source, "pairs": pair_shared}


# -- type and extension summaries -----------------------------------------


def kind_summary(rows: list[FileRow]) -> dict[str, dict[str, dict[str, int]]]:
    summary: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"files": 0, "bytes": 0})
    )
    for row in rows:
        for source in (row.source_id, "__all__"):
            bucket = summary[source][row.kind]
            bucket["files"] += 1
            bucket["bytes"] += row.size_bytes
    return {source: dict(kinds) for source, kinds in summary.items()}


def extension_summary(rows: list[FileRow]) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"files": 0, "bytes": 0}
    )
    for row in rows:
        extension = (
            row.name.rsplit(".", 1)[-1].casefold() if "." in row.name.strip(".") else ""
        )
        bucket = totals[(row.source_id, extension or "(none)")]
        bucket["files"] += 1
        bucket["bytes"] += row.size_bytes
    output = [
        {
            "source_id": source,
            "extension": extension,
            "files": values["files"],
            "bytes": values["bytes"],
        }
        for (source, extension), values in totals.items()
    ]
    return sorted(output, key=lambda item: (item["source_id"], -item["bytes"]))


def signature_mismatches(
    rows: list[FileRow], profile: TypeProfile
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        if not row.signature_hex or row.hash_status != "ok":
            continue
        if profile.signature_mismatch_for_kind(row.kind, row.signature_hex):
            family = profile.signature_family(row.signature_hex)
            mismatches.append(
                {
                    "source_id": row.source_id,
                    "rel_path": row.rel_path,
                    "size_bytes": row.size_bytes,
                    "extension_kind": row.kind,
                    "signature_family": family,
                    "signature_hex": row.signature_hex,
                }
            )
    return mismatches


# -- structure -------------------------------------------------------------


def directory_totals(rows: list[FileRow]) -> dict[tuple[str, str], dict[str, int]]:
    totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"files": 0, "bytes": 0}
    )
    for row in rows:
        parts = row.rel_path.split("/")
        for depth in range(0, len(parts)):
            directory = "." if depth == 0 else "/".join(parts[:depth])
            bucket = totals[(row.source_id, directory)]
            bucket["files"] += 1
            bucket["bytes"] += row.size_bytes
    return totals


def top_directories(
    totals: dict[tuple[str, str], dict[str, int]], limit: int = 40
) -> list[dict[str, Any]]:
    items = [
        {
            "source_id": source,
            "rel_path": directory,
            "files": values["files"],
            "bytes": values["bytes"],
        }
        for (source, directory), values in totals.items()
    ]
    return sorted(items, key=lambda item: -item["bytes"])[:limit]


def top_level_overlap(rows: list[FileRow]) -> list[dict[str, Any]]:
    trees: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for row in rows:
        if row.hash_status != "ok" or not row.digest:
            continue
        first = row.rel_path.split("/", 1)[0]
        tree = (row.source_id, first)
        trees[tree][row.digest] = trees[tree].get(row.digest, 0) + 1

    candidates = [
        (key, digest_counts)
        for key, digest_counts in trees.items()
        if sum(digest_counts.values()) >= 20
    ]
    output: list[dict[str, Any]] = []
    for index, (left_key, left) in enumerate(candidates):
        for right_key, right in candidates[index + 1 :]:
            if left_key[0] == right_key[0] and left_key[1] == right_key[1]:
                continue
            shared = set(left) & set(right)
            if not shared:
                continue
            shared_files = sum(min(left[digest], right[digest]) for digest in shared)
            union_files = sum(
                max(left.get(digest, 0), right.get(digest, 0)) for digest in set(left) | set(right)
            )
            if not union_files:
                continue
            similarity = shared_files / union_files
            if similarity < 0.25:
                continue
            output.append(
                {
                    "left_source": left_key[0],
                    "left_path": left_key[1],
                    "right_source": right_key[0],
                    "right_path": right_key[1],
                    "shared_hashes": len(shared),
                    "shared_files": shared_files,
                    "union_files": union_files,
                    "similarity": round(similarity, 4),
                }
            )
    return sorted(output, key=lambda item: -item["similarity"])[:40]


def depth_histogram(rows: list[FileRow]) -> list[dict[str, Any]]:
    histogram: dict[int, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "bytes": 0}
    )
    for row in rows:
        histogram[row.depth]["files"] += 1
        histogram[row.depth]["bytes"] += row.size_bytes
    return [
        {"depth": depth, **values} for depth, values in sorted(histogram.items())
    ]


def media_libraries(
    dirs: list[DirRow], rows: list[FileRow]
) -> list[dict[str, Any]]:
    roots = [
        directory
        for directory in dirs
        if directory.is_media_library
        and not any(
            other.source_id == directory.source_id
            and other.rel_path == _parent(directory.rel_path)
            and other.is_media_library
            for other in dirs
        )
    ]
    output: list[dict[str, Any]] = []
    for root in roots:
        prefix = root.rel_path + "/"
        files = [
            row
            for row in rows
            if row.source_id == root.source_id and row.rel_path.startswith(prefix)
        ]
        output.append(
            {
                "source_id": root.source_id,
                "rel_path": root.rel_path,
                "files": len(files),
                "bytes": sum(row.size_bytes for row in files),
                "deferred_files": sum(
                    1 for row in files if row.hash_status == "deferred"
                ),
            }
        )
    return sorted(output, key=lambda item: -item["bytes"])


def nested_context_findings(dirs: list[DirRow]) -> list[dict[str, Any]]:
    findings: Counter[tuple[str, str, int]] = Counter()
    examples: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for directory in dirs:
        if directory.rel_path == ".":
            continue
        parts = directory.rel_path.split("/")
        if len(parts) < 2:
            continue
        for index, part in enumerate(parts):
            if index >= 1 and part.casefold() in CONTEXT_SEGMENTS:
                key = (directory.source_id, part.casefold(), index)
                findings[key] += 1
                if len(examples[key]) < 3:
                    examples[key].append(directory.rel_path)
                break
    return [
        {
            "source_id": source,
            "segment": segment,
            "depth_index": depth,
            "count": count,
            "examples": examples[(source, segment, depth)],
        }
        for (source, segment, depth), count in findings.most_common(30)
    ]


def repeated_segments(dirs: list[DirRow]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for directory in dirs:
        parts = directory.rel_path.split("/")
        for index in range(1, len(parts)):
            if parts[index].casefold() == parts[index - 1].casefold():
                findings.append(
                    {
                        "source_id": directory.source_id,
                        "rel_path": directory.rel_path,
                        "repeated_segment": parts[index],
                    }
                )
                break
    return findings


def case_collisions(dirs: list[DirRow]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for directory in dirs:
        parent = _parent(directory.rel_path)
        grouped[(directory.source_id, parent, directory.name.casefold())].append(
            directory.name
        )
    return [
        {
            "source_id": source,
            "parent": parent,
            "names": names,
        }
        for (source, parent, _folded), names in grouped.items()
        if len(names) > 1
    ]


def name_conflicts(rows: list[FileRow]) -> dict[str, Any]:
    by_name: dict[str, set[str]] = defaultdict(set)
    bytes_by_name: Counter[str] = Counter()
    for row in rows:
        if row.hash_status == "ok" and row.digest:
            by_name[row.name.casefold()].add(row.digest)
            bytes_by_name[row.name.casefold()] += row.size_bytes
    conflicts = {
        name: digests for name, digests in by_name.items() if len(digests) > 1
    }
    return {
        "conflicting_names": len(conflicts),
        "top": sorted(
            (
                {"name": name, "distinct_contents": len(digests), "bytes": bytes_by_name[name]}
                for name, digests in conflicts.items()
            ),
            key=lambda item: -item["bytes"],
        )[:25],
    }


def timeline(rows: list[FileRow]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], dict[str, int]] = defaultdict(
        lambda: {"files": 0, "bytes": 0}
    )
    for row in rows:
        if row.modified_ns is None:
            continue
        year = datetime.fromtimestamp(row.modified_ns / 1e9, tz=timezone.utc).year
        bucket = buckets[(row.source_id, year)]
        bucket["files"] += 1
        bucket["bytes"] += row.size_bytes
    return [
        {"source_id": source, "year": year, **values}
        for (source, year), values in sorted(buckets.items())
    ]


def empty_directories(store: Store, run_id: str) -> dict[str, Any]:
    rows = store.conn.execute(
        """
        SELECT d.source_id, d.rel_path
        FROM entries d
        WHERE d.run_id=? AND d.entry_type='dir' AND d.rel_path <> '.'
          AND NOT EXISTS (
            SELECT 1 FROM entries c
            WHERE c.run_id=d.run_id AND c.source_id=d.source_id
              AND c.parent_rel_path=d.rel_path
          )
        ORDER BY d.source_id, d.rel_path
        """,
        (run_id,),
    ).fetchall()
    by_source: Counter[str] = Counter(row["source_id"] for row in rows)
    return {
        "total": len(rows),
        "by_source": dict(by_source),
        "examples": [f"{row['source_id']}:{row['rel_path']}" for row in rows[:40]],
    }


def long_paths(store: Store, run_id: str) -> dict[str, Any]:
    root_lengths = {
        row["source_id"]: len(row["root_path"])
        for row in store.conn.execute("SELECT source_id, root_path FROM sources")
    }
    rows = store.conn.execute(
        """
        SELECT source_id, rel_path FROM entries
        WHERE run_id=? AND entry_type IN ('file', 'dir')
        """,
        (run_id,),
    ).fetchall()
    measured = [
        (
            len(row["rel_path"]) + root_lengths.get(row["source_id"], 0) + 1,
            row["source_id"],
            row["rel_path"],
        )
        for row in rows
    ]
    measured.sort(reverse=True)
    over_240 = sum(1 for length, _, _ in measured if length >= 240)
    over_260 = sum(1 for length, _, _ in measured if length >= 260)
    return {
        "max_length": measured[0][0] if measured else 0,
        "count_ge_240": over_240,
        "count_ge_260": over_260,
        "longest": [
            {"length": length, "source_id": source, "rel_path": rel}
            for length, source, rel in measured[:10]
        ],
    }


def largest_files(rows: list[FileRow], limit: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "source_id": row.source_id,
            "rel_path": row.rel_path,
            "size_bytes": row.size_bytes,
            "kind": row.kind,
        }
        for row in sorted(rows, key=lambda item: -item.size_bytes)[:limit]
    ]


# -- disposition -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispositionRow:
    row: FileRow
    recommended: str
    rule_id: str
    rationale: str
    confidence: float
    duplicate_role: str
    duplicate_group: str
    effective: str
    decision_source: str


def build_dispositions(
    rows: list[FileRow],
    groups: list[DuplicateGroup],
    policy: PolicyEngine,
) -> list[DispositionRow]:
    roles = duplicate_role_map(groups)
    output: list[DispositionRow] = []
    for row in rows:
        decision = policy.evaluate(row, row.kind)  # type: ignore[arg-type]
        role, group_id = roles.get(row.entry_id, ("unique", ""))
        recommended = decision.disposition
        rule_id = decision.rule_id
        rationale = decision.rationale
        if role == "duplicate" and recommended == "retain":
            recommended = "duplicate"
            rule_id = "exact-duplicate"
            rationale = (
                "Byte-identical to another inventoried file; retain at least one "
                "occurrence and record provenance for every location"
            )
        effective = policy.resolve(decision).disposition
        if recommended != decision.disposition:
            effective = recommended
        output.append(
            DispositionRow(
                row=row,
                recommended=recommended,
                rule_id=rule_id,
                rationale=rationale,
                confidence=decision.confidence,
                duplicate_role=role,
                duplicate_group=group_id,
                effective=effective,
                decision_source="recommendation",
            )
        )
    return output


def disposition_summary(
    dispositions: list[DispositionRow],
) -> dict[str, dict[str, dict[str, int]]]:
    summary: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"files": 0, "bytes": 0})
    )
    for item in dispositions:
        bucket = summary[item.row.source_id][item.recommended]
        bucket["files"] += 1
        bucket["bytes"] += item.row.size_bytes
        overall = summary["__all__"][item.recommended]
        overall["files"] += 1
        overall["bytes"] += item.row.size_bytes
    return {source: dict(values) for source, values in summary.items()}


# -- source comparison -----------------------------------------------------


def source_comparison(
    store: Store, run_id: str, rows: list[FileRow], overlap: dict[str, Any]
) -> list[dict[str, Any]]:
    base = store.conn.execute(
        """
        SELECT e.source_id,
               COUNT(*) AS files,
               SUM(e.size_bytes) AS bytes,
               SUM(e.is_hidden) AS hidden_files,
               SUM(e.is_media_library) AS media_files,
               SUM(CASE WHEN e.is_media_library=1 THEN e.size_bytes ELSE 0 END) AS media_bytes,
               MAX(e.depth) AS max_depth
        FROM entries e
        WHERE e.run_id=? AND e.entry_type='file'
        GROUP BY e.source_id
        """,
        (run_id,),
    ).fetchall()
    dirs = store.conn.execute(
        """
        SELECT source_id, COUNT(*) AS dirs
        FROM entries WHERE run_id=? AND entry_type='dir'
        GROUP BY source_id
        """,
        (run_id,),
    ).fetchall()
    dir_counts = {row["source_id"]: row["dirs"] for row in dirs}
    empty = empty_directories(store, run_id)["by_source"]
    hash_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        hash_stats[row.source_id][row.hash_status] += 1
    output: list[dict[str, Any]] = []
    for row in base:
        source = row["source_id"]
        overlap_source = overlap["sources"].get(source, {})
        kinds = Counter()
        for item in rows:
            if item.source_id == source:
                kinds[item.kind] += item.size_bytes
        output.append(
            {
                "source_id": source,
                "files": row["files"],
                "bytes": row["bytes"] or 0,
                "dirs": dir_counts.get(source, 0),
                "empty_dirs": empty.get(source, 0),
                "max_depth": row["max_depth"] or 0,
                "hidden_files": row["hidden_files"] or 0,
                "media_files": row["media_files"] or 0,
                "media_bytes": row["media_bytes"] or 0,
                "hashed_files": hash_stats[source]["ok"],
                "deferred_files": hash_stats[source]["deferred"],
                "error_files": hash_stats[source]["error"],
                "unique_bytes": overlap_source.get("unique_content_bytes", 0),
                "shared_bytes": overlap_source.get("shared_content_bytes", 0),
                "distinct_bytes": overlap_source.get("distinct_content_bytes", 0),
                "intra_duplicate_bytes": overlap_source.get(
                    "intra_source_redundant_bytes", 0
                ),
                "top_kinds": [
                    {"kind": kind, "bytes": size}
                    for kind, size in kinds.most_common(3)
                ],
            }
        )
    return sorted(output, key=lambda item: -item["bytes"])


def _parent(rel_path: str) -> str:
    if rel_path == ".":
        return ""
    head, sep, _ = rel_path.rpartition("/")
    return head if sep else "."


def build_metrics(
    store: Store,
    run_id: str,
    rows: list[FileRow],
    dirs: list[DirRow],
    groups: list[DuplicateGroup],
    dispositions: list[DispositionRow],
    profile: TypeProfile,
) -> dict[str, Any]:
    overlap = source_overlap(rows)
    totals = directory_totals(rows)
    all_bytes = sum(row.size_bytes for row in rows)
    redundant = sum(group.redundant_bytes for group in groups)
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "files": len(rows),
            "dirs": len(dirs),
            "bytes": all_bytes,
            "hashed_files": sum(1 for row in rows if row.hash_status == "ok"),
            "deferred_files": sum(1 for row in rows if row.hash_status == "deferred"),
            "error_files": sum(1 for row in rows if row.hash_status == "error"),
            "duplicate_groups": len(groups),
            "duplicate_occurrences": sum(len(group.occurrences) for group in groups),
            "redundant_bytes": redundant,
            "redundant_share": round(redundant / all_bytes, 6) if all_bytes else 0.0,
        },
        "source_comparison": source_comparison(store, run_id, rows, overlap),
        "overlap": {
            "sources": overlap["sources"],
            "pairs": [
                {
                    "left": left,
                    "right": right,
                    "shared_hashes": values[0],
                    "shared_bytes": values[1],
                }
                for (left, right), values in overlap["pairs"].items()
            ],
        },
        "kinds": kind_summary(rows),
        "extensions": extension_summary(rows),
        "duplicates": {
            "count": len(groups),
            "redundant_bytes": redundant,
            "top": [
                {
                    "group_id": group.group_id,
                    "digest": group.digest,
                    "size_bytes": group.size_bytes,
                    "occurrences": len(group.occurrences),
                    "sources": group.sources,
                    "redundant_bytes": group.redundant_bytes,
                    "canonical": f"{group.canonical.source_id}:{group.canonical.rel_path}",
                }
                for group in groups[:30]
            ],
        },
        "structure": {
            "depth_histogram": depth_histogram(rows),
            "top_directories": top_directories(totals),
            "top_level_overlap": top_level_overlap(rows),
            "media_libraries": media_libraries(dirs, rows),
            "nested_context": nested_context_findings(dirs),
            "repeated_segments": repeated_segments(dirs)[:40],
            "case_collisions": case_collisions(dirs)[:40],
            "name_conflicts": name_conflicts(rows),
            "timeline": timeline(rows),
            "empty_directories": empty_directories(store, run_id),
            "long_paths": long_paths(store, run_id),
            "largest_files": largest_files(rows),
        },
        "dispositions": {
            "summary": disposition_summary(dispositions),
            "potential_junk_files": sum(
                1 for item in dispositions if item.recommended == "potential_junk"
            ),
            "potential_junk_bytes": sum(
                item.row.size_bytes
                for item in dispositions
                if item.recommended == "potential_junk"
            ),
            "needs_review_files": sum(
                1 for item in dispositions if item.recommended == "needs_review"
            ),
            "deferred_media_files": sum(
                1 for item in dispositions if item.recommended == "defer_media"
            ),
        },
        "signature_mismatches": signature_mismatches(rows, profile),
    }
