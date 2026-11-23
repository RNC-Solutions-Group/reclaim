# M2 — Test and Acceptance Matrix

## 1. Automated validation

- **130 tests, all passing** on the M2 branch.
- `python -m unittest discover -s tests -t .`
- `python -m compileall -q src tests`
- GUI tests run with `QT_QPA_PLATFORM=offscreen`; no display is required.
- All fixtures are generated under temporary directories. No real project,
  personal path, external dataset, network service, or model is used.

## 2a. M2A corrective coverage

| Finding | Regression coverage |
| --- | --- |
| M2A-01 indexed review access | `tests/test_m2_gui.py::test_filter_does_not_materialize_full_rows`; benchmark at 10k/50k/100k in `docs/m2/benchmarks.md` |
| M2A-02 selected destination identity | Service tests for selected-path preservation, junction root rejection, redirected ancestor rejection, post-creation destination change, valid physical destination |
| M2A-03 plan freshness | Service reopen/settings/overrides freshness tests; GUI test that changed settings disable execution and restoration re-enables it |
| M2A-04 deterministic plan order | Service test creates three plans rapidly and verifies `latest_plan`/`list_plans` follow insertion order |
- **M2A benchmark:** reproducible script outside unit tests; results and limitations recorded in [benchmarks](benchmarks.md).

## 2. Test inventory

| Area | Coverage |
| --- | --- |
| Existing CP1/M1/M1A | `tests/test_config.py`, `test_scanner.py`, `test_hashing.py`, `test_paths.py`, `test_policy.py`, `test_analysis.py`, `test_store.py`, `test_destination.py`, `test_planning.py`, `test_m1_cli.py` |
| M2 application services | `tests/test_m2_services.py`: project creation/validation, scan, hash, analysis, review rows, override add/remove, plan regeneration, authorization, execution, pause/resume, verification, reconciliation, reopen, destination conflict rejection |
| M2 GUI integration | `tests/test_m2_gui.py`: offscreen window, full Project → Review → Plan → Results workflow, model checkbox override, plan regeneration, filters, results controls |
| Engine contract retained | Existing 113 M1/M1A tests remain passing |

## 3. Level-by-level evidence

### Level 1 — Unit/contract

- `ProjectSession.create/open` validates sources and destination; source inside
  destination is rejected by the existing destination policy.
- Review rows preserve recommendation, effective decision, override ID/reason,
  duplicate role, and hash status.
- Plan snapshots preserve the exact digest and authorization state.
- `execute_plan` accepts an optional progress callback and cooperative stop
  event; existing M1 tests still pass.

### Level 2 — Integration

- Service end-to-end: synthetic project → scan → hash → analyze → plan →
  authorize → execute → verify → reconcile → reopen.
- GUI end-to-end: same workflow through the MainWindow; review table, plan tree,
  authorization, execution, verification label, reconciliation table.
- Review checkbox creates a file override and regenerates a plan with the
  changed copy count.

### Level 3 — Fault injection

- Preexisting destination with a matching hash is a plan conflict, not adopted.
- Paused execution (`Event` set before execution) leaves pending items; a second
  execute resumes the same journaled execution and completes.
- Destination/source changes and verification failures remain covered by M1A
  tests.
- GUI stop control is wired to the cooperative stop event; UI remains responsive
  while jobs run in `QThread`.

### Level 4 — Cross-platform

- **Windows 11 / NTFS / Python 3.13.5** is the only executed environment.
- GUI tests are offscreen Qt tests, not tests of Windows-native file dialogs or
  macOS/Linux platform integration.
- macOS/Linux execution and GUI packaging are **not verified** and must not be
  reported as passed.

### Level 5 — Packaging

- `python -m pip wheel . --no-deps` built `reclaim-0.3.0-py3-none-any.whl`.
- Wheel installed into a virtual environment with access to the host PySide6
  6.9.3; `reclaim --version` reported `0.3.0`.
- `reclaim-gui.exe` and `reclaim.exe` entry points were generated.
- The GUI module imported from the installed wheel and a `MainWindow`
  instantiated offscreen (4 tabs).
- PySide6 is an optional extra; the wheel itself does not bundle Qt.
- No installer, signing, or auto-update validation.

## 4. Acceptance checklist

| M2 gate | Status |
| --- | --- |
| Non-technical synthetic migration through GUI | PASS (automated GUI workflow; manual smoke) |
| Long jobs do not freeze UI | PASS (QThread service jobs; offscreen test event loop) |
| Review decisions reversible and reflected in plans | PASS (service + GUI tests) |
| Destination conflicts/validation visible | PASS (service tests, plan conflict list) |
| Explicit exact-digest authorization | PASS (GUI confirmation and engine enforcement) |
| Close/reopen safe recovery/resume | PASS for persisted state and paused resume (service); GUI startup loads latest state |
| Verification/reconciliation through GUI | PASS (GUI integration test) |
| Existing M1/M1A tests remain passing | PASS (113) |
| GUI tests/packaging checks | PASS for offscreen GUI tests, wheel build/install, and GUI import; installer/Qt-legal review not claimed |
| Documentation reflects actual capabilities | PASS (M2 docs + README/roadmap/ADR updates) |

## 5. Manual screenshot validation

Screenshots in `docs/m2/screenshots/` were generated from a synthetic temporary
project with `tools/capture_m2_screens.py`:

- Review: model-backed table, duplicate role, recommendation/effective action,
  detail pane.
- Plan: plan digest, counts, destination hierarchy, source-vs-destination
  columns, authorization controls.
- Results: execution counts, verification/reconciliation summary, outcome table.

The screenshots contain no real personal data. They may show a home-relative
temporary path (`~/AppData/...`) from the synthetic demo.

## 6. Limitations not verified

- macOS/Linux GUI, file dialogs, path rules, and packaging.
- Native installer, code signing, auto-update.
- Full private-collection scale/benchmark performance.
- OS-level kill/power-loss recovery (cooperative pause only).
- Accessibility testing (screen readers, keyboard-only traversal, high-DPI
  scaling).
- Full Qt/PySide6 redistribution legal review; ADR-009 remains conditional.
- No extraction/classification/naming/AI capability is claimed.
