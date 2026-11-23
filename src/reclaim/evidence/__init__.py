"""M3 evidence extraction: registry, bounded workers, cache, and reporting."""

from __future__ import annotations

from .contracts import (
    EVIDENCE_SCHEMA_ID,
    EVIDENCE_SCHEMA_VERSION,
    EvidenceRecord,
    ExtractionLimits,
)
from .registry import EvidenceSettings, ExtractorSpec, extractor_for_suffix, registry_dict
from .service import EvidenceError, EvidenceRunResult, EvidenceService, EvidenceStats

__all__ = [
    "EVIDENCE_SCHEMA_ID",
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceRecord",
    "ExtractionLimits",
    "EvidenceSettings",
    "ExtractorSpec",
    "extractor_for_suffix",
    "registry_dict",
    "EvidenceError",
    "EvidenceRunResult",
    "EvidenceService",
    "EvidenceStats",
]
