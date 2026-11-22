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

1. revalidates sources and destination;
2. checks free space;
3. creates/reuses a journaled execution;
4. re-hashes each source and compares the planned digest;
5. copies to a temp file, verifies, publishes without overwrite;
6. re-hashes the destination and records `verified`, `failed`, or `conflict`;
7. prints an execution id and summary.

Failed/conflicting items do not stop other items. The final status is `complete`
or `complete_with_failures`.

### Resume

Re-run the same `execute` command after an interruption. A `paused` or crashed
`running` execution is resumed. Verified items are not recopied. Failed items
are retried by default; use `--no-retry-failed` to leave them failed.

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

Verification re-hashes every item recorded `verified`. A mismatch moves it to
`failed` and records a verification result.

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

`balanced: true` means the outcome total equals the plan-item total.

## 9. Expected safety behavior

| Situation | Result |
| --- | --- |
| Destination exists before execution | Conflict; no overwrite |
| Destination appears after preflight | Conflict; no overwrite |
| Source changed after planning | Authorization/execution refuses; no copy |
| Source content changed with same size/mtime | Per-item source hash failure; no copy |
| Plan row tampered in DB | Digest mismatch; refuses |
| Wrong authorization digest | Refuses |
| Destination is a link/reparse root | Refuses |
| Insufficient capacity | Refuses before writes |
| Failed item | Recorded; other items continue |
| Interrupted execution | Resume without recopying verified items |

## 10. Exit behavior

CLI errors are written to stderr and return exit code `2`. A completed execution
with item failures still exits `0` after reporting the execution status; inspect
the summary and reconciliation before treating it as successful.

## 11. Privacy and scope

- M1 operates only on the configured sources and destination.
- Plans, journals, and reconciliation reports contain private paths; keep them
  in the private workspace, not in Git.
- Do not run M1 against a real personal collection without explicit operator
  authorization.
- M1 does not read document content, classify, rename, or extract archives.
