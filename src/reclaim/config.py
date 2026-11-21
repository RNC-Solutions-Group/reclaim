"""Project configuration: sources, workspace, and scan settings.

Configuration is data, never code.  Paths in a project file are private and
must live outside the repository; the repository ships only synthetic
examples.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError, SafetyError, SourceUnavailableError
from .paths import (
    assert_disjoint,
    canonical_path,
    is_within,
    paths_overlap,
)

SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

DEFAULT_EXCLUDED_NAMES = (
    "$RECYCLE.BIN",
    "System Volume Information",
    "$SysReset",
    ".Trashes",
)

DEFAULT_MEDIA_LIBRARY_PATTERNS = (
    "*.photoslibrary",
    "*.photolibrary",
    "*.migratedphotolibrary",
    "*.aplibrary",
    "iphoto library",
    "photos library.photoslibrary",
)


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    label: str
    path: Path
    priority: int = 100

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "path": str(self.path),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ScanSettings:
    include_hidden: bool = True
    follow_reparse_points: bool = False
    exclude_names: tuple[str, ...] = DEFAULT_EXCLUDED_NAMES
    max_depth: int | None = None
    hash_media_libraries: bool = False
    media_library_patterns: tuple[str, ...] = DEFAULT_MEDIA_LIBRARY_PATTERNS
    chunk_bytes: int = 4 * 1024 * 1024

    def as_dict(self) -> dict[str, object]:
        return {
            "include_hidden": self.include_hidden,
            "follow_reparse_points": self.follow_reparse_points,
            "exclude_names": list(self.exclude_names),
            "max_depth": self.max_depth,
            "hash_media_libraries": self.hash_media_libraries,
            "media_library_patterns": list(self.media_library_patterns),
            "chunk_bytes": self.chunk_bytes,
        }


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    config_path: Path
    workspace: Path
    database_path: Path
    reports_dir: Path
    sources: tuple[SourceSpec, ...]
    scan: ScanSettings = field(default_factory=ScanSettings)
    types_file: Path | None = None
    policy_file: Path | None = None
    policy_overlay_file: Path | None = None

    def fingerprint(self) -> str:
        payload = {
            "project": self.project_name,
            "sources": [s.as_dict() for s in self.sources],
            "scan": self.scan.as_dict(),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def source_by_id(self, source_id: str) -> SourceSpec:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise KeyError(source_id)


def _resolve(config_dir: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    return canonical_path(candidate)


def _workspace_path(workspace: Path, value: object) -> Path:
    """Resolve a database/report path; relative values are workspace-relative."""

    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return canonical_path(candidate)


def load_config(path: str | Path) -> ProjectConfig:
    config_path = canonical_path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration {config_path}: {exc}") from exc

    project = raw.get("project", {})
    if not isinstance(project, dict):
        raise ConfigError("[project] must be a table")

    name = str(project.get("name", "reclaim-project")).strip()
    workspace_value = project.get("workspace")
    if not workspace_value:
        raise ConfigError("[project].workspace is required")
    workspace = _resolve(config_path.parent, str(workspace_value))

    database_path = _workspace_path(
        workspace, project.get("database", workspace / "db" / "inventory.sqlite3")
    )
    reports_dir = _workspace_path(
        workspace, project.get("reports_dir", workspace / "reports")
    )

    raw_scan = raw.get("scan", {})
    if not isinstance(raw_scan, dict):
        raise ConfigError("[scan] must be a table")
    settings = ScanSettings(
        include_hidden=bool(raw_scan.get("include_hidden", True)),
        follow_reparse_points=bool(raw_scan.get("follow_reparse_points", False)),
        exclude_names=tuple(
            str(v) for v in raw_scan.get("exclude_names", DEFAULT_EXCLUDED_NAMES)
        ),
        max_depth=(
            None if raw_scan.get("max_depth") in (None, 0) else int(raw_scan["max_depth"])
        ),
        hash_media_libraries=bool(raw_scan.get("hash_media_libraries", False)),
        media_library_patterns=tuple(
            str(v).lower()
            for v in raw_scan.get(
                "media_library_patterns", DEFAULT_MEDIA_LIBRARY_PATTERNS
            )
        ),
        chunk_bytes=int(raw_scan.get("chunk_bytes", 4 * 1024 * 1024)),
    )

    types_file = (
        _resolve(config_path.parent, str(raw["types"]["file"]))
        if isinstance(raw.get("types"), dict) and raw["types"].get("file")
        else None
    )
    policy_file = (
        _resolve(config_path.parent, str(raw["policy"]["file"]))
        if isinstance(raw.get("policy"), dict) and raw["policy"].get("file")
        else None
    )
    policy_overlay_file = (
        _resolve(config_path.parent, str(raw["policy"]["overlay"]))
        if isinstance(raw.get("policy"), dict) and raw["policy"].get("overlay")
        else None
    )

    raw_sources = raw.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("At least one [[sources]] entry is required")

    sources: list[SourceSpec] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            raise ConfigError("Each [[sources]] entry must be a table")
        source_id = str(item.get("id", "")).strip()
        label = str(item.get("label", source_id)).strip()
        path_value = item.get("path")
        if not SOURCE_ID_PATTERN.match(source_id):
            raise ConfigError(
                f"Invalid source id {source_id!r}; use lowercase letters, digits, "
                "hyphens, and underscores"
            )
        if not path_value:
            raise ConfigError(f"Source {source_id!r} has no path")
        sources.append(
            SourceSpec(
                source_id=source_id,
                label=label or source_id,
                path=_resolve(config_path.parent, str(path_value)),
                priority=int(item.get("priority", 100)),
            )
        )

    if len({s.source_id for s in sources}) != len(sources):
        raise ConfigError("Source ids must be unique")
    if len({str(s.path).casefold() for s in sources}) != len(sources):
        raise ConfigError("Source paths must be distinct")

    config = ProjectConfig(
        project_name=name,
        config_path=config_path,
        workspace=workspace,
        database_path=database_path,
        reports_dir=reports_dir,
        sources=tuple(sources),
        scan=settings,
        types_file=types_file,
        policy_file=policy_file,
        policy_overlay_file=policy_overlay_file,
    )
    validate_config(config)
    return config


def validate_config(config: ProjectConfig, *, require_sources: bool = True) -> None:
    if not is_within(config.database_path, config.workspace):
        raise ConfigError("Database must live inside the project workspace")
    if not is_within(config.reports_dir, config.workspace):
        raise ConfigError("Reports directory must live inside the project workspace")

    for source in config.sources:
        if paths_overlap(config.workspace, source.path):
            raise SafetyError(
                f"Workspace {config.workspace} overlaps source {source.source_id} "
                f"({source.path})"
            )
        if require_sources and not source.path.exists():
            raise SourceUnavailableError(
                f"Source {source.source_id!r} is unavailable: {source.path}"
            )
        if require_sources and not source.path.is_dir():
            raise ConfigError(f"Source {source.source_id!r} is not a directory")
        if source.priority < 0:
            raise ConfigError("Source priority must be zero or greater")

    assert_disjoint({s.source_id: s.path for s in config.sources})
    if config.scan.chunk_bytes < 64 * 1024:
        raise ConfigError("scan.chunk_bytes must be at least 65536")


def ensure_workspace(config: ProjectConfig) -> None:
    """Create the private workspace directories after validation."""

    for directory in (config.workspace, config.database_path.parent, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
