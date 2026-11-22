"""Destination path policy and validation for migration planning.

Validation is deliberately independent of the host platform: a plan may target
Windows, macOS, or Linux from any host.  Filesystem *checks* (existing files,
links, free space) still use the host, but path-safety rules do not.

The module never writes during plan validation or preview.  ``safe_makedirs``
and ``publish_no_overwrite`` are execution-only helpers.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import ReclaimError, SafetyError
from .paths import canonical_path, paths_overlap

SUPPORTED_PLATFORMS = ("windows", "macos", "linux")

WINDOWS_INVALID_CHARS = set('<>:"|?*')
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
    "COM¹",
    "COM²",
    "COM³",
    "LPT¹",
    "LPT²",
    "LPT³",
}

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class DestinationError(ReclaimError):
    """A destination cannot be used or a path cannot be published."""


class DestinationOccupied(DestinationError):
    """The destination path already exists; refusing to overwrite."""


@dataclass(frozen=True)
class DestinationPolicy:
    target_platform: str
    max_path_length: int = 240
    max_component_length: int = 255
    case_sensitive: bool | None = None

    def __post_init__(self) -> None:
        if self.target_platform not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported target platform {self.target_platform!r}; "
                f"expected one of {SUPPORTED_PLATFORMS}"
            )
        if self.max_path_length < 32:
            raise ValueError("max_path_length must be at least 32")
        if self.max_component_length < 8:
            raise ValueError("max_component_length must be at least 8")

    @property
    def effective_case_sensitive(self) -> bool:
        if self.case_sensitive is not None:
            return self.case_sensitive
        return self.target_platform == "linux"

    @property
    def unicode_sensitive(self) -> bool:
        # Windows and macOS are treated conservatively as normalization-
        # insensitive for collision detection; Linux is byte-oriented.
        return self.target_platform == "linux"

    @classmethod
    def for_host(cls) -> DestinationPolicy:
        if os.name == "nt":
            platform = "windows"
        elif os.sys.platform == "darwin":
            platform = "macos"
        else:
            platform = "linux"
        return cls(target_platform=platform)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    rel_path: str | None = None
    item_id: int | None = None


@dataclass(frozen=True)
class DestinationItem:
    item_id: int
    rel_path: str
    size_bytes: int


@dataclass
class DestinationValidation:
    issues: list[ValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.issues

    def by_item(self) -> dict[int, list[ValidationIssue]]:
        grouped: dict[int, list[ValidationIssue]] = {}
        for issue in self.issues:
            if issue.item_id is not None:
                grouped.setdefault(issue.item_id, []).append(issue)
        return grouped


def _issue(code: str, message: str, *, rel: str | None = None, item_id: int | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, rel_path=rel, item_id=item_id)


def normalize_relative(rel_path: str) -> str:
    """Normalize a POSIX-style relative destination path.

    Backslashes are rejected rather than silently converted, so a plan cannot
    mean different things on different platforms.
    """

    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("Destination path is empty")
    if "\\" in rel_path:
        raise ValueError("Destination paths must use '/' separators")
    if rel_path.startswith("/") or re.match(r"^[A-Za-z]:", rel_path):
        raise ValueError("Destination path must be relative")
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Destination path contains an empty, '.' or '..' segment")
    for part in parts:
        if "\x00" in part or any(ord(char) < 32 for char in part):
            raise ValueError("Destination path contains a control character")
    normalized = "/".join(parts)
    if len(normalized) > 4096:
        raise ValueError("Destination path is unreasonably long")
    return normalized


def name_keys(name: str, policy: DestinationPolicy) -> frozenset[str]:
    """Equivalence keys for case/Unicode collision detection."""

    if policy.unicode_sensitive:
        forms = {name}
    else:
        forms = {
            unicodedata.normalize("NFC", name),
            unicodedata.normalize("NFD", name),
        }
    if not policy.effective_case_sensitive:
        forms = {form.casefold() for form in forms}
    return frozenset(forms)


def _reserved_windows_name(segment: str) -> bool:
    base = segment.rstrip(" .").split(".", 1)[0].upper()
    return base in WINDOWS_RESERVED


def validate_segment(segment: str, policy: DestinationPolicy) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if segment in ("", ".", ".."):
        issues.append(_issue("INVALID_SEGMENT", "Empty, '.' and '..' segments are not allowed"))
        return issues
    if "\x00" in segment or any(ord(char) < 32 for char in segment):
        issues.append(_issue("CONTROL_CHARACTER", "Control characters are not allowed"))
    if policy.target_platform == "windows":
        bad = sorted(set(segment) & WINDOWS_INVALID_CHARS)
        if bad:
            issues.append(
                _issue(
                    "WINDOWS_INVALID_CHARACTER",
                    f"Windows-invalid character(s): {''.join(bad)}",
                )
            )
        if segment.endswith((" ", ".")):
            issues.append(
                _issue(
                    "WINDOWS_TRAILING_CHARACTER",
                    "Windows does not allow a trailing space or dot",
                )
            )
        if _reserved_windows_name(segment):
            issues.append(
                _issue("WINDOWS_RESERVED_NAME", f"Reserved Windows name: {segment}")
            )
        length = len(segment)
    else:
        length = len(segment.encode("utf-8"))
    if length > policy.max_component_length:
        issues.append(
            _issue(
                "COMPONENT_TOO_LONG",
                f"Component length {length} exceeds {policy.max_component_length}",
            )
        )
    return issues


def validate_relative(rel_path: str, policy: DestinationPolicy) -> list[ValidationIssue]:
    try:
        normalized = normalize_relative(rel_path)
    except ValueError as exc:
        return [_issue("INVALID_RELATIVE_PATH", str(exc), rel=rel_path)]
    issues: list[ValidationIssue] = []
    for segment in normalized.split("/"):
        for issue in validate_segment(segment, policy):
            issues.append(
                ValidationIssue(
                    code=issue.code,
                    message=f"{issue.message} (segment {segment!r})",
                    rel_path=normalized,
                )
            )
    return issues


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def validate_destination_root(
    root: str | Path,
    policy: DestinationPolicy,
    *,
    source_roots: list[Path] | None = None,
    workspace: Path | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw = Path(root)
    if not raw.is_absolute():
        return [_issue("DESTINATION_NOT_ABSOLUTE", "Destination root must be absolute")]
    try:
        canonical = canonical_path(raw)
    except OSError as exc:
        return [_issue("DESTINATION_UNREADABLE", f"Cannot resolve destination: {exc}")]
    if not canonical.exists():
        issues.append(_issue("DESTINATION_MISSING", "Destination root does not exist"))
        return issues
    if not canonical.is_dir():
        issues.append(_issue("DESTINATION_NOT_DIRECTORY", "Destination root is not a directory"))
        return issues
    if _is_reparse(canonical):
        issues.append(
            _issue("DESTINATION_REPARSE", "Destination root is a link/reparse point")
        )
    if os.path.normcase(str(raw.absolute())) != os.path.normcase(str(canonical)):
        issues.append(
            _issue(
                "DESTINATION_REDIRECTED",
                "Destination path resolves through a link or reparse point",
            )
        )
    if not os.access(canonical, os.W_OK):
        issues.append(_issue("DESTINATION_NOT_WRITABLE", "Destination root is not writable"))
    for source in source_roots or []:
        if paths_overlap(canonical, source):
            issues.append(
                _issue(
                    "DESTINATION_OVERLAPS_SOURCE",
                    f"Destination overlaps source root: {source}",
                )
            )
    if workspace is not None and paths_overlap(canonical, workspace):
        issues.append(
            _issue(
                "DESTINATION_OVERLAPS_WORKSPACE",
                "Destination overlaps the project workspace",
            )
        )
    return issues


def _existing_name_collision(
    root: Path, rel_path: str, policy: DestinationPolicy
) -> list[ValidationIssue]:
    """Detect case/Unicode collisions with existing sibling names."""

    issues: list[ValidationIssue] = []
    current = root
    for segment in rel_path.split("/"):
        if not current.is_dir():
            break
        try:
            names = [entry.name for entry in os.scandir(current)]
        except OSError:
            break
        target_keys = name_keys(segment, policy)
        for existing in names:
            if existing == segment:
                continue
            if name_keys(existing, policy) & target_keys:
                issues.append(
                    _issue(
                        "CASE_UNICODE_COLLISION",
                        f"{segment!r} collides with existing {existing!r}",
                        rel=rel_path,
                    )
                )
                return issues
        next_path = current / segment
        if not next_path.exists():
            break
        current = next_path
    return issues


def validate_plan(
    destination_root: str | Path,
    items: list[DestinationItem],
    policy: DestinationPolicy,
    *,
    source_roots: list[Path] | None = None,
    workspace: Path | None = None,
    require_free_space: bool = True,
) -> DestinationValidation:
    root_issues = validate_destination_root(
        destination_root,
        policy,
        source_roots=source_roots,
        workspace=workspace,
    )
    if root_issues:
        return DestinationValidation(root_issues)

    root = canonical_path(destination_root)
    issues: list[ValidationIssue] = list(root_issues)
    rel_set: dict[str, DestinationItem] = {}
    destination_index: dict[tuple[str, str], DestinationItem] = {}

    for item in items:
        rel_issues = validate_relative(item.rel_path, policy)
        for issue in rel_issues:
            issues.append(
                ValidationIssue(
                    code=issue.code,
                    message=issue.message,
                    rel_path=issue.rel_path,
                    item_id=item.item_id,
                )
            )
        if rel_issues:
            continue
        rel = normalize_relative(item.rel_path)
        if rel in rel_set:
            issues.append(
                _issue(
                    "DUPLICATE_DESTINATION",
                    f"Multiple plan items target {rel!r}",
                    rel=rel,
                    item_id=item.item_id,
                )
            )
            issues.append(
                _issue(
                    "DUPLICATE_DESTINATION",
                    f"Multiple plan items target {rel!r}",
                    rel=rel,
                    item_id=rel_set[rel].item_id,
                )
            )
        rel_set[rel] = item

        parts = rel.split("/")
        parent = "/".join(parts[:-1])
        for key in name_keys(parts[-1], policy):
            other_item = destination_index.get((parent, key))
            if other_item is not None and other_item.item_id != item.item_id:
                issues.append(
                    _issue(
                        "DUPLICATE_DESTINATION",
                        f"Destination name collides with another plan item: {rel}",
                        rel=rel,
                        item_id=item.item_id,
                    )
                )
            destination_index[(parent, key)] = item

        target_path = root.joinpath(*parts)
        total_length = len(str(target_path))
        if total_length > policy.max_path_length:
            issues.append(
                _issue(
                    "PATH_TOO_LONG",
                    f"Destination path length {total_length} exceeds "
                    f"{policy.max_path_length}",
                    rel=rel,
                    item_id=item.item_id,
                )
            )

        # Existing parent components must be directories, not links.
        current = root
        for segment in parts[:-1]:
            current = current / segment
            if not current.exists():
                break
            if _is_reparse(current):
                issues.append(
                    _issue(
                        "PARENT_REPARSE",
                        f"Parent path component is a link/reparse point: {segment}",
                        rel=rel,
                        item_id=item.item_id,
                    )
                )
                break
            if not current.is_dir():
                issues.append(
                    _issue(
                        "PARENT_NOT_DIRECTORY",
                        f"Parent path component is not a directory: {segment}",
                        rel=rel,
                        item_id=item.item_id,
                    )
                )
                break

        if target_path.exists() or target_path.is_symlink():
            issues.append(
                _issue(
                    "DESTINATION_EXISTS",
                    f"Destination already exists: {rel}",
                    rel=rel,
                    item_id=item.item_id,
                )
            )
        issues.extend(
            ValidationIssue(
                code=issue.code,
                message=issue.message,
                rel_path=rel,
                item_id=item.item_id,
            )
            for issue in _existing_name_collision(root, rel, policy)
        )

    # Prefix conflicts: a file cannot also be a directory.  Done after the
    # first pass so plan-item order cannot hide an ancestor/descendant clash.
    for rel, item in rel_set.items():
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if ancestor in rel_set and rel_set[ancestor].item_id != item.item_id:
                issues.append(
                    _issue(
                        "PATH_PREFIX_CONFLICT",
                        f"{rel!r} uses {ancestor!r}, which is also a planned file",
                        rel=rel,
                        item_id=item.item_id,
                    )
                )

    if require_free_space:
        required = sum(max(0, item.size_bytes) for item in items)
        try:
            import shutil

            free = shutil.disk_usage(root).free
        except OSError:
            free = None
        if free is not None and required > free:
            issues.append(
                _issue(
                    "INSUFFICIENT_SPACE",
                    f"Destination has {free} bytes free; plan requires {required}",
                )
            )
    return DestinationValidation(issues)


def safe_makedirs(root: str | Path, rel_parent: str) -> Path:
    """Create parent directories without following a link/reparse component."""

    base = canonical_path(root)
    if _is_reparse(base):
        raise DestinationError("Destination root is a link/reparse point")
    current = base
    if rel_parent not in ("", "."):
        for segment in rel_parent.split("/"):
            current = current / segment
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir()
            else:
                if _is_reparse(current):
                    raise DestinationError(
                        f"Refusing to traverse link/reparse component: {current}"
                    )
                if not stat.S_ISDIR(info.st_mode):
                    raise DestinationError(f"Parent is not a directory: {current}")
    return current


def publish_no_overwrite(temp_path: str | Path, destination: str | Path) -> None:
    """Publish a verified temp file without overwriting an existing path.

    ``os.link`` provides an atomic no-replace publication on filesystems that
    support hard links.  A conservative fallback reserves the final name with
    ``O_EXCL`` and then replaces only that reservation.
    """

    temp = Path(temp_path)
    dest = Path(destination)
    try:
        os.link(temp, dest)
    except FileExistsError as exc:
        raise DestinationOccupied(f"Destination appeared during execution: {dest}") from exc
    except OSError as exc:
        if exc.errno not in {
            errno.EPERM,
            errno.EACCES,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.EXDEV,
            errno.EINVAL,
        }:
            raise
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exists:
            raise DestinationOccupied(
                f"Destination appeared during execution: {dest}"
            ) from exists
        else:
            os.close(fd)
        os.replace(temp, dest)
        temp = None  # type: ignore[assignment]
    finally:
        if temp is not None:
            try:
                Path(temp).unlink()
            except FileNotFoundError:
                pass


def ensure_destination_is_safe_child(root: Path, candidate: Path) -> None:
    """Guard against a resolved destination escaping the selected root."""

    canonical_root = canonical_path(root)
    canonical_candidate = canonical_path(candidate)
    if canonical_candidate != canonical_root and canonical_root not in canonical_candidate.parents:
        raise SafetyError(f"Destination path escapes root: {candidate}")
