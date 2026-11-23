"""Deterministic migration planning, overrides, and authorization.

M1 deliberately plans only from inventory observations and policy/duplicate
decisions.  It does not classify document content, recommend filenames, or
modify source files.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import (
    FileRow,
    build_duplicates,
    duplicate_role_map,
    load_files,
)
from .classify import PolicyEngine, TypeProfile
from .config import ProjectConfig
from .destination import (
    DestinationItem,
    DestinationPolicy,
    normalize_relative,
    validate_destination_root,
    validate_plan,
)
from .errors import ReclaimError
from .paths import canonical_path, root_identity
from .scanner import SourceScanner
from .store import Store, utc_now_iso

PLAN_SCHEMA_VERSION = 1
DUPLICATE_POLICIES = ("prefer_one", "keep_all", "fail")
OVERRIDE_DISPOSITIONS = ("copy", "exclude", "defer", "duplicate_omit")


class PlanError(ReclaimError):
    """A plan cannot be built, validated, or authorized."""


@dataclass(frozen=True)
class PlanSettings:
    destination_root: Path
    destination_template: str = "{source_id}/{rel_path}"
    duplicate_policy: str = "prefer_one"
    target_platform: str | None = None
    max_path_length: int = 240
    max_component_length: int = 255

    def __post_init__(self) -> None:
        if self.duplicate_policy not in DUPLICATE_POLICIES:
            raise ValueError(
                f"duplicate_policy must be one of {DUPLICATE_POLICIES}"
            )
        if not str(self.destination_template).strip():
            raise ValueError("destination_template must not be empty")
        if self.max_path_length < 32:
            raise ValueError("max_path_length must be at least 32")


@dataclass(frozen=True)
class PlanItem:
    item_id: int
    entry_id: int
    source_id: str
    source_rel_path: str
    source_root_identity: str
    disposition: str
    destination_rel: str | None
    expected_sha256: str | None
    size_bytes: int
    source_mtime_ns: int | None
    canonical_entry_id: int | None
    decision_source: str
    reason: str


@dataclass
class PlanResult:
    plan_id: str
    plan_digest: str
    summary: dict[str, Any]
    items: list[PlanItem]
    issues: list[dict[str, Any]]
    created: bool


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha256(path.read_bytes())


def policy_fingerprint(config: ProjectConfig) -> str:
    """Fingerprint the policy/type data that affects recommendations."""

    parts = [config.fingerprint()]
    for path in (config.types_file, config.policy_file, config.policy_overlay_file):
        if path:
            parts.append(f"{path.name}:{_file_sha(path)}")
        else:
            parts.append("builtin")
    if not config.types_file:
        parts.append(
            "default-types:"
            + _sha256(
                resources.files("reclaim")
                .joinpath("data", "default-types.toml")
                .read_bytes()
            )
        )
    if not config.policy_file:
        parts.append(
            "default-policy:"
            + _sha256(
                resources.files("reclaim")
                .joinpath("data", "default-policy.toml")
                .read_bytes()
            )
        )
    return _sha256("\0".join(parts).encode("utf-8"))


def analysis_id_for(run_id: str, policy_fp: str) -> str:
    return "analysis-" + _sha256(
        f"{run_id}\0{policy_fp}\0{__version__}".encode("utf-8")
    )[:16]


def review_fingerprint(overrides: list[dict[str, Any]]) -> str:
    """Stable fingerprint of the overrides that affect a plan.

    The fingerprint deliberately excludes database IDs/timestamps.  It is
    stored in plan summary metadata for desktop freshness checks and does not
    alter the immutable plan body/digest.
    """

    normalized = [
        {
            "scope": row.get("scope", ""),
            "source_id": row.get("source_id", ""),
            "target_rel": row.get("target_rel", ""),
            "disposition": row.get("disposition", ""),
            "destination_rel": row.get("destination_rel") or "",
            "reason": row.get("reason", ""),
        }
        for row in overrides
    ]
    normalized.sort(
        key=lambda row: (
            row["source_id"],
            row["target_rel"],
            row["scope"],
            row["disposition"],
            row["destination_rel"],
            row["reason"],
        )
    )
    return _sha256(json.dumps(normalized, sort_keys=True).encode("utf-8"))


def _source_snapshot(store: Store, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    fingerprints = json.loads(run["source_fingerprints_json"] or "{}")
    identities = json.loads(run["source_identities_json"] or "{}")
    sources = {
        row["source_id"]: {
            "root_path": row["root_path"],
            "root_identity": row["root_identity"],
            "label": row["label"],
            "priority": row["priority"],
        }
        for row in store.conn.execute(
            "SELECT source_id, root_path, root_identity, label, priority FROM sources"
        )
    }
    if not fingerprints:
        raise PlanError(f"Run {run_id} has no source metadata snapshot")
    return {
        "fingerprints": fingerprints,
        "identities": identities,
        "sources": sources,
    }


def _render_template(
    template: str, source_id: str, rel_path: str
) -> str:
    name = rel_path.rsplit("/", 1)[-1]
    parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
    try:
        rendered = template.format(
            source_id=source_id,
            rel_path=rel_path,
            name=name,
            parent=parent,
        )
    except (KeyError, ValueError) as exc:
        raise PlanError(f"Invalid destination template: {exc}") from exc
    return rendered


def _active_overrides(store: Store) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.conn.execute(
            """
            SELECT override_id, scope, source_id, target_rel, disposition,
                   destination_rel, reason, created_at
            FROM overrides
            WHERE active=1
            ORDER BY created_at DESC, override_id
            """
        )
    ]


def _matching_override(
    overrides: list[dict[str, Any]], source_id: str, rel_path: str
) -> dict[str, Any] | None:
    exact = [
        row
        for row in overrides
        if row["scope"] == "file"
        and row["source_id"] == source_id
        and row["target_rel"] == rel_path
    ]
    if exact:
        return exact[0]
    directories = [
        row
        for row in overrides
        if row["scope"] == "directory"
        and row["source_id"] == source_id
        and (
            rel_path == row["target_rel"]
            or rel_path.startswith(row["target_rel"].rstrip("/") + "/")
        )
    ]
    if not directories:
        return None
    directories.sort(
        key=lambda row: (len(row["target_rel"]), row["created_at"]),
        reverse=True,
    )
    return directories[0]


def _recommendation(
    row: FileRow,
    policy: PolicyEngine,
    duplicate_role: str,
    duplicate_policy: str,
    canonical_entry_id: int | None,
) -> tuple[str, str, str, float, str]:
    """Return (disposition, rule_id, rationale, confidence, decision_source)."""

    decision = policy.evaluate(row, row.kind)  # type: ignore[arg-type]
    disposition = decision.disposition
    rule_id = decision.rule_id
    rationale = decision.rationale

    if duplicate_role == "duplicate":
        if duplicate_policy == "prefer_one":
            return (
                "duplicate_omit",
                "exact-duplicate",
                "Exact duplicate retained elsewhere in the plan; provenance is preserved",
                decision.confidence,
                "recommendation",
            )
        if duplicate_policy == "fail":
            return (
                "conflict",
                "exact-duplicate-conflict",
                "Duplicate policy is 'fail'; operator must resolve this group",
                decision.confidence,
                "recommendation",
            )

    if disposition == "retain":
        return ("copy", rule_id, rationale, decision.confidence, "recommendation")
    if disposition == "duplicate":
        return (
            "duplicate_omit",
            rule_id,
            rationale,
            decision.confidence,
            "recommendation",
        )
    if disposition == "exclude":
        return ("exclude", rule_id, rationale, decision.confidence, "recommendation")
    if disposition == "defer_media":
        return ("defer", rule_id, rationale, decision.confidence, "recommendation")
    # needs_review, potential_junk, related_version, similar, analysis_failure
    return (
        "conflict",
        rule_id,
        f"{rationale} (requires an explicit override before planning)",
        decision.confidence,
        "recommendation",
    )


def _plan_body(
    *,
    source_run_id: str,
    analysis_id: str,
    destination_root: str,
    destination_selected: str,
    destination_template: str,
    duplicate_policy: str,
    target_platform: str,
    max_path_length: int,
    policy_fp: str,
    source_snapshot: dict[str, Any],
    items: list[PlanItem],
    global_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "tool_version": __version__,
        "source_run_id": source_run_id,
        "analysis_id": analysis_id,
        "destination_root": destination_root,
        "destination_selected": destination_selected,
        "destination_template": destination_template,
        "duplicate_policy": duplicate_policy,
        "target_platform": target_platform,
        "max_path_length": max_path_length,
        "policy_fingerprint": policy_fp,
        "source_snapshot": source_snapshot,
        "items": [
            {
                "item_id": item.item_id,
                "entry_id": item.entry_id,
                "source_id": item.source_id,
                "source_rel_path": item.source_rel_path,
                "source_root_identity": item.source_root_identity,
                "disposition": item.disposition,
                "destination_rel": item.destination_rel,
                "expected_sha256": item.expected_sha256,
                "size_bytes": item.size_bytes,
                "source_mtime_ns": item.source_mtime_ns,
                "canonical_entry_id": item.canonical_entry_id,
                "decision_source": item.decision_source,
                "reason": item.reason,
            }
            for item in sorted(items, key=lambda value: value.item_id)
        ],
        "global_issues": global_issues,
    }


def plan_digest_for_body(body: dict[str, Any]) -> str:
    blob = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256(blob)


def recompute_plan_digest(store: Store, plan_id: str) -> str:
    row = get_plan_row(store, plan_id)
    items = get_plan_items(store, plan_id)
    global_issues = json.loads(row["summary_json"]).get("global_issues", [])
    body = _plan_body(
        source_run_id=row["source_run_id"],
        analysis_id=row["analysis_id"],
        destination_root=row["destination_root"],
        destination_selected=row["destination_selected"],
        destination_template=row["destination_template"],
        duplicate_policy=row["duplicate_policy"],
        target_platform=row["target_platform"],
        max_path_length=row["max_path_length"],
        policy_fp=row["policy_fingerprint"],
        source_snapshot=json.loads(row["source_snapshot_json"]),
        items=items,
        global_issues=global_issues,
    )
    return plan_digest_for_body(body)


def get_plan_row(store: Store, plan_id: str):
    row = store.conn.execute(
        "SELECT * FROM plans WHERE plan_id=?", (plan_id,)
    ).fetchone()
    if row is None:
        raise PlanError(f"Unknown plan: {plan_id}")
    return row


def get_plan_items(store: Store, plan_id: str) -> list[PlanItem]:
    return [
        PlanItem(
            item_id=row["item_id"],
            entry_id=row["entry_id"],
            source_id=row["source_id"],
            source_rel_path=row["source_rel_path"],
            source_root_identity=row["source_root_identity"],
            disposition=row["disposition"],
            destination_rel=row["destination_rel"],
            expected_sha256=row["expected_sha256"],
            size_bytes=row["size_bytes"],
            source_mtime_ns=row["source_mtime_ns"],
            canonical_entry_id=row["canonical_entry_id"],
            decision_source=row["decision_source"],
            reason=row["reason"],
        )
        for row in store.conn.execute(
            "SELECT * FROM plan_items WHERE plan_id=? ORDER BY item_id",
            (plan_id,),
        )
    ]


def build_plan(
    store: Store,
    config: ProjectConfig,
    policy: PolicyEngine,
    profile: TypeProfile,
    run_id: str,
    settings: PlanSettings,
) -> PlanResult:
    run = store.get_run(run_id)
    if run["status"] not in ("complete", "scanned"):
        raise PlanError(f"Run {run_id} is not complete enough to plan: {run['status']}")

    destination_selected = Path(os.path.abspath(os.fspath(settings.destination_root)))
    destination_root = canonical_path(destination_selected)
    target_platform = settings.target_platform or DestinationPolicy.for_host().target_platform
    destination_policy = DestinationPolicy(
        target_platform=target_platform,
        max_path_length=settings.max_path_length,
        max_component_length=settings.max_component_length,
    )
    source_roots = [source.path for source in config.sources]
    root_issues = validate_destination_root(
        destination_selected,
        destination_policy,
        source_roots=source_roots,
        workspace=config.workspace,
    )
    if root_issues:
        raise PlanError(
            "Invalid destination root: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in root_issues)
        )

    rows = load_files(store, run_id, profile)
    groups = build_duplicates(rows)
    roles = duplicate_role_map(groups)
    snapshot = _source_snapshot(store, run_id)
    fp = policy_fingerprint(config)
    analysis_id = analysis_id_for(run_id, fp)

    recommendations: list[tuple[Any, ...]] = []
    overrides = _active_overrides(store)
    items: list[PlanItem] = []
    copy_destinations: list[DestinationItem] = []
    issues: list[dict[str, Any]] = []

    for item_id, row in enumerate(rows, start=1):
        role, group_id = roles.get(row.entry_id, ("unique", ""))
        canonical_id = None
        if group_id:
            for group in groups:
                if group.group_id == group_id:
                    canonical_id = group.canonical.entry_id
                    break

        disposition, rule_id, rationale, confidence, decision_source = _recommendation(
            row,
            policy=policy,
            duplicate_role=role,
            duplicate_policy=settings.duplicate_policy,
            canonical_entry_id=canonical_id,
        )

        if row.hash_status == "deferred":
            disposition = "defer"
            rule_id = "hash-deferred"
            rationale = "Content hash deferred by policy"
            decision_source = "recommendation"
        elif row.hash_status != "ok":
            disposition = "conflict"
            rule_id = "hash-unavailable"
            rationale = f"Hash status is {row.hash_status!r}; cannot plan a copy"
            decision_source = "recommendation"
        elif row.digest is None and disposition == "copy":
            disposition = "conflict"
            rule_id = "digest-missing"
            rationale = "No SHA-256 digest is available for a planned copy"
            decision_source = "recommendation"

        # Preserve the pre-override recommendation for audit.
        recommended_disposition = disposition
        recommended_rule = rule_id
        recommended_rationale = rationale

        override = _matching_override(overrides, row.source_id, row.rel_path)
        if override is not None:
            disposition = override["disposition"]
            rule_id = override["override_id"]
            rationale = override["reason"]
            decision_source = (
                "file_override" if override["scope"] == "file" else "directory_override"
            )
            if disposition == "copy" and row.hash_status != "ok":
                disposition = "conflict"
                rationale = (
                    f"Override requested copy but hash status is {row.hash_status!r}"
                )

        destination_rel: str | None = None
        if disposition == "copy":
            try:
                rendered = _render_template(
                    settings.destination_template, row.source_id, row.rel_path
                )
                destination_rel = normalize_relative(rendered)
            except (ValueError, PlanError) as exc:
                disposition = "conflict"
                rationale = f"Destination template/path error: {exc}"
        elif override is not None and override["destination_rel"]:
            destination_rel = override["destination_rel"]

        items.append(
            PlanItem(
                item_id=item_id,
                entry_id=row.entry_id,
                source_id=row.source_id,
                source_rel_path=row.rel_path,
                source_root_identity=(
                    snapshot["identities"].get(row.source_id, "")
                    or snapshot["sources"].get(row.source_id, {}).get("root_identity", "")
                ),
                disposition=disposition,
                destination_rel=destination_rel,
                expected_sha256=row.digest,
                size_bytes=row.size_bytes,
                source_mtime_ns=row.modified_ns,
                canonical_entry_id=canonical_id,
                decision_source=decision_source,
                reason=rationale,
            )
        )
        recommendations.append(
            (
                analysis_id,
                row.entry_id,
                recommended_disposition,
                None,
                recommended_rule,
                recommended_rationale,
                confidence,
                role,
                group_id,
                canonical_id,
            )
        )
        if disposition == "copy" and destination_rel:
            copy_destinations.append(
                DestinationItem(item_id, destination_rel, row.size_bytes)
            )

    validation = validate_plan(
        destination_root,
        copy_destinations,
        destination_policy,
        source_roots=source_roots,
        workspace=config.workspace,
    )
    by_item = validation.by_item()
    updated: list[PlanItem] = []
    for item in items:
        item_issues = by_item.get(item.item_id, [])
        if item_issues:
            message = "; ".join(
                f"{issue.code}: {issue.message}" for issue in item_issues
            )
            updated.append(
                PlanItem(
                    item_id=item.item_id,
                    entry_id=item.entry_id,
                    source_id=item.source_id,
                    source_rel_path=item.source_rel_path,
                    source_root_identity=item.source_root_identity,
                    disposition="conflict",
                    destination_rel=item.destination_rel,
                    expected_sha256=item.expected_sha256,
                    size_bytes=item.size_bytes,
                    source_mtime_ns=item.source_mtime_ns,
                    canonical_entry_id=item.canonical_entry_id,
                    decision_source=item.decision_source,
                    reason=f"Destination validation failed: {message}",
                )
            )
        else:
            updated.append(item)
    items = updated

    global_issues = [
        {"code": issue.code, "message": issue.message}
        for issue in validation.issues
        if issue.item_id is None
    ]
    summary = _summary(items, global_issues)
    summary["review_fingerprint"] = review_fingerprint(overrides)
    body = _plan_body(
        source_run_id=run_id,
        analysis_id=analysis_id,
        destination_root=str(destination_root),
        destination_selected=str(destination_selected),
        destination_template=settings.destination_template,
        duplicate_policy=settings.duplicate_policy,
        target_platform=target_platform,
        max_path_length=settings.max_path_length,
        policy_fp=fp,
        source_snapshot=snapshot,
        items=items,
        global_issues=global_issues,
    )
    digest = plan_digest_for_body(body)
    plan_id = "plan-" + digest[:16]

    existing = store.conn.execute(
        "SELECT plan_id, plan_digest, summary_json FROM plans WHERE plan_digest=?",
        (digest,),
    ).fetchone()
    if existing is not None:
        return PlanResult(
            plan_id=existing["plan_id"],
            plan_digest=existing["plan_digest"],
            summary=json.loads(existing["summary_json"]),
            items=get_plan_items(store, existing["plan_id"]),
            issues=json.loads(existing["summary_json"]).get("global_issues", []),
            created=False,
        )

    store.conn.execute(
        """
        INSERT OR REPLACE INTO analysis_snapshots(
            analysis_id, source_run_id, created_at, tool_version,
            policy_fingerprint, metrics_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            analysis_id,
            run_id,
            utc_now_iso(),
            __version__,
            fp,
            json.dumps(summary, sort_keys=True),
        ),
    )
    store.conn.execute("DELETE FROM recommendations WHERE analysis_id=?", (analysis_id,))
    store.conn.executemany(
        """
        INSERT INTO recommendations(
            analysis_id, entry_id, disposition, destination_rel, rule_id,
            rationale, confidence, duplicate_role, duplicate_group,
            canonical_entry_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        recommendations,
    )
    store.conn.execute(
        """
        INSERT INTO plans(
            plan_id, plan_digest, plan_schema_version, source_run_id,
            analysis_id, destination_root, destination_selected,
            destination_template, duplicate_policy, target_platform,
            max_path_length, policy_fingerprint, source_snapshot_json,
            summary_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
        """,
        (
            plan_id,
            digest,
            PLAN_SCHEMA_VERSION,
            run_id,
            analysis_id,
            str(destination_root),
            str(destination_selected),
            settings.destination_template,
            settings.duplicate_policy,
            target_platform,
            settings.max_path_length,
            fp,
            json.dumps(snapshot, sort_keys=True),
            json.dumps(summary, sort_keys=True),
            utc_now_iso(),
        ),
    )
    store.conn.executemany(
        """
        INSERT INTO plan_items(
            plan_id, item_id, entry_id, source_id, source_rel_path,
            source_root_identity, disposition, destination_rel,
            expected_sha256, size_bytes, source_mtime_ns, canonical_entry_id,
            decision_source, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                plan_id,
                item.item_id,
                item.entry_id,
                item.source_id,
                item.source_rel_path,
                item.source_root_identity,
                item.disposition,
                item.destination_rel,
                item.expected_sha256,
                item.size_bytes,
                item.source_mtime_ns,
                item.canonical_entry_id,
                item.decision_source,
                item.reason,
            )
            for item in items
        ],
    )
    store.commit()
    return PlanResult(
        plan_id=plan_id,
        plan_digest=digest,
        summary=summary,
        items=items,
        issues=global_issues,
        created=True,
    )


def _summary(
    items: list[PlanItem], global_issues: list[dict[str, Any]]
) -> dict[str, Any]:
    counts = Counter(item.disposition for item in items)
    bytes_by = Counter()
    for item in items:
        bytes_by[item.disposition] += item.size_bytes
    return {
        "total_items": len(items),
        "counts": dict(counts),
        "bytes_by_disposition": dict(bytes_by),
        "copy_items": counts.get("copy", 0),
        "copy_bytes": bytes_by.get("copy", 0),
        "conflicts": counts.get("conflict", 0),
        "global_issues": global_issues,
        "by_source": dict(
            Counter(item.source_id for item in items if item.disposition == "copy")
        ),
    }


def add_override(
    store: Store,
    *,
    scope: str,
    source_id: str,
    target_rel: str,
    disposition: str,
    reason: str,
    destination_rel: str | None = None,
) -> str:
    if scope not in ("file", "directory"):
        raise PlanError("Override scope must be 'file' or 'directory'")
    if disposition not in OVERRIDE_DISPOSITIONS:
        raise PlanError(f"Override disposition must be one of {OVERRIDE_DISPOSITIONS}")
    if not reason.strip():
        raise PlanError("Override reason is required")
    if store.conn.execute(
        "SELECT 1 FROM sources WHERE source_id=?", (source_id,)
    ).fetchone() is None:
        raise PlanError(f"Unknown source: {source_id}")
    normalized = normalize_relative(target_rel)
    if destination_rel is not None:
        normalize_relative(destination_rel)
    identity = {
        "scope": scope,
        "source_id": source_id,
        "target_rel": normalized,
        "disposition": disposition,
        "destination_rel": destination_rel,
        "reason": reason,
    }
    override_id = "ovr-" + _sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    )[:12]
    store.conn.execute(
        """
        INSERT OR REPLACE INTO overrides(
            override_id, scope, source_id, target_rel, disposition,
            destination_rel, reason, active, created_at, superseded_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
        """,
        (
            override_id,
            scope,
            source_id,
            normalized,
            disposition,
            destination_rel,
            reason,
            utc_now_iso(),
        ),
    )
    store.commit()
    return override_id


def list_overrides(store: Store) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.conn.execute(
            """
            SELECT override_id, scope, source_id, target_rel, disposition,
                   destination_rel, reason, active, created_at, superseded_by
            FROM overrides WHERE active=1 ORDER BY created_at, override_id
            """
        )
    ]


def remove_override(store: Store, override_id: str) -> None:
    cursor = store.conn.execute(
        "UPDATE overrides SET active=0 WHERE override_id=? AND active=1",
        (override_id,),
    )
    if cursor.rowcount == 0:
        raise PlanError(f"Unknown or inactive override: {override_id}")
    store.commit()


def revalidate_sources(
    config: ProjectConfig, snapshot: dict[str, Any]
) -> list[str]:
    issues: list[str] = []
    for source in config.sources:
        expected = snapshot.get("fingerprints", {}).get(source.source_id)
        scanner = SourceScanner(source, config.scan)
        for _ in scanner.walk():
            pass
        actual = scanner.fingerprint.as_dict()
        if expected != actual:
            issues.append(
                f"{source.source_id}: source metadata changed since planning"
            )
        expected_identity = snapshot.get("identities", {}).get(source.source_id)
        if expected_identity and root_identity(source.path) != expected_identity:
            issues.append(
                f"{source.source_id}: source root identity changed since planning"
            )
    return issues


def authorize_plan(
    store: Store,
    config: ProjectConfig,
    plan_id: str,
    digest: str,
    *,
    actor: str = "operator",
    notes: str | None = None,
) -> str:
    row = get_plan_row(store, plan_id)
    if row["plan_digest"] != digest:
        raise PlanError("Plan digest does not match the requested digest")
    if recompute_plan_digest(store, plan_id) != digest:
        raise PlanError("Plan content no longer matches its digest")
    if row["status"] != "draft":
        raise PlanError(f"Plan is not draft (status={row['status']})")
    summary = json.loads(row["summary_json"])
    if summary.get("conflicts", 0):
        raise PlanError(
            f"Plan has {summary['conflicts']} conflict(s); resolve them before authorization"
        )
    if summary.get("global_issues"):
        raise PlanError("Plan has unresolved global validation issues")

    snapshot = json.loads(row["source_snapshot_json"])
    source_issues = revalidate_sources(config, snapshot)
    if source_issues:
        raise PlanError("; ".join(source_issues))

    policy = DestinationPolicy(
        target_platform=row["target_platform"],
        max_path_length=row["max_path_length"],
    )
    selected = row["destination_selected"] or row["destination_root"]
    root_issues = validate_destination_root(
        selected,
        policy,
        source_roots=[source.path for source in config.sources],
        workspace=config.workspace,
    )
    if root_issues:
        raise PlanError(
            "Destination is no longer valid: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in root_issues)
        )
    if os.path.normcase(str(canonical_path(selected))) != os.path.normcase(
        str(canonical_path(row["destination_root"]))
    ):
        raise PlanError(
            "Destination selected path no longer resolves to the planned root"
        )
    copy_items = [
        DestinationItem(item.item_id, item.destination_rel, item.size_bytes)
        for item in get_plan_items(store, plan_id)
        if item.disposition == "copy" and item.destination_rel
    ]
    validation = validate_plan(
        selected,
        copy_items,
        policy,
        source_roots=[source.path for source in config.sources],
        workspace=config.workspace,
    )
    if validation.issues:
        raise PlanError(
            "Destination changed since planning: "
            + "; ".join(
                f"{issue.code}: {issue.message}" for issue in validation.issues
            )
        )

    authorization_id = "auth-" + uuid.uuid4().hex[:16]
    store.conn.execute(
        """
        INSERT INTO plan_authorizations(
            authorization_id, plan_id, plan_digest, destination_root,
            authorized_at, authorized_by, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            authorization_id,
            plan_id,
            digest,
            row["destination_root"],
            utc_now_iso(),
            actor,
            notes,
        ),
    )
    store.conn.execute(
        "UPDATE plans SET status='authorized' WHERE plan_id=?", (plan_id,)
    )
    store.commit()
    return authorization_id


def get_authorization(store: Store, plan_id: str, digest: str):
    return store.conn.execute(
        """
        SELECT * FROM plan_authorizations
        WHERE plan_id=? AND plan_digest=?
        ORDER BY authorized_at DESC LIMIT 1
        """,
        (plan_id, digest),
    ).fetchone()


def plan_preview_lines(store: Store, plan_id: str, limit: int = 40) -> list[str]:
    row = get_plan_row(store, plan_id)
    items = get_plan_items(store, plan_id)
    summary = json.loads(row["summary_json"])
    lines = [
        f"plan: {row['plan_id']}",
        f"digest: {row['plan_digest']}",
        f"status: {row['status']}",
        f"destination: {row['destination_root']}",
        f"destination selected: {row['destination_selected']}",
        f"template: {row['destination_template']}",
        f"duplicate policy: {row['duplicate_policy']}",
        f"items: {summary.get('total_items', 0)}",
        f"copy: {summary.get('copy_items', 0)} ({summary.get('copy_bytes', 0)} bytes)",
        f"conflicts: {summary.get('conflicts', 0)}",
        f"global issues: {len(summary.get('global_issues', []))}",
    ]
    for issue in summary.get("global_issues", []):
        lines.append(f"  GLOBAL {issue['code']}: {issue['message']}")
    lines.append("mappings:")
    for item in items[:limit]:
        if item.destination_rel:
            lines.append(
                f"  {item.disposition:14} {item.source_id}:{item.source_rel_path}"
                f" -> {item.destination_rel}"
            )
        else:
            lines.append(
                f"  {item.disposition:14} {item.source_id}:{item.source_rel_path}"
                f" ({item.reason})"
            )
    if len(items) > limit:
        lines.append(f"  ... {len(items) - limit} more items")
    return lines


def export_plan(store: Store, plan_id: str, out_dir: str | Path) -> tuple[Path, Path]:
    row = get_plan_row(store, plan_id)
    items = get_plan_items(store, plan_id)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / f"{plan_id}.json"
    items_path = out / f"{plan_id}-items.ndjson"
    manifest = {
        "plan_schema_version": row["plan_schema_version"],
        "plan_id": row["plan_id"],
        "plan_digest": row["plan_digest"],
        "source_run_id": row["source_run_id"],
        "analysis_id": row["analysis_id"],
        "destination_root": row["destination_root"],
        "destination_selected": row["destination_selected"],
        "destination_template": row["destination_template"],
        "duplicate_policy": row["duplicate_policy"],
        "target_platform": row["target_platform"],
        "max_path_length": row["max_path_length"],
        "policy_fingerprint": row["policy_fingerprint"],
        "status": row["status"],
        "summary": json.loads(row["summary_json"]),
        "source_snapshot": json.loads(row["source_snapshot_json"]),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with items_path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(
                json.dumps(
                    {
                        "item_id": item.item_id,
                        "entry_id": item.entry_id,
                        "source_id": item.source_id,
                        "source_rel_path": item.source_rel_path,
                        "source_root_identity": item.source_root_identity,
                        "disposition": item.disposition,
                        "destination_rel": item.destination_rel,
                        "expected_sha256": item.expected_sha256,
                        "size_bytes": item.size_bytes,
                        "source_mtime_ns": item.source_mtime_ns,
                        "canonical_entry_id": item.canonical_entry_id,
                        "decision_source": item.decision_source,
                        "reason": item.reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return manifest_path, items_path
