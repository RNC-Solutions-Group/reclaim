"""File typing, signature hints, and disposition policy evaluation.

Everything in this module is data-driven.  The default profile and policy
shipped with Reclaim are generic; a migration supplies its own files.
"""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .errors import ConfigError
from .scanner import EntryRecord

DISPOSITIONS = (
    "retain",
    "exclude",
    "duplicate",
    "potential_junk",
    "related_version",
    "similar",
    "needs_review",
    "analysis_failure",
    "defer_media",
)

# Signature families that are compatible with a given extension kind.
_SIGNATURE_COMPATIBILITY: dict[str, set[str]] = {
    "pdf": {"pdf"},
    "zip": {
        "archive",
        "document",
        "spreadsheet",
        "presentation",
        "ebook",
        "code",
        "executable",
    },
    "ole2": {"document", "spreadsheet", "presentation", "executable", "database"},
    "pe": {"executable"},
    "elf": {"executable"},
    "macho": {"executable"},
    "image": {"image"},
    "video": {"video"},
    "audio": {"audio"},
    "riff": {"video", "audio"},
    "archive": {"archive"},
    "sqlite": {"database"},
    "text": {
        "document",
        "code",
        "shortcut",
        "presentation",
        "spreadsheet",
        "database",
        "unknown",
    },
}

_APPDATA_SEGMENTS = {
    "appdata",
    ".cache",
    "caches",
    "cache",
    "node_modules",
    ".npm",
    ".nuget",
    "application support",
    "containers",
    "group containers",
}


def _default_data_path(name: str) -> Path:
    return Path(resources.files("reclaim").joinpath("data", name))


@dataclass(frozen=True)
class TypeProfile:
    extension_kinds: Mapping[str, str]
    default_kind: str = "unknown"
    appdata_segments: frozenset[str] = field(
        default_factory=lambda: frozenset(_APPDATA_SEGMENTS)
    )

    def kind_for(self, entry: EntryRecord) -> str:
        if entry.entry_type == "dir":
            return "directory"
        if entry.rel_path != ".":
            segments = {part.casefold() for part in entry.rel_path.split("/")[:-1]}
            if segments & self.appdata_segments:
                return "appdata"
        name = entry.name.casefold()
        if "." in name.strip("."):
            extension = name.rsplit(".", 1)[-1]
            if extension in self.extension_kinds:
                return self.extension_kinds[extension]
        return self.default_kind

    def signature_family(self, signature_hex: str | None) -> str | None:
        if not signature_hex:
            return None
        try:
            head = bytes.fromhex(signature_hex)
        except ValueError:
            return None
        return signature_family(head)

    def signature_mismatch(self, entry: EntryRecord, signature_hex: str | None) -> bool:
        return self.signature_mismatch_for_kind(
            self.kind_for(entry), signature_hex
        )

    def signature_mismatch_for_kind(
        self, kind: str, signature_hex: str | None
    ) -> bool:
        family = self.signature_family(signature_hex)
        if family is None or family == "empty":
            return False
        if kind in ("directory", "appdata", "unknown"):
            return False
        compatible = _SIGNATURE_COMPATIBILITY.get(family)
        if compatible is None:
            return False
        return kind not in compatible


def signature_family(head: bytes) -> str:
    """Classify the first bytes of a file without interpreting its content."""

    if not head:
        return "empty"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image"
    if head.startswith(b"BM"):
        return "image"
    if head.startswith(b"RIFF") and head[8:12] in (b"WEBP",):
        return "image"
    if head.startswith(b"RIFF") and head[8:12] in (b"WAVE", b"AVI "):
        return "riff"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2"
    if head.startswith(b"Rar!\x1a\x07"):
        return "archive"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "archive"
    if head.startswith(b"\x1f\x8b"):
        return "archive"
    if head.startswith(b"BZh"):
        return "archive"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "archive"
    if head.startswith(b"MZ"):
        return "pe"
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith((b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe")):
        return "macho"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P ", b"F4A "):
            return "audio"
        return "video"
    if head.lstrip()[:1] == b"<" and b"<svg" in head[:512].lower():
        return "image"
    if head.startswith(b"\x00\x00\x01\xba") or head.startswith(b"\x00\x00\x01\xb3"):
        return "video"
    if head.startswith(b"ID3"):
        return "audio"
    if head.startswith(b"OggS"):
        return "audio"
    if head.startswith(b"fLaC"):
        return "audio"
    if _looks_like_text(head):
        return "text"
    return "unknown"


def _looks_like_text(head: bytes) -> bool:
    sample = head[:512]
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable / len(sample) > 0.9


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    priority: int
    disposition: str
    rationale: str
    confidence: float
    predicates: Mapping[str, object]

    def matches(self, entry: EntryRecord, kind: str) -> bool:
        folded_path = entry.rel_path.casefold()
        segments = [part for part in folded_path.split("/") if part]
        name = entry.name.casefold()
        extension = name.rsplit(".", 1)[-1] if "." in name.strip(".") else ""

        for key, expected in self.predicates.items():
            if key in ("path_segment_any", "path_segment_all"):
                values = {str(value).casefold() for value in _as_list(expected)}
                if key == "path_segment_any" and not values.intersection(segments):
                    return False
                if key == "path_segment_all" and not values.issubset(segments):
                    return False
            elif key == "extension_any":
                values = {str(value).casefold().lstrip(".") for value in _as_list(expected)}
                if extension not in values:
                    return False
            elif key == "basename_glob_any":
                if not any(
                    fnmatch.fnmatchcase(name, str(pattern).casefold())
                    for pattern in _as_list(expected)
                ):
                    return False
            elif key == "path_glob_any":
                if not any(
                    fnmatch.fnmatchcase(folded_path, str(pattern).casefold())
                    for pattern in _as_list(expected)
                ):
                    return False
            elif key == "filename_contains_any":
                if not any(str(token).casefold() in name for token in _as_list(expected)):
                    return False
            elif key == "kind_any":
                if kind not in {str(value).casefold() for value in _as_list(expected)}:
                    return False
            elif key == "zero_size":
                if bool(expected) != (entry.size_bytes == 0):
                    return False
            elif key == "is_media_library":
                if bool(expected) != entry.is_media_library:
                    return False
            elif key == "is_cloud":
                if bool(expected) != entry.is_cloud:
                    return False
            elif key == "downloads_context":
                if bool(expected) != ("downloads" in segments or "download" in segments):
                    return False
            else:
                raise ConfigError(f"Unknown policy predicate: {key!r}")
        return True


@dataclass(frozen=True)
class PolicyDecision:
    disposition: str
    rule_id: str
    rationale: str
    confidence: float


@dataclass(frozen=True)
class Override:
    disposition: str
    reason: str
    override_id: str = "override"


@dataclass(frozen=True)
class EffectiveDisposition:
    disposition: str
    source: str  # file_override | directory_override | recommendation | default
    reason: str


class PolicyEngine:
    def __init__(self, rules: Iterable[PolicyRule], default: PolicyRule) -> None:
        self.rules = tuple(sorted(rules, key=lambda rule: -rule.priority))
        self.default = default
        for rule in (*self.rules, default):
            if rule.disposition not in DISPOSITIONS:
                raise ConfigError(
                    f"Rule {rule.rule_id!r} uses unknown disposition {rule.disposition!r}"
                )

    def evaluate(self, entry: EntryRecord, kind: str) -> PolicyDecision:
        for rule in self.rules:
            if rule.matches(entry, kind):
                return PolicyDecision(
                    disposition=rule.disposition,
                    rule_id=rule.rule_id,
                    rationale=rule.rationale,
                    confidence=rule.confidence,
                )
        return PolicyDecision(
            disposition=self.default.disposition,
            rule_id=self.default.rule_id,
            rationale=self.default.rationale,
            confidence=self.default.confidence,
        )

    def resolve(
        self,
        recommendation: PolicyDecision,
        *,
        file_override: Override | None = None,
        directory_override: Override | None = None,
    ) -> EffectiveDisposition:
        if file_override is not None:
            return EffectiveDisposition(
                file_override.disposition, "file_override", file_override.reason
            )
        if directory_override is not None:
            return EffectiveDisposition(
                directory_override.disposition,
                "directory_override",
                directory_override.reason,
            )
        return EffectiveDisposition(
            recommendation.disposition, "recommendation", recommendation.rationale
        )


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def load_type_profile(path: str | Path | None = None) -> TypeProfile:
    data_path = Path(path) if path else _default_data_path("default-types.toml")
    with data_path.open("rb") as handle:
        raw = tomllib.load(handle)
    extensions = raw.get("extensions", {})
    if not isinstance(extensions, dict):
        raise ConfigError(f"{data_path}: [extensions] must be a table")
    return TypeProfile(
        extension_kinds={
            str(key).casefold().lstrip("."): str(value).casefold()
            for key, value in extensions.items()
        },
        default_kind=str(raw.get("meta", {}).get("default_kind", "unknown")),
    )


def load_policy(
    path: str | Path | None = None,
    overlay_path: str | Path | None = None,
) -> tuple[PolicyEngine, TypeProfile]:
    """Load a policy file, optionally appending project-specific overlay rules.

    The base file is generic product data.  An overlay lets a project add
    phase boundaries (for example, deferring all media) without forking the
    default policy or teaching the core about a particular migration.
    """

    data_path = Path(path) if path else _default_data_path("default-policy.toml")
    raw = _read_toml(data_path)

    rules_raw = list(raw.get("rules", []))
    default_raw = dict(raw.get("default", {}))
    if overlay_path:
        overlay = _read_toml(Path(overlay_path))
        rules_raw.extend(overlay.get("rules", []))
        default_raw.update(overlay.get("default", {}))

    types_value = (
        raw.get("types", {}).get("file")
        if isinstance(raw.get("types"), dict)
        else None
    )
    profile = load_type_profile(types_value)

    rules: list[PolicyRule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(rules_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"{data_path}: rule {index} must be a table")
        rule_id = str(item.get("id", f"rule-{index}"))
        if rule_id in seen_ids:
            raise ConfigError(f"Duplicate policy rule id: {rule_id!r}")
        seen_ids.add(rule_id)
        predicates = {
            key: value
            for key, value in item.items()
            if key
            not in {"id", "priority", "disposition", "rationale", "confidence"}
        }
        rules.append(
            PolicyRule(
                rule_id=rule_id,
                priority=int(item.get("priority", 0)),
                disposition=str(item.get("disposition", "retain")),
                rationale=str(item.get("rationale", "")),
                confidence=float(item.get("confidence", 0.5)),
                predicates=predicates,
            )
        )

    default = PolicyRule(
        rule_id="default-retain",
        priority=-1,
        disposition=str(default_raw.get("disposition", "retain")),
        rationale=str(
            default_raw.get(
                "rationale", "Safe default: retain unless a rule or operator says otherwise"
            )
        ),
        confidence=float(default_raw.get("confidence", 0.5)),
        predicates={},
    )
    return PolicyEngine(rules, default), profile


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read policy file {path}: {exc}") from exc
