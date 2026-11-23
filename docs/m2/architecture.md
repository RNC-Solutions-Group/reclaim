# M2 — Architecture and GUI Boundaries

## 1. Purpose

M2 makes the existing deterministic migration workflow usable by a
non-technical operator. It is a presentation layer over M1/M1A, not a second
migration engine.

## 2. Component layout

| Component | Responsibility |
| --- | --- |
| `reclaim.gui.app` | QApplication startup and optional project/config argument |
| `reclaim.gui.main_window` | Four-step workspace, dialogs, progress/status, review/tree/result views |
| `reclaim.gui.project_dialog` | New-project collection: sources, destination, project folder |
| `reclaim.gui.services.ProjectSession` | Application facade and application-service boundary |
| `reclaim.gui.workers.Task` | QThread wrapper around one service call; progress/failure signals |
| `reclaim.gui.models` | Virtualized `QAbstractTableModel` implementations and review filter proxy |
| M1 engine modules | Inventory, plans, authorization, destination policy, journaled execution, verification, reconciliation |

The GUI does **not** import `sqlite3`, open the SQLite database, traverse source
trees, copy files, build plans, or implement authorization. Those operations
belong to `ProjectSession` and the engine modules. The widgets receive
plain Python snapshots/dataclasses from the service.

## 3. Threading and responsiveness

- The Qt main thread owns windows and models.
- Each expensive operation runs in a `Task` (`QThread`) and reports a
  `progress` signal dictionary plus a terminal `completed` or `failed` signal.
- The application-service call opens its own short-lived `Store` in the worker
  thread for write operations. The main-thread `Store` is used for read-only
  snapshots. SQLite connections are never shared across threads.
- Scan, hash, and execution are stoppable. A stop request sets an event; the
  current file/item finishes, no new item starts, and the operation reports a
  paused/partial state. There is no destructive rollback: already published
  copies remain journaled and resumable.
- Analysis, plan construction, verification, and reconciliation are
  non-stoppable bounded service calls in the alpha; they still run off the UI
  thread.
- Review tables use a source `QAbstractTableModel` plus a filter proxy; no
  per-row widgets are created. The filter hot path uses indexed
  `row_at(index)`/`count()` access, never `rows()` full-list materialization.
  Plan preview is capped at 4,000 displayed destination items. See
  [review-table benchmarks](benchmarks.md) for latency/memory measurements.
- Filtering is still synchronous on the GUI thread; at 100,000 synthetic rows
  measured search latency was in the ~2 second range on the validation host.
  This is an explicit alpha limitation, not a claim of instantaneous filtering.

## 4. Application-service boundary

`ProjectSession` owns:

- project config, policy/profile, GUI settings, and latest run;
- source/destination validation;
- scan/hash/analysis;
- review rows with recommendation/effective decision/override provenance;
- file override add/remove;
- plan construction/export and authorization;
- execution with progress and cancellation;
- verification and reconciliation.

The service returns dataclasses (`ReviewRow`, `PlanSnapshot`,
`ExecutionSnapshot`, `Reconciliation`, `AnalysisBundle`) and dict summaries.
Widgets do not construct SQL or filesystem operations.

### Plan freshness

A loaded plan snapshot carries its frozen run, policy fingerprint, destination
(selected and canonical), template, duplicate policy, target-platform rules,
path budget, and a stable fingerprint of active overrides. The service compares
these values against current settings, the current run, and the current active
overrides. If they differ, the plan remains available as a historical artifact
but is marked stale: authorization/execution controls are disabled until a new
plan is generated and authorized. The same check runs on project reopen, so a
newer destination/settings configuration cannot silently inherit an older
authorized plan.

Plan history uses durable SQLite insertion order (`rowid`) for “latest” and
listing rather than second-precision timestamps. Rapid plans created in the same
second therefore reopen in actual creation order; plan digests remain
independent of this ordering metadata.

### Destination identity

`ProjectSession.create` preserves the operator-selected destination spelling
separately from the canonical identity. The selected path is passed to the M1A
validator; link/junction roots and redirected ancestors are rejected at project
creation and on later destination changes. The plan retains M1A's
`destination_selected` and canonical `destination_root`.

## 5. Workflow

```text
create/open project
  → validate sources + destination/capacity
  → scan (metadata)
  → hash (read-only content)
  → analyze → review
  → include/exclude/defer overrides (reason required)
  → build immutable plan (new digest)
  → destination hierarchy/conflict preview
  → authorize exact digest
  → execute journaled copies (pause/resume)
  → verify copies
  → reconcile every plan item
```

A review decision or plan-setting change marks the current plan stale. The GUI
disables authorization/execution until a new plan is generated. The engine
independently recomputes the plan digest and enforces authorization, so the GUI
cannot bypass the contract even if a control is stale.

## 6. Safety contracts preserved

- Sources are read-only; the GUI does not offer delete/move/rename.
- Plan validation and destination no-overwrite policy remain entirely in
  `destination.py`.
- Execution requires the exact authorized digest; the GUI confirmation shows
  the digest before execution.
- Publication remains atomic hard-link no-overwrite; unsupported filesystems
  fail closed.
- Existing matching destination hashes are not adopted without execution
  provenance.
- Verification failure transitions are owned by `execution.py`, not the GUI.
- Reconciliation distinguishes `balanced` from `successful`.
- Project state, plans, execution journals, and reports stay in the private
  project workspace.

## 7. Reopen and recovery

On open, the service loads the latest scan run, plan, and execution. The GUI
shows the latest plan and execution state. If the execution is `paused`, the
Results page enables **Resume paused execution**; the engine resumes the same
journaled execution rather than creating a second one. Closing during a
background job asks the operator to stop safely after the current item; the
window closes after the journal/state write is consistent, and reopening
supports resume. A non-stoppable verification/reconciliation job is allowed to
finish before the close completes.

## 8. UX structure

- **Project:** project identity, source table, destination validation/free
  space, scan/hash/analyze actions, run counts.
- **Review:** search and filters, virtualized review table, evidence/detail
  pane, checkbox decisions, bulk actions, override removal/listing.
- **Plan:** destination/template/duplicate/platform settings, plan digest and
  counts, conflict list, destination hierarchy with original source and proposed
  destination columns, authorize/execute/results actions.
- **Results:** execution status and counts, resume, verify, reconcile, and
  reconciled item outcomes.

## 9. Known M2 limitations

- Validated on Windows 11/NTFS only; macOS/Linux GUI execution is unverified.
- PySide6 is an optional runtime dependency; the engine remains usable via CLI.
- The GUI displays the first 4,000 destination items in the tree; the underlying
  plan and reconciliation include all items.
- Source editing after project creation is not exposed; create a new project or
  edit the TOML with operator review.
- Advanced destination-folder editing (create/rename/nest/reassign) is deferred,
  but the override/plan contract is not replaced.
- No installer or code-signed binary is included; packaging is a smoke-tested
  Python wheel plus Qt system dependency.
- No content extraction, classification, semantic naming, or AI is present.
- Large-table behavior is implemented with models/filter proxies but filtering
  remains synchronous; 100,000-row search measured ~2 seconds on the validation
  host. Not benchmarked at the full private-collection scale.
- Plan freshness depends on stored review-fingerprint metadata for plans created
  by M2; older plans without that fingerprint are conservatively treated as
  stale when the current configuration cannot be proven equivalent.
- Stop requests are cooperative between files/items, not an immediate
  interruption of an in-progress file copy.
