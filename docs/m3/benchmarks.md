# M3 — Synthetic Evidence Benchmark

## Method

`benchmarks/m3_evidence_benchmark.py` creates a temporary synthetic corpus,
runs inventory/hash and M3 evidence extraction, then repeats extraction to
measure cache reuse. It records:

- corpus file count;
- statuses and per-format worker latency (p50/p95 from persisted `duration_ms`);
- synthetic ground-truth title checks;
- first/second run wall time, cache hits, worker calls;
- source modification count (before/after SHA-256).

No real data or repository artifacts are used. Benchmark output is written
outside the repository or to gitignored `benchmark-output/`.

```powershell
$env:PYTHONPATH = "src"
python benchmarks/m3_evidence_benchmark.py --out benchmark-output/m3.json
```

## Actual results

Environment: **Windows 11 / Python 3.13.5 / subprocess worker**, 2026-11-23.

| Metric | Result |
| --- | ---: |
| Corpus files | 61 |
| First-run wall time | 13.88 s |
| Second-run wall time | 0.00 s |
| Second-run cache hits | 61 / 61 |
| Second-run worker calls | 0 |
| Source files modified | 0 |
| Synthetic metadata-title checks | 14 / 14 |
| Field correctness | 1.00 |

### Status distribution

| Status | Count |
| --- | ---: |
| `success` | 56 |
| `metadata_only` | 2 |
| `encrypted` | 1 |
| `malformed` | 1 |
| `unsupported` | 1 |

The two `metadata_only` PDFs have no extractable text; they are not counted as
full-text successes. The encrypted/malformed/unsupported files are explicit
non-success outcomes.

### Per-format worker latency (persisted `duration_ms`)

| Format | Count | p50 | p95 |
| --- | ---: | ---: | ---: |
| TXT | 32 | 187.0 ms | 201.0 ms |
| HTML | 8 | 183.0 ms | 201.0 ms |
| DOCX | 4 | 325.5 ms | 330.0 ms |
| PPTX | 4 | 397.5 ms | 410.0 ms |
| XLSX | 4 | 428.5 ms | 442.0 ms |
| PDF | 8 | 368.5 ms | 460.0 ms |
| Unsupported binary | 1 | 0 ms | 0 ms |

Each supported document pays subprocess startup overhead (~180–250 ms in this
environment). This is intentional isolation cost, not parser-only latency.

## Cache behavior

First run extracts every unique supported group; duplicate content is extracted
once and linked to all occurrences. Second run is effectively instant
(61/61 cache hits, zero worker calls). Cache identity includes content hash,
evidence schema, extractor ID/version, and configuration fingerprint.

## Limitations

- Compact synthetic corpus (61 files), not the roadmap's 300–500-document
  benchmark; no claim of full-collection throughput.
- Ground-truth checks cover metadata titles for HTML/DOCX/blank PDFs; not
  exhaustive field accuracy.
- Worker memory peak is not measured on Windows; POSIX resource limits were not
  exercised.
- Per-format latency includes process spawning and host load; not a hard
  performance gate.
- No OCR/scanned-text quality evaluation.
