"""Synthetic fixture helpers.  No real user data is ever used."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def write_bytes(path: Path, data: bytes, mtime_ns: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def write_config(
    base: Path,
    sources: dict[str, Path],
    *,
    workspace: Path | None = None,
    priority: dict[str, int] | None = None,
    scan_extra: str = "",
) -> Path:
    workspace = workspace or (base / "workspace")
    lines = [
        "[project]",
        'name = "synthetic-cp1"',
        f'workspace = "{workspace.as_posix()}"',
        'database = "db/inventory.sqlite3"',
        'reports_dir = "reports"',
        "",
        "[scan]",
        scan_extra.strip() or "include_hidden = true",
        "",
    ]
    for source_id, path in sources.items():
        lines.extend(
            [
                "[[sources]]",
                f'id = "{source_id}"',
                f'label = "{source_id}"',
                f'path = "{path.as_posix()}"',
                f"priority = {(priority or {}).get(source_id, 100)}",
                "",
            ]
        )
    config_path = base / "project.toml"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def make_junction(link: Path, target: Path) -> bool:
    """Create a directory junction/symlink.  Returns False when unsupported."""

    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


def safe_rmtree(path: Path) -> None:
    """Remove a synthetic tree, including any junctions, without following them."""

    import shutil

    if not path.exists():
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", str(path)], capture_output=True)
    else:
        shutil.rmtree(path, ignore_errors=True)
