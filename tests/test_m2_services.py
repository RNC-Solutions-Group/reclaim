"""M2 application-service tests using synthetic trees only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event

from .helpers import write_bytes

from reclaim.gui.services import ProjectSession


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
