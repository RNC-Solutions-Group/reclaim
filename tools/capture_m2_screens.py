"""Generate the synthetic M2 screenshots checked into docs/m2/screenshots.

Run from the repository root on a desktop Windows session:

    $env:PYTHONPATH = "src"
    python tools/capture_m2_screens.py

The generated project is temporary and synthetic.  No private paths or files
are used in the images.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication  # noqa: E402

from reclaim.gui.main_window import MainWindow  # noqa: E402
from reclaim.gui.services import ProjectSession  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    base = Path(tempfile.mkdtemp(prefix="reclaim-m2-demo-"))
    output = Path("docs/m2/screenshots")
    output.mkdir(parents=True, exist_ok=True)
    try:
        source_a = base / "source-a"
        (source_a / "Documents").mkdir(parents=True)
        (source_a / "Documents" / "Project-Notes.txt").write_bytes(
            b"synthetic project notes\n"
        )
        (source_a / "Documents" / "Report.pdf").write_bytes(
            b"%PDF synthetic report\n"
        )
        (source_a / "Pictures").mkdir()
        (source_a / "Pictures" / "Summer.jpg").write_bytes(
            b"synthetic image bytes\n"
        )
        source_b = base / "source-b"
        (source_b / "Documents").mkdir(parents=True)
        (source_b / "Documents" / "Project-Notes.txt").write_bytes(
            b"synthetic project notes\n"
        )
        destination = base / "destination"
        destination.mkdir()
        session = ProjectSession.create(
            project_dir=base / "project",
            project_name="Synthetic Family Archive",
            sources=[("Laptop A", source_a), ("Old Drive", source_b)],
            destination=destination,
        )
        session.scan()
        session.hash()
        bundle = session.analyze()
        window = MainWindow(session, interactive=False)
        window.resize(1400, 900)
        window.show()
        app.processEvents()

        window._analysis_complete(bundle)
        if window.review_model.rowCount():
            window.review_table.selectRow(0)
        app.processEvents()
        window.grab().save(str(output / "review.png"))

        plan = session.build_plan()
        window._plan_ready(plan)
        window.tabs.setCurrentIndex(2)
        app.processEvents()
        window.grab().save(str(output / "plan.png"))

        session.authorize(plan)
        plan = session.latest_plan()
        window.plan = plan
        result, execution = session.execute(plan)
        window.execution = execution
        window._refresh_results_ui()
        verification = session.verify(execution.execution_id)
        reconciliation = session.reconcile(execution.execution_id)
        window._verification_done(verification)
        window._reconciliation_done(reconciliation)
        window.tabs.setCurrentIndex(3)
        app.processEvents()
        window.grab().save(str(output / "results.png"))
        print(f"saved screenshots to {output}")
        window.close()
        session.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)
        app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
