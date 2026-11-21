"""Streaming SHA-256 hashing with change detection.

Hashing is read-only.  A file whose size or modification time changes during
a read is reported as an analysis failure rather than being recorded with a
digest that may not describe a stable state.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    CHANGED_DURING_READ,
    NOT_REGULAR_FILE,
    SIZE_MISMATCH,
    ErrorInfo,
    classify_os_error,
)
from .paths import long_path

READ_CHUNK = 4 * 1024 * 1024
SIGNATURE_BYTES = 64


@dataclass(frozen=True)
class HashOutcome:
    status: str  # ok | error
    digest: str | None
    signature_hex: str | None
    bytes_read: int
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.digest is not None


def hash_file(
    path: str | Path,
    *,
    expected_size: int,
    expected_mtime_ns: int,
    chunk_bytes: int = READ_CHUNK,
) -> HashOutcome:
    """Compute a full SHA-256 digest, detecting files that change mid-read."""

    started = time.monotonic()
    target = long_path(path)
    try:
        before = os.stat(target)
    except OSError as exc:
        info = classify_os_error(exc)
        return _error(info, 0, started)

    if not os.path.isfile(target):
        return _error(
            ErrorInfo(NOT_REGULAR_FILE, "Entry is not a regular file"), 0, started
        )
    if before.st_size != expected_size:
        return _error(
            ErrorInfo(
                SIZE_MISMATCH,
                f"Size changed since inventory: expected {expected_size}, saw {before.st_size}",
            ),
            0,
            started,
        )
    if before.st_mtime_ns != expected_mtime_ns:
        return _error(
            ErrorInfo(
                CHANGED_DURING_READ,
                "Modification time changed since inventory",
            ),
            0,
            started,
        )

    digest = hashlib.sha256()
    head = b""
    bytes_read = 0
    try:
        with open(target, "rb") as handle:
            while True:
                block = handle.read(chunk_bytes)
                if not block:
                    break
                if len(head) < SIGNATURE_BYTES:
                    head += block[: SIGNATURE_BYTES - len(head)]
                digest.update(block)
                bytes_read += len(block)
    except OSError as exc:
        info = classify_os_error(exc)
        return _error(info, bytes_read, started)

    try:
        after = os.stat(target)
    except OSError as exc:
        info = classify_os_error(exc)
        return _error(info, bytes_read, started)

    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        return _error(
            ErrorInfo(
                CHANGED_DURING_READ,
                "File changed while it was being read; digest discarded",
            ),
            bytes_read,
            started,
        )
    if bytes_read != before.st_size:
        return _error(
            ErrorInfo(
                SIZE_MISMATCH,
                f"Read {bytes_read} bytes, expected {before.st_size}",
            ),
            bytes_read,
            started,
        )

    return HashOutcome(
        status="ok",
        digest=digest.hexdigest(),
        signature_hex=head.hex() if head else None,
        bytes_read=bytes_read,
        duration_ms=_elapsed_ms(started),
    )


def verify_file(
    path: str | Path, *, expected_digest: str, chunk_bytes: int = READ_CHUNK
) -> tuple[bool, ErrorInfo | None]:
    """Re-read a file and compare it with a stored digest."""

    digest = hashlib.sha256()
    try:
        with open(long_path(path), "rb") as handle:
            while True:
                block = handle.read(chunk_bytes)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        return False, classify_os_error(exc)
    actual = digest.hexdigest()
    if actual != expected_digest:
        return False, ErrorInfo(
            "HASH_MISMATCH",
            f"Stored digest {expected_digest[:12]}... does not match {actual[:12]}...",
        )
    return True, None


def _error(info: ErrorInfo, bytes_read: int, started: float) -> HashOutcome:
    return HashOutcome(
        status="error",
        digest=None,
        signature_hex=None,
        bytes_read=bytes_read,
        duration_ms=_elapsed_ms(started),
        error_code=info.code,
        error_message=info.message,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
