# Reclaim

Reclaim is a local-first utility for discovering, inventorying, reconciling,
organizing, and migrating personal files across multiple computers and backup
collections — without risking the originals.

> **Status: M2 desktop functional alpha (pre-alpha).** The repository contains a
> read-only inventory/hashing engine, the M1/M1A plan, authorization, journaled
> copy, verification, resume, and reconciliation subsystem, and a PySide6
> desktop shell for the full synthetic migration workflow. It does **not** yet
> contain document extraction, classification, naming intelligence, GUI
> packaging/installers, or real-data migration authorization. M1/M2 are
> validated only on synthetic datasets.

## Why Reclaim exists

Long-lived personal computing produces overlapping copies of the same data:
old Downloads folders, redirected OneDrive directories, exported archives,
multiple backups of the same laptop, and years of near-duplicate revisions.
Cleaning that up by hand is slow and risky; deleting the wrong copy is
irreversible.

Reclaim's approach is deliberately conservative:

1. **Inventory first.** Enumerate every source occurrence and preserve
   provenance before deciding anything.
2. **Recommend, don't command.** Automated findings are proposals. A user
   decision always wins, and exclusion means "omit from the destination", never
   "delete from the source".
3. **Plan before writing.** A migration plan is a reviewable artifact with a
   destination preview; execution is a separate, explicitly authorized step.
4. **Verify everything.** Every copied file is checked, and every source file
   is reconciled against an explicit disposition.

## Safety contract

- Sources are opened read-only. The inventory engine has no delete, move, or
  overwrite code path.
- Symbolic links, junctions, and cloud placeholders are recorded but not
  followed or hydrated by default.
- Personal data, manifests, and project databases live outside the Git
  repository. The repository contains only code, tests, synthetic fixtures,
  and public-safe documentation.
- Processing is offline. There are no telemetry, cloud, or model/API calls.
- Unreadable or changing files are recorded as documented failures rather than
  silently skipped or given an unreliable digest.
- Destination publication uses atomic hard-link no-overwrite semantics. If the
  filesystem cannot support it, execution fails closed; there is no
  overwrite-capable fallback.

## Quick start (development)

Requires Python 3.11 or newer. The engine uses only the standard library. The
desktop shell uses PySide6 as an optional dependency. The editable install uses
the standard PEP 517 build path, which requires `setuptools` (pip normally
provides it in an isolated build environment; an offline install needs it
preinstalled).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[gui]"

# Desktop shell
reclaim-gui

# Engine CLI
reclaim init   --config /path/to/project.toml
reclaim scan   --config /path/to/project.toml
reclaim hash   --config /path/to/project.toml
reclaim export --config /path/to/project.toml
reclaim verify --config /path/to/project.toml --sample 25

# M1 migration spine (synthetic data only)
reclaim plan --config /path/to/project.toml --destination /path/to/destination
reclaim preview --config /path/to/project.toml --plan plan-...
reclaim authorize --config /path/to/project.toml --plan plan-... --digest <sha256>
reclaim execute --config /path/to/project.toml --plan plan-... --digest <sha256> --dry-run
reclaim execute --config /path/to/project.toml --plan plan-... --digest <sha256>
reclaim verify-execution --config /path/to/project.toml --execution exec-...
reclaim reconcile --config /path/to/project.toml --execution exec-...
```

A project file describes the private workspace, one or more sources, and scan
settings. See [`config/reclaim.example.toml`](config/reclaim.example.toml).
A real project file contains private paths and must not be committed.

The workspace holds the SQLite inventory, private CSV reports, and run
manifests. It must be on a different path from every source; Reclaim rejects
overlapping source/workspace/destination paths before scanning.

## What is implemented

| Capability | Status |
| --- | --- |
| Multi-source registration and canonical-path validation | Implemented |
| Read-only enumeration of files, directories, links, excluded and failed entries | Implemented |
| Metadata capture (size, timestamps, attributes, hidden/system/cloud flags, depth) | Implemented |
| Resumable SHA-256 hashing with size/mtime cache and source-root identity | Implemented |
| Exact duplicate grouping across sources, with all occurrences retained | Implemented |
| File-type and extension analysis with lightweight signature checks | Implemented |
| Data-driven disposition recommendations with rule evidence | Implemented |
| Source metadata-consistency fingerprints before and after hashing (metadata only, not byte integrity) | Implemented |
| Private CSV/JSON reports and run manifest | Implemented |
| Immutable migration plans with deterministic digests | Implemented (M1) |
| Host-independent destination validation (paths, collisions, reserved names, links, capacity) | Implemented (M1) |
| Explicit plan authorization and duplicate policies | Implemented (M1) |
| Journaled, resumable, copy-only execution with no overwrite | Implemented (M1) |
| Post-copy SHA-256 verification and balanced reconciliation | Implemented (M1) |
| Verification failure invalidation and accurate reconciliation success/assessment | Implemented (M1A) |
| Whole-path target equivalence and fail-closed atomic publication | Implemented (M1A) |
| Stable CLI exit codes for failure/incomplete/verification/reconciliation | Implemented (M1A) |
| PySide6 desktop shell: project/source/destination setup, scan/hash/analyze, review, plan, authorize, execute/pause/resume, verify, reconcile | Implemented (M2 functional alpha) |
| Similarity, revision detection, content extraction, and AI assistance | Not implemented (later milestone) |
| GUI packaging/installer, naming intelligence, real-data migration | Not implemented (later milestones) |

## Repository layout

```text
src/reclaim/          Reusable inventory and analysis engine
  gui/                 PySide6 desktop shell and application services
  config.py           Project/source configuration and safety validation
  paths.py            Canonical paths, overlap checks, long-path handling
  scanner.py          Read-only enumeration
  hashing.py          Streaming SHA-256 with change detection
  store.py            SQLite project store
  classify.py         Type profiles and disposition policy engine
  analysis.py         Duplicates, overlap, structure, dispositions
  report.py           Private CSV/JSON writers
  destination.py      M1 destination policy and validation
  planning.py         M1 plans, recommendations, overrides, authorization
  execution.py        M1 journaled copy, resume, verification, reconciliation
  cli.py              Command-line interface
  data/               Generic default type map and policy
tests/                Synthetic-fixture tests (no real user data)
docs/cp1/             CP1 architecture, data model, roadmap, decisions
docs/m1/              M1 architecture, data model, operator guide, test matrix
docs/m2/              M2 desktop architecture, user guide, test matrix, screenshots, packaging
docs/product/         Product outlook, architecture assessment, roadmap, ADRs, benchmarks
docs/cp1-followup/    CP1 corrective work notes
config/               Synthetic example configuration
```

## Privacy and data boundaries

The repository is for the reusable product. Real source paths, filenames,
hashes, inventories, reports, and migration plans belong in an external
private workspace, not in Git. `.gitignore` is a backstop, not a substitute
for that separation.

Reports may contain personal filenames even when no file contents are read.
Treat the entire workspace as sensitive.

## Testing

```bash
python -m unittest discover -s tests -t .
```

The suite builds synthetic trees covering exact duplicates across sources,
renamed duplicates, same-name/different-content collisions, empty and hidden
entries, Unicode names, deep paths, junctions/symlinks, hash failures, policy
precedence, cache reuse, and an end-to-end scan/hash/export run. M1 adds
destination policy, whole-path equivalence, plan determinism, authorization
invalidation, journaled execution, resume, no-overwrite/publication failure,
source-change, destination-race, verification invalidation/recovery, and
balanced-vs-successful reconciliation tests. **M2 adds 17 synthetic GUI/service
tests**, for **130 total passing** on the M2 branch. Tests that require junction
creation are skipped on platforms where the process is not permitted to create
one.

## Documentation

**M2 desktop shell**

- [M2 overview and screenshots](docs/m2/README.md)
- [M2 architecture and GUI boundaries](docs/m2/architecture.md)
- [M2 operator guide](docs/m2/user-guide.md)
- [M2 test matrix](docs/m2/test-matrix.md)
- [Packaging and Qt/LGPL notes](docs/m2/packaging.md)

**M1 migration spine**

- [M1 overview](docs/m1/README.md)
- [M1 architecture and execution model](docs/m1/architecture.md)
- [M1 data model](docs/m1/data-model.md)
- [M1 operator guide](docs/m1/operator-guide.md)
- [M1 test and acceptance matrix](docs/m1/test-matrix.md)

**Product and engineering direction**

- [Product outlook](docs/product/product-outlook.md)
- [Architecture assessment](docs/product/architecture-assessment.md)
- [Roadmap](docs/product/roadmap.md)
- [Architecture decisions](docs/product/architecture-decisions.md)
- [Testing and benchmark strategy](docs/product/testing-and-benchmarking.md)
- [Immediate next assignment](docs/product/next-assignment.md)

**CP1 background and corrective work**

- [Engine architecture](docs/cp1/cp1-engine-architecture.md)
- [Data model](docs/cp1/cp1-data-model.md)
- [CP1 roadmap (historical)](docs/cp1/cp1-roadmap.md)
- [Decisions required](docs/cp1/cp1-decisions-required.md)
- [CP1 corrective notes](docs/cp1-followup/cp1-corrective-notes.md)

## License

License selection is pending operator approval before any public release; see
the decisions document. No license is granted by this checkpoint.
