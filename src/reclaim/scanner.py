"""Read-only filesystem enumeration.

The scanner records every entry it can see, including symbolic links,
junctions, excluded subtrees, unreadable directories, and entries whose
metadata cannot be read.  It never follows a reparse point and never opens a
file for content.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat as stat_module
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import ScanSettings, SourceSpec
from .errors import (
    ErrorInfo,
    SourceUnavailableError,
    classify_os_error,
)
from .paths import long_path, parent_rel

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

CLOUD_ATTRIBUTES = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


@dataclass(frozen=True)
class EntryRecord:
    source_id: str
    rel_path: str
    parent_rel_path: str | None
    name: str
    depth: int
    entry_type: str  # file | dir | reparse | excluded | unknown
    size_bytes: int | None
    created_ns: int | None
    modified_ns: int | None
    accessed_ns: int | None
    mode: int | None
    attributes: int
    is_hidden: bool
    is_system: bool
    is_reparse: bool
    is_cloud: bool
    is_media_library: bool
    link_target: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class SourceFingerprint:
    """Metadata-consistency fingerprint for a source tree.

    It covers relative paths, entry types, sizes, and modification times.  A
    match confirms that the metadata observed at two points is consistent; it
    does **not** establish byte-level integrity.  SHA-256 hashes (and sampled
    re-reads) are the evidence for content integrity.
    """

    entry_count: int
    file_count: int
    total_bytes: int
    digest: str
    errors: int

    def as_dict(self) -> dict[str, object]:
        return {
            "entry_count": self.entry_count,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "digest": self.digest,
            "errors": self.errors,
        }


def _created_ns(st: os.stat_result) -> int | None:
    if sys.platform == "darwin":
        return getattr(st, "st_birthtime_ns", None)
    if os.name == "nt":
        # st_ctime is creation time on Windows.
        return st.st_ctime_ns
    # POSIX ctime is inode-change time, not creation time; do not mislabel it.
    return None


def _is_cloud(attributes: int) -> bool:
    return bool(attributes & CLOUD_ATTRIBUTES)


class SourceScanner:
    """Enumerate one source without modifying it."""

    def __init__(self, source: SourceSpec, settings: ScanSettings) -> None:
        self.source = source
        self.settings = settings
        self._excluded = {name.casefold() for name in settings.exclude_names}
        self._fingerprint_rows: list[tuple[str, str, int, int]] = []
        self._file_count = 0
        self._total_bytes = 0
        self._error_count = 0
        self._fingerprint: SourceFingerprint | None = None

    @property
    def fingerprint(self) -> SourceFingerprint:
        if self._fingerprint is None:
            raise RuntimeError("fingerprint is available only after walk() completes")
        return self._fingerprint

    def walk(self) -> Iterator[EntryRecord]:
        root = self.source.path
        try:
            root_stat = os.stat(long_path(root))
        except OSError as exc:
            raise SourceUnavailableError(
                f"Cannot read source {self.source.source_id}: {root} ({exc})"
            ) from exc
        if not stat_module.S_ISDIR(root_stat.st_mode):
            raise SourceUnavailableError(f"Source is not a directory: {root}")

        yield from self._walk_dir(
            abs_dir=root,
            rel_path=".",
            depth=0,
            in_media_library=False,
        )
        self._fingerprint = self._build_fingerprint()

    # -- internals ---------------------------------------------------------

    def _build_fingerprint(self) -> SourceFingerprint:
        rows = sorted(self._fingerprint_rows, key=lambda item: item[0])
        digest = hashlib.sha256()
        for rel_path, entry_type, size, modified in rows:
            digest.update(
                f"{rel_path}\0{entry_type}\0{size}\0{modified}\n".encode(
                    "utf-8", "surrogatepass"
                )
            )
        return SourceFingerprint(
            entry_count=len(rows),
            file_count=self._file_count,
            total_bytes=self._total_bytes,
            digest=digest.hexdigest(),
            errors=self._error_count,
        )

    def _record(self, entry: EntryRecord) -> EntryRecord:
        # Excluded entries are recorded for accountability but are not part of
        # the audited dataset, so they must not influence its preservation
        # fingerprint (an in-progress transfer, for example, keeps changing).
        if entry.entry_type != "excluded":
            self._fingerprint_rows.append(
                (
                    entry.rel_path,
                    entry.entry_type,
                    -1 if entry.size_bytes is None else entry.size_bytes,
                    -1 if entry.modified_ns is None else entry.modified_ns,
                )
            )
        if entry.entry_type == "file":
            self._file_count += 1
            self._total_bytes += entry.size_bytes or 0
        if entry.error_code:
            self._error_count += 1
        return entry

    def _walk_dir(
        self, abs_dir: Path, rel_path: str, depth: int, in_media_library: bool
    ) -> Iterator[EntryRecord]:
        name = "." if rel_path == "." else rel_path.rsplit("/", 1)[-1]
        error: ErrorInfo | None = None
        children: list[os.DirEntry[str]] = []
        try:
            with os.scandir(long_path(abs_dir)) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            error = classify_os_error(exc, cloud_only=in_media_library)

        dir_stat: os.stat_result | None
        try:
            dir_stat = os.stat(long_path(abs_dir))
        except OSError:
            dir_stat = None

        attributes = (
            getattr(dir_stat, "st_file_attributes", 0) if dir_stat is not None else 0
        )
        yield self._record(
            EntryRecord(
                source_id=self.source.source_id,
                rel_path=rel_path,
                parent_rel_path=parent_rel(rel_path),
                name=name,
                depth=depth,
                entry_type="dir",
                size_bytes=dir_stat.st_size if dir_stat else None,
                created_ns=_created_ns(dir_stat) if dir_stat else None,
                modified_ns=dir_stat.st_mtime_ns if dir_stat else None,
                accessed_ns=dir_stat.st_atime_ns if dir_stat else None,
                mode=(dir_stat.st_mode & 0o7777) if dir_stat else None,
                attributes=attributes,
                is_hidden=bool(
                    attributes & FILE_ATTRIBUTE_HIDDEN
                    or (name != "." and name.startswith("."))
                ),
                is_system=bool(attributes & FILE_ATTRIBUTE_SYSTEM),
                is_reparse=False,
                is_cloud=_is_cloud(attributes),
                is_media_library=in_media_library,
                error_code=error.code if error else None,
                error_message=error.message if error else None,
                note="directory listing failed" if error else None,
            )
        )
        if error:
            return
        if not self.settings.include_hidden and name.startswith("."):
            return

        for child in children:
            child_rel = child.name if rel_path == "." else f"{rel_path}/{child.name}"
            child_depth = depth + 1
            if self.settings.max_depth is not None and child_depth > self.settings.max_depth:
                continue
            yield from self._handle_child(
                child=child,
                abs_parent=abs_dir,
                rel_path=child_rel,
                depth=child_depth,
                in_media_library=in_media_library,
            )

    def _handle_child(
        self,
        child: os.DirEntry[str],
        abs_parent: Path,
        rel_path: str,
        depth: int,
        in_media_library: bool,
    ) -> Iterator[EntryRecord]:
        abs_path = abs_parent / child.name
        name = child.name
        parent = "." if depth == 1 else rel_path.rsplit("/", 1)[0]

        if name.casefold() in self._excluded:
            st = self._safe_stat(abs_path)
            yield self._record(
                self._make_entry(
                    rel_path=rel_path,
                    parent=parent,
                    name=name,
                    depth=depth,
                    entry_type="excluded",
                    st=st,
                    in_media_library=in_media_library,
                    note="excluded by scan configuration; not descended",
                )
            )
            return

        st = self._safe_stat(abs_path)
        if st is None:
            yield self._record(
                EntryRecord(
                    source_id=self.source.source_id,
                    rel_path=rel_path,
                    parent_rel_path=parent,
                    name=name,
                    depth=depth,
                    entry_type="unknown",
                    size_bytes=None,
                    created_ns=None,
                    modified_ns=None,
                    accessed_ns=None,
                    mode=None,
                    attributes=0,
                    is_hidden=name.startswith("."),
                    is_system=False,
                    is_reparse=False,
                    is_cloud=False,
                    is_media_library=in_media_library,
                    error_code="STAT_FAILED",
                    error_message="Metadata could not be read",
                )
            )
            return

        attributes = getattr(st, "st_file_attributes", 0)
        is_reparse = bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or child.is_symlink()
        is_dir = stat_module.S_ISDIR(st.st_mode)

        if is_reparse and not self.settings.follow_reparse_points:
            link_target = None
            try:
                link_target = os.readlink(long_path(abs_path))
            except OSError:
                pass
            yield self._record(
                self._make_entry(
                    rel_path=rel_path,
                    parent=parent,
                    name=name,
                    depth=depth,
                    entry_type="reparse",
                    st=st,
                    in_media_library=in_media_library,
                    link_target=link_target,
                    note="link/reparse point not followed",
                )
            )
            return

        is_media_library = in_media_library or self._matches_media_library(name)
        if is_dir:
            yield from self._walk_dir(
                abs_dir=abs_path,
                rel_path=rel_path,
                depth=depth,
                in_media_library=is_media_library,
            )
            return

        yield self._record(
            self._make_entry(
                rel_path=rel_path,
                parent=parent,
                name=name,
                depth=depth,
                entry_type="file",
                st=st,
                in_media_library=is_media_library,
            )
        )

    def _matches_media_library(self, name: str) -> bool:
        folded = name.casefold()
        return any(
            fnmatch.fnmatchcase(folded, pattern.casefold())
            for pattern in self.settings.media_library_patterns
        )

    @staticmethod
    def _safe_stat(abs_path: Path) -> os.stat_result | None:
        try:
            # lstat: never resolve a link/junction while inventorying it.
            return os.lstat(long_path(abs_path))
        except OSError:
            return None

    def _make_entry(
        self,
        *,
        rel_path: str,
        parent: str,
        name: str,
        depth: int,
        entry_type: str,
        st: os.stat_result,
        in_media_library: bool,
        link_target: str | None = None,
        note: str | None = None,
    ) -> EntryRecord:
        attributes = getattr(st, "st_file_attributes", 0)
        return EntryRecord(
            source_id=self.source.source_id,
            rel_path=rel_path,
            parent_rel_path=parent,
            name=name,
            depth=depth,
            entry_type=entry_type,
            size_bytes=st.st_size,
            created_ns=_created_ns(st),
            modified_ns=st.st_mtime_ns,
            accessed_ns=st.st_atime_ns,
            mode=st.st_mode & 0o7777,
            attributes=attributes,
            is_hidden=bool(attributes & FILE_ATTRIBUTE_HIDDEN or name.startswith(".")),
            is_system=bool(attributes & FILE_ATTRIBUTE_SYSTEM),
            is_reparse=entry_type == "reparse",
            is_cloud=_is_cloud(attributes),
            is_media_library=in_media_library,
            link_target=link_target,
            note=note,
        )
