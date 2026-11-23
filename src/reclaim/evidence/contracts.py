"""Versioned, provenance-rich evidence contract for M3 extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

EVIDENCE_SCHEMA_VERSION = 1
EXTRACTOR_API_VERSION = 1
EVIDENCE_SCHEMA_ID = "reclaim-evidence-v1"

STATUS_SUCCESS = "success"
STATUS_METADATA_ONLY = "metadata_only"
STATUS_EMPTY = "empty"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ENCRYPTED = "encrypted"
STATUS_MALFORMED = "malformed"
STATUS_TIMEOUT = "timeout"
STATUS_CRASHED = "crashed"
STATUS_RESOURCE_LIMIT = "resource_limit"
STATUS_INPUT_TOO_LARGE = "input_too_large"
STATUS_HASH_UNAVAILABLE = "hash_unavailable"
STATUS_SOURCE_UNAVAILABLE = "source_unavailable"
STATUS_SOURCE_CHANGED = "source_changed"
STATUS_DEPENDENCY_MISSING = "dependency_missing"

CACHEABLE_STATUSES = (
    STATUS_SUCCESS,
    STATUS_METADATA_ONLY,
    STATUS_EMPTY,
    STATUS_UNSUPPORTED,
)
FAILURE_STATUSES = (
    STATUS_ENCRYPTED,
    STATUS_MALFORMED,
    STATUS_TIMEOUT,
    STATUS_CRASHED,
    STATUS_RESOURCE_LIMIT,
    STATUS_INPUT_TOO_LARGE,
    STATUS_HASH_UNAVAILABLE,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_SOURCE_CHANGED,
    STATUS_DEPENDENCY_MISSING,
)

OBSERVATION_SOURCES = (
    "document_property",
    "page",
    "slide",
    "sheet",
    "text",
    "extension",
    "container",
)

MAX_TEXT_SAMPLE = 4000
MAX_METADATA_ENTRIES = 64
MAX_METADATA_VALUE = 1000
MAX_HEADINGS = 100
MAX_KEYWORDS = 30
MAX_WARNINGS = 30
MAX_OBSERVATIONS = 500


@dataclass(frozen=True)
class ExtractionLimits:
    max_input_bytes: int = 32 * 1024 * 1024
    max_text_chars: int = 500_000
    max_metadata_entries: int = MAX_METADATA_ENTRIES
    max_metadata_value_chars: int = MAX_METADATA_VALUE
    timeout_seconds: float = 20.0
    max_memory_bytes: int = 512 * 1024 * 1024
    max_archive_entries: int = 2000
    max_expanded_bytes: int = 256 * 1024 * 1024

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_text_chars": self.max_text_chars,
            "max_metadata_entries": self.max_metadata_entries,
            "max_metadata_value_chars": self.max_metadata_value_chars,
            "timeout_seconds": self.timeout_seconds,
            "max_memory_bytes": self.max_memory_bytes,
            "max_archive_entries": self.max_archive_entries,
            "max_expanded_bytes": self.max_expanded_bytes,
        }


@dataclass(frozen=True)
class Observation:
    """One observed value, including where it came from."""

    name: str
    value: str
    source: str
    detail: str = ""
    method: str = "observed"

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "detail": self.detail,
            "method": self.method,
        }


@dataclass
class EvidenceRecord:
    content_sha256: str
    extractor_id: str
    extractor_version: str
    config_fingerprint: str
    status: str
    source_format: str
    title: Observation | None = None
    authors: list[Observation] = field(default_factory=list)
    dates: list[Observation] = field(default_factory=list)
    headings: list[Observation] = field(default_factory=list)
    type_hints: list[Observation] = field(default_factory=list)
    page_count: int | None = None
    slide_count: int | None = None
    sheet_count: int | None = None
    text_chars: int = 0
    text_sample: str = ""
    keywords: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    raw_metadata: dict[str, str] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    evidence_schema_version: int = EVIDENCE_SCHEMA_VERSION

    @property
    def has_text(self) -> bool:
        return self.text_chars > 0 or bool(self.text_sample)

    @property
    def has_metadata(self) -> bool:
        return bool(self.raw_metadata) or self.title is not None or bool(self.authors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_schema_version": self.evidence_schema_version,
            "evidence_schema_id": EVIDENCE_SCHEMA_ID,
            "content_sha256": self.content_sha256,
            "extractor_id": self.extractor_id,
            "extractor_version": self.extractor_version,
            "config_fingerprint": self.config_fingerprint,
            "status": self.status,
            "source_format": self.source_format,
            "has_text": self.has_text,
            "has_metadata": self.has_metadata,
            "title": self.title.as_dict() if self.title else None,
            "authors": [item.as_dict() for item in self.authors],
            "dates": [item.as_dict() for item in self.dates],
            "headings": [item.as_dict() for item in self.headings],
            "type_hints": [item.as_dict() for item in self.type_hints],
            "page_count": self.page_count,
            "slide_count": self.slide_count,
            "sheet_count": self.sheet_count,
            "text_chars": self.text_chars,
            "text_sample": self.text_sample,
            "keywords": self.keywords,
            "warnings": self.warnings,
            "limitations": self.limitations,
            "raw_metadata": self.raw_metadata,
            "observations": [item.as_dict() for item in self.observations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRecord:
        def obs(value: Any) -> Observation | None:
            if not isinstance(value, dict):
                return None
            return Observation(
                name=str(value.get("name", "")),
                value=str(value.get("value", "")),
                source=str(value.get("source", "")),
                detail=str(value.get("detail", "")),
                method=str(value.get("method", "observed")),
            )

        def obs_list(value: Any) -> list[Observation]:
            if not isinstance(value, list):
                return []
            return [item for item in (obs(raw) for raw in value) if item is not None]

        return cls(
            evidence_schema_version=int(
                data.get("evidence_schema_version", EVIDENCE_SCHEMA_VERSION)
            ),
            content_sha256=str(data.get("content_sha256", "")),
            extractor_id=str(data.get("extractor_id", "")),
            extractor_version=str(data.get("extractor_version", "")),
            config_fingerprint=str(data.get("config_fingerprint", "")),
            status=str(data.get("status", STATUS_MALFORMED)),
            source_format=str(data.get("source_format", "")),
            title=obs(data.get("title")),
            authors=obs_list(data.get("authors")),
            dates=obs_list(data.get("dates")),
            headings=obs_list(data.get("headings")),
            type_hints=obs_list(data.get("type_hints")),
            page_count=_int_or_none(data.get("page_count")),
            slide_count=_int_or_none(data.get("slide_count")),
            sheet_count=_int_or_none(data.get("sheet_count")),
            text_chars=int(data.get("text_chars", 0) or 0),
            text_sample=str(data.get("text_sample", ""))[:MAX_TEXT_SAMPLE],
            keywords=[str(item) for item in data.get("keywords", [])][:MAX_KEYWORDS],
            warnings=[str(item) for item in data.get("warnings", [])][:MAX_WARNINGS],
            limitations=[str(item) for item in data.get("limitations", [])][:MAX_WARNINGS],
            raw_metadata={
                str(key): str(value)[:MAX_METADATA_VALUE]
                for key, value in dict(data.get("raw_metadata", {})).items()
            },
            observations=obs_list(data.get("observations"))[:MAX_OBSERVATIONS],
        )


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def observation(
    name: str,
    value: Any,
    source: str,
    *,
    detail: str = "",
    method: str = "observed",
) -> Observation:
    return Observation(
        name=name[:100],
        value=str(value).strip()[:MAX_METADATA_VALUE],
        source=source if source in OBSERVATION_SOURCES else "container",
        detail=detail[:200],
        method=method[:40],
    )


def status_for_content(*, has_text: bool, has_metadata: bool) -> str:
    if has_text:
        return STATUS_SUCCESS
    if has_metadata:
        return STATUS_METADATA_ONLY
    return STATUS_EMPTY


def first_text_sample(text: str, limit: int = MAX_TEXT_SAMPLE) -> str:
    return text[: max(0, limit)]


def bounded_text_chars(text: str, limit: int) -> int:
    return min(len(text), max(0, limit))


def keyword_candidates(text: str, limit: int = MAX_KEYWORDS) -> list[str]:
    """Deterministic frequency keywords; explicitly a heuristic, not evidence."""

    import re
    from collections import Counter

    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "not",
        "you",
        "your",
        "but",
        "all",
        "can",
        "will",
        "our",
        "their",
        "they",
        "its",
        "into",
        "than",
        "then",
        "when",
        "where",
        "which",
        "what",
        "how",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,30}", text.lower())
    counts = Counter(word for word in words if word not in stop)
    return [word for word, _ in counts.most_common(limit)]


def to_json(record: EvidenceRecord) -> str:
    return json.dumps(record.as_dict(), sort_keys=True, ensure_ascii=False)
