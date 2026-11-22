from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from .m1_helpers import create_project

from reclaim.cli import main


class M1CliTests(unittest.TestCase):
    def test_plan_authorize_execute_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            base = Path(tmp)
            project = create_project(
                base,
                {"source-a": {"Documents/report.pdf": b"report"}},
            )
            stack.callback(project.close)
            plan_out = base / "plans"
            reconciliation_out = base / "reconciliation"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(
                    [
                        "plan",
                        "--config",
                        str(project.config.config_path),
                        "--run",
                        project.run_id,
                        "--destination",
                        str(project.destination),
                        "--out",
                        str(plan_out),
                    ]
                )
            self.assertEqual(rc, 0, output.getvalue())
            plan_id = None
            digest = None
            for line in output.getvalue().splitlines():
                if line.startswith("plan:"):
                    plan_id = line.split(":", 1)[1].strip()
                if line.startswith("digest:"):
                    digest = line.split(":", 1)[1].strip()
            self.assertIsNotNone(plan_id)
            self.assertIsNotNone(digest)

            dry_output = io.StringIO()
            with contextlib.redirect_stdout(dry_output):
                self.assertEqual(
                    main(
                        [
                            "execute",
                            "--config",
                            str(project.config.config_path),
                            "--plan",
                            plan_id,
                            "--digest",
                            digest,
                            "--dry-run",
                        ]
                    ),
                    0,
                )
            self.assertIn('"dry_run": true', dry_output.getvalue())
            self.assertEqual(project.destination_files(), {})

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "authorize",
                            "--config",
                            str(project.config.config_path),
                            "--plan",
                            plan_id,
                            "--digest",
                            digest,
                        ]
                    ),
                    0,
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "execute",
                            "--config",
                            str(project.config.config_path),
                            "--plan",
                            plan_id,
                            "--digest",
                            digest,
                        ]
                    ),
                    0,
                )
            self.assertIn("status:    complete", output.getvalue())
            execution_id = None
            for line in output.getvalue().splitlines():
                if line.startswith("execution:"):
                    execution_id = line.split(":", 1)[1].strip()
            self.assertIsNotNone(execution_id)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "reconcile",
                            "--config",
                            str(project.config.config_path),
                            "--execution",
                            execution_id,
                            "--out",
                            str(reconciliation_out),
                        ]
                    ),
                    0,
                )
            reports = list(reconciliation_out.glob("reconciliation-*.csv"))
            self.assertEqual(len(reports), 1)
            self.assertTrue(
                (project.destination / "source-a" / "Documents" / "report.pdf").exists()
            )


if __name__ == "__main__":
    unittest.main()
