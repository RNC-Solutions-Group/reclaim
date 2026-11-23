"""Project creation dialog for the desktop shell."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .services import GuiError, ProjectSession


class ProjectDialog(QDialog):
    """Collect the minimum safe inputs for a new synthetic/real project."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Reclaim Project")
        self.setMinimumWidth(680)
        self.session: ProjectSession | None = None
        self._sources: list[tuple[str, Path]] = []

        self.name_edit = QLineEdit("Reclaim project")
        self.folder_edit = QLineEdit(str(Path.home() / "Reclaim Projects" / "project-1"))
        folder_button = QPushButton("Browse…")
        folder_button.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(folder_button)
        folder_widget = QWidget()
        folder_widget.setLayout(folder_row)

        self.destination_edit = QLineEdit()
        destination_button = QPushButton("Browse…")
        destination_button.clicked.connect(self._browse_destination)
        destination_row = QHBoxLayout()
        destination_row.addWidget(self.destination_edit)
        destination_row.addWidget(destination_button)
        destination_widget = QWidget()
        destination_widget.setLayout(destination_row)

        self.source_table = QTableWidget(0, 2)
        self.source_table.setHorizontalHeaderLabels(["Source label", "Directory"])
        self.source_table.horizontalHeader().setStretchLastSection(True)
        self.source_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.source_table.setEditTriggers(QTableWidget.NoEditTriggers)
        add_source = QPushButton("Add source directory…")
        add_source.clicked.connect(self._add_source)
        remove_source = QPushButton("Remove selected")
        remove_source.clicked.connect(self._remove_source)
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(add_source)
        source_buttons.addWidget(remove_source)
        source_buttons.addStretch(1)

        form = QFormLayout()
        form.addRow("Project name:", self.name_edit)
        form.addRow("Project folder:", folder_widget)
        form.addRow("Destination:", destination_widget)

        source_box = QVBoxLayout()
        source_box.addWidget(QLabel("Sources (read-only inventory inputs):"))
        source_box.addWidget(self.source_table)
        source_box.addLayout(source_buttons)

        note = QLabel(
            "The destination must be a separate physical directory. Sources are "
            "never modified. A new plan and fresh authorization are required after "
            "any review decision changes."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(source_box)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose project folder")
        if path:
            self.folder_edit.setText(path)

    def _browse_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose destination directory")
        if path:
            self.destination_edit.setText(path)

    def _add_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose source directory")
        if not path:
            return
        selected = Path(path)
        if any(existing == selected for _, existing in self._sources):
            return
        label = selected.name or f"Source {len(self._sources) + 1}"
        self._sources.append((label, selected))
        row = self.source_table.rowCount()
        self.source_table.insertRow(row)
        self.source_table.setItem(row, 0, QTableWidgetItem(label))
        self.source_table.setItem(row, 1, QTableWidgetItem(str(selected)))

    def _remove_source(self) -> None:
        rows = sorted({index.row() for index in self.source_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.source_table.removeRow(row)
            del self._sources[row]

    def _create(self) -> None:
        if not self._sources:
            QMessageBox.warning(self, "Sources required", "Add at least one source directory.")
            return
        if not self.destination_edit.text().strip():
            QMessageBox.warning(self, "Destination required", "Choose a destination directory.")
            return
        try:
            self.session = ProjectSession.create(
                project_dir=self.folder_edit.text().strip(),
                project_name=self.name_edit.text().strip(),
                sources=[(label, path) for label, path in self._sources],
                destination=self.destination_edit.text().strip(),
            )
        except (GuiError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot create project", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - dialog boundary
            QMessageBox.critical(self, "Cannot create project", str(exc))
            return
        self.accept()
