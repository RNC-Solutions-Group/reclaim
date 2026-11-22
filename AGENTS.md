# AGENTS.md — Reclaim

## Purpose

Reclaim is an open-source, local-first utility for discovering, inventorying, reconciling, organizing, and migrating files across multiple storage sources. It should help users build a clean, accountable destination library from scattered or overlapping data **without risking the originals**.

This repository contains the **reusable product**, not any particular user's migration. Keep customer names, source layouts, category taxonomies, excluded extensions, and machine-specific paths out of the core implementation.

## Operating Principles

1. **Preservation first.** Source data is read-only by default. Inventory, analysis, and planning must never mutate source files.
2. **Privacy by design.** Processing is local/offline by default. No telemetry, cloud upload, external model/API calls, or remote content inspection without an explicitly authorized feature and user action.
3. **Every file is accountable.** Track source provenance, analysis outcomes, decisions, and final verification. An omitted file needs a documented reason.
4. **Decisions are reversible.** Recommendations are not commands. Exclusion means *omit from the proposed destination*, never delete from the source.
5. **Deterministic core.** Use reproducible algorithms for scanning, hashing, policy evaluation, planning, copying, and verification; reserve human judgment for ambiguity.
6. **Configurable behavior.** Profiles and rules are data, not customer-specific code. Unsupported or ambiguous content must remain reviewable.
7. **Practical engineering.** Prefer small, testable modules and useful reports over speculative frameworks or premature GUI infrastructure.

## Product Workflow

Design for this logical pipeline:

`Register sources → Inventory → Analyze → Recommend → Review/Override → Plan/Preview → Execute → Verify`

The core engine must remain independent of its interfaces. Initial development may use a CLI; a later desktop GUI and standalone binaries should call the same operations rather than duplicate business logic.

Future user-facing capabilities should include:

- Multi-source inventory and original-location provenance.
- SHA-256 exact deduplication; clearly separate exact matches from similarity or revision candidates.
- Configurable retention, exclusion, grouping, and destination rules.
- A navigable exclusion review where users can restore individual files or groups to the migration plan.
- A destination-tree preview, with **optional Advanced Edit** for folder creation, renaming, nesting, and reassignment of planned files.
- Reversible overrides, plan validation, resumable execution, integrity verification, and an auditable final reconciliation.

These capabilities define architectural direction; **do not implement all of them unless the active checkpoint authorizes it**.

## Repository and Data Boundaries

- The Git repository is for source code, tests, synthetic fixtures, non-sensitive documentation, and public-safe examples.
- Real personal files, real inventory databases, unsanitized manifests, extracts, logs containing private paths, and migration outputs **must live outside the repository**. `.gitignore` is not an adequate substitute for physical separation.
- Source, working, and final destination directories must be externally configurable. Resolve and validate their canonical paths before an operation.
- Never assume a fixed drive letter, directory name, operating system, user profile layout, or number of input sources.
- Reject unsafe overlap between source, workspace, and destination paths. Avoid recursive traversal through symlinks, junctions, mount points, aliases, or shortcuts by default; record skipped entries and reasons.
- Do not use actual user documents, filenames, photos, or hashes as test fixtures, examples, or source-control artifacts.
- Before commits or PRs, inspect the diff and staged files for private data, accidental manifests, credentials, and unintended binary artifacts.

## Filesystem and Integrity Requirements

- Model each source occurrence separately, even where multiple paths have identical content. Keep source identity, relative path, metadata, file status, and hash or hash-failure reason.
- Base **exact duplicate** claims on complete content hashes, not filenames, timestamps, or file-size matches alone. Clearly label heuristic findings as candidates.
- Treat timestamps as evidence, not authoritative revision ordering.
- Handle inaccessible files, interrupted reads, changing files, missing volumes, cloud-only placeholders, empty directories, unusual encodings, long paths, and filesystem errors explicitly.
- Account for Windows/macOS/Linux differences, including case sensitivity, Unicode normalization, reserved names, path lengths, permissions, links, and filesystem timestamp behavior.
- Never silently overwrite conflicting destination files. Design execution for journaling, safe resumption, validated destinations, copy verification, and clear failure reporting.
- Destination publication must use an atomic no-overwrite primitive where available; if equivalent guarantees cannot be provided, fail closed rather than degrading to an overwrite-capable rename/replace.
- Post-execution verification must move missing, unreadable, or corrupted destinations out of a successful state and persist the failure. A previously verified item cannot remain "verified" after verification disproves it.
- Reconciliation must distinguish accounting completeness (`balanced`) from successful migration (`successful`); a balanced report with failed/conflicting items is not success.
- CLI commands must expose stable nonzero exit codes for failure, incomplete execution, verification failure, and unsuccessful reconciliation so automation does not parse human-readable prose.
- Do not auto-execute downloaded files, follow internet shortcuts, or unpack untrusted archives as a side effect of discovery. Archive inspection or content extraction must have deliberate limits and appropriate safeguards.

## Classification and Disposition Model

Keep these concepts separate:

- **Observed facts:** immutable or versioned inventory measurements and source provenance.
- **Recommendations:** rules or analysis suggestions, including rationale and confidence where relevant.
- **User overrides:** explicit choices that supersede recommendations for particular files, directories, or a project.
- **Effective plan:** the fully resolved actions and destinations proposed for execution.
- **Execution record:** what actually happened, including verification and unresolved errors.

A representative policy precedence is:

`Explicit file override > Directory override > Profile rules > Safe default`

The default should favor retention or review over silent omission. Do not encode a universal list of “junk” extensions: executable archives, shortcuts, or installers may be essential in some migration profiles. Preserve a reason for every recommended exclusion and make exclusions reviewable.

Plan edits must not mutate the inventory or source filesystem. Validate collisions and unsafe destinations after every plan change. Support future undo/revert behavior without pretending a preview is an actual filesystem operation.

## Engineering Conventions

- Prefer a modular Python core with clear interfaces; justify additional dependencies and keep offline installation/distribution in mind.
- Use durable local storage (SQLite is a strong default) for inventory, provenance, decisions, plans, and execution state; separate observed facts from mutable user decisions at the schema level.
- Keep path resolution, filesystem access, policy evaluation, analysis, persistence, reporting, and presentation independently testable.
- Make long-running scans observable and restartable when feasible. Avoid rehashing unchanged data without checking cache validity.
- Keep configuration, schemas, and public APIs documented and versionable. Avoid hardcoded migration-specific behavior.
- Use structured diagnostics with clear failure categories, but do not leak private content into routine logs or development artifacts.
- Favor targeted changes with meaningful tests. Do not introduce a GUI, service, network stack, or deployment infrastructure merely to anticipate a future roadmap item.

## Testing and Validation

Test primarily with **synthetic** directory trees and generated files. Cover at least:

- Exact duplicates within and across sources, renamed duplicates, and same-name/different-content collisions.
- Empty, hidden, deeply nested, and Unicode paths; case-only name differences; reserved/invalid destination names.
- Symlinks/junctions, attempted recursive paths, and workspace/destination overlap.
- Missing, unreadable, changing, truncated, and interrupted files.
- Policy precedence, individual inclusion overrides, destination edits, and plan recalculation.
- Scan resumption, copy interruption, restart, overwrite prevention, and post-copy hash verification when those features exist.
- Invariant checks proving sources are unchanged by read-only operations.

Document which operating systems and filesystems were actually tested. Never describe unrun tests or unsupported platforms as verified.

## Agent Workflow and Governance

1. Read this file and any more-specific `AGENTS.md` that applies to files you touch. Inspect repository status, branch, existing architecture, and current checkpoint instructions before changes.
2. Respect the active assignment's scope. Discovery does not authorize consolidation; planning does not authorize writes; future roadmap items are not implicit work orders.
3. Keep reusable code and synthetic tests in the repo. Place all private or generated customer work products in a verified external workspace.
4. Work on an appropriate feature branch; make focused commits at meaningful checkpoints. Run relevant tests and static checks, and report their exact outcomes.
5. Push or open a PR when the assigned workflow calls for review. **Do not merge PRs or promote code into protected branches**; the operator retains merge and release authority.
6. If a safety boundary, source identity, destination, or destructive consequence is ambiguous, stop that specific operation and report the blocker. Continue other authorized, safe work where possible.
7. Report findings, evidence, changed files, tests run, limitations, decisions required, and the recommended next checkpoint. Clearly distinguish implemented, tested, proposed, and deferred capabilities.

## Definition of Done for a Checkpoint

A checkpoint is complete when its assigned deliverables are present, testable, documented, and within scope; private data has remained outside source control; source preservation and applicable integrity checks have been validated; limitations are explicit; and the operator has the information needed to approve or revise the next stage.

**Guiding rule:** Preserve originals. Explain decisions. Let users control the outcome.
