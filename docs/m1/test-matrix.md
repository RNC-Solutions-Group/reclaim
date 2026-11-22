# M1 — Test and Acceptance Matrix

## 1. Automated validation

Run from the repository root:

```bash
python -m unittest discover -s tests -t .
python -m compileall -q src tests
```

Latest results on Windows 11 / NTFS / Python 3.13:

- **87 tests, all passing.**
- `compileall` passes.
- No real personal data is used; all fixtures are generated under temporary
  directories.

## 2. Test files

| File | Coverage |
| --- | --- |
| `tests/test_destination.py` | Path traversal, Windows reserved/invalid names, path/component length, case/Unicode keys, duplicate targets, prefix conflicts, existing targets, missing root, capacity, junction/reparse root, no-overwrite publication |
| `tests/test_planning.py` | Deterministic plans, duplicate policies, same-name/different-content, merged-template conflicts, overrides and digest changes, zero-byte override, source change before authorization, dry-run no-write, tampered plan rejection, authorization required |
| `tests/test_planning.py` (execution) | Copy/verify/reconcile, existing destination, source change, resume, destination tamper verification, destination race after preflight, stale temp cleanup, same-metadata content change, verification failure reconciliation, case collision, unsafe template |
| `tests/test_m1_cli.py` | End-to-end CLI: plan → authorize → execute → reconcile |
| `tests/test_store.py` | Schema v1→v3 migration, legacy cache non-reuse, source-root cache invalidation |
| `tests/test_config.py`, `test_scanner.py`, `test_hashing.py`, `test_paths.py`, `test_policy.py`, `test_analysis.py` | CP1 foundation regressions |

## 3. Requirement coverage

| M1 requirement | Evidence |
| --- | --- |
| Minimum schema evolution | Schema v3 tables + migration test |
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
| Automated tests/static checks | PASS (87 tests + compileall) |
| Documentation updated | PASS (M1 docs + README) |

## 5. Not verified / limitations

- macOS and Linux execution were not run; destination path rules are tested as
  policy logic, not on those filesystems.
- No real personal-data migration or real-data dry run.
- Empty directories are not migrated.
- Extended-length Windows paths, ACLs, ADS, sparse files, and hard-link identity
  are not supported.
- The no-overwrite fallback when `os.link` is unavailable is tested only through
  the normal path here; the fallback has a narrow reservation window.
- Concurrent external writers are modeled by destination-race tests, not by a
  stress/race harness.
- Full-disk behavior is checked at preflight via capacity; an actual disk-full
  mid-copy is not exercised.
- Cross-process interruption (kill/power loss) is represented by the paused/limit
  execution path; OS-level crash recovery is not tested.
- Package build/install is not verified in this environment (`setuptools`
  absent); tests run from the source tree.
- M1 does not include content extraction, classification, naming intelligence,
  GUI, SLM, or external AI.
