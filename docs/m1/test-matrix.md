# M1 — Test and Acceptance Matrix

## 1. Automated validation

Run from the repository root:

```bash
python -m unittest discover -s tests -t .
python -m compileall -q src tests
```

Latest results on Windows 11 / NTFS / Python 3.13:

- **113 tests, all passing.**
- `compileall` passes.
- **Packaging smoke:** `pip wheel . --no-deps` produced
  `reclaim-0.2.0-py3-none-any.whl`; installation into a clean virtual
  environment succeeded; `reclaim --version` reported `0.2.0` and the CLI help
  loaded. This environment is Windows.
- No real personal data is used; all fixtures are generated under temporary
  directories.

## 2. Test files

| File | Coverage |
| --- | --- |
| `tests/test_destination.py` | Path traversal, Windows reserved/invalid names, path/component length, whole-path case/Unicode equivalence, equivalent parent/prefix collisions, existing directory aliases, duplicate targets, existing targets, missing root, capacity, junction/reparse root, no-overwrite publication, fail-closed unsupported publication |
| `tests/test_planning.py` | Deterministic plans, duplicate policies, same-name/different-content, merged-template conflicts, overrides and digest changes, zero-byte override, source change before authorization, dry-run no-write, tampered plan rejection, authorization required, redirected destination root/parent, destination change after planning |
| `tests/test_planning.py` (execution) | Copy/verify/reconcile, existing destination, preexisting matching hash, source change/removal, resume, destination tamper/missing/unreadable verification, verification recovery, destination race after preflight, stale temp cleanup, same-metadata content change, publish failure/unsupported, retry, case collision, unsafe template, balanced-vs-successful reconciliation |
| `tests/test_m1_cli.py` | End-to-end CLI: plan → dry-run → authorize → execute → reconcile; exit codes for success, failure, pause/incomplete, verification failure, reconciliation failure, and plan conflicts |
| `tests/test_store.py` | Schema v1→v4 migration, legacy cache non-reuse, source-root cache invalidation |
| `tests/test_config.py`, `test_scanner.py`, `test_hashing.py`, `test_paths.py`, `test_policy.py`, `test_analysis.py` | CP1 foundation regressions |

## 2a. M1A corrective coverage

| Finding | Regression coverage |
| --- | --- |
| M1A-01 verification invalidation | Missing/corrupt/unreadable destination tests; persisted failed state; execution `verification_failed`; reconciliation failure; retry stability; restoration recovery |
| M1A-02 whole-path equivalence | Equivalent parent directories, Unicode parent/child, equivalent prefix conflicts, existing directory alias, case-sensitive distinct paths |
| M1A-03 fail-closed publication | Existing target, forced `os.link` unsupported, cleanup, unchanged target, publication failure/retry |
| M1A-04 destination redirection | Junction root, redirected ancestor, destination changed after planning |
| M1A-05 CLI exit codes | Success, failure, pause/incomplete, verification failure, reconciliation failure, blocked dry-run, blocked plan |
| M1A-06 balanced vs successful | Fully successful, one failed, all failed, conflict, authorized exclusions/deferrals, incomplete execution, post-verification failure |

## 3. Requirement coverage

| M1 requirement | Evidence |
| --- | --- |
| Minimum schema evolution | Schema v3/v4 tables + migration test |
| Analysis/recommendation references | `analysis_snapshots`, `recommendations`, persisted by `build_plan` |
| Operator decisions/overrides | `overrides`, CLI add/list/remove, precedence tests |
| Immutable plans / digest | Canonical plan body, deterministic digest test, tamper rejection |
| Authorization invalidation | New digest after override; old digest rejected |
| Destination path escape | Traversal/absolute tests |
| Symlinks/reparse | Junction root test; parent reparse checks |
| Case/Unicode collisions | `name_keys` tests, duplicate plan targets, case-collision plan |
| Reserved/invalid names | Windows policy tests |
| Path length | Plan path-budget test |
| Existing destination | Conflict test; no overwrite |
| Duplicate target assignments | Plan duplicate tests |
| Unavailable/unwritable destination | Missing-root test; writable check |
| Insufficient capacity | Capacity test + execution preflight |
| Unsupported/ambiguous semantics | Target-platform policy; explicit limitations |
| Duplicate policies | `prefer_one`, `keep_all`, `fail` behavior |
| Same-name/different-content | Never duplicate; both copy or conflict by template |
| Provenance for duplicates | `duplicate_omit` item retains source occurrence and canonical id |
| Preview/authorization | CLI preview, authorize, exact digest |
| No copy without authorization | Authorization-required test |
| Dry run no writes | Dry-run test |
| Copy-only, no source writes | Source snapshot test |
| No implicit overwrite | Existing/race tests |
| Journal durable state | `execution_events` + execution item state |
| Resume | Interrupted execution test |
| Source revalidation | Metadata change before authorization/execution; content change test |
| Destination race | Destination appears after preflight test |
| Post-copy verification | `ITEM_VERIFIED` path + verify test |
| Reconciliation | Balanced report test; verification failure outcome |
| Explicit failure reporting | Failed/conflict outcomes and error codes |
| CLI documentation | `docs/m1/operator-guide.md` |
| Architecture/data model docs | `docs/m1/architecture.md`, `docs/m1/data-model.md` |

## 4. Acceptance checklist

| Gate | Status |
| --- | --- |
| 100% reconciliation on synthetic plans | PASS (test) |
| Interruption recovery/resume | PASS (test) |
| Zero unauthorized overwrites | PASS (tests) |
| Zero source modifications | PASS (snapshot test) |
| Hostile-path/collision matrix | PASS (unit tests) |
| Deterministic plans/hashes | PASS (test) |
| Authorization invalidation on change | PASS (tests) |
| Source change after planning | PASS (tests) |
| Destination byte integrity | PASS (post-copy hash + verify test) |
| Explicit failures | PASS (failed/conflict tests) |
| No destination writes during dry-run | PASS (test) |
| Automated tests/static checks | PASS (113 tests + compileall) |
| Package build/install | PASS (wheel built and installed in an isolated venv; version/help smoke) |
| Documentation updated | PASS (M1 docs + README) |

## 5. Not verified / limitations

- macOS and Linux execution were not run. The host has only a stopped WSL
  `docker-desktop` utility distribution, not a project Linux environment.
  Destination path rules are tested as policy logic, not on those filesystems.
- No real personal-data migration or real-data dry run.
- Empty directories are not migrated.
- Extended-length Windows paths, ACLs, ADS, sparse files, and hard-link identity
  are not supported.
- Atomic publication requires hard-link support. The fail-closed unsupported
  path is tested with a forced `os.link` failure; filesystems without hard links
  cannot execute a migration under M1.
- Destination redirection has time-of-check/time-of-use limits. Junction
  root/parent and post-planning destination changes are tested; an adversarial
  replacement after execution preflight is not stress-tested.
- Concurrent external writers are modeled by destination-race tests, not by a
  stress/race harness.
- Full-disk behavior is checked at preflight via capacity; an actual disk-full
  mid-copy is not exercised.
- Cross-process interruption (kill/power loss) is represented by the paused/limit
  execution path; OS-level crash recovery is not tested.
- Unreadable destinations are tested via a forced read error; OS permission
  configurations were not exhaustively exercised.
- M1 does not include content extraction, classification, naming intelligence,
  GUI, SLM, or external AI.
