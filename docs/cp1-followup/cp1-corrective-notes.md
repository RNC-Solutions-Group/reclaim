# CP1 Follow-Through — Corrective Work Notes

**Status:** Implemented and tested; awaiting operator review.
**Scope:** The five corrective items authorized after CP1. No consolidation,
migration, extraction, GUI, or roadmap implementation is included.

---

## A. Hash-cache identity

### Problem

The v1 cache key was `(source_id, rel_path, size_bytes, modified_ns, algorithm)`.
A source could be re-registered against a different root while keeping the same
`source_id`; a cached digest could then be reused for a different file that
happened to share the relative path, size, and mtime.

### Change

- `sources` now stores a **root identity** derived from:
  - the canonical source path;
  - the backing volume identity (Windows volume serial + filesystem, or POSIX
    `st_dev`).
- `hash_cache` stores the same identity.
- `seed_hashes` only reuses a cache row when the source's current root identity
  matches the cache row's identity.
- `upsert_source` compares the existing identity with the newly computed one
  and deletes that source's cache when they differ.
- `scan_runs` records the source identities used for the run.
- Schema version is now **2**. A v1 database is forward-migrated; legacy cache
  rows have no identity and are deliberately not reused.
- A one-time `meta` note records the cache-identity migration.

### Tests added

- Root identity is stable for the same root and differs for a different root.
- Re-registering the same root preserves the cache.
- Re-registering a different root with the same relative path, size, and mtime
  invalidates the cache, forces a new hash, and produces the new digest.
- A synthetic v1 database migrates to v2 and its legacy cache is not reused.

### Behavior change

Existing v1 project caches are conservative-invalidated. The next scan may
rehash files once. This is intentional: the v1 database cannot establish which
root produced a cache row.

---

## B. Repository agent instructions

`AGENTS.md` was excluded by `.gitignore`. The file contains generic engineering
and governance instructions, not operator paths, secrets, or migration data.
It is now trackable and versioned so fresh clones receive the same repository
instructions. `.gitignore` retains backstops for local databases and private
project workspaces.

No private content was added to the repository.

---

## C. Source-integrity terminology

The earlier wording could imply that a metadata fingerprint proves byte-level
source integrity. That is incorrect.

Clarified in the README, architecture document, data model, and CLI output:

- A **source metadata-consistency fingerprint** covers relative paths, entry
  types, sizes, and mtimes. It establishes metadata consistency, not content
  integrity.
- **SHA-256** establishes the content digest at hash time.
- A **sampled re-read** establishes that a sample still matches stored digests.
- A **destination post-copy hash** establishes that the copied file matches the
  planned digest.
- **Full reconciliation** establishes that every source occurrence has an
  accounted outcome.

The architecture document now has a verification-layer table. Future reports
must use "metadata consistency" rather than "byte-identical sources" unless a
full hash comparison actually ran.

---

## D. Completed iPhoto archive

The previously interrupted transfer is complete.

Read-only verification performed:

- exact byte size recorded;
- SHA-256 computed over the complete file;
- ZIP central directory opened read-only (no extraction) to confirm structure:
  ~50k entries, ~29.7 GB uncompressed, rooted at an iPhoto library plus macOS
  resource-fork metadata;
- no independent source digest was available for comparison.

Registration:

- a supplemental configuration includes the archive and excludes only the OS
  recycle containers;
- a new scan run records the archive as a file occurrence with its digest,
  size, and source identity;
- a project policy rule assigns `defer_media` disposition with the rationale
  that it is a completed media-library archive;
- the archive is **not** extracted, consolidated, deleted, or included in the
  document migration;
- the original CP1 baseline run and its reports are preserved; the supplemental
  run is separate.

The archive digest and full verification record are in the private workspace,
not the repository.

---

## E. Operator-resolved CP1 findings

Recorded in the CP1 decisions document:

- six source directories are six intentional collections;
- the two MacBook collections are distinct sources and remain separate;
- the operator manually recovered the relevant additional content;
- the prior collector's cloud/path gaps do not require a new recovery project;
- the staged set is accepted as the working source of truth, with the historical
  coverage caveat retained as audit evidence.

No further provenance/recovery workstream is opened by this follow-through.

---

## Validation

| Check | Result |
| --- | --- |
| Synthetic unit/integration tests | 46 passing |
| Cache identity root-change regression | Pass |
| v1 → v2 schema migration | Pass |
| Legacy cache non-reuse | Pass |
| AGENTS.md tracked and free of private paths | Pass |
| Documentation terminology scan | Updated |
| CP1 baseline database preserved | Copy retained before migration |
| Archive size/SHA-256/ZIP structure | Verified read-only |
| Supplemental scan | Registered archive; separate run |
| Supplemental hash/verify/export | Recorded separately |
| Real source modification | None by Reclaim |
| Consolidation/migration | Not performed |

### Not verified

- Cross-platform execution (Windows only in this environment).
- Package build/install (`setuptools` is absent from the local Python
  environment).
- Archive contents beyond the central directory (intentionally not extracted).
- Independent digest comparison (none supplied).

---

## Private evidence artifacts

The detailed archive digest, supplemental metrics, manifest, and run logs are in
the private working workspace. They are not committed and must not be published.

The public repository contains only generic implementation code, synthetic
tests, and repository-safe documentation.
