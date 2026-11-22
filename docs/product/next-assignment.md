# Recommended Immediate Assignment — M1: Migration Spine and Plan Contract

**Status:** Proposed. Requires operator authorization.
**Predecessor:** M0 CP1 corrective foundation (this assignment).
**Goal:** Deliver a safe, deterministic CLI migration vertical slice before
building the intelligence layer or the GUI.

---

## 1. Why this is first

The product's central promise is a **verified migration**. Today Reclaim can
inventory and analyze but cannot produce a destination. Building extraction and
classification first would create rich evidence with no execution path, no
review model, and no way to validate the product on real data.

M1 establishes:

- the persisted plan/override schema the GUI and intelligence layer will use;
- the destination validator that prevents unsafe paths and overwrites;
- the journaled executor and reconciliation report that make the migration
  trustworthy;
- a stable target for evidence/classification work.

It is also the smallest useful alpha backbone: even with a simple preserve-style
organizer, it can perform a real migration safely.

---

## 2. Scope

### 2.1 Schema v3

Add tables (names indicative):

- `analyses` — frozen analysis snapshots (run, evidence/policy versions).
- `recommendations` — disposition, category, destination, rationale, confidence,
  evidence refs.
- `overrides` — file/directory/pattern/project scope, disposition/destination,
  reason, active/superseded, timestamps.
- `plans`, `plan_items`, `plan_edits`.
- `executions`, `execution_items`, `execution_events`.
- `destination_verifications`.

Add forward-only migration from schema v2 and migration tests.

### 2.2 Plan builder v0

- Preserve the source-relative tree by default.
- Optional simple top-level mapping from a TOML template (e.g., source folder →
  destination collection).
- Exact-duplicate policy: retain one canonical occurrence, record all others as
  duplicate-omitted with provenance.
- Effective disposition resolution:
  `file override > directory override > profile rule > safe default`.
- Every plan item records `decision_source`, `reason`, and evidence/rule ids.

### 2.3 Destination validator

Implement and test:

- path traversal and absolute-path rejection;
- Windows reserved names, trailing spaces/dots, invalid characters;
- path-length budget;
- case-fold collisions (Windows/macOS) and Unicode normalization collisions;
- duplicate destinations within the plan;
- existing destination files (fail by default);
- destination inside a source/workspace;
- package/dependent-file handling (no renaming inside packages).

No silent overwrite. A disambiguator, if enabled, is recorded.

### 2.4 Preview and plan export

- CLI tree summary with counts by source, disposition, and destination.
- Canonical plan hash.
- `plan.json` manifest + `plan-items.ndjson`.
- A preview that is a pure function of the plan; it never writes.

### 2.5 Authorization and execution

- `authorize` pins plan id + hash + destination root.
- `execute --dry-run` reports exactly what would happen without writing.
- Copy to a temp file in the destination directory; flush; SHA-256; atomic
  rename.
- Journal every transition; resume after interruption.
- Refuse execution if source root identity or metadata consistency has changed
  in a way that invalidates the plan (policy decision).

### 2.6 Verification and reconciliation

- Post-copy hash verification per file.
- `reconciliation.csv` mapping every source occurrence to one outcome:
  copied, excluded, duplicate-omitted, deferred, failed.
- Counts must balance.

### 2.7 Tests

- Synthetic end-to-end: scan → plan → preview → execute → verify.
- Interrupted execution at multiple states; resume.
- Destination collision, permission denial, full disk (simulated), source change.
- Long/Unicode/reserved paths.
- Plan hash determinism.
- Source metadata consistency before/after.
- Migration of a v2 project DB.

---

## 3. Explicitly out of scope

- Content extraction or classification.
- Embeddings, SLMs, or external AI.
- GUI work.
- Advanced folder editing/nesting UI.
- Media-library conversion or extraction.
- Consolidation of the real dataset without separate authorization.

M1 may include a **dry run** on the real dataset if the operator approves; it
must not execute a real migration.

---

## 4. Deliverables

1. Schema v3 migration and data-model update.
2. `reclaim plan`, `preview`, `authorize`, `execute` (with `--dry-run`),
   `verify`, `reconcile` CLI commands.
3. Destination validator with a hostile-name test matrix.
4. Journal/resume implementation.
5. End-to-end and fault-injection tests.
6. An operator guide for a synthetic migration and a real dry run.
7. A short design note recording plan semantics, duplicate policy, and
   authorization.

---

## 5. Acceptance gate

M1 is complete when:

- A synthetic multi-source migration completes with **100% reconciliation**.
- Every copied file matches its planned SHA-256.
- Interrupting execution at defined points and resuming produces a complete
  result.
- No destination overwrite occurs without an explicit recorded decision.
- The destination validator rejects every hostile path in the test matrix.
- Re-running the planner on identical frozen inputs produces the same plan hash.
- Sources are metadata-consistent after the run; targeted hashes confirm no
  source content change.
- A real-data **dry run** produces a plan and preview without writing to the
  destination.
- Operator reviews and approves the plan schema and duplicate policy.

---

## 6. Dependencies and decisions

Before coding:

- Approve the plan schema shape (this document + data model).
- Approve the duplicate policy: one canonical occurrence, all provenance
  retained.
- Approve the destination validation rules and collision behavior.
- Confirm the destination root and capacity for the dry run.
- Confirm that a real dry run is authorized; real execution is not.

---

## 7. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Plan schema churn | Keep v0 minimal; version the document; avoid UI-specific fields |
| Resume edge cases | Journal-first state machine; fault injection at every transition |
| Destination filesystem differences | Windows-first test matrix; case/Unicode/path budgets explicit |
| Duplicate canonical choice disputed | Reviewable per group; default rule recorded, overridable |
| Scope creep into GUI/intelligence | Hard exclusion list; M2/M3 are separate gates |

---

## 8. Estimated effort

3–4 engineer-weeks for one engineer familiar with the codebase, including tests
and a real dry run. The estimate excludes GUI and content extraction.

---

## 9. Definition of done

The operator can run a synthetic migration and a real-data dry run from the CLI,
inspect exactly what would be copied, authorize a controlled test migration,
resume it after an interruption, and receive a balanced reconciliation report —
with no source modification and no silent overwrite.

That is the correct foundation for M2 (GUI) and M3 (evidence).
