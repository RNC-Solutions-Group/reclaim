# M1 — Migration Spine and Plan Contract

**Status:** M1A corrective hardening complete on `feat/m1`; operator review
pending. 113 tests pass. See the
[test matrix](test-matrix.md) for acceptance evidence and explicit limitations.
**Scope:** Deterministic planning, preview, authorization, journaled copy,
verification, resume, and reconciliation using synthetic data only.

M1 does not perform content extraction, classification, filename intelligence,
GUI work, SLM work, external AI, or real-data migration.

## Documents

1. [Architecture and execution model](architecture.md)
2. [Data model](data-model.md)
3. [Operator guide](operator-guide.md)
4. [Test and acceptance matrix](test-matrix.md)

## What M1 establishes

- An immutable, content-addressed migration plan built from inventory hashes,
  policy recommendations, duplicate policy, and explicit overrides.
- A destination validator independent of the host platform for path safety,
  reserved names, length, whole-path case/Unicode equivalence, duplicates,
  existing files/directories, links, redirection, and capacity.
- Explicit plan-digest authorization; changed plan content invalidates
  authorization.
- Journaled, resumable, copy-only execution with no implicit overwrite or
  deletion; atomic hard-link publication with fail-closed unsupported handling.
- Source metadata and content revalidation before copying.
- Post-copy SHA-256 verification; missing/corrupt destinations cannot retain
  verified status.
- Reconciliation that distinguishes accounting completeness (`balanced`) from
  successful migration (`successful`).
- CLI commands with stable nonzero exit codes for failure, incomplete,
  verification, and reconciliation outcomes.
- Synthetic end-to-end, fault-injection, resume, and CLI tests.

## Non-goals

- Real personal-data migration.
- Document extraction or semantic classification.
- Intelligent naming or folder-taxonomy design.
- Advanced plan editing or a GUI.
- Empty-directory preservation, extended-length path support, or platform-
  specific metadata/ACL preservation. These are documented limitations, not
  silent behavior.
