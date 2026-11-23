# M3 — Evidence Operator Guide

M3 extracts bounded document evidence from files already in the inventory. It
is read-only and local. It never changes source files, executes macros, evaluates
spreadsheet formulas, fetches remote resources, or uses OCR/AI.

## 1. Install

```powershell
python -m pip install -e ".[evidence]"
```

The base engine and M2 GUI remain available without the evidence extra; the
evidence commands require the parser packages.

## 2. Prerequisites

1. A project config with validated sources.
2. A scan run (`reclaim scan`).
3. Content hashes (`reclaim hash`). Evidence extraction only processes files
   with an available SHA-256; unhashed files are explicit `hash_unavailable`
   outcomes.

## 3. Run extraction

```powershell
reclaim evidence --config path\to\project.toml

# Optional bound overrides and export
reclaim evidence --config path\to\project.toml \
  --timeout 20 --max-input-mb 32 --max-text-chars 500000 \
  --out path\to\evidence-export
```

Use `--run` to select a specific inventory run. `--retry-failures` re-attempts
cached failures; otherwise prior outcomes (including explicit failures) are
reused from the content-addressed cache. A cached failure is never presented as
a successful extraction.

The command prints run counts and returns:

| Code | Meaning |
| ---: | --- |
| 0 | Completed with no failure statuses |
| 7 | Completed with explicit extraction failures (malformed, encrypted, timeout, crash, etc.) |
| 2 | Configuration/service error |
| 4 | Cancelled/incomplete run |

Unsupported formats are terminal outcomes, not parser failures; use
`evidence-stats` to see coverage.

## 4. Status and reporting

```powershell
reclaim evidence-stats --config path\to\project.toml
reclaim evidence-export --config path\to\project.toml --out path\to\export
```

`evidence-stats` reports:

- total inventory files and covered evidence occurrences;
- outcome status counts;
- source-format counts;
- cache hits and worker calls;
- failures and unsupported counts.

`evidence-export` writes:

- `evidence.jsonl` — one occurrence record with source provenance and evidence;
- `evidence.csv` — tabular summary plus bounded payload JSON.

Treat the entire export/workspace as private: it can contain filenames, titles,
authors, and text samples.

## 5. Interpreting outcomes

| Outcome | Meaning |
| --- | --- |
| `success` | Text was extracted |
| `metadata_only` | Valid document with metadata but no extractable text (e.g., scanned PDF) |
| `empty` | Valid document with no text or metadata |
| `unsupported` | No M3 extractor for the format |
| `encrypted` | Encryption prevented extraction |
| `malformed` | Parser rejected the file |
| `timeout` / `crashed` | Worker safety boundary triggered |
| `resource_limit` / `input_too_large` | Configured safety cap |
| `hash_unavailable` | Inventory hash missing |
| `source_unavailable` / `source_changed` | Source no longer matches inventory |
| `dependency_missing` | Parser package absent |

A metadata-only PDF is **not** a full-text success. Encrypted/malformed files
are never silently treated as empty documents.

## 6. Format limitations

- PDF: pypdf text; pdfminer fallback; no OCR, heading reconstruction, annotations,
  attachments, JavaScript, or embedded-file execution.
- DOCX/PPTX/XLSX: core properties and bounded text; no macros, visual rendering,
  formula evaluation, or external-link retrieval. Office archives are
  preflighted for entry/expansion/XML-entity abuse.
- RTF: text-only; no metadata/object extraction.
- Legacy DOC/XLS/PPT: OLE SummaryInformation metadata only; no body text.
- HTML: local markup only; scripts/resources are never fetched or executed.
- TXT/Markdown: conservative bounded decode; Markdown heading syntax only.

## 7. Cache behavior

Cache identity is content SHA-256 + evidence schema + extractor ID/version +
configuration fingerprint. Changing content, parser version, limits, or schema
creates a new cache entry. Re-running with unchanged inputs should reuse evidence
without worker calls; `evidence-stats` shows the hit count.

## 8. Security/resource behavior

- Worker subprocess per document; timeout and crash are isolated.
- Input size, text, metadata, archive entry, and expansion caps.
- POSIX resource limits where supported; Windows relies on timeout/input/stream
  caps and process isolation.
- No source mutation. Verify with source snapshots in tests/benchmarks.
- No OCR/network/macro/formula execution.

## 9. Known limitations

- Compact synthetic corpus only; no full private-collection benchmark claim.
- No GUI evidence browser; the M3 CLI/application service is the interface.
- No legacy body text, OCR, visual layout, or semantic inference.
- No worker memory measurement on Windows.
- Dependency/license review is documented but final product license remains open.
