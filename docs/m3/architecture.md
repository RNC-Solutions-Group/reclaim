# M3 — Evidence Architecture

## 1. Layering

```text
CLI / GUI (future evidence status)
  → EvidenceService (application service)
    → evidence registry + subprocess worker
      → bounded extractor
    → SQLite evidence cache/occurrence links
  → inventory hashes + source provenance
```

Widgets and the CLI do not parse documents. `EvidenceService` owns format
selection, cache identity, worker invocation, status persistence, and reporting.

## 2. Extractor registry

Each `ExtractorSpec` declares:

- stable extractor ID and version;
- supported suffixes;
- capabilities (metadata, text, page/slide/sheet count, headings, encryption
  detection, format hint);
- dependencies;
- known limitations.

The registry version set participates in the evidence configuration
fingerprint. Registry and extractor versions must change whenever output
semantics change.

## 3. Evidence contract

`EvidenceRecord` is versioned (`EVIDENCE_SCHEMA_VERSION = 1`) and contains:

- content SHA-256, extractor ID/version, config fingerprint, source format;
- status and bounded warnings/limitations;
- title, authors, dates, headings, and format hints as **observations with
  source and detail**;
- page/slide/sheet counts where observed;
- text character count and a bounded sample (not full document text);
- frequency keywords explicitly marked as heuristic limitations;
- raw metadata properties;
- all observations with `source` (`document_property`, `page`, `slide`, `sheet`,
  `text`, `extension`, `container`) and origin detail.

Raw observations remain separate from interpretations. A metadata title is not
inferred from body text; a frequency keyword does not establish a topic; no
evidence is executable migration authority.

### Status semantics

| Status | Meaning |
| --- | --- |
| `success` | Text extracted; metadata may also be present |
| `metadata_only` | Valid document, metadata observed, no extractable text |
| `empty` | Valid document, no text and no metadata (e.g., blank page) |
| `unsupported` | No M3 extractor for the suffix |
| `encrypted` | Encryption prevents extraction |
| `malformed` | Parser rejected the document |
| `timeout` | Worker exceeded per-document timeout |
| `crashed` | Worker exited/crashed without a valid response |
| `resource_limit` | Archive/entry/expansion/memory limit |
| `input_too_large` | Input exceeds configured byte cap |
| `hash_unavailable` | Inventory content hash is not available |
| `source_unavailable` | Source missing/unreadable |
| `source_changed` | Current bytes differ from inventory hash |
| `dependency_missing` | Required parser package unavailable |

All persisted outcomes are reusable cache entries, including explicit failures,
so repeated runs do not repeatedly pay for known-bad documents. Failures remain
distinct statuses and are re-attempted only when `retry_failures` is requested
(or the CLI `--retry-failures` flag is used). A cached failure can never be
reported as a successful extraction.

## 4. Worker isolation

- One subprocess per unique extraction request.
- Request/response JSON files in a per-document temporary directory; cleanup on
  every path.
- Per-document timeout; timeout/crash/nonzero exit are explicit outcomes.
- POSIX worker uses `RLIMIT_AS`/`RLIMIT_CPU` where available.
- Windows uses subprocess timeout, input/expansion caps, and worker process
  isolation; equivalent OS memory rlimits are not available and are documented.
- Input, text, metadata, sample, warning, and observation caps are enforced.
- No network, no macros, no formula evaluation, no external entities, no OCR, no
  recursive archive extraction, no LibreOffice.
- Office ZIP packages are preflighted: entry count, declared expansion, entry
  encryption, and XML DTD/entity declarations are rejected before library parse.
  `defusedxml` parses bounded XML parts as defense in depth.
- Worker exceptions become `crashed`; the parent continues with other documents.

## 5. Content-addressed cache

Cache key:

```text
SHA-256(
  content_sha256,
  source_format,
  extractor_id,
  extractor_version,
  evidence_config_fingerprint,
  evidence_schema_version
)
```

`evidence_records` stores one payload per key; `evidence_occurrences` maps each
source occurrence (`run_id`, `entry_id`, `source_id`, `source_rel_path`) to that
content-addressed record. Duplicate occurrences preserve provenance while
reusing one extraction.

Changed content, extractor version, evidence schema, or config invalidates the
cache key. Failed/interrupted workers never write a successful payload. Failed
rows persist error state and are retried explicitly.

## 6. Schema v5

Forward migration from schema v4 adds:

- `evidence_records`
- `evidence_occurrences`
- `evidence_runs`

Existing inventory, hashes, plans, authorizations, executions, and M1 plan
snapshots are not modified.

## 7. CLI

`reclaim evidence` runs extraction for the selected inventory run; `--out`
writes JSONL/CSV. `evidence-stats` reports source/evidence counts, statuses,
formats, cache hits, worker calls, and failures. `evidence-export` writes the
same artifacts without re-extracting. Exit code `7` signals extraction failures;
`0` means no failure statuses (unsupported/metadata-only/empty are explicit
terminal outcomes, not parser failures).

## 8. Limitations

- No OCR, image understanding, visual layout, or reading-order reconstruction.
- No full legacy DOC/XLS/PPT body text; metadata-only.
- No RTF metadata/object extraction; text-only.
- PDF heading/structure inference is not implemented.
- XLSX formulas are not evaluated; data-only cached values are read.
- No nested archive extraction or embedded-file payload extraction.
- No worker memory measurement on Windows; POSIX rlimits are best-effort.
- No full private-collection performance claim; compact synthetic corpus only.
- No M4 classification/naming/semantic work.
