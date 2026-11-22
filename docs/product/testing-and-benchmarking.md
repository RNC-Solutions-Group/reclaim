# Testing and Benchmark Strategy

**Status:** Proposal. Thresholds marked *provisional* must be calibrated on the
actual corpus before they become release gates.
**Principle:** Measure whether organization is actually better, not merely
whether the pipeline produces output.

---

## 1. What must be evaluated

1. **Extraction** — does Reclaim obtain useful, correct evidence from supported
   documents, within bounded time and memory?
2. **Understanding** — are document types, dates, categories, and collection
   relationships accurate and explainable?
3. **Organization** — is the proposed destination useful to a human, or merely
   tidy-looking?
4. **Naming** — do proposed filenames materially improve findability without
   inventing facts or leaking sensitive content?
5. **Automation** — at a given confidence threshold, how much review is
   required, and how often is an automatic decision wrong?
6. **Semantics** — does an embedding model (and later an SLM) improve results
   enough to justify its size, latency, and complexity?
7. **Migration** — is every source occurrence accounted for, copied safely, and
   verified?

---

## 2. Evaluation principles

- **Baseline first.** Every semantic improvement is compared with the
  deterministic, non-embedding engine on the same frozen evidence.
- **Held-out test data.** Calibration and threshold tuning use a development
  split; final numbers use a test split.
- **Leakage control.** Near-duplicate documents must not appear across splits.
- **Human labels, not model labels.** Classification/collection/naming ground
  truth is operator-labeled, with an explicit “ambiguous” label where needed.
- **Safety is a hard gate.** A quality gain does not offset a wrong automatic
  placement, an overwrite, or a source mutation.
- **Report uncertainty.** Include sample size and confidence intervals; do not
  present small-sample differences as established.
- **Private data stays private.** Real evaluation documents and labels live in
  the private workspace; the repository contains only synthetic fixtures,
  generators, metric code, and aggregate results approved for publication.

---

## 3. Evaluation corpus

### 3.1 Synthetic corpus (public-safe)

Generate programmatically with known labels:

- course/project collections with mixed PDF/DOCX/PPTX/XLSX/TXT files;
- unrelated documents sharing vocabulary;
- good and bad filenames (`IMG_0421.pdf`, `scan0001.pdf`, `2023-04-12 - ...`);
- missing, misleading, and conflicting timestamps;
- exact duplicates, renamed duplicates, near-duplicates, revisions;
- documents distributed across simulated source directories;
- empty, malformed, encrypted, truncated, and unsupported files;
- long/Unicode paths and reserved-name candidates;
- scanned/image-only PDFs (for unsupported/OCR behavior).

Target initial size: 300–500 documents, expandable. The generator and expected
labels live in the repository; generated binaries are test fixtures, not real
data.

### 3.2 Controlled real corpus (private)

- A small, operator-approved subset of the staged dataset.
- Labels stored in the private workspace, pseudonymized in any report.
- Covers real-world messiness: legacy Office, long names, duplicates,
  course packs, clinical/military/personal mixtures.
- Never committed; never included in public benchmark output.
- Used for calibration and final alpha acceptance, not for unit tests.

### 3.3 Splits

| Split | Purpose | Approx. share |
| --- | --- | ---: |
| Development | Rule/feature iteration | 50% |
| Calibration | Confidence thresholds | 25% |
| Test | Final reported metrics | 25% |

Keep collections intact within a split where possible; splitting a course across
train/test overstates collection accuracy. If a collection must be split, report
it explicitly.

---

## 4. Label schema

Each labeled document records:

| Field | Values / notes |
| --- | --- |
| `document_type` | lecture, exam, note, reference, form, letter, certificate, report, resume, photo, other |
| `category` | configurable hierarchy; `ambiguous` allowed |
| `collection_id` | logical course/project/certification/event, or `none` |
| `dates` | list of (value, granularity, source, confidence) |
| `title` | optional human title |
| `filename_quality` | good / acceptable / poor / misleading |
| `proposed_name_acceptable` | yes / no / unsure (for naming evaluation) |
| `duplicate_relation` | exact / near / revision / unrelated |
| `notes` | ambiguity and edge cases |

Collection labels are the most expensive and most important. Label the
collection boundary explicitly (what belongs, what does not).

---

## 5. Evaluation configurations

Run the same frozen corpus and thresholds through:

| ID | Configuration |
| --- | --- |
| **C1** | Extraction + rules/features; no embeddings |
| **C2** | C1 + embedded model A |
| **C3** | C1 + embedded model B |
| **C4** | Best of C2/C3 + ~0.6B SLM (ambiguous cases only) |
| **C5** | Best of C2/C3 + larger SLM, if justified |

External providers are a later configuration (C6+), not part of alpha
evaluation.

Each run records: corpus version/hash, evidence schema/extractor versions,
model id/hash/runtime, policy version, threshold preset, hardware, OS, Python
version, wall time, peak memory, and result hash.

---

## 6. Metrics

### 6.1 Extraction

- **Coverage:** supported documents with non-empty, valid evidence / supported
  documents.
- **Field precision/recall/F1:** title, author, embedded dates, headings.
- **Failure rate:** extraction errors / attempted documents, by format and
  error code.
- **Latency:** p50/p95 seconds per document and per MB, by format.
- **Resource use:** peak RSS per worker, timeout rate.
- **Cache efficiency:** extraction cache hit rate on a repeated run.

### 6.2 Classification

- Accuracy and **macro F1** over document types and categories.
- Top-2 accuracy (useful for review suggestions).
- Confusion matrix; per-category precision/recall.
- **Abstention/review rate:** documents routed to review instead of
  auto-classified.
- **Incorrect automatic placement rate:** wrong auto-placed documents / all
  auto-placed documents. This is the primary safety metric.

### 6.3 Collection reconstruction

- Pairwise precision/recall/F1 over document pairs (“same collection”).
- Cluster purity and normalized mutual information.
- **Merge errors:** unrelated collections combined.
- **Split errors:** one collection divided.
- Collection-level review rate.

### 6.4 Dates

- Exact-date, month, and year accuracy against labels.
- Granularity correctness (date vs month vs year).
- **False-authority rate:** cases where an inferred date is presented as
  authoritative when the evidence is weak.
- Provenance correctness: does the reported source actually support the date?

### 6.5 Naming

- Human acceptance rate of proposed names.
- **Material improvement rate:** accepted names judged better than original.
- Regression rate: proposed name judged worse.
- Collision/portability violations (must be zero).
- Sensitive-content leakage (must be zero).
- Preservation rate under the Intelligent policy (how often it correctly leaves
  a good name alone).

### 6.6 Automation/review burden

- Reviewed items per 1,000 documents at each preset.
- Auto-coverage vs. error curves across thresholds.
- Reviewer acceptance/override rate.
- Time-to-review (observed, not estimated).

### 6.7 Migration

- Source occurrences reconciled / total (must be 100%).
- Destination files hash-verified / copied (must be 100%).
- Silent overwrites (must be zero).
- Source mutations (must be zero; metadata + targeted hash checks).
- Resume success after forced interruption.
- Throughput (files/min, MB/s) and peak memory.

### 6.8 Semantic model value

- Delta vs C1 on macro F1, collection F1, auto-placement error, review burden.
- Installer size delta, model load time, per-document latency, peak RAM.
- Reproducibility across repeated runs and machines.

### 6.9 Confidence calibration

- Reliability diagram and expected calibration error (ECE).
- Brier score.
- Auto-coverage at fixed error budgets.
- Separate curves for classification, collection, date, and naming.

---

## 7. Provisional acceptance thresholds

These are **starting gates**, to be revised after the first labeled run. They
are deliberately conservative because a wrong automatic placement is worse than
an extra review item.

| Area | Provisional gate |
| --- | --- |
| Extraction coverage (supported formats) | ≥ 90%, with 100% of failures accounted |
| Extraction crash/timeout | 0 unhandled crashes; timeouts recorded and retried |
| Classification macro F1 (C1 baseline) | ≥ 0.75 |
| Classification macro F1 (best semantic) | ≥ C1 + 0.03, or review burden reduced ≥ 20% at equal error |
| Incorrect automatic placement | ≤ 1% of auto-placed documents at Balanced preset |
| Collection pairwise F1 | ≥ 0.80 at Balanced preset |
| Collection merge errors | ≤ 2% of collections |
| Naming acceptance | ≥ 80% of proposals; < 5% judged worse |
| Naming safety violations | 0 |
| Plan determinism | 100% identical plan hash for identical frozen inputs |
| Migration reconciliation | 100% |
| Destination hash verification | 100% |
| Silent overwrites | 0 |
| Source mutations | 0 |
| Review burden at Balanced | ≤ 5% of documents initially |

If a threshold cannot be met, report it and adjust the product promise rather
than weakening safety.

---

## 8. Test levels

| Level | Scope | Frequency |
| --- | --- | --- |
| Unit | Paths, policy, evidence parsing, naming rules, validation, journal states | Every commit |
| Integration | Extractor → evidence → understanding → plan → execute on synthetic trees | Every PR |
| Corpus benchmark | C1–C5 on labeled corpora; metrics and regression gates | Per milestone / nightly |
| Fault injection | Interrupted copy, full disk, permission denied, source change, collision, corrupt evidence cache | Per execution milestone |
| Security | Malformed PDF/Office/XML/ZIP, encrypted, oversized, path traversal, no-network | Per extractor milestone |
| Performance | 100k-document synthetic inventory/evidence/plan benchmark | Per milestone |
| Cross-platform | Windows, macOS, Linux CI for core + packaging smoke tests | Before alpha/release |
| Real-world acceptance | Full 87 GB migration with operator sign-off | M7 |

No test uses real personal data in the repository or CI.

---

## 9. Benchmark harness

Proposed CLI:

```bash
reclaim benchmark \
  --corpus /private/eval/corpus-a \
  --config /private/eval/config-c2.toml \
  --out /private/eval/results/c2.json
```

The harness:

1. Refuses to write outside the configured private workspace.
2. Verifies the corpus manifest and content hashes.
3. Runs inventory/evidence/understanding/planning against a temporary project
   DB.
4. Computes metrics from labels and predictions.
5. Emits a machine-readable result plus a human summary.
6. Records environment, versions, model hashes, and timing.
7. Never copies source content into the repository or logs.

Corpus manifest shape:

```json
{
  "corpus_version": 1,
  "documents": [
    {
      "id": "doc-0001",
      "sha256": "…",
      "relative_path": "fixtures/course-a/lecture-01.pdf",
      "labels": {
        "document_type": "lecture",
        "category": "education/clinical",
        "collection_id": "course-a",
        "dates": [{"value": "2021-03-04", "granularity": "day", "source": "content"}],
        "filename_quality": "poor"
      }
    }
  ]
}
```

Private corpora use the same schema but live outside the repository.

---

## 10. Reproducibility and reporting

Every result records:

- corpus version + manifest hash;
- extractor ids/versions and schema version;
- policy/profile version;
- model id, file hash, runtime version, quantization, tokenizer, max length;
- threshold preset;
- OS, CPU, RAM, Python version;
- wall time, peak memory, per-stage timings;
- code commit hash.

Reports include sample size and confidence intervals. A single aggregate score
is insufficient; include per-format and per-category breakdowns.

---

## 11. Security and failure testing

Required before extractor milestones close:

- malformed/truncated PDF, DOCX, PPTX, XLSX, RTF, HTML;
- XML entity expansion and zip bombs;
- encrypted documents;
- documents with macros or external links (must not execute/follow);
- path traversal in archive/office entry names;
- huge page/sheet/slide counts and text volumes;
- worker timeout, crash, and memory pressure;
- no network egress during extraction (test with a blocked socket).

A parser failure must produce a recorded, reviewable outcome — not a crash, hang,
or silent empty evidence.

---

## 12. Governance

- The operator owns the real evaluation corpus and labels.
- Public repository results are aggregate and sanitized.
- Model or threshold changes require a new benchmark run and a recorded
  decision.
- A quality claim without a reproducible run is not accepted.
- Do not tune thresholds on the test split.
