"""Main window for the RNC Reclaim desktop shell (M2)."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import OutcomeTableModel, ReviewFilterProxy, ReviewTableModel
from .project_dialog import ProjectDialog
from .services import (
    AnalysisBundle,
    ExecutionSnapshot,
    GuiError,
    PlanSnapshot,
    ProjectSession,
    Reconciliation,
    ReviewRow,
)
from .workers import Task

APP_TITLE = "RNC Reclaim — Migration Studio"


def _display_path(value: str | Path | None) -> str:
    if value is None or str(value) == "":
        return ""
    path = Path(value)
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~/" + relative.as_posix()


def _size(value: int | None) -> str:
    if value is None:
        return "Unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


class MainWindow(QMainWindow):
    def __init__(self, session: ProjectSession | None = None, *, interactive: bool = True) -> None:
        super().__init__()
        self.interactive = interactive
        self.session: ProjectSession | None = None
        self.analysis: AnalysisBundle | None = None
        self.plan: PlanSnapshot | None = None
        self.execution: ExecutionSnapshot | None = None
        self.reconciliation: Reconciliation | None = None
        self.task: Task | None = None
        self._busy = False
        self._close_after_task = False
        self._loading_settings = False

        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 860)
        self.setMinimumSize(1060, 700)
        self._build_ui()
        self._build_menu()
        if session is not None:
            self.adopt_session(session)

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(8)

        header = QHBoxLayout()
        brand = QLabel("RECLAIM")
        brand.setStyleSheet("font-size: 22px; font-weight: 700; letter-spacing: 2px;")
        subtitle = QLabel("Migration Studio")
        subtitle.setStyleSheet("color: #666; font-size: 13px; padding-top: 6px;")
        self.project_label = QLabel("No project open")
        self.project_label.setStyleSheet("font-weight: 600; padding-left: 12px;")
        new_button = QPushButton("New project")
        new_button.clicked.connect(self.new_project)
        open_button = QPushButton("Open project")
        open_button.clicked.connect(self.open_project)
        header.addWidget(brand)
        header.addWidget(subtitle)
        header.addSpacing(18)
        header.addWidget(self.project_label, 1)
        header.addWidget(new_button)
        header.addWidget(open_button)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_project_tab(), "1 · Project")
        self.tabs.addTab(self._build_review_tab(), "2 · Review")
        self.tabs.addTab(self._build_plan_tab(), "3 · Plan")
        self.tabs.addTab(self._build_results_tab(), "4 · Results")
        outer.addWidget(self.tabs, 1)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Open or create a project to begin.")
        self.status_label.setStyleSheet("color: #444;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(250)
        self.progress_bar.setVisible(False)
        self.pause_button = QPushButton("Stop safely")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.request_stop)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.progress_bar)
        status_row.addWidget(self.pause_button)
        outer.addLayout(status_row)

        self.setCentralWidget(root)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("New project…", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self.new_project)
        open_action = QAction("Open project…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_project)
        close_action = QAction("Close project", self)
        close_action.triggered.connect(self.close_project)
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        project_menu = self.menuBar().addMenu("&Project")
        for label, slot in (
            ("Scan sources", self.start_scan),
            ("Hash files", self.start_hash),
            ("Analyze and review", self.start_analyze),
        ):
            action = QAction(label, self)
            action.triggered.connect(slot)
            project_menu.addAction(action)
        project_menu.addSeparator()
        refresh_action = QAction("Refresh project state", self)
        refresh_action.setShortcut(QKeySequence.Refresh)
        refresh_action.triggered.connect(self.refresh_project_view)
        project_menu.addAction(refresh_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("About RNC Reclaim", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    # -- project tab -------------------------------------------------------

    def _build_project_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        project_box = QGroupBox("Project and sources")
        project_layout = QVBoxLayout(project_box)
        self.project_path_label = QLabel("No project open")
        self.project_path_label.setWordWrap(True)
        self.project_path_label.setStyleSheet("color: #555;")
        project_layout.addWidget(self.project_path_label)
        self.source_table = QTableWidget(0, 4)
        self.source_table.setHorizontalHeaderLabels(
            ["Source ID", "Label", "Directory", "Status"]
        )
        self.source_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.source_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.source_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.source_table.setMaximumHeight(180)
        project_layout.addWidget(self.source_table)
        source_buttons = QHBoxLayout()
        self.scan_button = QPushButton("1 · Scan sources")
        self.scan_button.clicked.connect(self.start_scan)
        self.hash_button = QPushButton("2 · Hash files")
        self.hash_button.clicked.connect(self.start_hash)
        self.analyze_button = QPushButton("3 · Analyze and review")
        self.analyze_button.clicked.connect(self.start_analyze)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_project_view)
        source_buttons.addWidget(self.scan_button)
        source_buttons.addWidget(self.hash_button)
        source_buttons.addWidget(self.analyze_button)
        source_buttons.addStretch(1)
        source_buttons.addWidget(self.refresh_button)
        project_layout.addLayout(source_buttons)
        layout.addWidget(project_box)

        destination_box = QGroupBox("Destination")
        destination_layout = QFormLayout(destination_box)
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Select an existing, separate directory")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_destination)
        destination_row = QHBoxLayout()
        destination_row.addWidget(self.destination_edit)
        destination_row.addWidget(browse)
        destination_widget = QWidget()
        destination_widget.setLayout(destination_row)
        destination_layout.addRow("Destination root:", destination_widget)
        self.destination_status_label = QLabel("No destination selected.")
        self.destination_status_label.setWordWrap(True)
        destination_layout.addRow("Validation:", self.destination_status_label)
        self.capacity_label = QLabel("")
        destination_layout.addRow("Capacity:", self.capacity_label)
        layout.addWidget(destination_box)

        inventory_box = QGroupBox("Inventory state")
        inventory_layout = QFormLayout(inventory_box)
        self.run_status_label = QLabel("No scan run")
        self.run_counts_label = QLabel("")
        self.run_counts_label.setWordWrap(True)
        inventory_layout.addRow("Run status:", self.run_status_label)
        inventory_layout.addRow("Counts:", self.run_counts_label)
        self.inventory_hint = QLabel(
            "Scan reads source metadata only. Hashing is read-only. Nothing is "
            "written to a source or destination until you explicitly authorize "
            "and execute a plan."
        )
        self.inventory_hint.setWordWrap(True)
        self.inventory_hint.setStyleSheet("color: #555;")
        inventory_layout.addRow(self.inventory_hint)
        layout.addWidget(inventory_box)
        layout.addStretch(1)
        return page

    # -- review tab --------------------------------------------------------

    def _build_review_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by path, source, type, decision…")
        self.search_edit.textChanged.connect(self._filter_review)
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All files", "all")
        self.filter_combo.addItem("Will copy", "copy")
        self.filter_combo.addItem("Excluded", "exclude")
        self.filter_combo.addItem("Needs decision", "review")
        self.filter_combo.addItem("Duplicates", "duplicates")
        self.filter_combo.addItem("Hash errors / deferred", "errors")
        self.filter_combo.currentIndexChanged.connect(self._filter_review)
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.filter_combo)
        layout.addLayout(top)

        self.review_summary = QLabel("Analyze an inventory to populate review.")
        self.review_summary.setStyleSheet("color: #444;")
        layout.addWidget(self.review_summary)

        self.review_model = ReviewTableModel(self)
        self.review_proxy = ReviewFilterProxy(self)
        self.review_proxy.setSourceModel(self.review_model)
        self.review_model.decisionRequested.connect(self._decision_from_model)
        self.review_table = QTableView()
        self.review_table.setModel(self.review_proxy)
        self.review_table.setSortingEnabled(True)
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.review_table.setAlternatingRowColors(True)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setColumnWidth(0, 74)
        self.review_table.setColumnWidth(1, 165)
        self.review_table.setColumnWidth(2, 340)
        self.review_table.setColumnWidth(3, 84)
        self.review_table.setColumnWidth(5, 130)
        self.review_table.setColumnWidth(6, 135)
        self.review_table.setColumnWidth(7, 155)
        self.review_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Interactive
        )
        self.review_table.horizontalHeader().setSectionResizeMode(
            8, QHeaderView.Stretch
        )
        self.review_table.setStyleSheet(
            "QTableView::item:selected { background: #2f6fed; color: white; }"
        )
        self.review_table.selectionModel().selectionChanged.connect(
            self._review_selection_changed
        )
        layout.addWidget(self.review_table, 3)

        detail = QTextBrowser()
        detail.setPlaceholderText("Select a file to see its evidence and decision.")
        detail.setMaximumHeight(130)
        self.review_detail = detail
        layout.addWidget(detail, 1)

        buttons = QHBoxLayout()
        include = QPushButton("Include selected")
        include.clicked.connect(lambda: self.apply_review_decision("copy"))
        exclude = QPushButton("Exclude selected")
        exclude.clicked.connect(lambda: self.apply_review_decision("exclude"))
        defer = QPushButton("Defer selected")
        defer.clicked.connect(lambda: self.apply_review_decision("defer"))
        remove = QPushButton("Remove override")
        remove.clicked.connect(self.remove_review_overrides)
        overrides = QPushButton("All overrides…")
        overrides.clicked.connect(self.show_overrides)
        for button in (include, exclude, defer, remove):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(overrides)
        layout.addLayout(buttons)
        return page

    # -- plan tab ----------------------------------------------------------

    def _build_plan_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        settings_box = QGroupBox("Plan settings")
        settings_form = QFormLayout(settings_box)
        self.plan_destination_edit = QLineEdit()
        plan_browse = QPushButton("Browse…")
        plan_browse.clicked.connect(self.choose_destination)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.plan_destination_edit)
        dest_row.addWidget(plan_browse)
        dest_widget = QWidget()
        dest_widget.setLayout(dest_row)
        settings_form.addRow("Destination:", dest_widget)
        self.template_edit = QLineEdit("{source_id}/{rel_path}")
        settings_form.addRow("Destination template:", self.template_edit)
        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItems(["prefer_one", "keep_all", "fail"])
        settings_form.addRow("Duplicate policy:", self.duplicate_combo)
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("Host platform", "")
        self.platform_combo.addItem("Windows", "windows")
        self.platform_combo.addItem("macOS", "macos")
        self.platform_combo.addItem("Linux", "linux")
        settings_form.addRow("Target path rules:", self.platform_combo)
        self.max_path_spin = QSpinBox()
        self.max_path_spin.setRange(64, 4096)
        self.max_path_spin.setValue(240)
        settings_form.addRow("Maximum path length:", self.max_path_spin)
        self.plan_destination_edit.textChanged.connect(self._plan_controls_changed)
        self.template_edit.textChanged.connect(self._plan_controls_changed)
        self.duplicate_combo.currentIndexChanged.connect(self._plan_controls_changed)
        self.platform_combo.currentIndexChanged.connect(self._plan_controls_changed)
        self.max_path_spin.valueChanged.connect(self._plan_controls_changed)
        self.generate_button = QPushButton("Generate immutable plan")
        self.generate_button.clicked.connect(self.generate_plan)
        settings_form.addRow(self.generate_button)
        left_layout.addWidget(settings_box)

        summary_box = QGroupBox("Plan summary")
        summary_layout = QFormLayout(summary_box)
        self.plan_id_label = QLabel("No plan")
        self.plan_digest_label = QLabel("")
        self.plan_digest_label.setWordWrap(True)
        self.plan_digest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.plan_status_label = QLabel("")
        self.plan_counts_label = QLabel("")
        self.plan_counts_label.setWordWrap(True)
        summary_layout.addRow("Plan ID:", self.plan_id_label)
        summary_layout.addRow("Digest:", self.plan_digest_label)
        summary_layout.addRow("Status:", self.plan_status_label)
        summary_layout.addRow("Counts:", self.plan_counts_label)
        self.plan_warning = QLabel("")
        self.plan_warning.setWordWrap(True)
        self.plan_warning.setStyleSheet("color: #a33;")
        summary_layout.addRow(self.plan_warning)
        left_layout.addWidget(summary_box)

        self.plan_issues = QListWidget()
        self.plan_issues.setMaximumHeight(150)
        left_layout.addWidget(QLabel("Conflicts / validation issues:"))
        left_layout.addWidget(self.plan_issues)
        plan_buttons = QHBoxLayout()
        self.authorize_button = QPushButton("Authorize exact plan…")
        self.authorize_button.clicked.connect(self.authorize_plan)
        self.execute_button = QPushButton("Execute plan…")
        self.execute_button.clicked.connect(self.execute_plan)
        self.results_button = QPushButton("Results")
        self.results_button.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        plan_buttons.addWidget(self.authorize_button)
        plan_buttons.addWidget(self.execute_button)
        plan_buttons.addWidget(self.results_button)
        left_layout.addLayout(plan_buttons)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.preview_label = QLabel("Destination preview")
        self.preview_label.setStyleSheet("font-weight: 600;")
        right_layout.addWidget(self.preview_label)
        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderLabels(["Destination", "Action", "Source", "Size"])
        self.plan_tree.setColumnWidth(0, 380)
        self.plan_tree.setColumnWidth(1, 120)
        self.plan_tree.setColumnWidth(2, 270)
        self.plan_tree.setAlternatingRowColors(True)
        right_layout.addWidget(self.plan_tree, 1)
        self.plan_notes = QLabel("")
        self.plan_notes.setWordWrap(True)
        self.plan_notes.setStyleSheet("color: #555;")
        right_layout.addWidget(self.plan_notes)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([430, 850])
        layout.addWidget(splitter, 1)
        return page

    # -- results tab -------------------------------------------------------

    def _build_results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        summary_box = QGroupBox("Execution and verification")
        summary_layout = QFormLayout(summary_box)
        self.execution_id_label = QLabel("No execution")
        self.execution_status_label = QLabel("")
        self.execution_counts_label = QLabel("")
        self.execution_counts_label.setWordWrap(True)
        self.execution_destination_label = QLabel("")
        self.execution_destination_label.setWordWrap(True)
        summary_layout.addRow("Execution:", self.execution_id_label)
        summary_layout.addRow("Status:", self.execution_status_label)
        summary_layout.addRow("Item counts:", self.execution_counts_label)
        summary_layout.addRow("Destination:", self.execution_destination_label)
        layout.addWidget(summary_box)

        controls = QHBoxLayout()
        self.resume_button = QPushButton("Resume paused execution")
        self.resume_button.clicked.connect(self.resume_execution)
        self.verify_button = QPushButton("Verify copies")
        self.verify_button.clicked.connect(self.verify_execution)
        self.reconcile_button = QPushButton("Reconcile")
        self.reconcile_button.clicked.connect(self.reconcile_execution)
        self.refresh_results_button = QPushButton("Refresh")
        self.refresh_results_button.clicked.connect(self.refresh_results)
        controls.addWidget(self.resume_button)
        controls.addWidget(self.verify_button)
        controls.addWidget(self.reconcile_button)
        controls.addStretch(1)
        controls.addWidget(self.refresh_results_button)
        layout.addLayout(controls)

        self.reconciliation_label = QLabel("No reconciliation yet.")
        self.reconciliation_label.setWordWrap(True)
        self.reconciliation_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.reconciliation_label)

        self.outcome_model = OutcomeTableModel(self)
        self.outcome_table = QTableView()
        self.outcome_table.setModel(self.outcome_model)
        self.outcome_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.outcome_table.verticalHeader().setVisible(False)
        self.outcome_table.setColumnWidth(0, 55)
        self.outcome_table.setColumnWidth(1, 190)
        self.outcome_table.setColumnWidth(3, 150)
        self.outcome_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        layout.addWidget(self.outcome_table, 1)
        return page

    # -- project lifecycle -------------------------------------------------

    def adopt_session(self, session: ProjectSession) -> None:
        if self.session is not None:
            self.session.close()
        self.session = session
        self.analysis = None
        self.plan = session.latest_plan()
        self.execution = session.latest_execution()
        self.reconciliation = None
        self.project_label.setText(session.project_name)
        self.project_path_label.setText(
            f"Project: {session.project_name}\nConfig: {_display_path(session.config.config_path)}\n"
            f"Workspace: {_display_path(session.config.workspace)}"
        )
        self._loading_settings = True
        try:
            self.destination_edit.setText(_display_path(session.destination))
            self.plan_destination_edit.setText(self.destination_edit.text())
            self.plan_destination_edit.setCursorPosition(0)
            self.template_edit.setText(session.settings.destination_template)
            self.duplicate_combo.setCurrentText(session.settings.duplicate_policy)
            index = self.platform_combo.findData(session.settings.target_platform)
            self.platform_combo.setCurrentIndex(max(0, index))
            self.max_path_spin.setValue(session.settings.max_path_length)
        finally:
            self._loading_settings = False
        if self.plan is not None:
            session.plan_stale = not session.plan_is_current(self.plan)
        self.refresh_project_view()
        self._refresh_plan_ui()
        self._refresh_results_ui()
        self.tabs.setCurrentIndex(0)
        self.status_label.setText(
            "Project ready. Start with Scan sources, then Hash files and Analyze."
        )

    def new_project(self) -> None:
        if not self._can_leave_project():
            return
        dialog = ProjectDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.session is not None:
            self.adopt_session(dialog.session)

    def open_project(self) -> None:
        if not self._can_leave_project():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Reclaim project", "", "Reclaim project (*.toml);;All files (*)"
        )
        if not path:
            return
        try:
            session = ProjectSession.open(path)
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self._critical("Cannot open project", str(exc))
            return
        self.adopt_session(session)

    def close_project(self) -> None:
        if not self._can_leave_project():
            return
        if self.session is not None:
            self.session.close()
        self.session = None
        self.analysis = None
        self.plan = None
        self.execution = None
        self.reconciliation = None
        self.project_label.setText("No project open")
        self.project_path_label.setText("No project open")
        self.source_table.setRowCount(0)
        self.destination_edit.clear()
        self.plan_destination_edit.clear()
        self.review_model.set_rows([])
        self.review_summary.setText("Analyze an inventory to populate review.")
        self.plan_tree.clear()
        self._refresh_plan_ui()
        self._refresh_results_ui()
        self.status_label.setText("Project closed.")

    def _can_leave_project(self) -> bool:
        if self.task is not None and self.task.isRunning():
            self._warn("Background job running",
                "Wait for the current operation to finish or stop it safely before "
                "closing the project.",
            )
            return False
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.task is not None and self.task.isRunning():
            if not self._confirm(
                "Background job running",
                "Stop the current job safely after its current item and close?\n\n"
                "Already completed copies remain journaled; a paused execution can "
                "be resumed when the project is reopened.",
            ):
                event.ignore()
                return
            self._close_after_task = True
            self.task.request_stop()
            self.pause_button.setEnabled(False)
            self.status_label.setText(
                "Stopping safely after the current item; the window will close when "
                "the journal is consistent."
            )
            event.ignore()
            return
        if self.session is not None:
            self.session.close()
            self.session = None
        event.accept()

    # -- project view ------------------------------------------------------

    def refresh_project_view(self) -> None:
        if self.session is None:
            return
        sources = self.session.source_views()
        self.source_table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            values = (
                source.source_id,
                source.label,
                _display_path(source.path),
                "Ready" if source.available else source.error,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and not source.available:
                    item.setForeground(QColor("#a33"))
                self.source_table.setItem(row, column, item)
        self.destination_edit.setText(_display_path(self.session.destination))
        self.plan_destination_edit.setText(self.destination_edit.text())
        self.plan_destination_edit.setCursorPosition(0)
        self._refresh_destination_status()
        run = self.session.run_summary()
        if run is None:
            self.run_status_label.setText("No scan run")
            self.run_counts_label.setText("")
        else:
            counts = run["counts"]
            self.run_status_label.setText(
                f"{run['status']} · {run['run_id']} · started {run['started_at']}"
            )
            self.run_counts_label.setText(
                f"{counts.get('files', 0):,} files · {counts.get('dirs', 0):,} directories · "
                f"{_size(counts.get('bytes', 0))} · hashes pending {counts.get('hash_pending', 0):,} "
                f"· errors {counts.get('hash_errors', 0):,}"
            )
        self._update_action_states()

    def _refresh_destination_status(self) -> None:
        if self.session is None:
            self.destination_status_label.setText("No project open.")
            self.capacity_label.setText("")
            return
        status = self.session.destination_status()
        if status.path is None:
            self.destination_status_label.setText("No destination selected.")
            self.capacity_label.setText("")
            return
        self.destination_edit.setText(_display_path(status.path))
        self.destination_edit.setCursorPosition(0)
        self._loading_settings = True
        try:
            self.plan_destination_edit.setText(_display_path(status.path))
            self.plan_destination_edit.setCursorPosition(0)
        finally:
            self._loading_settings = False
        if status.issues:
            self.destination_status_label.setText("; ".join(status.issues))
            self.destination_status_label.setStyleSheet("color: #a33;")
        else:
            self.destination_status_label.setText(
                f"Valid {status.target_platform} target · physical directory"
            )
            self.destination_status_label.setStyleSheet("color: #276b2b;")
        self.capacity_label.setText(
            f"Free space: {_size(status.free_bytes)}"
            if status.free_bytes is not None
            else "Free space: unavailable"
        )

    def choose_destination(self) -> None:
        if self.session is None:
            self._info("No project", "Create or open a project first.")
            return
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose destination directory",
            str(self.session.destination or Path.home()),
        )
        if not path:
            return
        try:
            status = self.session.set_destination(path)
        except Exception as exc:  # noqa: BLE001
            self._critical("Destination rejected", str(exc))
            return
        self._loading_settings = True
        try:
            self.plan_destination_edit.setText(_display_path(status.path))
            self.plan_destination_edit.setCursorPosition(0)
        finally:
            self._loading_settings = False
        self._refresh_destination_status()
        self._refresh_plan_freshness()

    # -- operations --------------------------------------------------------

    def start_scan(self) -> None:
        if self.session is None:
            return
        self._run_task(
            lambda progress, stop: self.session.scan(progress, stop),
            self._scan_complete,
            "Scanning source metadata…",
        )

    def _scan_complete(self, result: dict[str, Any]) -> None:
        if self.session is not None:
            self.session.refresh()
        self.refresh_project_view()
        if result.get("status") == "partial":
            self.status_label.setText("Scan stopped or incomplete; scan again before hashing.")
            self._warn("Scan incomplete",
                "The scan was stopped. Source coverage is incomplete; run Scan sources again.",
            )
        else:
            self.status_label.setText("Scan complete. Next: Hash files.")
            self._info("Scan complete",
                f"Scanned {result['counts'].get('files', 0):,} files and "
                f"{result['counts'].get('dirs', 0):,} directories.",
            )

    def start_hash(self) -> None:
        if self.session is None:
            return
        self._run_task(
            lambda progress, stop: self.session.hash(progress, stop),
            self._hash_complete,
            "Hashing files…",
        )

    def _hash_complete(self, result: dict[str, Any]) -> None:
        self.refresh_project_view()
        if result.get("status") == "complete":
            self.status_label.setText("Hashing complete. Next: Analyze and review.")
            self._info("Hashing complete",
                f"Hashed {result['total']:,} files with {result['errors']:,} error(s).",
            )
        else:
            self.status_label.setText(
                "Hashing incomplete or has errors. Review errors, then hash again."
            )
            self._warn("Hashing incomplete",
                f"{result['errors']:,} hash error(s) remain. Resolve unreadable/changing "
                "files or hash again before planning.",
            )

    def start_analyze(self) -> None:
        if self.session is None:
            return
        if not self.session.run_id:
            self._info("Scan required", "Run Scan sources first.")
            return
        self._run_task(
            lambda progress, stop: self.session.analyze(),
            self._analysis_complete,
            "Loading review analysis…",
            stoppable=False,
        )

    def _analysis_complete(self, bundle: AnalysisBundle) -> None:
        self.analysis = bundle
        self.review_model.set_rows(bundle.rows)
        self.review_summary.setText(
            f"{len(bundle.rows):,} files · {bundle.duplicate_groups:,} exact-duplicate groups "
            f"({bundle.duplicate_occurrences:,} occurrences) · decisions apply to the next plan."
        )
        self._filter_review()
        self._refresh_plan_freshness()
        self.tabs.setCurrentIndex(1)
        self.status_label.setText(
            "Review complete. Toggle Include, or select rows for bulk decisions; "
            "then generate a new plan."
        )

    def _filter_review(self) -> None:
        self.review_proxy.set_query(self.search_edit.text())
        self.review_proxy.set_mode(str(self.filter_combo.currentData()))

    def _selected_review_rows(self) -> list[ReviewRow]:
        rows: list[ReviewRow] = []
        selection = self.review_table.selectionModel().selectedRows()
        for proxy_index in selection:
            source_index = self.review_proxy.mapToSource(proxy_index)
            if source_index.isValid():
                rows.append(self.review_model.row_at(source_index.row()))
        return rows

    def _review_selection_changed(self) -> None:
        rows = self._selected_review_rows()
        if not rows:
            self.review_detail.clear()
            return
        row = rows[0]
        lines = [
            f"<b>{html.escape(row.source_id)}:{html.escape(row.rel_path)}</b>",
            f"Size: {_size(row.size_bytes)} · Type: {html.escape(row.kind)} · Hash: {html.escape(row.hash_status)}",
            f"Recommendation: <b>{html.escape(row.recommended)}</b> — {html.escape(row.rationale)}",
            f"Effective plan action: <b>{html.escape(row.effective_plan)}</b>",
            f"Duplicate role: {html.escape(row.duplicate_role or 'unique')}"
            + (f" ({html.escape(row.duplicate_group)})" if row.duplicate_group else ""),
        ]
        if row.override_id:
            lines.append(
                f"Override {html.escape(row.override_id)} ({html.escape(row.decision_source)}): "
                f"{html.escape(row.override_reason)}"
            )
        self.review_detail.setHtml("<br>".join(lines))

    def _decision_from_model(self, rows: object, disposition: str, reason: str) -> None:
        if not self.session or not isinstance(rows, list) or not rows:
            return
        self._apply_rows(rows, disposition, reason)

    def apply_review_decision(self, disposition: str) -> None:
        rows = self._selected_review_rows()
        if not rows:
            self._info("No selection", "Select one or more files first.")
            return
        self._apply_rows(rows, disposition, "Bulk decision in review")

    def _apply_rows(
        self, rows: list[ReviewRow], disposition: str, suggested_reason: str
    ) -> None:
        if self.session is None:
            return
        reason, ok = self._ask_text("Record decision reason",
            "Why is this decision being made? (required for audit)",
            suggested_reason,
        )
        if not ok:
            self._filter_review()
            return
        if not reason.strip():
            self._warn("Reason required", "Enter a reason for the override.")
            self._filter_review()
            return
        try:
            self.session.apply_overrides(rows, disposition, reason)
        except Exception as exc:  # noqa: BLE001
            self._critical("Cannot apply decision", str(exc))
            return
        self._mark_plan_stale("Review decisions changed; generate a new plan.")
        self.status_label.setText("Decision recorded. Refreshing review analysis…")
        self.start_analyze()

    def remove_review_overrides(self) -> None:
        if self.session is None:
            return
        rows = self._selected_review_rows()
        if not rows:
            self._info("No selection", "Select one or more files first.")
            return
        try:
            removed = self.session.remove_overrides(rows)
        except Exception as exc:  # noqa: BLE001
            self._critical("Cannot remove override", str(exc))
            return
        if removed:
            self._mark_plan_stale("Overrides removed; generate a new plan.")
            self.status_label.setText(f"Removed {removed} file override(s).")
            self.start_analyze()
        else:
            self._info("No file override removed",
                "The selected rows have no removable file-level override. "
                "Directory overrides are managed by their parent scope.",
            )

    def show_overrides(self) -> None:
        if self.session is None:
            return
        rows = self.session.active_overrides()
        dialog = QDialog(self)
        dialog.setWindowTitle("Active overrides")
        dialog.resize(820, 400)
        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(
            ["Override", "Scope", "Source", "Path", "Decision", "Reason"]
        )
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, override in enumerate(rows):
            values = (
                override["override_id"],
                override["scope"],
                override["source_id"],
                override["target_rel"],
                override["disposition"],
                override["reason"],
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(table)
        layout.addWidget(buttons)
        dialog.exec()

    # -- plan --------------------------------------------------------------

    def generate_plan(self) -> None:
        if self.session is None:
            return
        if not self._can_leave_project():
            return
        destination = self.plan_destination_edit.text().strip()
        if not destination:
            self._info("Destination required", "Select a destination before planning."
            )
            return
        try:
            self.session.set_destination(destination)
            self.session.update_plan_settings(
                template=self.template_edit.text(),
                duplicate_policy=self.duplicate_policy(),
                target_platform=str(self.platform_combo.currentData() or ""),
                max_path_length=self.max_path_spin.value(),
            )
        except (GuiError, ValueError) as exc:
            self._critical("Invalid plan settings", str(exc))
            return
        self._refresh_destination_status()
        self._run_task(
            lambda progress, stop: self.session.build_plan(progress, stop),
            self._plan_ready,
            "Building immutable plan…",
            stoppable=False,
        )

    def duplicate_policy(self) -> str:
        return str(self.duplicate_combo.currentText())

    def _plan_ready(self, plan: PlanSnapshot) -> None:
        self.plan = plan
        self.session.plan_stale = not self.session.plan_is_current(plan)
        self._refresh_plan_ui()
        self.tabs.setCurrentIndex(2)
        if plan.issues:
            self.status_label.setText(
                f"Plan created with {len(plan.issues)} issue(s). Resolve conflicts before authorization."
            )
        else:
            self.status_label.setText(
                "Plan ready. Review the destination tree, then authorize the exact digest."
            )

    def _plan_controls_changed(self) -> None:
        if self._loading_settings or self.session is None or self.plan is None:
            return
        self._refresh_plan_freshness()

    def _refresh_plan_freshness(self) -> None:
        if self.session is None:
            return
        if self.plan is None:
            self.session.plan_stale = False
            self._update_action_states()
            return
        self.session.plan_stale = not self.session.plan_is_current(
            self.plan,
            destination=self.plan_destination_edit.text().strip(),
            destination_template=self.template_edit.text(),
            duplicate_policy=self.duplicate_policy(),
            target_platform=str(self.platform_combo.currentData() or ""),
            max_path_length=self.max_path_spin.value(),
        )
        self._update_plan_warning(self.plan)
        self._update_action_states()

    def _update_plan_warning(self, plan: PlanSnapshot) -> None:
        if plan.issues:
            self.plan_warning.setText(
                "This plan has unresolved conflicts. Authorization is disabled until "
                "review decisions produce a conflict-free plan."
            )
            self.plan_warning.setStyleSheet("color: #a33;")
        elif self.session is not None and self.session.plan_stale:
            self.plan_warning.setText(
                "Current settings/review decisions differ from this plan. "
                "Generate and authorize a new plan."
            )
            self.plan_warning.setStyleSheet("color: #a33;")
        elif plan.authorized:
            self.plan_warning.setText("Authorized for this exact digest.")
            self.plan_warning.setStyleSheet("color: #276b2b;")
        else:
            self.plan_warning.setText(
                "Not authorized. Authorization is required before execution."
            )
            self.plan_warning.setStyleSheet("color: #a33;")

    def _refresh_plan_ui(self) -> None:
        self.plan_tree.clear()
        self.plan_issues.clear()
        if self.plan is None:
            self.plan_id_label.setText("No plan")
            self.plan_digest_label.clear()
            self.plan_status_label.clear()
            self.plan_counts_label.clear()
            self.plan_warning.clear()
            self.plan_notes.clear()
            self._update_action_states()
            return
        plan = self.plan
        self.plan_id_label.setText(plan.plan_id)
        self.plan_digest_label.setText(plan.digest)
        self.plan_status_label.setText(plan.status)
        summary = plan.summary
        self.plan_counts_label.setText(
            f"{summary.get('total_items', 0):,} items · "
            f"{summary.get('copy_items', 0):,} copies · "
            f"{_size(summary.get('copy_bytes', 0))} · "
            f"{summary.get('conflicts', 0):,} conflicts"
        )
        for issue in plan.issues:
            item = QListWidgetItem(
                f"{issue.get('code', 'ISSUE')}: {issue.get('message', '')}"
            )
            item.setToolTip(str(issue))
            self.plan_issues.addItem(item)
        self._update_plan_warning(plan)
        self._build_plan_tree(plan)
        self._update_action_states()

    def _build_plan_tree(self, plan: PlanSnapshot) -> None:
        self.plan_tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        shown = 0
        for item in plan.items:
            if not item.destination_rel:
                continue
            if shown >= 4000:
                continue
            shown += 1
            parts = item.destination_rel.split("/")
            parent = None
            path_so_far: list[str] = []
            for part in parts:
                path_so_far.append(part)
                key = "/".join(path_so_far)
                node = nodes.get(key)
                if node is None:
                    node = (
                        QTreeWidgetItem(parent)
                        if parent is not None
                        else QTreeWidgetItem(self.plan_tree)
                    )
                    node.setText(0, part)
                    nodes[key] = node
                parent = node
            assert parent is not None
            parent.setText(1, item.disposition)
            parent.setText(2, f"{item.source_id}:{item.source_rel_path}")
            parent.setText(3, _size(item.size_bytes))
            if item.disposition == "conflict":
                parent.setForeground(1, QColor("#a33"))
        if shown == 0:
            self.plan_notes.setText("No copy destinations in this plan.")
        elif shown >= 4000:
            self.plan_notes.setText(
                f"Showing the first 4,000 of {summary_count(plan)} destination items."
            )
        else:
            self.plan_notes.setText(
                f"{shown:,} destination items shown. "
                "Source paths are original; destination paths are proposed only."
            )
        self.plan_tree.expandToDepth(1)

    def authorize_plan(self) -> None:
        if self.session is None or self.plan is None:
            return
        if self.session.plan_stale:
            self._warn("Plan is stale",
                "Review decisions or settings changed. Generate a new plan first.",
            )
            return
        if self.plan.issues:
            self._warn("Conflicts remain",
                "Resolve all plan conflicts before authorizing.",
            )
            return
        text = (
            "Authorize this exact immutable plan?\n\n"
            f"Plan: {self.plan.plan_id}\n"
            f"Digest: {self.plan.digest}\n\n"
            "Any review change will require a new plan and fresh authorization."
        )
        if self._confirm("Authorize exact plan",
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        notes, ok = self._ask_text("Authorization note (optional)", "Notes for the audit record:", ""
        )
        if not ok:
            return
        try:
            self.session.authorize(self.plan, notes)
        except Exception as exc:  # noqa: BLE001
            self._critical("Authorization failed", str(exc))
            return
        self.plan = self.session.latest_plan()
        self._refresh_plan_ui()
        self.status_label.setText("Plan authorized. Execution is now available.")

    def execute_plan(self) -> None:
        if self.session is None or self.plan is None:
            return
        if self.plan.status != "authorized" and not self.plan.authorized:
            self._warn("Authorization required", "Authorize the exact plan digest first."
            )
            return
        if self._confirm("Execute migration",
            "Execute this authorized plan? Copies are published atomically and "
            "existing destinations are never overwritten.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._start_execution(self.plan)

    def _start_execution(self, plan: PlanSnapshot) -> None:
        if self.session is None:
            return
        self._run_task(
            lambda progress, stop: self.session.execute(plan, progress, stop),
            self._execution_done,
            "Executing migration…",
        )
        self.tabs.setCurrentIndex(3)

    def _execution_done(self, result: object) -> None:
        execution_result, snapshot = result  # type: ignore[misc]
        self.execution = snapshot
        self._refresh_results_ui()
        self.plan = self.session.latest_plan() if self.session else self.plan
        self._refresh_plan_ui()
        if execution_result.status == "complete":
            self.status_label.setText("Execution complete. Verify copies and reconcile.")
            self._info("Execution complete",
                "All planned copy items completed. Run Verify copies and Reconcile "
                "before treating the migration as successful.",
            )
        elif execution_result.status == "paused":
            self.status_label.setText(
                "Execution paused safely. You can resume it later from Results."
            )
        else:
            self.status_label.setText(
                "Execution completed with failures/conflicts. Review outcomes before continuing."
            )

    def resume_execution(self) -> None:
        if self.session is None or self.execution is None:
            return
        if self.execution.status != "paused":
            self._info("Nothing to resume", "The execution is not paused.")
            return
        plan = self.plan
        if plan is None or plan.plan_id != self.execution.plan_id:
            plan = self.session.latest_plan()
        if plan is None or plan.plan_id != self.execution.plan_id:
            self._warn("Plan unavailable", "Open the plan for this execution before resuming."
            )
            return
        self._start_execution(plan)

    def refresh_results(self) -> None:
        if self.session is not None:
            self.execution = self.session.latest_execution()
        self._refresh_results_ui()

    def _refresh_results_ui(self) -> None:
        if self.execution is None:
            self.execution_id_label.setText("No execution")
            self.execution_status_label.setText("")
            self.execution_counts_label.setText("")
            self.execution_destination_label.setText("")
            self.reconciliation_label.setText("No reconciliation yet.")
            self.outcome_model.set_rows([])
            self._update_action_states()
            return
        self.execution_id_label.setText(self.execution.execution_id)
        self.execution_status_label.setText(self.execution.status)
        self.execution_counts_label.setText(
            " · ".join(f"{key}: {value}" for key, value in self.execution.counts.items())
            if self.execution.counts
            else "No copy items"
        )
        self.execution_destination_label.setText(_display_path(self.execution.destination_root))
        if self.execution.status == "paused":
            self.reconciliation_label.setText(
                "Execution paused. Resume to finish remaining items."
            )
        elif self.execution.status == "verification_failed":
            self.reconciliation_label.setText(
                "Verification failed. Review failed outcomes; restore/fix files, "
                "then verify again or reconcile."
            )
        else:
            self.reconciliation_label.setText(
                "Run Verify copies and Reconcile before treating this execution as successful."
            )
        self._update_action_states()

    def verify_execution(self) -> None:
        if self.session is None or self.execution is None:
            return
        execution_id = self.execution.execution_id
        self._run_task(
            lambda progress, stop: self.session.verify(execution_id, progress, stop),
            self._verification_done,
            "Verifying destination copies…",
            stoppable=False,
        )

    def _verification_done(self, result: dict[str, Any]) -> None:
        self.execution = self.session.latest_execution() if self.session else self.execution
        self._refresh_results_ui()
        status = result.get("status")
        if status == "PASS":
            self.reconciliation_label.setText(
                f"Verification PASS · {result.get('verified', 0)} verified · "
                f"{result.get('failed', 0)} failed."
            )
        else:
            self.reconciliation_label.setText(
                f"Verification {status} · {result.get('verified', 0)} verified · "
                f"{result.get('failed', 0)} failed · {result.get('missing', 0)} missing."
            )
            self._warn("Verification did not pass",
                "One or more destinations are missing, unreadable, or no longer match "
                "the planned SHA-256. Reconcile reports these as failures.",
            )

    def reconcile_execution(self) -> None:
        if self.session is None or self.execution is None:
            return
        execution_id = self.execution.execution_id
        self._run_task(
            lambda progress, stop: self.session.reconcile(execution_id, progress, stop),
            self._reconciliation_done,
            "Writing reconciliation report…",
            stoppable=False,
        )

    def _reconciliation_done(self, reconciliation: Reconciliation) -> None:
        self.reconciliation = reconciliation
        summary = reconciliation.summary
        self.outcome_model.set_rows(reconciliation.records)
        self.reconciliation_label.setText(
            f"Balanced: {summary.get('balanced')} · Successful: {summary.get('successful')} · "
            f"Assessment: {summary.get('assessment')} · "
            f"Required copy: {summary.get('required_copy_items', 0)} · "
            f"Verified copy: {summary.get('verified_copy_items', 0)}"
        )
        self.status_label.setText(
            f"Reconciliation complete: {summary.get('assessment')}. "
            "Reports are in the private project workspace."
        )

    # -- testable dialog boundary -----------------------------------------

    def _info(self, title: str, text: str) -> None:
        if self.interactive:
            QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        if self.interactive:
            QMessageBox.warning(self, title, text)

    def _critical(self, title: str, text: str) -> None:
        if self.interactive:
            QMessageBox.critical(self, title, text)

    def _confirm(self, title: str, text: str, buttons=None, default=None):
        if not self.interactive:
            return QMessageBox.Yes
        return QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

    def _ask_text(self, title: str, label: str, text: str = "") -> tuple[str, bool]:
        if not self.interactive:
            return text, True
        return QInputDialog.getMultiLineText(self, title, label, text)

    def _about(self, title: str, text: str) -> None:
        if self.interactive:
            QMessageBox.about(self, title, text)

    # -- task plumbing -----------------------------------------------------

    def _run_task(
        self,
        job,
        callback: Callable[[Any], None],
        message: str,
        *,
        stoppable: bool = True,
    ) -> None:
        if self.task is not None and self.task.isRunning():
            self._info("Operation in progress", "Wait for the current operation to finish."
            )
            return
        task = Task(job, self)
        self.task = task
        self._set_busy(True, message, stoppable=stoppable)
        task.progress.connect(self._on_progress)
        task.completed.connect(
            lambda result, current=task, done=callback: self._task_complete(
                current, result, done
            )
        )
        task.failed.connect(lambda error, current=task: self._task_failed(current, error))
        task.finished.connect(task.deleteLater)
        task.start()

    def _task_complete(self, task: Task, result: object, callback: Callable[[Any], None]) -> None:
        if self.task is task:
            self.task = None
        self._set_busy(False, "Ready", stoppable=False)
        if self._close_after_task:
            self._close_after_task = False
            self.close()
            return
        if self.task is None and task.stop_requested:
            self.status_label.setText(
                "Operation stopped safely. Resume or retry when ready."
            )
        callback(result)

    def _task_failed(self, task: Task, message: str) -> None:
        if self.task is task:
            self.task = None
        self._set_busy(False, "Operation failed", stoppable=False)
        if self._close_after_task:
            self._close_after_task = False
            self.close()
            return
        self._critical("Operation failed", message)

    def _on_progress(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        message = str(payload.get("message", "Working…"))
        self.status_label.setText(message)
        processed = payload.get("processed")
        total = payload.get("total")
        if isinstance(processed, int) and isinstance(total, int) and total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(min(processed, total))
        else:
            self.progress_bar.setRange(0, 0)

    def _set_busy(self, busy: bool, message: str, *, stoppable: bool = True) -> None:
        self._busy = busy
        self.status_label.setText(message)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
        self.pause_button.setEnabled(busy and stoppable)
        self._update_action_states()

    def request_stop(self) -> None:
        if self.task is None:
            return
        self.task.request_stop()
        self.status_label.setText("Stop requested; finishing the current item safely…")
        self.pause_button.setEnabled(False)

    def _update_action_states(self) -> None:
        has_project = self.session is not None
        busy = self._busy
        has_run = bool(self.session and self.session.run_id)
        self.scan_button.setEnabled(has_project and not busy)
        self.hash_button.setEnabled(has_project and has_run and not busy)
        self.analyze_button.setEnabled(has_project and has_run and not busy)
        self.refresh_button.setEnabled(has_project and not busy)
        self.generate_button.setEnabled(has_project and not busy and bool(self.destination_edit.text()))
        plan_ready = self.plan is not None and not busy
        plan_clean = plan_ready and not self.plan.issues and not (self.session and self.session.plan_stale)
        self.authorize_button.setEnabled(bool(plan_clean and not self.plan.authorized))
        self.execute_button.setEnabled(
            bool(plan_ready and self.plan.authorized and not (self.session and self.session.plan_stale))
        )
        self.results_button.setEnabled(bool(self.execution or self.plan))
        self.resume_button.setEnabled(
            bool(self.execution and self.execution.status == "paused" and not busy)
        )
        self.verify_button.setEnabled(
            bool(self.execution and self.execution.status in ("complete", "complete_with_failures", "verification_failed") and not busy)
        )
        self.reconcile_button.setEnabled(
            bool(self.execution and self.execution.status not in ("running", "paused") and not busy)
        )
        self.refresh_results_button.setEnabled(has_project and not busy)

    def _mark_plan_stale(self, reason: str) -> None:
        if self.session is not None:
            self.session.plan_stale = True
        self.plan_warning.setText(reason)
        self.plan_warning.setStyleSheet("color: #a33;")
        self._update_action_states()

    def show_about(self) -> None:
        self._about("About RNC Reclaim",
            "<b>RNC Reclaim — Migration Studio</b><br><br>"
            "Local-first inventory, review, planning, journaled copy, verification, "
            "and reconciliation. M2 functional alpha; no telemetry or external AI.<br><br>"
            "PySide6/Qt is dynamically linked under its LGPL terms.",
        )


def summary_count(plan: PlanSnapshot) -> int:
    return int(plan.summary.get("total_items", len(plan.items)))
