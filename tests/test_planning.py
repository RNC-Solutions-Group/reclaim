from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from .m1_helpers import create_project

from reclaim.execution import (
    ExecutionError,
    dry_run,
    execute_plan,
    reconcile_execution,
    verify_execution,
)
from reclaim.planning import (
    PlanError,
    add_override,
    authorize_plan,
    recompute_plan_digest,
)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = None

    def tearDown(self) -> None:
        if self.project is not None:
            self.project.close()

    def _project(self, files):
        self.project = create_project(Path(self.tmp.name), files)
        return self.project

    def _authorize(self, result):
        return authorize_plan(
            self.project.store,
            self.project.config,
            result.plan_id,
            result.plan_digest,
        )

    def test_prefer_one_duplicates_and_determinism(self) -> None:
        project = self._project(
            {
                "source-a": {
                    "Documents/one.pdf": b"same-content",
                    "Documents/unique.txt": b"unique",
                },
                "source-b": {"Documents/two.pdf": b"same-content"},
            }
        )
        first = project.build()
        second = project.build()
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(first.plan_digest, second.plan_digest)
        self.assertFalse(second.created)
        self.assertEqual(first.summary["counts"].get("copy"), 2)
        self.assertEqual(first.summary["counts"].get("duplicate_omit"), 1)
        self.assertEqual(first.summary["conflicts"], 0)

    def test_keep_all_duplicates(self) -> None:
        project = self._project(
            {
                "source-a": {"Documents/one.pdf": b"same-content"},
                "source-b": {"Documents/two.pdf": b"same-content"},
            }
        )
        result = project.build(duplicate_policy="keep_all")
        self.assertEqual(result.summary["counts"].get("copy"), 2)
        self.assertEqual(result.summary["counts"].get("duplicate_omit", 0), 0)

    def test_fail_policy_makes_duplicates_conflicts(self) -> None:
        project = self._project(
            {
                "source-a": {"Documents/one.pdf": b"same-content"},
                "source-b": {"Documents/two.pdf": b"same-content"},
            }
        )
        result = project.build(duplicate_policy="fail")
        self.assertGreaterEqual(result.summary["conflicts"], 1)
        with self.assertRaises(PlanError):
            authorize_plan(
                project.store,
                project.config,
                result.plan_id,
                result.plan_digest,
            )

    def test_same_name_different_content_is_not_duplicate(self) -> None:
        project = self._project(
            {
                "source-a": {"Documents/report.pdf": b"alpha"},
                "source-b": {"Documents/report.pdf": b"beta"},
            }
        )
        result = project.build()
        self.assertEqual(result.summary["counts"].get("copy"), 2)
        self.assertEqual(result.summary["counts"].get("duplicate_omit", 0), 0)

    def test_merged_template_collision_is_a_conflict(self) -> None:
        project = self._project(
            {
                "source-a": {"Documents/report.pdf": b"alpha"},
                "source-b": {"Documents/report.pdf": b"beta"},
            }
        )
        result = project.build(template="{rel_path}")
        self.assertGreaterEqual(result.summary["conflicts"], 2)
        with self.assertRaises(PlanError):
            self._authorize(result)

    def test_override_changes_plan_and_old_digest_is_rejected(self) -> None:
        project = self._project(
            {
                "source-a": {
                    "Documents/one.pdf": b"same",
                    "Documents/unique.txt": b"unique",
                },
                "source-b": {"Documents/two.pdf": b"same"},
            }
        )
        first = project.build()
        self._authorize(first)
        add_override(
            project.store,
            scope="file",
            source_id="source-a",
            target_rel="Documents/unique.txt",
            disposition="exclude",
            reason="operator decision",
        )
        second = project.build()
        self.assertNotEqual(first.plan_digest, second.plan_digest)
        self.assertEqual(second.summary["counts"].get("exclude"), 1)
        with self.assertRaises(ExecutionError):
            execute_plan(
                project.store,
                project.config,
                second.plan_id,
                first.plan_digest,
            )

    def test_zero_byte_requires_override(self) -> None:
        project = self._project(
            {"source-a": {"Documents/empty.txt": b""}}
        )
        result = project.build()
        self.assertEqual(result.summary["counts"].get("conflict"), 1)
        add_override(
            project.store,
            scope="file",
            source_id="source-a",
            target_rel="Documents/empty.txt",
            disposition="copy",
            reason="zero-byte marker is intentional",
        )
        result = project.build()
        self.assertEqual(result.summary["counts"].get("copy"), 1)

    def test_source_change_blocks_authorization(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        source = project.sources["source-a"] / "Documents" / "one.pdf"
        source.write_bytes(b"changed")
        with self.assertRaises(PlanError):
            self._authorize(result)

    def test_dry_run_does_not_write_destination(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        before = project.destination_files()
        summary = dry_run(project.store, project.config, result.plan_id)
        self.assertTrue(summary["dry_run"])
        self.assertGreaterEqual(summary["would_copy"], 1)
        self.assertEqual(project.destination_files(), before)

    def test_authorization_rejects_destination_appearing_after_plan(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        target = project.destination / "source-a" / "Documents" / "one.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"appeared")
        with self.assertRaises(PlanError):
            self._authorize(result)

    def test_authorization_invalidation_after_plan_tamper(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        self._authorize(result)
        project.store.conn.execute(
            "UPDATE plan_items SET destination_rel='tampered.txt' WHERE plan_id=?",
            (result.plan_id,),
        )
        project.store.commit()
        self.assertNotEqual(recompute_plan_digest(project.store, result.plan_id), result.plan_digest)
        with self.assertRaises(ExecutionError):
            execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )

    def test_authorization_is_required(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        with self.assertRaises(ExecutionError):
            execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = None

    def tearDown(self) -> None:
        if self.project is not None:
            self.project.close()

    def _project(self, files):
        self.project = create_project(Path(self.tmp.name), files)
        return self.project

    def _plan(self, **kwargs):
        result = self.project.build(**kwargs)
        authorize_plan(
            self.project.store,
            self.project.config,
            result.plan_id,
            result.plan_digest,
        )
        return result

    def test_execute_verifies_and_reconciles(self) -> None:
        project = self._project(
            {
                "source-a": {
                    "Documents/one.pdf": b"same",
                    "Documents/unique.txt": b"unique",
                },
                "source-b": {"Documents/two.pdf": b"same"},
            }
        )
        before = project.source_snapshot()
        result = self._plan()
        execution = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        self.assertEqual(execution.status, "complete")
        self.assertEqual(project.destination_files()["source-a/Documents/one.pdf"], b"same")
        self.assertEqual(project.destination_files()["source-a/Documents/unique.txt"], b"unique")
        self.assertNotIn("source-b/Documents/two.pdf", project.destination_files())
        reconciliation = reconcile_execution(
            project.store, execution.execution_id, Path(self.tmp.name) / "recon"
        )
        self.assertTrue(reconciliation["balanced"])
        self.assertEqual(reconciliation["outcome_total"], result.summary["total_items"])
        self.assertEqual(
            reconciliation["outcomes"].get("copied_verified"),
            result.summary["copy_items"],
        )
        self.assertEqual(project.source_snapshot(), before)

    def test_existing_destination_after_planning_is_not_overwritten(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        authorize_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        target = project.destination / "source-a" / "Documents" / "one.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing")
        execution = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        self.assertEqual(execution.status, "complete_with_failures")
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(execution.summary["counts"].get("conflict"), 1)

    def test_source_change_after_authorization_blocks_execution(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        source = project.sources["source-a"] / "Documents" / "one.pdf"
        source.write_bytes(b"changed")
        with self.assertRaises(ExecutionError):
            execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )
        self.assertEqual(project.destination_files(), {})

    def test_source_removal_after_authorization_blocks_execution(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        (project.sources["source-a"] / "Documents" / "one.pdf").unlink()
        with self.assertRaises(ExecutionError):
            execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )
        self.assertEqual(project.destination_files(), {})

    def test_publish_failure_is_reported_and_reconciled(self) -> None:
        from unittest import mock

        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        with mock.patch(
            "reclaim.execution.publish_no_overwrite",
            side_effect=OSError("simulated disk failure"),
        ):
            execution = execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )
        self.assertEqual(execution.status, "complete_with_failures")
        self.assertEqual(execution.summary["counts"].get("failed"), 1)
        reconciliation = reconcile_execution(
            project.store, execution.execution_id, Path(self.tmp.name) / "recon3"
        )
        self.assertTrue(reconciliation["balanced"])
        self.assertEqual(reconciliation["outcomes"].get("failed"), 1)

    def test_interrupted_execution_resumes(self) -> None:
        project = self._project(
            {
                "source-a": {
                    "Documents/a.pdf": b"a",
                    "Documents/b.pdf": b"b",
                    "Documents/c.pdf": b"c",
                }
            }
        )
        result = self._plan()
        first = execute_plan(
            project.store,
            project.config,
            result.plan_id,
            result.plan_digest,
            max_items=1,
        )
        self.assertEqual(first.status, "paused")
        self.assertLess(len(project.destination_files()), 3)
        second = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        self.assertEqual(second.status, "complete")
        self.assertEqual(len(project.destination_files()), 3)
        self.assertEqual(second.execution_id, first.execution_id)

    def test_verify_detects_destination_tamper(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        execution = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        (project.destination / "source-a" / "Documents" / "one.pdf").write_bytes(
            b"tampered"
        )
        verification = verify_execution(
            project.store, project.config, execution.execution_id
        )
        self.assertEqual(verification["status"], "FAIL")
        self.assertEqual(verification["failed"], 1)

    def test_dry_run_and_preview_do_not_write(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build()
        summary = dry_run(project.store, project.config, result.plan_id)
        self.assertTrue(summary["would_execute"])
        self.assertEqual(project.destination_files(), {})

    def test_destination_appears_after_preflight_is_conflict(self) -> None:
        from unittest import mock

        from reclaim import execution

        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        target = project.destination / "source-a" / "Documents" / "one.pdf"
        original = execution.safe_makedirs

        def racy(root, parent):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"raced")
            return original(root, parent)

        with mock.patch("reclaim.execution.safe_makedirs", side_effect=racy):
            result_exec = execute_plan(
                project.store, project.config, result.plan_id, result.plan_digest
            )
        self.assertEqual(result_exec.status, "complete_with_failures")
        self.assertEqual(target.read_bytes(), b"raced")

    def test_stale_temp_file_is_removed_on_resume(self) -> None:
        project = self._project(
            {
                "source-a": {
                    "Documents/a.pdf": b"a",
                    "Documents/b.pdf": b"b",
                }
            }
        )
        result = self._plan()
        first = execute_plan(
            project.store,
            project.config,
            result.plan_id,
            result.plan_digest,
            max_items=1,
        )
        self.assertEqual(first.status, "paused")
        row = project.store.conn.execute(
            "SELECT item_id FROM plan_items WHERE plan_id=? AND source_rel_path='Documents/b.pdf'",
            (result.plan_id,),
        ).fetchone()
        stale = (
            project.destination
            / "source-a"
            / "Documents"
            / f".reclaim-tmp-{first.execution_id}-{row['item_id']}"
        )
        stale.write_bytes(b"partial")
        second = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        self.assertEqual(second.status, "complete")
        self.assertFalse(stale.exists())

    def test_content_change_with_same_size_and_mtime_is_detected(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        source = project.sources["source-a"] / "Documents" / "one.pdf"
        item = project.store.conn.execute(
            "SELECT source_mtime_ns FROM plan_items WHERE plan_id=?",
            (result.plan_id,),
        ).fetchone()
        source.write_bytes(b"evil")
        os.utime(source, ns=(item["source_mtime_ns"], item["source_mtime_ns"]))
        execution = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        self.assertEqual(execution.status, "complete_with_failures")
        self.assertEqual(project.destination_files(), {})
        self.assertEqual(execution.summary["counts"].get("failed"), 1)

    def test_reconcile_reports_verification_failure(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = self._plan()
        execution = execute_plan(
            project.store, project.config, result.plan_id, result.plan_digest
        )
        (project.destination / "source-a" / "Documents" / "one.pdf").write_bytes(
            b"tampered"
        )
        verify_execution(project.store, project.config, execution.execution_id)
        reconciliation = reconcile_execution(
            project.store, execution.execution_id, Path(self.tmp.name) / "recon2"
        )
        self.assertTrue(reconciliation["balanced"])
        self.assertEqual(reconciliation["outcomes"].get("failed"), 1)

    def test_case_collision_in_merged_plan_is_conflict(self) -> None:
        project = self._project(
            {
                "source-a": {"Documents/A.txt": b"alpha"},
                "source-b": {"Documents/a.txt": b"beta"},
            }
        )
        result = project.build(template="{rel_path}", target_platform="windows")
        self.assertGreaterEqual(result.summary["conflicts"], 1)
        with self.assertRaises(PlanError):
            authorize_plan(
                project.store,
                project.config,
                result.plan_id,
                result.plan_digest,
            )

    def test_unsafe_template_is_conflict(self) -> None:
        project = self._project(
            {"source-a": {"Documents/one.pdf": b"same"}}
        )
        result = project.build(template="../{rel_path}")
        self.assertGreaterEqual(result.summary["conflicts"], 1)


if __name__ == "__main__":
    unittest.main()
