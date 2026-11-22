# CP1-B — Recommended Roadmap

> **Historical checkpoint document.** This was the CP1 roadmap. It is
> superseded by the product-level roadmap in
> [`docs/product/roadmap.md`](../product/roadmap.md). It is retained for audit
> history.

**Status:** Proposal for operator review. CP1 is complete when its deliverables
are accepted; this document sequences what follows and what must be decided
before each step.

---

## 1. Sequencing logic

The order is driven by risk, not by feature appeal:

1. **Establish what the sources actually are.** A perfect plan over an
   incomplete source set is worse than no plan.
2. **Make recommendations reviewable.** Users must be able to see and change
   dispositions before a destination tree exists.
3. **Build and validate a plan.** Planning is where path, collision, and policy
   errors are cheap to fix.
4. **Execute only after explicit authorization.** Copying is the first
   irreversible-ish action; it gets its own checkpoint and its own tests.
5. **Reconcile media separately.** Packaged photo libraries have different
   structure, metadata, and near-duplicate problems.
6. **Add the GUI after the contracts are stable.** A GUI built on unstable
   services becomes a second implementation of the business logic.
7. **Package and publish last.** Distribution amplifies every unresolved
   privacy or licensing issue.

No checkpoint may silently absorb the next one.

---

## 2. Checkpoint overview

| Checkpoint | Objective | Primary outputs | Gate to proceed |
| --- | --- | --- | --- |
| **CP1** (current) | Understand the data; propose the foundation | Read-only inventory engine, audit, architecture, decisions | Operator accepts audit and source scope |
| **CP2** | Resolve source truth; persist decisions | Source-coverage record, analyses/recommendations/overrides schema, CLI review | Operator confirms sources and canonical policy |
| **CP3** | Plan and preview | Destination templates, plan builder, validator, preview, plan export | Plan balances and validates with no unresolved collisions |
| **CP4** | Execute and verify | Journaled copy executor, resume, reconciliation | Fault-injection tests pass; operator authorizes a dry run |
| **CP5** | Media reconciliation | Photo-library inventory, media hashing, near-duplicate review, album metadata | Media plan reviewed separately |
| **CP6** | Desktop GUI MVP | PySide6 app: sources, review, preview, execute | Usable by a non-technical operator |
| **CP7** | Advanced edit | Folder create/rename/nest/move, undo, validation | Plan edits cannot corrupt source or bypass validation |
| **CP8** | Distribution | Standalone builds, CI matrix, docs, license, release | Signed artifacts, privacy review, license approved |

CP5 can run in parallel with CP3/CP4 if the operator wants media analysis
earlier, but media must not block the document-focused migration.

---

## 3. CP2 — Source completeness and review foundation

### 3.1 Goals

- Establish whether the staged collections are the intended source of truth.
- Record coverage limitations instead of implying completeness.
- Persist analyses, recommendations, and overrides.
- Give the operator a CLI review loop.

### 3.2 Work items

1. **Source-coverage investigation**
   - Document how the collections were produced, including any prior collector
     that skipped junctions/cloud folders.
   - Compare current counts with the prior collector's manifests.
   - Identify OneDrive/cloud remnants and decide whether originals are still
     accessible to re-collect.
   - Record a `coverage_status` and note per source.
2. **Schema v2**
   - Add `source_probes`, `analyses`, `recommendations`, `duplicate_groups`,
     `duplicate_members`, `overrides`.
   - Add `sources.coverage_status`, `sources.volume_id`.
   - Add a migration command and migration test.
3. **Review services**
   - `list_recommendations(filter, page)` with counts by disposition/source/kind.
   - `set_file_override(entry_id, disposition, reason)`.
   - `set_directory_override(source_id, prefix, disposition, reason)`.
   - `revert_override(override_id)`.
   - `resolve_effective(analysis_id)` summary.
4. **CLI review**
   - `reclaim review summary`
   - `reclaim review list --disposition needs_review --source …`
   - `reclaim review override --entry … --disposition retain --reason …`
   - `reclaim review overrides`
   - `reclaim review revert …`
5. **Sanitized public documentation**
   - Generic policy/profile examples only; no real paths or filenames.

### 3.3 Acceptance criteria

- Every inventoried file has a persisted recommendation with rule id and reason.
- An override round-trips: it changes the effective disposition, appears in the
  audit history, and can be reverted.
- No source file is modified by any review command (fingerprint check).
- Source coverage is explicitly reported as complete/partial/unknown.
- Schema migration from CP1 works on a copy of the CP1 database.

### 3.4 Decisions required before CP2 closes

- Source identity/naming (including the two MacBook collections).
- Whether to re-collect from original machines or accept the staged copies.
- Canonical-copy selection policy.
- Destination root and capacity.
- Media phase boundary.

---

## 4. CP3 — Plan, preview, and validation

### 4.1 Goals

Build a reviewable, versioned destination plan without writing any destination
file.

### 4.2 Work items

1. **Destination policy**
   - TOML templates mapping categories/directories to destination paths.
   - Per-project destination root validation and capacity check.
   - Explicit collision policy (`fail` or recorded disambiguation).
2. **Plan builder**
   - Resolve effective dispositions.
   - Choose canonical occurrences per duplicate group policy.
   - Assign destinations (template + overrides).
   - Produce `plans`, `plan_items`, `plan_edits`.
3. **Validator**
   - Path traversal, reserved names, invalid characters, trailing dots/spaces,
     case-fold collisions, Unicode normalization, length budget, source overlap,
     duplicate destinations, existing destination conflicts.
   - Block authorization if any retained item is unresolved.
4. **Preview**
   - CLI tree summary and counts by destination/source.
   - Optional HTML report with expandable tree (public-safe renderer, private
     data stays local).
   - Diff between plan versions.
5. **Plan export**
   - `plan.json` manifest + `plan-items.ndjson`, canonical hash.
   - Import/validate for review on another machine without sources.

### 4.3 Acceptance criteria

- A plan for the full inventory can be generated without touching the
  destination.
- Counts reconcile: total occurrences = retained + excluded + duplicate-omitted
  + deferred + failed.
- Every retained item has a unique, valid destination.
- Re-running the planner on the same inputs produces the same plan hash.
- Editing a plan changes the hash and invalidates prior authorization.
- Source fingerprints are unchanged after planning.

---

## 5. CP4 — Journaled execution and verification

### 5.1 Goals

Copy the authorized plan non-destructively, resumably, and verifiably.

### 5.2 Work items

1. **Authorization**
   - `reclaim plan authorize --plan … --hash …` with explicit confirmation.
   - Refuse execution if source fingerprints changed since planning.
2. **Executor**
   - Copy to temp file in destination directory; flush; hash; atomic rename.
   - Never overwrite; handle collisions per plan.
   - Preserve mtime where configured; never preserve source ACLs blindly.
3. **Journal and resume**
   - Append events; materialize item state.
   - Resume after interruption; detect already-verified destinations.
4. **Reconciliation**
   - Emit `reconciliation.csv`/JSON: every source occurrence → outcome.
   - Counts must balance; failures listed with codes.
5. **Fault injection tests**
   - Interrupted copy, full disk, permission denial, destination deleted
     mid-run, source changed mid-run, filename collision.

### 5.3 Acceptance criteria

- Source files are unchanged (fingerprint + sample hash).
- Every copied file matches its planned SHA-256.
- Interrupting at any stage and resuming produces a complete, balanced
  reconciliation.
- No existing destination file is overwritten without an explicit recorded
  decision.
- A dry run (`--dry-run`) reports exactly what would happen without writing.

---

## 6. CP5 — Media reconciliation

### 6.1 Goals

Handle the deferred photo/media collections without losing originals,
albums, or edit relationships.

### 6.2 Work items

- Inventory Apple iPhoto/Photos library structure (originals, modified,
  thumbnails, albums, faces, metadata).
- Hash originals and edited versions; distinguish exact duplicates from
  re-encodes and edits.
- Detect near-duplicates using perceptual hashes (optional dependency) with
  review, not automatic deletion.
- Preserve album/event metadata; plan an export format that a modern Photos
  application can import.
- Define a media-specific review UI/CLI.

### 6.3 Acceptance criteria

- Every media file has a disposition or a documented deferral.
- Originals are preserved; edited versions are linked to originals, not merged.
- Album/event metadata is preserved or explicitly reported as unmappable.
- Media plan is separate from the document migration and reviewed independently.

---

## 7. CP6 — Desktop GUI MVP

### 7.1 Goals

Let a non-technical user complete the migration workflow without Python or a
terminal.

### 7.2 Scope

`Select Sources → Analyze → Review → Organize → Preview → Execute → Verify`

- Source picker with validation and coverage warnings.
- Progress/cancel for long operations.
- Virtualized review table with filters and bulk actions.
- Exclusion review: uncheck to include; selection updates effective counts.
- Preview tree with counts and conflict badges.
- Execute with explicit confirmation and plan hash display.
- Verification results and failure export.

### 7.3 Acceptance criteria

- The GUI can complete a synthetic migration end-to-end.
- Closing/crashing the GUI during execution does not corrupt state; resume works.
- No business logic is duplicated in the GUI.
- All destructive-looking actions require explicit confirmation and explain
  that sources are untouched.

---

## 8. CP7 — Advanced edit

### 8.1 Scope

- Create/rename/nest destination folders.
- Move individual or grouped files.
- Undo/revert edits.
- Preview exact effects before execution.
- Validation after every edit.

### 8.2 Acceptance criteria

- Every edit is recorded in `plan_edits` and reversible.
- Invalid paths are rejected with a specific reason.
- Plan hash changes and prior authorization is invalidated.
- Source files and inventory are never modified.

---

## 9. CP8 — Distribution and release

### 9.1 Work items

- PyInstaller one-directory builds for Windows, macOS, Linux.
- CI matrix: unit/integration tests, lint/type checks, build smoke tests.
- Versioned releases; changelog; reproducible build notes.
- License and third-party notices.
- Privacy review: no telemetry, no network calls, no private fixtures.
- Documentation: user guide, safety model, recovery/resume guide.

### 9.2 Acceptance criteria

- A clean machine can install and run the app without Python.
- The app works offline.
- Test artifacts contain no real data.
- License and contributor terms are approved.

---

## 10. Cross-cutting workstreams

| Workstream | When | Notes |
| --- | --- | --- |
| Automated tests | Continuous | Synthetic only; add fault injection in CP4 |
| Performance benchmarks | CP2/CP3 | 100k-file synthetic benchmark; track scan/hash/plan |
| Cross-platform CI | CP3+ | Windows first (current), then macOS/Linux |
| Documentation | Every checkpoint | Architecture decisions recorded as ADRs |
| Security/privacy review | Before CP6 and CP8 | Network audit, path traversal, log hygiene |
| Accessibility/i18n | CP6+ | Qt supports it; defer translation until UI stable |
| Database migrations | Every schema change | Forward-only with backup and tests |

---

## 11. Recommended immediate backlog

Ordered, small, and independently reviewable:

1. Record CP1 audit findings and operator decisions (this checkpoint).
2. Add `source_probes` + coverage status and re-run the scan metadata (no
   rehash needed).
3. Resolve the duplicate-canonical policy with the operator; implement it as
   data, not code.
4. Add schema v2 and the override/recommendation tables.
5. Implement `review summary/list/override/revert` on the existing CLI.
6. Write the destination validator with a test matrix of hostile names before
   writing the plan builder.
7. Build the plan for a **synthetic** dataset end-to-end before touching the
   real dataset.
8. Only then generate a draft plan for the real inventory.

---

## 12. Risks to the sequence

- **Source uncertainty may force re-collection**, which would invalidate the
  current inventory. Resolve it in CP2 before planning.
- **Canonical-copy disagreement** can stall review; make the default explicit
  and easy to override per group.
- **Media scope** can expand without bound; keep it a separate checkpoint with
  its own acceptance criteria.
- **GUI pressure** may tempt skipping the review/plan CLI. The GUI should consume
  stable services, not replace them.
- **License/legal review** can delay release; start it before CP8, not during.
