# CP1 — Decisions Required Before CP2/CP3

**Status:** Partially resolved. D1 and D2 were resolved by the operator on
2026-11-22; the remaining decisions are retained for planning.
**Scope:** These decisions are not implementation details; each changes what
"correct" means for the migration. They are recorded here without private paths
or filenames. The mapping to specific collection labels is in the private CP1
audit.

Legend: **Resolved** = operator decision recorded; **Blocking** = cannot proceed
without it; **Planning** = required before a migration plan is generated;
**Release** = required before publication.

## Operator resolution log (2026-11-22)

- **D1 resolved.** The six source directories are six intentionally separate
  collections. The two MacBook collections are distinct sources and must remain
  separate.
- **D2 resolved.** The operator manually recovered the additional source content
  relevant to the migration. The prior collector's cloud/path failures do not
  require a new recovery project and are accounted for within the available
  dataset. The staged set is accepted as the working source of truth. The
  historical coverage caveat is retained as audit evidence, not as an open
  investigation.

These resolutions supersede the "unknown/blocking" framing in the historical CP1
text below. The CP1 audit evidence itself is preserved unchanged.

---

## Decision summary

| ID | Decision | Default recommendation | Needed by |
| --- | --- | --- | --- |
| D1 | Source set and identity | **Resolved:** six distinct sources; two MacBook collections remain separate | Resolved |
| D2 | Source completeness / re-collection | **Resolved:** staged set accepted; prior gaps accounted for by operator | Resolved |
| D3 | Canonical-copy policy | Source priority, then path order; never timestamps; per-group override | **Blocking (M1)** |
| D4 | Media phase boundary | **Accepted:** defer all image/video/audio and packaged media to the media phase | Accepted |
| D5 | Destination taxonomy | Collection-first, category-second; no extension flattening | Planning (M5) |
| D6 | Duplicate treatment | Keep one canonical occurrence; record all provenance | Planning (M1) |
| D7 | Archives and installers | Retain archives for review; no automatic installer exclusion | Planning (M3/M5) |
| D8 | Destination root and capacity | Dedicated volume with ≥2× plan size free | Planning (M1 dry run) |
| D9 | Timestamp authority | Evidence only; never authoritative | Accepted |
| D10 | License | Apache-2.0 (subject to legal approval) | Release |
| D11 | GUI toolkit | PySide6/Qt | CP6 / M2 |
| D12 | Supported OS matrix | Windows 11 + macOS 13+ + Ubuntu 22.04+ | Release |
| D13 | Prior-tool artifacts in source root | Exclude, do not delete, document | Accepted |
| D14 | Project database protection | OS permissions + user-managed encrypted volume | CP6 / M2 |
| D15 | Cross-source canonical ownership | Review per group; no global machine priority yet | Planning (M1) |
| D16 | Media target format | Decide in media checkpoint (M8/media phase) | Media phase |

---

## D1 — Source set and identity

> **Resolved 2026-11-22:** Six separate, intentional collections. The two
> MacBook collections are distinct sources and remain separate. Do not reopen
> this as an investigation.

**Context.** The brief expected five collections; the staged set contains six.
Two are MacBook-named (one media-heavy, one small personal/coursework set), and
their relationship was initially unknown. A prior-tool reports directory also
sits in the source parent but is not a collection.

**Options.**

1. Treat all six as independent sources and preserve separate provenance.
2. Merge the two MacBook collections into one logical source.
3. Drop one as a duplicate/older copy.

**Recommendation:** Option 1 until the operator confirms the machine identity
and relationship. Merging loses provenance; dropping requires proof that one is
obsolete. The engine models sources separately, so a later merge is possible.

**Consequence of wrong choice:** Duplicate groups and "unique data" totals could
be attributed to the wrong machine; a real source could be omitted.

---

## D2 — Source completeness and re-collection

> **Resolved 2026-11-22:** The operator manually recovered the relevant
> additional content. The prior collector's cloud/path gaps are accounted for
> within the available dataset and do not require a recovery project. The staged
> set is accepted as the working source of truth; the historical coverage caveat
> remains as audit evidence.

**Context.** The staged collections are composite second-generation copies. A
prior collector copied only `Downloads` for two collections, skipped 12 reparse
directories per run (including `OneDrive`), and failed to copy 13 files. The
original profiles and staging destination no longer exist. Four collections
have no provenance report at all.

**Options.**

1. Re-collect from the original machines/drive, preserving timestamps and
   avoiding source-root artifacts.
2. Recover only the known gaps (13 failures, OneDrive content) if originals are
   available.
3. Accept the staged collections with documented `coverage_status = partial/unknown`.

**Recommendation:** Determine availability first. If originals are available,
re-collect or recover gaps before planning. If not, accept the staged set
explicitly and carry the coverage caveat into every report. Do not describe the
inventory as a complete machine archive.

**Consequence:** A plan built on incomplete sources can silently omit
irreplaceable data. This is the highest-risk decision in the project.

---

## D3 — Canonical-copy policy

**Context.** CP1 produced 1,605 exact-duplicate groups. The default heuristic
(equal source priority, then shallowest path, then shortest/lexicographic path)
selected the same source for 1,120 groups, mostly because its paths are
shallower. That is not evidence that it is the best copy.

**Options.**

1. Source-priority list (operator orders machines by authority), then path order.
2. Preserve the occurrence with the most complete surrounding directory context.
3. Always retain the oldest/newest timestamp (not recommended).
4. Ask the user per group (too much work for 1,605 groups, but good for
   high-value groups).

**Recommendation:** Option 1 with an explicit, configurable priority list, plus
per-group override and a "review largest groups first" workflow. Never use
timestamps as authority. Record `canonical_reason` for every group. Treat the
CP1 heuristic as provisional only.

**Consequence:** The canonical choice determines which path and metadata survive
into the destination. A wrong default can silently discard the better copy's
folder context even though the bytes are identical.

---

## D4 — Media phase boundary

> **Accepted 2026-11-22:** The alpha is document-focused. All image/video/audio
> and packaged media are deferred to the media phase. Media may be inventoried
> and hashed for audit, but it is not included in the first migration plan.

**Context.** 47.99 GB (55% of bytes) is image/video/audio, including a 35.22 GB
packaged photo library with 72,238 internal files. The CP1 amendment excludes
photographs from the first document-focused migration.

**Options.**

1. Defer all image/video/audio, including loose photos and videos.
2. Defer only packaged libraries; include loose photos in the document plan.
3. Hash and deduplicate all media now, but do not migrate it yet.

**Recommendation:** Option 1 for the first migration. Keep the library intact
and preserve loose media in a separate media staging area or leave it in place
until CP5. Hashing loose media for audit (as CP1 did) is acceptable; hashing
library internals is deferred. Do not convert or flatten the library in CP1–CP4.

**Consequence:** Mixing media into the document plan would roughly triple its
size and force album/metadata decisions that the document workflow cannot
represent.

---

## D5 — Destination taxonomy

**Context.** Existing folders already encode meaningful collections: military
course folders, clinical year, Red Cross certification, school, personal
administration. A pure extension/category sort would split these.

**Options.**

1. **Collection-first:** preserve each coherent source folder as a unit under a
   small set of top-level categories; attach category metadata.
2. **Category-first:** sort every file into Professional/Education/Military/
   Personal, flattening source folders.
3. **No taxonomy:** mirror the source tree exactly.

**Recommendation:** Option 1. Proposed top level:
`Documents/Professional-Clinical`, `Documents/Military`, `Documents/Education`,
`Documents/Personal-Admin`, `Downloads-Review`, `Media` (deferred),
`Needs-Review`, `Excluded` (manifest only). Keep coherent collections as
subfolders; do not split by extension. Option 3 is a safe fallback if taxonomy
review stalls.

**Consequence:** Category-first can destroy the context that makes course packs
usable; no-taxonomy preserves clutter and duplicates.

---

## D6 — Duplicate treatment

**Context.** 7.29 GB is redundant. Every occurrence is preserved in inventory.
The destination plan must decide what to copy.

**Options.**

1. Copy one canonical occurrence per group; record every other occurrence as
   omitted-with-provenance.
2. Copy all occurrences into a `Duplicates/` tree (safe but 7.29 GB larger and
   recreates clutter).
3. Copy none by default and require review (risks losing unique context).

**Recommendation:** Option 1 with review of the largest groups. "Duplicate"
means *omit the extra copy from the destination*, never delete from source.
Record canonical path, group id, and all source locations in the manifest.

**Consequence:** Option 2 wastes space; option 3 can omit a copy that had better
metadata/context.

---

## D7 — Archives and installers

**Context.** 131 archives / 8.58 GB (74 ZIP / 6.98 GB) and 52 installers /
2.55 GB are recommended `needs_review`, not junk. CP1 did not open archives.

**Options.**

1. Retain archives in the plan pending deliberate inspection; do not
   auto-exclude installers.
2. Exclude all installers by default; retain archives.
3. Exclude both by default; user can restore individually.

**Recommendation:** Option 1, with a separate opt-in archive inspection step
(list central directory, detect duplicate content) before any exclusion. Once
inspected, obsolete installers can be excluded by profile rule with per-file
restore. Never auto-delete.

**Consequence:** An installer may be the only copy of a tool the user needs; an
archive may contain unique course material or may duplicate an extracted
folder. Storage impact of keeping them is modest compared with media.

---

## D8 — Destination root and capacity

**Context.** The document-focused working set is ≈39.3 GB before selection; a
likely first library is 22–33 GB. Media adds ≈48 GB if included later.

**Options.**

1. Dedicated external volume with at least 2× the planned size free.
2. Free space on the system drive.
3. Network location.

**Recommendation:** Option 1. Require the destination to be distinct from every
source and from the working workspace, validate free space at plan time, and
keep the reserved final destination untouched until execution is authorized.
Avoid network paths for the first execution (latency, partial writes,
permissions).

**Consequence:** Insufficient space mid-copy creates partial state; a
destination inside a source risks recursive copies and corruption.

---

## D9 — Timestamp authority

**Context.** One collection has all timestamps reset to copy time; Windows
creation times are often copy times. Name conflicts include real revisions.

**Recommendation:** Treat timestamps as **evidence only**. Never automatically
choose a "newer" or "older" file as authoritative. Surface date differences in
review; let the operator decide.

**Consequence:** Reversing this would silently pick the wrong revision in
hundreds of name-conflict cases.

---

## D10 — License

**Context.** The project is intended to be open source, but no license has been
approved. The packaging metadata is intentionally marked "pending".

**Options.**

1. Apache-2.0 — explicit patent grant, enterprise-friendly, common for tools.
2. MIT — simplest permissive license.
3. MPL-2.0 — file-level copyleft.

**Recommendation:** Apache-2.0, subject to operator/legal approval. Do not
publish a release or accept contributions until the license and contributor
terms are approved.

**Consequence:** Without a license, the code is all-rights-reserved by default;
downstream use is legally unclear.

---

## D11 — GUI toolkit

**Context.** The eventual desktop app must be usable without Python or a
terminal, support large review tables, a preview tree, and advanced edit.

**Options.**

1. PySide6/Qt — mature model/view, single language, native packaging.
2. Tauri + web UI — smaller binaries, adds Rust/web toolchain.
3. Electron — large runtime, not recommended.

**Recommendation:** PySide6/Qt, built against the same application services as
the CLI. Defer the decision until CP6 if a web-first requirement emerges.

**Consequence:** The UI choice must not leak into the core; a wrong choice is
recoverable if the service contracts stay stable.

---

## D12 — Supported OS matrix

**Context.** CP1 ran only on Windows 11/NTFS. macOS and Linux behavior differs
in case sensitivity, Unicode normalization, timestamps, links, and reserved
names.

**Recommendation:** Declare support for Windows 11, macOS 13+, and Ubuntu
22.04+ only after CI runs the synthetic suite on each. Until then, document
Windows as tested and the others as unverified.

**Consequence:** Claiming cross-platform support without tests is a support and
data-safety risk.

---

## D13 — Prior-tool artifacts in the source root

**Context.** A prior collector wrote 24 report files (≈3.7 MB) inside the source
collection root. They are not original data.

**Recommendation:** Exclude the directory from the inventory and migration;
leave it untouched; document it as a prior-workflow artifact. Do not delete it
as part of CP1.

**Consequence:** Migrating it would copy private manifests into the clean
library; deleting it would modify the source.

---

## D14 — Project database protection

**Context.** The SQLite database contains private paths, hashes, and decisions.

**Options.**

1. OS file permissions + user-managed encrypted volume.
2. Application-level encryption (SQLCipher) with a passphrase.
3. No additional protection.

**Recommendation:** Option 1 for CP2–CP4. Consider option 2 only if the product
will store projects on shared machines; it adds dependency and key-management
complexity.

**Consequence:** A lost laptop exposes the inventory; encryption is not a
substitute for physical separation from the repository.

---

## D15 — Cross-source canonical ownership

**Context.** 1.66 GB is shared between two collections, mostly course materials;
most other cross-source overlap is negligible.

**Recommendation:** Do not assign a global "best machine" yet. Review the
largest shared groups with directory context; prefer the copy whose surrounding
collection is more complete. Use source priority as a tie-breaker only after the
operator reviews.

**Consequence:** A global priority could systematically discard the better
folder context on one machine.

---

## D16 — Media target format

**Context.** The media phase must decide whether the destination is an Apple
Photos library, a folder-based media library, or an archival export.

**Recommendation:** Defer to CP5 after inventorying albums, edits, and metadata.
CP1 only preserves the source libraries intact.

**Consequence:** Choosing a target before understanding album/edit metadata
risks losing organizational history.

---

## Decision log template

When a decision is made, record:

```text
Decision:
Date:
Owner:
Choice:
Rationale:
Affected collections / rule ids:
Reversibility:
Follow-up:
```

Decisions should be stored with the project configuration (outside the
repository if they reference private paths) and referenced by the plan that
depends on them.
