# M1 — Architecture and Execution Model

## 1. Components

| Module | Responsibility |
| --- | --- |
| `destination.py` | Host-independent path policy, destination validation, safe directory creation, atomic no-overwrite publication |
| `planning.py` | Analysis snapshot, recommendations, overrides, deterministic plan construction, plan digest, authorization, source revalidation |
| `execution.py` | Preflight, journaled copy, resume, verification, reconciliation |
| `store.py` | Schema v4 tables and migration |
| `cli.py` | `plan`, `preview`, `authorize`, `execute`, `verify-execution`, `reconcile`, `override` |

The modules are application services. The CLI is a thin adapter; a future GUI
can call the same functions.

## 2. Planning flow

```text
inventory run (entries + hashes)
  → analysis snapshot (policy fingerprint)
    → recommendations (policy + duplicate role)
      → active overrides (file > directory > recommendation)
        → plan items (copy / duplicate_omit / exclude / defer / conflict)
          → destination validation
            → immutable plan + digest
```

### 2.1 Analysis and recommendations

`analysis_id` is derived deterministically from:

- source run id;
- policy/types fingerprint;
- tool version.

Recommendations are stored before overrides. An override changes the effective
plan item, not the observed recommendation. This preserves the ADR-003
separation between recommendation and decision.

### 2.2 Overrides

- `file` scope matches one source-relative path.
- `directory` scope matches a path prefix; the longest matching prefix wins.
- Precedence: file override > directory override > profile rule > default.
- Overrides require a reason and have deterministic IDs derived from their
  content.
- Removing an override deactivates it; it is not deleted.

### 2.3 Duplicate policies

| Policy | Behavior |
| --- | --- |
| `prefer_one` (default) | Copy one canonical occurrence; mark other exact occurrences `duplicate_omit`, preserving provenance |
| `keep_all` | Copy every occurrence; destination template must avoid collisions |
| `fail` | Duplicate occurrences become conflicts; operator must resolve |

Exact duplicates require complete SHA-256 equality and non-zero size. Files
that share a name but differ in content are never treated as duplicates.

Canonical selection uses source priority, then shallower/shorter/lexicographic
path. This is a provisional M1 rule; the product-level canonical policy remains
subject to operator review.

### 2.4 Destination template

Tokens: `{source_id}`, `{rel_path}`, `{name}`, `{parent}`.

Default: `{source_id}/{rel_path}`. This namespaces sources and avoids
cross-source collisions during M1. A merged template such as `{rel_path}` is
supported and intentionally exercises collision detection.

Paths use `/`; backslashes are rejected rather than platform-converted.

## 3. Plan identity and immutability

The plan digest is SHA-256 over a canonical JSON body containing:

- plan schema version and tool version;
- source run id and analysis id;
- destination root (canonical), **selected destination path** (raw operator
  selection), template, duplicate policy, target platform, path budget;
- policy fingerprint;
- source metadata snapshot and root identities;
- every plan item and its effective decision/reason;
- global validation issues.

`plan_id = "plan-" + digest[:16]`.

Plans are never edited. A changed override, policy, destination, or item creates
a new plan with a new digest. Authorization is stored per `(plan_id, digest)`.
Authorization, preview, and execution recompute the digest from stored rows; a
tampered plan is rejected.

## 4. Destination validation

Validation is platform-targeted (`windows`, `macos`, `linux`), not host-assumed.

| Check | Behavior |
| --- | --- |
| Absolute paths, drive/UNC prefixes, `..`, empty segments | Rejected |
| Backslash separators | Rejected (ambiguous across platforms) |
| Windows invalid characters / trailing spaces/dots / reserved names | Rejected for Windows targets |
| Component and total path length | Enforced against the plan budget |
| Case-fold collisions | Every component of the relative path, for Windows/macOS targets |
| Unicode normalization collisions | Every component, conservative for Windows/macOS |
| Duplicate plan destinations (including equivalent parent paths) | Conflict |
| File/directory prefix conflict under normalized equivalence | Conflict |
| Existing destination file/directory | Conflict; never overwritten |
| Existing case/Unicode equivalent parent or filename | Conflict |
| Link/reparse destination root or any existing ancestor | Rejected |
| Destination selected path resolves to a different canonical root | Rejected |
| Destination inside source/workspace | Rejected |
| Insufficient free space | Global conflict |
| Missing/unwritable destination | Rejected |

The operator-selected destination path is retained separately from the
canonical root (`destination_selected`). Planning, authorization, and execution
validate the selected path so a symlink/junction cannot be hidden by
canonicalization. Time-of-check/time-of-use limits remain: a parent can be
replaced after validation, so execution revalidates before copying and fails
closed on a changed resolution.

Validation is read-only. Dry-run and preview never create directories, temp
files, or probes in the destination.

## 5. Execution state machine

```text
plan draft
  → authorized (exact digest)
    → execution running
      → item pending → verified
                     → failed
                     → conflict
      → paused (limit/interruption)
        → resume
      → complete | complete_with_failures | verification_failed | verification_incomplete
```

Plan-item dispositions and execution states are distinct:

- Plan: `copy`, `duplicate_omit`, `exclude`, `defer`, `conflict`.
- Execution (copy items): `pending`, `verified`, `failed`, `conflict`.
- Post-execution verification can move a `verified` item to `failed` with
  `DESTINATION_MISSING`, `DESTINATION_UNREADABLE`, or `VERIFY_MISMATCH`.

## 6. Journaled copy

For each copy item:

1. Revalidate source metadata and hash the source; compare with the planned
   digest.
2. Validate destination parent components; create missing directories without
   traversing links.
3. Refuse if the destination exists or appears.
4. Copy to a unique temp file in the destination directory, flush and `fsync`.
5. Hash the temp file; compare with the planned digest.
6. Publish with `os.link` only. `os.link` is the atomic no-overwrite
   primitive. If hard-link publication is unavailable, the item fails with
   `PUBLICATION_UNSUPPORTED`; there is **no overwrite-capable fallback**.
7. Journal a committed `DEST_PUBLISHED` event before final verification.
8. Hash the published destination; compare again.
9. Preserve source mtime best-effort.
10. Record the terminal state and journal event.

The journal (`execution_events`) records start/resume, item start, source
verification, `DEST_PUBLISHED`, item verified/failed/conflict, verification
pass/failure, pause, and completion. Item state is materialized in
`execution_items` for fast reconciliation.

## 7. Resume behavior

- A `paused` or crashed `running` execution is resumed, not duplicated.
- Verified items are revalidated: a missing/changed destination becomes a
  failure, not an assumed success.
- Pending/failed items may be retried; conflicts remain conflicts.
- Stale temp files with the execution/item-specific name are removed before
  reuse.
- A destination that appeared after planning becomes a conflict; it is never
  overwritten.
- An existing destination is adopted only when this execution has provenance:
  the item was already `verified`, or this execution journaled a committed
  `DEST_PUBLISHED` event. A matching hash alone is not provenance and produces
  `DESTINATION_PREEXISTING`.
- Source changes after authorization abort preflight before any writes.

## 8. Verification and reconciliation

- Per-file post-copy verification establishes destination byte integrity.
- `verify-execution` re-hashes every `verified` destination file (and retries
  prior verification failures). Missing files, unreadable files, and hash
  mismatches transition the item to `failed` with a persisted error code and a
  journal event. The execution status becomes `verification_failed`.
- A restored, matching destination can be recovered to `verified` on a later
  verification run; the execution status returns to `complete`.
- `reconcile` joins every plan item with its execution state and emits a CSV +
  JSON summary. Outcomes:
  - `copied_verified`
  - `duplicate_omitted`
  - `excluded`
  - `deferred`
  - `plan_conflict`
  - `failed`
  - `conflict`
  - `incomplete`

**Balanced is not successful.** `balanced` means every plan item has exactly one
accounted-for outcome. `successful` additionally requires:

- every required `copy` item is `copied_verified`;
- no failed, conflict, plan-conflict, incomplete, or unclassified outcome;
- authorized `exclude`, `defer`, and `duplicate_omit` outcomes are accepted as
  policy-terminal.

`assessment` is `success`, `failed`, or `incomplete`. A report where every file
failed can be balanced but is never successful.

Metadata consistency is not byte-level integrity; the distinction established
in CP1 remains in force. Byte integrity here comes from source/destination
SHA-256 comparisons.

## 9. Safety properties

- Copy-only: no source writes, moves, renames, or deletions.
- No implicit overwrite: atomic hard-link publication; fail closed if unavailable.
- Existing destinations are never adopted without execution provenance.
- Explicit authorization: execution requires the exact plan digest.
- Deterministic plan: identical inputs produce the same digest.
- Tamper detection: authorization/execution recompute the digest.
- Source revalidation: metadata before preflight, content before each copy.
- Failure isolation: one item failure does not stop the rest.
- Destination races: pre-copy check plus atomic no-overwrite publication.
- Capacity check before writes.
- Link/reparse refusal on destination selected path and existing components.
- Verification failures invalidate prior success and reconciliation.

## 10. CLI exit semantics

| Code | Meaning |
| ---: | --- |
| 0 | Success: clean plan, completed execution, PASS verification, or successful reconciliation |
| 2 | Usage/configuration/service error |
| 3 | Plan has blockers; execution completed with failures/conflicts; verification of a failed execution |
| 4 | Execution/reconciliation incomplete or deliberately paused |
| 5 | Verification failure |
| 6 | Reconciliation is balanced but unsuccessful (or otherwise failed) |

`plan` returns 3 when it creates a plan with conflicts/global issues.
`execute --dry-run` returns 3 when the plan is blocked, 0 when it is executable.
`execute` returns 3 for `complete_with_failures`, 4 for `paused`, and 0 only for
`complete`. `verify-execution` and `reconcile` use the table above.

## 11. Known M1 limitations

- Empty directories are not planned or recreated.
- Extended-length Windows paths are not supported; the default budget is 240.
- ACLs, alternate data streams, sparse attributes, and hard-link identity are
  not preserved.
- Source file mode/ownership is not preserved; mtime is best-effort.
- macOS/Linux execution is not tested in this environment.
- Destination case-insensitivity is modeled by target platform, not probed.
- Free-space checks are advisory against concurrent writers.
- **Hard-link publication is required.** Filesystems without hard-link support
  fail closed; there is no overwrite-capable fallback.
- Destination redirection validation has time-of-check/time-of-use limits; the
  execution preflight revalidates, but a race after preflight can still surface
  as a publication conflict.
- Existing destinations with matching hashes are not adopted without execution
  provenance; a post-publication crash before journaling may require operator
  review/cleanup.
- No real-data migration is authorized or part of M1.

These limitations are explicit and must remain so in operator documentation.
