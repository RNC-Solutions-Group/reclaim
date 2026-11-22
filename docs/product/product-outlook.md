# Reclaim Product Direction

**Status:** Proposal for operator review.
**Scope:** Product purpose, target users, end state, edition strategy, and
non-goals. This document does not authorize implementation beyond the CP1
corrective work.

---

## 1. Purpose

Reclaim exists to solve a problem that ordinary computer users face after years
of scattered personal computing:

> Important documents, photographs, course materials, professional records, and
> personal archives are spread across multiple computers, backups, Downloads
> folders, cloud redirects, and obsolete directory trees. The user knows the
> data matters but cannot reasonably reconstruct a coherent library by hand.

Reclaim should let a non-technical user select multiple source directories and a
destination, analyze the collection, review the consequential decisions, and
produce an organized, verified destination library **without modifying or
deleting the originals**.

The core value is not deduplication or file copying. Those are necessary
infrastructure. The differentiating value is **document understanding and
coherent organization**: figuring out what the files are, which documents belong
together, where they should live, and which filenames would genuinely help a
human years later.

---

## 2. Target users

**Primary user:** A privacy-conscious individual or household with years of
personal, academic, professional, or administrative data spread across several
machines. They are comfortable using a desktop application but not a terminal.
They do not know what a hash, embedding, or SQLite database is, and they should
not need to.

They care about:

- not losing originals;
- finding documents later;
- keeping sensitive personal data local;
- not spending days sorting files manually;
- understanding why Reclaim proposes a change.

**Secondary user:** A technical user or consultant performing a migration for
someone else, who wants CLI/automation access, audit evidence, and precise
control.

**Explicit non-user:** A cloud storage service, a multi-tenant archive platform,
or a forensic chain-of-custody system. Reclaim is a local desktop tool.

---

## 3. Product philosophy

1. **Preserve originals.** Source files are read-only. Reclaim plans and copies;
   it does not clean up the source.
2. **Evidence before opinion.** Classifications, dates, collections, and names
   are supported by recorded evidence and rationale, not by a single opaque
   score.
3. **Deterministic core, bounded intelligence.** File enumeration, hashing,
   policy resolution, planning, and execution are deterministic. Semantic
   models may interpret evidence, but their output is frozen as versioned
   evidence before a plan is built.
4. **User control over consequences.** Ordinary documents can be organized
   automatically; consequential or low-confidence decisions are reviewed. Every
   override is recorded and reversible.
5. **Local by default.** No telemetry, no automatic cloud processing, no
   document upload. External providers are opt-in and explicitly authorized.
6. **Useful before clever.** A working, safe migration with simple organization
   is more valuable than a research prototype with perfect taxonomies.
7. **One core, multiple interfaces.** CLI and GUI call the same application
   services. Business logic is not duplicated in the interface.

---

## 4. Functional end state

The user experience:

```text
Select sources → Select destination → Configure organization
→ Analyze → Review uncertain decisions → Preview destination
→ Execute → Verify → Reconcile
```

Required capabilities:

- Multiple source directories, each with independent provenance.
- Read-only inventory, hashing, and exact-duplicate analysis.
- Bounded content extraction from common document formats.
- Document type, subject, date, and collection evidence with provenance.
- Hierarchical, human-useful classification without extension-based sorting.
- Reconstruction of related documents across source directories.
- Destination organization that is coherent and not excessively deep.
- Filename recommendations with preserve/intelligent/standardized policies.
- Confidence presets and advanced thresholds.
- Review of exclusions, ambiguous placements, name changes, and collection
  merges.
- A complete destination preview before execution.
- Journaled, resumable, verified copying with destination collision safety.
- A reconciliation report that accounts for every source occurrence.
- A desktop GUI for ordinary users; a CLI for automation and diagnosis.

---

## 5. Product editions

The working direction is:

| Edition | Contents | Status |
| --- | --- | --- |
| **Reclaim** | Full application with deterministic extraction/classification/organization plus bundled local embedding model | Proposed base product |
| **Reclaim Enhanced** | Same application plus an optional bundled local small language model for ambiguous interpretation | Conditional on benchmarks |
| **Reclaim + external provider** | Opt-in configuration for a user-supplied external model service | Future, not an edition decision |

Principles for editions:

- The base application must be fully usable without an SLM or network.
- Semantic embeddings are the intended standard intelligence layer; they are
  adopted only after the M6 benchmark shows a material improvement. If they do
  not, the operator decides whether to ship a deterministic-only alpha while
  embedding work continues—an explicit decision, not a silent removal.
- An SLM is justified only if it measurably reduces review burden or improves
  organization accuracy enough to offset installation size, latency, memory,
  and support cost.
- External providers are a configuration capability, not a requirement and not
  a near-term deliverable.

Packaging sizes, model choices, and edition names remain open decisions.

---

## 6. What success looks like

**Alpha acceptance:**

- A non-technical operator can complete a full migration of the real staged
  dataset through the GUI.
- Sources are unchanged after analysis and execution.
- Every source occurrence has a reconciled outcome.
- Every copied file is verified against its planned digest.
- The destination is navigable and materially better organized than the source.
- The operator can explain why representative files were placed or renamed.
- Review effort is bounded: ordinary documents do not require individual
  inspection.

**Quality targets (provisional, to be calibrated):**

- Exact duplicate detection: complete for hashed files.
- Supported-format extraction coverage: high enough that most documents yield
  usable evidence; unsupported files remain reviewable.
- Automatic placement error rate: very low; a wrong automatic move is worse
  than an extra review item.
- Filename changes: only when the proposed name is measurably better and safe.
- Migration: 100% reconciliation, zero silent overwrites, zero source writes.

---

## 7. Non-goals

Reclaim is **not**:

- a cloud sync or backup service;
- a data-recovery tool for deleted or damaged media;
- a forensic evidence platform;
- a universal document converter;
- an automatic deleter of "junk";
- an AI chat interface over personal files;
- a plugin marketplace or extensibility platform in its first release;
- a multi-user server application.

These may become adjacent products, but they are not the alpha.

---

## 8. Trust and privacy posture

- Processing is local and offline by default.
- The project database, evidence cache, and reports contain private filenames
  and extracted text; they live outside the repository and are treated as
  sensitive.
- No telemetry, analytics, RNC communication, or remote model call is required
  for core operation.
- External providers are disabled unless the user explicitly configures and
  authorizes them for a specific project.
- Untrusted documents are parsed in bounded workers; macros are never executed,
  archives are not automatically extracted, and internet shortcuts are not
  followed.
- A missing or failed extractor degrades to "needs review", never to a silent
  omission.

---

## 9. Design tensions to manage

| Tension | Position |
| --- | --- |
| Automation vs. user trust | Bias toward review for consequential or low-confidence decisions; auto-organize only well-supported ordinary documents. |
| Semantic intelligence vs. determinism | Freeze model outputs as versioned evidence; plan only from frozen evidence and deterministic policy. |
| Rich organization vs. folder proliferation | Prefer shallow, meaningful collections; avoid a unique folder for every document. |
| Filename improvement vs. privacy | Use only safe, evidence-backed tokens; never put sensitive content into filenames just because it was extracted. |
| Model quality vs. install size | Base model must be small and CPU-usable; SLM is optional and benchmark-gated. |
| Early alpha vs. complete intelligence | Ship a safe migration spine and usable GUI early; enrich intelligence incrementally. |

---

## 10. Product decisions still open

- Exact base model and runtime.
- Whether the base installer bundles the embedding model or downloads it on
  first use (offline requirement favors bundling, size favors optional asset).
- Whether filename standardization is a first-class default or an advanced
  opt-in.
- Default destination taxonomy.
- Edition naming and licensing.
- Supported document formats at alpha.
- Minimum OS versions.

These are tracked in the architecture decision register.
