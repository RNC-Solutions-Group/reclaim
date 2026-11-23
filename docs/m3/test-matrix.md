# M3 — Test and Acceptance Matrix

## 1. Automated validation

- **143 tests, all passing** on `feat/m3`.
  - 130 M2/M2A tests remain passing.
  - 13 M3 tests cover contracts, registry, extractors, cache, failures, CLI,
    source integrity, and schema/engine compatibility.
- `python -m unittest discover -s tests -t .`
- `python -m compileall -q src tests`
- All fixtures are synthetic and generated in temporary directories.
- No real documents, personal paths/datasets, external services, or model calls.

## 2. M3 test inventory

| Area | Coverage |
| --- | --- |
| Registry/contracts | Nine declared extractors, versions, suffix lookup, evidence round-trip/bounds |
| Formats | TXT, HTML, RTF, DOCX, PPTX, XLSX, text PDF, blank/metadata-only PDF, encrypted PDF, malformed PDF, unsupported binary |
| Provenance | Title/author/heading source fields, counts, observations, raw metadata |
| Status semantics | `success`, `metadata_only`, `empty`, `unsupported`, `encrypted`, `malformed`, `timeout`, `crashed`, `resource_limit`/input limit, `hash_unavailable`, `source_changed` |
| Cache | Repeat run cache hits/zero workers; changed config fingerprint invalidates; duplicate content shares one evidence record and links both occurrences |
| Isolation | Real subprocess worker, timeout, crash injection, input cap, archive preflight |
| CLI | `evidence` exit 7 on failures, `evidence-stats` JSON, `evidence-export` JSONL/CSV |
| Source integrity | Source snapshot hashes unchanged by extraction |
| Schema | v1 and v3 migration paths to v5; evidence tables present |
| Regression | All M1/M1A/M2/M2A tests remain green |

## 3. Synthetic corpus

The compact corpus generator in `benchmarks/m3_evidence_benchmark.py` creates
61 files:

- 24 TXT, 8 HTML, 4 DOCX, 4 PPTX, 4 XLSX, 4 text PDFs, 2 blank/metadata PDFs,
  8 duplicate TXT, 1 encrypted PDF, 1 malformed PDF, 1 unsupported binary.

The benchmark validates 14 synthetic title observations against ground truth
(HTML/DOCX/blank PDF metadata); all 14 matched. More complex truth criteria are
not claimed.

## 4. Security validation

- Worker process isolation and timeout/crash handling tested.
- OOXML archive entry/expansion caps and XML DTD/entity rejection implemented;
  `defusedxml` parses bounded XML parts.
- HTML extraction uses a local stdlib parser with script/style skipping; no
  network fetch.
- No macros, formulas, embedded executable payloads, external entities, or OCR.
- Read-only source invariant checked by before/after SHA-256 snapshots.
- Windows-specific memory rlimits are not available; documented.

## 5. Packaging and import smoke

- `reclaim-0.4.0-py3-none-any.whl` built with `pip wheel . --no-deps`.
- Installed into a virtual environment with host evidence/GUI dependencies
  available; `reclaim --version` reported `0.4.0`.
- Installed-wheel `reclaim evidence` extracted a synthetic TXT through the
  subprocess worker; `evidence-stats` returned JSON; both exit 0.
- `reclaim.exe` and `reclaim-gui.exe` entry points were generated.
- The wheel does not bundle Qt or parser wheels; the `evidence` extra declares
  them. A fully clean extra-only environment was not separately rebuilt.
- No installer/signing/release distribution validation.

## 6. Acceptance checklist

| M3 gate | Status |
| --- | --- |
| Versioned extractor registry | PASS |
| Approved v1 format scope | PASS for PDF/DOCX/PPTX/XLSX/TXT/MD/HTML/RTF; legacy OLE metadata path implemented but not exercised with a real OLE fixture |
| Provenance-rich normalized evidence | PASS (observations + source/detail) |
| Bounded isolation and crash/timeout | PASS on Windows synthetic tests; POSIX rlimits untested |
| No network/macros/unsafe expansion | PASS by implementation and archive preflight |
| Content cache/invalidation | PASS |
| Schema migration/compatibility | PASS (v1/v3 paths to v5 tested; v4 is a straight version bump) |
| CLI extraction/status/export | PASS |
| Deterministic synthetic E2E | PASS |
| Quality/performance measurement | PASS (compact 61-file benchmark) |
| No source mutation | PASS (source_modified_count 0) |
| M1/M2 regressions | PASS (130 prior tests) |
| Packaging/import smoke | PASS (wheel 0.4.0, CLI evidence/stats from installed wheel; host deps) |
| Documentation/limitations | PASS (M3 docs) |

## 7. Not verified / limitations

- macOS/Linux extraction and POSIX rlimits.
- Full private-collection scale; 61-file compact corpus only.
- PDF `pdfminer.six` fallback is implemented but the pypdf failure path was not
  forced in the regression suite.
- OCR/image-only text, PDF structure/heading inference.
- Legacy OLE metadata extraction is implemented but not exercised against a real
  `.doc/.xls/.ppt` compound-file fixture; legacy body text remains out of scope.
- RTF metadata/objects.
- Worker memory peak (not measured on Windows).
- Word/Excel pagination or rendered layout.
- Full dependency redistribution/legal review; licenses are recorded in
  [dependencies](dependencies.md).
- M3 does not implement classification, naming, semantic search, embeddings,
  SLM, or a GUI evidence browser.
