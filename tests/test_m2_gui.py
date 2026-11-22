"""M2 GUI integration tests.  Qt runs offscreen and uses synthetic fixtures."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - environment-dependent
    Qt = None
    QApplication = None

from .helpers import write_bytes

from reclaim.gui.services import ProjectSession


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class M2GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        source = self.base / "source-a"
        write_bytes(source / "Documents" / "a.pdf", b"alpha")
        write_bytes(source / "Documents" / "b.pdf", b"beta")
        destination = self.base / "destination"
        destination.mkdir()
        self.session = ProjectSession.create(
            project_dir=self.base / "project",
            project_name="Synthetic GUI",
            sources=[("Source A", source)],
            destination=destination,
        )
        from reclaim.gui.main_window import MainWindow

        self.window = MainWindow(self.session, interactive=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        try:
            self.window.close()
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def _wait(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while self.window.task is not None and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        if self.window.task is not None:
            self.fail("GUI background task did not finish")

    def _inventory(self) -> None:
        self.window.start_scan()
        self._wait()
        self.assertIsNotNone(self.session.run_id)
        self.window.start_hash()
        self._wait()
        self.window.start_analyze()
        self._wait()
        self.assertGreater(self.window.review_model.rowCount(), 0)

    def test_full_gui_workflow(self) -> None:
        self._inventory()
        self.assertEqual(self.window.tabs.currentIndex(), 1)
        self.window.generate_plan()
        self._wait()
        self.assertIsNotNone(self.window.plan)
        self.assertEqual(self.window.plan.summary["copy_items"], 2)
        self.assertGreater(self.window.plan_tree.topLevelItemCount(), 0)
        self.window.authorize_plan()
        self._wait()
        self.assertTrue(self.window.plan.authorized)
        self.window.execute_plan()
        self._wait()
        self.assertIsNotNone(self.window.execution)
        self.assertEqual(self.window.execution.status, "complete")
        self.window.verify_execution()
        self._wait()
        self.assertIn("Verification PASS", self.window.reconciliation_label.text())
        self.window.reconcile_execution()
        self._wait()
        self.assertIn("Successful: True", self.window.reconciliation_label.text())
        self.assertGreater(self.window.outcome_model.rowCount(), 0)
        self.assertTrue((self.base / "destination" / "source-1" / "Documents" / "a.pdf").exists())

    def test_review_toggle_creates_override_and_regenerates(self) -> None:
        self._inventory()
        rows = self.window.review_model.rows()
        target_row = next(
            index for index, row in enumerate(rows) if row.rel_path.endswith("b.pdf")
        )
        self.window.review_model.setData(
            self.window.review_model.index(target_row, 0),
            Qt.Unchecked,
            Qt.CheckStateRole,
        )
        self._wait()
        refreshed = next(
            row for row in self.window.review_model.rows() if row.rel_path.endswith("b.pdf")
        )
        self.assertEqual(refreshed.effective_plan, "exclude")
        self.assertTrue(self.session.plan_stale)
        self.window.generate_plan()
        self._wait()
        self.assertEqual(self.window.plan.summary["copy_items"], 1)
        self.window.filter_combo.setCurrentIndex(2)
        self.app.processEvents()
        self.assertGreaterEqual(self.window.review_proxy.rowCount(), 1)

    def test_results_resume_controls_and_progress(self) -> None:
        self._inventory()
        self.window.generate_plan()
        self._wait()
        self.window.authorize_plan()
        self._wait()
        self.window.execute_plan()
        self._wait()
        self.assertEqual(self.window.execution.status, "complete")
        self.assertFalse(self.window.resume_button.isEnabled())
        self.assertTrue(self.window.verify_button.isEnabled())
        self.assertTrue(self.window.reconcile_button.isEnabled())

    def test_paused_execution_can_resume_from_results(self) -> None:
        self._inventory()
        self.window.generate_plan()
        self._wait()
        self.window.authorize_plan()
        self._wait()
        stop = Event()
        stop.set()
        paused, snapshot = self.session.execute(self.window.plan, stop=stop)
        self.assertEqual(paused.status, "paused")
        self.window.plan = self.session.latest_plan()
        self.window.execution = snapshot
        self.window._refresh_results_ui()
        self.assertTrue(self.window.resume_button.isEnabled())
        self.window.resume_execution()
        self._wait()
        self.assertEqual(self.window.execution.status, "complete")
        self.assertFalse(self.window.resume_button.isEnabled())
