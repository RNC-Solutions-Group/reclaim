"""Extractor registry and configuration fingerprinting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import EXTRACTOR_API_VERSION, ExtractionLimits


@dataclass(frozen=True)
class ExtractorSpec:
    extractor_id: str
    version: str
    formats: tuple[str, ...]
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    description: str = ""

    def supports(self, suffix: str) -> bool:
        return suffix.casefold() in self.formats

    def as_dict(self) -> dict[str, Any]:
        return {
            "extractor_id": self.extractor_id,
            "version": self.version,
            "api_version": EXTRACTOR_API_VERSION,
            "formats": list(self.formats),
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "limitations": list(self.limitations),
            "description": self.description,
        }


EXTRACTOR_SPECS: tuple[ExtractorSpec, ...] = (
    ExtractorSpec(
        "pdf-pypdf",
        "1",
        (".pdf",),
        ("metadata", "text", "page_count", "encryption_detection"),
        ("pypdf", "pdfminer.six"),
        (
            "No OCR; scanned/image-only PDFs produce empty text.",
            "No headings/reading-order inference.",
            "No JavaScript, launch actions, embedded files, or attachments are executed.",
        ),
        "PDF metadata and bounded page text; pdfminer fallback only after pypdf failure.",
    ),
    ExtractorSpec(
        "docx-python-docx",
        "1",
        (".docx",),
        ("metadata", "text", "headings"),
        ("python-docx",),
        (
            "Page count is not reliable and is left absent.",
            "No rendering, field evaluation, macros, or embedded-object extraction.",
        ),
        "DOCX core properties, paragraph/table text, and heading styles.",
    ),
    ExtractorSpec(
        "pptx-python-pptx",
        "1",
        (".pptx",),
        ("metadata", "text", "slide_count", "headings"),
        ("python-pptx",),
        (
            "Shape text is concatenated in slide order; no visual reading order.",
            "No media rendering or macro execution.",
        ),
        "PPTX core properties, slide text, titles, and slide count.",
    ),
    ExtractorSpec(
        "xlsx-openpyxl",
        "1",
        (".xlsx",),
        ("metadata", "text", "sheet_count"),
        ("openpyxl",),
        (
            "No formula evaluation; cached values only.",
            "No macros, external-link retrieval, or chart/image rendering.",
        ),
        "XLSX sheet names and bounded cell text with read-only/data-only loading.",
    ),
    ExtractorSpec(
        "text-plain",
        "1",
        (".txt", ".log", ".csv", ".tsv"),
        ("text",),
        (),
        ("Encoding is decoded as UTF-8 with a conservative fallback.",),
        "Bounded plain-text extraction.",
    ),
    ExtractorSpec(
        "text-markdown",
        "1",
        (".md", ".markdown"),
        ("text", "headings", "type_hint"),
        (),
        ("Markdown headings are observed from ATX/Setext syntax; no rendering.",),
        "Bounded Markdown text and heading extraction.",
    ),
    ExtractorSpec(
        "html-stdlib",
        "1",
        (".html", ".htm", ".xhtml"),
        ("metadata", "text", "headings"),
        (),
        (
            "No network retrieval, external entity expansion, script execution, or resource fetching.",
            "Only local markup text/metadata is observed.",
        ),
        "Safe local HTML title/meta/heading/text extraction with stdlib HTMLParser.",
    ),
    ExtractorSpec(
        "rtf-striprtf",
        "1",
        (".rtf",),
        ("text",),
        ("striprtf",),
        (
            "Text-only; no RTF metadata/object/embedded-payload extraction.",
            "RTF is not rendered.",
        ),
        "Bounded RTF text stripping; no macro/object execution.",
    ),
    ExtractorSpec(
        "ole-metadata",
        "1",
        (".doc", ".xls", ".ppt", ".dot", ".xlt", ".pot"),
        ("metadata", "format_hint"),
        ("olefile",),
        (
            "Metadata-only; legacy document text is not extracted.",
            "No macro, OLE object, embedded payload, or encryption bypass.",
        ),
        "Legacy OLE SummaryInformation metadata only.",
    ),
)

_BY_SUFFIX: dict[str, ExtractorSpec] = {}
for _spec in EXTRACTOR_SPECS:
    for _suffix in _spec.formats:
        _BY_SUFFIX[_suffix] = _spec


def extractor_for_suffix(suffix: str) -> ExtractorSpec | None:
    return _BY_SUFFIX.get(suffix.casefold())


def extractor_for_path(path: str | Path) -> ExtractorSpec | None:
    return extractor_for_suffix(Path(path).suffix)


def supported_suffixes() -> tuple[str, ...]:
    return tuple(sorted(_BY_SUFFIX))


def registry_dict() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in EXTRACTOR_SPECS]


@dataclass(frozen=True)
class EvidenceSettings:
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)
    evidence_schema_version: int = 1
    worker_isolation: str = "subprocess"
    format_registry: tuple[tuple[str, str], ...] = tuple(
        (spec.extractor_id, spec.version) for spec in EXTRACTOR_SPECS
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_schema_version": self.evidence_schema_version,
            "limits": self.limits.as_dict(),
            "worker_isolation": self.worker_isolation,
            "format_registry": [list(item) for item in self.format_registry],
        }

    def config_fingerprint(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
