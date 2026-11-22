"""Synthetic M1 test project builder.  No real data is used."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .helpers import write_bytes, write_config

from reclaim.classify import load_policy
from reclaim.config import ProjectConfig, load_config
from reclaim.hashing import hash_file
from reclaim.planning import PlanResult, PlanSettings, build_plan
from reclaim.scanner import SourceScanner
from reclaim.store import Store


@dataclass
class SyntheticProject:
    base: Path
    config: ProjectConfig
    store: Store
    run_id: str
    destination: Path
    sources: dict[str, Path]
    policy: object
    profile: object

    def close(self) -> None:
        self.store.close()

    def build(
        self,
        *,
        template: str = "{source_id}/{rel_path}",
        duplicate_policy: str = "prefer_one",
        target_platform: str | None = None,
    ) -> PlanResult:
        return build_plan(
            self.store,
            self.config,
            self.policy,  # type: ignore[arg-type]
            self.profile,  # type: ignore[arg-type]
            self.run_id,
            PlanSettings(
                destination_root=self.destination,
                destination_template=template,
                duplicate_policy=duplicate_policy,
                target_platform=target_platform,
            ),
        )

    def destination_files(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        if not self.destination.exists():
            return result
        for path in sorted(self.destination.rglob("*")):
            if path.is_file() and not path.name.startswith(".reclaim-tmp-"):
                result[path.relative_to(self.destination).as_posix()] = path.read_bytes()
        return result

    def source_snapshot(self) -> dict[str, tuple[int, int, str]]:
        snapshot: dict[str, tuple[int, int, str]] = {}
        for source_id, root in self.sources.items():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    snapshot[f"{source_id}:{path.relative_to(root).as_posix()}"] = (
                        stat.st_size,
                        stat.st_mtime_ns,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
        return snapshot


def create_project(
    base: Path,
    files_by_source: dict[str, dict[str, bytes]],
) -> SyntheticProject:
    """Create sources, a config, a scanned+hashed project, and a destination."""

    sources: dict[str, Path] = {}
    for source_id, files in files_by_source.items():
        root = base / "sources" / source_id
        for rel_path, content in files.items():
            write_bytes(root / rel_path, content)
        sources[source_id] = root

    config_path = write_config(base, sources)
    config = load_config(config_path)
    destination = base / "destination"
    destination.mkdir()

    store = Store(config.database_path)
    try:
        for source in config.sources:
            store.upsert_source(
                source.source_id, source.label, str(source.path), source.priority
            )
        run_id = store.start_run(config.fingerprint(), store.source_identities())
        fingerprints: dict[str, dict[str, object]] = {}
        for source in config.sources:
            scanner = SourceScanner(source, config.scan)
            store.insert_entries(run_id, scanner.walk())
            fingerprints[source.source_id] = scanner.fingerprint.as_dict()
        store.finish_run(run_id, status="scanned", fingerprints=fingerprints)
        store.seed_hashes(run_id, hash_media_libraries=False)
        for target in list(store.hash_targets(run_id)):
            root = config.source_by_id(target.source_id).path
            path = root.joinpath(*target.rel_path.split("/"))
            outcome = hash_file(
                path,
                expected_size=target.size_bytes,
                expected_mtime_ns=target.modified_ns,
            )
            store.save_hash(target, outcome)
        store.commit()
        store.set_run_status(run_id, "complete")
    except Exception:
        store.close()
        raise

    policy, profile = load_policy()
    return SyntheticProject(
        base=base,
        config=config,
        store=store,
        run_id=run_id,
        destination=destination,
        sources=sources,
        policy=policy,
        profile=profile,
    )
