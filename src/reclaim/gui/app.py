"""PySide6 application entry point for RNC Reclaim M2."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .main_window import APP_TITLE, MainWindow
from .services import ProjectSession


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    app = QApplication(arguments)
    app.setApplicationName("RNC Reclaim")
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName("RNC Solutions Group, Inc.")
    window = MainWindow()
    config_path = Path(arguments[1]) if len(arguments) > 1 else None
    if config_path is not None and config_path.is_file():
        try:
            window.adopt_session(ProjectSession.open(config_path))
        except Exception as exc:  # noqa: BLE001 - startup boundary
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(window, "Cannot open project", str(exc))
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
