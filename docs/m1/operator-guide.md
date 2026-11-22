# M1 — Operator Guide

This guide uses **synthetic directories only**. M1 is not authorized for a real
personal-data migration.

## 1. Prepare a synthetic project

```text
demo/
  source-a/Documents/one.pdf
  source-a/Documents/unique.txt
  source-b/Documents/two.pdf      # identical to one.pdf
  destination/                    # empty destination root
  project.toml
```

`project.toml`:

```toml
[project]
name = "m1-demo"
workspace = "./workspace"
database = "db/inventory.sqlite3"
reports_dir = "reports"

[[sources]]
id = "source-a"
label = "Source A"
path = "./source-a"
priority = 100

[[sources]]
id = "source-b"
label = "Source B"
path = "./source-b"
priority = 200
```

Run the existing inventory pipeline first:

```bash
reclaim init   --config demo/project.toml
reclaim scan   --config demo/project.toml
reclaim hash   --config demo/project.toml
reclaim status --config demo/project.toml
```

M1 requires a completed run with hashes. Media/deferred or hash-error entries
become conflicts unless an explicit override makes them copyable.

## 2. Build a plan

```bash
reclaim plan \
  --config demo/project.toml \
  --destination demo/destination \
  --template "{source_id}/{rel_path}" \
  --duplicate-policy prefer_one
```

Output includes:

- `plan-<id>` and its SHA-256 digest;
- counts by disposition and total copy bytes;
- conflict/global-issue counts;
- the first 20 source → destination mappings;
- private plan manifest and NDJSON item export under `workspace/plans/`.

**No destination files are written by `plan`.**

The `--destination` value is retained as the **selected path** and validated
separately from its canonical resolution. A symlink/junction destination root or
redirected ancestor is rejected; do not select a link as the destination.

### Duplicate policies

| Policy | Meaning |
| --- | --- |
| `prefer_one` | Keep one canonical copy; other exact occurrences are provenance-only `duplicate_omit` |
| `keep_all` | Copy every occurrence; the destination template must avoid collisions |
| `fail` | Exact duplicates become conflicts requiring explicit resolution |

### Destination template tokens

- `{source_id}` — source identifier;
- `{rel_path}` — source-relative path including filename;
- `{name}` — filename only;
- `{parent}` — source-relative parent directory.

The default `{source_id}/{rel_path}` avoids cross-source collisions. `{rel_path}`
merges sources and intentionally tests collision handling.

### Target platform

```bash
--target-platform windows|macos|linux
```

Path rules are applied for the target, not inferred from the host. The default
is the host platform. M1 does not support extended-length Windows paths; the
default budget is 240 characters.

## 3. Preview

```bash
reclaim preview --config demo/project.toml --plan plan-<id> --limit 100
```

Shows status, digest, counts, global issues, and mappings. Preview is read-only.

## 4. Overrides

Overrides are explicit operator decisions. A recommendation alone is never
authorization.

```bash
reclaim override add \
  --config demo/project.toml \
  --source source-a \
  --path Documents/unique.txt \
  --disposition copy \
  --reason "retain this file despite the policy review flag"

reclaim override add \
  --config demo/project.toml \
  --source source-a \
  --path Documents/archive \
  --directory \
  --disposition exclude \
  --reason "archive collection excluded by operator"

reclaim override list --config demo/project.toml
reclaim override remove --config demo/project.toml --id ovr-...
```

Precedence: file override > longest directory prefix > policy recommendation.
After changing overrides, rebuild the plan; the digest changes and prior
authorization does not apply to the new plan.

Valid override dispositions: `copy`, `exclude`, `defer`, `duplicate_omit`.
`copy` still requires a valid hash.

## 5. Authorize the exact plan

```bash
reclaim authorize \
  --config demo/project.toml \
  --plan plan-<id> \
  --digest <64-hex-digest> \
  --actor operator \
  --note "synthetic M1 acceptance"
```

Authorization is refused when:

- the digest does not match;
- the stored plan content no longer matches its digest;
- the plan has conflicts or global issues;
- source metadata/root identity changed since planning;
- the destination root is no longer valid.

No destination files are written by authorization.

## 6. Dry run

```bash
reclaim execute \
  --config demo/project.toml \
  --plan plan-<id> \
  --digest <digest> \
  --dry-run
```

Dry run reports would-copy/conflict counts, missing sources, required bytes,
free bytes, and plan conflicts. **It performs no destination writes.** It may be
run before authorization.

## 7. Execute

```bash
reclaim execute \
  --config demo/project.toml \
  --plan plan-<id> \
  --digest <digest>
```

Execution:

1. revalidates sources and the **raw selected destination path**;
2. checks free space;
3. creates/reuses a journaled execution;
4. re-hashes each source and compares the planned digest;
5. copies to a temp file, verifies, and publishes with atomic hard-link
   no-overwrite;
6. re-hashes the destination and records `verified`, `failed`, or `conflict`;
7. prints an execution id and summary.

Failed/conflicting items do not stop other items. The final status is `complete`,
`complete_with_failures`, or `paused`.

Publication requires `os.link`. Filesystems without hard-link support fail with
`PUBLICATION_UNSUPPORTED`; there is **no overwrite-capable fallback**.

### Resume

Re-run the same `execute` command after an interruption. A `paused` or crashed
`running` execution is resumed. Verified items are revalidated (missing/changed
destinations become failures). Failed items are retried by default; use
`--no-retry-failed` to leave them failed.

An existing destination is accepted only if this execution has provenance: the
item was already `verified`, or a committed `DEST_PUBLISHED` event exists for
this execution. A matching hash alone is **not** accepted; it produces
`DESTINATION_PREEXISTING`.

`--max-items N` is available for fault-injection/testing; it pauses after N
items and is intentionally useful for resume tests.

## 8. Verify and reconcile

```bash
reclaim verify-execution --config demo/project.toml --execution exec-<id>

reclaim reconcile \
  --config demo/project.toml \
  --execution exec-<id> \
  --out demo/reconciliation
```

Verification re-hashes every item recorded `verified` (and retries prior
verification failures). A missing file, unreadable file, or hash mismatch moves
the item to `failed`, persists an error code/event, sets the execution status to
`verification_failed`, and makes subsequent reconciliation report `failed`. A
restored matching file can be recovered on a later verification run.

Reconciliation writes `reconciliation-<execution>.csv` and `.json`. Every plan
item receives one outcome:

- `copied_verified`
- `duplicate_omitted`
- `excluded`
- `deferred`
- `plan_conflict`
- `failed`
- `conflict`
- `incomplete`

Two independent fields matter:

- **balanced**: every plan item has exactly one outcome.
- **successful**: every required copy is `copied_verified`, with no blocking
  outcome. Authorized exclusions, deferrals, and duplicate omissions are
  legitimate terminal outcomes.

`balanced: true` is accounting completeness, not migration success.
`assessment` is `success`, `failed`, or `incomplete`.

## 9. Expected safety behavior

| Situation | Result |
| --- | --- |
| Destination exists before execution | Conflict; no overwrite |
| Destination exists with a matching hash but no execution provenance | Conflict; not adopted |
| Destination appears after preflight | Conflict; no overwrite |
| Destination is a link/reparse root or redirected ancestor | Planning/authorization/execution refuses |
| Source changed after planning | Authorization/execution refuses; no copy |
| Source content changed with same size/mtime | Per-item source hash failure; no copy |
| Plan row tampered in DB | Digest mismatch; refuses |
| Wrong authorization digest | Refuses |
| Hard-link publication unsupported | Item fails closed; no fallback |
| Insufficient capacity | Refuses before writes |
| Failed item | Recorded; other items continue |
| Interrupted execution | Resume without recopying verified items |
| Destination deleted/corrupted after execution | Verification fails; reconciliation reports failure |

## 10. Exit behavior

| Code | Meaning |
| ---: | --- |
| 0 | Success: clean/executable plan, completed execution, PASS verification, or successful reconciliation |
| 2 | Usage/configuration/service error |
| 3 | Plan blockers; execution completed with failures/conflicts; verification of a failed execution |
| 4 | Execution/reconciliation incomplete or paused |
| 5 | Verification failure |
| 6 | Reconciliation balanced but unsuccessful, or otherwise failed |

`plan` returns 3 when it creates a plan with conflicts/global issues.
`execute --dry-run` returns 3 for a blocked plan and 0 for a clean executable
plan. `execute` returns 0 only for `complete`; 3 for `complete_with_failures`;
4 for `paused`. `verify-execution` returns 5 for verification failure and 4 for
an incomplete execution. `reconcile` returns 0 only when `successful` is true.

Machine-readable summaries remain in the JSON output; callers should use exit
codes plus the JSON fields, not prose parsing.

## 11. Privacy and scope

- M1 operates only on the configured sources and destination.
- Plans, journals, and reconciliation reports contain private paths; keep them
  in the private workspace, not in Git.
- Do not run M1 against a real personal collection without explicit operator
  authorization.
- M1 does not read document content, classify, rename, or extract archives.
