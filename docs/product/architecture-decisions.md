# Architecture Decision Register

**Status:** Proposals for operator review unless marked accepted or approved.
**Rule:** Each decision records the preferred option, meaningful alternatives,
and the consequence of getting it wrong. Implementation details that ordinary
engineering can settle are intentionally not listed.

## Operator approval log — 2026-11-22

The operator approved the architecture direction with these scope constraints:

- **ADR-003 approved.** Evidence, understanding, recommendations, overrides, and
  plans remain separate. A recommendation is never authorization.
- **ADR-004 implemented in M3.** Bounded local extraction with explicit
  unsupported/error states, subprocess isolation, archive/XML limits, and
  dependency/license review. Evidence remains non-executable migration
  intelligence.
- **ADR-005 approved in principle.** Local ONNX embedding-provider architecture
  retained; no model selection or bundling during M1.
- **ADR-006 approved.** Confidence must be empirically calibrated; no
  uncalibrated similarity scores presented as probabilities.
- **ADR-007 approved and required for M1.** Immutable, hash-pinned plans;
  journaled execution; explicit authorization; deterministic reconciliation;
  resumability.
- **ADR-008 approved.** Preserve / Intelligent / Standardized policies remain.
  Intelligent stays the intended default subject to evidence and acceptance
  thresholds; full naming intelligence is not M1.
- **ADR-009 conditionally approved.** PySide6/Qt is the GUI direction, subject to
  LGPL compliance, packaging, redistribution, and replacement considerations.
  The M2 functional alpha is implemented; distribution/legal review remains
  pending.
- **ADR-010 approved.** No SLM in the base alpha; Enhanced remains
  benchmark-gated and requires a future operator decision. No SLM or external
  provider work in M1.

---

## ADR-001 — Keep the Python modular monolith and SQLite system of record

**Status:** Accepted (CP1), reaffirmed.

**Context.** Reclaim must run offline on Windows, macOS, and Linux, ship as a
desktop application, and keep private state local. The dataset scale is large
but not server-scale.

**Decision.** Keep a modular Python 3.11+ monolith with SQLite as the durable
system of record. Add modules, not services. The GUI and CLI are adapters over
the same application services.

**Alternatives.**

- Go/Rust core with a separate UI: better packaging/performance, much higher
  development cost and a second language.
- Local service + web UI: unnecessary operational complexity and attack surface.
- PostgreSQL/DuckDB as primary store: service or analytical orientation does not
  fit transactional decisions and resumable state.

**Consequences.** Python packaging and CPU inference are constraints; the
architecture stays testable and portable. Revisit only if scale or packaging
proves untenable.

---

## ADR-002 — Source-root identity is part of every cache key

**Status:** Accepted; implemented in CP1 follow-through.

**Context.** A source identifier can be re-registered against a different root.
The original hash cache keyed only on `(source_id, rel_path, size, mtime)` and
could reuse a digest across different content.

**Decision.** Compute `root_identity` from canonical path plus volume identity.
Store it on the source, in the hash cache, and in the scan run. Invalidate a
source's cache when its root identity changes. Legacy v1 caches without identity
are not reused.

**Alternatives.**

- Invalidate only when the path string changes: misses volume swaps.
- Trust mtime/size: unsafe.
- Global cache keyed only by content: requires hashing first, defeating the
  cache.

**Consequences.** Re-registration may trigger rehashing; correctness wins.
Schema v2 is forward-migrated.

---

## ADR-003 — Separate observed evidence, inference, recommendation, and plan

**Status:** Approved 2026-11-22.

**Context.** CP1 computes recommendations at export time. A GUI needs reviewable,
persisted decisions; a plan must be reproducible from frozen inputs.

**Decision.** Persist four layers:

1. **Observed evidence** — extracted fields, provenance, extractor version.
2. **Understanding** — type/date/category/collection inferences with a
   confidence vector.
3. **Recommendation/override** — proposed action and explicit user decision.
4. **Plan/execution** — frozen effective decisions, destinations, journal.

A plan may only reference an immutable analysis snapshot (evidence + model +
policy versions).

**Alternatives.**

- Recompute recommendations on every open: non-reproducible and slow.
- Store only final plan items: loses explainability and review history.
- Put all inference in one opaque “AI result” blob: unauditable.

**Consequences.** More schema and migration work, but review, audit, and
determinism become possible.

---

## ADR-004 — Extraction stack, isolation, and format scope

**Status:** Approved in principle 2026-11-22; implemented in M3 (bounded subprocess extraction, archive/XML limits, content-addressed evidence cache).

**Context.** Document parsing is the largest capability gap and a security
boundary. Python has good permissive libraries but no single universal parser.

**Decision.**

- Extractor registry with explicit versions.
- v1 formats: PDF (`pypdf`, fallback `pdfminer.six`), DOCX (`python-docx`),
  PPTX (`python-pptx`), XLSX (`openpyxl`), TXT/Markdown, RTF, HTML.
- Legacy DOC/XLS/PPT: metadata-only via `olefile`; optional LibreOffice adapter
  later, not bundled initially.
- Avoid AGPL PDF libraries (e.g., PyMuPDF) unless a commercial license is
  purchased.
- Run extraction in bounded worker processes with timeouts, size/text caps, no
  macros, no network, no external entities.
- Use `defusedxml` and archive-bomb limits for Office XML/ZIP containers.

**Alternatives.**

- One heavyweight universal converter (LibreOffice): broad coverage, huge
  footprint and slow startup.
- Cloud extraction: violates local-first.
- OCR in v1: expensive and not needed to prove the pipeline.

**Consequences.** Unsupported/scanned documents go to review, not silent
failure. Format coverage is measured, not assumed.

---

## ADR-005 — Embeddings: small ONNX model behind a provider interface

**Status:** Approved in principle 2026-11-22; model choice benchmark-gated and
not in M1.

**Context.** Semantic similarity can improve collection reconstruction and
classification, but model quality, size, licensing, and reproducibility vary.

**Decision.**

- Define an `EmbeddingProvider` interface with a no-op baseline.
- Evaluate `bge-small-en-v1.5` and `all-MiniLM-L6-v2` first, exported to ONNX,
  CPU runtime (`onnxruntime` + `tokenizers` + `numpy`).
- Bundle a versioned model asset with a SHA-256 manifest; offline operation.
- Cache embeddings by content hash + model/runtime version.
- Use similarity as candidate evidence, not as a probability.
- Adopt the model only if the M6 benchmark shows material improvement without
  safety/determinism regression.

**Alternatives.**

- PyTorch/sentence-transformers: large installer and slower startup.
- Hosted embeddings: violates local-first.
- Vector database (FAISS/sqlite-vec): unnecessary at ≤200k documents.
- Large embedding model (137M+): revisit only if small models plateau.

**Consequences.** Base installer grows by roughly tens of MB; model selection
remains reversible.

---

## ADR-006 — Confidence is a calibrated vector, not cosine similarity

**Status:** Approved 2026-11-22.

**Context.** The product exposes confidence presets (e.g., 0.85–0.99). Those
numbers are meaningless unless calibrated and separated by decision type.

**Decision.**

- Store separate confidence dimensions: extraction, classification, collection,
  date, filename.
- Calibrate on a held-out labeled corpus (Platt/isotonic as appropriate).
- Report reliability (ECE/Brier) and review-burden curves.
- Presets map to thresholds; advanced users can adjust them.
- Do not call raw similarity a probability.

**Alternatives.**

- Single opaque confidence: impossible to explain or tune.
- Uncalibrated model scores: misleading automation.
- No confidence: forces excessive review.

**Consequences.** Requires labeled data and calibration work before the UI
promises numeric confidence.

---

## ADR-007 — Plans are immutable, hash-pinned, and separate from execution

**Status:** Approved 2026-11-22; required for M1; implemented and hardened in
`feat/m1` (M1A).

**Context.** Migration is consequential. A preview must be exactly what executes.

**Decision.**

- Build a versioned plan with a canonical hash.
- Editing creates a new draft version; authorization pins plan id + hash.
- Execution refuses a mismatched plan or changed source identity.
- Plan items reference frozen analysis/evidence versions.
- Journal every copy state; resume from journal; verify destination hashes.
- Never overwrite; collisions are explicit failures or recorded disambiguators.

**Alternatives.**

- Execute from live recommendations: non-reproducible.
- Edit plan in place: authorization meaningless.
- Copy-then-fix: risks overwrites and untraceable state.

**Consequences.** More upfront schema work; execution becomes auditable and
resumable. Verification is part of the contract: missing, unreadable, or
corrupted destinations transition out of `verified`, and reconciliation must
distinguish accounting completeness (`balanced`) from successful migration
(`successful`).

---

## ADR-008 — Filename policies: preserve, intelligent, standardized

**Status:** Approved 2026-11-22; naming intelligence deferred to M5.

**Context.** Filenames are often the only human-facing metadata, but aggressive
renaming can destroy recognition and leak sensitive content.

**Decision.**

- **Preserve:** no changes.
- **Intelligent (default):** rename only when quality is poor and evidence-backed
  improvement is material; show original and proposed name.
- **Standardized (opt-in):** apply configurable templates to eligible files.
- Always preserve the original filename in inventory/plan; rename destination
  copies only.
- Never invent dates/versions/titles; never place sensitive content in names.
- Never rename inside packages/dependent structures.
- Validate portability, collisions, and length.

**Alternatives.**

- Preserve-only: safer but leaves unusable names.
- Always-standardize: high regression risk and loses human recognition.
- Let an LLM invent titles: unverifiable and privacy-sensitive.

**Consequences.** Naming quality must be measured with human acceptance, not
claimed.

---

## ADR-009 — GUI: PySide6/Qt, with LGPL compliance review

**Status:** Conditionally approved 2026-11-22; implemented in M2 as a dynamically
linked optional PySide6 dependency.

**Context.** The alpha needs large virtualized tables, a tree preview, progress
jobs, and native packaging.

**Decision.** Use PySide6 (LGPLv3) as the leading candidate, with dynamic
linking, license notices, and a Qt replacement/compliance plan. The GUI consumes
application services and job events; it does not query SQLite or traverse the
filesystem.

**Alternatives.**

- PyQt6: GPL/commercial; simpler licensing only if the project adopts GPL.
- Tauri/web UI: smaller runtime, adds Rust/web toolchain and custom tree UI.
- Tkinter: available but weak for the review experience.

**Consequences.** Legal review required before distribution; packaging must
include Qt notices and allow library replacement.

---

## ADR-010 — No SLM in the base alpha; Enhanced is benchmark-gated

**Status:** Approved 2026-11-22; no SLM work in M1.

**Context.** A bundled SLM may improve ambiguous interpretation, but adds
hundreds of MB to gigabytes, latency, memory, licensing constraints, and support
surface.

**Decision.** Ship the alpha without an SLM. Evaluate 0.6B/1.7B Apache-2.0
candidates only after the embedding baseline and measured review burden exist.
Use the SLM only for ambiguous cases with structured output; it never controls
filesystem operations. Create an Enhanced edition only if a pre-registered
threshold is met.

**Alternatives.**

- Bundle an SLM from the start: unproven value, large cost.
- No SLM ever: may leave accuracy on the table.
- Cloud LLM: violates local-first.

**Consequences.** Base alpha is smaller and simpler; Enhanced remains optional.

---

## ADR-011 — External providers are an interface, not a dependency

**Status:** Proposed; implementation deferred.

**Context.** The operator has a DeepSeek API for future experiments. External
reasoning may help later, but must not become a hidden dependency or privacy
risk.

**Decision.** Define a provider interface and project-level authorization model
(provider, endpoint, data scope, redaction preview). No automatic fallback, no
telemetry, no document transmission without explicit consent. Do not implement
providers in the alpha.

**Alternatives.**

- Build DeepSeek/OpenAI integration now: distracts from alpha and adds privacy
  surface.
- Never support providers: closes a useful future option.

**Consequences.** Contract work now, implementation later.

---

## ADR-012 — Media libraries and archives are opaque packages

**Status:** Accepted; reaffirmed by the completed iPhoto archive.

**Context.** Packaged photo libraries contain tens of thousands of internal
files, generated previews, and metadata databases. Blind hashing, flattening,
or extraction risks losing album/edit relationships.

**Decision.** Treat known libraries/archives as opaque packages: inventory at
metadata level, preserve intact, defer content hashing/similarity/conversion to
a media phase. A completed archive is registered with size/SHA-256 and deferred
disposition; do not assume it matches another library and do not extract it.

**Alternatives.**

- Treat every internal file as a document: noise, wrong categories.
- Extract and reorganize now: risks album metadata and duplicates.
- Ignore media: loses accountability.

**Consequences.** Media remains a separate, explicitly accounted phase.

---

## Decision status summary

| ADR | Decision | Status | Needed by |
| --- | --- | --- | --- |
| 001 | Modular monolith / SQLite | Accepted | — |
| 002 | Source-root cache identity | Accepted | M1 |
| 003 | Evidence/understanding/plan separation | Approved | M3 |
| 004 | Extraction stack/isolation | Implemented in M3; format/security limitations documented | M3 |
| 005 | Embedding runtime/model | Approved in principle; model gated | M6 |
| 006 | Confidence calibration | Approved | M4 |
| 007 | Immutable plan/execution | Approved; implemented/hardened in M1/M1A | M1 |
| 008 | Filename policies | Approved | M5 |
| 009 | PySide6 GUI | Conditionally approved; implemented in M2; distribution/legal review pending | M2 |
| 010 | SLM deferral | Approved | M8 |
| 011 | External provider interface | Proposed | M9 |
| 012 | Media packages opaque | Accepted | M7 |
