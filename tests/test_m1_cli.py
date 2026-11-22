from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .m1_helpers import create_project

from reclaim.cli import (
    EXIT_EXECUTION_FAILURE,
    EXIT_INCOMPLETE,
    EXIT_RECONCILIATION_FAILURE,
    EXIT_VERIFICATION_FAILURE,
    main,
)
from reclaim.planning import authorize_plan


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

    def test_execute_failure_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp), {"source-a": {"Documents/report.pdf": b"report"}}
            )
            stack.callback(project.close)
            plan = project.build()
            authorize_plan(
                project.store,
                project.config,
                plan.plan_id,
                plan.plan_digest,
            )
            with mock.patch(
                "reclaim.execution.publish_no_overwrite",
                side_effect=OSError("simulated failure"),
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "execute",
                        "--config",
                        str(project.config.config_path),
                        "--plan",
                        plan.plan_id,
                        "--digest",
                        plan.plan_digest,
                    ]
                )
            self.assertEqual(rc, EXIT_EXECUTION_FAILURE)

    def test_verification_and_reconciliation_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp), {"source-a": {"Documents/report.pdf": b"report"}}
            )
            stack.callback(project.close)
            plan = project.build()
            authorize_plan(
                project.store,
                project.config,
                plan.plan_id,
                plan.plan_digest,
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "execute",
                            "--config",
                            str(project.config.config_path),
                            "--plan",
                            plan.plan_id,
                            "--digest",
                            plan.plan_digest,
                        ]
                    ),
                    0,
                )
            execution_id = None
            for line in output.getvalue().splitlines():
                if line.startswith("execution:"):
                    execution_id = line.split(":", 1)[1].strip()
            (project.destination / "source-a" / "Documents" / "report.pdf").unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                verify_rc = main(
                    [
                        "verify-execution",
                        "--config",
                        str(project.config.config_path),
                        "--execution",
                        execution_id,
                    ]
                )
            self.assertEqual(verify_rc, EXIT_VERIFICATION_FAILURE)
            with contextlib.redirect_stdout(io.StringIO()):
                reconcile_rc = main(
                    [
                        "reconcile",
                        "--config",
                        str(project.config.config_path),
                        "--execution",
                        execution_id,
                        "--out",
                        str(Path(tmp) / "recon"),
                    ]
                )
            self.assertEqual(reconcile_rc, EXIT_RECONCILIATION_FAILURE)

    def test_dry_run_blocked_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp), {"source-a": {"Documents/report.pdf": b"report"}}
            )
            stack.callback(project.close)
            plan = project.build()
            target = project.destination / "source-a" / "Documents" / "report.pdf"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "execute",
                        "--config",
                        str(project.config.config_path),
                        "--plan",
                        plan.plan_id,
                        "--digest",
                        plan.plan_digest,
                        "--dry-run",
                    ]
                )
            self.assertEqual(rc, EXIT_EXECUTION_FAILURE)

    def test_paused_execution_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp),
                {
                    "source-a": {
                        "Documents/a.pdf": b"a",
                        "Documents/b.pdf": b"b",
                    }
                },
            )
            stack.callback(project.close)
            plan = project.build()
            authorize_plan(
                project.store,
                project.config,
                plan.plan_id,
                plan.plan_digest,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "execute",
                        "--config",
                        str(project.config.config_path),
                        "--plan",
                        plan.plan_id,
                        "--digest",
                        plan.plan_digest,
                        "--max-items",
                        "1",
                    ]
                )
            self.assertEqual(rc, EXIT_INCOMPLETE)

    def test_plan_conflict_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp),
                {
                    "source-a": {"Documents/report.pdf": b"alpha"},
                    "source-b": {"Documents/report.pdf": b"beta"},
                },
            )
            stack.callback(project.close)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "plan",
                        "--config",
                        str(project.config.config_path),
                        "--run",
                        project.run_id,
                        "--destination",
                        str(project.destination),
                        "--template",
                        "{rel_path}",
                    ]
                )
            self.assertEqual(rc, EXIT_EXECUTION_FAILURE)

    def test_incomplete_reconcile_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            project = create_project(
                Path(tmp),
                {
                    "source-a": {
                        "Documents/a.pdf": b"a",
                        "Documents/b.pdf": b"b",
                    }
                },
            )
            stack.callback(project.close)
            plan = project.build()
            authorize_plan(
                project.store,
                project.config,
                plan.plan_id,
                plan.plan_digest,
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "execute",
                            "--config",
                            str(project.config.config_path),
                            "--plan",
                            plan.plan_id,
                            "--digest",
                            plan.plan_digest,
                            "--max-items",
                            "1",
                        ]
                    ),
                    EXIT_INCOMPLETE,
                )
            execution_id = None
            for line in output.getvalue().splitlines():
                if line.startswith("execution:"):
                    execution_id = line.split(":", 1)[1].strip()
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "reconcile",
                        "--config",
                        str(project.config.config_path),
                        "--execution",
                        execution_id,
                        "--out",
                        str(Path(tmp) / "recon-incomplete"),
                    ]
                )
            self.assertEqual(rc, EXIT_INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
