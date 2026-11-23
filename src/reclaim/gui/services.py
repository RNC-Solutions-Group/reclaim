"""M2 application services for the PySide6 desktop shell.

The GUI talks to this module, not to SQLite or the filesystem directly.  Every
long-running operation accepts a progress callback and a cancellation event so
the presentation layer can stay responsive.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterator

from ..analysis import load_dirs, load_files
from ..classify import PolicyEngine, TypeProfile, load_policy, load_type_profile
from ..config import (
    ProjectConfig,
    ensure_workspace,
    load_config,
    validate_config,
)
from ..destination import DestinationPolicy, validate_destination_root
from ..errors import ReclaimError
from ..execution import execute_plan, reconcile_execution, verify_execution
from ..hashing import hash_file
from ..paths import canonical_path
from ..planning import (
    PlanItem,
    PlanSettings,
    add_override,
    authorize_plan,
    build_plan,
    export_plan,
    get_plan_items,
    get_plan_row,
    list_overrides,
    policy_fingerprint,
    remove_override,
    review_fingerprint,
)
from ..report import build_all
from ..scanner import SourceScanner
from ..store import Store

Progress = Callable[[dict[str, Any]], None]


class GuiError(ReclaimError):
    """A user-actionable desktop/application-service error."""


@dataclass(frozen=True)
class SourceView:
    source_id: str
    label: str
    path: Path
    priority: int
    available: bool
    error: str = ""


@dataclass(frozen=True)
class DestinationStatus:
    path: Path | None
    exists: bool
    free_bytes: int | None
    issues: list[str]
    target_platform: str


@dataclass(frozen=True)
class ReviewRow:
    entry_id: int
    source_id: str
    source_label: str
    rel_path: str
    size_bytes: int
    kind: str
    hash_status: str
    recommended: str
    recommended_plan: str
    effective_plan: str
    decision_source: str
    override_id: str
    override_reason: str
    duplicate_role: str
    duplicate_group: str
    rationale: str


@dataclass
class AnalysisBundle:
    rows: list[ReviewRow]
    duplicate_groups: int
    duplicate_occurrences: int
    metrics: dict[str, Any]
    run_id: str


@dataclass
class PlanSnapshot:
    plan_id: str
    digest: str
    status: str
    summary: dict[str, Any]
    items: list[PlanItem]
    issues: list[dict[str, Any]]
    destination_root: str
    destination_selected: str
    destination_template: str
    duplicate_policy: str
    target_platform: str
    max_path_length: int
    source_run_id: str
    analysis_id: str
    policy_fingerprint: str
    review_fingerprint: str
    authorized: bool
    manifest_path: str = ""
    items_path: str = ""


@dataclass
class ExecutionSnapshot:
    execution_id: str
    plan_id: str
    status: str
    destination_root: str
    summary: dict[str, Any]
    counts: dict[str, int]
    started_at: str
    finished_at: str


@dataclass
class Reconciliation:
    summary: dict[str, Any]
    records: list[dict[str, str]]


@dataclass
class _GuiSettings:
    destination: str = ""
    destination_template: str = "{source_id}/{rel_path}"
    duplicate_policy: str = "prefer_one"
    target_platform: str = ""
    max_path_length: int = 240


SETTINGS_NAME = "gui-settings.json"


def _settings_path(config: ProjectConfig) -> Path:
    return config.workspace / SETTINGS_NAME


def _load_gui_settings(config: ProjectConfig) -> _GuiSettings:
    path = _settings_path(config)
    if not path.is_file():
        return _GuiSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _GuiSettings()
    return _GuiSettings(
        destination=str(raw.get("destination", "")),
        destination_template=str(
            raw.get("destination_template", "{source_id}/{rel_path}")
        ),
        duplicate_policy=str(raw.get("duplicate_policy", "prefer_one")),
        target_platform=str(raw.get("target_platform", "")),
        max_path_length=int(raw.get("max_path_length", 240)),
    )


def _save_gui_settings(config: ProjectConfig, settings: _GuiSettings) -> None:
    config.workspace.mkdir(parents=True, exist_ok=True)
    _settings_path(config).write_text(
        json.dumps(
            {
                "destination": settings.destination,
                "destination_template": settings.destination_template,
                "duplicate_policy": settings.duplicate_policy,
                "target_platform": settings.target_platform,
                "max_path_length": settings.max_path_length,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _plan_disposition(recommended: str) -> str:
    if recommended == "retain":
        return "copy"
    if recommended == "duplicate":
        return "duplicate_omit"
    if recommended == "exclude":
        return "exclude"
    if recommended == "defer_media":
        return "defer"
    return "conflict"


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
        return exact[-1]
    directories = [
        row
        for row in overrides
        if row["scope"] == "directory"
        and row["source_id"] == source_id
        and (rel_path == row["target_rel"] or rel_path.startswith(row["target_rel"] + "/"))
    ]
    if not directories:
        return None
    return max(directories, key=lambda row: len(row["target_rel"]))


def _profile(config: ProjectConfig) -> tuple[PolicyEngine, TypeProfile]:
    engine, profile = load_policy(config.policy_file, config.policy_overlay_file)
    if config.types_file:
        profile = load_type_profile(config.types_file)
    return engine, profile


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def _selected_path(value: str | Path) -> Path:
    """Preserve the operator's path spelling; do not resolve links."""

    return Path(os.path.abspath(Path(value).expanduser()))


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class ProjectSession:
    """Owns one open Reclaim project and its application-level operations."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.store = Store(config.database_path)
        self.policy, self.profile = _profile(config)
        self.settings = _load_gui_settings(config)
        self.run_id: str | None = None
        self.plan_stale = False
        self.last_execution_id = ""
        self.refresh()
        latest = self.latest_plan()
        if latest is not None:
            self.plan_stale = not self.plan_is_current(latest)

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        project_dir: str | Path,
        project_name: str,
        sources: list[tuple[str, str | Path]],
        destination: str | Path,
        destination_template: str = "{source_id}/{rel_path}",
        duplicate_policy: str = "prefer_one",
        target_platform: str = "",
        max_path_length: int = 240,
    ) -> ProjectSession:
        base = Path(project_dir).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        config_path = base / "reclaim-project.toml"
        if config_path.exists():
            raise GuiError(f"Project config already exists: {config_path}")
        if not project_name.strip():
            raise GuiError("Project name is required")
        if not sources:
            raise GuiError("At least one source directory is required")
        destination_path = _selected_path(destination)
        if not destination_path.is_dir():
            raise GuiError(f"Destination directory does not exist: {destination_path}")

        source_items: list[tuple[str, str, Path]] = []
        for index, (label, raw_path) in enumerate(sources, start=1):
            path = Path(raw_path).expanduser().resolve()
            if not path.is_dir():
                raise GuiError(f"Source directory does not exist: {path}")
            source_id = f"source-{index}"
            source_items.append((source_id, label.strip() or f"Source {index}", path))

        lines = [
            "[project]",
            f"name = {_toml_string(project_name.strip())}",
            'workspace = "workspace"',
            'database = "db/inventory.sqlite3"',
            'reports_dir = "reports"',
            "",
            "[scan]",
            "include_hidden = true",
            "follow_reparse_points = false",
            "hash_media_libraries = false",
            "chunk_bytes = 8388608",
            "",
        ]
        for source_id, label, path in source_items:
            lines.extend(
                [
                    "[[sources]]",
                    f"id = {_toml_string(source_id)}",
                    f"label = {_toml_string(label)}",
                    f"path = {_toml_string(str(path))}",
                    "priority = 100",
                    "",
                ]
            )
        config_path.write_text("\n".join(lines), encoding="utf-8")
        try:
            config = load_config(config_path)
            validate_config(config)
            ensure_workspace(config)
        except Exception:
            config_path.unlink(missing_ok=True)
            raise
        session = cls(config)
        session.settings = _GuiSettings(
            destination=str(destination_path),
            destination_template=destination_template,
            duplicate_policy=duplicate_policy,
            target_platform=target_platform,
            max_path_length=max_path_length,
        )
        destination_issues = session.destination_status().issues
        if destination_issues:
            session.close()
            config_path.unlink(missing_ok=True)
            raise GuiError("; ".join(destination_issues))
        _save_gui_settings(config, session.settings)
        return session

    @classmethod
    def open(cls, config_path: str | Path) -> ProjectSession:
        config = load_config(config_path)
        validate_config(config)
        ensure_workspace(config)
        return cls(config)

    def close(self) -> None:
        self.store.close()

    def refresh(self) -> None:
        latest = self.store.latest_run()
        self.run_id = str(latest["run_id"]) if latest else None

    def run_summary(self) -> dict[str, Any] | None:
        if not self.run_id:
            return None
        try:
            run = self.store.get_run(self.run_id)
            counts = self.store.counts(self.run_id)
        except ReclaimError:
            return None
        return {
            "run_id": self.run_id,
            "status": str(run["status"]),
            "started_at": str(run["started_at"]),
            "finished_at": str(run["finished_at"] or ""),
            "counts": counts,
        }

    # -- project/source/destination ---------------------------------------

    @property
    def project_name(self) -> str:
        return self.config.project_name

    @property
    def destination(self) -> Path | None:
        if not self.settings.destination:
            return None
        return Path(self.settings.destination)

    @property
    def destination_identity(self) -> Path | None:
        """Canonical destination identity, when a selected path exists."""

        destination = self.destination
        return canonical_path(destination) if destination is not None else None

    def source_views(self) -> list[SourceView]:
        output: list[SourceView] = []
        for source in self.config.sources:
            available = source.path.is_dir()
            output.append(
                SourceView(
                    source_id=source.source_id,
                    label=source.label,
                    path=source.path,
                    priority=source.priority,
                    available=available,
                    error="" if available else "Source is unavailable or is not a directory",
                )
            )
        return output

    def destination_status(self) -> DestinationStatus:
        destination = self.destination
        if destination is None:
            return DestinationStatus(
                None,
                False,
                None,
                ["No destination selected"],
                DestinationPolicy.for_host().target_platform,
            )
        platform = (
            self.settings.target_platform
            or DestinationPolicy.for_host().target_platform
        )
        policy = DestinationPolicy(
            target_platform=platform,
            max_path_length=self.settings.max_path_length,
        )
        issues = [
            f"{issue.code}: {issue.message}"
            for issue in validate_destination_root(
                destination,
                policy,
                source_roots=[source.path for source in self.config.sources],
                workspace=self.config.workspace,
            )
        ]
        free: int | None = None
        try:
            if destination.is_dir():
                free = shutil.disk_usage(destination).free
        except OSError:
            free = None
        return DestinationStatus(
            destination,
            destination.is_dir(),
            free,
            issues,
            platform,
        )

    def set_destination(self, destination: str | Path) -> DestinationStatus:
        path = _selected_path(destination)
        previous = self.settings
        self.settings = _GuiSettings(
            destination=str(path),
            destination_template=self.settings.destination_template,
            duplicate_policy=self.settings.duplicate_policy,
            target_platform=self.settings.target_platform,
            max_path_length=self.settings.max_path_length,
        )
        status = self.destination_status()
        if status.issues:
            self.settings = previous
            raise GuiError("; ".join(status.issues))
        _save_gui_settings(self.config, self.settings)
        self.plan_stale = True
        return status

    def update_plan_settings(
        self,
        *,
        template: str,
        duplicate_policy: str,
        target_platform: str,
        max_path_length: int,
    ) -> None:
        if not template.strip():
            raise GuiError("Destination template is required")
        if duplicate_policy not in ("prefer_one", "keep_all", "fail"):
            raise GuiError("Invalid duplicate policy")
        self.settings = _GuiSettings(
            destination=self.settings.destination,
            destination_template=template.strip(),
            duplicate_policy=duplicate_policy,
            target_platform=target_platform,
            max_path_length=max_path_length,
        )
        _save_gui_settings(self.config, self.settings)
        self.plan_stale = True

    # -- worker store ------------------------------------------------------

    @contextmanager
    def _open_store(self) -> Iterator[Store]:
        store = Store(self.config.database_path)
        try:
            for source in self.config.sources:
                store.upsert_source(
                    source.source_id,
                    source.label,
                    str(source.path),
                    source.priority,
                )
            store.commit()
            yield store
        finally:
            store.close()

    # -- inventory/hash ----------------------------------------------------

    def scan(
        self,
        progress: Progress | None = None,
        stop: Event | None = None,
    ) -> dict[str, Any]:
        validate_config(self.config)
        with self._open_store() as store:
            run_id = store.start_run(
                self.config.fingerprint(), store.source_identities()
            )
            fingerprints: dict[str, dict[str, object]] = {}
            total_entries = 0
            cancelled = False
            for source in self.config.sources:
                if stop is not None and stop.is_set():
                    cancelled = True
                    break
                scanner = SourceScanner(source, self.config.scan)
                buffer = []
                inserted = 0
                for record in scanner.walk():
                    if stop is not None and stop.is_set():
                        cancelled = True
                        break
                    if record.error_code:
                        store.add_scan_error(
                            run_id,
                            record.source_id,
                            record.rel_path,
                            "scan",
                            record.error_code,
                            record.error_message or "",
                        )
                    buffer.append(record)
                    if len(buffer) >= 1000:
                        inserted += store.insert_entries(run_id, buffer)
                        total_entries += len(buffer)
                        buffer.clear()
                        if progress:
                            progress(
                                {
                                    "phase": "scan",
                                    "run_id": run_id,
                                    "source_id": source.source_id,
                                    "entries": total_entries,
                                    "message": f"Scanning {source.label}",
                                }
                            )
                if buffer:
                    inserted += store.insert_entries(run_id, buffer)
                    total_entries += len(buffer)
                fingerprints[source.source_id] = scanner.fingerprint.as_dict()
                if progress:
                    progress(
                        {
                            "phase": "scan",
                            "run_id": run_id,
                            "source_id": source.source_id,
                            "entries": total_entries,
                            "message": f"Scanned {source.label}: {inserted:,} entries",
                        }
                    )
                if cancelled:
                    break
            store.seed_hashes(
                run_id,
                hash_media_libraries=self.config.scan.hash_media_libraries,
            )
            store.finish_run(
                run_id,
                status="partial" if cancelled else "scanned",
                fingerprints=fingerprints,
                notes="Scan cancelled by desktop operator" if cancelled else None,
            )
            counts = store.counts(run_id)
        self.run_id = run_id
        if progress:
            progress(
                {
                    "phase": "scan",
                    "message": "Scan cancelled" if cancelled else "Scan complete",
                    "counts": counts,
                }
            )
        return {
            "run_id": run_id,
            "status": "partial" if cancelled else "scanned",
            "counts": counts,
        }

    def hash(
        self,
        progress: Progress | None = None,
        stop: Event | None = None,
    ) -> dict[str, Any]:
        if not self.run_id:
            raise GuiError("Run a scan before hashing")
        with self._open_store() as store:
            run_id = self.run_id
            targets = list(store.hash_targets(run_id))
            total = len(targets)
            errors = 0
            cancelled = False
            processed = 0
            for index, target in enumerate(targets, start=1):
                if stop is not None and stop.is_set():
                    cancelled = True
                    break
                processed = index
                source = self.config.source_by_id(target.source_id)
                path = source.path.joinpath(*target.rel_path.split("/"))
                outcome = hash_file(
                    path,
                    expected_size=target.size_bytes,
                    expected_mtime_ns=target.modified_ns,
                    chunk_bytes=self.config.scan.chunk_bytes,
                )
                store.save_hash(target, outcome)
                if outcome.status != "ok":
                    errors += 1
                if index % 100 == 0:
                    store.commit()
                if progress and (index == 1 or index % 25 == 0 or index == total):
                    progress(
                        {
                            "phase": "hash",
                            "processed": index,
                            "total": total,
                            "errors": errors,
                            "message": f"Hashing {index:,}/{total:,}",
                        }
                    )
            store.commit()
            counts = store.counts(run_id)
            status = (
                "complete"
                if counts["hash_pending"] == 0 and counts["hash_errors"] == 0
                else "partial"
            )
            if cancelled:
                status = "partial"
            store.set_run_status(run_id, status)
        if progress:
            progress(
                {
                    "phase": "hash",
                    "message": "Hash cancelled" if cancelled else "Hashing complete",
                }
            )
        return {
            "run_id": run_id,
            "status": status,
            "processed": processed,
            "total": total,
            "errors": errors,
            "counts": counts,
        }

    # -- analysis/review --------------------------------------------------

    def analyze(self) -> AnalysisBundle:
        if not self.run_id:
            raise GuiError("Run a scan and hash before reviewing")
        with self._open_store() as store:
            rows = load_files(store, self.run_id, self.profile)
            dirs = load_dirs(store, self.run_id)
            groups, dispositions, metrics = build_all(
                store,
                self.run_id,
                rows,
                dirs,
                self.profile,
                self.policy,
            )
            overrides = list_overrides(store)
        roles = {
            row.entry_id: (item.duplicate_role, item.duplicate_group)
            for row, item in zip(rows, dispositions)
        }
        review: list[ReviewRow] = []
        for row, disposition in zip(rows, dispositions):
            override = _matching_override(overrides, row.source_id, row.rel_path)
            effective = _plan_disposition(disposition.recommended)
            source = "recommendation"
            override_id = ""
            override_reason = ""
            if override is not None:
                effective = override["disposition"]
                source = f"{override['scope']}_override"
                override_id = override["override_id"]
                override_reason = override["reason"]
                if effective == "copy" and row.hash_status != "ok":
                    effective = "conflict"
            role, group_id = roles.get(row.entry_id, ("unique", ""))
            review.append(
                ReviewRow(
                    entry_id=row.entry_id,
                    source_id=row.source_id,
                    source_label=row.source_label,
                    rel_path=row.rel_path,
                    size_bytes=row.size_bytes,
                    kind=row.kind,
                    hash_status=row.hash_status,
                    recommended=disposition.recommended,
                    recommended_plan=_plan_disposition(disposition.recommended),
                    effective_plan=effective,
                    decision_source=source,
                    override_id=override_id,
                    override_reason=override_reason,
                    duplicate_role=role,
                    duplicate_group=group_id,
                    rationale=disposition.rationale,
                )
            )
        occurrences = sum(len(group.occurrences) for group in groups)
        return AnalysisBundle(
            rows=review,
            duplicate_groups=len(groups),
            duplicate_occurrences=occurrences,
            metrics=metrics,
            run_id=self.run_id,
        )

    def apply_overrides(
        self,
        rows: list[ReviewRow],
        disposition: str,
        reason: str,
    ) -> list[str]:
        if not rows:
            raise GuiError("Select at least one file")
        if disposition not in ("copy", "exclude", "defer", "duplicate_omit"):
            raise GuiError("Unsupported disposition")
        if not reason.strip():
            raise GuiError("A reason is required for each override")
        ids: list[str] = []
        for row in rows:
            ids.append(
                add_override(
                    self.store,
                    scope="file",
                    source_id=row.source_id,
                    target_rel=row.rel_path,
                    disposition=disposition,
                    reason=reason.strip(),
                )
            )
        self.store.commit()
        self.plan_stale = True
        return ids

    def remove_overrides(self, rows: list[ReviewRow]) -> int:
        removed = 0
        active = {row["override_id"]: row for row in list_overrides(self.store)}
        for row in rows:
            override = active.get(row.override_id)
            if override and override["scope"] == "file":
                remove_override(self.store, row.override_id)
                removed += 1
        if removed:
            self.plan_stale = True
        return removed

    def active_overrides(self) -> list[dict[str, Any]]:
        return list_overrides(self.store)

    # -- plans -------------------------------------------------------------

    def build_plan(
        self,
        progress: Progress | None = None,
        stop: Event | None = None,
    ) -> PlanSnapshot:
        if not self.run_id:
            raise GuiError("Run a scan before generating a plan")
        destination = self.destination
        if destination is None:
            raise GuiError("Select a destination before generating a plan")
        settings = PlanSettings(
            destination_root=destination,
            destination_template=self.settings.destination_template,
            duplicate_policy=self.settings.duplicate_policy,
            target_platform=self.settings.target_platform or None,
            max_path_length=self.settings.max_path_length,
        )
        if progress:
            progress({"phase": "plan", "message": "Building immutable plan"})
        with self._open_store() as store:
            result = build_plan(
                store,
                self.config,
                self.policy,
                self.profile,
                self.run_id,
                settings,
            )
            manifest, items_path = export_plan(
                store, result.plan_id, self.config.workspace / "plans"
            )
            snapshot = self._plan_snapshot(store, result.plan_id)
        snapshot.manifest_path = str(manifest)
        snapshot.items_path = str(items_path)
        self.plan_stale = False
        if progress:
            progress(
                {
                    "phase": "plan",
                    "message": "Plan ready for review",
                    "plan_id": snapshot.plan_id,
                }
            )
        return snapshot

    def _plan_snapshot(self, store: Store, plan_id: str) -> PlanSnapshot:
        row = get_plan_row(store, plan_id)
        items = get_plan_items(store, plan_id)
        summary = json.loads(row["summary_json"])
        authorized = (
            store.conn.execute(
                "SELECT 1 FROM plan_authorizations WHERE plan_id=? AND plan_digest=? LIMIT 1",
                (plan_id, row["plan_digest"]),
            ).fetchone()
            is not None
        )
        issues = list(summary.get("global_issues", []))
        for item in items:
            if item.disposition == "conflict":
                issues.append(
                    {
                        "code": "ITEM_CONFLICT",
                        "message": f"{item.source_id}:{item.source_rel_path}: {item.reason}",
                        "item_id": item.item_id,
                    }
                )
        return PlanSnapshot(
            plan_id=row["plan_id"],
            digest=row["plan_digest"],
            status=row["status"],
            summary=summary,
            items=items,
            issues=issues,
            destination_root=row["destination_root"],
            destination_selected=row["destination_selected"],
            destination_template=row["destination_template"],
            duplicate_policy=row["duplicate_policy"],
            target_platform=row["target_platform"],
            max_path_length=row["max_path_length"],
            source_run_id=row["source_run_id"],
            analysis_id=row["analysis_id"],
            policy_fingerprint=row["policy_fingerprint"],
            review_fingerprint=str(summary.get("review_fingerprint", "")),
            authorized=authorized,
        )

    def latest_plan(self) -> PlanSnapshot | None:
        row = self.store.conn.execute(
            "SELECT plan_id FROM plans ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self._plan_snapshot(self.store, row["plan_id"])

    def plan_is_current(
        self,
        plan: PlanSnapshot,
        *,
        destination: str | Path | None = None,
        destination_template: str | None = None,
        duplicate_policy: str | None = None,
        target_platform: str | None = None,
        max_path_length: int | None = None,
        overrides: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Check the loaded plan against the current effective configuration.

        Historical plans remain inspectable even when this returns False; the
        desktop disables execution until a matching plan is authorized.
        """

        if not self.run_id or plan.source_run_id != self.run_id:
            return False
        selected = (
            _selected_path(destination)
            if destination is not None
            else self.destination
        )
        if selected is None:
            return False
        if not selected.is_dir():
            return False
        if os.path.normcase(str(selected)) != os.path.normcase(
            str(Path(plan.destination_selected))
        ):
            return False
        if os.path.normcase(str(canonical_path(selected))) != os.path.normcase(
            str(canonical_path(plan.destination_root))
        ):
            return False
        current_template = (
            destination_template
            if destination_template is not None
            else self.settings.destination_template
        )
        if current_template != plan.destination_template:
            return False
        current_policy = (
            duplicate_policy
            if duplicate_policy is not None
            else self.settings.duplicate_policy
        )
        if current_policy != plan.duplicate_policy:
            return False
        current_platform = (
            target_platform
            if target_platform is not None
            else self.settings.target_platform
        ) or DestinationPolicy.for_host().target_platform
        if current_platform != plan.target_platform:
            return False
        current_max = (
            max_path_length
            if max_path_length is not None
            else self.settings.max_path_length
        )
        if current_max != plan.max_path_length:
            return False
        if plan.policy_fingerprint != policy_fingerprint(self.config):
            return False
        current_overrides = (
            overrides if overrides is not None else list_overrides(self.store)
        )
        return plan.review_fingerprint == review_fingerprint(current_overrides)

    def list_plans(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.store.conn.execute(
                "SELECT plan_id, plan_digest, status, created_at, summary_json "
                "FROM plans ORDER BY rowid DESC"
            )
        ]

    def authorize(self, plan: PlanSnapshot, notes: str = "") -> str:
        authorization_id = authorize_plan(
            self.store,
            self.config,
            plan.plan_id,
            plan.digest,
            actor="desktop-operator",
            notes=notes or None,
        )
        self.plan_stale = False
        return authorization_id

    # -- execution ---------------------------------------------------------

    def execute(
        self,
        plan: PlanSnapshot,
        progress: Progress | None = None,
        stop: Event | None = None,
    ):
        if progress:
            progress({"phase": "execute", "message": "Starting execution", "processed": 0})
        with self._open_store() as store:
            result = execute_plan(
                store,
                self.config,
                plan.plan_id,
                plan.digest,
                progress=progress,
                stop_event=stop,
            )
            snapshot = (
                self._execution_snapshot(store, result.execution_id)
                if result.execution_id
                else None
            )
        self.last_execution_id = result.execution_id
        return result, snapshot

    def _execution_snapshot(self, store: Store, execution_id: str) -> ExecutionSnapshot:
        row = store.conn.execute(
            "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
        ).fetchone()
        if row is None:
            raise GuiError(f"Unknown execution: {execution_id}")
        counts = {
            "verified": 0,
            "failed": 0,
            "conflict": 0,
            "pending": 0,
        }
        for item in store.conn.execute(
            "SELECT state, COUNT(*) AS n FROM execution_items "
            "WHERE execution_id=? GROUP BY state",
            (execution_id,),
        ):
            counts[str(item["state"])] = int(item["n"])
        return ExecutionSnapshot(
            execution_id=row["execution_id"],
            plan_id=row["plan_id"],
            status=row["status"],
            destination_root=row["destination_root"],
            summary=json.loads(row["summary_json"] or "{}"),
            counts=counts,
            started_at=row["started_at"],
            finished_at=row["finished_at"] or "",
        )

    def latest_execution(self, plan_id: str | None = None) -> ExecutionSnapshot | None:
        if plan_id:
            row = self.store.conn.execute(
                "SELECT execution_id FROM executions WHERE plan_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        else:
            row = self.store.conn.execute(
                "SELECT execution_id FROM executions ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return self._execution_snapshot(self.store, row["execution_id"]) if row else None

    def execution_snapshot(self, execution_id: str) -> ExecutionSnapshot:
        return self._execution_snapshot(self.store, execution_id)

    def verify(
        self,
        execution_id: str,
        progress: Progress | None = None,
        stop: Event | None = None,
    ) -> dict[str, Any]:
        if progress:
            progress({"phase": "verify", "message": "Verifying destination copies"})
        with self._open_store() as store:
            result = verify_execution(store, self.config, execution_id)
        if progress:
            progress({"phase": "verify", "message": result.get("status", "Verification complete")})
        return result

    def reconcile(
        self,
        execution_id: str,
        progress: Progress | None = None,
        stop: Event | None = None,
    ) -> Reconciliation:
        if progress:
            progress({"phase": "reconcile", "message": "Writing reconciliation report"})
        with self._open_store() as store:
            summary = reconcile_execution(
                store, execution_id, self.config.workspace / "reconciliation"
            )
        records: list[dict[str, str]] = []
        try:
            with open(summary["csv"], "r", encoding="utf-8-sig", newline="") as handle:
                records = list(csv.DictReader(handle))
        except (OSError, KeyError, TypeError):
            records = []
        for record in records:
            record["source"] = (
                f"{record.get('source_id', '')}:{record.get('source_rel_path', '')}"
            )
            record["detail"] = " / ".join(
                part
                for part in (
                    record.get("error_code", ""),
                    record.get("error_message", ""),
                )
                if part
            )
        if progress:
            progress({"phase": "reconcile", "message": "Reconciliation complete"})
        return Reconciliation(summary=summary, records=records)

    # -- formatting helpers ------------------------------------------------

    @staticmethod
    def format_bytes(value: int | None) -> str:
        return "Unknown" if value is None else _format_bytes(value)
