"""PySide6 desktop application for RNC Reclaim (M2)."""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from .app import main as run

    return run(argv)
