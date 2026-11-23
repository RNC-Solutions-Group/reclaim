# M3 — Evidence Extraction v1

**Status:** Functional alpha implemented on `feat/m3`; operator review pending.
M3 adds bounded, provenance-rich document evidence on top of the M1/M1A
inventory and M2 desktop shell. It does not classify, name, or recommend
migration actions.

## What M3 adds

- Versioned extractor registry with explicit formats, versions, capabilities,
  dependency lists, and limitations.
- Bounded subprocess extraction for PDF, DOCX, PPTX, XLSX, TXT/Markdown, HTML,
  RTF, and legacy OLE metadata.
- Normalized evidence contract with field-level provenance, raw observations,
  bounded text samples, warnings, and explicit non-success statuses.
- Content-addressed evidence cache keyed by content SHA-256, evidence schema,
  extractor identity/version, and configuration fingerprint.
- SQLite schema v5 with evidence records, occurrence links, and evidence runs.
- CLI `evidence`, `evidence-stats`, and `evidence-export`.
- Deterministic synthetic extraction tests and a reproducible benchmark.

## Quick start

```powershell
python -m pip install -e ".[evidence]"

reclaim scan   --config path\to\project.toml
reclaim hash   --config path\to\project.toml
reclaim evidence --config path\to\project.toml --out path\to\evidence-export

reclaim evidence-stats --config path\to\project.toml
```

The GUI remains usable without evidence dependencies. The M3 CLI requires the
optional `evidence` extra.

## Format support

| Format | Status | Notes |
| --- | --- | --- |
| PDF | Supported | `pypdf`; bounded `pdfminer.six` fallback; no OCR |
| DOCX | Supported | `python-docx`; properties, text, heading styles |
| PPTX | Supported | `python-pptx`; properties, slide text, slide count |
| XLSX | Supported | `openpyxl` read-only/data-only; no formula evaluation |
| TXT/Markdown/CSV/TSV | Supported | Bounded local decode; Markdown headings |
| HTML | Supported | stdlib parser; no network/script/entity retrieval |
| RTF | Supported (text) | `striprtf`; text-only |
| Legacy DOC/XLS/PPT | Metadata only | `olefile` SummaryInformation; no body text |
| Encrypted/malformed/unsupported | Explicit failure | Never reported as success |

## Evidence statuses

`success`, `metadata_only`, `empty`, `unsupported`, `encrypted`, `malformed`,
`timeout`, `crashed`, `resource_limit`, `input_too_large`, `hash_unavailable`,
`source_unavailable`, `source_changed`, `dependency_missing`.

A scanned/image-only PDF is `metadata_only` or `empty`, never full-text success.
An archive/XML limit, parser crash, or timeout is an explicit outcome and does
not abort the whole evidence job.

## Documentation

- [Architecture, registry, cache, and security](architecture.md)
- [Operator guide](user-guide.md)
- [Test and acceptance matrix](test-matrix.md)
- [Benchmark results](benchmarks.md)
- [Dependency and license inventory](dependencies.md)

## Non-goals

No classification, semantic search, OCR, naming intelligence, embeddings, SLM,
GUI evidence browser, or migration-authority change. See the
[roadmap](../product/roadmap.md) and ADR-004/ADR-005.
