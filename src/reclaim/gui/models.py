"""Virtualized table models used by the M2 review and results views."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)

from .services import ReviewRow

_DISPOSITION_LABELS = {
    "copy": "Copy",
    "exclude": "Exclude",
    "defer": "Defer",
    "duplicate_omit": "Duplicate omitted",
    "conflict": "Needs decision",
}

_RECOMMENDATION_LABELS = {
    "retain": "Keep",
    "exclude": "Exclude candidate",
    "defer_media": "Defer media",
    "duplicate": "Duplicate occurrence",
    "needs_review": "Review",
    "potential_junk": "Review",
    "related_version": "Review",
    "similar": "Review",
    "analysis_failure": "Review",
}


def _size_label(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


class ReviewTableModel(QAbstractTableModel):
    """Scalable review model; one row per inventoried source occurrence."""

    decisionRequested = Signal(object, str, str)

    HEADERS = (
        "Include",
        "Source",
        "Path",
        "Size",
        "Type",
        "Recommendation",
        "Effective",
        "Duplicate",
        "Decision / reason",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[ReviewRow] = []

    def set_rows(self, rows: list[ReviewRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> list[ReviewRow]:
        return list(self._rows)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        if role == Qt.TextAlignmentRole and orientation == Qt.Horizontal:
            if section == 0:
                return int(Qt.AlignCenter)
            if section == 3:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.CheckStateRole and column == 0:
            return Qt.Checked if row.effective_plan == "copy" else Qt.Unchecked
        if role == Qt.DisplayRole:
            if column == 0:
                return ""
            if column == 1:
                return f"{row.source_label} ({row.source_id})"
            if column == 2:
                return row.rel_path
            if column == 3:
                return _size_label(row.size_bytes)
            if column == 4:
                return row.kind
            if column == 5:
                return _RECOMMENDATION_LABELS.get(row.recommended, row.recommended)
            if column == 6:
                return _DISPOSITION_LABELS.get(row.effective_plan, row.effective_plan)
            if column == 7:
                if row.duplicate_role == "canonical":
                    return "Canonical"
                if row.duplicate_role == "duplicate":
                    return f"Duplicate {row.duplicate_group[:12]}"
                return ""
            if column == 8:
                if row.override_id:
                    return f"{row.decision_source}: {row.override_reason}"
                return f"{row.decision_source}: {row.rationale}"
        if role == Qt.ToolTipRole:
            lines = [
                f"{row.source_id}:{row.rel_path}",
                f"SHA-256 status: {row.hash_status}",
                f"Recommendation: {_RECOMMENDATION_LABELS.get(row.recommended, row.recommended)}",
                f"Effective plan action: {_DISPOSITION_LABELS.get(row.effective_plan, row.effective_plan)}",
                f"Rationale: {row.rationale}",
            ]
            if row.override_id:
                lines.append(f"Override {row.override_id}: {row.override_reason}")
            return "\n".join(lines)
        if role == Qt.TextAlignmentRole and column == 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if (
            index.isValid()
            and index.column() == 0
            and role == Qt.CheckStateRole
            and index.row() < len(self._rows)
        ):
            disposition = "copy" if value == Qt.Checked else "exclude"
            self.decisionRequested.emit(
                [self._rows[index.row()]],
                disposition,
                "Decision toggled in review",
            )
            return True
        return False


class ReviewFilterProxy(QSortFilterProxyModel):
    """Search and quick-filter the review model without duplicating rows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""
        self._mode = "all"
        self.setDynamicSortFilter(True)

    def set_query(self, query: str) -> None:
        self._query = query.strip().casefold()
        self.invalidateFilter()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None or source_row >= len(model.rows()):  # type: ignore[attr-defined]
            return True
        row = model.rows()[source_row]  # type: ignore[attr-defined]
        if self._query:
            haystack = " ".join(
                (
                    row.source_id,
                    row.source_label,
                    row.rel_path,
                    row.kind,
                    row.recommended,
                    row.effective_plan,
                    row.duplicate_role,
                    row.override_reason,
                )
            ).casefold()
            if self._query not in haystack:
                return False
        if self._mode == "copy":
            return row.effective_plan == "copy"
        if self._mode == "exclude":
            return row.effective_plan == "exclude"
        if self._mode == "review":
            return row.effective_plan == "conflict"
        if self._mode == "duplicates":
            return row.duplicate_role in ("canonical", "duplicate")
        if self._mode == "errors":
            return row.hash_status != "ok"
        return True


class OutcomeTableModel(QAbstractTableModel):
    HEADERS = ("Item", "Source", "Destination", "Outcome", "Detail")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        keys = ("item_id", "source", "destination_rel", "outcome", "detail")
        if role == Qt.DisplayRole:
            return str(row.get(keys[index.column()], ""))
        if role == Qt.ToolTipRole:
            return "\n".join(f"{key}: {row.get(key, '')}" for key in row)
        return None
