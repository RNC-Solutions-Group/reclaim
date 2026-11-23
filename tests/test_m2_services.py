"""M2 application-service tests using synthetic trees only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event

from .helpers import make_junction, write_bytes

from reclaim.gui.services import GuiError, ProjectSession


class M2ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source_root = self.base / "source-a"
        write_bytes(self.source_root / "Documents" / "a.pdf", b"alpha")
        write_bytes(self.source_root / "Documents" / "b.pdf", b"beta")
        self.destination = self.base / "destination"
        self.destination.mkdir()
        self.sessions: list[ProjectSession] = []

    def tearDown(self) -> None:
        for session in self.sessions:
            try:
                session.close()
            except Exception:
                pass
        self.tmp.cleanup()

    def _session(self) -> ProjectSession:
        session = ProjectSession.create(
            project_dir=self.base / "project",
            project_name="Synthetic M2",
            sources=[("Source A", self.source_root)],
            destination=self.destination,
        )
        self.sessions.append(session)
        return session

    def _inventory(self, session: ProjectSession) -> None:
        self.assertEqual(session.scan()["status"], "scanned")
        self.assertEqual(session.hash()["status"], "complete")
        session.refresh()

    def _plan(self, session: ProjectSession):
        return session.build_plan()

    def test_full_workflow_and_reopen(self) -> None:
        session = self._session()
        self._inventory(session)
        bundle = session.analyze()
        self.assertEqual(len(bundle.rows), 2)
        plan = self._plan(session)
        self.assertEqual(plan.summary["copy_items"], 2)
        self.assertFalse(plan.authorized)
        session.authorize(plan)
        plan = session.latest_plan()
        self.assertTrue(plan.authorized)
        result, execution = session.execute(plan)
        self.assertEqual(result.status, "complete")
        self.assertIsNotNone(execution)
        verification = session.verify(execution.execution_id)
        self.assertEqual(verification["status"], "PASS")
        reconciliation = session.reconcile(execution.execution_id)
        self.assertTrue(reconciliation.summary["balanced"])
        self.assertTrue(reconciliation.summary["successful"])
        self.assertEqual(
            (self.destination / "source-1" / "Documents" / "a.pdf").read_bytes(),
            b"alpha",
        )
        self.assertEqual(
            self.source_root.joinpath("Documents", "a.pdf").read_bytes(), b"alpha"
        )

        config_path = session.config.config_path
        session.close()
        self.sessions.remove(session)
        reopened = ProjectSession.open(config_path)
        self.sessions.append(reopened)
        self.assertEqual(reopened.run_id, session.run_id)
        self.assertIsNotNone(reopened.latest_plan())
        self.assertIsNotNone(reopened.latest_execution())

    def test_overrides_regenerate_plan_and_restore(self) -> None:
        session = self._session()
        self._inventory(session)
        bundle = session.analyze()
        target = next(row for row in bundle.rows if row.rel_path.endswith("b.pdf"))
        session.apply_overrides([target], "exclude", "Synthetic operator exclusion")
        bundle = session.analyze()
        target = next(row for row in bundle.rows if row.rel_path.endswith("b.pdf"))
        self.assertEqual(target.effective_plan, "exclude")
        plan = self._plan(session)
        self.assertEqual(plan.summary["copy_items"], 1)

        session.remove_overrides([target])
        bundle = session.analyze()
        target = next(row for row in bundle.rows if row.rel_path.endswith("b.pdf"))
        self.assertEqual(target.effective_plan, "copy")
        plan = self._plan(session)
        self.assertEqual(plan.summary["copy_items"], 2)

    def test_pause_and_resume_execution(self) -> None:
        session = self._session()
        self._inventory(session)
        bundle = session.analyze()
        self.assertEqual(len(bundle.rows), 2)
        plan = self._plan(session)
        session.authorize(plan)
        plan = session.latest_plan()
        stop = Event()
        stop.set()
        events: list[dict[str, object]] = []
        paused, snapshot = session.execute(plan, progress=events.append, stop=stop)
        self.assertEqual(paused.status, "paused")
        self.assertEqual(snapshot.status, "paused")
        self.assertTrue(events)
        self.assertGreater(snapshot.counts.get("pending", 0), 0)
        resumed, snapshot = session.execute(plan, stop=Event())
        self.assertEqual(resumed.status, "complete")
        self.assertEqual(snapshot.counts.get("verified", 0), 2)
        verification = session.verify(snapshot.execution_id)
        self.assertEqual(verification["status"], "PASS")

    def test_destination_inside_source_is_rejected(self) -> None:
        inside = self.source_root / "destination"
        inside.mkdir()
        with self.assertRaises(Exception):
            ProjectSession.create(
                project_dir=self.base / "bad-project",
                project_name="Bad",
                sources=[("Source A", self.source_root)],
                destination=inside,
            )

    def test_preexisting_destination_is_reported_as_conflict(self) -> None:
        session = self._session()
        self._inventory(session)
        session.analyze()
        target = self.destination / "source-1" / "Documents" / "a.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"preexisting")
        plan = self._plan(session)
        self.assertEqual(plan.summary["conflicts"], 1)

    def test_selected_destination_path_is_preserved(self) -> None:
        session = self._session()
        self.assertEqual(
            session.settings.destination, str(self.destination.absolute())
        )
        self.assertEqual(session.destination, self.destination.absolute())
        self._inventory(session)
        plan = self._plan(session)
        self.assertEqual(
            Path(plan.destination_selected), self.destination.absolute()
        )

    def test_junction_destination_is_rejected_at_creation(self) -> None:
        target = self.base / "physical-target"
        target.mkdir()
        link = self.base / "linked-destination"
        if not make_junction(link, target):
            self.skipTest("junction/symlink creation not permitted")
        with self.assertRaises(GuiError):
            ProjectSession.create(
                project_dir=self.base / "linked-project",
                project_name="Linked",
                sources=[("Source A", self.source_root)],
                destination=link,
            )

    def test_redirected_destination_ancestor_is_rejected(self) -> None:
        real_parent = self.base / "real-parent"
        real_parent.mkdir()
        (real_parent / "destination").mkdir()
        linked_parent = self.base / "linked-parent"
        if not make_junction(linked_parent, real_parent):
            self.skipTest("junction/symlink creation not permitted")
        with self.assertRaises(GuiError):
            ProjectSession.create(
                project_dir=self.base / "redirect-project",
                project_name="Redirected",
                sources=[("Source A", self.source_root)],
                destination=linked_parent / "destination",
            )

    def test_destination_change_after_creation_blocks_plan(self) -> None:
        session = self._session()
        moved = self.base / "moved-destination"
        self.destination.rename(moved)
        if not make_junction(self.destination, moved):
            self.skipTest("junction/symlink creation not permitted")
        with self.assertRaises(GuiError):
            session.set_destination(self.destination)
        self._inventory(session)
        with self.assertRaises(Exception):
            self._plan(session)

    def test_plan_freshness_survives_reopen_and_detects_changes(self) -> None:
        session = self._session()
        self._inventory(session)
        plan = self._plan(session)
        session.authorize(plan)
        self.assertTrue(session.plan_is_current(session.latest_plan()))
        config_path = session.config.config_path
        session.close()
        self.sessions.remove(session)

        reopened = ProjectSession.open(config_path)
        self.sessions.append(reopened)
        reopened_plan = reopened.latest_plan()
        self.assertIsNotNone(reopened_plan)
        self.assertTrue(reopened.plan_is_current(reopened_plan))
        reopened.update_plan_settings(
            template="{source_id}/{name}",
            duplicate_policy=reopened.settings.duplicate_policy,
            target_platform=reopened.settings.target_platform,
            max_path_length=reopened.settings.max_path_length,
        )
        self.assertFalse(reopened.plan_is_current(reopened_plan))
        reopened.update_plan_settings(
            template=reopened_plan.destination_template,
            duplicate_policy=reopened_plan.duplicate_policy,
            target_platform=reopened_plan.target_platform,
            max_path_length=reopened_plan.max_path_length,
        )
        self.assertTrue(reopened.plan_is_current(reopened_plan))

        bundle = reopened.analyze()
        target = next(row for row in bundle.rows if row.rel_path.endswith("b.pdf"))
        reopened.apply_overrides([target], "exclude", "freshness test")
        self.assertFalse(reopened.plan_is_current(reopened_plan))
        refreshed = reopened.analyze()
        target = next(row for row in refreshed.rows if row.rel_path.endswith("b.pdf"))
        reopened.remove_overrides([target])
        self.assertTrue(reopened.plan_is_current(reopened_plan))

    def test_latest_plan_uses_insertion_order_for_same_timestamp(self) -> None:
        session = self._session()
        self._inventory(session)
        session.analyze()
        plan_ids = []
        for template in ("{source_id}/{rel_path}", "{source_id}/v2/{rel_path}", "{source_id}/v3/{rel_path}"):
            session.update_plan_settings(
                template=template,
                duplicate_policy="prefer_one",
                target_platform="",
                max_path_length=240,
            )
            plan_ids.append(session.build_plan().plan_id)
        self.assertEqual(session.latest_plan().plan_id, plan_ids[-1])
        self.assertEqual(session.list_plans()[0]["plan_id"], plan_ids[-1])
        self.assertEqual(len(set(plan_ids)), 3)
