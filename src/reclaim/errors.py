"""Structured error codes used by inventory and hashing.

Error categories are stable strings so that reports, databases, and future
interfaces can group failures without parsing human-readable messages.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass


class ReclaimError(Exception):
    """Base class for expected, user-actionable Reclaim failures."""


class ConfigError(ReclaimError):
    """Configuration is missing, malformed, or unsafe."""


class SafetyError(ReclaimError):
    """An operation would violate a preservation or privacy boundary."""


class SourceUnavailableError(ReclaimError):
    """A registered source cannot be reached."""


class SchemaError(ReclaimError):
    """The project database schema is unsupported or corrupt."""


# Stable failure categories.  Keep these independent of OS wording.
PERMISSION_DENIED = "PERMISSION_DENIED"
NOT_FOUND = "NOT_FOUND"
PATH_TOO_LONG = "PATH_TOO_LONG"
INVALID_NAME = "INVALID_NAME"
SHARING_VIOLATION = "SHARING_VIOLATION"
CLOUD_UNAVAILABLE = "CLOUD_UNAVAILABLE"
DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
CHANGED_DURING_READ = "CHANGED_DURING_READ"
SIZE_MISMATCH = "SIZE_MISMATCH"
IS_A_DIRECTORY = "IS_A_DIRECTORY"
UNKNOWN_IO_ERROR = "UNKNOWN_IO_ERROR"
MEDIA_LIBRARY_DEFERRED = "MEDIA_LIBRARY_DEFERRED"
CLOUD_PLACEHOLDER = "CLOUD_PLACEHOLDER"
STAT_FAILED = "STAT_FAILED"
HASH_MISMATCH = "HASH_MISMATCH"
NOT_REGULAR_FILE = "NOT_REGULAR_FILE"


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    os_errno: int | None = None
    winerror: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "os_errno": self.os_errno,
            "winerror": self.winerror,
        }


_WINERROR_MAP = {
    2: NOT_FOUND,
    3: NOT_FOUND,
    5: PERMISSION_DENIED,
    32: SHARING_VIOLATION,
    33: SHARING_VIOLATION,
    123: INVALID_NAME,
    206: PATH_TOO_LONG,
    1920: CLOUD_UNAVAILABLE,
    1921: CLOUD_UNAVAILABLE,
}


def classify_os_error(exc: OSError, *, cloud_only: bool = False) -> ErrorInfo:
    """Translate an OSError into a stable category and a short message."""

    winerror = getattr(exc, "winerror", None)
    if winerror in _WINERROR_MAP:
        code = _WINERROR_MAP[winerror]
    elif isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
        code = PERMISSION_DENIED
    elif isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT:
        code = CLOUD_UNAVAILABLE if cloud_only else NOT_FOUND
    elif isinstance(exc, IsADirectoryError) or exc.errno == errno.EISDIR:
        code = IS_A_DIRECTORY
    elif exc.errno == errno.ENAMETOOLONG:
        code = PATH_TOO_LONG
    elif exc.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
        code = DEVICE_UNAVAILABLE
    else:
        code = UNKNOWN_IO_ERROR

    message = exc.strerror or str(exc)
    if winerror is not None:
        message = f"{message} (winerror {winerror})"
    return ErrorInfo(code=code, message=message, os_errno=exc.errno, winerror=winerror)
