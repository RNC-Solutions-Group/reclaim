"""Canonical path handling and preservation-boundary checks.

All path comparisons are performed on canonical, case-normalised absolute
paths.  Nothing in this module touches file content.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

from .errors import SafetyError

WINDOWS = os.name == "nt"


def canonical_path(value: str | os.PathLike[str]) -> Path:
    """Resolve an absolute, symlink-free path without requiring it to exist.

    ``os.path.realpath`` resolves junctions on Windows and symlinks on POSIX.
    For not-yet-created paths it resolves the existing ancestors, which is
    what safety checks need.
    """

    return Path(os.path.realpath(os.path.abspath(os.fspath(value))))


def path_key(value: str | os.PathLike[str]) -> str:
    """Return a comparison key: canonical, normalised, case-folded on Windows."""

    return os.path.normcase(os.path.normpath(str(canonical_path(value))))


def is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    """True when *child* is *parent* or lies beneath it."""

    c, p = path_key(child), path_key(parent)
    if c == p:
        return True
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives / UNC shares
        return False


def paths_overlap(
    first: str | os.PathLike[str], second: str | os.PathLike[str]
) -> bool:
    """True when either path contains the other."""

    return is_within(first, second) or is_within(second, first)


def assert_disjoint(
    mapping: dict[str, str | os.PathLike[str]],
    *,
    pair_exceptions: Iterable[frozenset[str]] = (),
) -> None:
    """Reject unsafe relationships between named paths.

    ``mapping`` maps a human label to a path.  Any overlap is rejected unless
    the pair is explicitly allowed by *pair_exceptions*.
    """

    allowed = {frozenset(pair) for pair in pair_exceptions}
    labels = list(mapping)
    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            if frozenset({left, right}) in allowed:
                continue
            if paths_overlap(mapping[left], mapping[right]):
                raise SafetyError(
                    f"Unsafe path relationship: {left} ({mapping[left]}) overlaps "
                    f"{right} ({mapping[right]})"
                )


def long_path(value: str | os.PathLike[str]) -> str:
    r"""Return a Windows extended-length path when needed for system calls.

    Python is long-path aware on modern Windows, but third-party tooling and
    some APIs are not.  Using ``\\?\`` for deep paths keeps enumeration and
    hashing reliable without altering the path stored in the database.
    """

    text = os.fspath(value)
    if not WINDOWS or text.startswith("\\\\?\\"):
        return text
    if not os.path.isabs(text) or len(text) < 240:
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def display_path(value: str | os.PathLike[str]) -> str:
    """Strip an extended-length prefix for readable output."""

    text = os.fspath(value)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def rel_to_posix(parts: Iterable[str]) -> str:
    """Join relative path components with forward slashes."""

    return "/".join(part for part in parts if part not in ("", "."))


def parent_rel(rel_path: str) -> str | None:
    """Return the POSIX-style parent of a relative path, or None at the root."""

    if rel_path == ".":
        return None
    head, sep, _ = rel_path.rpartition("/")
    return head if sep else "."


def depth_of(rel_path: str) -> int:
    if rel_path == ".":
        return 0
    return rel_path.count("/") + 1


def assert_safe_relative(rel_path: str) -> None:
    """Reject relative paths that could escape a source root."""

    if not rel_path or rel_path == ".":
        return
    if rel_path.startswith(("/", "\\")) or ":" in rel_path.split("/")[0]:
        raise SafetyError(f"Absolute path not allowed: {rel_path!r}")
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SafetyError(f"Unsafe relative path: {rel_path!r}")


def platform_note() -> str:
    return f"{sys.platform} (os.name={os.name})"
