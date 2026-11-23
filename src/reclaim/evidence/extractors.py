"""Bounded built-in extractors for the M3 evidence subsystem.

Every extractor is read-only and bounded.  Libraries are imported lazily so a
missing optional dependency produces an explicit failure instead of a crash.
"""

from __future__ import annotations

import datetime as _datetime
import re
import zipfile
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .contracts import (
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_MALFORMED,
    STATUS_METADATA_ONLY,
    STATUS_UNSUPPORTED,
    EvidenceRecord,
    ExtractionLimits,
    bounded_text_chars,
    first_text_sample,
    keyword_candidates,
    observation,
    status_for_content,
)
from .registry import EvidenceSettings, ExtractorSpec, extractor_for_suffix


class ExtractionFailure(Exception):
    def __init__(self, status: str, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _require(module: str, package: str) -> Any:
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as exc:
        raise ExtractionFailure(
            "dependency_missing",
            "DEPENDENCY_MISSING",
            f"Extractor dependency {package!r} is not installed",
        ) from exc


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _metadata(record: EvidenceRecord, metadata: dict[str, Any], limits: ExtractionLimits) -> None:
    mapping = {
        "title": "title",
        "author": "author",
        "subject": "subject",
        "keywords": "keywords",
        "creator": "creator",
        "producer": "producer",
        "category": "category",
        "comments": "comments",
        "description": "description",
        "last_modified_by": "last_modified_by",
        "revision": "revision",
    }
    for key, label in mapping.items():
        value = _iso(metadata.get(key))
        if value is None:
            continue
        value = value[: limits.max_metadata_value_chars]
        record.raw_metadata[label] = value
        record.observations.append(
            observation(label, value, "document_property", detail=f"property:{key}")
        )
    title = _iso(metadata.get("title"))
    if title:
        record.title = observation(
            "title", title[: limits.max_metadata_value_chars], "document_property", detail="property:title"
        )
    author = _iso(metadata.get("author"))
    if author:
        record.authors.append(
            observation("author", author[: limits.max_metadata_value_chars], "document_property", detail="property:author")
        )
    for key in ("created", "modified", "last_printed"):
        value = _iso(metadata.get(key))
        if value:
            record.dates.append(
                observation(key, value[: limits.max_metadata_value_chars], "document_property", detail=f"property:{key}")
            )
    for key, value in list(record.raw_metadata.items())[: limits.max_metadata_entries]:
        record.raw_metadata[key] = value[: limits.max_metadata_value_chars]


def _finish(
    record: EvidenceRecord,
    text: str,
    *,
    limitations: list[str],
    limits: ExtractionLimits,
) -> EvidenceRecord:
    text = text or ""
    record.text_chars = bounded_text_chars(text, limits.max_text_chars)
    record.text_sample = first_text_sample(text, min(4000, limits.max_text_chars))
    record.type_hints.append(
        observation("declared_format", record.source_format, "extension", detail="filename suffix")
    )
    if record.text_chars:
        record.keywords = keyword_candidates(text, 20)
        record.limitations.append("keywords are frequency heuristics, not document-established topics")
    elif record.has_metadata:
        record.warnings.append("No extractable text; metadata-only outcome")
    else:
        record.warnings.append("No extractable text or document metadata")
    record.status = status_for_content(has_text=record.has_text, has_metadata=record.has_metadata)
    if record.text_chars >= limits.max_text_chars:
        record.warnings.append(f"Text extraction truncated at {limits.max_text_chars} characters")
    record.limitations.extend(limitations)
    record.warnings = record.warnings[:30]
    record.limitations = list(dict.fromkeys(record.limitations))[:30]
    record.observations = record.observations[:500]
    return record


def _base(
    path: Path,
    expected_sha: str,
    spec: ExtractorSpec,
    settings: EvidenceSettings,
) -> EvidenceRecord:
    return EvidenceRecord(
        content_sha256=expected_sha,
        extractor_id=spec.extractor_id,
        extractor_version=spec.version,
        config_fingerprint=settings.config_fingerprint(),
        status=STATUS_MALFORMED,
        source_format=path.suffix.casefold(),
    )


def _check_size(path: Path, limits: ExtractionLimits) -> None:
    size = path.stat().st_size
    if size > limits.max_input_bytes:
        raise ExtractionFailure(
            "input_too_large",
            "INPUT_TOO_LARGE",
            f"Input is {size} bytes; limit is {limits.max_input_bytes}",
        )


def _check_ooxml_container(path: Path, limits: ExtractionLimits) -> None:
    """Reject archive bombs/XML entity declarations before library parsing.

    Office formats are ZIP packages.  We inspect the central directory without
    extracting entries, limit entry count/declared expansion, and reject DTD or
    entity declarations in XML parts.  No nested archive is ever extracted.
    """

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_archive_entries:
                raise ExtractionFailure(
                    "resource_limit",
                    "ARCHIVE_ENTRY_LIMIT",
                    f"Archive has {len(infos)} entries; limit is {limits.max_archive_entries}",
                )
            expanded = sum(max(0, info.file_size) for info in infos)
            if expanded > limits.max_expanded_bytes:
                raise ExtractionFailure(
                    "resource_limit",
                    "ARCHIVE_EXPANSION_LIMIT",
                    f"Archive expands to {expanded} bytes; limit is {limits.max_expanded_bytes}",
                )
            for info in infos:
                if info.flag_bits & 0x1:
                    raise ExtractionFailure(
                        "encrypted", "ARCHIVE_ENCRYPTED", "Encrypted Office archive entry"
                    )
                name = info.filename.casefold()
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                if info.file_size > 256 * 1024:
                    continue
                data = archive.read(info)[: 256 * 1024]
                upper = data.upper()
                if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                    raise ExtractionFailure(
                        "malformed",
                        "XML_ENTITY_REJECTED",
                        "Office XML DTD/entity declarations are not allowed",
                    )
                try:
                    from defusedxml import ElementTree as SafeElementTree

                    SafeElementTree.fromstring(data)
                except ImportError:
                    # The byte-level DTD check above still applies if the
                    # optional hardening package is unavailable.
                    pass
                except Exception as exc:  # noqa: BLE001
                    raise ExtractionFailure(
                        "malformed",
                        "XML_PARSE_REJECTED",
                        f"Office XML part was rejected: {type(exc).__name__}",
                    ) from exc
    except ExtractionFailure:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionFailure(
            "malformed", "ARCHIVE_INVALID", f"Office archive could not be read: {exc}"
        ) from exc


def _extract_pdf(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    pypdf = _require("pypdf", "pypdf")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    text_parts: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {}
    try:
        reader = pypdf.PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise ExtractionFailure("encrypted", "ENCRYPTED", "PDF is encrypted")
        metadata = dict(reader.metadata or {})
        page_count = len(reader.pages)
        record.page_count = page_count
        for page in reader.pages:
            if sum(len(part) for part in text_parts) >= limits.max_text_chars:
                break
            try:
                extracted = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - bounded parser failure
                warnings.append(f"page text extraction failed: {type(exc).__name__}")
                continue
            if extracted:
                text_parts.append(extracted)
    except ExtractionFailure:
        raise
    except Exception as pypdf_error:  # noqa: BLE001 - fallback boundary
        try:
            pdfminer = _require("pdfminer.high_level", "pdfminer.six")
            text = pdfminer.extract_text(str(path), maxpages=1000) or ""
            record.page_count = None
            text_parts = [text]
            warnings.append("pypdf failed; pdfminer.six fallback used")
        except ExtractionFailure:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionFailure(
                "malformed",
                "PDF_PARSE_FAILED",
                f"PDF could not be parsed ({type(pypdf_error).__name__}; fallback {type(exc).__name__})",
            ) from exc

    metadata_view = {
        "title": metadata.get("/Title"),
        "author": metadata.get("/Author"),
        "subject": metadata.get("/Subject"),
        "keywords": metadata.get("/Keywords"),
        "creator": metadata.get("/Creator"),
        "producer": metadata.get("/Producer"),
        "created": metadata.get("/CreationDate"),
        "modified": metadata.get("/ModDate"),
    }
    _metadata(record, metadata_view, limits)
    record.warnings.extend(warnings)
    return _finish(
        record,
        "\n\n".join(text_parts),
        limitations=[
            "PDF headings/reading order are not inferred",
            "Image-only/scanned PDFs have no OCR text in M3",
        ],
        limits=limits,
    )


def _extract_docx(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    docx = _require("docx", "python-docx")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    _check_ooxml_container(path, limits)
    try:
        document = docx.Document(str(path))
        core = document.core_properties
        _metadata(
            record,
            {
                "title": core.title,
                "author": core.author,
                "subject": core.subject,
                "keywords": core.keywords,
                "comments": core.comments,
                "category": core.category,
                "last_modified_by": core.last_modified_by,
                "revision": core.revision,
                "created": core.created,
                "modified": core.modified,
                "last_printed": core.last_printed,
            },
            limits,
        )
        parts: list[str] = []
        total = 0
        for paragraph in document.paragraphs:
            style = getattr(paragraph.style, "name", "") or ""
            text = paragraph.text.strip()
            if not text:
                continue
            if style.lower().startswith("heading"):
                record.headings.append(
                    observation("heading", text, "text", detail=f"paragraph_style:{style}")
                )
            parts.append(text)
            total += len(text)
            if total >= limits.max_text_chars:
                break
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
                    total += len(cells)
                if total >= limits.max_text_chars:
                    break
            if total >= limits.max_text_chars:
                break
    except ExtractionFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionFailure("malformed", "DOCX_PARSE_FAILED", f"DOCX could not be parsed: {type(exc).__name__}") from exc
    return _finish(
        record,
        "\n".join(parts),
        limitations=[
            "DOCX page count is not reliable and is not reported",
            "Macros/embedded objects are not extracted or executed",
        ],
        limits=limits,
    )


def _extract_pptx(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    pptx = _require("pptx", "python-pptx")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    _check_ooxml_container(path, limits)
    try:
        presentation = pptx.Presentation(str(path))
        core = presentation.core_properties
        _metadata(
            record,
            {
                "title": core.title,
                "author": core.author,
                "subject": core.subject,
                "keywords": core.keywords,
                "comments": core.comments,
                "category": core.category,
                "last_modified_by": core.last_modified_by,
                "revision": core.revision,
                "created": core.created,
                "modified": core.modified,
                "last_printed": core.last_printed,
            },
            limits,
        )
        record.slide_count = len(presentation.slides)
        parts: list[str] = []
        total = 0
        for slide_number, slide in enumerate(presentation.slides, start=1):
            title = slide.shapes.title
            if title is not None and getattr(title, "text", "").strip():
                record.headings.append(
                    observation(
                        "slide_title",
                        title.text.strip(),
                        "slide",
                        detail=f"slide:{slide_number}",
                    )
                )
            for shape in slide.shapes:
                text = getattr(shape, "text", "") or ""
                if not text.strip():
                    continue
                parts.append(text.strip())
                total += len(text)
                if total >= limits.max_text_chars:
                    break
            if total >= limits.max_text_chars:
                break
    except ExtractionFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionFailure("malformed", "PPTX_PARSE_FAILED", f"PPTX could not be parsed: {type(exc).__name__}") from exc
    return _finish(
        record,
        "\n\n".join(parts),
        limitations=[
            "PPTX shape text is concatenated; visual reading order is not inferred",
            "Notes/media are not rendered or extracted",
        ],
        limits=limits,
    )


def _extract_xlsx(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    openpyxl = _require("openpyxl", "openpyxl")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    _check_ooxml_container(path, limits)
    try:
        workbook = openpyxl.load_workbook(
            str(path), read_only=True, data_only=True, keep_links=False
        )
        try:
            record.sheet_count = len(workbook.sheetnames)
            for name in workbook.sheetnames:
                record.observations.append(
                    observation("sheet_name", name, "sheet", detail="workbook sheet")
                )
            parts: list[str] = []
            total = 0
            for worksheet in workbook.worksheets:
                for row in worksheet.iter_rows(values_only=True):
                    values = []
                    for value in row:
                        if value is None:
                            continue
                        text = _iso(value)
                        if text:
                            values.append(text)
                    if values:
                        line = " | ".join(values)
                        parts.append(line)
                        total += len(line)
                    if total >= limits.max_text_chars:
                        break
                if total >= limits.max_text_chars:
                    break
        finally:
            workbook.close()
    except ExtractionFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "encrypt" in message.casefold():
            raise ExtractionFailure("encrypted", "ENCRYPTED", "XLSX is encrypted") from exc
        raise ExtractionFailure("malformed", "XLSX_PARSE_FAILED", f"XLSX could not be parsed: {type(exc).__name__}") from exc
    return _finish(
        record,
        "\n".join(parts),
        limitations=[
            "Spreadsheet formulas are not evaluated; data-only cached values are read",
            "Macros/external links are not executed or retrieved",
        ],
        limits=limits,
    )


def _extract_text(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtractionFailure("malformed", "READ_FAILED", f"Text could not be read: {type(exc).__name__}") from exc
    text = raw.decode("utf-8-sig", errors="replace")
    if "\ufffd" in text and not text.startswith("\ufeff"):
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            pass
    return _finish(
        record,
        text,
        limitations=["Encoding is decoded conservatively; no language detection"],
        limits=limits,
    )


def _extract_markdown(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    record = _extract_text(path, expected, spec, settings)
    text = record.text_sample
    for line in text.splitlines()[:200]:
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            record.headings.append(
                observation(
                    "heading",
                    match.group(2).strip(),
                    "text",
                    detail=f"markdown_level:{len(match.group(1))}",
                )
            )
    record.headings = record.headings[:100]
    return record


class _HTMLCollector(HTMLParser):
    def __init__(self, limits: ExtractionLimits) -> None:
        super().__init__(convert_charrefs=True)
        self.limits = limits
        self.title_parts: list[str] = []
        self.headings: list[Observation] = []
        self.metadata: dict[str, str] = {}
        self.text_parts: list[str] = []
        self.current_heading: tuple[int, str] | None = None
        self.skip_depth = 0
        self.title_depth = 0
        self.total = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in ("script", "style", "noscript"):
            self.skip_depth += 1
        if tag == "title":
            self.title_depth += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.current_heading = (int(tag[1]), "")
        if tag == "meta":
            attributes = {key.casefold(): value or "" for key, value in attrs}
            name = attributes.get("name") or attributes.get("property")
            content = attributes.get("content")
            if name and content:
                self.metadata[name.casefold()[:100]] = content[: self.limits.max_metadata_value_chars]

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in ("script", "style", "noscript") and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.current_heading:
            level, text = self.current_heading
            if text.strip():
                self.headings.append(
                    observation("heading", text.strip(), "text", detail=f"html_level:{level}")
                )
            self.current_heading = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.title_depth:
            self.title_parts.append(data)
            return
        if self.current_heading:
            level, text = self.current_heading
            self.current_heading = (level, text + data)
        if data.strip():
            self.text_parts.append(data)
            self.total += len(data)


def _extract_html(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        parser = _HTMLCollector(limits)
        parser.feed(text)
        parser.close()
    except Exception as exc:  # noqa: BLE001
        raise ExtractionFailure("malformed", "HTML_PARSE_FAILED", f"HTML could not be parsed: {type(exc).__name__}") from exc
    title = "".join(parser.title_parts).strip()
    if title:
        record.title = observation("title", title, "document_property", detail="html.title")
    for key, value in list(parser.metadata.items())[: limits.max_metadata_entries]:
        record.raw_metadata[key] = value
        record.observations.append(
            observation(key, value, "document_property", detail=f"html.meta:{key}")
        )
    if "author" in parser.metadata:
        record.authors.append(
            observation("author", parser.metadata["author"], "document_property", detail="html.meta:author")
        )
    record.headings = parser.headings[:100]
    return _finish(
        record,
        " ".join(parser.text_parts),
        limitations=[
            "HTML is parsed locally; external resources/scripts are never fetched or executed",
            "No rendering or visual reading order",
        ],
        limits=limits,
    )


def _extract_rtf(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    striprtf = _require("striprtf.striprtf", "striprtf")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    try:
        raw = path.read_bytes().decode("latin-1", errors="replace")
        text = striprtf.rtf_to_text(raw, errors="ignore")
    except Exception as exc:  # noqa: BLE001
        raise ExtractionFailure("malformed", "RTF_PARSE_FAILED", f"RTF could not be parsed: {type(exc).__name__}") from exc
    return _finish(
        record,
        text,
        limitations=[
            "RTF metadata/objects/embedded payloads are not extracted; text-only",
        ],
        limits=limits,
    )


def _extract_ole(path: Path, expected: str, spec: ExtractorSpec, settings: EvidenceSettings) -> EvidenceRecord:
    olefile = _require("olefile", "olefile")
    record = _base(path, expected, spec, settings)
    limits = settings.limits
    try:
        if not olefile.isOleFile(str(path)):
            raise ExtractionFailure("malformed", "NOT_OLE", "File is not an OLE compound document")
        with closing(olefile.OleFileIO(str(path))) as ole:
            metadata = ole.get_metadata()
            values = {
                "title": getattr(metadata, "title", None),
                "author": getattr(metadata, "author", None),
                "subject": getattr(metadata, "subject", None),
                "keywords": getattr(metadata, "keywords", None),
                "comments": getattr(metadata, "comments", None),
                "last_modified_by": getattr(metadata, "last_saved_by", None),
                "created": getattr(metadata, "create_time", None),
                "modified": getattr(metadata, "last_saved_time", None),
            }
            _metadata(record, values, limits)
    except ExtractionFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionFailure("malformed", "OLE_PARSE_FAILED", f"OLE metadata could not be parsed: {type(exc).__name__}") from exc
    record.status = STATUS_METADATA_ONLY if record.has_metadata else STATUS_EMPTY
    record.limitations.extend(
        [
            "Legacy OLE documents are metadata-only; body text is not extracted",
            "Macros/embedded OLE objects are not executed or extracted",
        ]
    )
    return record


_EXTRACTOR_FUNCTIONS = {
    "pdf-pypdf": _extract_pdf,
    "docx-python-docx": _extract_docx,
    "pptx-python-pptx": _extract_pptx,
    "xlsx-openpyxl": _extract_xlsx,
    "text-plain": _extract_text,
    "text-markdown": _extract_markdown,
    "html-stdlib": _extract_html,
    "rtf-striprtf": _extract_rtf,
    "ole-metadata": _extract_ole,
}


def extract_path(
    path: str | Path,
    expected_sha256: str,
    settings: EvidenceSettings,
    *,
    extractor_id: str | None = None,
) -> EvidenceRecord:
    source = Path(path)
    _check_size(source, settings.limits)
    spec = extractor_for_suffix(source.suffix)
    if spec is None and extractor_id:
        # A caller may explicitly request an extractor only for a supported
        # suffix; do not allow extension/extractor mismatch to smuggle formats.
        raise ExtractionFailure(STATUS_UNSUPPORTED, "UNSUPPORTED_FORMAT", f"Unsupported suffix: {source.suffix}")
    if spec is None:
        raise ExtractionFailure(STATUS_UNSUPPORTED, "UNSUPPORTED_FORMAT", f"Unsupported suffix: {source.suffix}")
    if extractor_id and extractor_id != spec.extractor_id:
        raise ExtractionFailure(STATUS_UNSUPPORTED, "EXTRACTOR_MISMATCH", "Extractor does not match suffix")
    function = _EXTRACTOR_FUNCTIONS.get(spec.extractor_id)
    if function is None:
        raise ExtractionFailure(STATUS_UNSUPPORTED, "UNSUPPORTED_EXTRACTOR", f"No extractor for {spec.extractor_id}")
    return function(source, expected_sha256, spec, settings)
