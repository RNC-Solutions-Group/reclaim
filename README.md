# Reclaim

Reclaim is a local-first utility for discovering, inventorying, reconciling,
organizing, and migrating personal files across multiple computers and backup
collections — without risking the originals.

> **Status: CP1 (pre-alpha).** The repository currently contains a read-only
> inventory, hashing, duplicate-analysis, and reporting engine. It does **not**
> copy, move, rename, delete, or reorganize source files, and it does not yet
> contain a GUI or a migration executor. Migration execution is a later,
> separately authorized checkpoint.

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

## Quick start (development)

Requires Python 3.11 or newer. The CP1 engine uses only the standard library.
The editable install uses the standard PEP 517 build path, which requires
`setuptools` (pip normally provides it in an isolated build environment; an
offline install needs it preinstalled).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .

reclaim init   --config /path/to/project.toml
reclaim scan   --config /path/to/project.toml
reclaim hash   --config /path/to/project.toml
reclaim export --config /path/to/project.toml
reclaim verify --config /path/to/project.toml --sample 25
```

A project file describes the private workspace, one or more sources, and scan
settings. See [`config/reclaim.example.toml`](config/reclaim.example.toml).
A real project file contains private paths and must not be committed.

The workspace holds the SQLite inventory, private CSV reports, and run
manifests. It must be on a different path from every source; Reclaim rejects
overlapping source/workspace/destination paths before scanning.

## What CP1 implements

| Capability | Status |
| --- | --- |
| Multi-source registration and canonical-path validation | Implemented |
| Read-only enumeration of files, directories, links, excluded and failed entries | Implemented |
| Metadata capture (size, timestamps, attributes, hidden/system/cloud flags, depth) | Implemented |
| Resumable SHA-256 hashing with size/mtime cache | Implemented |
| Exact duplicate grouping across sources, with all occurrences retained | Implemented |
| File-type and extension analysis with lightweight signature checks | Implemented |
| Data-driven disposition recommendations with rule evidence | Implemented |
| Source-preservation fingerprints before and after hashing | Implemented |
| Private CSV/JSON reports and run manifest | Implemented |
| Similarity, revision detection, content extraction, and AI assistance | Not implemented (later checkpoint) |
| Migration planning, destination preview, copy execution, GUI | Not implemented (later checkpoints) |

## Repository layout

```text
src/reclaim/          Reusable inventory and analysis engine
  config.py           Project/source configuration and safety validation
  paths.py            Canonical paths, overlap checks, long-path handling
  scanner.py          Read-only enumeration
  hashing.py          Streaming SHA-256 with change detection
  store.py            SQLite project store
  classify.py         Type profiles and disposition policy engine
  analysis.py         Duplicates, overlap, structure, dispositions
  report.py           Private CSV/JSON writers
  cli.py              Command-line interface
  data/               Generic default type map and policy
tests/                Synthetic-fixture tests (no real user data)
docs/cp1/             CP1 architecture, data model, roadmap, decisions
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
precedence, cache reuse, and an end-to-end scan/hash/export run. Tests that
require junction creation are skipped on platforms where the process is not
permitted to create one.

## Documentation

- [Engine architecture](docs/cp1/cp1-engine-architecture.md)
- [Data model](docs/cp1/cp1-data-model.md)
- [Roadmap](docs/cp1/cp1-roadmap.md)
- [Decisions required](docs/cp1/cp1-decisions-required.md)

## License

License selection is pending operator approval before any public release; see
the decisions document. No license is granted by this checkpoint.
