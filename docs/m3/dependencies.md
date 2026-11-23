# M3 — Dependency and License Inventory

## 1. Packaging boundary

Evidence parsers are an optional install extra:

```toml
[project.optional-dependencies]
evidence = [
  "pypdf>=6.0,<7",
  "pdfminer.six>=20240706",
  "python-docx>=1.1,<2",
  "python-pptx>=0.6.23,<2",
  "openpyxl>=3.1,<4",
  "olefile>=0.47,<1",
  "striprtf>=0.0.26,<1",
  "defusedxml>=0.7.1,<1",
]
```

The base engine remains dependency-light. The optional extra does not decide the
product's final open-source/proprietary license. All direct licenses below are
permissive; the project retains distribution flexibility subject to legal review.

## 2. Direct dependencies (validated environment)

| Package | Version | License | Purpose |
| --- | --- | --- | --- |
| pypdf | 6.19.0 | BSD-3-Clause | PDF metadata/text |
| pdfminer.six | 20260107 | MIT | Bounded PDF fallback |
| python-docx | 1.2.0 | MIT | DOCX properties/text/headings |
| python-pptx | 1.0.2 | MIT | PPTX properties/slides/text |
| openpyxl | 3.1.5 | MIT | XLSX read-only/data-only |
| olefile | 0.47 | BSD | Legacy OLE metadata |
| striprtf | 0.0.35 | BSD-3-Clause | RTF text stripping |
| defusedxml | 0.7.1 | PSF-2.0 | Hardened XML parsing in OOXML preflight |

## 3. Relevant transitive dependencies

| Package | Version | License | Pulled by |
| --- | --- | --- | --- |
| lxml | 6.1.3 | BSD-3-Clause | python-docx/python-pptx |
| Pillow | 13.0.0 | MIT-CMU | python-pptx |
| XlsxWriter | 3.3.4 | BSD-2-Clause | python-pptx |
| et-xmlfile | 2.0.0 | MIT | openpyxl |
| typing_extensions | 4.16.0 | PSF-2.0 | python-docx/python-pptx |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | PDF encryption support via pypdf |
| cffi | 2.1.1 | MIT | cryptography |
| pycparser | 3.12 | BSD-3-Clause | cffi |
| charset-normalizer | 3.5.3 | MIT | PDF/text-adjacent dependency set |

`cryptography`, `cffi`, and `pycparser` may be present in this development
environment through other packages; they are not all directly needed by the
M3 code paths and do not alter source content.

## 4. Security/licensing notes

- No AGPL PDF parser (e.g., PyMuPDF) is used.
- No LibreOffice/UNO is bundled.
- `defusedxml` is used only for bounded OOXML XML parts; Office ZIP preflight
  rejects DTD/entity declarations before parser libraries consume them.
- All dependencies are imported lazily; a missing parser yields an explicit
  `dependency_missing` evidence outcome rather than an unhandled exception.
- No dependency performs network calls in M3 code paths.
- A future binary distribution must include applicable license texts/notices for
  all shipped packages, including transitive dependencies and Qt/PySide6 if the
  GUI is bundled. This document is an engineering inventory, not legal advice or
  final redistribution approval.

## 5. Reproducibility

`pyproject.toml` pins compatible ranges, not hashes. A release build should
freeze exact versions/lockfile and re-run the M3 corpus and license inventory
against that environment. The validated versions above are the M3 development
baseline.
