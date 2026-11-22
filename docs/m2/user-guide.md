# M2 — Desktop Operator Guide

This guide describes the functional alpha desktop workspace. It uses only
synthetic development examples until a real-data migration is separately
authorized.

## 1. Install and launch

```powershell
python -m pip install -e ".[gui]"
reclaim-gui
```

Source checkout:

```powershell
$env:PYTHONPATH = "src"
python -m reclaim.gui.app
```

The optional GUI dependency is PySide6 6.7–6.9. The CLI remains available
without it.

## 2. Create a project

1. **File → New project** (or **New project** in the header).
2. Enter a project name and folder. The project folder gets a
   `reclaim-project.toml` and a private `workspace/` database/report directory.
3. Add one or more source directories. Each is inventoried read-only.
4. Select an existing destination directory. It must be a separate physical
   directory, not a source, workspace child, link, or junction.
5. Select **Create**.

The project screen shows source availability, destination validation, free
space, and inventory state. **Open project** reopens an existing
`reclaim-project.toml`.

> Keep project workspaces private. They contain source-relative paths,
> inventory records, plans, execution journals, and reports.

## 3. Inventory

Use the numbered buttons in order:

1. **Scan sources** — metadata only; no source content is read or written.
2. **Hash files** — read-only SHA-256; unreadable/changing files are recorded as
   explicit failures.
3. **Analyze and review** — loads recommendations, duplicate groups, and the
   review table.

The global **Stop safely** button is available for scan, hash, and execution. A
stop finishes the current item, then reports an incomplete/paused state. Retry
or resume when ready.

## 4. Review decisions

The Review table has one row per source occurrence. The Include checkbox shows
the effective plan action (`copy`). Select one or many rows and use:

- **Include selected** — override to copy.
- **Exclude selected** — override to omit from the destination (never delete
  from the source).
- **Defer selected** — leave for a later decision.
- **Remove override** — remove a selected file-level override and return to the
  recommendation.
- **All overrides…** — inspect active overrides.

A reason is required for each new override and is preserved in the plan/audit
record. Search and the filter list narrow the table; the row count remains
model-backed for large inventories.

Recommendations, duplicate roles, hash status, effective action, and the
override reason are visible in the row tooltip and detail pane.

## 5. Generate and authorize a plan

1. Open **Plan**.
2. Confirm destination, template, duplicate policy, target path rules, and path
   length budget.
3. **Generate immutable plan**.
4. Review the plan summary, conflict list, and destination hierarchy. The
   destination tree shows proposed paths; the Source column shows original
   source paths.
5. Resolve conflicts in Review, then generate a new plan.
6. **Authorize exact plan…** and confirm the displayed plan digest. Authorization
   applies only to that digest.
7. Review decisions or settings changed after planning mark the plan stale and
   require a new plan.

## 6. Execute, pause, and resume

1. With the authorized plan, choose **Execute plan…**.
2. Confirm the migration. Execution is copy-only, journaled, and uses atomic
   hard-link no-overwrite publication.
3. Watch the status/progress area. **Stop safely** requests a pause between
   items; already published files remain verified/journaled.
4. The Results page shows execution counts and enables **Resume paused
   execution** when a paused execution exists.
5. Closing the application during a job asks whether to stop safely after the
   current item and close. Already published files remain journaled; reopen the
   project and use **Resume paused execution** in Results.

If a destination or source changed after planning, execution fails closed and
records the failure. Resolve the condition, then retry/resume as appropriate.

## 7. Verify and reconcile

On **Results**:

- **Verify copies** re-hashes every verified destination. Missing, unreadable,
  or mismatched files become failures and the execution status becomes
  `verification_failed`; they are never reported as successful.
- **Reconcile** writes the CSV/JSON report and shows every plan item's outcome.
  Read the distinction:
  - **Balanced** — every plan item has exactly one accounted-for outcome.
  - **Successful** — every required copy is `copied_verified` and no blocking
    outcome remains.
  - **Assessment** — `success`, `failed`, or `incomplete`.

Authorized exclusions, deferrals, and duplicate omissions are legitimate
terminal outcomes. A balanced report with failures is not successful.

## 8. Reopen and recovery

Opening the project again loads the latest run, plan, and execution. A paused
execution can be resumed from Results. The exact authorized plan/digest is
required; if the visible plan does not match the execution's plan, the GUI asks
you to load the correct plan before resuming.

## 9. Known limitations

- Windows/NTFS is the validated environment; macOS/Linux GUI execution is
  unverified.
- Hard-link-capable destinations are required for M1/M2 execution.
- No advanced folder-create/rename/nest editing yet.
- No content extraction, classification, naming intelligence, semantic search,
  SLM, or external AI.
- No installer/code-signed distribution; see [packaging](packaging.md).
- Large-table virtualization is implemented but not benchmarked at full private
  collection scale.
- Empty directories are not migrated; extended-length paths, ACLs, ADS, sparse
  files, and hard-link identity are not preserved.
