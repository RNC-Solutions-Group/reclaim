"""Subprocess worker for bounded document extraction.

The parent process never parses a document itself.  This worker receives a
small JSON request, performs one extraction, and writes a bounded JSON response.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .contracts import (
    STATUS_CRASHED,
    STATUS_RESOURCE_LIMIT,
    ExtractionLimits,
)
from .extractors import ExtractionFailure, extract_path
from .registry import EvidenceSettings


def _request_limits(data: dict) -> ExtractionLimits:
    raw = data.get("limits", {})
    return ExtractionLimits(
        max_input_bytes=int(raw.get("max_input_bytes", 32 * 1024 * 1024)),
        max_text_chars=int(raw.get("max_text_chars", 500_000)),
        max_metadata_entries=int(raw.get("max_metadata_entries", 64)),
        max_metadata_value_chars=int(raw.get("max_metadata_value_chars", 1000)),
        timeout_seconds=float(raw.get("timeout_seconds", 20)),
        max_memory_bytes=int(raw.get("max_memory_bytes", 512 * 1024 * 1024)),
        max_archive_entries=int(raw.get("max_archive_entries", 2000)),
        max_expanded_bytes=int(raw.get("max_expanded_bytes", 256 * 1024 * 1024)),
    )


def _write(response_path: Path, payload: dict) -> None:
    response_path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m reclaim.evidence.worker REQUEST RESPONSE", file=sys.stderr)
        return 2
    request_path = Path(args[0])
    response_path = Path(args[1])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        limits = _request_limits(request)
        settings = EvidenceSettings(limits=limits)
        record = extract_path(
            request["path"],
            str(request.get("content_sha256", "")),
            settings,
            extractor_id=request.get("extractor_id"),
        )
        _write(response_path, {"ok": True, "evidence": record.as_dict()})
        return 0
    except ExtractionFailure as exc:
        _write(
            response_path,
            {
                "ok": False,
                "status": exc.status,
                "error_code": exc.code,
                "error_message": exc.message,
            },
        )
        return 0
    except MemoryError:
        _write(
            response_path,
            {
                "ok": False,
                "status": STATUS_RESOURCE_LIMIT,
                "error_code": "MEMORY_LIMIT",
                "error_message": "Extraction exceeded available memory",
            },
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary
        _write(
            response_path,
            {
                "ok": False,
                "status": STATUS_CRASHED,
                "error_code": "WORKER_EXCEPTION",
                "error_message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3)[-4000:],
            },
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
