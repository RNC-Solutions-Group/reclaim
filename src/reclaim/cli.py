"""Command-line interface for the CP1 read-only inventory engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from importlib import resources
from pathlib import Path

from . import __version__
from .analysis import load_dirs, load_files
from .classify import TypeProfile, load_policy, load_type_profile
from .config import ProjectConfig, ensure_workspace, load_config
from .errors import ReclaimError
from .hashing import hash_file, verify_file
from .paths import platform_note
from .report import (
    build_all,
    write_dispositions,
    write_duplicates,
    write_errors,
    write_extensions,
    write_filetypes,
    write_inventory,
    write_manifest,
    write_metrics,
    write_signature_mismatches,
)
from .scanner import SourceFingerprint, SourceScanner
from .store import Store
from .planning import (
    PlanSettings,
    add_override,
    authorize_plan,
    build_plan,
    export_plan,
    list_overrides,
    plan_preview_lines,
    remove_override,
)
from .execution import (
    execute_plan,
    reconcile_execution,
    verify_execution,
)
from .evidence.contracts import ExtractionLimits
from .evidence.registry import EvidenceSettings
from .evidence.service import EvidenceService

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_EXECUTION_FAILURE = 3
EXIT_INCOMPLETE = 4
EXIT_VERIFICATION_FAILURE = 5
EXIT_RECONCILIATION_FAILURE = 6
EXIT_EVIDENCE_FAILURE = 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reclaim",
        description="Read-only multi-source inventory, deduplication, and analysis.",
    )
    parser.add_argument("--version", action="version", version=f"reclaim {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="Validate configuration and create the project store")
    init.add_argument("--config", required=True)
    init.set_defaults(func=command_init)

    scan = subcommands.add_parser("scan", help="Enumerate registered sources (metadata only)")
    scan.add_argument("--config", required=True)
    scan.set_defaults(func=command_scan)

    hash_cmd = subcommands.add_parser("hash", help="Hash pending files and verify source metadata consistency")
    hash_cmd.add_argument("--config", required=True)
    hash_cmd.add_argument("--run", default=None, help="Scan run id (default: config run or latest)")
    hash_cmd.add_argument("--retry-errors", action="store_true")
    hash_cmd.add_argument("--limit", type=int, default=None)
    hash_cmd.add_argument("--no-verify", action="store_true", help="Skip post-hash source metadata consistency check")
    hash_cmd.set_defaults(func=command_hash)

    export = subcommands.add_parser("export", help="Write private analysis reports")
    export.add_argument("--config", required=True)
    export.add_argument("--run", default=None)
    export.add_argument("--out", default=None)
    export.set_defaults(func=command_export)

    verify = subcommands.add_parser("verify", help="Re-check source metadata consistency and sample hashes")
    verify.add_argument("--config", required=True)
    verify.add_argument("--run", default=None)
    verify.add_argument("--sample", type=int, default=25)
    verify.set_defaults(func=command_verify)

    status = subcommands.add_parser("status", help="Show inventory and hashing progress")
    status.add_argument("--config", required=True)
    status.add_argument("--run", default=None)
    status.set_defaults(func=command_status)

    plan = subcommands.add_parser("plan", help="Build an immutable migration plan (no destination writes)")
    plan.add_argument("--config", required=True)
    plan.add_argument("--run", default=None)
    plan.add_argument("--destination", required=True, help="Existing destination root")
    plan.add_argument("--template", default="{source_id}/{rel_path}")
    plan.add_argument("--duplicate-policy", choices=("prefer_one", "keep_all", "fail"), default="prefer_one")
    plan.add_argument("--target-platform", choices=("windows", "macos", "linux"), default=None)
    plan.add_argument("--max-path-length", type=int, default=240)
    plan.add_argument("--out", default=None, help="Plan export directory (default: workspace/plans)")
    plan.set_defaults(func=command_plan)

    preview = subcommands.add_parser("preview", help="Preview a migration plan without writing the destination")
    preview.add_argument("--config", required=True)
    preview.add_argument("--plan", required=True)
    preview.add_argument("--limit", type=int, default=40)
    preview.set_defaults(func=command_preview)

    authorize = subcommands.add_parser("authorize", help="Authorize an exact plan digest")
    authorize.add_argument("--config", required=True)
    authorize.add_argument("--plan", required=True)
    authorize.add_argument("--digest", required=True)
    authorize.add_argument("--actor", default="operator")
    authorize.add_argument("--note", default=None)
    authorize.set_defaults(func=command_authorize)

    execute = subcommands.add_parser("execute", help="Execute an authorized plan, or dry-run it")
    execute.add_argument("--config", required=True)
    execute.add_argument("--plan", required=True)
    execute.add_argument("--digest", required=True)
    execute.add_argument("--dry-run", action="store_true")
    execute.add_argument("--max-items", type=int, default=None, help="Stop after N items (testing/resume)")
    execute.add_argument("--no-retry-failed", action="store_true")
    execute.set_defaults(func=command_execute)

    verify_exec = subcommands.add_parser("verify-execution", help="Re-hash destination files for an execution")
    verify_exec.add_argument("--config", required=True)
    verify_exec.add_argument("--execution", required=True)
    verify_exec.set_defaults(func=command_verify_execution)

    reconcile = subcommands.add_parser("reconcile", help="Write a reconciliation report for an execution")
    reconcile.add_argument("--config", required=True)
    reconcile.add_argument("--execution", required=True)
    reconcile.add_argument("--out", default=None)
    reconcile.set_defaults(func=command_reconcile)

    override = subcommands.add_parser("override", help="Manage explicit file/directory overrides")
    override_sub = override.add_subparsers(dest="override_command", required=True)
    override_add = override_sub.add_parser("add")
    override_add.add_argument("--config", required=True)
    override_add.add_argument("--source", required=True)
    override_add.add_argument("--path", required=True)
    override_add.add_argument("--disposition", choices=("copy", "exclude", "defer", "duplicate_omit"), required=True)
    override_add.add_argument("--reason", required=True)
    override_add.add_argument("--destination", default=None)
    override_add.add_argument("--directory", action="store_true")
    override_add.set_defaults(func=command_override_add)
    override_list = override_sub.add_parser("list")
    override_list.add_argument("--config", required=True)
    override_list.set_defaults(func=command_override_list)
    override_remove = override_sub.add_parser("remove")
    override_remove.add_argument("--config", required=True)
    override_remove.add_argument("--id", required=True)
    override_remove.set_defaults(func=command_override_remove)

    evidence = subcommands.add_parser(
        "evidence", help="Extract bounded, provenance-rich evidence from hashed files"
    )
    evidence.add_argument("--config", required=True)
    evidence.add_argument("--run", default=None)
    evidence.add_argument("--timeout", type=float, default=None)
    evidence.add_argument("--max-input-mb", type=int, default=None)
    evidence.add_argument("--max-text-chars", type=int, default=None)
    evidence.add_argument("--retry-failures", action="store_true")
    evidence.add_argument("--out", default=None, help="Optional evidence export directory")
    evidence.set_defaults(func=command_evidence)

    evidence_stats = subcommands.add_parser(
        "evidence-stats", help="Show evidence coverage and cache statistics"
    )
    evidence_stats.add_argument("--config", required=True)
    evidence_stats.add_argument("--evidence-run", default=None)
    evidence_stats.set_defaults(func=command_evidence_stats)

    evidence_export = subcommands.add_parser(
        "evidence-export", help="Export evidence records and occurrence provenance"
    )
    evidence_export.add_argument("--config", required=True)
    evidence_export.add_argument("--evidence-run", default=None)
    evidence_export.add_argument("--out", required=True)
    evidence_export.set_defaults(func=command_evidence_export)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ReclaimError, ValueError) as exc:
        print(f"reclaim: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


# -- helpers ---------------------------------------------------------------


def _config(args: argparse.Namespace) -> ProjectConfig:
    config = load_config(args.config)
    ensure_workspace(config)
    return config


def _store(config: ProjectConfig) -> Store:
    return Store(config.database_path)


def _register(store: Store, config: ProjectConfig) -> None:
    for source in config.sources:
        store.upsert_source(
            source.source_id, source.label, str(source.path), source.priority
        )
    store.commit()


def _run_id(store: Store, requested: str | None, config: ProjectConfig) -> str:
    candidate = requested or config.default_run_id
    if candidate:
        store.get_run(candidate)
        return candidate
    latest = store.latest_run()
    if latest is None:
        raise ReclaimError("No scan run exists yet; run 'reclaim scan' first")
    return str(latest["run_id"])


def _profiles(config: ProjectConfig) -> tuple[TypeProfile, object]:
    engine, profile = load_policy(
        config.policy_file, config.policy_overlay_file
    )
    if config.types_file:
        profile = load_type_profile(config.types_file)
    return profile, engine


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _configuration_evidence(config: ProjectConfig) -> dict[str, object]:
    """Record exactly which type/policy data produced this export."""

    evidence: dict[str, object] = {}
    if config.types_file:
        evidence["types"] = {
            "source": str(config.types_file),
            "sha256": _sha256_bytes(config.types_file.read_bytes()),
        }
    else:
        data = resources.files("reclaim").joinpath("data", "default-types.toml").read_bytes()
        evidence["types"] = {"source": "builtin:default-types.toml", "sha256": _sha256_bytes(data)}

    if config.policy_file:
        evidence["policy"] = {
            "source": str(config.policy_file),
            "sha256": _sha256_bytes(config.policy_file.read_bytes()),
        }
    else:
        data = resources.files("reclaim").joinpath("data", "default-policy.toml").read_bytes()
        evidence["policy"] = {"source": "builtin:default-policy.toml", "sha256": _sha256_bytes(data)}

    if config.policy_overlay_file:
        evidence["policy_overlay"] = {
            "source": str(config.policy_overlay_file),
            "sha256": _sha256_bytes(config.policy_overlay_file.read_bytes()),
        }
    return evidence


def _absolute_path(config: ProjectConfig, source_id: str, rel_path: str) -> Path:
    source = config.source_by_id(source_id)
    return source.path.joinpath(*rel_path.split("/"))


def _scan_fingerprint(config: ProjectConfig, source_id: str) -> SourceFingerprint:
    source = config.source_by_id(source_id)
    scanner = SourceScanner(source, config.scan)
    for _ in scanner.walk():
        pass
    return scanner.fingerprint


def _stored_fingerprints(store: Store, run_id: str) -> dict[str, dict[str, object]]:
    """Stored metadata-consistency records, not byte-level integrity proofs."""

    run = store.get_run(run_id)
    raw = run["source_fingerprints_json"]
    return json.loads(raw) if raw else {}


def _verify_fingerprints(
    config: ProjectConfig,
    store: Store,
    run_id: str,
    *,
    verbose: bool = True,
) -> list[dict[str, object]]:
    stored = _stored_fingerprints(store, run_id)
    checks: list[dict[str, object]] = []
    for source in config.sources:
        if verbose:
            print(
                f"verifying source metadata consistency (not byte integrity): "
                f"{source.source_id}",
                flush=True,
            )
        try:
            current = _scan_fingerprint(config, source.source_id).as_dict()
        except ReclaimError as exc:
            check = {
                "check": "source_metadata_consistency",
                "source_id": source.source_id,
                "status": "ERROR",
                "detail": str(exc),
            }
        else:
            expected = stored.get(source.source_id)
            matches = expected == current
            check = {
                "check": "source_metadata_consistency",
                "source_id": source.source_id,
                "status": "PASS" if matches else "FAIL",
                "detail": None
                if matches
                else f"expected={expected} current={current}",
            }
        checks.append(check)
        store.record_check(
            run_id,
            f"source_metadata_consistency:{source.source_id}",
            str(check["status"]),
            str(check.get("detail") or ""),
        )
        if verbose:
            print(f"  {check['status']}", flush=True)
    return checks


# -- commands --------------------------------------------------------------


def command_init(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        _register(store, config)
    print(f"project:   {config.project_name}")
    print(f"workspace: {config.workspace}")
    print(f"database:  {config.database_path}")
    print(f"reports:   {config.reports_dir}")
    print(f"config sha256: {config.fingerprint()}")
    for source in config.sources:
        print(f"source {source.source_id}: {source.path}")
    print("workspace initialized; no source files were modified")
    return 0


def command_scan(args: argparse.Namespace) -> int:
    config = _config(args)
    started = time.monotonic()
    with _store(config) as store:
        _register(store, config)
        run_id = store.start_run(
            config.fingerprint(), store.source_identities()
        )
        print(f"scan run: {run_id}")
        if config.default_run_id and config.default_run_id != run_id:
            print(
                f"note: this config pins run {config.default_run_id}; "
                f"pass --run {run_id} to hash/export the new run"
            )
        fingerprints: dict[str, dict[str, object]] = {}
        for source in config.sources:
            print(f"scanning {source.source_id}: {source.path}", flush=True)
            scanner = SourceScanner(source, config.scan)
            source_started = time.monotonic()
            seen = 0
            last_report = source_started

            def records():
                nonlocal seen, last_report
                for record in scanner.walk():
                    seen += 1
                    if record.error_code:
                        store.add_scan_error(
                            run_id,
                            record.source_id,
                            record.rel_path,
                            "scan",
                            record.error_code,
                            record.error_message or "",
                        )
                    if seen % 5000 == 0:
                        now = time.monotonic()
                        print(
                            f"  {seen:,} entries "
                            f"({now - last_report:.1f}s since last checkpoint)",
                            flush=True,
                        )
                        last_report = now
                    yield record

            inserted = store.insert_entries(run_id, records())
            fingerprint = scanner.fingerprint
            fingerprints[source.source_id] = fingerprint.as_dict()
            elapsed = time.monotonic() - source_started
            print(
                f"  {inserted:,} entries, {fingerprint.file_count:,} files, "
                f"{fingerprint.total_bytes:,} bytes in {elapsed:.1f}s"
            )
        store.seed_hashes(
            run_id, hash_media_libraries=config.scan.hash_media_libraries
        )
        store.finish_run(run_id, status="scanned", fingerprints=fingerprints)
        counts = store.counts(run_id)
        elapsed = time.monotonic() - started
        print(
            f"scan complete in {elapsed:.1f}s: {counts['files']:,} files, "
            f"{counts['dirs']:,} directories, {counts['bytes']:,} bytes; "
            f"pending hashes: {counts['hash_pending']:,}; "
            f"deferred: {counts['hash_deferred']:,}"
        )
    return 0


def command_hash(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        _register(store, config)
        run_id = _run_id(store, args.run, config)
        targets = list(store.hash_targets(run_id, retry_errors=args.retry_errors))
        if args.limit is not None:
            targets = targets[: args.limit]
        total = len(targets)
        print(f"hashing {total:,} files for run {run_id}", flush=True)
        if total == 0:
            print("nothing pending")
        started = time.monotonic()
        bytes_done = 0
        errors = 0
        last_report = started
        for index, target in enumerate(targets, start=1):
            path = _absolute_path(config, target.source_id, target.rel_path)
            outcome = hash_file(
                path,
                expected_size=target.size_bytes,
                expected_mtime_ns=target.modified_ns,
                chunk_bytes=config.scan.chunk_bytes,
            )
            store.save_hash(target, outcome)
            bytes_done += outcome.bytes_read
            if outcome.status != "ok":
                errors += 1
            if index % 200 == 0:
                store.commit()
            now = time.monotonic()
            if index % 500 == 0 or (now - last_report) >= 30:
                elapsed = max(1e-6, now - started)
                print(
                    f"  {index:,}/{total:,} files, {bytes_done / 1e9:.2f} GB, "
                    f"{bytes_done / elapsed / 1e6:.0f} MB/s, {errors} errors",
                    flush=True,
                )
                last_report = now
        store.commit()
        elapsed = time.monotonic() - started
        counts = store.counts(run_id)
        run_status = (
            "complete"
            if counts["hash_pending"] == 0 and counts["hash_errors"] == 0
            else "partial"
        )
        store.set_run_status(run_id, run_status)
        print(
            f"hashing complete: {total:,} files, {bytes_done / 1e9:.2f} GB, "
            f"{errors} errors, {elapsed:.1f}s; run status: {run_status}"
        )
        if not args.no_verify:
            checks = _verify_fingerprints(config, store, run_id)
            failures = [check for check in checks if check["status"] != "PASS"]
            print(
                "source metadata consistency check (not byte integrity): "
                + ("PASS" if not failures else f"{len(failures)} failure(s)")
            )
            for failure in failures:
                print(f"  {failure}")
    return 0


def command_export(args: argparse.Namespace) -> int:
    config = _config(args)
    profile, policy = _profiles(config)
    with _store(config) as store:
        run_id = _run_id(store, args.run, config)
        counts = store.counts(run_id)
        if counts["hash_pending"]:
            print(
                f"warning: {counts['hash_pending']:,} files are not hashed; "
                "duplicate analysis is incomplete",
                file=sys.stderr,
            )
        rows = load_files(store, run_id, profile)
        dirs = load_dirs(store, run_id)
        groups, dispositions, metrics = build_all(
            store, run_id, rows, dirs, profile, policy
        )
        out_dir = Path(args.out) if args.out else config.reports_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        print("writing inventory ...", flush=True)
        inventory_rows = write_inventory(
            store, run_id, profile, out_dir / "cp1-source-inventory.csv"
        )
        print("writing duplicate analysis ...", flush=True)
        write_duplicates(
            groups,
            out_dir / "cp1-duplicate-analysis.csv",
            out_dir / "cp1-duplicate-occurrences.csv",
        )
        print("writing type analysis ...", flush=True)
        write_filetypes(metrics, out_dir / "cp1-filetype-analysis.csv")
        write_extensions(metrics, out_dir / "cp1-extension-analysis.csv")
        print("writing disposition candidates ...", flush=True)
        write_dispositions(dispositions, out_dir / "cp1-disposition-candidates.csv")
        write_signature_mismatches(
            metrics, out_dir / "cp1-signature-mismatches.csv"
        )
        error_rows = write_errors(store, run_id, out_dir / "cp1-errors.csv")
        configuration = _configuration_evidence(config)
        metrics["configuration"] = configuration
        write_metrics(metrics, out_dir / "cp1-metrics.json")
        checks = [
            dict(row)
            for row in store.conn.execute(
                "SELECT check_name, status, detail, checked_at FROM verification_checks WHERE run_id=?",
                (run_id,),
            )
        ]
        write_manifest(
            store,
            run_id,
            out_dir / "cp1-run-manifest.json",
            tool_version=__version__,
            config_path=str(config.config_path),
            config_sha256=config.fingerprint(),
            sources=[source.as_dict() for source in config.sources],
            checks=checks,
            configuration=configuration,
        )
        print(
            f"exported {inventory_rows:,} inventory rows, {len(groups):,} duplicate groups, "
            f"{len(dispositions):,} disposition rows, {error_rows:,} error rows to {out_dir}"
        )
        print(f"platform: {platform_note()}")
        print(
            "totals: "
            + json.dumps(metrics["totals"], sort_keys=True)
        )
    return 0


def command_verify(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        run_id = _run_id(store, args.run, config)
        print(f"run: {run_id}")
        checks = _verify_fingerprints(config, store, run_id)

        rows = store.conn.execute(
            """
            SELECT e.entry_id, e.source_id, e.rel_path, h.digest
            FROM entries e JOIN hashes h ON h.entry_id=e.entry_id
            WHERE e.run_id=? AND h.status='ok'
            ORDER BY e.entry_id
            """,
            (run_id,),
        ).fetchall()
        sample_size = min(max(0, args.sample), len(rows))
        sample: list[object] = []
        if sample_size:
            step = max(1, len(rows) // sample_size)
            sample = rows[::step][:sample_size]
        print(f"re-reading {len(sample)} of {len(rows):,} hashed files")
        failures = 0
        for row in sample:
            path = _absolute_path(config, row["source_id"], row["rel_path"])
            ok, error = verify_file(path, expected_digest=row["digest"])
            if not ok:
                failures += 1
                print(f"  FAIL {row['source_id']}:{row['rel_path']}: {error.message if error else ''}")
        checks.append(
            {
                "check": "hash_sample",
                "source_id": "*",
                "status": "PASS" if failures == 0 else "FAIL",
                "detail": f"{len(sample)} files re-read, {failures} mismatches",
            }
        )
        store.record_check(
            run_id,
            "hash_sample",
            "PASS" if failures == 0 else "FAIL",
            f"{len(sample)} files re-read, {failures} mismatches",
        )
        print(
            f"sample verification: {'PASS' if failures == 0 else 'FAIL'} "
            f"({len(sample)} files)"
        )
        print("checks: " + json.dumps(checks, indent=2, default=str))
    return 0


def command_status(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        run_id = _run_id(store, args.run, config)
        counts = store.counts(run_id)
        run = store.get_run(run_id)
        print(f"run:        {run_id}")
        print(f"status:     {run['status']}")
        print(f"started:    {run['started_at']}")
        print(f"finished:   {run['finished_at']}")
        for key in sorted(counts):
            print(f"{key:18} {counts[key]:,}")
    return 0


def command_plan(args: argparse.Namespace) -> int:
    config = _config(args)
    profile, policy = _profiles(config)
    with _store(config) as store:
        run_id = _run_id(store, args.run, config)
        settings = PlanSettings(
            destination_root=Path(args.destination),
            destination_template=args.template,
            duplicate_policy=args.duplicate_policy,
            target_platform=args.target_platform,
            max_path_length=args.max_path_length,
        )
        result = build_plan(
            store, config, policy, profile, run_id, settings
        )
        out_dir = Path(args.out) if args.out else config.workspace / "plans"
        manifest, items_path = export_plan(store, result.plan_id, out_dir)
        print(f"plan:   {result.plan_id}")
        print(f"digest: {result.plan_digest}")
        print(f"created: {result.created}")
        print(
            f"summary: items={result.summary['total_items']} "
            f"copy={result.summary['copy_items']} "
            f"copy_bytes={result.summary['copy_bytes']} "
            f"conflicts={result.summary['conflicts']}"
        )
        print(f"plan manifest: {manifest}")
        print(f"plan items:    {items_path}")
        print()
        for line in plan_preview_lines(store, result.plan_id, limit=20):
            print(line)
        if result.summary["conflicts"] or result.summary.get("global_issues"):
            return EXIT_EXECUTION_FAILURE
    return EXIT_OK


def command_preview(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        for line in plan_preview_lines(store, args.plan, limit=args.limit):
            print(line)
    return 0


def command_authorize(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        authorization_id = authorize_plan(
            store,
            config,
            args.plan,
            args.digest,
            actor=args.actor,
            notes=args.note,
        )
        print(f"authorized: {authorization_id}")
        print(f"plan:       {args.plan}")
        print(f"digest:     {args.digest}")
    return 0


def command_execute(args: argparse.Namespace) -> int:
    if args.max_items is not None and args.max_items < 1:
        raise ReclaimError("--max-items must be at least 1")
    config = _config(args)
    with _store(config) as store:
        result = execute_plan(
            store,
            config,
            args.plan,
            args.digest,
            dry_run_mode=args.dry_run,
            max_items=args.max_items,
            retry_failed=not args.no_retry_failed,
        )
        print(f"execution: {result.execution_id or '(dry run)'}")
        print(f"plan:      {result.plan_id}")
        print(f"status:    {result.status}")
        print(json.dumps(result.summary, indent=2, sort_keys=True, default=str))
        if result.dry_run:
            return EXIT_OK if result.summary.get("would_execute") else EXIT_EXECUTION_FAILURE
        if result.status == "complete":
            return EXIT_OK
        if result.status == "paused":
            return EXIT_INCOMPLETE
        if result.status == "verification_failed":
            return EXIT_VERIFICATION_FAILURE
        if result.status in ("verification_incomplete",):
            return EXIT_INCOMPLETE
        return EXIT_EXECUTION_FAILURE
    return EXIT_OK


def command_verify_execution(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        result = verify_execution(store, config, args.execution)
        print(json.dumps(result, indent=2, sort_keys=True))
        if result["status"] == "INCOMPLETE":
            return EXIT_INCOMPLETE
        if result.get("execution_status") == "verification_failed":
            return EXIT_VERIFICATION_FAILURE
        if result["status"] == "FAIL":
            return EXIT_VERIFICATION_FAILURE
        if result.get("execution_status") == "verification_incomplete":
            return EXIT_INCOMPLETE
        if result.get("execution_status") == "complete_with_failures":
            return EXIT_EXECUTION_FAILURE
        return EXIT_OK


def command_reconcile(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        out_dir = Path(args.out) if args.out else config.workspace / "reconciliation"
        summary = reconcile_execution(store, args.execution, out_dir)
        print(json.dumps(summary, indent=2, sort_keys=True))
        if summary.get("successful"):
            return EXIT_OK
        if summary.get("assessment") == "incomplete":
            return EXIT_INCOMPLETE
        return EXIT_RECONCILIATION_FAILURE


def command_override_add(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        override_id = add_override(
            store,
            scope="directory" if args.directory else "file",
            source_id=args.source,
            target_rel=args.path,
            disposition=args.disposition,
            reason=args.reason,
            destination_rel=args.destination,
        )
        print(f"override: {override_id}")
    return 0


def command_override_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        rows = list_overrides(store)
        if not rows:
            print("no active overrides")
            return 0
        for row in rows:
            print(
                f"{row['override_id']}  {row['scope']:9} "
                f"{row['source_id']}:{row['target_rel']} -> {row['disposition']} "
                f"({row['reason']})"
            )
    return 0


def command_override_remove(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        remove_override(store, args.id)
        print(f"removed: {args.id}")
    return 0


def _evidence_settings(args: argparse.Namespace) -> EvidenceSettings:
    from dataclasses import replace

    limits = ExtractionLimits()
    if args.timeout is not None:
        limits = replace(limits, timeout_seconds=max(0.1, float(args.timeout)))
    if args.max_input_mb is not None:
        limits = replace(
            limits,
            max_input_bytes=max(1, int(args.max_input_mb)) * 1024 * 1024,
        )
    if args.max_text_chars is not None:
        limits = replace(limits, max_text_chars=max(1000, int(args.max_text_chars)))
    return EvidenceSettings(limits=limits)


def command_evidence(args: argparse.Namespace) -> int:
    config = _config(args)
    settings = _evidence_settings(args)
    with _store(config) as store:
        _register(store, config)
        run_id = _run_id(store, args.run, config)
        service = EvidenceService(store, config, settings)
        print(f"evidence run for {run_id}", flush=True)

        def progress(payload: dict[str, object]) -> None:
            if payload.get("message"):
                print(str(payload["message"]), flush=True)

        result = service.run(
            run_id,
            progress=progress,
            retry_failures=args.retry_failures,
        )
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        if args.out:
            jsonl, csv_path = service.export(args.out)
            print(f"evidence JSONL: {jsonl}")
            print(f"evidence CSV:   {csv_path}")
        if result.status == "cancelled":
            return EXIT_INCOMPLETE
        failures = sum(
            result.counts.get(status, 0)
            for status in (
                "encrypted",
                "malformed",
                "timeout",
                "crashed",
                "resource_limit",
                "input_too_large",
                "hash_unavailable",
                "source_unavailable",
                "source_changed",
                "dependency_missing",
            )
        )
        return EXIT_EVIDENCE_FAILURE if failures else EXIT_OK


def command_evidence_stats(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        _register(store, config)
        service = EvidenceService(store, config)
        try:
            stats = service.stats(args.evidence_run)
        except ReclaimError as exc:
            print(f"reclaim: error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps(stats.__dict__, indent=2, sort_keys=True))
    return EXIT_OK


def command_evidence_export(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        _register(store, config)
        service = EvidenceService(store, config)
        jsonl, csv_path = service.export(args.out, args.evidence_run)
        print(f"evidence JSONL: {jsonl}")
        print(f"evidence CSV:   {csv_path}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
